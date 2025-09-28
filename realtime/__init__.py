"""
Real-time Stock Price System

This module provides real-time stock price functionality with:
- Yahoo Finance API integration (yfinance)
- WebSocket server for real-time price streaming
- Background price fetching every 10 seconds
- Extensible architecture for additional data sources

Components:
- price_fetcher: Background service to fetch prices from yfinance
- websocket_server: WebSocket server for real-time price streaming
- stock_api: REST API endpoints for stock price management
- models: Data models for stock prices and configuration
"""

from .price_fetcher import StockPriceFetcher, PriceManager
from .stock_api import router as stock_router
from .models import StockPrice, PriceUpdate, StockSubscription, PriceFetchConfig

__all__ = [
    'StockPriceFetcher',
    'PriceManager',
    'stock_router',
    'StockPrice',
    'PriceUpdate',
    'StockSubscription',
    'PriceFetchConfig'
]