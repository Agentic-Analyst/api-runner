# main.py — aligned to /data/<email>/<TICKER>/<timestamp>/... layout

"""
Stock Analyst API Runner

A FastAPI service that manages and runs stock analysis jobs using Docker containers.
Features:
- Run stock analysis jobs in Docker containers
- Real-time streaming of analysis progress
- Download analysis results (articles, reports)
- Health monitoring and job management
"""

import os
import json
import asyncio
import logging
import time
import concurrent.futures
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
import tempfile
import zipfile
import shutil
from io import BytesIO
import shlex
import codecs
from fastapi import Request
import subprocess

import docker
import docker.errors
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
import uvicorn
from fastapi import Response
import shlex, time, io, zipfile, hashlib
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware import Middleware
from agents.gateway_agent import GatewayAgent
from auth_oauth import router as oauth_router  # noqa: E402
from md_pdf_converter import convert_md_to_pdf
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Email-code login router
from auth_code_login import router as code_router  # <— ensure this file is present

# Tunables
POLL_INTERVAL_SEC = 0.75
IDLE_HEARTBEAT_SEC = 12
FINAL_DRAIN_STABLE_CYCLES = 3  # how many consecutive polls with no growth before we finalize

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Env / App init ---
def _csv_env(name: str, default: str = "") -> List[str]:
    raw = os.getenv(name, default)
    vals = [v.strip() for v in raw.split(",") if v.strip()]
    return vals

# --- Env ---
FRONTEND_ORIGINS = _csv_env("FRONTEND_ORIGIN")
ROOT_PATH = os.getenv("ROOT_PATH")
SESSION_SECRET = os.getenv("SESSION_SECRET")
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY").lower() == "true"
SESSION_STORE_COOKIE = os.getenv("SESSION_STORE_COOKIE")

# SameSite configuration
SESSION_SAMESITE = os.getenv("SESSION_SAMESITE", "lax").lower()
if SESSION_SAMESITE not in {"lax", "none", "strict"}:
    SESSION_SAMESITE = "lax"
if SESSION_SAMESITE == "none" and not SESSION_HTTPS_ONLY:
    SESSION_HTTPS_ONLY = True  # browsers require Secure when SameSite=None

# Build app with explicit middleware order: Session -> CORS
middleware = [
    Middleware(
        SessionMiddleware,
        secret_key=SESSION_SECRET,
        same_site=SESSION_SAMESITE,
        https_only=SESSION_HTTPS_ONLY,
        max_age=60 * 60 * 8,
        path="/",
        session_cookie=SESSION_STORE_COOKIE,
    ),
    Middleware(
        CORSMiddleware,
        allow_origins=FRONTEND_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    ),
]

app = FastAPI(
    title="Stock Analyst API Runner",
    description="API service for running stock analysis jobs in Docker containers",
    version="1.0.0",
    root_path=ROOT_PATH,
    middleware=middleware,
)

# Routers
app.include_router(oauth_router)
app.include_router(code_router)

# Health endpoint (useful for container probes)
@app.get("/healthz")
def healthz():
    return {"ok": True}

# Global variables
docker_client = None
jobs: Dict[str, Dict] = {}

# Configuration from environment variables
BACKEND_IMAGE = os.getenv("BACKEND_IMAGE", "stock-analyst:latest")
DATA_VOLUME = os.getenv("DATA_VOLUME", "stockdata")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# --------- Utilities for new layout ---------
def job_root(job: Dict) -> str:
    """
    Return canonical path under the Docker-mounted volume for this job:
    /data/<email>/<TICKER>/<timestamp>
    """
    email = (job.get("user_email") or "").strip()
    ticker = (job.get("ticker") or "").upper().strip()
    timestamp = (job.get("timestamp") or "").strip()
    if not (email and ticker and timestamp):
        raise HTTPException(status_code=500, detail="Job missing email/ticker/timestamp")
    # All reads are in containers with /data bound to the named volume
    return f"/data/{email}/{ticker}/{timestamp}"

# ------------- Pydantic models -------------
class JobRequest(BaseModel):
    ticker: str = Field(..., description="Stock ticker symbol (e.g., NVDA)")
    company: str = Field(..., description="Company name (e.g., 'NVIDIA')")
    email: str = Field(..., description="User email for data organization")
    pipeline: Optional[str] = Field(default=None, description="Pipeline to run (default: comprehensive)")
    model: Optional[str] = Field(default=None, description="Financial model type (default: comprehensive)")
    years: Optional[int] = Field(default=None, description="Projection years (default: 5)")
    term_growth: Optional[float] = Field(default=None, description="Terminal growth rate (auto-inferred)")
    wacc: Optional[float] = Field(default=None, description="Override WACC (auto-inferred)")
    strategy: Optional[str] = Field(default=None, description="Force specific forecast strategy")
    query: Optional[str] = Field(default=None, description="Search query for news-related pipelines (e.g., 'AAPL earnings Q3')")
    peers: Optional[str] = Field(default=None, description="Comma-separated peer tickers for comparable analysis (e.g., 'AAPL,MSFT,GOOGL')")
    max_searched: Optional[int] = Field(default=None, description="Maximum articles to scrape (default: 20)")
    min_score: Optional[float] = Field(default=None, description="Minimum relevance score (default: 3.0)")
    max_filtered: Optional[int] = Field(default=None, description="Maximum filtered articles (default: 10)")
    min_confidence: Optional[float] = Field(default=None, description="Minimum confidence (default: 0.5)")
    scaling: Optional[float] = Field(default=None, description="Base scaling factor (default: 0.15)")
    adjustment_cap: Optional[float] = Field(default=None, description="Maximum adjustment % (default: 0.20)")

class NLRequest(BaseModel):
    """Natural language request model that includes the request text plus optional override parameters."""
    request: str = Field(..., description="Natural language request describing the analysis task")
    # Optional overrides - if provided, these take priority over NL-extracted values
    ticker: Optional[str] = Field(default=None, description="Stock ticker symbol (e.g., NVDA)")
    company: Optional[str] = Field(default=None, description="Company name (e.g., 'NVIDIA')")
    email: str = Field(..., description="User email for data organization")
    pipeline: Optional[str] = Field(default=None, description="Pipeline to run (default: comprehensive)")
    model: Optional[str] = Field(default=None, description="Financial model type (default: comprehensive)")
    years: Optional[int] = Field(default=None, description="Projection years (default: 5)")
    term_growth: Optional[float] = Field(default=None, description="Terminal growth rate (auto-inferred)")
    wacc: Optional[float] = Field(default=None, description="Override WACC (auto-inferred)")
    strategy: Optional[str] = Field(default=None, description="Force specific forecast strategy")
    query: Optional[str] = Field(default=None, description="Search query for news-related pipelines (e.g., 'AAPL earnings Q3')")
    peers: Optional[str] = Field(default=None, description="Comma-separated peer tickers for comparable analysis (e.g., 'AAPL,MSFT,GOOGL')")
    max_searched: Optional[int] = Field(default=None, description="Maximum articles to scrape (default: 20)")
    min_score: Optional[float] = Field(default=None, description="Minimum relevance score (default: 3.0)")
    max_filtered: Optional[int] = Field(default=None, description="Maximum filtered articles (default: 10)")
    min_confidence: Optional[float] = Field(default=None, description="Minimum confidence (default: 0.5)")
    scaling: Optional[float] = Field(default=None, description="Base scaling factor (default: 0.15)")
    adjustment_cap: Optional[float] = Field(default=None, description="Maximum adjustment % (default: 0.20)")

class JobResponse(BaseModel):
    job_id: str
    ticker: str
    status: str
    message: str

class JobStatus(BaseModel):
    job_id: str
    ticker: str
    company: Optional[str] = None
    status: str
    progress: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    error: Optional[str] = None
    latest_log: Optional[str] = None
    last_activity: Optional[str] = None
    recent_logs: Optional[List[str]] = None

def process_nl_request(nl_req: NLRequest) -> JobRequest:
    """
    Process natural language request through NLAgent and merge with provided parameters.
    Provided parameters take priority over NL-extracted ones.
    """
    
    def safe_convert_value(value, expected_type, param_name):
        """
        Safely convert LLM-extracted values to expected types with proper error handling.
        """
        if value is None:
            return None
            
        try:
            # Handle string parameters
            if expected_type == str:
                if isinstance(value, list):
                    if len(value) > 0:
                        # Special handling for peers parameter - convert list to comma-separated string
                        if param_name == 'peers':
                            # Join all values with commas, stripping whitespace
                            peer_list = [str(v).strip().upper() for v in value if str(v).strip()]
                            if peer_list:
                                logger.info(f"Converting peer list to comma-separated string: {value} -> {','.join(peer_list)}")
                                return ','.join(peer_list)
                            else:
                                logger.warning(f"Empty peer list after filtering: {value}")
                                return None
                        else:
                            # For other string parameters, take the first element
                            return str(value[0])
                    else:
                        # Empty list
                        logger.warning(f"Empty list provided for {param_name}")
                        return None
                elif isinstance(value, (str, int, float)):
                    # For peers, ensure it's properly formatted (uppercase, no extra spaces)
                    if param_name == 'peers':
                        # Clean up comma-separated peer string
                        peers_str = str(value).strip()
                        if peers_str:
                            peer_list = [p.strip().upper() for p in peers_str.split(',') if p.strip()]
                            if peer_list:
                                cleaned_peers = ','.join(peer_list)
                                logger.info(f"Cleaned peer string: {value} -> {cleaned_peers}")
                                return cleaned_peers
                        return None
                    else:
                        return str(value)
                else:
                    logger.warning(f"Unexpected type for {param_name}: {type(value)}, value: {value}")
                    return str(value)
            
            # Handle integer parameters
            elif expected_type == int:
                if isinstance(value, list):
                    if len(value) > 0:
                        return int(float(value[0]))  # Handle case where list contains string numbers
                    else:
                        logger.warning(f"Empty list provided for {param_name}")
                        return None
                elif isinstance(value, (int, float)):
                    return int(value)
                elif isinstance(value, str):
                    return int(float(value))  # Handle string numbers
                else:
                    logger.warning(f"Cannot convert {param_name} to int: {value}")
                    return None
            
            # Handle float parameters
            elif expected_type == float:
                if isinstance(value, list):
                    if len(value) > 0:
                        return float(value[0])
                    else:
                        logger.warning(f"Empty list provided for {param_name}")
                        return None
                elif isinstance(value, (int, float)):
                    return float(value)
                elif isinstance(value, str):
                    return float(value)
                else:
                    logger.warning(f"Cannot convert {param_name} to float: {value}")
                    return None
            
            # Default: return as-is
            return value
            
        except (ValueError, TypeError, IndexError) as e:
            logger.warning(f"Error converting {param_name} (value: {value}): {e}")
            return None
    
    try:
        # Use GatewayAgent to extract parameters from the request
        gateway_agent = GatewayAgent()
        extracted_args = gateway_agent.process_request(nl_req.request)

        logger.info(f"Gateway Agent extracted: {extracted_args}")

        # Create final parameters by merging extracted and provided values
        # Provided values take priority over extracted ones
        final_args = {}
        
        # Handle ticker - prefer provided, then extracted, then raise error
        if nl_req.ticker:
            final_args['ticker'] = nl_req.ticker
        elif extracted_args.get('ticker'):
            final_args['ticker'] = safe_convert_value(extracted_args['ticker'], str, 'ticker')
        else:
            raise ValueError("No ticker found in request or provided parameters")
            
        # Handle company - prefer provided, then extracted (mapped from company_name), then raise error
        if nl_req.company:
            final_args['company'] = nl_req.company
        elif extracted_args.get('company_name'):
            final_args['company'] = safe_convert_value(extracted_args['company_name'], str, 'company_name')
        elif extracted_args.get('company'):
            final_args['company'] = safe_convert_value(extracted_args['company'], str, 'company')
        else:
            raise ValueError("No company found in request or provided parameters")
            
        # Email is always required from the request
        final_args['email'] = nl_req.email
        
        # For other parameters, prefer provided, then extracted, then None (use defaults)
        # Define expected types for validation
        param_mapping = {
            'pipeline': ('pipeline', str),
            'model': ('model', str), 
            'years': ('years', int),
            'term_growth': ('term_growth', float),
            'wacc': ('wacc', float),
            'strategy': ('strategy', str),
            'query': ('query', str),
            'peers': ('peers', str),
            'max_searched': ('max_searched', int),
            'min_score': ('min_score', float),
            'max_filtered': ('max_filtered', int),
            'min_confidence': ('min_confidence', float),
            'scaling': ('scaling', float),
            'adjustment_cap': ('adjustment_cap', float)
        }
        
        for param_name, (extracted_key, expected_type) in param_mapping.items():
            provided_value = getattr(nl_req, param_name, None)
            if provided_value is not None:
                final_args[param_name] = provided_value
            elif extracted_args.get(extracted_key) is not None:
                converted_value = safe_convert_value(extracted_args[extracted_key], expected_type, param_name)
                if converted_value is not None:
                    final_args[param_name] = converted_value
            # else: leave as None to use defaults
        
        logger.info(f"Final merged parameters: {final_args}")
        
        # Create and return JobRequest with merged parameters
        return JobRequest(**final_args)
        
    except Exception as e:
        logger.error(f"Error processing NL request: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to process natural language request: {str(e)}")

# ------------- Docker helpers -------------
def init_docker_client():
    global docker_client
    try:
        logger.info("Initializing Docker client...")
        docker_client = docker.from_env()
        docker_client.ping()
        logger.info("✅ Docker client connected successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to connect to Docker: {e}")
        docker_client = None
        return False

def ensure_volume_exists():
    if not docker_client:
        return False
    try:
        docker_client.volumes.get(DATA_VOLUME)
        logger.info(f"✅ Volume '{DATA_VOLUME}' exists")
        return True
    except docker.errors.NotFound:
        try:
            docker_client.volumes.create(name=DATA_VOLUME)
            logger.info(f"✅ Created volume '{DATA_VOLUME}'")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to create volume '{DATA_VOLUME}': {e}")
            return False
    except Exception as e:
        logger.error(f"❌ Error checking volume '{DATA_VOLUME}': {e}")
        return False

# ------------- Log monitoring (polls info.log) -------------
async def monitor_info_log(job_id: str, container):
    """Monitor info.log file in real-time and update job progress"""
    try:
        def monitor_logs():
            try:
                job = jobs.get(job_id) or {}
                base = job_root(job)  # /data/<email>/<TICKER>/<timestamp>
                last_info_log_size = 0
                container_running = True

                while container_running:
                    if job_id not in jobs:
                        break

                    # Check if container is still running
                    try:
                        container.reload()
                        container_running = (container.status == 'running')
                    except docker.errors.NotFound:
                        logger.info(f"Container {container.id[:12]} was removed during log monitoring")
                        container_running = False
                    except Exception as e:
                        logger.warning(f"Error checking container status during log monitoring: {e}")
                        container_running = False

                    try:
                        # Get current file size of info.log
                        temp_container = docker_client.containers.run(
                            "alpine:latest",
                            command=f"sh -c 'if [ -f {shlex.quote(base)}/info.log ]; then wc -c {shlex.quote(base)}/info.log | cut -d\" \" -f1; else echo 0; fi'",
                            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                            remove=True,
                            detach=False
                        )
                        current_size = int(temp_container.decode('utf-8').strip())

                        if current_size > last_info_log_size:
                            # Read appended bytes
                            temp_container2 = docker_client.containers.run(
                                "alpine:latest",
                                command=f"sh -c 'if [ -f {shlex.quote(base)}/info.log ]; then tail -c +{last_info_log_size + 1} {shlex.quote(base)}/info.log; fi'",
                                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                                remove=True,
                                detach=False
                            )
                            new_content = temp_container2.decode('utf-8').strip()

                            if new_content:
                                for line in new_content.split('\n'):
                                    if line.strip():
                                        jobs[job_id]["latest_log"] = line.strip()
                                        jobs[job_id]["last_activity"] = datetime.now().isoformat()

                                        jobs[job_id].setdefault("recent_logs", []).append(line.strip())
                                        if len(jobs[job_id]["recent_logs"]) > 100:
                                            jobs[job_id]["recent_logs"].pop(0)

                                        if "Stock Analysis Pipeline Session Started" in line:
                                            jobs[job_id]["progress"] = "Analysis started..."
                                        elif "STAGE: COMPREHENSIVE PIPELINE" in line:
                                            jobs[job_id]["progress"] = "Starting comprehensive analysis..."
                                        elif "STAGE: FINANCIAL SCRAPING" in line:
                                            jobs[job_id]["progress"] = "Scraping financial data..."
                                        elif "STAGE: FINANCIAL MODEL GENERATION" in line:
                                            jobs[job_id]["progress"] = "Generating financial model..."
                                        elif "STAGE: ARTICLE SCRAPING" in line:
                                            jobs[job_id]["progress"] = "Scraping new articles..."
                                        elif "STAGE: ARTICLE FILTERING" in line:
                                            jobs[job_id]["progress"] = "Filtering news articles..."
                                        elif "STAGE: ARTICLE SCREENING" in line:
                                            jobs[job_id]["progress"] = "Screening news articles..."
                                        elif "STAGE: PRICE ADJUSTMENT" in line:
                                            jobs[job_id]["progress"] = "Adjusting price based on news..."
                                        elif "STAGE: PROFESSIONAL REPORT GENERATION" in line:
                                            jobs[job_id]["progress"] = "Generating professional report..."
                                        elif "PIPELINE SESSION COMPLETED" in line:
                                            jobs[job_id]["progress"] = "Finalizing analysis..."
                                        elif "THE ENTIRE PROGRAM IS COMPLETED" in line:
                                            jobs[job_id]["progress"] = "Analysis completed successfully"
                                            jobs[job_id]["status"] = "completed"


                            last_info_log_size = current_size

                    except Exception:
                        # info.log may not exist yet; that's OK
                        pass

                    # If container stopped, do one final read
                    if not container_running:
                        try:
                            final_temp = docker_client.containers.run(
                                "alpine:latest",
                                command=f"sh -c 'if [ -f {shlex.quote(base)}/info.log ]; then tail -c +{last_info_log_size + 1} {shlex.quote(base)}/info.log; fi'",
                                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                                remove=True,
                                detach=False
                            )
                            final_content = final_temp.decode('utf-8').strip()
                            if final_content:
                                for line in final_content.split('\n'):
                                    if line.strip():
                                        jobs[job_id].setdefault("recent_logs", []).append(line.strip())
                                        if "THE ENTIRE PROGRAM IS COMPLETED" in line:
                                            jobs[job_id]["status"] = "completed"
                                            jobs[job_id]["progress"] = "Analysis completed successfully"
                        except:
                            pass
                        break

                    time.sleep(2)

            except Exception as e:
                logger.error(f"Error in log monitoring for job {job_id}: {e}")

        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as executor:
            await loop.run_in_executor(executor, monitor_logs)

    except Exception as e:
        logger.error(f"Error monitoring logs for job {job_id}: {e}")

# ------------- Job runner -------------
async def run_analysis_job(job_id: str, ticker: str, company: Optional[str], 
                         email: Optional[str], timestamp: Optional[str],
                         pipeline: Optional[str] = None, model: Optional[str] = None, 
                         years: Optional[int] = None, term_growth: Optional[float] = None,
                         wacc: Optional[float] = None, strategy: Optional[str] = None,
                         query: Optional[str] = None, peers: Optional[str] = None,
                         max_searched: Optional[int] = None, min_score: Optional[float] = None, 
                         max_filtered: Optional[int] = None, min_confidence: Optional[float] = None,
                         scaling: Optional[float] = None, adjustment_cap: Optional[float] = None):
    """
    Run stock analysis job in Docker container.
    """
    if not docker_client:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = "Docker client not available"
        return

    try:
        jobs[job_id]["status"] = "running"
        jobs[job_id]["progress"] = "Starting analysis container..."
        jobs[job_id]["recent_logs"] = []

        env_vars = {
            "SERPAPI_API_KEY": SERPAPI_API_KEY,
            "OPENAI_API_KEY": OPENAI_API_KEY,
            "DATA_PATH": "/data",
        }

        cmd = ["--ticker", ticker, "--company", company, "--email", email, "--timestamp", timestamp]
        if pipeline:
            cmd.extend(["--pipeline", pipeline])
        if model:
            cmd.extend(["--model", model])
        if years is not None:
            cmd.extend(["--years", str(years)])
        if term_growth is not None:
            cmd.extend(["--term-growth", str(term_growth)])
        if wacc is not None:
            cmd.extend(["--wacc", str(wacc)])
        if strategy:
            cmd.extend(["--strategy", strategy])
        if query:
            cmd.extend(["--query", query])
        if peers:
            cmd.extend(["--peers", peers])
        if max_searched is not None:
            cmd.extend(["--max-searched", str(max_searched)])
        if min_score is not None:
            cmd.extend(["--min-score", str(min_score)])
        if max_filtered is not None:
            cmd.extend(["--max-filtered", str(max_filtered)])
        if min_confidence is not None:
            cmd.extend(["--min-confidence", str(min_confidence)])
        if scaling is not None:
            cmd.extend(["--scaling", str(scaling)])
        if adjustment_cap is not None:
            cmd.extend(["--adjustment-cap", str(adjustment_cap)])

        logger.info(f"🚀 Starting analysis for {ticker} ({company}) with command: {cmd}")
        jobs[job_id]["progress"] = f"Running {pipeline} analysis for {ticker} ({company})..."

        container = docker_client.containers.run(
            BACKEND_IMAGE,
            command=cmd,
            environment=env_vars,
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
            detach=True,
            remove=False,
            name=f"analysis-{job_id}"
        )

        jobs[job_id]["container_id"] = container.id
        jobs[job_id]["progress"] = "Analysis running..."

        loop = asyncio.get_event_loop()
        log_task = asyncio.create_task(monitor_info_log(job_id, container))

        try:
            def wait_for_container():
                return container.wait(timeout=1800)
            
            # Monitor for stop requests while container is running
            stop_check_task = None
            
            async def check_stop_request():
                """Periodically check if job should be stopped"""
                while True:
                    await asyncio.sleep(2)  # Check every 2 seconds
                    current_status = jobs[job_id].get("status")
                    if current_status in ["stopping", "stopped"]:
                        logger.info(f"Stop request detected for job {job_id}, killing container")
                        try:
                            # Check if container still exists before trying to kill it
                            try:
                                container.reload()
                                if container.status in ['running', 'paused']:
                                    container.kill()
                                    logger.info(f"Container {container.id[:12]} killed due to stop request")
                            except docker.errors.NotFound:
                                logger.info(f"Container {container.id[:12]} already removed")
                        except Exception as kill_e:
                            logger.error(f"Error killing container on stop request: {kill_e}")
                        return
            
            # Start stop monitoring task
            stop_check_task = asyncio.create_task(check_stop_request())
            
            try:
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    await loop.run_in_executor(executor, wait_for_container)
            finally:
                if stop_check_task:
                    stop_check_task.cancel()

        except Exception as e:
            logger.error(f"Container wait timeout or error: {e}")
            try:
                # Check if container still exists before trying to kill it
                try:
                    container.reload()
                    if container.status in ['running', 'paused']:
                        container.kill()
                except docker.errors.NotFound:
                    logger.info(f"Container {container.id[:12]} already removed during error handling")
                except Exception as kill_e:
                    logger.error(f"Failed to kill container during error handling: {kill_e}")
                    
                current_progress = jobs[job_id].get("progress", "").lower()
                if "llm analysis" in current_progress or "running llm" in current_progress:
                    jobs[job_id]["status"] = "llm_timeout"
                    jobs[job_id]["error"] = f"LLM analysis timeout after 30 minutes: {str(e)}"
                    jobs[job_id]["progress"] = "LLM analysis timed out - retrying may help"
                else:
                    jobs[job_id]["status"] = "failed"
                    jobs[job_id]["error"] = f"Analysis timeout: {str(e)}"
                log_task.cancel()
                return
            except Exception as cleanup_e:
                logger.error(f"Error during cleanup: {cleanup_e}")

        # Cancel log monitoring task
        log_task.cancel()
        
        # Try to get container exit code, but handle case where container was removed
        exit_code = None
        try:
            container.reload()
            exit_code = container.attrs['State']['ExitCode']
        except docker.errors.NotFound:
            logger.info(f"Container {container.id[:12]} was removed before exit code check")
        except Exception as e:
            logger.warning(f"Could not get container exit code: {e}")

        # Check if job was stopped by user before checking exit code
        current_status = jobs[job_id].get("status")
        if current_status in ["stopping", "stopped"]:
            if current_status == "stopping":
                jobs[job_id]["status"] = "stopped"
                jobs[job_id]["progress"] = "Analysis stopped by user"
            jobs[job_id]["completed_at"] = datetime.now().isoformat()
            logger.info(f"✅ Analysis job {job_id} stopped by user request")
        elif exit_code == 0:
            if jobs[job_id]["status"] != "completed":
                jobs[job_id]["status"] = "completed"
                jobs[job_id]["progress"] = "Analysis completed successfully"
            jobs[job_id]["completed_at"] = datetime.now().isoformat()
            logger.info(f"✅ Analysis job {job_id} completed successfully")
        elif exit_code is not None and exit_code != 0:
            if current_status == "llm_timeout":
                jobs[job_id]["progress"] = "LLM analysis timed out - retrying may help"
            else:
                jobs[job_id]["status"] = "failed"
                jobs[job_id]["error"] = f"Analysis failed with exit code {exit_code}"
                logger.error(f"❌ Analysis job {job_id} failed with exit code {exit_code}")

        # Only try to remove container if job wasn't stopped by user (stop endpoint handles removal)
        current_final_status = jobs[job_id].get("status")
        if current_final_status not in ["stopped"]:
            try:
                container.remove()
                logger.info(f"Container {container.id[:12]} removed after job completion")
            except docker.errors.NotFound:
                logger.info(f"Container {container.id[:12]} already removed")
            except Exception as e:
                logger.warning(f"Failed to remove container: {e}")
        else:
            logger.info(f"Container removal skipped - job was stopped by user")

    except Exception as e:
        logger.error(f"❌ Error in analysis job {job_id}: {e}")
        error_str = str(e).lower()
        current_progress = jobs[job_id].get("progress", "").lower()
        if (("timeout" in error_str or "timed out" in error_str or "llm" in error_str) and 
            ("llm analysis" in current_progress or "running llm" in current_progress)):
            jobs[job_id]["status"] = "llm_timeout"
            jobs[job_id]["error"] = f"LLM analysis timeout: {str(e)}"
            jobs[job_id]["progress"] = "LLM analysis timed out - retrying may help"
        else:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = str(e)

# ------------- Startup / health -------------
@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Starting Stock Analyst API Runner...")
    docker_ready = init_docker_client()
    if not docker_ready:
        logger.warning("⚠️ Docker not available - running in limited mode")
    else:
        ensure_volume_exists()

    if not SERPAPI_API_KEY:
        logger.warning("⚠️ SERPAPI_API_KEY not set")
    else:
        logger.info("✅ SERPAPI_API_KEY configured")
    if not OPENAI_API_KEY:
        logger.warning("⚠️ OPENAI_API_KEY not set")
    else:
        logger.info("✅ OPENAI_API_KEY configured")

    logger.info("✅ API Runner started successfully")

@app.get("/")
async def root():
    return {
        "status": "healthy",
        "service": "Stock Analyst API Runner",
        "docker_available": docker_client is not None,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    docker_status = "connected" if docker_client else "disconnected"
    return {
        "status": "healthy",
        "docker": docker_status,
        "backend_image": BACKEND_IMAGE,
        "data_volume": DATA_VOLUME,
        "api_keys_configured": {
            "serpapi": bool(SERPAPI_API_KEY),
            "openai": bool(OPENAI_API_KEY)
        }
    }

# ------------- Create job -------------
@app.post("/run", response_model=JobResponse)
async def start_analysis(job: JobRequest, background_tasks: BackgroundTasks):
    """
    Start a new stock analysis job
    """
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker service not available")

    if not SERPAPI_API_KEY or not OPENAI_API_KEY:
        raise HTTPException(status_code=400, detail="API keys not configured")
    
    if not job.email:
        raise HTTPException(
            status_code=400,
            detail="Email required (log in first or include 'email' in the request body).",
        )

    # Auth (works for OAuth cookie or email-code session)
    current_timestamp = datetime.now().isoformat()
    user_email = job.email.lower()

    job_id = f"{job.ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    jobs[job_id] = {
        "job_id": job_id,
        "ticker": job.ticker,
        "company": job.company,
        "user_email": user_email,
        "timestamp": current_timestamp,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "progress": "Job queued",
    }

    background_tasks.add_task(
        run_analysis_job,
        job_id, job.ticker, job.company,
        user_email, current_timestamp,
        job.pipeline, job.model, job.years, job.term_growth,
        job.wacc, job.strategy, job.query, job.peers, job.max_searched, job.min_score,
        job.max_filtered, job.min_confidence, job.scaling, job.adjustment_cap,
    )

    return JobResponse(job_id=job_id, status="pending", message="Analysis job started")

# ------------- Natural Language Request Processing -------------
@app.post("/nl/request", response_model=JobResponse)
async def process_nl_request_endpoint(nl_req: NLRequest, background_tasks: BackgroundTasks):
    """
    Process a natural language request for stock analysis.
    
    The request text is processed by NLAgent to extract parameters, which are then
    merged with any explicitly provided parameters (provided parameters take priority).
    """
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker service not available")

    if not SERPAPI_API_KEY or not OPENAI_API_KEY:
        raise HTTPException(status_code=400, detail="API keys not configured")
    
    if not nl_req.email:
        raise HTTPException(status_code=400, detail="User email is required")

    try:
        # Process the natural language request and get merged parameters
        job_request = process_nl_request(nl_req)
        
        # Log the processing result
        logger.info(f"Processed NL request: '{nl_req.request}' -> ticker: {job_request.ticker}, company: {job_request.company}")
        
        # Use the existing start_analysis logic
        current_timestamp = datetime.now().isoformat()
        user_email = job_request.email.lower()

        job_id = f"{job_request.ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        jobs[job_id] = {
            "job_id": job_id,
            "ticker": job_request.ticker,
            "company": job_request.company,
            "user_email": user_email,
            "timestamp": current_timestamp,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "progress": "Job queued from natural language request",
            "original_request": nl_req.request,  # Store the original NL request for reference
        }

        background_tasks.add_task(
            run_analysis_job,
            job_id, job_request.ticker, job_request.company,
            user_email, current_timestamp,
            job_request.pipeline, job_request.model, job_request.years, job_request.term_growth,
            job_request.wacc, job_request.strategy, job_request.query, job_request.peers, job_request.max_searched, job_request.min_score,
            job_request.max_filtered, job_request.min_confidence, job_request.scaling, job_request.adjustment_cap,
        )

        return JobResponse(job_id=job_id, ticker=job_request.ticker, status="pending", message=f"Natural language analysis job started for {job_request.ticker}")

    except Exception as e:
        logger.error(f"Error in NL request processing: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to process request: {str(e)}")


# ------------- Read job(s) -------------
@app.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(**jobs[job_id])

@app.get("/jobs")
async def list_jobs():
    return {"jobs": list(jobs.values())}

@app.get("/jobs/stoppable")
async def list_stoppable_jobs():
    """
    List all jobs that can be stopped (status: pending or running)
    """
    stoppable_jobs = [
        job for job in jobs.values() 
        if job.get("status") in ["pending", "running"]
    ]
    return {
        "stoppable_jobs": stoppable_jobs,
        "count": len(stoppable_jobs)
    }

# ------------- Stop job -------------
@app.post("/jobs/{job_id}/stop", response_model=JobResponse)
async def stop_job(job_id: str):
    """
    Stop a running analysis job gracefully.
    
    This endpoint will:
    1. Change job status to 'stopping'
    2. Send SIGTERM to the Docker container (graceful stop)
    3. Wait up to 10 seconds for graceful shutdown
    4. Clean up the container
    5. Update job status to 'stopped'
    
    Only jobs with status 'pending' or 'running' can be stopped.
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    current_status = job.get("status")
    
    # Check if job can be stopped
    if current_status not in ["pending", "running"]:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot stop job with status '{current_status}'. Only pending or running jobs can be stopped."
        )
    
    container_id = job.get("container_id")
    ticker = job.get("ticker", "Unknown")
    
    try:
        # Update job status immediately
        jobs[job_id]["status"] = "stopping"
        jobs[job_id]["progress"] = "Stopping analysis..."
        jobs[job_id]["latest_log"] = "Job stop requested by user"
        jobs[job_id]["last_activity"] = datetime.now().isoformat()
        
        # If there's a container, stop it gracefully
        if container_id and docker_client:
            try:
                container = docker_client.containers.get(container_id)
                container.reload()
                
                if container.status == "running":
                    logger.info(f"Stopping container {container_id} for job {job_id} ({ticker})")
                    
                    # Try graceful stop first (SIGTERM)
                    container.stop(timeout=10)
                    logger.info(f"Container {container_id} stopped gracefully")
                    
                    # Clean up container
                    try:
                        container.remove()
                        logger.info(f"Container {container_id} removed")
                    except Exception as e:
                        logger.warning(f"Failed to remove container {container_id}: {e}")
                else:
                    logger.info(f"Container {container_id} was not running (status: {container.status})")
                    
            except docker.errors.NotFound:
                logger.info(f"Container {container_id} not found, may have already been removed")
            except Exception as e:
                logger.error(f"Error stopping container {container_id}: {e}")
                # Continue with job cleanup even if container stop fails
        
        # Update final job status
        jobs[job_id]["status"] = "stopped"
        jobs[job_id]["progress"] = "Analysis stopped by user"
        jobs[job_id]["completed_at"] = datetime.now().isoformat()
        jobs[job_id]["error"] = None  # Clear any previous errors
        
        logger.info(f"✅ Successfully stopped job {job_id} ({ticker})")
        
        return {
            "job_id": job_id,
            "ticker": ticker,
            "status": "stopped",
            "message": "Analysis job stopped successfully"
        }
        
    except Exception as e:
        logger.error(f"❌ Error stopping job {job_id}: {e}")
        
        # Update job with error status
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = f"Failed to stop job: {str(e)}"
        jobs[job_id]["completed_at"] = datetime.now().isoformat()
        
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to stop job: {str(e)}"
        )

# ------------- Stream logs (SSE) -------------
@app.get("/jobs/{job_id}/logs/stream")
async def get_job_logs_stream(job_id: str):
    """Stream info.log in real time using Server-Sent Events (with final drain)."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    ticker = (job.get('ticker') or '').upper()
    container_id = job.get('container_id')

    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    base = job_root(job)  # /data/<email>/<TICKER>/<timestamp>

    async def log_generator():
        last_size = 0
        container = None
        decoder = codecs.getincrementaldecoder('utf-8')()
        pending_line = ""
        idle_timer = 0.0
        stable_cycles = 0
        have_seen_any_data = False

        def sse(event_type: str, message: str):
            payload = json.dumps({
                "status": event_type,
                "message": message,
                "timestamp": datetime.now().isoformat()
            })
            try:
                logger.info(f"SSE -> {event_type}: {payload}")
            except Exception:
                pass
            return f"event: {event_type}\ndata: {payload}\n\n"

        yield sse("connection", f"Connected to log stream for {ticker}")

        if container_id:
            try:
                c = docker_client.containers.get(container_id)
                c.reload()
                container = c
                logger.info(f"Found analysis container for {ticker}: {container_id}")
            except docker.errors.NotFound:
                logger.info(f"Analysis container not found for {ticker}, using volume fallback")
                container = None

        async def probe_size_and_read_from_container(start_byte: int):
            if not container:
                return 0, b""
            try:
                container.reload()
                path = f"{base}/info.log"
                size_res = container.exec_run(
                    f"sh -lc 'if [ -f {shlex.quote(path)} ]; then wc -c < {shlex.quote(path)}; else echo 0; fi'"
                )
                if size_res.exit_code != 0:
                    return 0, b""
                current_size = int((size_res.output or b"0").decode().strip() or "0")
                if current_size > start_byte:
                    content_res = container.exec_run(
                        f"sh -lc 'if [ -f {shlex.quote(path)} ]; then tail -c +{start_byte + 1} {shlex.quote(path)}; fi'"
                    )
                    if content_res.exit_code == 0 and content_res.output:
                        return current_size, content_res.output
                return current_size, b""
            except docker.errors.NotFound:
                logger.warning(f"Container {container_id} was removed during SSE log reading")
                return 0, b""
            except Exception as e:
                logger.warning(f"Error reading from container {container_id}: {e}")
                return 0, b""

        async def probe_size_and_read_from_volume(start_byte: int):
            try:
                path = f"{base}/info.log"
                size_out = docker_client.containers.run(
                    "alpine:latest",
                    command=f"sh -lc 'if [ -f {shlex.quote(path)} ]; then wc -c < {shlex.quote(path)}; else echo 0; fi'",
                    volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                    remove=True,
                    detach=False,
                )
                current_size = int((size_out or b"0").decode().strip() or "0")
                if current_size > start_byte:
                    content_out = docker_client.containers.run(
                        "alpine:latest",
                        command=f"sh -lc 'if [ -f {shlex.quote(path)} ]; then tail -c +{start_byte + 1} {shlex.quote(path)}; fi'",
                        volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                        remove=True,
                        detach=False,
                    )
                    return current_size, content_out or b""
                return current_size, b""
            except Exception as e:
                logger.debug(f"Volume read failed: {e}")
                return 0, b""

        def emit_lines(decoded_text: str, flush: bool = True):
            nonlocal pending_line, have_seen_any_data
            text = pending_line + decoded_text
            parts = text.splitlines(True)
            if parts and (flush or parts[-1].endswith(("\n", "\r"))):
                complete, pending = parts, ""
            else:
                complete, pending = parts[:-1], parts[-1] if parts else ""
            for seg in complete:
                line = seg.rstrip("\r\n")
                if not line:
                    continue
                have_seen_any_data = True
                if "ENTIRE PROGRAM" in line:
                    job["status"] = "completed"
                    job["progress"] = "Analysis completed successfully"
                    yield sse("completed", "The entire program is completed")
                else:
                    yield sse("log", line)
            pending_line = pending

        async def drain_once():
            nonlocal container, last_size
            next_size, chunk = 0, b""
            if container:
                next_size, chunk = await probe_size_and_read_from_container(last_size)
                try:
                    container.reload()
                    if container.status != "running":
                        if next_size == last_size:
                            _, extra_chunk = await probe_size_and_read_from_container(last_size)
                            if extra_chunk:
                                chunk += extra_chunk
                                next_size = last_size + len(extra_chunk)
                        container = None
                except Exception:
                    container = None
            if next_size <= last_size:
                v_size, v_chunk = await probe_size_and_read_from_volume(last_size)
                if v_size > last_size:
                    next_size, chunk = v_size, v_chunk
            if chunk:
                decoded = decoder.decode(chunk)
                for evt in emit_lines(decoded):
                    yield evt
                last_size = next_size

        try:
            while True:
                # Check for stopped job at the beginning of each iteration
                current_status = job.get("status")
                if current_status == "stopped":
                    yield sse("completed", "Analysis stopped by user")
                    break
                
                something_new = False
                async for evt in drain_once():
                    something_new = True
                    idle_timer = 0.0
                    try:
                        evt_str = str(evt)
                    except Exception:
                        evt_str = None
                    if evt_str and not evt_str.startswith("event:"):
                        logger.info(f"SSE -> raw: {evt_str.strip()}")
                    yield evt

                if not something_new:
                    idle_timer += POLL_INTERVAL_SEC
                    if idle_timer >= IDLE_HEARTBEAT_SEC:
                        idle_timer = 0.0
                        heartbeat = ": keep-alive\n\n"
                        logger.info(f"SSE -> heartbeat: {heartbeat.strip()}")
                        yield heartbeat
                        if not have_seen_any_data:
                            yield sse("status", "Analysis running, waiting for log output..." if container else "Waiting for analysis to start...")

                job_status = job.get("status")
                job_done = job_status in {"completed", "failed", "stopped", "stopping"}
                
                # Immediate exit for stopped jobs to prevent continued streaming
                if job_status == "stopped":
                    yield sse("completed", "Analysis stopped by user")
                    await asyncio.sleep(0.25)
                    break
                
                container_running = False
                if container:
                    try:
                        container.reload()
                        container_running = (container.status == "running")
                    except docker.errors.NotFound:
                        logger.info(f"Container {container_id} was removed during SSE streaming")
                        container_running = False
                        container = None
                    except Exception as e:
                        logger.warning(f"Error checking container status during SSE: {e}")
                        container_running = False
                        container = None

                if job_done and not container_running:
                    if something_new:
                        stable_cycles = 0
                    else:
                        stable_cycles += 1
                    if stable_cycles >= FINAL_DRAIN_STABLE_CYCLES:
                        for evt in emit_lines("", flush=True):
                            yield evt
                        yield sse("completed", "Analysis completed")
                        await asyncio.sleep(0.25)
                        break

                await asyncio.sleep(POLL_INTERVAL_SEC)

        except Exception as e:
            logger.error(f"Error streaming info.log for job {job_id}: {e}")
            yield sse("error", f"Stream error: {str(e)}")

    return StreamingResponse(
        log_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Vary": "Origin",
        },
    )

# ------------- Simple logs snapshot -------------
@app.get("/jobs/{job_id}/logs")
async def get_job_logs(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "progress": job.get("progress"),
        "error": job.get("error"),
        "recent_logs": job.get("recent_logs", []),
        "latest_log": job.get("latest_log"),
        "last_activity": job.get("last_activity")
    }

# ------------- Download specific file -------------
@app.get("/jobs/{job_id}/files/{filename}")
async def download_file(job_id: str, filename: str):
    """
    Download a specific top-level file from job results.
    Supported: info.log, screening_report.md, screening_data.json, price_adjustment_explanation_latest.md, 
               technical_analysis_latest.md, professional_analyst_report_latest.md
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    job = jobs[job_id]
    base = job_root(job)

    supported = {
        "info.log": "text/plain",
        "screening_report.md": "text/markdown",
        "screening_data.json": "application/json",
        "price_adjustment_explanation_latest.md": "text/markdown",
        "technical_analysis_latest.md": "text/markdown",
        "professional_analyst_report_latest.md": "text/markdown",
    }
    if filename not in supported:
        raise HTTPException(status_code=400, detail=f"Unsupported filename '{filename}'. Supported: {list(supported.keys())}")

    try:
        if filename.startswith("price_adjustment_explanation") or \
           filename.startswith("technical_analysis") or \
           filename.startswith("professional_analyst_report"):
            path = f"{base}/reports/{filename}"
        elif filename.startswith("screening"):
            path = f"{base}/screened/{filename}"
        else:
            path = f"{base}/{filename}"

        result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'cat {shlex.quote(path)}'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
            remove=True,
            detach=False
        )
        media_type = supported[filename]
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return StreamingResponse(BytesIO(result), media_type=media_type, headers=headers)
    except Exception as e:
        logger.error(f"Error reading {filename} for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read {filename}: {str(e)}")

# ------------- Get full info.log as JSON -------------
@app.get("/jobs/{job_id}/info-log")
async def get_info_log(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    job = jobs[job_id]
    base = job_root(job)

    try:
        temp_container = docker_client.containers.run(
            "alpine:latest",
            command=f"cat {shlex.quote(base)}/info.log",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
            remove=True,
            detach=False
        )
        log_content = temp_container.decode('utf-8')
        return {
            "job_id": job_id,
            "ticker": job.get('ticker', '').upper(),
            "log_content": log_content,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error reading info.log for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read info.log: {str(e)}")

# ------------- Zip searched articles -------------
@app.get("/jobs/{job_id}/download/searched-articles")
async def download_searched_articles(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    job = jobs[job_id]
    base = job_root(job)
    ticker = (job.get('ticker') or '').upper()

    try:
        list_result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'if [ -d {shlex.quote(base)}/searched ]; then find {shlex.quote(base)}/searched -name \"*.md\" -type f; fi'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
            remove=True,
            detach=False
        )
        file_list = [p.strip() for p in list_result.decode('utf-8').splitlines() if p.strip().endswith(".md")]

        if not file_list:
            raise HTTPException(status_code=404, detail="No searched articles found")

        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zipf:
            for path in file_list:
                try:
                    content = docker_client.containers.run(
                        "alpine:latest",
                        command=f"sh -c 'cat {shlex.quote(path)}'",
                        volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                        remove=True,
                        detach=False
                    )
                    zipf.writestr(os.path.basename(path), content)
                except Exception as e:
                    logger.warning(f"Could not read file {path}: {e}")
                    continue

        buf.seek(0)
        headers = {"Content-Disposition": f'attachment; filename="{ticker}_searched_articles.zip"'}
        return StreamingResponse(buf, media_type="application/zip", headers=headers)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating searched articles zip for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not create zip file: {str(e)}")

# ------------- Zip filtered articles -------------
@app.get("/jobs/{job_id}/download/filtered-articles")
async def download_filtered_articles(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    job = jobs[job_id]
    base = job_root(job)
    ticker = (job.get('ticker') or '').upper()

    try:
        list_result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'if [ -d {shlex.quote(base)}/filtered ]; then find {shlex.quote(base)}/filtered -name \"filtered_*.md\" -type f; fi'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
            remove=True,
            detach=False
        )
        file_list = [p.strip() for p in list_result.decode('utf-8').splitlines() if p.strip().endswith(".md")]

        if not file_list:
            raise HTTPException(status_code=404, detail="No filtered articles found")

        # Try to include index CSV if present
        index_content: Optional[bytes] = None
        try:
            idx = docker_client.containers.run(
                "alpine:latest",
                command=f"sh -c 'cat {shlex.quote(base)}/filtered/filtered_articles_index.csv'",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                remove=True,
                detach=False
            )
            index_content = idx if idx else None
        except Exception:
            index_content = None

        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zipf:
            for path in file_list:
                try:
                    content = docker_client.containers.run(
                        "alpine:latest",
                        command=f"sh -c 'cat {shlex.quote(path)}'",
                        volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                        remove=True,
                        detach=False
                    )
                    zipf.writestr(os.path.basename(path), content)
                except Exception as e:
                    logger.warning(f"Could not read file {path}: {e}")
                    continue

            if index_content:
                zipf.writestr("filtered_articles_index.csv", index_content)

        buf.seek(0)
        headers = {"Content-Disposition": f'attachment; filename="{ticker}_filtered_articles.zip"'}
        return StreamingResponse(buf, media_type="application/zip", headers=headers)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating filtered articles zip for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not create zip file: {str(e)}")

# ------------- Convert screening_report.md -> PDF -------------
@app.get("/jobs/{job_id}/download/screening-report")
async def download_screening_report(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    job = jobs[job_id]
    base = job_root(job)
    ticker = (job.get('ticker') or '').upper()

    try:
        result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'cat {shlex.quote(base)}/screened/screening_report.md'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
            remove=True,
            detach=False
        )
        if not result:
            raise HTTPException(status_code=404, detail="Screening report not found")
        md_content = result.decode('utf-8')
        pdf_bytes = convert_md_to_pdf(md_content, ticker)
        headers = {"Content-Disposition": f'attachment; filename="{ticker}_screening_report.pdf"'}
        return StreamingResponse(BytesIO(pdf_bytes), media_type='application/pdf', headers=headers)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading converted PDF screening report for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read or convert screening report: {str(e)}")

# ------------- Tar all results (entire timestamp folder) -------------
@app.get("/jobs/{job_id}/download/all-results")
async def download_all_results(job_id: str):
    """
    Stream ALL analysis results (entire /data/<email>/<TICKER>/<timestamp>) as a tar.
    No helper container; reads directly from the mounted volume inside this API container.
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    ticker = (job.get("ticker") or "").upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker not found for job")

    # Ensure docker client exists only if you use it elsewhere
    # (Not needed for this endpoint, but leaving your global check pattern intact is fine.)

    # Container-view path (this API container must mount the data volume at /data)
    base = Path(job_root(job))  # e.g., /data/<email>/<TICKER>/<timestamp>

    # Quick sanity: ensure the base path is under /data and exists
    try:
        base.relative_to("/data")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid job data path")

    if not base.exists() or not base.is_dir():
        raise HTTPException(status_code=404, detail="No data found for job")

    # Nice tar filename
    safe_ts = (job.get("timestamp") or "").replace(":", "-")
    tar_name = f"{ticker}_{safe_ts}_complete_analysis.tar"

    # Build the tar command: tar -C / -cf - -- data/<email>/<TICKER>/<timestamp>
    # We tar from / so the archive contains "data/<email>/<TICKER>/<timestamp>" (no absolute paths).
    tar_cmd = [
        "tar", "-C", "/", "-cf", "-", "--", str(base).lstrip("/"),
    ]

    def tar_generator():
        proc = None
        try:
            # Start subprocess without shell; safer and faster.
            proc = subprocess.Popen(
                tar_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )

            # Stream stdout in chunks
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk

            # Wait for process to exit and check status
            ret = proc.wait(timeout=10)
            if ret != 0:
                # Capture stderr to include in logs / error message
                err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "ignore")
                logger.error("tar exited with code %s: %s", ret, err.strip())
                # Raising here would break mid-stream for clients; at this point
                # we've already ended the stream. Optionally you can log only.
        except Exception as e:
            logger.error("Error streaming tar for %s: %s", job_id, e)
            # If something goes wrong early, yield nothing further and let the client see an incomplete stream.
            # Alternatively, re-raise to let FastAPI return a 500 before sending any bytes.
            # raise
        finally:
            # Best-effort cleanup
            if proc is not None:
                try:
                    if proc.poll() is None:
                        proc.kill()
                except Exception:
                    pass
                try:
                    if proc.stderr:
                        _ = proc.stderr.read()
                except Exception:
                    pass

    headers = {
        "Content-Disposition": f'attachment; filename="{tar_name}"',
        "Cache-Control": "no-store, private",
        # Disable proxy buffering to start download immediately (helps behind Caddy/Nginx)
        "X-Accel-Buffering": "no",
    }

    return StreamingResponse(
        tar_generator(),
        media_type="application/x-tar",
        headers=headers,
    )

# ------------- Download financials_annual JSON -------------
@app.get("/jobs/{job_id}/download/financials-annual")
async def download_financials_annual(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    job = jobs[job_id]
    base = job_root(job)
    ticker = (job.get('ticker') or '').upper()

    try:
        result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'cat {shlex.quote(base)}/financials/financials_annual_modeling_latest.json'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
            remove=True,
            detach=False
        )
        if not result:
            raise HTTPException(status_code=404, detail="Financials annual JSON not found")

        headers = {"Content-Disposition": f'attachment; filename="{ticker}_financials_annual_modeling_latest.json"'}
        return StreamingResponse(BytesIO(result), media_type='application/json', headers=headers)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading financials annual for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read financials annual file: {str(e)}")

# ------------- Download peer financials -------------
@app.get("/jobs/{job_id}/download/peer-financials")
async def download_peer_financials(job_id: str):
    """
    Download financials for all peer companies as a ZIP file.
    Only includes peer financials, not the main company.
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    job = jobs[job_id]
    base = job_root(job)
    ticker = (job.get('ticker') or '').upper()

    try:
        # Find peer directories
        peer_base = str(Path(base))  # Search /data/<email>/<TICKER>/<timestamp>
        logger.info(f"Looking for peer directories in: {peer_base}")
        
        # First, let's see what directories actually exist
        ls_result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'ls -la {shlex.quote(peer_base)} 2>/dev/null || echo \"Directory not found\"'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
            remove=True,
            detach=False
        )
        logger.info(f"Directory listing: {ls_result.decode('utf-8')}")
        
        peer_list_result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'find {shlex.quote(peer_base)} -maxdepth 1 -type d -name \"temp_peer_*\" | head -20'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
            remove=True,
            detach=False
        )
        
        peer_dirs = [p.strip() for p in peer_list_result.decode('utf-8').splitlines() if p.strip()]
        logger.info(f"Found peer directories: {peer_dirs}")
        
        if not peer_dirs:
            raise HTTPException(status_code=404, detail=f"No peer financials found. Searched in: {peer_base}. Available directories: {ls_result.decode('utf-8').strip()}")
        
        # Create ZIP file with peer financials
        buf = BytesIO()
        peer_count = 0
        
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zipf:
            for peer_dir in peer_dirs:
                try:
                    # Extract peer ticker from directory name (temp_peer_AAPL -> AAPL)
                    peer_ticker = peer_dir.split('_')[-1].upper()
                    peer_financials_path = f"{peer_dir}/financials/financials_annual_modeling_latest.json"
                    
                    peer_result = docker_client.containers.run(
                        "alpine:latest",
                        command=f"sh -c 'if [ -f {shlex.quote(peer_financials_path)} ]; then cat {shlex.quote(peer_financials_path)}; fi'",
                        volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                        remove=True,
                        detach=False
                    )
                    
                    if peer_result:
                        zipf.writestr(f"{peer_ticker}_financials_annual_modeling_latest.json", peer_result)
                        peer_count += 1
                        logger.info(f"Added peer financials for {peer_ticker}")
                    else:
                        logger.warning(f"No financials found for peer {peer_ticker}")
                        
                except Exception as e:
                    logger.error(f"Error processing peer financials for {peer_dir}: {e}")
                    continue
        
        if peer_count == 0:
            raise HTTPException(status_code=404, detail="No valid peer financial data found")
        
        buf.seek(0)
        headers = {"Content-Disposition": f'attachment; filename="{ticker}_peer_financials_annual.zip"'}
        return StreamingResponse(buf, media_type="application/zip", headers=headers)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating peer financials ZIP for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not create peer financials ZIP file: {str(e)}")

# ------------- Download financial model Excel -------------
@app.get("/jobs/{job_id}/download/financial-model")
async def download_financial_model(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    ticker = (job.get("ticker") or "").upper()

    # Container-side path (the API container sees the volume at /data)
    base = Path(job_root(job))  # e.g. /data/<email>/<TICKER>/<timestamp>
    xlsx_path = base / "models" / "financial_model_comprehensive_latest.xlsx"

    # Optional: small stability wait to avoid half-written ZIP
    for _ in range(6):  # up to ~3s
        if xlsx_path.exists() and xlsx_path.stat().st_size > 0:
            break
        time.sleep(0.5)

    if not xlsx_path.exists():
        raise HTTPException(status_code=404, detail="Financial model Excel not found")

    return FileResponse(
        path=str(xlsx_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{ticker}_financial_model_comprehensive_latest.xlsx",
        headers={
            "Cache-Control": "no-store, private",
            "Content-Disposition": f'attachment; filename="{ticker}_financial_model_comprehensive_latest.xlsx"',
            "Content-Transfer-Encoding": "binary",
        },
    )

@app.get("/jobs/{job_id}/download/financial-model-comparable")
async def download_financial_model_comparable(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    ticker = (job.get("ticker") or "").upper()

    # Container-side path (the API container sees the volume at /data)
    base = Path(job_root(job))  # e.g. /data/<email>/<TICKER>/<timestamp>
    xlsx_path = base / "models" / "financial_model_comparable_latest.xlsx"

    # Optional: small stability wait to avoid half-written ZIP
    for _ in range(6):  # up to ~3s
        if xlsx_path.exists() and xlsx_path.stat().st_size > 0:
            break
        time.sleep(0.5)

    if not xlsx_path.exists():
        raise HTTPException(status_code=404, detail="Financial model Excel not found")

    return FileResponse(
        path=str(xlsx_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{ticker}_financial_model_comparable_latest.xlsx",
        headers={
            "Cache-Control": "no-store, private",
            "Content-Disposition": f'attachment; filename="{ticker}_financial_model_comparable_latest.xlsx"',
            "Content-Transfer-Encoding": "binary",
        },
    )

# ------------- Download filtered_report.md -------------
@app.get("/jobs/{job_id}/download/filtered-report")
async def download_filtered_report(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    job = jobs[job_id]
    base = job_root(job)
    ticker = (job.get('ticker') or '').upper()

    try:
        result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'cat {shlex.quote(base)}/filtered/filtered_report.md'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
            remove=True,
            detach=False
        )
        if not result:
            raise HTTPException(status_code=404, detail="Filtered report not found")

        headers = {"Content-Disposition": f'attachment; filename="{ticker}_filtered_report.md"'}
        return StreamingResponse(BytesIO(result), media_type='text/markdown', headers=headers)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading filtered report for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read filtered report file: {str(e)}")

# ------------- Download technical_analysis_latest.md -------------
@app.get("/jobs/{job_id}/download/technical-analysis")
async def download_technical_analysis(job_id: str):
    """
    Download technical analysis report as PDF
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    job = jobs[job_id]
    base = job_root(job)
    ticker = (job.get('ticker') or '').upper()

    try:
        result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'cat {shlex.quote(base)}/reports/technical_analysis_latest.md'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
            remove=True,
            detach=False
        )
        if not result:
            raise HTTPException(status_code=404, detail="Technical analysis report not found")
        
        md_content = result.decode('utf-8')
        pdf_bytes = convert_md_to_pdf(md_content, ticker)
        headers = {"Content-Disposition": f'attachment; filename="{ticker}_technical_analysis_latest.pdf"'}
        return StreamingResponse(BytesIO(pdf_bytes), media_type='application/pdf', headers=headers)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading technical analysis for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read technical analysis file: {str(e)}")

# ------------- Download professional_analyst_report_latest.md -------------
@app.get("/jobs/{job_id}/download/professional-analyst-report")
async def download_professional_analyst_report(job_id: str):
    """
    Download professional analyst report as PDF
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    job = jobs[job_id]
    base = job_root(job)
    ticker = (job.get('ticker') or '').upper()

    try:
        result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'cat {shlex.quote(base)}/reports/professional_analyst_report_latest.md'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
            remove=True,
            detach=False
        )
        if not result:
            raise HTTPException(status_code=404, detail="Professional analyst report not found")
        
        md_content = result.decode('utf-8')
        pdf_bytes = convert_md_to_pdf(md_content, ticker)
        headers = {"Content-Disposition": f'attachment; filename="{ticker}_professional_analyst_report_latest.pdf"'}
        return StreamingResponse(BytesIO(pdf_bytes), media_type='application/pdf', headers=headers)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading professional analyst report for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read professional analyst report file: {str(e)}")

# ------------- List job files -------------
@app.get("/jobs/{job_id}/files")
async def list_job_files(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    job = jobs[job_id]
    base = job_root(job)

    try:
        files = {
            "info_log": False,
            "screening_report": False,
            "screening_data": False,
            "searched_articles_count": 0,
            "filtered_articles_count": 0,
            "financials_annual": False,
            "peer_financials": False,
            "financial_model": False,
            "financial_model_comparable": False,
            "filtered_report": False,
            "price_adjustment_explanation": False,
            "technical_analysis": False,
            "professional_analyst_report": False,
        }

        # info.log
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f {shlex.quote(base)}/info.log",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                remove=True,
                detach=False
            )
            files["info_log"] = True
        except:
            pass

        # screening_report.md
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f {shlex.quote(base)}/screened/screening_report.md",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                remove=True,
                detach=False
            )
            files["screening_report"] = True
        except:
            pass

        # screening_data.json
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f {shlex.quote(base)}/screened/screening_data.json",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                remove=True,
                detach=False
            )
            files["screening_data"] = True
        except:
            pass

        # searched/*.md count
        try:
            searched_list = docker_client.containers.run(
                "alpine:latest",
                command=f"sh -c 'if [ -d {shlex.quote(base)}/searched ]; then ls {shlex.quote(base)}/searched/*.md 2>/dev/null | wc -l; else echo 0; fi'",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                remove=True,
                detach=False
            )
            files["searched_articles_count"] = int((searched_list.decode('utf-8').strip() or "0"))
        except:
            files["searched_articles_count"] = 0

        # filtered/filtered_*.md count
        try:
            filtered_list = docker_client.containers.run(
                "alpine:latest",
                command=f"sh -c 'if [ -d {shlex.quote(base)}/filtered ]; then ls {shlex.quote(base)}/filtered/filtered_*.md 2>/dev/null | wc -l; else echo 0; fi'",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                remove=True,
                detach=False
            )
            files["filtered_articles_count"] = int((filtered_list.decode('utf-8').strip() or "0"))
        except:
            files["filtered_articles_count"] = 0

        # financials/financials_annual_modeling_latest.json
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f {shlex.quote(base)}/financials/financials_annual_modeling_latest.json",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                remove=True,
                detach=False
            )
            files["financials_annual"] = True
        except:
            pass

        # models/financial_model_comprehensive_latest.xlsx
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f {shlex.quote(base)}/models/financial_model_comprehensive_latest.xlsx",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                remove=True,
                detach=False
            )
            files["financial_model"] = True
        except:
            pass

        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f {shlex.quote(base)}/models/financial_model_comparable_latest.xlsx",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                remove=True,
                detach=False
            )
            files["financial_model_comparable"] = True
        except:
            pass

        # filtered_report.md
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f {shlex.quote(base)}/filtered/filtered_report.md",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                remove=True,
                detach=False
            )
            files["filtered_report"] = True
        except:
            pass

        # models/price_adjustment_explanation_latest.md
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f {shlex.quote(base)}/reports/price_adjustment_explanation_latest.md",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                remove=True,
                detach=False
            )
            files["price_adjustment_explanation"] = True
        except:
            pass

        # reports/technical_analysis_latest.md
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f {shlex.quote(base)}/reports/technical_analysis_latest.md",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                remove=True,
                detach=False
            )
            files["technical_analysis"] = True
        except:
            pass

        # reports/professional_analyst_report_latest.md
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f {shlex.quote(base)}/reports/professional_analyst_report_latest.md",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                remove=True,
                detach=False
            )
            files["professional_analyst_report"] = True
        except:
            pass

        # Check for peer financials
        try:
            peer_base = str(Path(base).parent)  # Go up one level
            docker_client.containers.run(
                "alpine:latest",
                command=f"sh -c 'find {shlex.quote(peer_base)} -maxdepth 1 -type d -name \"temp_peer_*\" | head -20'",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
                remove=True,
                detach=False
            )
            files["peer_financials"] = True   
        except Exception:
            pass

        return {
            "job_id": job_id,
            "ticker": job.get("ticker"),
            "files": files,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error listing files for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not list files: {str(e)}")

# ------------- Detailed status -------------
@app.get("/jobs/{job_id}/status/detailed")
async def get_detailed_job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]

    # Get file information
    try:
        files_info = await list_job_files(job_id)
        files = files_info.get("files", {})
    except:
        files = {}

    return {
        "job_id": job_id,
        "ticker": job.get("ticker"),
        "company": job.get("company"),
        "status": job.get("status"),
        "progress": job.get("progress"),
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
        "duration": job.get("duration"),
        "completed_stages": job.get("completed_stages"),
        "error": job.get("error"),
        "analysis_completed": job.get("analysis_completed", False),
        "last_activity": job.get("last_activity"),
        "files_available": files,
        "recent_logs_count": len(job.get("recent_logs", [])),
        "latest_log": job.get("latest_log")
    }

# ------------- Entrypoint -------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, workers=1)
