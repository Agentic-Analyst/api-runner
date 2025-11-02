"""
Daily Reports API
Handles daily intelligence reports generation and delivery
"""

import os
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from io import BytesIO

from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse

# Import from config module instead of main to avoid circular imports
from config import BACKEND_IMAGE, DATA_VOLUME
from config import SERPAPI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, MONGO_URI, MONGO_DB
from .daily_report_pdf_converter import convert_daily_report_to_pdf
from .scheduler import init_scheduler, get_scheduler
from .models import (
    DailyReportRequest,
    CompanyDailyReportRequest,
    SectorDailyReportRequest,
    DailyReportJob,
    BatchJobResponse,
    DailyReportContent,
    MultiReportResponse,
    GetCompanyReportsRequest,
    GetSectorReportsRequest
)

logger = logging.getLogger(__name__)

# Module-level docker client (set by main.py after initialization)
docker_client = None

def set_docker_client(client):
    """Set the docker client instance (called from main.py after initialization)"""
    global docker_client
    docker_client = client

# Router
daily_reports_router = APIRouter(prefix="/api/daily-reports", tags=["Daily Reports"])

# Global storage for daily report jobs
daily_jobs: dict = {}

# Global storage for batch jobs
batch_jobs: dict = {}

# Scheduler instance (initialized on startup)
scheduler = None

# Fixed configuration for daily reports
DAILY_REPORT_EMAIL = "reports@vynnai.com"


def get_daily_report_path(ticker: str, timestamp: str) -> Path:
    """
    Get the path for daily report data.
    Path structure: /data/{email}/{TICKER}/{timestamp}/reports/
    
    This matches the job_root() structure from main.py:
    /data/<email>/<TICKER>/<timestamp>/reports/
    
    Args:
        ticker: Company ticker or sector name (e.g., "AAPL" or "Communication Services")
        timestamp: Date in format YYYY-MM-DD
    
    Returns:
        Path object for the reports directory
    """
    email = DAILY_REPORT_EMAIL
    # Normalize ticker/sector: uppercase and replace spaces with underscores
    # e.g., "Communication Services" -> "COMMUNICATION_SERVICES"
    ticker_normalized = ticker.upper().replace(" ", "_")
    return Path(f"/data/{email}/{ticker_normalized}/{timestamp}/reports")

async def run_daily_report_job(
    job_id: str,
    ticker: str,
    timestamp: str,
    pipeline: str,
    docker_client,
    backend_image: str,
    data_volume: str,
    env_keys: dict
):
    """
    Run daily report generation job in Docker container.
    
    Args:
        job_id: Unique job identifier
        ticker: Company ticker or sector name (will be normalized)
        timestamp: Date in format YYYY-MM-DD
        pipeline: Pipeline type ("company-daily-report" or "sector-daily-report")
        docker_client: Docker client instance
        backend_image: Docker image name for backend
        data_volume: Docker volume name for data
        env_keys: Dictionary containing API keys and MongoDB config
    """
    if not docker_client:
        daily_jobs[job_id]["status"] = "failed"
        daily_jobs[job_id]["error"] = "Docker client not available"
        return

    try:
        daily_jobs[job_id]["status"] = "running"
        daily_jobs[job_id]["progress"] = "Starting daily report generation..."

        # Normalize ticker/sector: uppercase and replace spaces with underscores
        # e.g., "Communication Services" -> "COMMUNICATION_SERVICES"
        ticker_normalized = ticker.upper().replace(" ", "_")

        # Environment variables
        env_vars = {
            "SERPAPI_API_KEY": env_keys.get("SERPAPI_API_KEY"),
            "OPENAI_API_KEY": env_keys.get("OPENAI_API_KEY"),
            "ANTHROPIC_API_KEY": env_keys.get("ANTHROPIC_API_KEY"),
            "MONGO_URI": env_keys.get("MONGO_URI"),
            "MONGO_DB": env_keys.get("MONGO_DB"),
            "DATA_PATH": "/data",
        }

        # Build command with fixed parameters (using normalized ticker)
        cmd = [
            "--ticker", ticker_normalized,
            "--email", DAILY_REPORT_EMAIL,
            "--timestamp", timestamp,
            "--pipeline", pipeline
        ]

        logger.info(f"🚀 Starting daily report generation with command: {cmd}")
        daily_jobs[job_id]["progress"] = "Running daily report pipeline..."

        # Normalize job_id for container name (remove spaces, special chars)
        container_name = f"daily-report-{job_id}".replace(" ", "_")

        # Start container
        container = docker_client.containers.run(
            backend_image,
            command=cmd,
            environment=env_vars,
            volumes={data_volume: {'bind': '/data', 'mode': 'rw'}},
            detach=True,
            remove=False,
            name=container_name
        )

        daily_jobs[job_id]["container_id"] = container.id
        daily_jobs[job_id]["progress"] = "Daily report generation running..."

        # Wait for container to complete
        try:
            result = container.wait(timeout=1800)  # 30 minute timeout
            exit_code = result.get("StatusCode", -1)

            if exit_code == 0:
                daily_jobs[job_id]["status"] = "completed"
                daily_jobs[job_id]["progress"] = "Daily reports generated successfully"
                daily_jobs[job_id]["completed_at"] = datetime.now().isoformat()
                logger.info(f"✅ Daily report job {job_id} completed successfully")
            else:
                # Capture container logs to see the actual error
                try:
                    container_logs = container.logs(tail=50).decode('utf-8')
                    logger.error(f"❌ Container logs (last 50 lines):\n{container_logs}")
                    daily_jobs[job_id]["error"] = f"Container exited with code {exit_code}. Check logs for details."
                    daily_jobs[job_id]["container_logs"] = container_logs
                except Exception as log_err:
                    logger.error(f"Failed to retrieve container logs: {log_err}")
                    daily_jobs[job_id]["error"] = f"Container exited with code {exit_code}"
                
                daily_jobs[job_id]["status"] = "failed"
                logger.error(f"❌ Daily report job {job_id} failed with exit code {exit_code}")

        except Exception as e:
            logger.error(f"Container wait timeout or error: {e}")
            try:
                container.reload()
                if container.status in ['running', 'paused']:
                    container.kill()
            except Exception as kill_e:
                logger.error(f"Failed to kill container: {kill_e}")
            
            daily_jobs[job_id]["status"] = "failed"
            daily_jobs[job_id]["error"] = f"Generation timeout: {str(e)}"
            daily_jobs[job_id]["completed_at"] = datetime.now().isoformat()

        # Cleanup container
        try:
            container.remove(force=True)
        except Exception as e:
            logger.warning(f"Failed to remove container: {e}")

    except Exception as e:
        logger.error(f"Error running daily report job {job_id}: {e}")
        daily_jobs[job_id]["status"] = "failed"
        daily_jobs[job_id]["error"] = str(e)
        daily_jobs[job_id]["completed_at"] = datetime.now().isoformat()


async def trigger_daily_report_generation(report_date: str) -> dict:
    """
    [DEPRECATED] Internal function to trigger daily report generation.
    
    This function is deprecated. The scheduler functionality needs to be updated
    to use the new company/sector report generation endpoints.
    
    Args:
        report_date: Date string in format YYYY-MM-DD
        
    Returns:
        Job information dict
    """
    logger.error("Scheduler is deprecated. Use /api/daily-reports/generate/company or /generate/sector instead.")
    return {
        "error": "Scheduler is deprecated. Please use the new company/sector report generation endpoints."
    }


async def initialize_scheduler():
    """Initialize and start the daily reports scheduler"""
    global scheduler
    
    try:
        scheduler = init_scheduler()
        scheduler.set_generation_callback(trigger_daily_report_generation)
        await scheduler.start()
        logger.info("✅ Daily reports scheduler initialized and started")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to initialize daily reports scheduler: {e}")
        return False


async def shutdown_scheduler():
    """Stop the daily reports scheduler"""
    global scheduler
    
    if scheduler:
        try:
            await scheduler.stop()
            logger.info("✅ Daily reports scheduler stopped")
        except Exception as e:
            logger.error(f"❌ Error stopping scheduler: {e}")


# ============================================================================
# ENDPOINTS
# ============================================================================

@daily_reports_router.get("/health")
async def daily_reports_health():
    """Health check for daily reports system"""
    return {
        "status": "healthy",
        "service": "daily-reports",
        "active_jobs": len(daily_jobs),
        "timestamp": datetime.now().isoformat()
    }


@daily_reports_router.post("/generate/company", response_model=BatchJobResponse)
async def generate_company_daily_report(
    request: CompanyDailyReportRequest,
    background_tasks: BackgroundTasks
):
    """
    Generate daily intelligence reports for multiple companies.
    
    Args:
        request: Contains list of tickers and timestamp (date) in format YYYY-MM-DD
    
    Returns:
        Batch job information including batch_id, job_ids and status
    """
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker service not available")

    if not SERPAPI_API_KEY or not OPENAI_API_KEY or not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=400, detail="API keys not configured")

    if not request.tickers:
        raise HTTPException(status_code=400, detail="At least one ticker is required")

    # Generate batch ID
    batch_id = f"company_batch_{request.timestamp}_{datetime.now().strftime('%H%M%S')}"
    job_ids = []

    # Prepare environment keys (shared across all jobs)
    env_keys = {
        "SERPAPI_API_KEY": SERPAPI_API_KEY,
        "OPENAI_API_KEY": OPENAI_API_KEY,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "MONGO_URI": MONGO_URI,
        "MONGO_DB": MONGO_DB
    }

    # Create jobs for each ticker
    for ticker in request.tickers:
        # Normalize ticker: uppercase and replace spaces with underscores
        ticker_normalized = ticker.upper().replace(" ", "_")
        job_id = f"company_report_{ticker_normalized}_{request.timestamp}"
        
        # Check if job already exists
        if job_id in daily_jobs:
            existing_status = daily_jobs[job_id].get("status")
            if existing_status in ["pending", "running"]:
                logger.warning(f"Job {job_id} already in progress, skipping")
                job_ids.append(job_id)
                continue

        # Create job record (store original ticker for display)
        daily_jobs[job_id] = {
            "job_id": job_id,
            "ticker": ticker_normalized,
            "original_ticker": ticker,  # Store original for display
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "progress": "Job queued",
            "timestamp": request.timestamp,
            "report_type": "company",
            "batch_id": batch_id
        }

        # Start background task (pass original ticker, will be normalized inside)
        background_tasks.add_task(
            run_daily_report_job,
            job_id,
            ticker,  # Pass original, will be normalized in run_daily_report_job
            request.timestamp,
            "company-daily-report",
            docker_client,
            BACKEND_IMAGE,
            DATA_VOLUME,
            env_keys
        )

        job_ids.append(job_id)
        logger.info(f"📋 Company report generation triggered for {ticker} on {request.timestamp} (job: {job_id})")

    # Store batch job info
    batch_jobs[batch_id] = {
        "batch_id": batch_id,
        "total_jobs": len(job_ids),
        "job_ids": job_ids,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "tickers_or_sectors": request.tickers,
        "report_type": "company",
        "timestamp": request.timestamp
    }

    return BatchJobResponse(
        batch_id=batch_id,
        total_jobs=len(job_ids),
        job_ids=job_ids,
        status="pending",
        created_at=batch_jobs[batch_id]["created_at"],
        tickers_or_sectors=request.tickers,
        report_type="company"
    )


@daily_reports_router.post("/generate/sector", response_model=BatchJobResponse)
async def generate_sector_daily_report(
    request: SectorDailyReportRequest,
    background_tasks: BackgroundTasks
):
    """
    Generate daily intelligence reports for multiple sectors.
    
    Args:
        request: Contains list of sector names and timestamp (date) in format YYYY-MM-DD
    
    Returns:
        Batch job information including batch_id, job_ids and status
    """
    if not docker_client:
        raise HTTPException(status_code=503, detail="Docker service not available")

    if not SERPAPI_API_KEY or not OPENAI_API_KEY or not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=400, detail="API keys not configured")

    if not request.sectors:
        raise HTTPException(status_code=400, detail="At least one sector is required")

    # Generate batch ID
    batch_id = f"sector_batch_{request.timestamp}_{datetime.now().strftime('%H%M%S')}"
    job_ids = []

    # Prepare environment keys (shared across all jobs)
    env_keys = {
        "SERPAPI_API_KEY": SERPAPI_API_KEY,
        "OPENAI_API_KEY": OPENAI_API_KEY,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "MONGO_URI": MONGO_URI,
        "MONGO_DB": MONGO_DB
    }

    # Create jobs for each sector
    for sector in request.sectors:
        # Normalize sector: uppercase and replace spaces with underscores
        sector_normalized = sector.upper().replace(" ", "_")
        job_id = f"sector_report_{sector_normalized}_{request.timestamp}"
        
        # Check if job already exists
        if job_id in daily_jobs:
            existing_status = daily_jobs[job_id].get("status")
            if existing_status in ["pending", "running"]:
                logger.warning(f"Job {job_id} already in progress, skipping")
                job_ids.append(job_id)
                continue

        # Create job record (store original sector for display)
        daily_jobs[job_id] = {
            "job_id": job_id,
            "ticker": sector_normalized,
            "original_ticker": sector,  # Store original for display
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "progress": "Job queued",
            "timestamp": request.timestamp,
            "report_type": "sector",
            "batch_id": batch_id
        }

        # Start background task (pass original sector, will be normalized inside)
        background_tasks.add_task(
            run_daily_report_job,
            job_id,
            sector,  # Pass original, will be normalized in run_daily_report_job
            request.timestamp,
            "sector-daily-report",
            docker_client,
            BACKEND_IMAGE,
            DATA_VOLUME,
            env_keys
        )

        job_ids.append(job_id)
        logger.info(f"📋 Sector report generation triggered for {sector} on {request.timestamp} (job: {job_id})")

    # Store batch job info
    batch_jobs[batch_id] = {
        "batch_id": batch_id,
        "total_jobs": len(job_ids),
        "job_ids": job_ids,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "tickers_or_sectors": request.sectors,
        "report_type": "sector",
        "timestamp": request.timestamp
    }

    return BatchJobResponse(
        batch_id=batch_id,
        total_jobs=len(job_ids),
        job_ids=job_ids,
        status="pending",
        created_at=batch_jobs[batch_id]["created_at"],
        tickers_or_sectors=request.sectors,
        report_type="sector"
    )


@daily_reports_router.get("/jobs/{job_id}/status", response_model=DailyReportJob)
async def get_job_status(job_id: str):
    """
    Get status of a report generation job.
    Useful for polling job progress after manual generation.
    """
    if job_id not in daily_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = daily_jobs[job_id]
    return DailyReportJob(
        job_id=job_id,
        status=job["status"],
        progress=job.get("progress"),
        created_at=job["created_at"],
        completed_at=job.get("completed_at"),
        error=job.get("error"),
        ticker=job.get("ticker"),
        report_type=job.get("report_type")
    )


@daily_reports_router.get("/batch/{batch_id}/status", response_model=BatchJobResponse)
async def get_batch_status(batch_id: str):
    """
    Get status of a batch job.
    Returns overall batch status and list of all job IDs.
    """
    if batch_id not in batch_jobs:
        raise HTTPException(status_code=404, detail="Batch job not found")
    
    batch = batch_jobs[batch_id]
    
    # Calculate overall batch status
    job_statuses = [daily_jobs.get(job_id, {}).get("status", "unknown") for job_id in batch["job_ids"]]
    
    if all(status == "completed" for status in job_statuses):
        overall_status = "completed"
    elif any(status == "failed" for status in job_statuses):
        overall_status = "partial" if any(status == "completed" for status in job_statuses) else "failed"
    elif any(status in ["pending", "running"] for status in job_statuses):
        overall_status = "running"
    else:
        overall_status = "unknown"
    
    return BatchJobResponse(
        batch_id=batch_id,
        total_jobs=batch["total_jobs"],
        job_ids=batch["job_ids"],
        status=overall_status,
        created_at=batch["created_at"],
        tickers_or_sectors=batch["tickers_or_sectors"],
        report_type=batch["report_type"]
    )


@daily_reports_router.get("/jobs/{job_id}/logs")
async def get_job_logs(job_id: str):
    """
    Get container logs for a job (useful for debugging failures).
    """
    if job_id not in daily_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = daily_jobs[job_id]
    container_logs = job.get("container_logs")
    
    if not container_logs:
        return {
            "job_id": job_id,
            "status": job.get("status"),
            "logs": "No logs available (job may still be running or logs weren't captured)"
        }
    
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "logs": container_logs
    }


@daily_reports_router.get("/scheduler/status")
async def get_scheduler_status():
    """Get status of the daily reports scheduler"""
    global scheduler
    
    if not scheduler:
        return {
            "status": "not_initialized",
            "message": "Scheduler not initialized"
        }
    
    return scheduler.get_status()


@daily_reports_router.get("/available-reports")
async def list_available_reports():
    """
    List all available company and sector daily reports.
    Scans the data volume for all tickers with reports.
    """
    base_path = Path(f"/data/{DAILY_REPORT_EMAIL}")
    
    if not base_path.exists():
        return {
            "company_reports": [],
            "sector_reports": [],
            "total": 0
        }
    
    company_reports = []
    sector_reports = []
    
    # Scan all ticker/sector folders
    for ticker_folder in base_path.iterdir():
        if ticker_folder.is_dir():
            ticker = ticker_folder.name
            reports_path = ticker_folder / "reports"
            
            if reports_path.exists() and any(reports_path.glob("*.md")):
                # Find all report files
                for md_file in reports_path.glob("*.md"):
                    if md_file.is_file():
                        # Extract date from filename or use folder structure
                        # Assuming filename format: {ticker}_daily_report_{date}.md
                        filename = md_file.stem
                        
                        # Try to determine if it's a company or sector report
                        # This is based on the job_id pattern in daily_jobs
                        matching_jobs = [
                            (job_id, job) for job_id, job in daily_jobs.items()
                            if job.get("ticker") == ticker
                        ]
                        
                        report_type = "company"  # Default
                        timestamp = None
                        
                        if matching_jobs:
                            # Use the most recent job info
                            job_id, job = matching_jobs[-1]
                            report_type = job.get("report_type", "company")
                            timestamp = job.get("timestamp")
                        
                        report_info = {
                            "ticker": ticker,
                            "filename": md_file.name,
                            "path": str(md_file),
                            "size": md_file.stat().st_size,
                            "timestamp": timestamp
                        }
                        
                        if report_type == "sector":
                            sector_reports.append(report_info)
                        else:
                            company_reports.append(report_info)
    
    # Sort by ticker
    company_reports.sort(key=lambda x: x["ticker"])
    sector_reports.sort(key=lambda x: x["ticker"])
    
    return {
        "company_reports": company_reports,
        "sector_reports": sector_reports,
        "total": len(company_reports) + len(sector_reports)
    }


@daily_reports_router.get("/company/{ticker}/markdown", response_model=DailyReportContent)
async def get_company_report_markdown(ticker: str, timestamp: str):
    """
    Get the company daily report as markdown content by ticker and date.
    
    Args:
        ticker: Company ticker symbol (e.g., "AAPL")
        timestamp: Date in format YYYY-MM-DD (e.g., "2025-10-31")
    """
    reports_path = get_daily_report_path(ticker, timestamp)
    
    if not reports_path.exists():
        raise HTTPException(status_code=404, detail=f"Reports folder not found for {ticker} on {timestamp}")
    
    # Find the company report for this date
    company_file = None
    for md_file in sorted(reports_path.glob("*.md"), reverse=True):
        if md_file.is_file():
            company_file = md_file
            break
    
    if not company_file or not company_file.exists():
        raise HTTPException(status_code=404, detail=f"Company report not found for {ticker} on {timestamp}")
    
    try:
        with open(company_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return DailyReportContent(
            filename=company_file.name,
            content=content,
            type="company",
            ticker=ticker
        )
    except Exception as e:
        logger.error(f"Error reading company report: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read company report: {str(e)}")


@daily_reports_router.post("/companies/markdown", response_model=MultiReportResponse)
async def get_multiple_company_reports_markdown(request: GetCompanyReportsRequest):
    """
    Get multiple company daily reports as markdown content for a specific date.
    
    Args:
        request: Contains list of tickers and timestamp (date)
    
    Returns:
        Multiple report contents with total count
    """
    if not request.tickers:
        raise HTTPException(status_code=400, detail="At least one ticker is required")
    
    reports = []
    
    for ticker in request.tickers:
        reports_path = get_daily_report_path(ticker, request.timestamp)
        
        if not reports_path.exists():
            logger.warning(f"Reports folder not found for {ticker} on {request.timestamp}")
            continue
        
        # Find the company report for this date
        company_file = None
        for md_file in sorted(reports_path.glob("*.md"), reverse=True):
            if md_file.is_file():
                company_file = md_file
                break
        
        if company_file and company_file.exists():
            try:
                with open(company_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                reports.append(DailyReportContent(
                    filename=company_file.name,
                    content=content,
                    type="company",
                    ticker=ticker
                ))
            except Exception as e:
                logger.error(f"Error reading company report for {ticker} on {request.timestamp}: {e}")
                # Continue with other reports
                continue
    
    if not reports:
        raise HTTPException(
            status_code=404, 
            detail=f"No company reports found for the specified tickers on {request.timestamp}"
        )
    
    return MultiReportResponse(
        reports=reports,
        total=len(reports),
        report_type="company"
    )


@daily_reports_router.get("/sector/{sector}/markdown", response_model=DailyReportContent)
async def get_sector_report_markdown(sector: str, timestamp: str):
    """
    Get the sector daily report as markdown content by sector name and date.
    
    Args:
        sector: Sector name (e.g., "Technology", "Healthcare")
        timestamp: Date in format YYYY-MM-DD (e.g., "2025-10-31")
    """
    reports_path = get_daily_report_path(sector, timestamp)
    
    if not reports_path.exists():
        raise HTTPException(status_code=404, detail=f"Reports folder not found for {sector} on {timestamp}")
    
    # Find the sector report for this date
    sector_file = None
    for md_file in sorted(reports_path.glob("*.md"), reverse=True):
        if md_file.is_file():
            sector_file = md_file
            break
    
    if not sector_file or not sector_file.exists():
        raise HTTPException(status_code=404, detail=f"Sector report not found for {sector} on {timestamp}")
    
    try:
        with open(sector_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return DailyReportContent(
            filename=sector_file.name,
            content=content,
            type="sector",
            ticker=sector
        )
    except Exception as e:
        logger.error(f"Error reading sector report: {e}")
        raise HTTPException(status_code=500, detail=f"Could not read sector report: {str(e)}")


@daily_reports_router.post("/sectors/markdown", response_model=MultiReportResponse)
async def get_multiple_sector_reports_markdown(request: GetSectorReportsRequest):
    """
    Get multiple sector daily reports as markdown content for a specific date.
    
    Args:
        request: Contains list of sectors and timestamp (date)
    
    Returns:
        Multiple report contents with total count
    """
    if not request.sectors:
        raise HTTPException(status_code=400, detail="At least one sector is required")
    
    reports = []
    
    for sector in request.sectors:
        reports_path = get_daily_report_path(sector, request.timestamp)
        
        if not reports_path.exists():
            logger.warning(f"Reports folder not found for {sector} on {request.timestamp}")
            continue
        
        # Find the sector report for this date
        sector_file = None
        for md_file in sorted(reports_path.glob("*.md"), reverse=True):
            if md_file.is_file():
                sector_file = md_file
                break
        
        if sector_file and sector_file.exists():
            try:
                with open(sector_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                reports.append(DailyReportContent(
                    filename=sector_file.name,
                    content=content,
                    type="sector",
                    ticker=sector
                ))
            except Exception as e:
                logger.error(f"Error reading sector report for {sector} on {request.timestamp}: {e}")
                # Continue with other reports
                continue
    
    if not reports:
        raise HTTPException(
            status_code=404, 
            detail=f"No sector reports found for the specified sectors on {request.timestamp}"
        )
    
    return MultiReportResponse(
        reports=reports,
        total=len(reports),
        report_type="sector"
    )


@daily_reports_router.get("/company/{ticker}/pdf")
async def download_company_report_pdf(ticker: str, timestamp: str):
    """
    Download the company daily report as PDF for a specific date.
    
    Args:
        ticker: Company ticker symbol (e.g., "AAPL")
        timestamp: Date in format YYYY-MM-DD (e.g., "2025-10-31")
    """
    reports_path = get_daily_report_path(ticker, timestamp)
    
    if not reports_path.exists():
        raise HTTPException(status_code=404, detail=f"Reports folder not found for {ticker} on {timestamp}")
    
    # Find the company report for this date
    company_file = None
    for md_file in sorted(reports_path.glob("*.md"), reverse=True):
        if md_file.is_file():
            company_file = md_file
            break
    
    if not company_file or not company_file.exists():
        raise HTTPException(status_code=404, detail=f"Company report not found for {ticker} on {timestamp}")
    
    try:
        # Read markdown content
        with open(company_file, 'r', encoding='utf-8') as f:
            md_content = f.read()
        
        # Convert to PDF
        pdf_bytes = convert_daily_report_to_pdf(md_content)
        
        # Generate PDF filename
        pdf_filename = f"{ticker}_daily_report.pdf"
        
        headers = {
            "Content-Disposition": f'attachment; filename="{pdf_filename}"'
        }
        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type='application/pdf',
            headers=headers
        )
        
    except Exception as e:
        logger.error(f"Error converting company report to PDF: {e}")
        raise HTTPException(status_code=500, detail=f"Could not convert report to PDF: {str(e)}")


@daily_reports_router.get("/sector/{sector}/pdf")
async def download_sector_report_pdf(sector: str, timestamp: str):
    """
    Download the sector daily report as PDF for a specific date.
    
    Args:
        sector: Sector name (e.g., "Technology", "Healthcare")
        timestamp: Date in format YYYY-MM-DD (e.g., "2025-10-31")
    """
    reports_path = get_daily_report_path(sector, timestamp)
    
    if not reports_path.exists():
        raise HTTPException(status_code=404, detail=f"Reports folder not found for {sector} on {timestamp}")
    
    # Find the sector report for this date
    sector_file = None
    for md_file in sorted(reports_path.glob("*.md"), reverse=True):
        if md_file.is_file():
            sector_file = md_file
            break
    
    if not sector_file or not sector_file.exists():
        raise HTTPException(status_code=404, detail=f"Sector report not found for {sector} on {timestamp}")
    
    try:
        # Read markdown content
        with open(sector_file, 'r', encoding='utf-8') as f:
            md_content = f.read()
        
        # Convert to PDF
        pdf_bytes = convert_daily_report_to_pdf(md_content)
        
        # Generate PDF filename
        pdf_filename = f"{sector}_daily_report.pdf"
        
        headers = {
            "Content-Disposition": f'attachment; filename="{pdf_filename}"'
        }
        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type='application/pdf',
            headers=headers
        )
        
    except Exception as e:
        logger.error(f"Error converting sector report to PDF: {e}")
        raise HTTPException(status_code=500, detail=f"Could not convert report to PDF: {str(e)}")

