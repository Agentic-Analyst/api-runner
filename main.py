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
from fastapi import FastAPI, HTTPException, BackgroundTasks, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
import uvicorn
from fastapi import Response
import shlex, time, io, zipfile, hashlib
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware import Middleware
from news_feed.news_manager import NewsManager

# Load environment variables FIRST before any imports that use them
from dotenv import load_dotenv
load_dotenv()

# Import shared configuration
import config
from config import (
    BACKEND_IMAGE, DATA_VOLUME,
    SERPAPI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY,
    MONGO_URI, MONGO_DB,
    csv_env
)

# Auth routers (imported after .env is loaded)
from auth_oauth import router as oauth_router
from auth_code_login import router as code_router

from md_pdf_converter import convert_md_to_pdf

# Real-time stock price system
from realtime import (
    PriceManager, 
    stock_router,
    PriceFetchConfig
)
from realtime.stock_api import init_realtime_api

# News feed system
from news_feed import news_router
from news_feed.api import init_news_manager, close_news_manager
from news_feed.news_auto_updater import NewsUpdateManager, NewsUpdateConfig
from news_feed.news_websocket import init_news_websocket_manager, get_news_websocket_manager

# Daily reports system
from reports.daily import daily_reports_router, initialize_scheduler, shutdown_scheduler
from reports.daily.api import set_docker_client

# Tunables
POLL_INTERVAL_SEC = 0.75
IDLE_HEARTBEAT_SEC = 12
FINAL_DRAIN_STABLE_CYCLES = 3  # how many consecutive polls with no growth before we finalize

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global real-time services
price_manager: Optional[PriceManager] = None
websocket_manager = None
news_auto_updater: Optional[NewsUpdateManager] = None
news_websocket_manager = None

# Frontend origins from config
FRONTEND_ORIGINS = csv_env("FRONTEND_ORIGIN")
ROOT_PATH = config.ROOT_PATH
SESSION_SECRET = config.SESSION_SECRET
SESSION_HTTPS_ONLY = config.SESSION_HTTPS_ONLY
SESSION_STORE_COOKIE = config.SESSION_STORE_COOKIE

# SameSite configuration
SESSION_SAMESITE = config.SESSION_SAMESITE
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
app.include_router(stock_router)
app.include_router(news_router)
app.include_router(daily_reports_router)

# Health endpoint (useful for container probes)
@app.get("/healthz")
def healthz():
    return {"ok": True}

# Global variables
docker_client = None
jobs: Dict[str, Dict] = {}

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
    llm: Optional[str] = Field(default="gpt-4o-mini", description="LLM model to use for analysis")
    pipeline: Optional[str] = Field(default=None, description="Pipeline to run (default: comprehensive)")
    query: Optional[str] = Field(default=None, description="Search query for news-related pipelines (e.g., 'AAPL earnings Q3')")

class JobResponse(BaseModel):
    job_id: str
    ticker: str
    status: str
    message: str

class ChatRequest(BaseModel):
    """Chat request model for conversational AI analysis"""
    email: str = Field(..., description="User email for data organization")
    timestamp: str = Field(..., description="Timestamp for the chat session")
    user_prompt: str = Field(..., description="User's chat message/question")
    session_id: Optional[str] = Field(default=None, description="Session ID for continuing conversation (optional)")

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
                         llm: Optional[str] = None, pipeline: Optional[str] = None,
                         query: Optional[str] = None):
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
            "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
            "MONGO_URI": MONGO_URI,
            "MONGO_DB": MONGO_DB,
            "DATA_PATH": "/data",
        }

        cmd = ["--ticker", ticker, "--email", email, "--timestamp", timestamp]
        if llm:
            cmd.extend(["--llm", llm])
        if pipeline:
            cmd.extend(["--pipeline", pipeline])
        if query:
            cmd.extend(["--query", query])

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

# ------------- Chat job runner -------------
async def run_chat_job(
    job_id: str,
    email: str,
    timestamp: str,
    user_prompt: str,
    session_id: Optional[str] = None
):
    """
    Run chat job in Docker container with the chat pipeline.
    
    Args:
        job_id: Unique job identifier
        email: User email
        timestamp: ISO timestamp
        user_prompt: User's chat message/question
        session_id: Optional session ID for continuing conversation
    """
    if not docker_client:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = "Docker client not available"
        return

    try:
        jobs[job_id]["status"] = "running"
        jobs[job_id]["progress"] = "Starting chat session..."
        jobs[job_id]["recent_logs"] = []

        env_vars = {
            "SERPAPI_API_KEY": SERPAPI_API_KEY,
            "OPENAI_API_KEY": OPENAI_API_KEY,
            "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
            "MONGO_URI": MONGO_URI,
            "MONGO_DB": MONGO_DB,
            "DATA_PATH": "/data",
        }

        # Build command for chat pipeline (4 required args: email, timestamp, pipeline, user-prompt, session-id)
        cmd = [
            "--email", email,
            "--timestamp", timestamp,
            "--pipeline", "chat",
            "--user-prompt", user_prompt
        ]
        
        # Add optional session-id if provided
        if session_id:
            cmd.extend(["--session-id", session_id])

        logger.info(f"🚀 Starting chat session with command: {cmd}")
        jobs[job_id]["progress"] = f"Running chat session..."

        container = docker_client.containers.run(
            BACKEND_IMAGE,
            command=cmd,
            environment=env_vars,
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'rw'}},
            detach=True,
            remove=False,
            name=f"chat-{job_id}"
        )

        jobs[job_id]["container_id"] = container.id
        jobs[job_id]["progress"] = "Waiting for ticker identification..."

        # Listen to container stdout to extract ticker
        # The backend prints: "[SUPERVISOR] ✅ Identified ticker: {ticker}"
        ticker_identified = False
        identified_ticker = None
        
        def extract_ticker_from_logs():
            """Extract ticker from container logs (synchronous function for executor)"""
            nonlocal ticker_identified, identified_ticker
            try:
                # Stream container logs to extract ticker
                log_stream = container.logs(stream=True, follow=True)
                for log_line in log_stream:
                    try:
                        line = log_line.decode('utf-8').strip()
                        logger.info(f"[CHAT CONTAINER] {line}")
                        
                        # Look for ticker identification line
                        if "[SUPERVISOR] ✅ Identified ticker:" in line:
                            # Extract ticker: "[SUPERVISOR] ✅ Identified ticker: NVDA"
                            parts = line.split("Identified ticker:")
                            if len(parts) > 1:
                                identified_ticker = parts[1].strip().upper()
                                ticker_identified = True
                                logger.info(f"🎯 Extracted ticker from chat: {identified_ticker}")
                                
                                # Update job with identified ticker
                                jobs[job_id]["ticker"] = identified_ticker
                                jobs[job_id]["progress"] = f"Analyzing {identified_ticker}..."
                                
                                # Break after finding ticker
                                break
                    except Exception as decode_error:
                        logger.warning(f"Error decoding log line: {decode_error}")
                        continue
                        
            except Exception as e:
                logger.error(f"Error extracting ticker from logs: {e}")
        
        # Run ticker extraction with timeout
        loop = asyncio.get_event_loop()
        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                # Wait up to 30 seconds for ticker identification
                await asyncio.wait_for(
                    loop.run_in_executor(executor, extract_ticker_from_logs),
                    timeout=30.0
                )
        except asyncio.TimeoutError:
            logger.warning(f"⚠️ Ticker identification timeout for job {job_id}, continuing anyway...")
            # If we couldn't identify ticker, keep "pending" as ticker
            if not ticker_identified:
                logger.info(f"Ticker not identified for job {job_id}, keeping 'pending' status")
        except Exception as e:
            logger.error(f"Error during ticker extraction: {e}")

        # Now start monitoring info.log with the identified ticker
        if ticker_identified and identified_ticker:
            logger.info(f"✅ Ticker identified: {identified_ticker}, starting log monitoring...")
        else:
            logger.warning(f"⚠️ Ticker not identified, keeping 'pending' status")
        
        log_task = asyncio.create_task(monitor_info_log(job_id, container))

        try:
            def wait_for_container():
                result = container.wait(timeout=1800)
                return result
            
            # Monitor for stop requests while container is running
            stop_check_task = None
            
            async def check_stop_request():
                """Periodically check if job should be stopped"""
                while True:
                    await asyncio.sleep(2)
                    current_status = jobs[job_id].get("status")
                    if current_status in ["stopping", "stopped"]:
                        logger.info(f"Stop request detected for job {job_id}, killing container")
                        try:
                            container.reload()
                            if container.status in ['running', 'paused']:
                                container.kill()
                                logger.info(f"Container {container.id[:12]} killed for chat job {job_id}")
                        except Exception as kill_e:
                            logger.error(f"Failed to kill container: {kill_e}")
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
                container.reload()
                if container.status in ['running', 'paused']:
                    container.kill()
            except docker.errors.NotFound:
                logger.info(f"Container {container.id[:12]} already removed during error handling")
            except Exception as kill_e:
                logger.error(f"Failed to kill container during error handling: {kill_e}")
                    
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = f"Chat session timeout: {str(e)}"
            log_task.cancel()
            return

        # Cancel log monitoring task
        log_task.cancel()
        
        # Try to get container exit code
        exit_code = None
        try:
            container.reload()
            exit_code = container.attrs['State']['ExitCode']
        except docker.errors.NotFound:
            logger.info(f"Container {container.id[:12]} was removed before exit code check")
        except Exception as e:
            logger.warning(f"Could not get container exit code: {e}")

        # Check if job was stopped by user
        current_status = jobs[job_id].get("status")
        if current_status in ["stopping", "stopped"]:
            if current_status == "stopping":
                jobs[job_id]["status"] = "stopped"
                jobs[job_id]["progress"] = "Chat stopped by user"
            jobs[job_id]["completed_at"] = datetime.now().isoformat()
            logger.info(f"✅ Chat job {job_id} stopped by user request")
        elif exit_code == 0:
            if jobs[job_id]["status"] != "completed":
                jobs[job_id]["status"] = "completed"
                jobs[job_id]["progress"] = "Chat session completed successfully"
            jobs[job_id]["completed_at"] = datetime.now().isoformat()
            logger.info(f"✅ Chat job {job_id} completed successfully")
        elif exit_code is not None and exit_code != 0:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = f"Chat failed with exit code {exit_code}"
            logger.error(f"❌ Chat job {job_id} failed with exit code {exit_code}")

        # Remove container
        current_final_status = jobs[job_id].get("status")
        if current_final_status not in ["stopped"]:
            try:
                container.remove()
                logger.info(f"Container {container.id[:12]} removed after chat completion")
            except docker.errors.NotFound:
                logger.info(f"Container {container.id[:12]} already removed")
            except Exception as e:
                logger.warning(f"Failed to remove container: {e}")
        else:
            logger.info(f"Container removal skipped - job was stopped by user")

    except Exception as e:
        logger.error(f"❌ Error in chat job {job_id}: {e}")
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)

# ------------- Startup / health -------------
@app.on_event("startup")
async def startup_event():
    global price_manager, websocket_manager, news_auto_updater, news_websocket_manager
    
    logger.info("🚀 Starting Stock Analyst API Runner...")
    docker_ready = init_docker_client()
    if not docker_ready:
        logger.warning("⚠️ Docker not available - running in limited mode")
    else:
        ensure_volume_exists()
        # Set docker client for daily reports module
        set_docker_client(docker_client)

    if not SERPAPI_API_KEY:
        logger.warning("⚠️ SERPAPI_API_KEY not set")
    else:
        logger.info("✅ SERPAPI_API_KEY configured")
    if not OPENAI_API_KEY:
        logger.warning("⚠️ OPENAI_API_KEY not set")
    else:
        logger.info("✅ OPENAI_API_KEY configured")
    if not ANTHROPIC_API_KEY:
        logger.warning("⚠️ ANTHROPIC_API_KEY not set")
    else:
        logger.info("✅ ANTHROPIC_API_KEY configured")

    # Initialize news feed system (REQUIRED for news WebSocket to work)
    news_ready = await init_news_manager()
    if news_ready:
        logger.info("✅ News feed system initialized successfully")
        
        # Initialize news WebSocket manager (WITHOUT auto-updater)
        try:
            news_manager = NewsManager()
            await news_manager.initialize()
            
            # Initialize WebSocket manager WITHOUT auto-updater (passing None)
            news_websocket_manager = init_news_websocket_manager(news_manager, auto_updater=None)
            
            logger.info("✅ News WebSocket manager initialized (manual updates only)")
            logger.info("📡 News WebSocket endpoint available at /api/news/ws")
        except Exception as e:
            logger.error(f"❌ Failed to initialize news WebSocket manager: {e}")
            logger.warning("⚠️ News WebSocket will be unavailable")
        
        # OPTIONAL: Initialize news auto-updater (currently disabled)
        # Uncomment this section if you want automatic background news updates
        # try:
        #     logger.info("🚀 Starting automatic news update system...")
        #     
        #     # Create optimized news auto-updater with 30-minute intervals and API rate limiting
        #     news_config = NewsUpdateConfig(
        #         update_interval=7200,  # 2 hours in seconds (reduced from 5 min)
        #         max_tickers_per_batch=2,  # Reduced from 10 for API limits
        #         days_back=1,  # Only check last 1 day (reduced from 7)
        #         articles_limit=10,  # Reduced from 20
        #         timeout_seconds=180,
        #         force_refresh=False,
        #         min_update_interval=3600,  # Minimum 1 hour between updates per ticker
        #         max_api_calls_per_hour=20  # SerpAPI rate limiting
        #     )
        #     news_auto_updater = NewsUpdateManager(news_config)
        #     
        #     # Initialize and start auto-updater
        #     auto_update_ready = await news_auto_updater.initialize()
        #     if auto_update_ready:
        #         await news_auto_updater.start()
        #         logger.info("✅ News auto-updater started - updating every 30 minutes with smart rate limiting")
        #         
        #         # Connect auto-updater to WebSocket manager
        #         if news_websocket_manager:
        #             news_websocket_manager.set_auto_updater(news_auto_updater)
        #             logger.info("✅ Auto-updater connected to WebSocket manager")
        #     else:
        #         logger.warning("⚠️ News auto-updater failed to initialize")
        #         
        # except Exception as e:
        #     logger.error(f"❌ Failed to start news auto-updater: {e}")
        #     logger.warning("⚠️ Automatic news updates will be unavailable")
    else:
        logger.warning("⚠️ News feed system not available")

    # Initialize real-time stock price system
    try:
        logger.info("🚀 Starting real-time stock price system...")
        
        # Create price manager with configuration
        price_config = PriceFetchConfig(
            fetch_interval=10,  # 10 seconds as requested
            max_symbols_per_request=50,
            retry_attempts=3,
            timeout=30
        )
        price_manager = PriceManager(price_config)
        
        # Start price manager
        await price_manager.start()
        
        # Initialize FastAPI WebSocket manager (integrated with main FastAPI app)
        from realtime.fastapi_websocket import init_fastapi_websocket_manager
        websocket_manager = init_fastapi_websocket_manager(price_manager)
        
        # Initialize API endpoints with services
        init_realtime_api(price_manager, websocket_manager)
        
        logger.info("✅ Real-time stock price system started")
        logger.info("📡 WebSocket endpoint available at /api/realtime/ws")
        
    except Exception as e:
        logger.error(f"❌ Failed to start real-time system: {e}")
        logger.warning("⚠️ Real-time features will be unavailable")

    # # Initialize daily reports scheduler
    # try:
    #     logger.info("🚀 Starting daily reports scheduler...")
    #     scheduler_ready = await initialize_scheduler()
    #     if scheduler_ready:
    #         logger.info("✅ Daily reports scheduler started - auto-generates reports at 8:30 AM ET (Mon-Fri)")
    #     else:
    #         logger.warning("⚠️ Daily reports scheduler failed to initialize")
    # except Exception as e:
    #     logger.error(f"❌ Failed to start daily reports scheduler: {e}")
    #     logger.warning("⚠️ Automatic daily report generation will be unavailable")

    logger.info("✅ API Runner started successfully")

@app.on_event("shutdown")
async def shutdown_event():
    global price_manager, websocket_manager, news_auto_updater, news_websocket_manager
    
    logger.info("🔌 Shutting down API Runner...")
    
    # Shutdown real-time services
    try:
        if price_manager:
            logger.info("🛑 Stopping real-time stock price system...")
            await price_manager.stop()
            
        logger.info("✅ Real-time services stopped")
    except Exception as e:
        logger.error(f"❌ Error stopping real-time services: {e}")
    
    # Shutdown news auto-updater
    try:
        if news_auto_updater:
            logger.info("🛑 Stopping news auto-updater...")
            await news_auto_updater.stop()
            logger.info("✅ News auto-updater stopped")
    except Exception as e:
        logger.error(f"❌ Error stopping news auto-updater: {e}")
    
    # Shutdown daily reports scheduler
    try:
        logger.info("🛑 Stopping daily reports scheduler...")
        await shutdown_scheduler()
        logger.info("✅ Daily reports scheduler stopped")
    except Exception as e:
        logger.error(f"❌ Error stopping daily reports scheduler: {e}")
    
    # Shutdown news feed system
    try:
        await close_news_manager()
        logger.info("✅ News feed system stopped")
    except Exception as e:
        logger.error(f"❌ Error stopping news feed system: {e}")
    
    logger.info("✅ API Runner shutdown complete")

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

    # Check real-time service status
    realtime_status = "stopped"
    websocket_status = "integrated"  # WebSocket is now integrated with FastAPI
    if price_manager and price_manager.is_running:
        realtime_status = "running"
    
    # Check news feed status
    try:
        from news_feed.api import get_news_manager
        news_manager = get_news_manager()
        news_status = "running" if news_manager else "stopped"
    except:
        news_status = "not_initialized"
    
    return {
        "status": "healthy",
        "docker": docker_status,
        "realtime_prices": realtime_status,
        "websocket_server": websocket_status,
        "news_feed": news_status,
        "backend_image": BACKEND_IMAGE,
        "data_volume": DATA_VOLUME,
        "api_keys_configured": {
            "serpapi": bool(SERPAPI_API_KEY),
            "openai": bool(OPENAI_API_KEY),
            "anthropic": bool(ANTHROPIC_API_KEY)
        }
    }

@app.get("/api/news/auto-updater/status")
async def news_auto_updater_status():
    """Get status of the news auto-updater system."""
    global news_auto_updater, news_websocket_manager
    
    if not news_auto_updater:
        return {
            "status": "not_initialized",
            "message": "News auto-updater not initialized"
        }
    
    try:
        status = news_auto_updater.get_status()
        
        # Add WebSocket connection info
        if news_websocket_manager:
            status["websocket_connections"] = news_websocket_manager.get_connection_count()
            status["subscribed_tickers_websocket"] = news_websocket_manager.get_all_subscribed_tickers()
        
        return status
        
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }

@app.websocket("/api/news/ws")
async def news_websocket_endpoint(websocket):
    """WebSocket endpoint for real-time news streaming with automatic updates."""
    global news_websocket_manager
    
    if not news_websocket_manager:
        await websocket.close(code=1011, reason="News WebSocket service not available")
        return
    
    await news_websocket_manager.handle_websocket_connection(websocket)

# ------------- Create job -------------
@app.post("/run", response_model=JobResponse)
async def start_analysis(job: JobRequest, background_tasks: BackgroundTasks):
    """
    Start a new stock analysis job
    """
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker service not available")

    if not SERPAPI_API_KEY or not OPENAI_API_KEY or not ANTHROPIC_API_KEY:
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
        job.llm, job.pipeline, job.query,
    )

    return JobResponse(job_id=job_id, ticker=job.ticker, status="pending", message="Analysis job started")

# ------------- Chat Feature -------------
@app.post("/chat", response_model=JobResponse)
async def start_chat(chat_req: ChatRequest, background_tasks: BackgroundTasks):
    """
    Start a chat session for conversational AI stock analysis.
    
    The chat pipeline allows interactive Q&A about stocks with session continuity.
    
    Args:
        chat_req: Contains email, user_prompt, and optional session_id
    
    Returns:
        Job information including job_id and status
    """
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker service not available")

    if not SERPAPI_API_KEY or not OPENAI_API_KEY or not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=400, detail="API keys not configured")
    
    if not chat_req.email:
        raise HTTPException(status_code=400, detail="User email is required")

    try:
        user_email = chat_req.email.lower()
        request_timestamp = chat_req.timestamp

        # Generate job_id using timestamp from request
        # Extract datetime components from ISO timestamp (e.g., "2025-11-06T02:19:04")
        timestamp_str = request_timestamp.replace("-", "").replace(":", "").replace("T", "_").split(".")[0]
        job_id = f"chat_{timestamp_str}"

        jobs[job_id] = {
            "job_id": job_id,
            "ticker": "pending",  # Will be updated once backend identifies ticker
            "company": None,  # Not required for chat
            "user_email": user_email,
            "timestamp": request_timestamp,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "progress": "Chat session queued",
            "user_prompt": chat_req.user_prompt,
            "session_id": chat_req.session_id,
            "pipeline": "chat"
        }

        # Start chat job in background
        background_tasks.add_task(
            run_chat_job,
            job_id,
            user_email,
            request_timestamp,
            chat_req.user_prompt,
            chat_req.session_id
        )

        return JobResponse(
            job_id=job_id, 
            ticker="pending",  # Will be updated to actual ticker (e.g., "NVDA") once identified
            status="pending", 
            message=f"Chat session started"
        )

    except Exception as e:
        logger.error(f"Error starting chat session: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to start chat: {str(e)}")


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
    container_id = job.get('container_id')

    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")

    async def log_generator():
        # Get ticker dynamically - it may be updated during streaming (e.g., chat jobs)
        def get_current_ticker():
            return (jobs[job_id].get('ticker') or '').upper()
        
        # Get base path dynamically as ticker may change
        def get_current_base():
            return job_root(jobs[job_id])
        
        ticker = get_current_ticker()
        base = get_current_base()
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

        yield sse("connection", f"Connected to log stream for {get_current_ticker()}")

        if container_id:
            try:
                c = docker_client.containers.get(container_id)
                c.reload()
                container = c
                logger.info(f"Found analysis container for {get_current_ticker()}: {container_id}")
            except docker.errors.NotFound:
                logger.info(f"Analysis container not found for {get_current_ticker()}, using volume fallback")
                container = None

        async def probe_size_and_read_from_container(start_byte: int):
            if not container:
                return 0, b""
            try:
                container.reload()
                current_base = get_current_base()  # Get fresh path in case ticker updated
                path = f"{current_base}/info.log"
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
                current_base = get_current_base()  # Get fresh path in case ticker updated
                path = f"{current_base}/info.log"
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
            
            # Track last two lines for SESSION_ID extraction
            last_two_lines = []
            
            for seg in complete:
                line = seg.rstrip("\r\n")
                if not line:
                    continue
                have_seen_any_data = True
                
                # Track last two lines
                last_two_lines.append(line)
                if len(last_two_lines) > 2:
                    last_two_lines.pop(0)
                
                # Check for completion message
                if "THE ENTIRE PROGRAM IS COMPLETED" in line:
                    job["status"] = "completed"
                    job["progress"] = "Analysis completed successfully"
                    
                    # Extract SESSION_ID from second-to-last line if available
                    session_id = None
                    if len(last_two_lines) >= 2:
                        second_last = last_two_lines[-2]
                        if "SESSION_ID:" in second_last:
                            # Extract session_id value after "SESSION_ID: "
                            parts = second_last.split("SESSION_ID:")
                            if len(parts) > 1:
                                session_id = parts[1].strip()
                    
                    # Send completion with optional session_id
                    completion_payload = {
                        "status": "completed",
                        "message": "The entire program is completed",
                        "timestamp": datetime.now().isoformat()
                    }
                    if session_id:
                        completion_payload["session_id"] = session_id
                    
                    yield f"event: completed\ndata: {json.dumps(completion_payload)}\n\n"
                else:
                    # Determine message type: NL (natural language) or LOG
                    message_type = "NL" if "[LLM]" in line else "LOG"
                    
                    payload = {
                        "status": "log",
                        "message": line,
                        "type": message_type,
                        "timestamp": datetime.now().isoformat()
                    }
                    yield f"event: log\ndata: {json.dumps(payload)}\n\n"
            
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

# ------------- Download Excel Financial Model -------------
@app.get("/jobs/{job_id}/download/financial-model")
async def download_financial_model(job_id: str):
    """
    Download the Excel financial model (.xlsx file)
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    ticker = (job.get("ticker") or "").upper()
    
    # Container-side path (the API container sees the volume at /data)
    base = Path(job_root(job))  # e.g. /data/<email>/<TICKER>/<timestamp>
    xlsx_path = base / "models" / f"{ticker}_financial_model.xlsx"

    # Optional: small stability wait to avoid half-written file
    for _ in range(6):  # up to ~3s
        if xlsx_path.exists() and xlsx_path.stat().st_size > 0:
            break
        time.sleep(0.5)

    if not xlsx_path.exists():
        raise HTTPException(status_code=404, detail="Financial model Excel not found")

    return FileResponse(
        path=str(xlsx_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{ticker}_financial_model.xlsx",
        headers={
            "Cache-Control": "no-store, private",
            "Content-Disposition": f'attachment; filename="{ticker}_financial_model.xlsx"',
            "Content-Transfer-Encoding": "binary",
        },
    )

# ------------- Download Professional Analysis Report as PDF -------------
@app.get("/jobs/{job_id}/download/professional-report")
async def download_professional_report(job_id: str):
    """
    Download professional analysis report as PDF (converted from markdown)
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    ticker = (job.get("ticker") or "").upper()
    
    # Container-side path
    base = Path(job_root(job))
    md_path = base / "reports" / f"{ticker}_Professional_Analysis_Report.md"

    # Stability wait
    for _ in range(6):
        if md_path.exists() and md_path.stat().st_size > 0:
            break
        time.sleep(0.5)

    if not md_path.exists():
        raise HTTPException(status_code=404, detail="Professional analysis report not found")

    try:
        # Read markdown content
        with open(md_path, 'r', encoding='utf-8') as f:
            md_content = f.read()
        
        # Convert to PDF
        pdf_bytes = convert_md_to_pdf(md_content, ticker)
        
        headers = {"Content-Disposition": f'attachment; filename="{ticker}_Professional_Analysis_Report.pdf"'}
        return StreamingResponse(BytesIO(pdf_bytes), media_type='application/pdf', headers=headers)
        
    except Exception as e:
        logger.error(f"Error downloading professional report for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read or convert professional report: {str(e)}")

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

# ------------- List job files -------------
@app.get("/jobs/{job_id}/files")
async def list_job_files(job_id: str):
    """
    List available files for a job. Now only checks for the 2 main download files.
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    ticker = (job.get("ticker") or "").upper()
    base = Path(job_root(job))

    try:
        files = {
            "financial_model": False,
            "professional_report": False,
        }

        # Check for Excel financial model
        xlsx_path = base / "models" / f"{ticker}_financial_model.xlsx"
        files["financial_model"] = xlsx_path.exists()

        # Check for professional analysis report
        md_path = base / "reports" / f"{ticker}_Professional_Analysis_Report.md"
        files["professional_report"] = md_path.exists()

        return {
            "job_id": job_id,
            "ticker": ticker,
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
