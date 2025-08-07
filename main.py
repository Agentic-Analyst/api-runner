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

import docker
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
import aiofiles
import uvicorn

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
    ticker: str
    company: Optional[str] = None
    query: Optional[str] = None
    pipeline: Optional[str] = "full"

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
        # Try different connection methods
        logger.info("Initializing Docker client...")
        
        # Method 1: Default connection
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
                                        if "PIPELINE SESSION COMPLETED" in line:
                                            jobs[job_id]["progress"] = "Analysis completed successfully"
                                            jobs[job_id]["status"] = "completed"
                                        elif "STAGE: ARTICLE SCRAPING" in line:
                                            jobs[job_id]["progress"] = "Scraping articles..."
                                        elif "STAGE: ARTICLE FILTERING" in line:
                                            jobs[job_id]["progress"] = "Filtering articles..."
                                        elif "STAGE: LLM ANALYSIS & SCREENING" in line:
                                            jobs[job_id]["progress"] = "Running LLM analysis..."
                                        elif "Pipeline initialized for" in line:
                                            jobs[job_id]["progress"] = "Pipeline initialized"
                                        elif "ERROR" in line:
                                            error_msg = line.split("|")[-1].strip() if "|" in line else line
                                            jobs[job_id]["error"] = error_msg
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
                                        if "PIPELINE SESSION COMPLETED" in line:
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


async def run_analysis_job(job_id: str, ticker: str, company: Optional[str] = None, query: Optional[str] = None, pipeline: str = "full"):
    """Run stock analysis job in Docker container with real-time monitoring"""
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
        
        cmd = ["--ticker", ticker, "--company", company, "--pipeline", pipeline]
        if query:
            cmd.extend(["--search-query", query])
        
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
                result = await loop.run_in_executor(executor, wait_for_container)
                
        except Exception as e:
            logger.error(f"Container wait timeout or error: {e}")
            # Kill the container if it's taking too long
            try:
                container.kill()
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
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = f"Container exited with code {exit_code}"
            logger.error(f"❌ Analysis job {job_id} failed with exit code {exit_code}")
            
        # Clean up container
        try:
            container.remove()
        except Exception as e:
            logger.warning(f"Failed to remove container: {e}")
            
    except Exception as e:
        logger.error(f"❌ Error in analysis job {job_id}: {e}")
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
    """Start a new stock analysis job"""
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
    
    # Start background task
    background_tasks.add_task(run_analysis_job, job_id, request.ticker, request.company, request.query, request.pipeline)
    
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
    """Stream info.log file content directly in real-time using Server-Sent Events"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    ticker = job.get('ticker', '').upper()
    container_id = job.get('container_id')
    
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")
    
    async def log_generator():
        last_size = 0
        container = None
        
        # Send initial connection confirmation
        yield f"data: {json.dumps({'message': f'Connected to log stream for {ticker}', 'timestamp': datetime.now().isoformat(), 'type': 'connection'})}\n\n"
        
        # Try to get the analysis container
        if container_id:
            try:
                container = docker_client.containers.get(container_id)
                logger.info(f"Found analysis container for {ticker}: {container_id}")
            except docker.errors.NotFound:
                logger.info(f"Analysis container not found for {ticker}, will check volume")
        
        while True:
            try:
                current_size = 0
                new_content = ""
                
                # First, try to read from the running analysis container
                if container:
                    try:
                        container.reload()
                        if container.status == 'running':
                            # Read directly from the analysis container's working directory
                            size_result = container.exec_run(f"sh -c 'if [ -f data/{ticker}/info.log ]; then wc -c data/{ticker}/info.log | cut -d\" \" -f1; else echo 0; fi'")
                            if size_result.exit_code == 0:
                                current_size = int(size_result.output.decode('utf-8').strip())
                                
                                if current_size > last_size:
                                    # Get new content from the container
                                    content_result = container.exec_run(f"sh -c 'if [ -f data/{ticker}/info.log ]; then tail -c +{last_size + 1} data/{ticker}/info.log; fi'")
                                    if content_result.exit_code == 0:
                                        new_content = content_result.output.decode('utf-8').strip()
                        else:
                            # Container finished, try one final read then switch to volume
                            try:
                                content_result = container.exec_run(f"sh -c 'if [ -f data/{ticker}/info.log ]; then tail -c +{last_size + 1} data/{ticker}/info.log; fi'")
                                if content_result.exit_code == 0:
                                    new_content = content_result.output.decode('utf-8').strip()
                                    if new_content:
                                        current_size = last_size + len(new_content)
                            except:
                                pass
                            container = None  # Stop trying to read from container
                    except Exception as container_error:
                        logger.warning(f"Error reading from container {container_id}: {container_error}")
                        container = None
                
                # If no container or container method failed, try volume (fallback)
                if current_size == 0 and last_size == 0:
                    try:
                        size_container = docker_client.containers.run(
                            "alpine:latest",
                            command=f"sh -c 'if [ -f /data/{ticker}/info.log ]; then wc -c /data/{ticker}/info.log | cut -d\" \" -f1; else echo 0; fi'",
                            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                            remove=True,
                            detach=False
                        )
                        volume_size = int(size_container.decode('utf-8').strip())
                        
                        if volume_size > last_size:
                            content_container = docker_client.containers.run(
                                "alpine:latest",
                                command=f"sh -c 'if [ -f /data/{ticker}/info.log ]; then tail -c +{last_size + 1} /data/{ticker}/info.log; fi'",
                                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                                remove=True,
                                detach=False
                            )
                            new_content = content_container.decode('utf-8').strip()
                            current_size = volume_size
                    except Exception as volume_error:
                        logger.debug(f"Volume read failed (expected if file doesn't exist yet): {volume_error}")
                
                # Process new content if any
                if new_content:
                    logger.info(f"New content found for {ticker}: {len(new_content)} characters")
                    # Send each new line immediately as it appears in info.log
                    for line in new_content.split('\n'):
                        if line.strip():
                            yield f"data: {json.dumps({'message': line.strip(), 'timestamp': datetime.now().isoformat(), 'type': 'log'})}\n\n"
                    last_size = current_size
                else:
                    # Send heartbeat/status if no new content
                    if last_size == 0:
                        if container:
                            yield f"data: {json.dumps({'message': 'Analysis running, waiting for log output...', 'timestamp': datetime.now().isoformat(), 'type': 'status'})}\n\n"
                        else:
                            # Check if job is completed
                            if job.get('status') == 'completed':
                                yield f"data: {json.dumps({'message': 'Analysis completed', 'timestamp': datetime.now().isoformat(), 'type': 'completion'})}\n\n"
                                break
                            else:
                                yield f"data: {json.dumps({'message': 'Waiting for analysis to start...', 'timestamp': datetime.now().isoformat(), 'type': 'status'})}\n\n"
                
            except Exception as e:
                logger.error(f"Error streaming info.log for job {job_id}: {e}")
                yield f"data: {json.dumps({'error': f'Stream error: {str(e)}', 'timestamp': datetime.now().isoformat(), 'type': 'error'})}\n\n"
                break
            
            # Wait before next check
            await asyncio.sleep(1)
    
    return StreamingResponse(log_generator(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", 
        "Connection": "keep-alive",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET",
        "Access-Control-Allow-Headers": "Cache-Control"
    })


@app.get("/jobs/{job_id}/files/{filename}")
async def download_file(job_id: str, filename: str):
    """Download a specific file from job results"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    ticker = job.get('ticker', '').upper()
    
    # For info.log, we can read it directly from the Docker volume
    if filename == "info.log" and docker_client:
        try:
            # Create a temporary container to access the volume
            temp_container = docker_client.containers.run(
                "alpine:latest",
                command=f"cat /data/{ticker}/info.log",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                remove=True,
                detach=False
            )
            
            log_content = temp_container.decode('utf-8')
            
            # Return as plain text
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse(log_content, media_type="text/plain")
            
        except Exception as e:
            logger.error(f"Error reading {filename} for job {job_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Could not read {filename}: {str(e)}")
    
    # For other files, return placeholder for now
    raise HTTPException(status_code=501, detail="File download not yet implemented for this file type")


@app.get("/jobs/{job_id}/info-log")
async def get_info_log(job_id: str):
    """Get the info.log file content for a job"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    ticker = job.get('ticker', '').upper()
    
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")
    
    try:
        # Read info.log from the volume
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
    """Download all searched articles as a zip file"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    ticker = job.get('ticker', '').upper()
    
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")
    
    try:
        # Create a temporary directory to extract files
        with tempfile.TemporaryDirectory() as temp_dir:
            # Copy searched articles from Docker volume to temp directory
            temp_container = docker_client.containers.run(
                "alpine:latest",
                command=f"sh -c 'if [ -d /data/{ticker}/searched ]; then tar -czf /tmp/searched.tar.gz -C /data/{ticker}/searched .; else echo \"No searched articles\"; fi'",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                remove=True,
                detach=False
            )
            
            # Extract the tar.gz to temp directory
            extract_container = docker_client.containers.run(
                "alpine:latest",
                command=f"sh -c 'if [ -f /data/{ticker}/searched.tar.gz ]; then cp /data/{ticker}/searched.tar.gz /tmp/; fi'",
                volumes={
                    DATA_VOLUME: {'bind': '/data', 'mode': 'ro'},
                    temp_dir: {'bind': '/tmp', 'mode': 'rw'}
                },
                remove=True,
                detach=False
            )
            
            zip_path = os.path.join(temp_dir, f"{ticker}_searched_articles.zip")
            
            # Create zip file with all searched articles
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Get list of files from Docker volume
                list_container = docker_client.containers.run(
                    "alpine:latest",
                    command=f"sh -c 'if [ -d /data/{ticker}/searched ]; then find /data/{ticker}/searched -name \"*.md\" -type f; fi'",
                    volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                    remove=True,
                    detach=False
                )
                
                file_list = list_container.decode('utf-8').strip().split('\n')
                
                for file_path in file_list:
                    if file_path.strip() and file_path.endswith('.md'):
                        # Read each file content
                        file_container = docker_client.containers.run(
                            "alpine:latest",
                            command=f"cat {file_path}",
                            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                            remove=True,
                            detach=False
                        )
                        
                        content = file_container.decode('utf-8')
                        filename = os.path.basename(file_path)
                        zipf.writestr(filename, content)
            
            # Return the zip file
            return FileResponse(
                zip_path,
                media_type='application/zip',
                filename=f"{ticker}_searched_articles.zip"
            )
    
    except Exception as e:
        logger.error(f"Error creating searched articles zip for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not create zip file: {str(e)}")


@app.get("/jobs/{job_id}/download/filtered-articles")
async def download_filtered_articles(job_id: str):
    """Download all filtered articles as a zip file"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    ticker = job.get('ticker', '').upper()
    
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")
    
    try:
        # Create a temporary directory for the zip file
        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = os.path.join(temp_dir, f"{ticker}_filtered_articles.zip")
            
            # Create zip file with all filtered articles
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Get list of filtered files from Docker volume
                list_container = docker_client.containers.run(
                    "alpine:latest",
                    command=f"sh -c 'if [ -d /data/{ticker}/filtered ]; then find /data/{ticker}/filtered -name \"filtered_*.md\" -type f; fi'",
                    volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                    remove=True,
                    detach=False
                )
                
                file_list = list_container.decode('utf-8').strip().split('\n')
                
                for file_path in file_list:
                    if file_path.strip() and file_path.endswith('.md'):
                        # Read each file content
                        file_container = docker_client.containers.run(
                            "alpine:latest",
                            command=f"cat {file_path}",
                            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                            remove=True,
                            detach=False
                        )
                        
                        content = file_container.decode('utf-8')
                        filename = os.path.basename(file_path)
                        zipf.writestr(filename, content)
                
                # Also include the filtered articles index if it exists
                try:
                    index_container = docker_client.containers.run(
                        "alpine:latest",
                        command=f"cat /data/{ticker}/filtered/filtered_articles_index.csv",
                        volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                        remove=True,
                        detach=False
                    )
                    index_content = index_container.decode('utf-8')
                    zipf.writestr("filtered_articles_index.csv", index_content)
                except:
                    pass  # Index file might not exist
            
            # Return the zip file
            return FileResponse(
                zip_path,
                media_type='application/zip',
                filename=f"{ticker}_filtered_articles.zip"
            )
    
    except Exception as e:
        logger.error(f"Error creating filtered articles zip for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not create zip file: {str(e)}")


@app.get("/jobs/{job_id}/download/screening-report")
async def download_screening_report(job_id: str):
    """Download the screening report markdown file"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    ticker = job.get('ticker', '').upper()
    
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")
    
    try:
        # Read screening_report.md from the volume
        temp_container = docker_client.containers.run(
            "alpine:latest",
            command=f"cat /data/{ticker}/screening_report.md",
            volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
            remove=True,
            detach=False
        )
        
        report_content = temp_container.decode('utf-8')
        
        # Create a temporary file to serve
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as temp_file:
            temp_file.write(report_content)
            temp_path = temp_file.name
        
        # Return the markdown file
        return FileResponse(
            temp_path,
            media_type='text/markdown',
            filename=f"{ticker}_screening_report.md"
        )
        
    except Exception as e:
        logger.error(f"Error reading screening report for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read screening report: {str(e)}")


@app.get("/jobs/{job_id}/download/all-results")
async def download_all_results(job_id: str):
    """Download all analysis results as a comprehensive zip file"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    ticker = job.get('ticker', '').upper()
    
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker not available")
    
    try:
        # Create a temporary directory for the comprehensive zip file
        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = os.path.join(temp_dir, f"{ticker}_complete_analysis.zip")
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Add info.log
                try:
                    log_container = docker_client.containers.run(
                        "alpine:latest",
                        command=f"cat /data/{ticker}/info.log",
                        volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                        remove=True,
                        detach=False
                    )
                    log_content = log_container.decode('utf-8')
                    zipf.writestr("info.log", log_content)
                except:
                    pass
                
                # Add screening report
                try:
                    report_container = docker_client.containers.run(
                        "alpine:latest",
                        command=f"cat /data/{ticker}/screening_report.md",
                        volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                        remove=True,
                        detach=False
                    )
                    report_content = report_container.decode('utf-8')
                    zipf.writestr("screening_report.md", report_content)
                except:
                    pass
                
                # Add structured data JSON
                try:
                    json_container = docker_client.containers.run(
                        "alpine:latest",
                        command=f"cat /data/{ticker}/screening_data.json",
                        volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                        remove=True,
                        detach=False
                    )
                    json_content = json_container.decode('utf-8')
                    zipf.writestr("screening_data.json", json_content)
                except:
                    pass
                
                # Add searched articles
                try:
                    searched_list = docker_client.containers.run(
                        "alpine:latest",
                        command=f"sh -c 'if [ -d /data/{ticker}/searched ]; then find /data/{ticker}/searched -name \"*.md\" -type f; fi'",
                        volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                        remove=True,
                        detach=False
                    ).decode('utf-8').strip().split('\n')
                    
                    for file_path in searched_list:
                        if file_path.strip() and file_path.endswith('.md'):
                            file_container = docker_client.containers.run(
                                "alpine:latest",
                                command=f"cat {file_path}",
                                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                                remove=True,
                                detach=False
                            )
                            content = file_container.decode('utf-8')
                            filename = f"searched/{os.path.basename(file_path)}"
                            zipf.writestr(filename, content)
                except:
                    pass
                
                # Add filtered articles
                try:
                    filtered_list = docker_client.containers.run(
                        "alpine:latest",
                        command=f"sh -c 'if [ -d /data/{ticker}/filtered ]; then find /data/{ticker}/filtered -name \"*.md\" -o -name \"*.csv\" -type f; fi'",
                        volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                        remove=True,
                        detach=False
                    ).decode('utf-8').strip().split('\n')
                    
                    for file_path in filtered_list:
                        if file_path.strip() and (file_path.endswith('.md') or file_path.endswith('.csv')):
                            file_container = docker_client.containers.run(
                                "alpine:latest",
                                command=f"cat {file_path}",
                                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                                remove=True,
                                detach=False
                            )
                            content = file_container.decode('utf-8')
                            filename = f"filtered/{os.path.basename(file_path)}"
                            zipf.writestr(filename, content)
                except:
                    pass
            
            # Return the comprehensive zip file
            return FileResponse(
                zip_path,
                media_type='application/zip',
                filename=f"{ticker}_complete_analysis.zip"
            )
    
    except Exception as e:
        logger.error(f"Error creating complete analysis zip for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not create comprehensive zip file: {str(e)}")


@app.get("/jobs/{job_id}/files")
async def list_job_files(job_id: str):
    """List all available files for a job"""
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
            "searched_articles": [],
            "filtered_articles": [],
            "articles_index": False,
            "filtered_index": False
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
            searched_count = int(searched_list.decode('utf-8').strip())
            files["searched_articles"] = searched_count
        except:
            files["searched_articles"] = 0
        
        # List filtered articles
        try:
            filtered_list = docker_client.containers.run(
                "alpine:latest",
                command=f"sh -c 'if [ -d /data/{ticker}/filtered ]; then ls /data/{ticker}/filtered/filtered_*.md 2>/dev/null | wc -l; else echo 0; fi'",
                volumes={DATA_VOLUME: {'bind': '/data', 'mode': 'ro'}},
                remove=True,
                detach=False
            )
            filtered_count = int(filtered_list.decode('utf-8').strip())
            files["filtered_articles"] = filtered_count
        except:
            files["filtered_articles"] = 0
        
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
    uvicorn.run(app, host="0.0.0.0", port=8080)