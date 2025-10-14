"""
Automatic news update manager for stock analysis API.

Provides automatic background news updates for all tickers in the database
and streams updates to connected WebSocket clients.
"""

import asyncio
import logging
from typing import Set, List, Dict, Optional, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass

from .news_manager import NewsManager
from .models import NewsArticle, NewsRequest, NewsStreamUpdate, NewsStreamStatus
from vynn_core.db.mongo import get_db

logger = logging.getLogger(__name__)


@dataclass
class NewsUpdateConfig:
    """Configuration for automatic news updates."""
    update_interval: int = 1800  # 30 minutes instead of 5 minutes
    max_tickers_per_batch: int = 2  # Reduced from 10 to 2 for API limits
    days_back: int = 1  # Only check last 1 day instead of 7
    articles_limit: int = 10  # Reduced from 20 to 10
    timeout_seconds: int = 180
    force_refresh: bool = False
    min_update_interval: int = 3600  # Minimum 1 hour between updates per ticker
    max_api_calls_per_hour: int = 20  # SerpAPI rate limiting


class NewsUpdateManager:
    """
    Manages automatic news updates for all tickers in the database.
    
    Runs background task to refresh news every 5 minutes for all tickers
    that have collections in MongoDB and streams updates to WebSocket clients.
    """
    
    def __init__(self, config: NewsUpdateConfig = None):
        self.config = config or NewsUpdateConfig()
        self.news_manager = NewsManager()
        self.is_running = False
        self._background_task: Optional[asyncio.Task] = None
        self._start_time = datetime.now()
        
        # Subscribers for news updates
        self.subscribers: Dict[str, List[Callable]] = {}  # ticker -> list of callback functions
        self.active_tickers: Set[str] = set()
        
        # Track last update times to avoid excessive backend calls
        self.last_update_times: Dict[str, datetime] = {}
        
        # API call tracking for rate limiting
        self.api_calls_this_hour: List[datetime] = []
        self.total_api_calls: int = 0
        
    async def initialize(self) -> bool:
        """Initialize the news update manager."""
        try:
            # Initialize news manager
            success = await self.news_manager.initialize()
            if not success:
                return False
            
            # Discover existing tickers in database
            await self._discover_tickers()
            
            logger.info(f"✅ NewsUpdateManager initialized with {len(self.active_tickers)} tickers")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize NewsUpdateManager: {e}")
            return False
    
    async def start(self):
        """Start the automatic news update service."""
        if self.is_running:
            logger.warning("⚠️ NewsUpdateManager already running")
            return
        
        self.is_running = True
        self._start_time = datetime.now()
        
        # Start background news update task
        self._background_task = asyncio.create_task(self._news_update_loop())
        logger.info(f"🚀 NewsUpdateManager started - updating news every {self.config.update_interval} seconds")
    
    async def stop(self):
        """Stop the automatic news update service."""
        if not self.is_running:
            return
        
        self.is_running = False
        
        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass
        
        await self.news_manager.close()
        logger.info("🛑 NewsUpdateManager stopped")
    
    async def _discover_tickers(self):
        """Discover all ticker collections in the database."""
        try:
            # Get database instance
            db = get_db()
            
            # List all collections
            collections = db.list_collection_names()
            
            # Filter collections that look like tickers (exclude system collections)
            ticker_collections = []
            for collection in collections:
                # Skip system collections
                if collection.startswith('system.') or collection in ['admin', 'config', 'local']:
                    continue
                    
                # Common stock ticker patterns (3-5 uppercase letters)
                if len(collection) >= 3 and len(collection) <= 5 and collection.isupper():
                    ticker_collections.append(collection)
                elif collection.upper() in ['GOOGL', 'AAPL', 'MSFT', 'AMZN', 'TSLA', 'META', 'NVDA']:
                    # Known major tickers
                    ticker_collections.append(collection.upper())
            
            self.active_tickers = set(ticker_collections)
            logger.info(f"📊 Discovered {len(self.active_tickers)} ticker collections: {list(self.active_tickers)}")
            
        except Exception as e:
            logger.error(f"❌ Error discovering tickers: {e}")
            self.active_tickers = set()
    
    def subscribe_ticker(self, ticker: str, callback: Callable[[NewsStreamUpdate], None]):
        """Subscribe to news updates for a ticker."""
        ticker = ticker.upper()
        
        if ticker not in self.subscribers:
            self.subscribers[ticker] = []
        
        self.subscribers[ticker].append(callback)
        logger.info(f"📰 Subscribed to news for {ticker} - total subscribers: {len(self.subscribers)}")
    
    def unsubscribe_ticker(self, ticker: str, callback: Callable = None):
        """Unsubscribe from news updates for a ticker."""
        ticker = ticker.upper()
        
        if ticker in self.subscribers:
            if callback:
                # Remove specific callback
                if callback in self.subscribers[ticker]:
                    self.subscribers[ticker].remove(callback)
            else:
                # Remove all callbacks
                self.subscribers[ticker].clear()
            
            # Remove ticker if no more callbacks
            if not self.subscribers[ticker]:
                del self.subscribers[ticker]
        
        logger.info(f"📰 Unsubscribed from news for {ticker}")
    
    def get_subscribed_tickers(self) -> List[str]:
        """Get list of tickers with active subscribers."""
        return list(self.subscribers.keys())
    
    def get_active_tickers(self) -> List[str]:
        """Get list of all active tickers in database."""
        return list(self.active_tickers)
    
    async def _news_update_loop(self):
        """Background loop that updates news intelligently."""
        logger.info(f"🔄 Starting news update loop (every {self.config.update_interval} seconds)...")
        
        while self.is_running:
            try:
                # Prioritize subscribed tickers (active WebSocket connections)
                subscribed_tickers = list(self.subscribers.keys())
                
                if subscribed_tickers:
                    logger.info(f"📰 Prioritizing {len(subscribed_tickers)} subscribed tickers: {subscribed_tickers}")
                    
                    # Update subscribed tickers first
                    for i in range(0, len(subscribed_tickers), self.config.max_tickers_per_batch):
                        batch = subscribed_tickers[i:i + self.config.max_tickers_per_batch]
                        await self._update_news_batch(batch)
                        
                        # Delay between batches
                        if i + self.config.max_tickers_per_batch < len(subscribed_tickers):
                            await asyncio.sleep(30)  # 30 second delay
                
                # Only update other tickers if we have API quota remaining
                elif self._can_make_api_call() and self.active_tickers:
                    # Select a few random tickers for background updates
                    import random
                    all_tickers = list(self.active_tickers)
                    random.shuffle(all_tickers)
                    
                    # Only update 1-2 random tickers per cycle when no active subscribers
                    background_tickers = all_tickers[:min(2, len(all_tickers))]
                    
                    logger.info(f"📰 Background update for {len(background_tickers)} random tickers: {background_tickers}")
                    await self._update_news_batch(background_tickers)
                    
                else:
                    logger.info("💤 No subscribed tickers and API quota low, skipping update...")
                
                # Re-discover tickers periodically (every 20 cycles)
                if hasattr(self, '_cycle_count'):
                    self._cycle_count += 1
                else:
                    self._cycle_count = 1
                
                if self._cycle_count % 20 == 0:
                    await self._discover_tickers()
                
                # Wait for next update interval
                await asyncio.sleep(self.config.update_interval)
                
            except asyncio.CancelledError:
                logger.info("🛑 News update loop cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Error in news update loop: {e}")
                await asyncio.sleep(30)  # Short delay before retry
    
    async def _update_news_batch(self, tickers: List[str]):
        """Update news for a batch of tickers."""
        tasks = []
        for ticker in tickers:
            task = asyncio.create_task(self._update_ticker_news(ticker))
            tasks.append(task)
        
        # Run batch updates concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Log results
        for i, result in enumerate(results):
            ticker = tickers[i]
            if isinstance(result, Exception):
                logger.error(f"❌ Error updating news for {ticker}: {result}")
            else:
                logger.debug(f"✅ Updated news for {ticker}")
    
    async def _update_ticker_news(self, ticker: str):
        """Update news for a single ticker with smart rate limiting."""
        try:
            # Rate limiting check - don't exceed API limits
            if not self._can_make_api_call():
                logger.info(f"⏸️ Skipping {ticker} - API rate limit reached")
                return
            
            # Check if we updated this ticker recently
            now = datetime.now()
            last_update = self.last_update_times.get(ticker)
            
            if last_update and (now - last_update).total_seconds() < self.config.min_update_interval:
                time_remaining = self.config.min_update_interval - (now - last_update).total_seconds()
                logger.debug(f"⏭️ Skipping {ticker} - updated recently ({time_remaining/60:.0f} min remaining)")
                return
            
            # Check if ticker already has recent articles
            if await self._has_recent_articles(ticker):
                logger.debug(f"✅ {ticker} already has recent articles, skipping backend call")
                self.last_update_times[ticker] = now  # Update timestamp to avoid repeated checks
                return
            
            logger.info(f"🔄 Updating news for {ticker} (last update: {last_update.strftime('%H:%M') if last_update else 'never'})")
            
            # Create news request
            request = NewsRequest(
                tickers=[ticker],
                limit=self.config.articles_limit,
                days_back=self.config.days_back,
                force_refresh=True  # Force backend call for auto-updates
            )
            
            # Track API call
            self._track_api_call()
            
            # Get news articles
            response = await self.news_manager.get_news_for_tickers(request)
            
            # Update last update time
            self.last_update_times[ticker] = now
            
            # Get articles for this ticker from the response
            ticker_articles = response.articles_by_ticker.get(ticker, [])
            
            logger.info(f"📰 Updated {len(ticker_articles)} articles for {ticker}")
            
            # If we have subscribers for this ticker, notify them
            if ticker in self.subscribers and ticker_articles:
                update = NewsStreamUpdate(
                    type="articles",
                    ticker=ticker,
                    articles=ticker_articles,
                    total_count=len(ticker_articles),
                    cache_hit=ticker in response.cached_tickers,
                    backend_jobs=response.fetched_tickers if ticker in response.fetched_tickers else [],
                    status=NewsStreamStatus.COMPLETED,
                    message=f"Updated {len(ticker_articles)} articles for {ticker}",
                    timestamp=now
                )
                
                # Notify all subscribers for this ticker
                callbacks = self.subscribers.get(ticker, [])
                for callback in callbacks:
                    try:
                        await self._safe_callback(callback, update)
                    except Exception as e:
                        logger.error(f"Error in news update callback for {ticker}: {e}")
            
        except Exception as e:
            logger.error(f"❌ Error updating news for {ticker}: {e}")
    
    def _can_make_api_call(self) -> bool:
        """Check if we can make another API call within rate limits."""
        now = datetime.now()
        
        # Clean up old API call timestamps (older than 1 hour)
        self.api_calls_this_hour = [
            call_time for call_time in self.api_calls_this_hour 
            if (now - call_time).total_seconds() < 3600
        ]
        
        # Check if we're under the hourly limit
        return len(self.api_calls_this_hour) < self.config.max_api_calls_per_hour
    
    def _track_api_call(self):
        """Track an API call for rate limiting."""
        now = datetime.now()
        self.api_calls_this_hour.append(now)
        self.total_api_calls += 1
        
        logger.debug(f"� API calls this hour: {len(self.api_calls_this_hour)}/{self.config.max_api_calls_per_hour}")
    
    async def _has_recent_articles(self, ticker: str) -> bool:
        """Check if ticker already has articles from the last few hours."""
        try:
            # Check for any articles first, then worry about recency
            articles, _ = await self.news_manager.get_articles_for_ticker(
                ticker=ticker,
                limit=self.config.articles_limit,  # Use configured limit instead of hardcoded 5
                days_back=30  # Look back 30 days to see if we have any articles
            )
            
            # If we have articles, consider that "recent enough" for now
            # This prevents excessive backend calls when we already have some data
            if articles:
                logger.debug(f"✅ {ticker} has {len(articles)} existing articles, skipping update")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking recent articles for {ticker}: {e}")
            return False  # If error, proceed with update
    
    async def _safe_callback(self, callback: Callable, update: NewsStreamUpdate):
        """Safely execute callback function."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(update)
            else:
                callback(update)
        except Exception as e:
            logger.error(f"Error in news update callback: {e}")
    
    def get_status(self) -> Dict:
        """Get current status of the news update manager."""
        now = datetime.now()
        
        # Clean up old API call timestamps for accurate count
        self.api_calls_this_hour = [
            call_time for call_time in self.api_calls_this_hour 
            if (now - call_time).total_seconds() < 3600
        ]
        
        return {
            "is_running": self.is_running,
            "start_time": self._start_time.isoformat() if self._start_time else None,
            "active_tickers": list(self.active_tickers),
            "subscribed_tickers": list(self.subscribers.keys()),
            "last_update_times": {
                ticker: time.isoformat() 
                for ticker, time in self.last_update_times.items()
            },
            "api_usage": {
                "calls_this_hour": len(self.api_calls_this_hour),
                "max_calls_per_hour": self.config.max_api_calls_per_hour,
                "total_calls": self.total_api_calls,
                "can_make_call": self._can_make_api_call()
            },
            "config": {
                "update_interval": self.config.update_interval,
                "max_tickers_per_batch": self.config.max_tickers_per_batch,
                "days_back": self.config.days_back,
                "articles_limit": self.config.articles_limit,
                "min_update_interval": self.config.min_update_interval
            }
        }