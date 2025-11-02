"""
Daily Reports Models
Pydantic models for daily reports API requests and responses
"""

from typing import Optional, List
from pydantic import BaseModel, Field


class DailyReportRequest(BaseModel):
    """Request model for manual report generation (deprecated)"""
    timestamp: str = Field(..., description="Date in format YYYY-MM-DD (e.g., '2025-10-31')")


class CompanyDailyReportRequest(BaseModel):
    """Request model for company daily report generation"""
    tickers: List[str] = Field(..., description="List of company ticker symbols (e.g., ['AAPL', 'MSFT', 'GOOGL'])")
    timestamp: str = Field(..., description="Date in format YYYY-MM-DD (e.g., '2025-10-31')")


class SectorDailyReportRequest(BaseModel):
    """Request model for sector daily report generation"""
    sectors: List[str] = Field(..., description="List of sector names (e.g., ['Technology', 'Healthcare', 'Finance'])")
    timestamp: str = Field(..., description="Date in format YYYY-MM-DD (e.g., '2025-10-31')")


class DailyReportJob(BaseModel):
    """Response model for job status"""
    job_id: str
    status: str
    created_at: str
    progress: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    ticker: Optional[str] = None
    report_type: Optional[str] = None  # "company" or "sector"


class BatchJobResponse(BaseModel):
    """Response model for batch job submission"""
    batch_id: str
    total_jobs: int
    job_ids: List[str]
    status: str
    created_at: str
    tickers_or_sectors: List[str]
    report_type: str  # "company" or "sector"


class DailyReportContent(BaseModel):
    """Response model for markdown content"""
    filename: str
    content: str
    type: str  # "company" or "sector"
    ticker: Optional[str] = None


class MultiReportResponse(BaseModel):
    """Response model for multiple reports"""
    reports: List[DailyReportContent]
    total: int
    report_type: str  # "company" or "sector"


class GetCompanyReportsRequest(BaseModel):
    """Request model for retrieving multiple company reports"""
    tickers: List[str] = Field(..., description="List of company ticker symbols (e.g., ['AAPL', 'MSFT', 'GOOGL'])")
    timestamp: str = Field(..., description="Date in format YYYY-MM-DD (e.g., '2025-10-31')")


class GetSectorReportsRequest(BaseModel):
    """Request model for retrieving multiple sector reports"""
    sectors: List[str] = Field(..., description="List of sector names (e.g., ['Technology', 'Healthcare', 'Finance'])")
    timestamp: str = Field(..., description="Date in format YYYY-MM-DD (e.g., '2025-10-31')")

