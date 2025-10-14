"""
News Feed API Routes for Stock Analysis API

FastAPI routes for news feed operations including WebSocket streaming.
"""
import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends, WebSocket
from fastapi.responses import JSONResponse

from .models import NewsRequest, NewsResponse, NewsFeedStatus, NewsSearchJob
from .news_manager import NewsManager
from .news_websocket import get_news_websocket_manager

logger = logging.getLogger(__name__)

# Global news manager instance
news_manager: Optional[NewsManager] = None

def get_news_manager() -> NewsManager:
    """Dependency to get the news manager instance"""
    if not news_manager:
        raise HTTPException(status_code=503, detail="News manager not initialized")
    return news_manager

# Create router
news_router = APIRouter(prefix="/api/news", tags=["news"])

@news_router.post("/feed", response_model=NewsResponse)
async def get_news_feed(
    request: NewsRequest,
    manager: NewsManager = Depends(get_news_manager)
):
    """
    Get news feed for specified tickers (REST endpoint for simple integrations).
    
    Note: For real-time streaming and live updates, use the WebSocket endpoint at /api/news/ws
    This REST endpoint is for one-off requests and simple integrations that don't need streaming.
    """
    try:
        logger.info(f"📰 REST news feed request for tickers: {request.tickers}")
        
        # Validate request
        if not request.tickers:
            raise HTTPException(status_code=400, detail="At least one ticker is required")
        
        if len(request.tickers) > 20:  # Reasonable limit
            raise HTTPException(status_code=400, detail="Maximum 20 tickers allowed per request")
        
        # Process news feed request
        response = await manager.get_news_for_tickers(request)
        
        logger.info(f"✅ REST news feed response: {response.status} - {response.total_articles} articles")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error processing REST news feed request: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@news_router.get("/status/{ticker}")
async def get_ticker_news_status(
    ticker: str,
    manager: NewsManager = Depends(get_news_manager)
):
    """Get news status for a specific ticker"""
    try:
        ticker = ticker.upper().strip()
        
        # Get articles to check status
        articles, cache_hit = await manager.get_articles_for_ticker(ticker, limit=1)
        
        status = NewsFeedStatus(
            ticker=ticker,
            status="available" if cache_hit else "no_data",
            article_count=len(articles),
            last_updated=articles[0].createdAt if articles else None,
            source="cache" if cache_hit else "none"
        )
        
        return status
        
    except Exception as e:
        logger.error(f"❌ Error getting status for {ticker}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting ticker status: {str(e)}")

@news_router.get("/jobs")
async def list_news_jobs(
    manager: NewsManager = Depends(get_news_manager)
):
    """List all active news search jobs"""
    try:
        active_jobs = manager.list_active_jobs()
        return {
            "active_jobs": active_jobs,
            "total_active": len(active_jobs)
        }
    except Exception as e:
        logger.error(f"❌ Error listing news jobs: {e}")
        raise HTTPException(status_code=500, detail=f"Error listing jobs: {str(e)}")

@news_router.get("/jobs/{job_id}")
async def get_news_job_status(
    job_id: str,
    manager: NewsManager = Depends(get_news_manager)
):
    """Get status of a specific news search job"""
    try:
        job = manager.get_job_status(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error getting job status for {job_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting job status: {str(e)}")

@news_router.post("/refresh/{ticker}")
async def refresh_ticker_news(
    ticker: str,
    background_tasks: BackgroundTasks,
    company: Optional[str] = None,
    manager: NewsManager = Depends(get_news_manager)
):
    """
    Force refresh news for a specific ticker (REST endpoint for admin/testing).
    
    Note: For real-time refresh operations, use the WebSocket "refresh" message type.
    This REST endpoint is primarily for administrative use and testing.
    """
    try:
        ticker = ticker.upper().strip()
        company = company or ticker  # Use ticker as company name if not provided
        
        # Trigger backend search job
        job = await manager.fetch_news_from_backend(
            ticker=ticker,
            company=company,
            email="manual@newsapi.com"
        )
        
        return {
            "message": f"News refresh started for {ticker}",
            "job_id": job.job_id,
            "status": job.status
        }
        
    except Exception as e:
        logger.error(f"❌ Error refreshing news for {ticker}: {e}")
        raise HTTPException(status_code=500, detail=f"Error refreshing news: {str(e)}")

@news_router.get("/debug/{ticker}")
async def debug_ticker_data(
    ticker: str,
    manager: NewsManager = Depends(get_news_manager)
):
    """Debug endpoint to check ticker data in database"""
    try:
        ticker = ticker.upper().strip()
        
        # Get database collection
        collection = manager.database[ticker]
        
        # Count total documents
        total_docs = collection.count_documents({})
        
        # Get sample documents (convert ObjectId to string)
        sample_docs = []
        for doc in collection.find({}).limit(3):
            # Convert ObjectId to string for JSON serialization
            if '_id' in doc:
                doc['_id'] = str(doc['_id'])
            # Convert datetime objects to strings
            for key, value in doc.items():
                if hasattr(value, 'isoformat'):
                    doc[key] = value.isoformat()
            sample_docs.append(doc)
        
        # Check recent documents with different field names
        from datetime import datetime, timedelta
        cutoff_date = datetime.now() - timedelta(days=7)
        
        field_checks = {
            "publish_date": collection.count_documents({"publish_date": {"$gte": cutoff_date}}),
            "published_at": collection.count_documents({"published_at": {"$gte": cutoff_date}}),
            "createdAt": collection.count_documents({"createdAt": {"$gte": cutoff_date}}),
            "created_at": collection.count_documents({"created_at": {"$gte": cutoff_date}}),
            "date": collection.count_documents({"date": {"$gte": cutoff_date}}),
            "timestamp": collection.count_documents({"timestamp": {"$gte": cutoff_date}})
        }
        
        # Get field names from sample documents
        all_fields = set()
        for doc in sample_docs:
            all_fields.update(doc.keys())
        
        return {
            "ticker": ticker,
            "total_documents": total_docs,
            "field_counts_7days": field_checks,
            "all_fields_found": list(all_fields),
            "sample_documents": sample_docs,
            "cutoff_date": cutoff_date.isoformat(),
            "collection_name": ticker
        }
        
    except Exception as e:
        logger.error(f"❌ Error debugging ticker {ticker}: {e}")
        return {
            "ticker": ticker,
            "error": str(e),
            "total_documents": 0
        }

@news_router.get("/health")
async def news_health_check(
    manager: NewsManager = Depends(get_news_manager)
):
    """Health check for news feed system"""
    try:
        # Basic health check
        health_status = {
            "news_manager": "healthy",
            "mongodb": "connected" if manager.mongo_client else "disconnected",
            "docker": "available" if manager.docker_client else "unavailable",
            "backend_image": manager.backend_image,
            "active_jobs": len(manager.list_active_jobs())
        }
        
        return health_status
        
    except Exception as e:
        logger.error(f"❌ News health check failed: {e}")
        return {
            "news_manager": "unhealthy",
            "error": str(e)
        }

# Initialize function to be called from main.py
async def init_news_manager() -> bool:
    """Initialize the global news manager"""
    global news_manager
    try:
        news_manager = NewsManager()
        success = await news_manager.initialize()
        if success:
            # Initialize WebSocket manager
            from .news_websocket import init_news_websocket_manager
            init_news_websocket_manager(news_manager)
            logger.info("✅ News feed system initialized successfully")
        else:
            logger.error("❌ Failed to initialize news feed system")
        return success
    except Exception as e:
        logger.error(f"❌ Error initializing news manager: {e}")
        return False

async def close_news_manager():
    """Close the global news manager"""
    global news_manager
    if news_manager:
        await news_manager.close()
        news_manager = None
        logger.info("✅ News feed system closed")

# WebSocket endpoint for real-time news streaming
@news_router.websocket("/ws")
async def news_websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time news feed streaming."""
    ws_manager = get_news_websocket_manager()
    if ws_manager is None:
        await websocket.close(code=1000, reason="News WebSocket service not available")
        return
    
    await ws_manager.handle_websocket_connection(websocket)