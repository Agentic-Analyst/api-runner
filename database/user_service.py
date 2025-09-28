"""
User service layer for database operations.
Handles all user-related database interactions and business logic.
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any
from bson import ObjectId

from .mongodb import get_collection
from .models import UserInDB, UserCreate, UserUpdate, UserResponse

logger = logging.getLogger(__name__)

class UserService:
    """Service class for user-related operations."""
    
    def __init__(self):
        self.collection_name = "users"
    
    def _get_collection(self):
        """Get the users collection."""
        return get_collection(self.collection_name)
    
    async def find_user_by_email(self, email: str) -> Optional[UserInDB]:
        """Find a user by email address."""
        try:
            collection = self._get_collection()
            user_doc = await collection.find_one({"email": email})
            
            if user_doc:
                return UserInDB(**user_doc)
            return None
            
        except Exception as e:
            logger.error(f"Error finding user by email {email}: {e}")
            raise
    
    async def find_user_by_id(self, user_id: str) -> Optional[UserInDB]:
        """Find a user by ID."""
        try:
            collection = self._get_collection()
            
            if not ObjectId.is_valid(user_id):
                return None
                
            user_doc = await collection.find_one({"_id": ObjectId(user_id)})
            
            if user_doc:
                return UserInDB(**user_doc)
            return None
            
        except Exception as e:
            logger.error(f"Error finding user by ID {user_id}: {e}")
            raise
    
    async def create_user(self, user_data: UserCreate) -> UserInDB:
        """Create a new user."""
        try:
            collection = self._get_collection()
            
            # Check if user already exists
            existing_user = await self.find_user_by_email(user_data.email)
            if existing_user:
                raise ValueError(f"User with email {user_data.email} already exists")
            
            # Create user document
            user_dict = user_data.dict()
            user_dict["created_at"] = datetime.utcnow()
            user_dict["last_active"] = datetime.utcnow()
            
            # Insert into database
            result = await collection.insert_one(user_dict)
            user_dict["_id"] = result.inserted_id
            
            logger.info(f"Created new user: {user_data.email}")
            return UserInDB(**user_dict)
            
        except Exception as e:
            logger.error(f"Error creating user {user_data.email}: {e}")
            raise
    
    async def update_user(self, user_id: str, update_data: UserUpdate) -> Optional[UserInDB]:
        """Update user information."""
        try:
            collection = self._get_collection()
            
            if not ObjectId.is_valid(user_id):
                return None
            
            # Prepare update document (only include non-None fields)
            update_dict = {k: v for k, v in update_data.dict().items() if v is not None}
            
            if not update_dict:
                # No updates to make
                return await self.find_user_by_id(user_id)
            
            # Perform update
            result = await collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": update_dict}
            )
            
            if result.modified_count > 0:
                logger.info(f"Updated user {user_id}")
                return await self.find_user_by_id(user_id)
            
            return await self.find_user_by_id(user_id)
            
        except Exception as e:
            logger.error(f"Error updating user {user_id}: {e}")
            raise
    
    async def update_user_activity(self, email: str, activity_type: str = "general", metadata: Optional[Dict[str, Any]] = None) -> bool:
        """Update user's last activity timestamp."""
        try:
            collection = self._get_collection()
            
            update_doc = {
                "last_active": datetime.utcnow()
            }
            
            # Add activity metadata if provided
            if metadata:
                update_doc[f"last_{activity_type}_activity"] = datetime.utcnow()
                update_doc[f"last_{activity_type}_metadata"] = metadata
            
            result = await collection.update_one(
                {"email": email},
                {"$set": update_doc}
            )
            
            return result.modified_count > 0
            
        except Exception as e:
            logger.error(f"Error updating activity for user {email}: {e}")
            return False
    
    async def get_or_create_user(self, email: str, name: Optional[str] = None, phone_number: Optional[str] = None, auto_create: bool = True) -> tuple[UserInDB, bool]:
        """
        Get existing user or create new one if not found.
        Returns (user, is_new_user).
        """
        try:
            # Try to find existing user
            existing_user = await self.find_user_by_email(email)
            
            if existing_user:
                # Update last activity
                await self.update_user_activity(email, "login")
                return existing_user, False
            
            # User not found
            if not auto_create:
                raise ValueError(f"User with email {email} not found and auto_create is disabled")
            
            # Create new user
            user_create = UserCreate(
                email=email,
                name=name or email.split("@")[0],  # Use email prefix as default name
                phone_number=phone_number,
                preferences={}
            )
            
            new_user = await self.create_user(user_create)
            return new_user, True
            
        except Exception as e:
            logger.error(f"Error in get_or_create_user for {email}: {e}")
            raise
    
    def user_to_response(self, user: UserInDB) -> UserResponse:
        """Convert UserInDB to UserResponse."""
        return UserResponse(
            id=str(user.id),
            email=user.email,
            name=user.name,
            phone_number=user.phone_number,
            created_at=user.created_at,
            last_active=user.last_active,
            preferences=user.preferences or {}
        )

# Global service instance
user_service = UserService()