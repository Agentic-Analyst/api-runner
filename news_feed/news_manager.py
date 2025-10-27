"""
News Manager for Stock Analysis API

Manages news feed operations including database queries and backend job coordination.
"""
import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import docker
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import DESCENDING

# Import vynn_core components
from vynn_core.dao.articles import find_recent, get_article_by_url, get_articles_by_ids, upsert_articles
from vynn_core.db.mongo import get_mongo_client, get_db, init_indexes

from .models import NewsArticle, NewsRequest, NewsResponse, NewsFeedStatus, NewsSearchJob

logger = logging.getLogger(__name__)


class NewsManager:
    """
    Manages news feed operations for the stock analysis API.
    
    Responsibilities:
    - Query MongoDB for existing news articles using vynn_core
    - Trigger backend "search-news" jobs for missing data
    - Convert between vynn_core Article model and API NewsArticle model
    - Coordinate between cache and fresh data fetching
    """
    
    def __init__(self):
        self.mongo_client: Optional[AsyncIOMotorClient] = None
        self.database = None
        self.docker_client = None
        self.backend_image = os.getenv("BACKEND_IMAGE")
        self.data_volume = os.getenv("DATA_VOLUME")
        self.news_jobs: Dict[str, NewsSearchJob] = {}
        
        # API keys for backend jobs
        self.serpapi_key = os.getenv("SERPAPI_API_KEY")
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        self.MONGO_URI = os.getenv("MONGO_URI")
        self.MONGO_DB = os.getenv("MONGO_DB")
    
    async def initialize(self) -> bool:
        """Initialize MongoDB connection and vynn_core components"""
        try:
            # Get MongoDB client through vynn_core
            self.mongo_client = get_mongo_client()
            self.database = get_db()
            
            # Initialize indexes for better performance
            init_indexes()
            
            # Initialize Docker client for backend jobs
            try:
                self.docker_client = docker.from_env()
                self.docker_client.ping()
                logger.info("✅ Docker client connected for news feed")
            except Exception as e:
                logger.warning(f"⚠️ Docker not available for news backend jobs: {e}")
                self.docker_client = None
            
            logger.info("✅ NewsManager initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize NewsManager: {e}")
            return False
    
    async def close(self):
        """Close database connections"""
        if self.mongo_client:
            self.mongo_client.close()
            logger.info("✅ NewsManager connections closed")
    
    def _db_document_to_news_article(self, doc: dict, ticker: str) -> NewsArticle:
        """Convert database document to NewsArticle model using exact database fields."""
        from datetime import datetime
        
        # Parse datetime fields
        created_at = doc.get('createdAt')
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            except:
                created_at = datetime.now()
        
        updated_at = doc.get('updatedAt')
        if isinstance(updated_at, str):
            try:
                updated_at = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
            except:
                updated_at = datetime.now()
        
        return NewsArticle(
            id=str(doc.get('_id', '')),
            urlHash=doc.get('urlHash', ''),
            company=doc.get('company', ''),
            content=doc.get('content', ''),
            createdAt=created_at or datetime.now(),
            publish_date=doc.get('publish_date', ''),
            scraped_at=doc.get('scraped_at', ''),
            search_category=doc.get('search_category', ''),
            serpapi_authors=doc.get('serpapi_authors', []),
            serpapi_snippet=doc.get('serpapi_snippet', ''),
            serpapi_source=doc.get('serpapi_source', ''),
            serpapi_source_icon=doc.get('serpapi_source_icon', ''),
            serpapi_thumbnail=doc.get('serpapi_thumbnail', ''),
            source_url=doc.get('source_url', ''),
            ticker=doc.get('ticker', ticker),
            title=doc.get('title', ''),
            updatedAt=updated_at or datetime.now(),
            url=doc.get('url', ''),
            word_count=doc.get('word_count', '0')
        )
    
    async def get_articles_for_ticker(
        self, 
        ticker: str, 
        limit: int = 20, 
        days_back: int = 7
    ) -> Tuple[List[NewsArticle], bool]:
        """
        Get articles for a specific ticker from MongoDB.
        
        Returns:
            Tuple of (articles, cache_hit) where cache_hit indicates if data was found
        """
        if self.database is None:
            logger.error("Database not initialized")
            return [], False
        
        try:
            # Calculate date range
            cutoff_date = datetime.now() - timedelta(days=days_back)
            
            # In vynn_core, collections are typically named after tickers
            collection_name = ticker.upper()
            
            # Use vynn_core's find_recent function
            # Note: We need to check the actual function signature of find_recent
            try:
                # Get articles using vynn_core function
                # Since find_recent might not take collection name, we'll query directly
                collection = self.database[collection_name]
                
                # Build query filters - use correct field names from database
                # Since articles might be older, let's use a more flexible date range
                filters = {}
                
                # For string-based publish_date fields, we need a different approach
                # Since dates are stored as strings like "Oct 28, 2024", we can't use $gte with datetime
                # Instead, we'll fetch all articles and filter them in Python
                
                # Query without date filter first, then filter in Python
                cursor = collection.find(filters).sort("createdAt", DESCENDING).limit(min(limit * 3, 100))  # Get more to filter
                all_articles_data = list(cursor)
                
                # Filter articles by date in Python since publish_date is stored as strings
                articles_data = []
                if days_back < 30:
                    # Use a slightly stricter cutoff - exclude anything exactly at the boundary
                    cutoff_date = datetime.now() - timedelta(days=days_back, hours=-1)  # Add 1 hour buffer
                    
                    for article_data in all_articles_data:
                        # Try to parse the string date
                        article_date = None
                        publish_date_str = article_data.get('publish_date', '')
                        
                        # Try different date parsing approaches
                        if publish_date_str:
                            try:
                                # Handle formats like "Oct 28, 2024"
                                article_date = datetime.strptime(publish_date_str, "%b %d, %Y")
                            except:
                                try:
                                    # Handle formats like "5 days ago", "1 week ago", "2 weeks ago"
                                    if "ago" in publish_date_str:
                                        parts = publish_date_str.split()
                                        if len(parts) >= 3:
                                            number = int(parts[0])
                                            unit = parts[1].lower()
                                            
                                            if "day" in unit:
                                                article_date = datetime.now() - timedelta(days=number)
                                            elif "week" in unit:
                                                article_date = datetime.now() - timedelta(weeks=number)
                                            elif "month" in unit:
                                                article_date = datetime.now() - timedelta(days=number * 30)
                                            elif "hour" in unit:
                                                article_date = datetime.now() - timedelta(hours=number)
                                except:
                                    # If we can't parse the date, check createdAt as fallback
                                    created_at = article_data.get('createdAt')
                                    if created_at:
                                        try:
                                            if isinstance(created_at, str):
                                                article_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                                            else:
                                                article_date = created_at
                                        except:
                                            pass
                        
                        # Include article only if we successfully parsed the date AND it's within range
                        if article_date is not None and article_date >= cutoff_date:
                            articles_data.append(article_data)
                            
                        # Stop when we have enough articles
                        if len(articles_data) >= limit:
                            break
                else:
                    # No date filtering - use all articles up to limit
                    articles_data = all_articles_data[:limit]
                
                # If no articles found with date filter, try without date filter but limit more strictly
                if not articles_data and days_back < 30:
                    logger.info(f"No recent articles for {ticker}, using all available articles...")
                    articles_data = all_articles_data[:limit]
                
                # Debug: Check total articles in collection  
                total_in_collection = collection.count_documents({})
                cutoff_date_str = f"last {days_back} days" if days_back < 30 else "no filter"
                
                logger.info(f"Database debug for {ticker}: "
                           f"total_articles={total_in_collection}, "
                           f"all_fetched={len(all_articles_data)}, "
                           f"date_filter={cutoff_date_str}, "
                           f"filtered_articles={len(articles_data)}")
                
                # Log sample dates for debugging
                if articles_data and len(articles_data) > 0:
                    sample_dates = []
                    for i, article in enumerate(articles_data[:3]):
                        publish_date = article.get('publish_date', 'N/A')
                        created_at = article.get('createdAt', 'N/A') 
                        sample_dates.append(f"[{i+1}] publish_date: {publish_date}, createdAt: {created_at}")
                    logger.info(f"Sample dates for {ticker}: {'; '.join(sample_dates)}")
                
                # Convert to Article objects then to NewsArticle
                articles = []
                for article_data in articles_data:
                    try:
                        # Convert database document to NewsArticle directly
                        news_article = self._db_document_to_news_article(article_data, ticker)
                        articles.append(news_article)
                    except Exception as e:
                        logger.warning(f"Failed to convert article for {ticker}: {e}")
                        continue
                
                cache_hit = len(articles) > 0
                logger.info(f"Found {len(articles)} articles for {ticker} (cache_hit: {cache_hit})")
                
                return articles, cache_hit
                
            except Exception as e:
                logger.warning(f"Error using collection {collection_name}: {e}")
                return [], False
            
        except Exception as e:
            logger.error(f"Error querying articles for {ticker}: {e}")
            return [], False
    
    async def fetch_news_from_backend(
        self, 
        ticker: str, 
        company: str,
        email: str = "system@newsapi.com"
    ) -> NewsSearchJob:
        """
        Trigger backend "search-news" pipeline for a ticker.
        
        This runs the stock-analyst backend container with search-news pipeline
        to scrape and store news articles in MongoDB.
        """
        if not self.docker_client:
            raise Exception("Docker client not available for backend jobs")
        
        # Generate job ID
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        job_id = f"news_{ticker}_{timestamp}"
        
        # Create job record
        job = NewsSearchJob(
            job_id=job_id,
            ticker=ticker,
            company=company,
            status="pending"
        )
        self.news_jobs[job_id] = job
        
        try:
            # Environment variables for backend container
            env_vars = {
                "SERPAPI_API_KEY": self.serpapi_key,
                "OPENAI_API_KEY": self.openai_key,
                "ANTHROPIC_API_KEY": self.anthropic_key,
                "MONGO_URI": self.MONGO_URI,
                "MONGO_DB": self.MONGO_DB,
                "DATA_PATH": "/data",
            }
            
            # Command to run search-news pipeline
            cmd = [
                "--ticker", ticker,
                "--company", company,
                "--email", email,
                "--timestamp", datetime.now().isoformat(),
                "--pipeline", "search-news",
                "--max-searched", "10"
            ]
            
            logger.info(f"🚀 Starting news search for {ticker} with command: {cmd}")
            logger.debug(f"Environment variables: {env_vars}")
            logger.debug(f"Data volume: {self.data_volume}")
            logger.debug(f"Backend image: {self.backend_image}")
            
            job.status = "running"
            job.progress = f"Searching news for {ticker}..."
            
            # Run backend container
            container = self.docker_client.containers.run(
                self.backend_image,
                command=cmd,
                environment=env_vars,
                volumes={self.data_volume: {'bind': '/data', 'mode': 'rw'}},
                detach=True,
                remove=True,  # Auto-remove when done
                name=f"news-search-{job_id}"
            )
            
            # Start monitoring in background
            asyncio.create_task(self._monitor_news_job_async(job_id, container.id))
            
            logger.info(f"✅ News search job {job_id} started for {ticker}")
            return job
            
        except Exception as e:
            logger.error(f"❌ Failed to start news search for {ticker}: {e}")
            job.status = "failed"
            job.error = str(e)
            job.completed_at = datetime.now()
            raise
    
    async def _monitor_news_job_async(self, job_id: str, container_id: str):
        """Monitor a news search job container asynchronously"""
        try:
            job = self.news_jobs.get(job_id)
            if not job:
                return
            
            # Poll container status instead of waiting (since container auto-removes)
            timeout_seconds = 600  # 10 minute timeout
            poll_interval = 2  # Poll every 2 seconds
            elapsed = 0
            
            while elapsed < timeout_seconds:
                try:
                    # Try to get container - if it doesn't exist, it completed
                    container = self.docker_client.containers.get(container_id)
                    status = container.status
                    
                    if status == 'exited':
                        # Container finished, get exit code
                        exit_code = container.attrs['State']['ExitCode']
                        
                        if exit_code == 0:
                            job.status = "completed"
                            job.progress = "News search completed successfully"
                            logger.info(f"✅ News search job {job_id} completed successfully")
                        else:
                            job.status = "failed"
                            job.error = f"Container exited with code {exit_code}"
                            logger.error(f"❌ News search job {job_id} failed with exit code {exit_code}")
                        
                        job.completed_at = datetime.now()
                        return
                        
                except Exception as e:
                    # Container not found - likely auto-removed after completion
                    if "No such container" in str(e):
                        # Assume successful completion if container was auto-removed
                        job.status = "completed"
                        job.progress = "News search completed (container auto-removed)"
                        job.completed_at = datetime.now()
                        logger.info(f"✅ News search job {job_id} completed (container auto-removed)")
                        return
                    else:
                        # Other error, continue polling
                        logger.debug(f"Container polling error for {job_id}: {e}")
                        pass
                
                # Sleep before next poll
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval
            
            # Timeout reached
            job.status = "failed"
            job.error = f"News search job timed out after {timeout_seconds} seconds"
            job.completed_at = datetime.now()
            logger.error(f"❌ News search job {job_id} timed out")
            
        except Exception as e:
            logger.error(f"❌ Error monitoring news job {job_id}: {e}")
            if job_id in self.news_jobs:
                self.news_jobs[job_id].status = "failed"
                self.news_jobs[job_id].error = str(e)
                self.news_jobs[job_id].completed_at = datetime.now()
    
    async def _monitor_news_job(self, job_id: str, container):
        """Monitor a news search job container (DEPRECATED - kept for compatibility)"""
        # This method is deprecated, use _monitor_news_job_async instead
        await self._monitor_news_job_async(job_id, container.id)
    
    async def get_news_for_tickers(self, request: NewsRequest) -> NewsResponse:
        """
        Main method to get news for multiple tickers.
        
        Workflow:
        1. Check database for existing articles for each ticker
        2. If sufficient articles found, return from cache
        3. If not, trigger backend search-news job
        4. Wait for job completion and return results
        """
        articles_by_ticker = {}
        cached_tickers = []
        fetched_tickers = []
        failed_tickers = []
        total_articles = 0
        
        for ticker in request.tickers:
            ticker = ticker.upper().strip()
            
            try:
                # First, try to get from cache
                articles, cache_hit = await self.get_articles_for_ticker(
                    ticker=ticker,
                    limit=request.limit,
                    days_back=request.days_back
                )
                
                # Decide whether to use cache or fetch fresh data
                use_cache = cache_hit and not request.force_refresh and len(articles) >= (request.limit // 2)
                
                if use_cache:
                    # Use cached articles
                    articles_by_ticker[ticker] = articles
                    cached_tickers.append(ticker)
                    total_articles += len(articles)
                    logger.info(f"✅ Using cached articles for {ticker}: {len(articles)} articles")
                    
                else:
                    # Need to fetch from backend
                    try:
                        # For company name, we'll use a simple heuristic or lookup
                        # In a real system, you might have a ticker-to-company mapping
                        company = ticker  # Fallback to ticker if no mapping available
                        
                        # Trigger backend search
                        job = await self.fetch_news_from_backend(
                            ticker=ticker,
                            company=company,
                            email="system@newsapi.com"
                        )
                        
                        # Wait for job completion (with timeout)
                        timeout_seconds = 300  # 5 minutes
                        start_time = datetime.now()
                        
                        while (datetime.now() - start_time).seconds < timeout_seconds:
                            await asyncio.sleep(2)  # Check every 2 seconds
                            
                            current_job = self.news_jobs.get(job.job_id)
                            if not current_job:
                                break
                                
                            if current_job.status in ["completed", "failed"]:
                                break
                        
                        # Check final status
                        final_job = self.news_jobs.get(job.job_id)
                        if final_job and final_job.status == "completed":
                            # Small delay to ensure data persistence
                            await asyncio.sleep(5)
                            
                            # Debug: Check what's in database immediately after job completion
                            collection = self.database[ticker]
                            total_docs_before = collection.count_documents({})
                            recent_docs_before = collection.count_documents({"published_at": {"$gte": datetime.now() - timedelta(hours=1)}})
                            
                            logger.info(f"🔍 Post-job debug for {ticker}: total_docs={total_docs_before}, recent_docs_1hr={recent_docs_before}")
                            
                            # Try different field names that backend might be using
                            alt_fields_to_check = [
                                {"publish_date": {"$gte": datetime.now() - timedelta(days=request.days_back)}},
                                {"published_at": {"$gte": datetime.now() - timedelta(days=request.days_back)}},
                                {"createdAt": {"$gte": datetime.now() - timedelta(days=request.days_back)}},
                                {"created_at": {"$gte": datetime.now() - timedelta(days=request.days_back)}},
                                {"date": {"$gte": datetime.now() - timedelta(days=request.days_back)}},
                                {"timestamp": {"$gte": datetime.now() - timedelta(days=request.days_back)}}
                            ]
                            
                            for alt_filter in alt_fields_to_check:
                                alt_count = collection.count_documents(alt_filter)
                                if alt_count > 0:
                                    logger.info(f"🔍 Found {alt_count} articles using filter: {alt_filter}")
                            
                            # Sample a few documents to see their structure
                            sample_docs = list(collection.find({}).limit(2))
                            for i, doc in enumerate(sample_docs):
                                doc_fields = list(doc.keys())
                                logger.info(f"🔍 Sample doc {i+1} fields: {doc_fields}")
                                if 'publish_date' in doc:
                                    logger.info(f"🔍 Sample doc {i+1} publish_date: {doc['publish_date']}")
                                elif 'published_at' in doc:
                                    logger.info(f"🔍 Sample doc {i+1} published_at: {doc['published_at']}")
                                elif 'createdAt' in doc:
                                    logger.info(f"🔍 Sample doc {i+1} createdAt: {doc['createdAt']}")
                            
                            # Fetch articles from database after backend job
                            fresh_articles, _ = await self.get_articles_for_ticker(
                                ticker=ticker,
                                limit=request.limit,
                                days_back=request.days_back
                            )
                            
                            articles_by_ticker[ticker] = fresh_articles
                            fetched_tickers.append(ticker)
                            total_articles += len(fresh_articles)
                            logger.info(f"✅ Fetched fresh articles for {ticker}: {len(fresh_articles)} articles")
                            
                        else:
                            # Job failed or timed out
                            failed_tickers.append(ticker)
                            articles_by_ticker[ticker] = []
                            error_msg = final_job.error if final_job else "Job timeout"
                            logger.error(f"❌ Failed to fetch articles for {ticker}: {error_msg}")
                    
                    except Exception as e:
                        failed_tickers.append(ticker)
                        articles_by_ticker[ticker] = []
                        logger.error(f"❌ Error fetching articles for {ticker}: {e}")
            
            except Exception as e:
                failed_tickers.append(ticker)
                articles_by_ticker[ticker] = []
                logger.error(f"❌ Error processing ticker {ticker}: {e}")
        
        # Determine overall status
        if failed_tickers and not cached_tickers and not fetched_tickers:
            status = "error"
            message = f"Failed to fetch news for all tickers: {', '.join(failed_tickers)}"
        elif failed_tickers:
            status = "partial"
            message = f"Successfully fetched news for some tickers. Failed: {', '.join(failed_tickers)}"
        else:
            status = "success"
            message = f"Successfully fetched news for all {len(request.tickers)} tickers"
        
        return NewsResponse(
            status=status,
            message=message,
            total_articles=total_articles,
            articles_by_ticker=articles_by_ticker,
            cached_tickers=cached_tickers,
            fetched_tickers=fetched_tickers,
            failed_tickers=failed_tickers,
            timestamp=datetime.now()
        )
    
    def get_job_status(self, job_id: str) -> Optional[NewsSearchJob]:
        """Get status of a news search job"""
        return self.news_jobs.get(job_id)
    
    def list_active_jobs(self) -> List[NewsSearchJob]:
        """List all active (non-completed) news search jobs"""
        return [
            job for job in self.news_jobs.values()
            if job.status in ["pending", "running"]
        ]