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
import subprocess
import uuid
import markdown
import codecs

import docker
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
import aiofiles
import uvicorn
from fastapi import Response
import shlex, time, io, zipfile, hashlib

# Tunables
POLL_INTERVAL_SEC = 0.75
IDLE_HEARTBEAT_SEC = 12
FINAL_DRAIN_STABLE_CYCLES = 3  # how many consecutive polls with no growth before we finalize


# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # If python-dotenv is not installed, we'll just use os.getenv
    pass

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Stock Analyst API Runner",
    description="API service for running stock analysis jobs in Docker containers",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global variables
docker_client = None
jobs: Dict[str, Dict] = {}

# Configuration from environment variables
BACKEND_IMAGE = os.getenv("BACKEND_IMAGE", "stock-analyst:latest")
DATA_VOLUME = os.getenv("DATA_VOLUME", "stockdata")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Pydantic models
class JobRequest(BaseModel):
    """Request model for running stock analysis jobs.
    
    Only ticker and company are required - all other parameters use main.py defaults.
    Since we always use mapped deltas and LLM scenarios in production, these are not exposed.
    """
    ticker: str = Field(..., description="Stock ticker symbol (e.g., NVDA)")
    company: str = Field(..., description="Company name (e.g., 'NVIDIA')")
    # Optional parameters - main.py provides all defaults
    pipeline: Optional[str] = Field(default=None, description="Pipeline to run (default: comprehensive)")
    model: Optional[str] = Field(default=None, description="Financial model type (default: comprehensive)")
    years: Optional[int] = Field(default=None, description="Projection years (default: 5)")
    term_growth: Optional[float] = Field(default=None, description="Terminal growth rate (auto-inferred)")
    wacc: Optional[float] = Field(default=None, description="Override WACC (auto-inferred)")
    strategy: Optional[str] = Field(default=None, description="Force specific forecast strategy")
    max_articles: Optional[int] = Field(default=None, description="Maximum articles to scrape (default: 20)")
    min_score: Optional[float] = Field(default=None, description="Minimum relevance score (default: 3.0)")
    max_filtered: Optional[int] = Field(default=None, description="Maximum filtered articles (default: 10)")
    min_confidence: Optional[float] = Field(default=None, description="Minimum confidence (default: 0.5)")
    scaling: Optional[float] = Field(default=None, description="Base scaling factor (default: 0.15)")
    adjustment_cap: Optional[float] = Field(default=None, description="Maximum adjustment % (default: 0.20)")

class JobResponse(BaseModel):
    job_id: str
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


def init_docker_client():
    """Initialize Docker client with proper error handling"""
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
    """Ensure the data volume exists"""
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


async def monitor_info_log(job_id: str, container):
    """Monitor info.log file in real-time and update job progress"""
    try:
        def monitor_logs():
            try:
                ticker = jobs[job_id].get('ticker', '').upper()
                last_info_log_size = 0
                container_running = True

                while container_running:
                    if job_id not in jobs:
                        break

                    # Check if container is still running
                    try:
                        container.reload()
                        container_running = container.status == 'running'
                    except:
                        container_running = False

                    try:
                        # Monitor info.log for real-time updates - Get file size
                        temp_container = docker_client.containers.run(
                            "alpine:latest",
                            command=f"sh -c 'if [ -f /data/{ticker}/info.log ]; then wc -c /data/{ticker}/info.log | cut -d\" \" -f1; else echo 0; fi'",
                            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                            remove=True,
                            detach=False
                        )
                        current_size = int(temp_container.decode('utf-8').strip())

                        if current_size > last_info_log_size:
                            # Get new content from info.log
                            temp_container2 = docker_client.containers.run(
                                "alpine:latest",
                                command=f"sh -c 'if [ -f /data/{ticker}/info.log ]; then tail -c +{last_info_log_size + 1} /data/{ticker}/info.log; fi'",
                                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                                remove=True,
                                detach=False
                            )
                            new_content = temp_container2.decode('utf-8').strip()

                            if new_content:
                                # Process each new line from info.log
                                for line in new_content.split('\n'):
                                    if line.strip():
                                        # Store latest log line
                                        jobs[job_id]["latest_log"] = line.strip()
                                        jobs[job_id]["last_activity"] = datetime.now().isoformat()

                                        # Store recent logs from info.log
                                        if "recent_logs" not in jobs[job_id]:
                                            jobs[job_id]["recent_logs"] = []
                                        jobs[job_id]["recent_logs"].append(line.strip())
                                        if len(jobs[job_id]["recent_logs"]) > 100:
                                            jobs[job_id]["recent_logs"].pop(0)

                                        # Parse specific logging patterns for progress updates
                                        if "🏁 THE ENTIRE PROGRAM IS COMPLETED" in line:
                                            jobs[job_id]["progress"] = "Analysis completed successfully"
                                            jobs[job_id]["status"] = "completed"
                                        elif "STAGE: ARTICLE SCRAPING" in line:
                                            jobs[job_id]["progress"] = "Scraping articles..."
                                        elif "STAGE: ARTICLE FILTERING" in line:
                                            jobs[job_id]["progress"] = "Filtering articles..."
                                        elif "STAGE: ARTICLE SCREENING" in line:
                                            jobs[job_id]["progress"] = "Running LLM analysis..."
                                        elif "Pipeline initialized for" in line:
                                            jobs[job_id]["progress"] = "Pipeline initialized"
                                        elif "ERROR" in line:
                                            error_msg = line.split("|")[-1].strip() if "|" in line else line
                                            jobs[job_id]["error"] = error_msg
                                            
                                            # Distinguish between LLM timeout and other failures
                                            if any(timeout_keyword in error_msg.lower() for timeout_keyword in 
                                                  ["timeout", "timed out", "connection timeout", "read timeout", 
                                                   "llm", "openai timeout", "api timeout"]):
                                                jobs[job_id]["status"] = "llm_timeout"
                                                jobs[job_id]["progress"] = "LLM analysis timed out - retrying may help"
                                            else:
                                                jobs[job_id]["status"] = "failed"

                            last_info_log_size = current_size

                    except Exception as info_log_error:
                        # Info.log might not exist yet, that's OK
                        pass

                    # If container stopped, do one final read
                    if not container_running:
                        try:
                            final_temp = docker_client.containers.run(
                                "alpine:latest",
                                command=f"sh -c 'if [ -f /data/{ticker}/info.log ]; then tail -c +{last_info_log_size + 1} /data/{ticker}/info.log; fi'",
                                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                                remove=True,
                                detach=False
                            )
                            final_content = final_temp.decode('utf-8').strip()
                            if final_content:
                                for line in final_content.split('\n'):
                                    if line.strip():
                                        jobs[job_id]["recent_logs"].append(line.strip())
                                        if "🏁 THE ENTIRE PROGRAM IS COMPLETED" in line:
                                            jobs[job_id]["status"] = "completed"
                                            jobs[job_id]["progress"] = "Analysis completed successfully"
                        except:
                            pass
                        break

                    # Wait before next check
                    time.sleep(2)

            except Exception as e:
                logger.error(f"Error in log monitoring for job {job_id}: {e}")

        # Run log monitoring in thread pool
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as executor:
            await loop.run_in_executor(executor, monitor_logs)

    except Exception as e:
        logger.error(f"Error monitoring logs for job {job_id}: {e}")


async def run_analysis_job(job_id: str, ticker: str, company: Optional[str] = None, 
                         pipeline: Optional[str] = None, model: Optional[str] = None, 
                         years: Optional[int] = None, term_growth: Optional[float] = None,
                         wacc: Optional[float] = None, strategy: Optional[str] = None,
                         max_articles: Optional[int] = None, min_score: Optional[float] = None, 
                         max_filtered: Optional[int] = None, min_confidence: Optional[float] = None,
                         scaling: Optional[float] = None, adjustment_cap: Optional[float] = None):
    """
    Run stock analysis job in Docker container.
    
    All parameters except ticker and company are optional - main.py provides sensible defaults.
    Mapped deltas and LLM scenarios are always enabled in production.
    """
    if not docker_client:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = "Docker client not available"
        return

    try:
        jobs[job_id]["status"] = "running"
        jobs[job_id]["progress"] = "Starting analysis container..."
        jobs[job_id]["recent_logs"] = []

        # Prepare environment variables
        env_vars = {
            "SERPAPI_API_KEY": SERPAPI_API_KEY,
            "OPENAI_API_KEY": OPENAI_API_KEY,
            "DATA_PATH": "/data",  # Tell backend to use /data path
        }

        # Prepare command - backend requires both ticker and company
        if not company:
            # Try to infer company name from common tickers
            company_map = {
                "AAPL": "Apple Inc",
                "GOOGL": "Alphabet Inc",
                "MSFT": "Microsoft Corporation",
                "AMZN": "Amazon.com Inc",
                "TSLA": "Tesla Inc",
                "NVDA": "NVIDIA Corporation",
                "META": "Meta Platforms Inc",
                "NFLX": "Netflix Inc"
            }
            company = company_map.get(ticker.upper(), f"{ticker.upper()} Corporation")

        cmd = ["--ticker", ticker, "--company", company]
        
        # Only add optional parameters if they're provided (not None)
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
        
        # Add news analysis parameters if provided
        if max_articles is not None:
            cmd.extend(["--max-articles", str(max_articles)])
        if min_score is not None:
            cmd.extend(["--min-score", str(min_score)])
        if max_filtered is not None:
            cmd.extend(["--max-filtered", str(max_filtered)])
        if min_confidence is not None:
            cmd.extend(["--min-confidence", str(min_confidence)])
        
        # Add price adjustment parameters if provided
        if scaling is not None:
            cmd.extend(["--scaling", str(scaling)])
        if adjustment_cap is not None:
            cmd.extend(["--adjustment-cap", str(adjustment_cap)])
        # Mapped deltas and LLM scenarios are always enabled - no parameters needed

        logger.info(f"🚀 Starting analysis for {ticker} ({company}) with command: {cmd}")
        jobs[job_id]["progress"] = f"Running {pipeline} analysis for {ticker} ({company})..."

        # Run container
        container = docker_client.containers.run(
            BACKEND_IMAGE,
            command=cmd,
            environment=env_vars,
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
            detach=True,
            remove=False,  # Don't auto-remove so we can get logs
            name=f"analysis-{job_id}"
        )

        jobs[job_id]["container_id"] = container.id
        jobs[job_id]["progress"] = "Analysis running..."

        # Start log monitoring in background
        loop = asyncio.get_event_loop()

        # Monitor logs in a separate task
        log_task = asyncio.create_task(monitor_info_log(job_id, container))

        # Wait for container to complete with timeout
        try:
            def wait_for_container():
                return container.wait(timeout=1800)  # 30 minute timeout

            with concurrent.futures.ThreadPoolExecutor() as executor:
                _ = await loop.run_in_executor(executor, wait_for_container)

        except Exception as e:
            logger.error(f"Container wait timeout or error: {e}")
            # Kill the container if it's taking too long
            try:
                container.kill()
                # Check if we were in LLM analysis phase when timeout occurred
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
            except Exception as kill_e:
                logger.error(f"Failed to kill container: {kill_e}")

        # Cancel log monitoring
        log_task.cancel()

        # Check container status
        container.reload()
        exit_code = container.attrs['State']['ExitCode']

        if exit_code == 0:
            # Status will be set by log monitoring when it sees completion
            if jobs[job_id]["status"] != "completed":
                jobs[job_id]["status"] = "completed"
                jobs[job_id]["progress"] = "Analysis completed successfully"
            jobs[job_id]["completed_at"] = datetime.now().isoformat()
            logger.info(f"✅ Analysis job {job_id} completed successfully")
        else:
            # Check if this was an LLM timeout scenario before marking as failed
            current_status = jobs[job_id].get("status")
            if current_status == "llm_timeout":
                # Keep the LLM timeout status, don't override to failed
                logger.warning(f"⚠️ Analysis job {job_id} timed out during LLM analysis")
            else:
                jobs[job_id]["status"] = "failed"
                jobs[job_id]["error"] = f"Container exited with code {exit_code}"
                logger.error(f"❌ Analysis job {job_id} failed with exit code {exit_code}")

        # Clean up container (OK since we read via named volume)
        try:
            container.remove()
        except Exception as e:
            logger.warning(f"Failed to remove container: {e}")

    except Exception as e:
        logger.error(f"❌ Error in analysis job {job_id}: {e}")
        # Check if this might be an LLM-related timeout
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


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    logger.info("🚀 Starting Stock Analyst API Runner...")

    # Initialize Docker
    docker_ready = init_docker_client()
    if not docker_ready:
        logger.warning("⚠️ Docker not available - running in limited mode")
    else:
        # Ensure volume exists
        ensure_volume_exists()

    # Check API keys
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
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "Stock Analyst API Runner",
        "docker_available": docker_client is not None,
        "timestamp": datetime.now().isoformat()
    }


@app.get("/health")
async def health_check():
    """Detailed health check"""
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


@app.post("/run", response_model=JobResponse)
async def start_analysis(request: JobRequest, background_tasks: BackgroundTasks):
    """
    Start a new stock analysis job
    
    Parameters:
    - ticker: Stock ticker symbol (e.g., "NVDA")
    - company: Company name (optional, will be inferred if not provided)
    - pipeline: Analysis pipeline ("comprehensive", "financial-only", "model-only", "news-only", "model-to-price", "news-to-price")
    - model: Financial model type ("dcf", "comparable", "comprehensive")
    - years: Projection years (default: 5)
    - term_growth: Terminal growth rate (optional, auto-inferred)
    - wacc: Override WACC (optional, auto-inferred)  
    - strategy: Force specific forecast strategy (optional)
    - max_articles: Maximum articles to scrape (default: 20)
    - min_score: Minimum relevance score for filtering (default: 3.0)
    - max_filtered: Maximum filtered articles to keep (default: 10)
    - min_confidence: Minimum confidence for LLM insights (default: 0.5)
    - scaling: Base scaling factor for qualitative adjustment (default: 0.15)
    - adjustment_cap: Maximum adjustment percentage ± (default: 0.20)
    - use_mapped_deltas: Use deterministic event→parameter mapping (default: true)
    - use_llm_scenarios: Use LLM scenario analysis (default: true)
    """
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker service not available")

    if not SERPAPI_API_KEY or not OPENAI_API_KEY:
        raise HTTPException(status_code=400, detail="API keys not configured")

    # Generate job ID
    job_id = f"{request.ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # Create job record
    jobs[job_id] = {
        "job_id": job_id,
        "ticker": request.ticker,
        "company": request.company,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "progress": "Job queued"
    }

    # Start background task - all parameters are optional except ticker/company
    background_tasks.add_task(run_analysis_job, job_id, request.ticker, request.company, 
                             request.pipeline, request.model, request.years, request.term_growth, 
                             request.wacc, request.strategy, request.max_articles, request.min_score, 
                             request.max_filtered, request.min_confidence, request.scaling, 
                             request.adjustment_cap)

    return JobResponse(
        job_id=job_id,
        status="pending",
        message="Analysis job started"
    )


@app.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job_status(job_id: str):
    """Get status of a specific job"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    return JobStatus(**job)


@app.get("/jobs")
async def list_jobs():
    """List all jobs"""
    return {"jobs": list(jobs.values())}


@app.get("/jobs/{job_id}/logs")
async def get_job_logs(job_id: str):
    """Get detailed job logs from info.log file"""
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


@app.get("/jobs/{job_id}/logs/stream")
async def get_job_logs_stream(job_id: str):
    """Stream info.log in real time using Server-Sent Events (robust tail with final drain)."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    ticker = job.get('ticker', '').upper()
    container_id = job.get('container_id')

    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    async def log_generator():
        last_size = 0               # byte offset already sent
        container = None
        decoder = codecs.getincrementaldecoder('utf-8')()
        pending_line = ""           # partial line carried between chunks
        idle_timer = 0.0
        stable_cycles = 0
        have_seen_any_data = False

        def sse(event_type: str, message: str):
            payload = json.dumps({
                "status": event_type,
                "message": message,
                "timestamp": datetime.now().isoformat()
            })
            # Debug: print every SSE payload to server terminal for visibility
            try:
                logger.info(f"SSE -> {event_type}: {payload}")
            except Exception:
                # Ensure logging never breaks SSE
                pass
            return f"event: {event_type}\ndata: {payload}\n\n"

        # initial connection notice
        conn_msg = sse("connection", f"Connected to log stream for {ticker}")
        logger.info(f"Emitting initial SSE connection for {ticker}")
        yield conn_msg

        # try to get the analysis container
        if container_id:
            try:
                container = docker_client.containers.get(container_id)
                logger.info(f"Found analysis container for {ticker}: {container_id}")
            except docker.errors.NotFound:
                logger.info(f"Analysis container not found for {ticker}, will use volume fallback")
                container = None

        async def probe_size_and_read_from_container(start_byte: int):
            """Return (next_size:int, new_bytes:bytes) by reading from the running analysis container, or (0,b"") on miss."""
            if not container:
                return 0, b""
            try:
                container.reload()
                # path inside analysis container
                path = f"/data/{ticker}/info.log"
                # get file size in bytes
                size_res = container.exec_run(
                    f"sh -c 'if [ -f {path} ]; then wc -c {path} | awk {{\\''print $1\\''}}; else echo 0; fi'"
                )
                if size_res.exit_code != 0:
                    return 0, b""
                current_size = int((size_res.output or b"0").decode("utf-8").strip() or "0")
                if current_size > start_byte:
                    # read from byte N+1
                    content_res = container.exec_run(
                        f"sh -c 'if [ -f {path} ]; then tail -c +{start_byte + 1} {path}; fi'"
                    )
                    if content_res.exit_code == 0 and content_res.output:
                        return current_size, content_res.output
                return current_size, b""
            except Exception as e:
                logger.warning(f"Error reading from container {container_id}: {e}")
                return 0, b""

        async def probe_size_and_read_from_volume(start_byte: int):
            """Return (next_size:int, new_bytes:bytes) via a short-lived alpine with the volume bound RO."""
            try:
                path = f"/data/{ticker}/info.log"
                # size
                size_out = docker_client.containers.run(
                    "alpine:latest",
                    command=f"sh -c 'if [ -f {path} ]; then wc -c {path} | awk {{\\''print $1\\''}}; else echo 0; fi'",
                    volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                    remove=True,
                    detach=False,
                )
                current_size = int((size_out or b"0").decode("utf-8").strip() or "0")
                if current_size > start_byte:
                    content_out = docker_client.containers.run(
                        "alpine:latest",
                        command=f"sh -c 'if [ -f {path} ]; then tail -c +{start_byte + 1} {path}; fi'",
                        volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                        remove=True,
                        detach=False,
                    )
                    return current_size, content_out or b""
                return current_size, b""
            except Exception as e:
                logger.debug(f"Volume read failed (maybe file not ready yet): {e}")
                return 0, b""

        def emit_lines(decoded_text: str, flush: bool=True):
            """Emit complete lines and keep the last partial in pending_line (outer scope)."""
            nonlocal pending_line, have_seen_any_data
            text = pending_line + decoded_text
            # splitlines(True) keeps endlines; we use it to detect if last is complete
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
                    # Let the final drain still run to ensure nothing is missed,
                    # but also notify frontend that we're at logical completion.
                    job["status"] = "completed"
                    job["progress"] = "Analysis completed successfully"

                    yield sse("completed", "🏁 THE ENTIRE PROGRAM IS COMPLETED")
                    # yield sse("log", line)
                else:
                    yield sse("log", line)
            pending_line = pending

        async def drain_once():
            """Read from container if running; otherwise from volume."""
            nonlocal container, last_size
            # prefer container while running
            next_size, chunk = 0, b""
            if container:
                # read
                next_size, chunk = await probe_size_and_read_from_container(last_size)
                # if container stopped, try a final read and then null it out
                try:
                    container.reload()
                    if container.status != "running":
                        # attempt one more read after stop
                        if next_size == last_size:
                            # no size growth reported yet; still try tail from last_size+1
                            _, extra_chunk = await probe_size_and_read_from_container(last_size)
                            if extra_chunk:
                                chunk += extra_chunk
                                next_size = last_size + len(extra_chunk)
                        container = None
                except Exception:
                    container = None

            # fallback to volume if nothing came from container
            if next_size <= last_size:
                v_size, v_chunk = await probe_size_and_read_from_volume(last_size)
                if v_size > last_size:
                    next_size, chunk = v_size, v_chunk

            # process bytes
            if chunk:
                decoded = decoder.decode(chunk)
                # Emit decoded text lines and log them inside emit_lines
                for evt in emit_lines(decoded):
                    yield evt
                last_size = next_size

        try:
            # main loop: poll until we drain and stabilize after completion/stop
            while True:
                something_new = False
                async for evt in drain_once():
                    something_new = True
                    idle_timer = 0.0
                    # If evt is a raw non-SSE string (e.g. comment/keep-alive), log it.
                    try:
                        evt_str = str(evt)
                    except Exception:
                        evt_str = None
                    if evt_str and not evt_str.startswith("event:"):
                        logger.info(f"SSE -> raw: {evt_str.strip()}")
                    yield evt

                if not something_new:
                    idle_timer += POLL_INTERVAL_SEC

                    # status heartbeat to keep proxies from buffering
                    if idle_timer >= IDLE_HEARTBEAT_SEC:
                        idle_timer = 0.0
                        # SSE comment heartbeat line
                        heartbeat = ": keep-alive\n\n"
                        logger.info(f"SSE -> heartbeat: {heartbeat.strip()}")
                        yield heartbeat
                        # also send a friendly status if we haven't seen data yet
                        if not have_seen_any_data:
                            yield sse("status", "Analysis running, waiting for log output..." if container else "Waiting for analysis to start...")

                # decide if we should enter finalization
                job_done = job.get("status") in {"completed", "failed", "stopped"}
                container_running = False
                if container:
                    try:
                        container.reload()
                        container_running = (container.status == "running")
                    except Exception:
                        container_running = False
                        container = None

                if job_done and not container_running:
                    # final drain logic: keep polling until the file is stable for N cycles
                    if something_new:
                        stable_cycles = 0
                    else:
                        stable_cycles += 1
                    if stable_cycles >= FINAL_DRAIN_STABLE_CYCLES:
                        # flush any pending partial line
                        for evt in emit_lines("", flush=True):
                            yield evt
                        # emit final completed if not already sent
                        yield sse("completed", "Analysis completed")
                        # short grace to ensure the client receives the last events
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
            # Critical for Nginx or similar reverse proxies:
            "X-Accel-Buffering": "no",
            # CORS, if needed:
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET",
            "Access-Control-Allow-Headers": "Cache-Control",
        },
    )


@app.get("/jobs/{job_id}/files/{filename}")
async def download_file(job_id: str, filename: str):
    """
    Download a specific top-level file from job results.
    Supported: info.log, screening_report.md, screening_data.json, price_adjustment_explanation_latest.md
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    ticker = job.get('ticker', '').upper()

    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    supported = {"info.log": "text/plain",
                 "screening_report.md": "text/markdown",
                 "screening_data.json": "application/json",
                 "price_adjustment_explanation_latest.md": "text/markdown"}
    if filename not in supported:
        raise HTTPException(status_code=400, detail=f"Unsupported filename '{filename}'. Supported: {list(supported.keys())}")

    try:
        # Handle files in models subdirectory
        if filename.startswith("price_adjustment_explanation"):
            path = f"/data/{ticker}/models/{filename}"
        else:
            path = f"/data/{ticker}/{filename}"
        
        result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'cat {shlex.quote(path)}'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
            remove=True,
            detach=False
        )
        media_type = supported[filename]
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return StreamingResponse(BytesIO(result), media_type=media_type, headers=headers)
    except Exception as e:
        logger.error(f"Error reading {filename} for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read {filename}: {str(e)}")


@app.get("/jobs/{job_id}/info-log")
async def get_info_log(job_id: str):
    """Get the info.log file content for a job as JSON payload"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    ticker = job.get('ticker', '').upper()

    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    try:
        temp_container = docker_client.containers.run(
            "alpine:latest",
            command=f"cat /data/{ticker}/info.log",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
            remove=True,
            detach=False
        )

        log_content = temp_container.decode('utf-8')

        return {
            "job_id": job_id,
            "ticker": ticker,
            "log_content": log_content,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error reading info.log for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read info.log: {str(e)}")


@app.get("/jobs/{job_id}/download/searched-articles")
async def download_searched_articles(job_id: str):
    """Download all searched articles as a zip file (in-memory)"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    ticker = job.get('ticker', '').upper()

    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    try:
        list_result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'if [ -d /data/{ticker}/searched ]; then find /data/{ticker}/searched -name \"*.md\" -type f; fi'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
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
                        volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
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


@app.get("/jobs/{job_id}/download/filtered-articles")
async def download_filtered_articles(job_id: str):
    """Download all filtered articles as a zip file (in-memory)"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    ticker = job.get('ticker', '').upper()

    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    try:
        list_result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'if [ -d /data/{ticker}/filtered ]; then find /data/{ticker}/filtered -name \"filtered_*.md\" -type f; fi'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
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
                command=f"sh -c 'cat /data/{ticker}/filtered/filtered_articles_index.csv'",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
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
                        volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
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


@app.get("/jobs/{job_id}/download/screening-report")
async def download_screening_report(job_id: str):
    """Download the screening_report.md file as a PDF (converted in-memory)"""
    # Try to get job info, but fall back to extracting ticker from job_id if not found
    if job_id in jobs:
        job = jobs[job_id]
        ticker = job.get('ticker', '').upper()
    else:
        # Extract ticker from job_id (format is usually TICKER_YYYYMMDD_HHMMSS)
        ticker_part = job_id.split('_')[0] if '_' in job_id else job_id
        ticker = ticker_part.upper()
        logger.warning(f"Job {job_id} not found in memory, trying with ticker: {ticker}")
    
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")
    try:
        # 1. Fetch .md file from volume (docker)
        result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'cat /data/{ticker}/screening_report.md'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
            remove=True,
            detach=False
        )
        if not result:
            raise HTTPException(status_code=404, detail="Screening report not found")
        md_content = result.decode('utf-8')

        # 2. Convert Markdown to HTML
        import markdown
        html_content = markdown.markdown(md_content, output_format="html5")

        # 3. Convert HTML to PDF using ReportLab
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from io import BytesIO
        import re
        
        buffer = BytesIO()
        
        # Create PDF document
        doc = SimpleDocTemplate(buffer, pagesize=letter, 
                              topMargin=1*inch, bottomMargin=1*inch,
                              leftMargin=1*inch, rightMargin=1*inch)
        
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('CustomTitle', parent=styles['Title'], 
                                   fontSize=24, spaceAfter=30, textColor='darkblue')
        heading_style = ParagraphStyle('CustomHeading', parent=styles['Heading1'], 
                                     fontSize=16, spaceAfter=12, textColor='darkblue')
        
        # Build story (content for PDF)
        story = []
        
        # Add title
        story.append(Paragraph(f"{ticker} Stock Analysis Report", title_style))
        story.append(Spacer(1, 20))
        
        # Parse HTML content and convert to ReportLab elements
        # This is a simplified conversion - for better results, consider using a proper HTML parser
        lines = html_content.split('\n')
        current_paragraph = ""
        
        for line in lines:
            line = line.strip()
            if not line:
                if current_paragraph:
                    # Clean up HTML tags for basic conversion
                    clean_text = re.sub(r'<[^>]+>', '', current_paragraph)
                    if clean_text.strip():
                        story.append(Paragraph(clean_text, styles['Normal']))
                        story.append(Spacer(1, 12))
                    current_paragraph = ""
                continue
                
            # Handle headings
            if line.startswith('<h'):
                if current_paragraph:
                    clean_text = re.sub(r'<[^>]+>', '', current_paragraph)
                    if clean_text.strip():
                        story.append(Paragraph(clean_text, styles['Normal']))
                        story.append(Spacer(1, 12))
                    current_paragraph = ""
                
                clean_heading = re.sub(r'<[^>]+>', '', line)
                if clean_heading.strip():
                    story.append(Paragraph(clean_heading, heading_style))
                    story.append(Spacer(1, 6))
            else:
                current_paragraph += " " + line
        
        # Add any remaining paragraph
        if current_paragraph:
            clean_text = re.sub(r'<[^>]+>', '', current_paragraph)
            if clean_text.strip():
                story.append(Paragraph(clean_text, styles['Normal']))
        
        # Build PDF
        try:
            doc.build(story)
            pdf_bytes = buffer.getvalue()
            buffer.close()
        except Exception as e:
            buffer.close()
            raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(e)}")

        headers = {"Content-Disposition": f'attachment; filename="{ticker}_screening_report.pdf"'}
        return StreamingResponse(BytesIO(pdf_bytes), media_type='application/pdf', headers=headers)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading converted PDF screening report for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read or convert screening report: {str(e)}")

@app.get("/jobs/{job_id}/download/all-results")
async def download_all_results(job_id: str):
    """Download ALL analysis results (entire ticker folder) as a tar stream."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    ticker = jobs[job_id].get("ticker", "").upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker not found for job")

    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    # If you captured extra sub-mounts during the job, we can mount them too:
    extra_mounts = jobs[job_id].get("extra_mounts", [])  # list of {"Source": "...", "Destination": "...", "Mode": "ro"}

    def tar_stream():
        helper = None
        try:
            # Build a volumes map: always mount the main data volume at /data (ro)
            volumes = {DATA_VOLUME: {"bind": "/data", "mode": "ro"}}
            # Also mount any sub-mounts (optional; keeps things future-proof if you ever split volumes)
            for m in extra_mounts:
                src = m.get("Source")
                dst = m.get("Destination")
                if src and dst:
                    volumes[src] = {"bind": dst, "mode": m.get("Mode", "ro") or "ro"}

            helper = docker_client.containers.create(
                image="alpine:latest",
                command="sleep 300",  # long enough; we’ll remove explicitly
                volumes=volumes,
                detach=True,
                remove=False,
            )
            helper.start()

            # Sanity check that /data/TICKER exists
            chk = helper.exec_run(f"sh -c 'test -d /data/{shlex.quote(ticker)}'")
            if chk.exit_code != 0:
                raise HTTPException(status_code=404, detail=f"No data found for {ticker}")

            # Stream tar; IMPORTANT: do NOT kill the container until the generator finishes.
            exec_res = helper.exec_run(
                cmd=["tar", "-C", "/data", "-cf", "-", "--", ticker],
                stream=True,
                demux=False,
            )

            for chunk in exec_res.output:
                if chunk:
                    yield chunk

        finally:
            # now it's safe to remove the helper
            if helper is not None:
                try:
                    helper.remove(force=True)
                except Exception:
                    pass

    headers = {"Content-Disposition": f'attachment; filename="{ticker}_complete_analysis.tar"'}
    return StreamingResponse(tar_stream(), media_type="application/x-tar", headers=headers)

@app.get("/jobs/{job_id}/download/financials-annual")
async def download_financials_annual(job_id: str):
    """Download the financials_annual_modeling_latest.json file"""
    # Try to get job info, but fall back to extracting ticker from job_id if not found
    if job_id in jobs:
        job = jobs[job_id]
        ticker = job.get('ticker', '').upper()
    else:
        # Extract ticker from job_id (format is usually TICKER_YYYYMMDD_HHMMSS)
        ticker_part = job_id.split('_')[0] if '_' in job_id else job_id
        ticker = ticker_part.upper()
        logger.warning(f"Job {job_id} not found in memory, trying with ticker: {ticker}")
    
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")
    
    try:
        # Read the financials annual JSON file
        result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'cat /data/{ticker}/financials/financials_annual_modeling_latest.json'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
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


STABILITY_CHECK_INTERVAL = 0.5
STABILITY_CHECKS = 3
COPY_TIMEOUT_SEC = 60

@app.get("/jobs/{job_id}/download/financial-model")
async def download_financial_model(job_id: str, background_tasks: BackgroundTasks):
    # resolve ticker
    ticker = (jobs.get(job_id, {}) or {}).get("ticker")
    if not ticker:
        ticker = (job_id.split("_")[0] if "_" in job_id else job_id).upper()
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    src_path = f"/data/{ticker}/models/financial_model_comprehensive_latest.xlsx"
    download_name = f"{ticker}_financial_model_comprehensive_latest.xlsx"

    helper = None
    tmp_path = None
    try:
        # helper container, RO mount
        helper = docker_client.containers.create(
            image="alpine:latest",
            command="sleep 300",
            volumes={DATA_VOLUME: {"bind": "/data", "mode": "ro"}},
            detach=True, remove=False
        )
        helper.start()

        # ensure file exists
        if helper.exec_run(f"sh -c 'test -f {shlex.quote(src_path)}'").exit_code != 0:
            raise HTTPException(status_code=404, detail="Financial model Excel not found")

        # wait for stable size
        same, last = 0, -1
        start = time.time()
        while time.time() - start < COPY_TIMEOUT_SEC:
            sz = helper.exec_run(
                f"sh -c 'stat -c %s {shlex.quote(src_path)} || wc -c < {shlex.quote(src_path)}'"
            )
            size = int((sz.output or b"0").decode().strip() or "0")
            if size == last and size > 0:
                same += 1
                if same >= STABILITY_CHECKS:
                    break
            else:
                same, last = 0, size
            time.sleep(STABILITY_CHECK_INTERVAL)

        # copy bytes to a host temp file
        fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        with open(tmp_path, "wb") as dst:
            exec_res = helper.exec_run(
                cmd=["sh", "-lc", f"dd if={shlex.quote(src_path)} bs=1M status=none"],
                stream=True, demux=False
            )
            for chunk in exec_res.output:
                if chunk:
                    dst.write(chunk)

        # sanity check: valid XLSX zip
        if not zipfile.is_zipfile(tmp_path):
            os.remove(tmp_path)
            raise HTTPException(status_code=500, detail="Downloaded Excel appears invalid/corrupted")

        # checksum for logs/clients
        sha256 = hashlib.sha256()
        with open(tmp_path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                sha256.update(b)
        digest = sha256.hexdigest()

        # schedule cleanup after response finishes
        background_tasks.add_task(lambda p=tmp_path: os.path.exists(p) and os.remove(p))

        headers = {
            "Content-Disposition": f"attachment; filename={download_name}; filename*=UTF-8''{download_name}",
            "Cache-Control": "no-transform",  # tell proxies not to fiddle with it
            "X-File-SHA256": digest,          # easy integrity check client-side
        }
        return FileResponse(
            path=tmp_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=download_name,
            headers=headers,
            background=background_tasks,
        )
    finally:
        if helper is not None:
            try:
                helper.remove(force=True)
            except Exception:
                pass


@app.get("/jobs/{job_id}/download/filtered-report")
async def download_filtered_report(job_id: str):
    """Download the filtered_report.md file"""
    # Try to get job info, but fall back to extracting ticker from job_id if not found
    if job_id in jobs:
        job = jobs[job_id]
        ticker = job.get('ticker', '').upper()
    else:
        # Extract ticker from job_id (format is usually TICKER_YYYYMMDD_HHMMSS)
        ticker_part = job_id.split('_')[0] if '_' in job_id else job_id
        ticker = ticker_part.upper()
        logger.warning(f"Job {job_id} not found in memory, trying with ticker: {ticker}")
    
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")
    
    try:
        # Read the filtered report markdown file
        result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'cat /data/{ticker}/filtered_report.md'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
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


@app.get("/jobs/{job_id}/download/price-adjustment-explanation")
async def download_price_adjustment_explanation(job_id: str):
    """Download the price_adjustment_explanation_latest.md file"""
    # Try to get job info, but fall back to extracting ticker from job_id if not found
    if job_id in jobs:
        job = jobs[job_id]
        ticker = job.get('ticker', '').upper()
    else:
        # Extract ticker from job_id (format is usually TICKER_YYYYMMDD_HHMMSS)
        ticker_part = job_id.split('_')[0] if '_' in job_id else job_id
        ticker = ticker_part.upper()
        logger.warning(f"Job {job_id} not found in memory, trying with ticker: {ticker}")
    
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")
    
    try:
        # Read the price adjustment explanation markdown file
        result = docker_client.containers.run(
            "alpine:latest",
            command=f"sh -c 'cat /data/{ticker}/models/price_adjustment_explanation_latest.md'",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
            remove=True,
            detach=False
        )
        if not result:
            raise HTTPException(status_code=404, detail="Price adjustment explanation not found")

        headers = {"Content-Disposition": f'attachment; filename="{ticker}_price_adjustment_explanation_latest.md"'}
        return StreamingResponse(BytesIO(result), media_type='text/markdown', headers=headers)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading price adjustment explanation for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read price adjustment explanation file: {str(e)}")


@app.get("/jobs/{job_id}/files")
async def list_job_files(job_id: str):
    """List counts of available files for a job"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    ticker = job.get('ticker', '').upper()

    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    try:
        files = {
            "info_log": False,
            "screening_report": False,
            "screening_data": False,
            "searched_articles_count": 0,
            "filtered_articles_count": 0,
            "financials_annual": False,
            "financial_model": False,
            "filtered_report": False,
            "price_adjustment_explanation": False,
        }

        # Check info.log
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f /data/{ticker}/info.log",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                remove=True,
                detach=False
            )
            files["info_log"] = True
        except:
            pass

        # Check screening report
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f /data/{ticker}/screening_report.md",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                remove=True,
                detach=False
            )
            files["screening_report"] = True
        except:
            pass

        # Check screening data JSON
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f /data/{ticker}/screening_data.json",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                remove=True,
                detach=False
            )
            files["screening_data"] = True
        except:
            pass

        # List searched articles
        try:
            searched_list = docker_client.containers.run(
                "alpine:latest",
                command=f"sh -c 'if [ -d /data/{ticker}/searched ]; then ls /data/{ticker}/searched/*.md 2>/dev/null | wc -l; else echo 0; fi'",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                remove=True,
                detach=False
            )
            searched_count = int((searched_list.decode('utf-8').strip() or "0"))
            files["searched_articles_count"] = searched_count
        except:
            files["searched_articles_count"] = 0

        # List filtered articles
        try:
            filtered_list = docker_client.containers.run(
                "alpine:latest",
                command=f"sh -c 'if [ -d /data/{ticker}/filtered ]; then ls /data/{ticker}/filtered/filtered_*.md 2>/dev/null | wc -l; else echo 0; fi'",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                remove=True,
                detach=False
            )
            filtered_count = int((filtered_list.decode('utf-8').strip() or "0"))
            files["filtered_articles_count"] = filtered_count
        except:
            files["filtered_articles_count"] = 0

        # Check financials annual JSON
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f /data/{ticker}/financials/financials_annual_modeling_latest.json",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                remove=True,
                detach=False
            )
            files["financials_annual"] = True
        except:
            pass

        # Check financial model Excel
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f /data/{ticker}/models/financial_model_comprehensive_latest.xlsx",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                remove=True,
                detach=False
            )
            files["financial_model"] = True
        except:
            pass

        # Check filtered report
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f /data/{ticker}/filtered_report.md",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                remove=True,
                detach=False
            )
            files["filtered_report"] = True
        except:
            pass

        # Check price adjustment explanation
        try:
            docker_client.containers.run(
                "alpine:latest",
                command=f"test -f /data/{ticker}/models/price_adjustment_explanation_latest.md",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                remove=True,
                detach=False
            )
            files["price_adjustment_explanation"] = True
        except:
            pass

        return {
            "job_id": job_id,
            "ticker": ticker,
            "files": files,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error listing files for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not list files: {str(e)}")


@app.get("/jobs/{job_id}/status/detailed")
async def get_detailed_job_status(job_id: str):
    """Get detailed status including file availability and progress metrics"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]

    # Get file information
    try:
        files_info = await list_job_files(job_id)
        files = files_info.get("files", {})
    except:
        files = {}

    # Calculate progress percentage based on completed stages
    progress_percentage = 0
    if job.get("status") == "completed":
        progress_percentage = 100
    elif job.get("status") == "failed":
        progress_percentage = 0
    elif job.get("status") == "llm_timeout":
        progress_percentage = 75  # Made it to LLM analysis but timed out
    else:
        # Estimate progress based on stage
        progress_map = {
            "Scraping articles": 25,
            "Filtering articles": 50,
            "Running LLM analysis": 75,
            "Generating reports": 90,
            "Pipeline session completed": 100
        }
        current_progress = job.get("progress", "")
        for stage, percentage in progress_map.items():
            if stage.lower() in current_progress.lower():
                progress_percentage = percentage
                break

    return {
        "job_id": job_id,
        "ticker": job.get("ticker"),
        "company": job.get("company"),
        "status": job.get("status"),
        "progress": job.get("progress"),
        "progress_percentage": progress_percentage,
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, workers=1)
