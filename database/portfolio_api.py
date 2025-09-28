"""
Portfolio API endpoints for stock dashboard portfolio management.
Provides REST APIs for managing user portfolios including holdings CRUD operations.
"""

import logging
from fastapi import APIRouter, HTTPException, Query
from typing import List

from .models import (
    PortfolioHoldingCreate,
    PortfolioHoldingUpdate, 
    PortfolioHoldingResponse,
    PortfolioSummary,
    PortfolioRequest,
    PortfolioHoldingRequest
)
from .portfolio_service import portfolio_service

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

@router.get("/summary", response_model=PortfolioSummary)
async def get_portfolio_summary(user_email: str = Query(..., description="User email for identification")):
    """
    Get complete portfolio summary with all holdings and statistics.
    
    This is the main endpoint for the frontend dashboard to display the user's portfolio.
    """
    try:
        logger.info(f"Portfolio summary request for user: {user_email}")
        
        summary = await portfolio_service.get_portfolio_summary(user_email)
        
        logger.info(f"Portfolio summary retrieved for {user_email}: {summary.total_holdings} holdings, ${summary.total_value:.2f} total value")
        return summary
        
    except Exception as e:
        logger.error(f"Error getting portfolio summary for {user_email}: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve portfolio summary")

@router.get("/holdings", response_model=List[PortfolioHoldingResponse])
async def get_holdings(user_email: str = Query(..., description="User email for identification")):
    """Get all portfolio holdings for a user."""
    try:
        holdings = await portfolio_service.get_user_holdings(user_email)
        
        # Convert to response models
        responses = [portfolio_service.holding_to_response(holding) for holding in holdings]
        
        logger.info(f"Retrieved {len(responses)} holdings for user {user_email}")
        return responses
        
    except Exception as e:
        logger.error(f"Error getting holdings for {user_email}: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve holdings")

@router.get("/holdings/{symbol}", response_model=PortfolioHoldingResponse)
async def get_holding_by_symbol(
    symbol: str, 
    user_email: str = Query(..., description="User email for identification")
):
    """Get a specific holding by stock symbol."""
    try:
        holding = await portfolio_service.get_holding_by_symbol(user_email, symbol)
        
        if not holding:
            raise HTTPException(status_code=404, detail=f"Holding for symbol {symbol.upper()} not found")
        
        response = portfolio_service.holding_to_response(holding)
        logger.info(f"Retrieved holding {symbol} for user {user_email}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting holding {symbol} for {user_email}: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve holding")

@router.post("/holdings", response_model=PortfolioHoldingResponse)
async def create_holding(holding_request: PortfolioHoldingRequest):
    """
    Create a new portfolio holding.
    
    If a holding with the same symbol already exists, use the add endpoint instead.
    """
    try:
        logger.info(f"Creating holding {holding_request.symbol} for user {holding_request.user_email}")
        
        # Extract holding data (excluding user_email)
        holding_data = PortfolioHoldingCreate(
            symbol=holding_request.symbol,
            name=holding_request.name,
            shares=holding_request.shares,
            cost_basis=holding_request.cost_basis
        )
        
        new_holding = await portfolio_service.create_holding(holding_request.user_email, holding_data)
        response = portfolio_service.holding_to_response(new_holding)
        
        logger.info(f"Created holding {holding_request.symbol} for user {holding_request.user_email}")
        return response
        
    except ValueError as e:
        logger.warning(f"Holding creation failed for {holding_request.user_email}: {e}")
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating holding for {holding_request.user_email}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create holding")

@router.post("/holdings/add", response_model=PortfolioHoldingResponse)
async def add_or_update_holding(holding_request: PortfolioHoldingRequest):
    """
    Add shares to portfolio (create new holding or merge with existing).
    
    If the holding exists, this will:
    - Add the new shares to existing shares
    - Recalculate the weighted average cost basis
    - Update the company name if provided
    
    If the holding doesn't exist, it creates a new one.
    """
    try:
        logger.info(f"Adding/updating holding {holding_request.symbol} for user {holding_request.user_email}")
        
        # Extract holding data
        holding_data = PortfolioHoldingCreate(
            symbol=holding_request.symbol,
            name=holding_request.name,
            shares=holding_request.shares,
            cost_basis=holding_request.cost_basis
        )
        
        updated_holding, is_new = await portfolio_service.add_or_update_holding(holding_request.user_email, holding_data)
        response = portfolio_service.holding_to_response(updated_holding)
        
        action = "Created new" if is_new else "Updated existing"
        logger.info(f"{action} holding {holding_request.symbol} for user {holding_request.user_email}")
        
        return response
        
    except Exception as e:
        logger.error(f"Error adding/updating holding {holding_request.symbol} for {holding_request.user_email}: {e}")
        raise HTTPException(status_code=500, detail="Failed to add/update holding")

@router.put("/holdings/{holding_id}", response_model=PortfolioHoldingResponse)
async def update_holding_by_id(
    holding_id: str,
    update_data: PortfolioHoldingUpdate,
    user_email: str = Query(..., description="User email for identification")
):
    """Update a specific holding by ID."""
    try:
        updated_holding = await portfolio_service.update_holding(user_email, holding_id, update_data)
        
        if not updated_holding:
            raise HTTPException(status_code=404, detail="Holding not found")
        
        response = portfolio_service.holding_to_response(updated_holding)
        logger.info(f"Updated holding {holding_id} for user {user_email}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating holding {holding_id} for {user_email}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update holding")

@router.put("/holdings/symbol/{symbol}", response_model=PortfolioHoldingResponse)
async def update_holding_by_symbol(
    symbol: str,
    update_data: PortfolioHoldingUpdate,
    user_email: str = Query(..., description="User email for identification")
):
    """Update a specific holding by symbol."""
    try:
        updated_holding = await portfolio_service.update_holding_by_symbol(user_email, symbol, update_data)
        
        if not updated_holding:
            raise HTTPException(status_code=404, detail=f"Holding for symbol {symbol.upper()} not found")
        
        response = portfolio_service.holding_to_response(updated_holding)
        logger.info(f"Updated holding {symbol} for user {user_email}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating holding {symbol} for {user_email}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update holding")

@router.delete("/holdings/{holding_id}")
async def delete_holding_by_id(
    holding_id: str,
    user_email: str = Query(..., description="User email for identification")
):
    """Delete a specific holding by ID."""
    try:
        success = await portfolio_service.delete_holding(user_email, holding_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Holding not found")
        
        logger.info(f"Deleted holding {holding_id} for user {user_email}")
        return {"message": "Holding deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting holding {holding_id} for {user_email}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete holding")

@router.delete("/holdings/symbol/{symbol}")
async def delete_holding_by_symbol(
    symbol: str,
    user_email: str = Query(..., description="User email for identification")
):
    """Delete a specific holding by symbol."""
    try:
        success = await portfolio_service.delete_holding_by_symbol(user_email, symbol)
        
        if not success:
            raise HTTPException(status_code=404, detail=f"Holding for symbol {symbol.upper()} not found")
        
        logger.info(f"Deleted holding {symbol} for user {user_email}")
        return {"message": f"Holding for {symbol.upper()} deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting holding {symbol} for {user_email}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete holding")

@router.get("/health/check")
async def check_portfolio_service_health():
    """Check if the portfolio service is healthy."""
    try:
        from .mongodb import check_mongodb_health
        
        mongodb_healthy = await check_mongodb_health()
        
        if not mongodb_healthy:
            raise HTTPException(status_code=503, detail="MongoDB connection unhealthy")
        
        return {
            "status": "healthy",
            "mongodb": "connected",
            "service": "portfolio_service",
            "collections": ["portfolio_holdings", "users"]
        }
        
    except Exception as e:
        logger.error(f"Portfolio service health check failed: {e}")
        raise HTTPException(status_code=503, detail="Portfolio service unhealthy")