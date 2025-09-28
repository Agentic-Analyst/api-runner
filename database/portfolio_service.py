"""
Portfolio service layer for database operations.
Handles all portfolio-related database interactions and business logic.
"""

import logging
from datetime import datetime
from typing import Optional, List
from bson import ObjectId

from .mongodb import get_collection
from .models import (
    PortfolioHoldingInDB, 
    PortfolioHoldingCreate, 
    PortfolioHoldingUpdate, 
    PortfolioHoldingResponse,
    PortfolioSummary
)
from .user_service import user_service

logger = logging.getLogger(__name__)

class PortfolioService:
    """Service class for portfolio-related operations."""
    
    def __init__(self):
        self.collection_name = "portfolio_holdings"
    
    def _get_collection(self):
        """Get the portfolio holdings collection."""
        return get_collection(self.collection_name)
    
    async def _ensure_user_exists(self, user_email: str) -> str:
        """Ensure user exists and return user ID."""
        user, _ = await user_service.get_or_create_user(user_email, auto_create=True)
        return str(user.id)
    
    async def get_user_holdings(self, user_email: str) -> List[PortfolioHoldingInDB]:
        """Get all portfolio holdings for a user."""
        try:
            user_id = await self._ensure_user_exists(user_email)
            collection = self._get_collection()
            
            cursor = collection.find({"user_id": user_id})
            holdings = []
            
            async for holding_doc in cursor:
                holdings.append(PortfolioHoldingInDB(**holding_doc))
            
            logger.info(f"Retrieved {len(holdings)} holdings for user {user_email}")
            return holdings
            
        except Exception as e:
            logger.error(f"Error getting holdings for user {user_email}: {e}")
            raise
    
    async def get_holding_by_symbol(self, user_email: str, symbol: str) -> Optional[PortfolioHoldingInDB]:
        """Get a specific holding by symbol for a user."""
        try:
            user_id = await self._ensure_user_exists(user_email)
            collection = self._get_collection()
            
            holding_doc = await collection.find_one({
                "user_id": user_id,
                "symbol": symbol.upper()
            })
            
            if holding_doc:
                return PortfolioHoldingInDB(**holding_doc)
            return None
            
        except Exception as e:
            logger.error(f"Error getting holding {symbol} for user {user_email}: {e}")
            raise
    
    async def get_holding_by_id(self, user_email: str, holding_id: str) -> Optional[PortfolioHoldingInDB]:
        """Get a specific holding by ID for a user."""
        try:
            user_id = await self._ensure_user_exists(user_email)
            collection = self._get_collection()
            
            if not ObjectId.is_valid(holding_id):
                return None
            
            holding_doc = await collection.find_one({
                "_id": ObjectId(holding_id),
                "user_id": user_id
            })
            
            if holding_doc:
                return PortfolioHoldingInDB(**holding_doc)
            return None
            
        except Exception as e:
            logger.error(f"Error getting holding {holding_id} for user {user_email}: {e}")
            raise
    
    async def create_holding(self, user_email: str, holding_data: PortfolioHoldingCreate) -> PortfolioHoldingInDB:
        """Create a new portfolio holding."""
        try:
            user_id = await self._ensure_user_exists(user_email)
            collection = self._get_collection()
            
            # Check if holding with same symbol already exists
            existing_holding = await self.get_holding_by_symbol(user_email, holding_data.symbol)
            if existing_holding:
                raise ValueError(f"Holding for symbol {holding_data.symbol} already exists. Use update instead.")
            
            # Create holding document
            holding_dict = holding_data.dict()
            holding_dict["user_id"] = user_id
            holding_dict["symbol"] = holding_dict["symbol"].upper()  # Normalize symbol
            holding_dict["created_at"] = datetime.utcnow()
            holding_dict["updated_at"] = datetime.utcnow()
            
            # Insert into database
            result = await collection.insert_one(holding_dict)
            holding_dict["_id"] = result.inserted_id
            
            logger.info(f"Created holding {holding_data.symbol} for user {user_email}")
            return PortfolioHoldingInDB(**holding_dict)
            
        except Exception as e:
            logger.error(f"Error creating holding {holding_data.symbol} for user {user_email}: {e}")
            raise
    
    async def update_holding(self, user_email: str, holding_id: str, update_data: PortfolioHoldingUpdate) -> Optional[PortfolioHoldingInDB]:
        """Update an existing portfolio holding."""
        try:
            user_id = await self._ensure_user_exists(user_email)
            collection = self._get_collection()
            
            if not ObjectId.is_valid(holding_id):
                return None
            
            # Prepare update document (only include non-None fields)
            update_dict = {k: v for k, v in update_data.dict().items() if v is not None}
            
            if not update_dict:
                # No updates to make
                return await self.get_holding_by_id(user_email, holding_id)
            
            # Add updated timestamp
            update_dict["updated_at"] = datetime.utcnow()
            
            # Perform update
            result = await collection.update_one(
                {"_id": ObjectId(holding_id), "user_id": user_id},
                {"$set": update_dict}
            )
            
            if result.modified_count > 0:
                logger.info(f"Updated holding {holding_id} for user {user_email}")
                return await self.get_holding_by_id(user_email, holding_id)
            
            # Return existing holding even if no changes
            return await self.get_holding_by_id(user_email, holding_id)
            
        except Exception as e:
            logger.error(f"Error updating holding {holding_id} for user {user_email}: {e}")
            raise
    
    async def update_holding_by_symbol(self, user_email: str, symbol: str, update_data: PortfolioHoldingUpdate) -> Optional[PortfolioHoldingInDB]:
        """Update a holding by symbol."""
        try:
            existing_holding = await self.get_holding_by_symbol(user_email, symbol)
            if not existing_holding:
                return None
            
            return await self.update_holding(user_email, str(existing_holding.id), update_data)
            
        except Exception as e:
            logger.error(f"Error updating holding {symbol} for user {user_email}: {e}")
            raise
    
    async def delete_holding(self, user_email: str, holding_id: str) -> bool:
        """Delete a portfolio holding."""
        try:
            user_id = await self._ensure_user_exists(user_email)
            collection = self._get_collection()
            
            if not ObjectId.is_valid(holding_id):
                return False
            
            result = await collection.delete_one({
                "_id": ObjectId(holding_id),
                "user_id": user_id
            })
            
            if result.deleted_count > 0:
                logger.info(f"Deleted holding {holding_id} for user {user_email}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error deleting holding {holding_id} for user {user_email}: {e}")
            raise
    
    async def delete_holding_by_symbol(self, user_email: str, symbol: str) -> bool:
        """Delete a holding by symbol."""
        try:
            existing_holding = await self.get_holding_by_symbol(user_email, symbol)
            if not existing_holding:
                return False
            
            return await self.delete_holding(user_email, str(existing_holding.id))
            
        except Exception as e:
            logger.error(f"Error deleting holding {symbol} for user {user_email}: {e}")
            raise
    
    async def add_or_update_holding(self, user_email: str, holding_data: PortfolioHoldingCreate) -> tuple[PortfolioHoldingInDB, bool]:
        """
        Add new holding or update existing one (merge shares and recalculate cost basis).
        Returns (holding, is_new_holding).
        """
        try:
            existing_holding = await self.get_holding_by_symbol(user_email, holding_data.symbol)
            
            if existing_holding:
                # Calculate new weighted average cost basis
                existing_value = existing_holding.shares * existing_holding.cost_basis
                new_value = holding_data.shares * holding_data.cost_basis
                total_shares = existing_holding.shares + holding_data.shares
                total_value = existing_value + new_value
                
                new_cost_basis = total_value / total_shares if total_shares > 0 else 0
                
                # Update the holding
                update_data = PortfolioHoldingUpdate(
                    name=holding_data.name,  # Update name in case it changed
                    shares=total_shares,
                    cost_basis=new_cost_basis
                )
                
                updated_holding = await self.update_holding(user_email, str(existing_holding.id), update_data)
                logger.info(f"Merged holding {holding_data.symbol} for user {user_email}: {holding_data.shares} shares added")
                return updated_holding, False
            else:
                # Create new holding
                new_holding = await self.create_holding(user_email, holding_data)
                return new_holding, True
                
        except Exception as e:
            logger.error(f"Error adding/updating holding {holding_data.symbol} for user {user_email}: {e}")
            raise
    
    def holding_to_response(self, holding: PortfolioHoldingInDB) -> PortfolioHoldingResponse:
        """Convert PortfolioHoldingInDB to PortfolioHoldingResponse."""
        total_value = holding.shares * holding.cost_basis
        
        return PortfolioHoldingResponse(
            id=str(holding.id),
            symbol=holding.symbol,
            name=holding.name,
            shares=holding.shares,
            cost_basis=holding.cost_basis,
            total_value=total_value,
            created_at=holding.created_at,
            updated_at=holding.updated_at
        )
    
    async def get_portfolio_summary(self, user_email: str) -> PortfolioSummary:
        """Get portfolio summary with all holdings and statistics."""
        try:
            holdings = await self.get_user_holdings(user_email)
            
            # Convert to response models and calculate totals
            holding_responses = []
            total_value = 0.0
            total_shares = 0.0
            
            for holding in holdings:
                response = self.holding_to_response(holding)
                holding_responses.append(response)
                total_value += response.total_value
                total_shares += response.shares
            
            return PortfolioSummary(
                total_holdings=len(holding_responses),
                total_value=total_value,
                total_shares=total_shares,
                holdings=holding_responses
            )
            
        except Exception as e:
            logger.error(f"Error getting portfolio summary for user {user_email}: {e}")
            raise

# Global service instance
portfolio_service = PortfolioService()