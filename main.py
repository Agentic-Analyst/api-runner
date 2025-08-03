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


async def run_analysis_job(job_id: str, ticker: str, company: Optional[str] = None, query: Optional[str] = None, pipeline: str = "full"):
    """Run stock analysis job in Docker container"""
    if not docker_client:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = "Docker client not available"
        return
    
    try:
        jobs[job_id]["status"] = "running"
        jobs[job_id]["progress"] = "Starting analysis container..."
        
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
        
        # Wait for container to complete with timeout
        try:
            # Use asyncio to run the blocking wait() call in a thread pool
            import concurrent.futures
            
            def wait_for_container():
                return container.wait(timeout=1800)  # 30 minute timeout
            
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as executor:
                result = await loop.run_in_executor(executor, wait_for_container)
                
        except Exception as e:
            logger.error(f"Container wait timeout or error: {e}")
            # Kill the container if it's taking too long
            try:
                container.kill()
                jobs[job_id]["status"] = "failed"
                jobs[job_id]["error"] = f"Analysis timeout: {str(e)}"
                return
            except Exception as kill_e:
                logger.error(f"Failed to kill container: {kill_e}")
        
        # Get container logs for debugging
        logs = container.logs().decode('utf-8')
        jobs[job_id]["container_logs"] = logs[-2000:]  # Keep last 2000 chars
        
        if result["StatusCode"] == 0:
            jobs[job_id]["status"] = "completed"
            jobs[job_id]["progress"] = "Analysis completed successfully"
            jobs[job_id]["completed_at"] = datetime.now().isoformat()
            logger.info(f"✅ Analysis job {job_id} completed successfully")
        else:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = f"Container exited with code {result['StatusCode']}"
            logger.error(f"❌ Analysis job {job_id} failed with exit code {result['StatusCode']}")
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
    """Stream job logs in real-time"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    async def log_generator():
        # This would stream logs from the container or log files
        # For now, return job progress updates
        job = jobs[job_id]
        yield f"data: {json.dumps({'message': job.get('progress', 'No progress info')})}\n\n"
        
        if job['status'] == 'failed' and 'error' in job:
            yield f"data: {json.dumps({'error': job['error']})}\n\n"
            
        if 'container_logs' in job:
            yield f"data: {json.dumps({'container_logs': job['container_logs']})}\n\n"
    
    return StreamingResponse(log_generator(), media_type="text/plain")


@app.get("/jobs/{job_id}/files/{filename}")
async def download_file(job_id: str, filename: str):
    """Download a specific file from job results"""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    if job['status'] != 'completed':
        raise HTTPException(status_code=400, detail="Job not completed")
    
    # This would need to be implemented based on how files are stored
    # in the Docker volume. For now, return a placeholder.
    raise HTTPException(status_code=501, detail="File download not yet implemented")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)