"""
Stock Price Fetcher Service

Background service that fetches real-time stock prices from yfinance API
every 10 seconds and manages price updates for WebSocket streaming.
"""

import asyncio
import logging
import yfinance as yf
from typing import Dict, List, Set, Optional, Callable
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from .models import StockPrice, PriceUpdate, PriceFetchConfig, ErrorResponse

logger = logging.getLogger(__name__)

class StockPriceFetcher:
    """
    Fetches stock prices from yfinance API in a background thread.
    Designed to be non-blocking and efficient.
    """
    
    def __init__(self, config: PriceFetchConfig = None):
        self.config = config or PriceFetchConfig()
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="yfinance")
        self._cache: Dict[str, StockPrice] = {}
        self._last_fetch: Dict[str, datetime] = {}
        
    async def fetch_price(self, symbol: str) -> Optional[StockPrice]:
        """Fetch price for a single symbol."""
        try:
            # Run yfinance in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            ticker_data = await loop.run_in_executor(
                self.executor, 
                self._fetch_ticker_sync, 
                symbol
            )
            
            if ticker_data:
                price = self._parse_ticker_data(symbol, ticker_data)
                self._cache[symbol] = price
                self._last_fetch[symbol] = datetime.now()
                logger.debug(f"📈 Fetched price for {symbol}: ${price.current_price}")
                return price
            else:
                logger.warning(f"⚠️ No data returned for symbol: {symbol}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error fetching price for {symbol}: {e}")
            return None
    
    def _fetch_ticker_sync(self, symbol: str) -> Optional[Dict]:
        """Synchronous yfinance API call - runs in thread pool."""
        try:
            ticker = yf.Ticker(symbol)
            
            # Get current info and history
            info = ticker.info
            hist = ticker.history(period="1d", interval="1m")
            
            if hist.empty or not info:
                return None
                
            # Get the latest price data
            latest_data = hist.iloc[-1] if not hist.empty else None
            
            return {
                'info': info,
                'latest': latest_data,
                'history': hist
            }
            
        except Exception as e:
            logger.error(f"yfinance sync error for {symbol}: {e}")
            return None
    
    def _parse_ticker_data(self, symbol: str, data: Dict) -> StockPrice:
        """Parse yfinance data into StockPrice model."""
        info = data.get('info', {})
        latest = data.get('latest')
        
        # Get current price from multiple sources
        current_price = None
        if latest is not None:
            current_price = float(latest['Close']) if 'Close' in latest else None
        
        if current_price is None:
            current_price = info.get('currentPrice') or info.get('regularMarketPrice')
        
        # Get previous close
        previous_close = info.get('previousClose') or info.get('regularMarketPreviousClose')
        
        # Calculate change
        change_amount = None
        change_percent = None
        if current_price and previous_close:
            change_amount = current_price - previous_close
            change_percent = (change_amount / previous_close) * 100
        
        return StockPrice(
            symbol=symbol.upper(),
            current_price=current_price,
            previous_close=previous_close,
            open_price=info.get('regularMarketOpen') or (float(latest['Open']) if latest is not None and 'Open' in latest else None),
            day_high=info.get('regularMarketDayHigh') or info.get('dayHigh'),
            day_low=info.get('regularMarketDayLow') or info.get('dayLow'),
            volume=info.get('regularMarketVolume') or info.get('volume'),
            market_cap=info.get('marketCap'),
            pe_ratio=info.get('trailingPE'),
            change_amount=change_amount,
            change_percent=change_percent,
            currency=info.get('currency', 'USD'),
            last_updated=datetime.now(),
            is_market_open=self._is_market_open(info)
        )
    
    def _is_market_open(self, info: Dict) -> bool:
        """Determine if market is currently open based on info data."""
        # This is a simplified check - you might want to use market_calendar library
        # for more accurate market hours checking
        market_state = info.get('marketState', '').lower()
        return market_state in ['regular', 'pre', 'post']
    
    async def fetch_multiple_prices(self, symbols: List[str]) -> Dict[str, Optional[StockPrice]]:
        """Fetch prices for multiple symbols concurrently."""
        tasks = [self.fetch_price(symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        price_dict = {}
        for i, result in enumerate(results):
            symbol = symbols[i]
            if isinstance(result, Exception):
                logger.error(f"Error fetching {symbol}: {result}")
                price_dict[symbol] = None
            else:
                price_dict[symbol] = result
        
        return price_dict
    
    def get_cached_price(self, symbol: str) -> Optional[StockPrice]:
        """Get cached price for a symbol."""
        return self._cache.get(symbol)
    
    def is_cache_fresh(self, symbol: str, max_age_seconds: int = 60) -> bool:
        """Check if cached price is fresh enough."""
        last_fetch = self._last_fetch.get(symbol)
        if not last_fetch:
            return False
        
        age = (datetime.now() - last_fetch).total_seconds()
        return age < max_age_seconds
    
    def cleanup(self):
        """Cleanup resources."""
        self.executor.shutdown(wait=True)

class PriceManager:
    """
    Manages real-time price updates and WebSocket subscriptions.
    Runs background task to fetch prices every 10 seconds.
    """
    
    def __init__(self, config: PriceFetchConfig = None):
        self.config = config or PriceFetchConfig()
        self.fetcher = StockPriceFetcher(self.config)
        self.subscribed_symbols: Set[str] = set()
        self.subscribers: Dict[str, List[Callable]] = {}  # symbol -> list of callback functions
        self.is_running = False
        self._background_task: Optional[asyncio.Task] = None
        self._start_time = datetime.now()
        
    async def start(self):
        """Start the price management service."""
        if self.is_running:
            logger.warning("⚠️ PriceManager already running")
            return
        
        self.is_running = True
        self._start_time = datetime.now()
        
        # Start background price fetching task
        self._background_task = asyncio.create_task(self._price_fetch_loop())
        logger.info("🚀 PriceManager started - fetching prices every 10 seconds")
    
    async def stop(self):
        """Stop the price management service."""
        if not self.is_running:
            return
        
        self.is_running = False
        
        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass
        
        self.fetcher.cleanup()
        logger.info("🛑 PriceManager stopped")
    
    def subscribe_symbol(self, symbol: str, callback: Callable[[PriceUpdate], None]):
        """Subscribe to price updates for a symbol."""
        symbol = symbol.upper()
        self.subscribed_symbols.add(symbol)
        
        if symbol not in self.subscribers:
            self.subscribers[symbol] = []
        
        self.subscribers[symbol].append(callback)
        logger.info(f"📊 Subscribed to {symbol} - total symbols: {len(self.subscribed_symbols)}")
    
    def unsubscribe_symbol(self, symbol: str, callback: Callable = None):
        """Unsubscribe from price updates for a symbol."""
        symbol = symbol.upper()
        
        if symbol in self.subscribers:
            if callback:
                # Remove specific callback
                if callback in self.subscribers[symbol]:
                    self.subscribers[symbol].remove(callback)
            else:
                # Remove all callbacks
                self.subscribers[symbol].clear()
            
            # Remove symbol if no more callbacks
            if not self.subscribers[symbol]:
                del self.subscribers[symbol]
                self.subscribed_symbols.discard(symbol)
        
        logger.info(f"📊 Unsubscribed from {symbol} - remaining symbols: {len(self.subscribed_symbols)}")
    
    def get_subscribed_symbols(self) -> List[str]:
        """Get list of currently subscribed symbols."""
        return list(self.subscribed_symbols)
    
    async def get_current_price(self, symbol: str, force_refresh: bool = False) -> Optional[StockPrice]:
        """Get current price for a symbol."""
        symbol = symbol.upper()
        
        # Check cache first
        if not force_refresh and self.fetcher.is_cache_fresh(symbol):
            cached = self.fetcher.get_cached_price(symbol)
            if cached:
                return cached
        
        # Fetch fresh data
        return await self.fetcher.fetch_price(symbol)
    
    async def get_multiple_prices(self, symbols: List[str]) -> Dict[str, Optional[StockPrice]]:
        """Get current prices for multiple symbols."""
        symbols = [s.upper() for s in symbols]
        return await self.fetcher.fetch_multiple_prices(symbols)
    
    async def _price_fetch_loop(self):
        """Background loop that fetches prices every 10 seconds."""
        logger.info("🔄 Starting price fetch loop...")
        
        while self.is_running:
            try:
                if self.subscribed_symbols:
                    logger.debug(f"📈 Fetching prices for {len(self.subscribed_symbols)} symbols...")
                    
                    # Fetch prices for all subscribed symbols
                    symbols = list(self.subscribed_symbols)
                    prices = await self.fetcher.fetch_multiple_prices(symbols)
                    
                    # Notify subscribers of price updates
                    for symbol, price in prices.items():
                        if price and symbol in self.subscribers:
                            # Create price update message
                            update = PriceUpdate(
                                symbol=symbol,
                                current_price=price.current_price or 0.0,
                                change_amount=price.change_amount or 0.0,
                                change_percent=price.change_percent or 0.0,
                                volume=price.volume,
                                timestamp=price.last_updated
                            )
                            
                            # Notify all subscribers for this symbol
                            callbacks = self.subscribers.get(symbol, [])
                            for callback in callbacks:
                                try:
                                    await self._safe_callback(callback, update)
                                except Exception as e:
                                    logger.error(f"Error in price update callback for {symbol}: {e}")
                    
                    logger.debug(f"✅ Price fetch completed for {len(symbols)} symbols")
                else:
                    logger.debug("💤 No subscribed symbols, skipping fetch...")
                
                # Wait for next fetch interval
                await asyncio.sleep(self.config.fetch_interval)
                
            except asyncio.CancelledError:
                logger.info("🛑 Price fetch loop cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Error in price fetch loop: {e}")
                await asyncio.sleep(5)  # Short delay before retry
    
    async def _safe_callback(self, callback: Callable, update: PriceUpdate):
        """Safely execute callback function."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(update)
            else:
                callback(update)
        except Exception as e:
            logger.error(f"Callback error: {e}")
    
    def get_status(self) -> Dict:
        """Get service status information."""
        uptime = (datetime.now() - self._start_time).total_seconds()
        
        return {
            'is_running': self.is_running,
            'subscribed_symbols': len(self.subscribed_symbols),
            'uptime_seconds': uptime,
            'symbols': list(self.subscribed_symbols),
            'fetch_interval': self.config.fetch_interval
        }