"""
REST API endpoints for real-time stock price management.

Provides HTTP endpoints to complement the WebSocket functionality,
including manual price fetching, subscription management, and health checks.
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query, Depends, WebSocket
from fastapi.responses import JSONResponse

from .models import (
    StockPrice, PriceUpdate, StockSubscription, StockInfo, 
    MarketStatus, HealthCheck, ErrorResponse, PriceFetchConfig
)
from .price_fetcher import PriceManager, StockPriceFetcher

logger = logging.getLogger(__name__)

# Router for stock price endpoints
router = APIRouter(prefix="/api/realtime", tags=["Real-time Stock Prices"])

# Global price manager instance (will be initialized in main.py)
price_manager: Optional[PriceManager] = None
websocket_manager = None

def get_price_manager() -> PriceManager:
    """Dependency to get price manager instance."""
    if price_manager is None:
        raise HTTPException(status_code=503, detail="Real-time service not initialized")
    return price_manager

def get_websocket_manager():
    """Dependency to get websocket manager instance."""
    if websocket_manager is None:
        raise HTTPException(status_code=503, detail="WebSocket service not initialized")
    return websocket_manager

# Health and Status Endpoints

@router.get("/health", response_model=HealthCheck)
async def health_check(
    pm: PriceManager = Depends(get_price_manager)
) -> HealthCheck:
    """Check health of real-time stock price services."""
    try:
        pm_status = pm.get_status()
        
        services = {
            "price_manager": "healthy" if pm_status["is_running"] else "stopped",
            "price_fetcher": "healthy",
            "yfinance_api": "healthy"  # Could add actual yfinance connectivity test
        }
        
        # Try to get websocket stats if available
        try:
            ws_manager = get_websocket_manager()
            ws_stats = ws_manager.get_stats()
            services["websocket_server"] = f"healthy ({ws_stats['total_connections']} connections)"
        except:
            services["websocket_server"] = "not_available"
        
        return HealthCheck(
            status="healthy" if all(s.startswith("healthy") for s in services.values()) else "degraded",
            services=services,
            uptime=pm_status.get("uptime_seconds", 0)
        )
        
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return HealthCheck(
            status="unhealthy",
            services={"error": str(e)},
            uptime=0
        )

@router.get("/status")
async def get_service_status(
    pm: PriceManager = Depends(get_price_manager)
) -> Dict:
    """Get detailed service status and statistics."""
    try:
        pm_status = pm.get_status()
        
        status = {
            "price_manager": pm_status,
            "timestamp": datetime.now().isoformat()
        }
        
        # Add WebSocket stats if available
        try:
            ws_manager = get_websocket_manager()
            status["websocket"] = ws_manager.get_stats()
        except:
            status["websocket"] = {"status": "not_available"}
        
        return status
        
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")

# Price Fetching Endpoints

@router.get("/price/{symbol}", response_model=StockPrice)
async def get_stock_price(
    symbol: str,
    force_refresh: bool = Query(False, description="Force fetch fresh data from API"),
    pm: PriceManager = Depends(get_price_manager)
) -> StockPrice:
    """Get current price for a specific stock symbol."""
    try:
        symbol = symbol.upper()
        price = await pm.get_current_price(symbol, force_refresh=force_refresh)
        
        if price is None:
            raise HTTPException(
                status_code=404, 
                detail=f"Could not fetch price data for symbol: {symbol}"
            )
        
        return price
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching price for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

@router.post("/prices", response_model=Dict[str, Optional[StockPrice]])
async def get_multiple_stock_prices(
    symbols: List[str],
    force_refresh: bool = Query(False, description="Force fetch fresh data from API"),
    pm: PriceManager = Depends(get_price_manager)
) -> Dict[str, Optional[StockPrice]]:
    """Get current prices for multiple stock symbols."""
    try:
        if not symbols:
            raise HTTPException(status_code=400, detail="Must provide at least one symbol")
        
        if len(symbols) > 50:  # Reasonable limit
            raise HTTPException(status_code=400, detail="Too many symbols (max 50)")
        
        symbols = [s.upper() for s in symbols]
        
        if force_refresh:
            # Force refresh by fetching individually
            prices = {}
            for symbol in symbols:
                prices[symbol] = await pm.get_current_price(symbol, force_refresh=True)
        else:
            prices = await pm.get_multiple_prices(symbols)
        
        return prices
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching multiple prices: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

# Subscription Management Endpoints

@router.get("/subscriptions")
async def get_active_subscriptions(
    pm: PriceManager = Depends(get_price_manager)
) -> Dict:
    """Get list of currently subscribed symbols and connection count."""
    try:
        subscribed_symbols = pm.get_subscribed_symbols()
        
        # Get WebSocket connection info if available
        connection_info = {}
        try:
            ws_manager = get_websocket_manager()
            ws_stats = ws_manager.get_stats()
            connection_info = {
                "total_connections": ws_stats["total_connections"],
                "connections": ws_stats["connections"]
            }
        except:
            connection_info = {"status": "websocket_not_available"}
        
        return {
            "subscribed_symbols": subscribed_symbols,
            "symbol_count": len(subscribed_symbols),
            "websocket_info": connection_info,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error getting subscriptions: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

@router.post("/subscribe/{symbol}")
async def subscribe_to_symbol(
    symbol: str,
    pm: PriceManager = Depends(get_price_manager)
) -> Dict:
    """Manually subscribe to a symbol (useful for testing or preloading)."""
    try:
        symbol = symbol.upper()
        
        # Create a dummy callback for manual subscription
        def dummy_callback(update: PriceUpdate):
            logger.debug(f"Manual subscription update for {symbol}: ${update.current_price}")
        
        pm.subscribe_symbol(symbol, dummy_callback)
        
        # Get current price
        price = await pm.get_current_price(symbol, force_refresh=True)
        
        return {
            "message": f"Subscribed to {symbol}",
            "symbol": symbol,
            "current_price": price.dict() if price else None,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error subscribing to {symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

@router.delete("/subscribe/{symbol}")
async def unsubscribe_from_symbol(
    symbol: str,
    pm: PriceManager = Depends(get_price_manager)
) -> Dict:
    """Manually unsubscribe from a symbol."""
    try:
        symbol = symbol.upper()
        pm.unsubscribe_symbol(symbol)
        
        return {
            "message": f"Unsubscribed from {symbol}",
            "symbol": symbol,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error unsubscribing from {symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

# Market Information Endpoints

@router.get("/market/status")
async def get_market_status() -> MarketStatus:
    """Get current market status (simplified implementation)."""
    try:
        # This is a simplified implementation
        # In production, you might want to use a proper market calendar library
        
        current_time = datetime.now()
        current_hour = current_time.hour
        current_weekday = current_time.weekday()  # 0=Monday, 6=Sunday
        
        # Simple market hours check (9:30 AM - 4:00 PM ET, Monday-Friday)
        # This is very simplified and doesn't account for holidays or exact timezones
        is_open = (
            current_weekday < 5 and  # Monday-Friday
            9.5 <= current_hour <= 16  # 9:30 AM - 4:00 PM (simplified)
        )
        
        return MarketStatus(
            is_open=is_open,
            market_name="US Markets",
            timezone="America/New_York"
        )
        
    except Exception as e:
        logger.error(f"Error getting market status: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

# Configuration Endpoints

@router.get("/config", response_model=PriceFetchConfig)
async def get_configuration(
    pm: PriceManager = Depends(get_price_manager)
) -> PriceFetchConfig:
    """Get current price fetcher configuration."""
    return pm.config

@router.post("/config", response_model=Dict)
async def update_configuration(
    config: PriceFetchConfig,
    pm: PriceManager = Depends(get_price_manager)
) -> Dict:
    """Update price fetcher configuration (requires service restart for some settings)."""
    try:
        # Update configuration
        pm.config = config
        
        return {
            "message": "Configuration updated successfully",
            "config": config.dict(),
            "note": "Some configuration changes may require service restart",
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error updating configuration: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

# Testing and Development Endpoints

@router.post("/test/yfinance")
async def test_yfinance_connection() -> Dict:
    """Test yfinance API connectivity with a known symbol."""
    try:
        # Create a temporary fetcher for testing
        fetcher = StockPriceFetcher()
        
        # Test with AAPL (should always be available)
        test_symbol = "AAPL"
        price = await fetcher.fetch_price(test_symbol)
        
        fetcher.cleanup()
        
        if price:
            return {
                "status": "success",
                "message": "yfinance API is working",
                "test_symbol": test_symbol,
                "test_price": price.dict(),
                "timestamp": datetime.now().isoformat()
            }
        else:
            return {
                "status": "error",
                "message": "yfinance API returned no data",
                "test_symbol": test_symbol,
                "timestamp": datetime.now().isoformat()
            }
        
    except Exception as e:
        logger.error(f"yfinance test failed: {e}")
        return {
            "status": "error",
            "message": f"yfinance test failed: {str(e)}",
            "timestamp": datetime.now().isoformat()
        }

@router.get("/debug/cache")
async def get_cache_info(
    pm: PriceManager = Depends(get_price_manager)
) -> Dict:
    """Get information about cached prices (for debugging)."""
    try:
        cache_info = {
            "cached_symbols": [],
            "cache_ages": {},
            "total_cached": 0
        }
        
        # Get cached symbols from the fetcher
        for symbol in pm.get_subscribed_symbols():
            cached_price = pm.fetcher.get_cached_price(symbol)
            if cached_price:
                cache_info["cached_symbols"].append(symbol)
                last_fetch = pm.fetcher._last_fetch.get(symbol)
                if last_fetch:
                    age = (datetime.now() - last_fetch).total_seconds()
                    cache_info["cache_ages"][symbol] = f"{age:.1f}s ago"
        
        cache_info["total_cached"] = len(cache_info["cached_symbols"])
        cache_info["timestamp"] = datetime.now().isoformat()
        
        return cache_info
        
    except Exception as e:
        logger.error(f"Error getting cache info: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

# WebSocket endpoint for real-time price streaming
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time stock price updates."""
    from .fastapi_websocket import get_fastapi_websocket_manager
    
    ws_manager = get_fastapi_websocket_manager()
    if ws_manager is None:
        await websocket.close(code=1000, reason="WebSocket service not available")
        return
    
    await ws_manager.handle_websocket_connection(websocket)

# Initialize function to be called from main.py
def init_realtime_api(pm: PriceManager, ws_manager=None):
    """Initialize the real-time API with price manager and websocket manager."""
    global price_manager, websocket_manager
    price_manager = pm
    websocket_manager = ws_manager
    logger.info("🚀 Real-time stock price API initialized")