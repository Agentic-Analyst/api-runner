"""
Real-time Stock Price Data Models

Defines Pydantic models for stock price data, WebSocket messages,
and subscription management.
"""

from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, Dict, Any
from decimal import Decimal

class StockPrice(BaseModel):
    """Model for stock price data from yfinance API."""
    
    symbol: str = Field(..., description="Stock symbol (e.g., AAPL, GOOGL)")
    name: Optional[str] = Field(None, description="Company name")
    current_price: Optional[float] = Field(None, description="Current stock price")
    previous_close: Optional[float] = Field(None, description="Previous closing price")
    open_price: Optional[float] = Field(None, description="Today's opening price")
    day_high: Optional[float] = Field(None, description="Today's highest price")
    day_low: Optional[float] = Field(None, description="Today's lowest price")
    volume: Optional[int] = Field(None, description="Trading volume")
    market_cap: Optional[int] = Field(None, description="Market capitalization")
    pe_ratio: Optional[float] = Field(None, description="Price-to-earnings ratio")
    change_amount: Optional[float] = Field(None, description="Price change in dollars")
    change_percent: Optional[float] = Field(None, description="Price change as percentage")
    currency: Optional[str] = Field("USD", description="Currency of the stock price")
    last_updated: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    is_market_open: Optional[bool] = Field(None, description="Whether market is currently open")
    
    # Company classification
    sector: Optional[str] = Field(None, description="Company sector (e.g., Technology, Healthcare)")
    industry: Optional[str] = Field(None, description="Company industry (e.g., Software, Pharmaceuticals)")
    
    # Additional market data for enhanced frontend display
    bid: Optional[float] = Field(None, description="Bid price")
    ask: Optional[float] = Field(None, description="Ask price")
    high_52w: Optional[float] = Field(None, description="52-week high")
    low_52w: Optional[float] = Field(None, description="52-week low")
    avg_volume: Optional[int] = Field(None, description="Average volume")
    dividend_yield: Optional[float] = Field(None, description="Dividend yield (if available)")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class PriceUpdate(BaseModel):
    """Model for real-time price updates sent via WebSocket."""
    
    type: str = Field(default="price_update", description="Message type")
    symbol: str = Field(..., description="Stock symbol")
    name: Optional[str] = Field(None, description="Company name")
    current_price: float = Field(..., description="Current price")
    change_amount: float = Field(..., description="Price change in dollars")
    change_percent: float = Field(..., description="Price change as percentage")
    volume: Optional[int] = Field(None, description="Current day volume")
    market_cap: Optional[int] = Field(None, description="Market capitalization")
    timestamp: str = Field(..., description="Last update time")
    
    # Company classification
    sector: Optional[str] = Field(None, description="Company sector (e.g., Technology, Healthcare)")
    industry: Optional[str] = Field(None, description="Company industry (e.g., Software, Pharmaceuticals)")
    
    # Additional market data for enhanced frontend display
    bid: Optional[float] = Field(None, description="Bid price")
    ask: Optional[float] = Field(None, description="Ask price")
    high_52w: Optional[float] = Field(None, description="52-week high")
    low_52w: Optional[float] = Field(None, description="52-week low")
    day_high: Optional[float] = Field(None, description="Today's high")
    day_low: Optional[float] = Field(None, description="Today's low")
    avg_volume: Optional[int] = Field(None, description="Average volume")
    pe_ratio: Optional[float] = Field(None, description="P/E ratio (if available)")
    dividend_yield: Optional[float] = Field(None, description="Dividend yield (if available)")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class StockSubscription(BaseModel):
    """Model for WebSocket subscription requests."""
    
    action: str = Field(..., description="Action: 'subscribe' or 'unsubscribe'")
    symbols: List[str] = Field(..., description="List of stock symbols to subscribe/unsubscribe")
    user_id: Optional[str] = Field(None, description="Optional user identification")
    
    class Config:
        json_schema_extra = {
            "example": {
                "action": "subscribe",
                "symbols": ["AAPL", "GOOGL", "MSFT"],
                "user_id": "user@example.com"
            }
        }

class WebSocketMessage(BaseModel):
    """Base model for WebSocket messages."""
    
    type: str = Field(..., description="Message type")
    timestamp: datetime = Field(default_factory=datetime.now, description="Message timestamp")
    data: Dict[str, Any] = Field(default_factory=dict, description="Message payload")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class StockInfo(BaseModel):
    """Extended stock information model."""
    
    symbol: str = Field(..., description="Stock symbol")
    name: str = Field(..., description="Company name")
    sector: Optional[str] = Field(None, description="Business sector")
    industry: Optional[str] = Field(None, description="Industry classification")
    website: Optional[str] = Field(None, description="Company website")
    description: Optional[str] = Field(None, description="Company description")
    employees: Optional[int] = Field(None, description="Number of employees")
    headquarters: Optional[str] = Field(None, description="Company headquarters location")
    
class MarketStatus(BaseModel):
    """Market status information."""
    
    is_open: bool = Field(..., description="Whether market is open")
    market_name: str = Field(..., description="Market name (e.g., NASDAQ, NYSE)")
    next_open: Optional[datetime] = Field(None, description="Next market open time")
    next_close: Optional[datetime] = Field(None, description="Next market close time")
    timezone: str = Field(default="America/New_York", description="Market timezone")
    
class PriceFetchConfig(BaseModel):
    """Configuration for price fetching service."""
    
    fetch_interval: int = Field(default=10, description="Fetch interval in seconds")
    max_symbols_per_request: int = Field(default=50, description="Max symbols per API request")
    retry_attempts: int = Field(default=3, description="Number of retry attempts on failure")
    timeout: int = Field(default=30, description="Request timeout in seconds")
    enable_after_hours: bool = Field(default=True, description="Include after-hours trading data")
    
class ErrorResponse(BaseModel):
    """Error response model."""
    
    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Error message")
    symbol: Optional[str] = Field(None, description="Related stock symbol")
    timestamp: datetime = Field(default_factory=datetime.now, description="Error timestamp")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class HealthCheck(BaseModel):
    """Health check response model."""
    
    status: str = Field(..., description="Service status")
    services: Dict[str, str] = Field(..., description="Individual service statuses")
    timestamp: datetime = Field(default_factory=datetime.now, description="Health check timestamp")
    uptime: Optional[float] = Field(None, description="Service uptime in seconds")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class HistoricalDataPoint(BaseModel):
    """Model for historical price data points."""
    
    timestamp: str = Field(..., description="ISO timestamp")
    price: float = Field(..., description="Price at that time")
    volume: Optional[int] = Field(None, description="Volume (optional)")

class HistoricalData(BaseModel):
    """Model for historical price data response."""
    
    symbol: str = Field(..., description="Stock symbol")
    timeframe: str = Field(..., description="Timeframe: '1D', '1W', '1M', '3M', '1Y', 'ALL'")
    data: List[HistoricalDataPoint] = Field(..., description="Array of historical data points")