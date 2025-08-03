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
    container_logs: Optional[str] = None
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


async def monitor_container_logs(job_id: str, container):
    """Monitor container logs AND info.log file in real-time and update job progress"""
    try:
        def get_logs():
            try:
                # Monitor both container logs and info.log file
                import time
                import os
                last_info_log_size = 0
                
                # Get container logs in background
                log_iter = container.logs(stream=True, follow=True, stdout=True, stderr=True)
                
                while True:
                    if job_id not in jobs:
                        break
                    
                    try:
                        # Try to get next log line with timeout
                        log_line = next(log_iter, None)
                        if log_line:
                            log_text = log_line.decode('utf-8').strip()
                            if log_text:
                                # Update job with latest container log
                                jobs[job_id]["latest_log"] = log_text
                                jobs[job_id]["last_activity"] = datetime.now().isoformat()
                                
                                # Store recent logs (keep last 50 lines)
                                if "recent_logs" not in jobs[job_id]:
                                    jobs[job_id]["recent_logs"] = []
                                jobs[job_id]["recent_logs"].append(f"[CONTAINER] {log_text}")
                                if len(jobs[job_id]["recent_logs"]) > 50:
                                    jobs[job_id]["recent_logs"].pop(0)
                        
                        # Also check info.log file for updates
                        ticker = jobs[job_id].get('ticker', '').upper()
                        try:
                            # Use a temporary container to check info.log size and tail new content
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
                                    # Split into lines and process each
                                    for line in new_content.split('\n'):
                                        if line.strip():
                                            jobs[job_id]["latest_log"] = f"[INFO.LOG] {line.strip()}"
                                            jobs[job_id]["last_activity"] = datetime.now().isoformat()
                                            
                                            # Parse info.log for specific progress updates
                                            if "Pipeline initialized" in line:
                                                jobs[job_id]["progress"] = "Pipeline initialized"
                                            elif "Stock Analysis Pipeline Session Started" in line:
                                                jobs[job_id]["progress"] = "Analysis session started"
                                            elif "ERROR" in line:
                                                jobs[job_id]["progress"] = f"Error detected: {line.split('|')[-1].strip()}"
                                            elif "No filtered articles found" in line:
                                                jobs[job_id]["progress"] = "No articles found for screening"
                                            elif "completed" in line.lower():
                                                jobs[job_id]["progress"] = "Analysis completed"
                                            
                                            # Store recent logs from info.log
                                            if "recent_logs" not in jobs[job_id]:
                                                jobs[job_id]["recent_logs"] = []
                                            jobs[job_id]["recent_logs"].append(f"[INFO.LOG] {line.strip()}")
                                            if len(jobs[job_id]["recent_logs"]) > 50:
                                                jobs[job_id]["recent_logs"].pop(0)
                                
                                last_info_log_size = current_size
                        
                        except Exception as info_log_error:
                            # Info.log might not exist yet, that's OK
                            pass
                        
                        # Small delay to avoid overwhelming the system
                        time.sleep(2)
                        
                    except StopIteration:
                        # Container logs ended
                        break
                    except Exception as e:
                        logger.error(f"Error processing logs for job {job_id}: {e}")
                        time.sleep(5)  # Wait longer on error
                        
            except Exception as e:
                logger.error(f"Error in log streaming for job {job_id}: {e}")
        
        # Run log monitoring in thread pool to avoid blocking
        import concurrent.futures
        loop = asyncio.get_event_loop()
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            await loop.run_in_executor(executor, get_logs)
                    
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
        import concurrent.futures
        loop = asyncio.get_event_loop()
        
        # Monitor logs in a separate task
        log_task = asyncio.create_task(monitor_container_logs(job_id, container))
        
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
        
        # Get final container logs for debugging
        logs = container.logs().decode('utf-8')
        jobs[job_id]["container_logs"] = logs[-2000:]  # Keep last 2000 chars
        
        # Check container status
        container.reload()
        exit_code = container.attrs['State']['ExitCode']
        
        if exit_code == 0:
            jobs[job_id]["status"] = "completed"
            jobs[job_id]["progress"] = "Analysis completed successfully"
            jobs[job_id]["completed_at"] = datetime.now().isoformat()
            logger.info(f"✅ Analysis job {job_id} completed successfully")
        else:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = f"Container exited with code {exit_code}"
            # Check for specific error patterns in logs
            if "No filtered articles found" in logs:
                jobs[job_id]["error"] = "No articles found for screening - pipeline completed with warnings"
                jobs[job_id]["status"] = "completed_with_warnings"
            logger.error(f"❌ Analysis job {job_id} failed with exit code {exit_code}")
            logger.error(f"Container logs: {logs[-500:]}")  # Log last 500 chars
            
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
    """Get detailed job logs for debugging"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "progress": job.get("progress"),
        "error": job.get("error"),
        "container_logs": job.get("container_logs", "No logs available"),
        "container_id": job.get("container_id")
    }


@app.get("/jobs/{job_id}/logs/stream")
async def get_job_logs_stream(job_id: str):
    """Stream job logs in real-time using Server-Sent Events"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    async def log_generator():
        last_log_count = 0
        
        while True:
            if job_id not in jobs:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Job not found'})}\n\n"
                break
                
            job = jobs[job_id]
            
            # Send status update
            yield f"data: {json.dumps({'type': 'status', 'status': job.get('status'), 'progress': job.get('progress')})}\n\n"
            
            # Send new logs if available
            recent_logs = job.get('recent_logs', [])
            if len(recent_logs) > last_log_count:
                new_logs = recent_logs[last_log_count:]
                for log_line in new_logs:
                    yield f"data: {json.dumps({'type': 'log', 'message': log_line})}\n\n"
                last_log_count = len(recent_logs)
            
            # Send latest activity
            if 'latest_log' in job:
                yield f"data: {json.dumps({'type': 'latest', 'message': job['latest_log'], 'timestamp': job.get('last_activity')})}\n\n"
            
            # If job is completed or failed, send final update and break
            if job.get('status') in ['completed', 'failed', 'completed_with_warnings']:
                yield f"data: {json.dumps({'type': 'final', 'status': job['status'], 'message': job.get('error', 'Job completed')})}\n\n"
                break
                
            # Wait before next update
            await asyncio.sleep(2)
    
    return StreamingResponse(log_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)