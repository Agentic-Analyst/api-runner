# api-runner/main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import docker
import uuid
import os
import time
import pathlib
import logging
from typing import Dict, Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Stock Analyst Runner", version="1.0.0")

# Add CORS middleware for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this properly for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Docker client
docker_available = False
client = None

try:
    client = docker.from_env()
    client.ping()  # Test connection
    docker_available = True
    logger.info("Docker client initialized successfully")
except Exception as e:
    logger.warning(f"Docker client not available: {e}")
    logger.warning("Running in Docker-disabled mode - some features won't work")
    docker_available = False

VOLUME_NAME = os.getenv("DATA_VOLUME", "stockdata")
BACKEND_IMAGE = os.getenv("BACKEND_IMAGE", "stock-analyst:latest")

# Required API keys for the backend
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Check if required API keys are present
if not SERPAPI_API_KEY:
    logger.warning("SERPAPI_API_KEY not set - backend jobs may fail")
if not OPENAI_API_KEY:
    logger.warning("OPENAI_API_KEY not set - backend jobs may fail")

class RunRequest(BaseModel):
    ticker: str
    company: str
    pipeline: str = "full"

class JobStatus(BaseModel):
    job_id: str
    status: str
    exit_code: int = None
    outputs: Dict[str, str] = None
    logs_tail: str = None

# In-memory job storage (use Redis or database for production)
JOBS: Dict[str, Dict[str, Any]] = {}

@app.post("/run")
def run_analysis(req: RunRequest):
    """Start a new stock analysis job"""
    if not docker_available:
        raise HTTPException(503, "Docker is not available - please run the API runner with Docker access")
    
    try:
        job_id = str(uuid.uuid4())
        logger.info(f"Starting job {job_id} for ticker {req.ticker}")
        
        # Ensure the data volume exists
        try:
            client.volumes.get(VOLUME_NAME)
        except docker.errors.NotFound:
            logger.info(f"Creating volume {VOLUME_NAME}")
            client.volumes.create(VOLUME_NAME)
        
        container = client.containers.run(
            image=BACKEND_IMAGE,
            command=["--ticker", req.ticker, "--company", req.company, "--pipeline", req.pipeline],
            detach=True,
            volumes={VOLUME_NAME: {"bind": "/app/data", "mode": "rw"}},
            working_dir="/app",
            name=f"stock-analysis-{job_id[:8]}",  # Give container a readable name
            remove=False,  # Keep container for log inspection
            environment={
                "SERPAPI_API_KEY": SERPAPI_API_KEY,
                "OPENAI_API_KEY": OPENAI_API_KEY,
            }
        )
        
        JOBS[job_id] = {
            "container_id": container.id,
            "ticker": req.ticker.upper(),
            "company": req.company,
            "pipeline": req.pipeline,
            "created_at": time.time()
        }
        
        logger.info(f"Job {job_id} started with container {container.id}")
        return {"job_id": job_id, "status": "started"}
        
    except docker.errors.ImageNotFound:
        logger.error(f"Docker image {BACKEND_IMAGE} not found")
        raise HTTPException(404, f"Backend image {BACKEND_IMAGE} not found")
    except docker.errors.APIError as e:
        logger.error(f"Docker API error: {e}")
        raise HTTPException(500, f"Docker error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error starting job: {e}")
        raise HTTPException(500, f"Failed to start job: {str(e)}")

@app.get("/jobs/{job_id}")
def get_job_status(job_id: str):
    """Get job status and results"""
    if not docker_available:
        raise HTTPException(503, "Docker is not available")
        
    info = JOBS.get(job_id)
    if not info:
        raise HTTPException(404, "Job not found")
    
    try:
        container = client.containers.get(info["container_id"])
        status = container.status
        exit_code = container.attrs["State"].get("ExitCode")
        
        result = {
            "job_id": job_id,
            "status": status,
            "exit_code": exit_code,
            "ticker": info["ticker"],
            "company": info["company"],
            "pipeline": info["pipeline"]
        }
        
        # Add outputs and logs if job is finished
        if status == "exited":
            result["logs_tail"] = container.logs(tail=200).decode(errors="ignore")
            result["outputs"] = get_job_outputs(job_id, info["ticker"])
        
        return result
        
    except docker.errors.NotFound:
        logger.error(f"Container for job {job_id} not found")
        raise HTTPException(404, "Job container not found")
    except Exception as e:
        logger.error(f"Error getting job status: {e}")
        raise HTTPException(500, f"Failed to get job status: {str(e)}")

def get_job_outputs(job_id: str, ticker: str) -> Dict[str, str]:
    """Get list of output files for a job using a temporary container"""
    try:
        # Use a temporary container to list files in the volume
        temp_container = client.containers.run(
            image="alpine:latest",
            command=["find", f"/data/{ticker}", "-type", "f", "-exec", "basename", "{}", ";"],
            volumes={VOLUME_NAME: {"bind": "/data", "mode": "ro"}},
            remove=True,
            detach=False
        )
        
        output = temp_container.decode().strip()
        if output:
            files = {}
            for filename in output.split('\n'):
                if filename:
                    files[filename] = f"/jobs/{job_id}/files/{filename}"
            return files
        return {}
        
    except Exception as e:
        logger.error(f"Error getting outputs for job {job_id}: {e}")
        return {}

@app.get("/jobs/{job_id}/logs")
def get_job_logs(job_id: str, tail: int = 1000):
    """Get full job logs"""
    info = JOBS.get(job_id)
    if not info:
        raise HTTPException(404, "Job not found")
    
    try:
        container = client.containers.get(info["container_id"])
        logs = container.logs(tail=tail).decode(errors="ignore")
        return {"job_id": job_id, "logs": logs}
    except docker.errors.NotFound:
        raise HTTPException(404, "Job container not found")
    except Exception as e:
        logger.error(f"Error getting logs for job {job_id}: {e}")
        raise HTTPException(500, f"Failed to get logs: {str(e)}")

@app.get("/jobs/{job_id}/files/{filename}")
def download_file(job_id: str, filename: str):
    """Download an output file from a job"""
    info = JOBS.get(job_id)
    if not info:
        raise HTTPException(404, "Job not found")
    
    try:
        # Use a temporary container to copy the file out
        temp_container = client.containers.create(
            image="alpine:latest",
            command=["cat", f"/data/{info['ticker']}/{filename}"],
            volumes={VOLUME_NAME: {"bind": "/data", "mode": "ro"}}
        )
        
        temp_container.start()
        temp_container.wait()
        
        # Get the file content
        file_content = temp_container.logs()
        temp_container.remove()
        
        # Create a temporary file to serve
        temp_path = f"/tmp/{job_id}_{filename}"
        with open(temp_path, "wb") as f:
            f.write(file_content)
        
        return FileResponse(
            temp_path,
            filename=filename,
            media_type="application/octet-stream"
        )
        
    except Exception as e:
        logger.error(f"Error downloading file {filename} for job {job_id}: {e}")
        raise HTTPException(500, f"Failed to download file: {str(e)}")

@app.get("/")
def root():
    """Health check endpoint"""
    return {
        "service": "Stock Analyst Runner",
        "status": "healthy",
        "version": "1.0.0",
        "docker_available": docker_available,
        "api_keys_configured": {
            "serpapi": bool(SERPAPI_API_KEY),
            "openai": bool(OPENAI_API_KEY)
        }
    }

@app.get("/jobs")
def list_jobs():
    """List all jobs"""
    jobs = []
    for job_id, info in JOBS.items():
        try:
            container = client.containers.get(info["container_id"])
            jobs.append({
                "job_id": job_id,
                "ticker": info["ticker"],
                "company": info["company"],
                "status": container.status,
                "created_at": info.get("created_at")
            })
        except docker.errors.NotFound:
            # Container was removed
            jobs.append({
                "job_id": job_id,
                "ticker": info["ticker"],
                "company": info["company"],
                "status": "removed",
                "created_at": info.get("created_at")
            })
    
    return {"jobs": jobs}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
