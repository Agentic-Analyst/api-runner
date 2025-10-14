"""
News Feed Models for Stock Analysis API

Pydantic models for news feed requests, responses, and data structures.
"""
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class NewsArticle(BaseModel):
    """Individual news article model - matches exactly what's stored in database"""
    id: Optional[str] = Field(None, description="Article ObjectId from database")
    urlHash: str = Field(..., description="URL hash identifier")
    company: str = Field(..., description="Company name")
    content: str = Field(..., description="Article content/summary")
    createdAt: datetime = Field(..., description="When article was created in database")
    publish_date: str = Field(..., description="Publication date as string (e.g. '5 days ago')")
    scraped_at: str = Field(..., description="When article was scraped (datetime string)")
    search_category: str = Field(..., description="Search category used")
    serpapi_authors: List[str] = Field(default_factory=list, description="Authors from SerpAPI (can be empty)")
    serpapi_snippet: str = Field(..., description="Snippet from SerpAPI")
    serpapi_source: str = Field(..., description="Source from SerpAPI (can be empty)")
    serpapi_source_icon: str = Field(..., description="Source icon from SerpAPI (can be empty)")
    serpapi_thumbnail: str = Field(..., description="Thumbnail URL from SerpAPI")
    source_url: str = Field(..., description="Original source URL")
    ticker: str = Field(..., description="Related stock ticker")
    title: str = Field(..., description="Article title")
    updatedAt: datetime = Field(..., description="When article was last updated in database")
    url: str = Field(..., description="Article URL")
    word_count: str = Field(..., description="Word count as string")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class NewsRequest(BaseModel):
    """Request model for fetching news"""
    tickers: List[str] = Field(..., description="List of stock tickers to fetch news for")
    limit: Optional[int] = Field(default=20, description="Maximum number of articles per ticker")
    days_back: Optional[int] = Field(default=7, description="Number of days to look back for news")
    force_refresh: Optional[bool] = Field(default=False, description="Force fetch from backend even if cache exists")


class NewsResponse(BaseModel):
    """Response model for news feed"""
    status: str = Field(..., description="Response status (success/partial/error)")
    message: str = Field(..., description="Response message")
    total_articles: int = Field(..., description="Total number of articles returned")
    articles_by_ticker: Dict[str, List[NewsArticle]] = Field(..., description="Articles grouped by ticker")
    cached_tickers: List[str] = Field(..., description="Tickers that were served from cache")
    fetched_tickers: List[str] = Field(..., description="Tickers that required backend fetch")
    failed_tickers: List[str] = Field(..., description="Tickers that failed to fetch")
    timestamp: datetime = Field(default_factory=datetime.now, description="Response timestamp")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class NewsFeedStatus(BaseModel):
    """Status model for news feed operations"""
    ticker: str = Field(..., description="Stock ticker")
    status: str = Field(..., description="Operation status")
    article_count: int = Field(..., description="Number of articles found/fetched")
    last_updated: Optional[datetime] = Field(None, description="When articles were last updated")
    source: str = Field(..., description="Data source (cache/backend)")
    error: Optional[str] = Field(None, description="Error message if any")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class NewsSearchJob(BaseModel):
    """Model for backend news search job"""
    job_id: str = Field(..., description="Unique job identifier")
    ticker: str = Field(..., description="Stock ticker")
    company: str = Field(..., description="Company name")
    status: str = Field(..., description="Job status")
    progress: Optional[str] = Field(None, description="Job progress description")
    created_at: datetime = Field(default_factory=datetime.now, description="Job creation time")
    completed_at: Optional[datetime] = Field(None, description="Job completion time")
    error: Optional[str] = Field(None, description="Error message if failed")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# WebSocket Models for News Feed Streaming

class NewsSubscription(BaseModel):
    """Model for news WebSocket subscription requests"""
    tickers: List[str] = Field(..., description="List of stock tickers to subscribe to")
    limit: Optional[int] = Field(default=20, description="Maximum articles per ticker")
    days_back: Optional[int] = Field(default=7, description="Days to look back for news")
    force_refresh: Optional[bool] = Field(default=False, description="Force backend refresh")
    user_id: Optional[str] = Field(None, description="Optional user identifier")


class NewsWebSocketMessage(BaseModel):
    """Base model for news WebSocket messages"""
    type: str = Field(..., description="Message type")
    data: Dict[str, Any] = Field(..., description="Message payload")
    timestamp: datetime = Field(default_factory=datetime.now, description="Message timestamp")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class NewsStreamUpdate(BaseModel):
    """Model for streaming news updates via WebSocket"""
    ticker: str = Field(..., description="Related stock ticker")
    article: NewsArticle = Field(..., description="News article data")
    source: str = Field(..., description="Data source (cache/fresh)")
    batch_info: Dict[str, Any] = Field(..., description="Batch processing information")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class NewsStreamStatus(BaseModel):
    """Model for news stream status updates"""
    ticker: str = Field(..., description="Stock ticker")
    status: str = Field(..., description="Processing status")
    message: str = Field(..., description="Status message")
    articles_found: int = Field(default=0, description="Number of articles found")
    progress: Optional[str] = Field(None, description="Progress description")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }