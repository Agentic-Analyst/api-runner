"""
Database package for MongoDB operations.
Provides user management APIs and database connectivity.
"""

from .mongodb import init_mongodb, close_mongodb, get_database, get_collection, check_mongodb_health
from .models import (
    UserBase, UserCreate, UserUpdate, UserInDB, UserResponse,
    UserIdentifyRequest, UserIdentifyResponse, UserActivityUpdate,
    PortfolioHoldingBase, PortfolioHoldingCreate, PortfolioHoldingUpdate,
    PortfolioHoldingInDB, PortfolioHoldingResponse, PortfolioSummary,
    PortfolioRequest, PortfolioHoldingRequest
)
from .user_service import user_service
from .portfolio_service import portfolio_service
from .user_api import router as user_router
from .portfolio_api import router as portfolio_router

__all__ = [
    "init_mongodb",
    "close_mongodb", 
    "get_database",
    "get_collection",
    "check_mongodb_health",
    "UserBase",
    "UserCreate", 
    "UserUpdate",
    "UserInDB",
    "UserResponse",
    "UserIdentifyRequest",
    "UserIdentifyResponse", 
    "UserActivityUpdate",
    "PortfolioHoldingBase",
    "PortfolioHoldingCreate",
    "PortfolioHoldingUpdate", 
    "PortfolioHoldingInDB",
    "PortfolioHoldingResponse",
    "PortfolioSummary",
    "PortfolioRequest",
    "PortfolioHoldingRequest",
    "user_service",
    "portfolio_service",
    "user_router",
    "portfolio_router"
]