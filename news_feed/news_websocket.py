"""
WebSocket manager for real-time news feed streaming.

Provides WebSocket endpoints for streaming news articles in real-time,
including cache lookup and backend job coordination.
"""

import asyncio
import json
import logging
from typing import Dict, Set, List, Optional, Any
from datetime import datetime
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from .models import (
    NewsSubscription, NewsWebSocketMessage, NewsStreamUpdate, 
    NewsStreamStatus, NewsArticle
)
from .news_manager import NewsManager

logger = logging.getLogger(__name__)


class NewsWebSocketConnection:
    """Represents a single news WebSocket connection with subscription management."""
    
    def __init__(self, websocket: WebSocket, connection_id: str):
        self.websocket = websocket
        self.connection_id = connection_id
        self.subscribed_tickers: Set[str] = set()
        self.user_id: Optional[str] = None
        self.connected_at = datetime.now()
        self.last_ping = datetime.now()
        self._is_dead = False  # Track if connection is dead to prevent cascading errors
        self._streaming_task: Optional[asyncio.Task] = None  # Track active streaming task
        self.subscription_settings = {
            "limit": 20,
            "days_back": 7,
            "force_refresh": False
        }
    
    async def send_message(self, message: dict):
        """Send JSON message to client with proper state checking."""
        try:
            # Check if WebSocket is still connected
            if (hasattr(self.websocket, 'client_state') and 
                self.websocket.client_state == WebSocketState.CONNECTED):
                await self.websocket.send_text(json.dumps(message, default=str))
            else:
                logger.debug(f"News connection {self.connection_id} not connected, skipping message send")
        except Exception as e:
            # Don't re-raise exceptions for closed connections to prevent cascading errors
            logger.debug(f"News connection {self.connection_id} failed to send message: {e}")
            # Mark connection as dead to prevent further attempts
            if hasattr(self, '_is_dead'):
                self._is_dead = True
    
    async def send_news_update(self, update: NewsStreamUpdate):
        """Send news update to client."""
        if hasattr(self, '_is_dead') and self._is_dead:
            return
        message = {
            "type": "news_update", 
            "data": update.dict()
        }
        await self.send_message(message)

    async def send_status_update(self, status: NewsStreamStatus):
        """Send status update to client."""
        if hasattr(self, '_is_dead') and self._is_dead:
            return
        message = {
            "type": "status_update",
            "data": status.dict()
        }
        await self.send_message(message)

    async def send_error(self, error: str, message: str, ticker: str = ""):
        """Send error message to client, but only if connection is still alive."""
        try:
            # Don't try to send error messages if connection is already dead
            if (hasattr(self, '_is_dead') and self._is_dead):
                logger.debug(f"Connection {self.connection_id} is dead, skipping error message send")
                return
                
            error_msg = {
                "type": "error",
                "data": {
                    "error": error,
                    "message": message,
                    "ticker": ticker,
                    "timestamp": datetime.now().isoformat()
                }
            }
            await self.send_message(error_msg)
        except Exception as e:
            # Prevent cascading errors - if we can't send error messages, just log
            logger.debug(f"Failed to send error message to {self.connection_id}: {e}")
            if hasattr(self, '_is_dead'):
                self._is_dead = True
    
    async def send_completion(self, tickers: List[str], total_articles: int):
        """Send completion notification."""
        if hasattr(self, '_is_dead') and self._is_dead:
            return
        completion_msg = {
            "type": "completed",
            "data": {
                "tickers": tickers,
                "total_articles": total_articles,
                "timestamp": datetime.now().isoformat(),
                "message": f"News feed complete for {len(tickers)} tickers"
            }
        }
        await self.send_message(completion_msg)
    
    def is_subscribed_to(self, ticker: str) -> bool:
        """Check if connection is subscribed to a ticker."""
        return ticker.upper() in self.subscribed_tickers

    def cancel_streaming_task(self):
        """Cancel any active streaming task for this connection."""
        if self._streaming_task and not self._streaming_task.done():
            self._streaming_task.cancel()
            self._streaming_task = None

    def set_streaming_task(self, task: asyncio.Task):
        """Set the active streaming task, canceling any previous one."""
        self.cancel_streaming_task()
        self._streaming_task = task


class NewsWebSocketManager:
    """Manages news WebSocket connections and coordinates with NewsManager and AutoUpdater."""
    
    def __init__(self, news_manager: NewsManager, auto_updater=None):
        self.news_manager = news_manager
        self.auto_updater = auto_updater
        self.connections: Dict[str, NewsWebSocketConnection] = {}
        self.ticker_subscribers: Dict[str, Set[str]] = {}  # ticker -> connection_ids
        self._connection_counter = 0
    
    def _generate_connection_id(self) -> str:
        """Generate unique connection ID."""
        self._connection_counter += 1
        return f"news_ws_{self._connection_counter}_{int(datetime.now().timestamp())}"
    
    async def handle_websocket_connection(self, websocket: WebSocket):
        """Handle new news WebSocket connection."""
        await websocket.accept()
        
        connection_id = self._generate_connection_id()
        connection = NewsWebSocketConnection(websocket, connection_id)
        self.connections[connection_id] = connection
        
        logger.info(f"📰 New news WebSocket connection: {connection_id} (total: {len(self.connections)})")
        
        try:
            # Send welcome message
            await connection.send_message({
                "type": "connected",
                "data": {
                    "connection_id": connection_id,
                    "timestamp": datetime.now().isoformat(),
                    "message": "Connected to real-time news feed"
                }
            })
            
            # Handle incoming messages
            while True:
                try:
                    message = await websocket.receive_text()
                    await self._handle_message(connection, message)
                except WebSocketDisconnect:
                    logger.info(f"📰 News WebSocket {connection_id} disconnected")
                    connection._is_dead = True  # Mark as dead to prevent further message attempts
                    break
                except Exception as e:
                    logger.error(f"❌ Error handling news message from {connection_id}: {e}")
                    try:
                        await connection.send_error("internal_error", "Internal server error")
                    except:
                        # If we can't send error messages, connection is likely dead
                        connection._is_dead = True
                
        except Exception as e:
            logger.error(f"❌ Unexpected error for news WebSocket {connection_id}: {e}")
        finally:
            await self._cleanup_connection(connection_id)
    
    async def _handle_message(self, connection: NewsWebSocketConnection, message: str):
        """Handle incoming WebSocket message from client."""
        try:
            data = json.loads(message)
            message_type = data.get('type', '').lower()
            
            if message_type == 'subscribe':
                await self._handle_subscription(connection, data)
            elif message_type == 'unsubscribe':
                await self._handle_unsubscription(connection, data)
            elif message_type == 'ping':
                await self._handle_ping(connection)
            elif message_type == 'refresh':
                await self._handle_refresh(connection, data)
            else:
                await connection.send_error(
                    "invalid_message_type",
                    f"Unknown message type: {message_type}"
                )
                
        except json.JSONDecodeError:
            await connection.send_error(
                "invalid_json",
                "Message must be valid JSON"
            )
        except Exception as e:
            logger.error(f"Error handling news message from {connection.connection_id}: {e}")
            await connection.send_error(
                "internal_error",
                "Internal server error"
            )
    
    
    async def _handle_unsubscription(self, connection: NewsWebSocketConnection, data: dict):
        """Handle unsubscription request with auto-updater integration."""
        try:
            tickers = data.get('tickers', [])
            
            if not tickers:
                # Unsubscribe from all
                tickers = list(connection.subscribed_tickers)
            
            unsubscribed = []
            auto_update_removed = []
            
            for ticker in tickers:
                ticker = ticker.upper()
                
                if ticker in connection.subscribed_tickers:
                    connection.subscribed_tickers.remove(ticker)
                    
                    # Remove from global subscribers
                    if ticker in self.ticker_subscribers:
                        self.ticker_subscribers[ticker].discard(connection.connection_id)
                        
                        # If no more subscribers for this ticker, unsubscribe from auto-updates
                        if not self.ticker_subscribers[ticker]:
                            del self.ticker_subscribers[ticker]
                            
                            # Unsubscribe from auto-updater
                            if self.auto_updater:
                                self.auto_updater.unsubscribe_ticker(ticker, self.handle_auto_update)
                                auto_update_removed.append(ticker)
                    
                    unsubscribed.append(ticker)
            
            if auto_update_removed:
                logger.info(f"🔄 Removed {len(auto_update_removed)} tickers from auto-updates: {auto_update_removed}")
            
            # Send confirmation
            await connection.send_message({
                "type": "unsubscribed",
                "data": {
                    "tickers": unsubscribed,
                    "remaining_subscriptions": len(connection.subscribed_tickers),
                    "auto_updates_removed": auto_update_removed,
                    "timestamp": datetime.now().isoformat()
                }
            })
            
            logger.info(f"📰 WebSocket {connection.connection_id} unsubscribed from: {unsubscribed}")
            
        except Exception as e:
            logger.error(f"Error handling news unsubscription: {e}")
            await connection.send_error("unsubscription_error", str(e))
    
    async def _handle_ping(self, connection: NewsWebSocketConnection):
        """Handle ping message."""
        connection.last_ping = datetime.now()
        await connection.send_message({
            "type": "pong",
            "data": {
                "timestamp": datetime.now().isoformat()
            }
        })
    
    async def _handle_refresh(self, connection: NewsWebSocketConnection, data: dict):
        """Handle refresh request for specific tickers."""
        try:
            tickers = data.get('tickers', list(connection.subscribed_tickers))
            
            # Force refresh for specified tickers
            for ticker in tickers:
                ticker = ticker.upper()
                if ticker in connection.subscribed_tickers:
                    await connection.send_status_update(NewsStreamStatus(
                        ticker=ticker,
                        status="refreshing",
                        message=f"Force refreshing news for {ticker}",
                        articles_found=0,
                        progress="Triggering backend search"
                    ))
            
            # Start fresh streaming with force_refresh=True
            temp_settings = connection.subscription_settings.copy()
            connection.subscription_settings["force_refresh"] = True
            
            # Cancel any existing streaming task and start a new one
            task = asyncio.create_task(self._stream_news_for_connection(connection, tickers))
            connection.set_streaming_task(task)
            
            # Restore original settings
            connection.subscription_settings = temp_settings
            
        except Exception as e:
            logger.error(f"Error handling news refresh: {e}")
            await connection.send_error("refresh_error", str(e))
    
    async def _stream_news_for_connection(self, connection: NewsWebSocketConnection, tickers: List[str]):
        """Stream news articles for connection's subscribed tickers."""
        try:
            total_articles = 0
            
            for ticker in tickers:
                ticker = ticker.upper()
                
                if not connection.is_subscribed_to(ticker):
                    continue  # Skip if unsubscribed during processing
                
                # Send status update
                await connection.send_status_update(NewsStreamStatus(
                    ticker=ticker,
                    status="processing",
                    message=f"Processing news for {ticker}",
                    articles_found=0,
                    progress="Checking cache and backend"
                ))
                
                try:
                    # Get articles using NewsManager
                    articles, cache_hit = await self.news_manager.get_articles_for_ticker(
                        ticker=ticker,
                        limit=connection.subscription_settings.get("limit", 20),
                        days_back=connection.subscription_settings.get("days_back", 7)
                    )
                    
                    source = "cache" if cache_hit else "fresh"
                    
                    # If no cache hit and not force refresh, try backend
                    if not cache_hit or connection.subscription_settings.get("force_refresh", False):
                        await connection.send_status_update(NewsStreamStatus(
                            ticker=ticker,
                            status="fetching",
                            message=f"Fetching fresh news for {ticker}",
                            articles_found=len(articles),
                            progress="Running backend search"
                        ))
                        
                        # Trigger backend search
                        try:
                            job = await self.news_manager.fetch_news_from_backend(
                                ticker=ticker,
                                company=ticker,  # Use ticker as company name as requested
                                email="NEWS"     # Use "NEWS" as user_email as requested
                            )
                            
                            # Wait for job completion with timeout
                            timeout_seconds = 300  # 5 minutes
                            start_time = datetime.now()
                            
                            while (datetime.now() - start_time).seconds < timeout_seconds:
                                await asyncio.sleep(2)
                                
                                current_job = self.news_manager.get_job_status(job.job_id)
                                if not current_job or current_job.status in ["completed", "failed"]:
                                    break
                            
                            # Get fresh articles after backend job
                            if current_job and current_job.status == "completed":
                                fresh_articles, _ = await self.news_manager.get_articles_for_ticker(
                                    ticker=ticker,
                                    limit=connection.subscription_settings.get("limit", 20),
                                    days_back=connection.subscription_settings.get("days_back", 7)
                                )
                                articles = fresh_articles
                                source = "fresh"
                            
                        except Exception as e:
                            logger.error(f"Backend fetch failed for {ticker}: {e}")
                            # Continue with cached articles if available
                    
                    # Stream articles to client
                    batch_info = {
                        "ticker": ticker,
                        "total_articles": len(articles),
                        "source": source,
                        "batch_size": len(articles),
                        "cache_hit": cache_hit
                    }
                    
                    for i, article in enumerate(articles):
                        if not connection.is_subscribed_to(ticker):
                            break  # Stop if unsubscribed during streaming
                        
                        # Add batch information
                        batch_info.update({
                            "article_index": i + 1,
                            "is_last": i == len(articles) - 1
                        })
                        
                        # Create stream update
                        update = NewsStreamUpdate(
                            ticker=ticker,
                            article=article,
                            source=source,
                            batch_info=batch_info
                        )
                        
                        # Send article to client
                        await connection.send_news_update(update)
                        total_articles += 1
                        
                        # Small delay to prevent overwhelming client
                        await asyncio.sleep(0.1)
                    
                    # Send completion status for this ticker
                    await connection.send_status_update(NewsStreamStatus(
                        ticker=ticker,
                        status="completed",
                        message=f"Completed streaming {len(articles)} articles for {ticker}",
                        articles_found=len(articles),
                        progress="Done"
                    ))
                    
                except Exception as e:
                    logger.error(f"Error streaming news for {ticker}: {e}")
                    await connection.send_status_update(NewsStreamStatus(
                        ticker=ticker,
                        status="error",
                        message=f"Error processing {ticker}: {str(e)}",
                        articles_found=0,
                        progress="Failed"
                    ))
            
            # Send overall completion
            await connection.send_completion(tickers, total_articles)
            
        except Exception as e:
            logger.error(f"Error in news streaming for connection {connection.connection_id}: {e}")
            await connection.send_error("streaming_error", str(e))
    
    async def _cleanup_connection(self, connection_id: str):
        """Clean up connection and subscriptions with auto-updater integration."""
        if connection_id not in self.connections:
            return
        
        connection = self.connections[connection_id]
        # Mark connection as dead to prevent any further message attempts
        connection._is_dead = True
        # Cancel any active streaming task
        connection.cancel_streaming_task()
        auto_update_removed = []
        
        # Remove from ticker subscribers
        for ticker in list(connection.subscribed_tickers):
            if ticker in self.ticker_subscribers:
                self.ticker_subscribers[ticker].discard(connection_id)
                
                # If no more subscribers for this ticker, unsubscribe from auto-updates
                if not self.ticker_subscribers[ticker]:
                    del self.ticker_subscribers[ticker]
                    
                    # Unsubscribe from auto-updater
                    if self.auto_updater:
                        self.auto_updater.unsubscribe_ticker(ticker, self.handle_auto_update)
                        auto_update_removed.append(ticker)
        
        if auto_update_removed:
            logger.info(f"🔄 Removed {len(auto_update_removed)} tickers from auto-updates during cleanup: {auto_update_removed}")
        
        # Remove connection
        del self.connections[connection_id]
        
        logger.info(f"📰 Cleaned up news WebSocket connection {connection_id} (remaining: {len(self.connections)})")
    
    def get_connection_count(self) -> int:
        """Get number of active connections."""
        return len(self.connections)
    
    def get_subscriber_count_for_ticker(self, ticker: str) -> int:
        """Get number of subscribers for a ticker."""
        return len(self.ticker_subscribers.get(ticker.upper(), set()))
    
    def get_all_subscribed_tickers(self) -> List[str]:
        """Get all currently subscribed tickers."""
        return list(self.ticker_subscribers.keys())
    
    def set_auto_updater(self, auto_updater):
        """Set the auto-updater instance and register for automatic updates."""
        self.auto_updater = auto_updater
        logger.info("🔄 Auto-updater integrated with news WebSocket manager")
    
    async def handle_auto_update(self, update: 'NewsStreamUpdate'):
        """Handle automatic news updates from the auto-updater."""
        try:
            ticker = update.ticker.upper()
            
            # Check if we have subscribers for this ticker
            if ticker not in self.ticker_subscribers:
                return
            
            # Broadcast update to all subscribers of this ticker
            connection_ids = list(self.ticker_subscribers[ticker])
            
            logger.info(f"📰 Broadcasting auto-update for {ticker} to {len(connection_ids)} connections")
            
            for connection_id in connection_ids:
                if connection_id in self.connections:
                    connection = self.connections[connection_id]
                    try:
                        await connection.send_news_update(update)
                    except Exception as e:
                        logger.error(f"Error sending auto-update to {connection_id}: {e}")
                        # Connection might be broken, clean it up
                        asyncio.create_task(self._cleanup_connection(connection_id))
            
        except Exception as e:
            logger.error(f"Error handling auto-update for {update.ticker}: {e}")
    
    def register_auto_update_subscriptions(self):
        """Register current WebSocket subscriptions with the auto-updater."""
        if not self.auto_updater:
            return
        
        # Subscribe to auto-updates for all currently subscribed tickers
        for ticker in self.ticker_subscribers.keys():
            self.auto_updater.subscribe_ticker(ticker, self.handle_auto_update)
        
        logger.info(f"📰 Registered {len(self.ticker_subscribers)} tickers for auto-updates")
    
    async def _handle_subscription(self, connection: NewsWebSocketConnection, data: dict):
        """Handle news subscription request with auto-updater integration."""
        try:
            tickers = data.get('tickers', [])
            user_id = data.get('user_id')
            limit = data.get('limit', 20)
            days_back = data.get('days_back', 7)
            force_refresh = data.get('force_refresh', False)
            
            if not tickers:
                await connection.send_error(
                    "invalid_subscription",
                    "Must provide tickers array"
                )
                return
            
            # Update connection settings
            if user_id:
                connection.user_id = user_id
            
            connection.subscription_settings.update({
                "limit": limit,
                "days_back": days_back,
                "force_refresh": force_refresh
            })
            
            # Subscribe to each ticker
            # Check if this is a new subscription vs duplicate
            current_subscriptions = set(connection.subscribed_tickers)
            requested_tickers_set = set(ticker.upper() for ticker in tickers)
            
            # Only restart streaming if the subscription actually changed
            subscription_changed = current_subscriptions != requested_tickers_set
            
            subscribed = []
            new_tickers = []
            
            for ticker in tickers:
                ticker = ticker.upper()
                
                # Check if this is a new ticker subscription
                was_subscribed = ticker in self.ticker_subscribers
                is_new_for_connection = ticker not in connection.subscribed_tickers
                
                if is_new_for_connection:
                    new_tickers.append(ticker)
                
                # Add to connection subscriptions
                connection.subscribed_tickers.add(ticker)
                
                # Add to global ticker subscribers
                if ticker not in self.ticker_subscribers:
                    self.ticker_subscribers[ticker] = set()
                self.ticker_subscribers[ticker].add(connection.connection_id)
                
                subscribed.append(ticker)
                
                # If this is a new ticker and we have auto-updater, subscribe for auto-updates
                if not was_subscribed and self.auto_updater:
                    self.auto_updater.subscribe_ticker(ticker, self.handle_auto_update)
                    new_tickers.append(ticker)
            
            if new_tickers:
                logger.info(f"🔄 Registered {len(new_tickers)} new tickers for auto-updates: {new_tickers}")
            
            # Send confirmation
            await connection.send_message({
                "type": "subscribed",
                "data": {
                    "tickers": subscribed,
                    "total_subscriptions": len(connection.subscribed_tickers),
                    "settings": connection.subscription_settings,
                    "auto_updates_enabled": self.auto_updater is not None,
                    "timestamp": datetime.now().isoformat()
                }
            })
            
            # Log appropriately based on whether this is new or duplicate
            if subscription_changed or new_tickers:
                logger.info(f"📰 WebSocket {connection.connection_id} subscribed to: {subscribed} (new: {new_tickers})")
                
                # Start streaming news for subscribed tickers (cancel any existing task first)
                task = asyncio.create_task(self._stream_news_for_connection(connection, subscribed))
                connection.set_streaming_task(task)
            else:
                logger.debug(f"📰 WebSocket {connection.connection_id} duplicate subscription ignored: {subscribed}")
            
        except Exception as e:
            logger.error(f"Error handling news subscription: {e}")
            await connection.send_error("subscription_error", str(e))


# Global WebSocket manager instance
_news_websocket_manager: Optional[NewsWebSocketManager] = None

def init_news_websocket_manager(news_manager: NewsManager, auto_updater=None) -> NewsWebSocketManager:
    """Initialize the global news WebSocket manager with optional auto-updater."""
    global _news_websocket_manager
    _news_websocket_manager = NewsWebSocketManager(news_manager, auto_updater)
    logger.info("✅ News WebSocket manager initialized")
    return _news_websocket_manager

def get_news_websocket_manager() -> Optional[NewsWebSocketManager]:
    """Get the global news WebSocket manager."""
    return _news_websocket_manager