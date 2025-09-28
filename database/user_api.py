"""
User API endpoints for user identification and management.
Provides REST APIs for user operations including identification, creation, and updates.
"""

import logging
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional

from .models import (
    UserIdentifyRequest, 
    UserIdentifyResponse, 
    UserResponse, 
    UserUpdate,
    UserActivityUpdate
)
from .user_service import user_service

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api/users", tags=["users"])

@router.post("/identify", response_model=UserIdentifyResponse)
async def identify_user(request: UserIdentifyRequest):
    """
    Identify a user by email. Creates the user if not found and auto_create is True.
    
    This is the main endpoint for the frontend to identify users.
    It will either return an existing user or create a new one.
    """
    try:
        logger.info(f"User identification request for: {request.email}")
        
        # Get or create user
        user, is_new_user = await user_service.get_or_create_user(
            email=request.email,
            name=request.name,
            phone_number=request.phone_number,
            auto_create=request.auto_create
        )
        
        # Convert to response model
        user_response = user_service.user_to_response(user)
        
        message = "New user created" if is_new_user else "Existing user found"
        logger.info(f"User identification successful for {request.email}: {message}")
        
        return UserIdentifyResponse(
            user=user_response,
            is_new_user=is_new_user,
            message=message
        )
        
    except ValueError as e:
        logger.warning(f"User identification failed for {request.email}: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error in user identification for {request.email}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error during user identification")

@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: str):
    """Get user by ID."""
    try:
        user = await user_service.find_user_by_id(user_id)
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        return user_service.user_to_response(user)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/email/{email}", response_model=UserResponse)
async def get_user_by_email(email: str):
    """Get user by email address."""
    try:
        user = await user_service.find_user_by_email(email)
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        return user_service.user_to_response(user)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user by email {email}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.put("/{user_id}", response_model=UserResponse)
async def update_user(user_id: str, update_data: UserUpdate):
    """Update user information."""
    try:
        updated_user = await user_service.update_user(user_id, update_data)
        
        if not updated_user:
            raise HTTPException(status_code=404, detail="User not found")
        
        logger.info(f"User {user_id} updated successfully")
        return user_service.user_to_response(updated_user)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/activity", response_model=dict)
async def update_user_activity(activity_update: UserActivityUpdate, email: str):
    """Update user activity timestamp."""
    try:
        success = await user_service.update_user_activity(
            email=email,
            activity_type=activity_update.activity_type,
            metadata=activity_update.metadata
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="User not found or activity not updated")
        
        return {"message": "User activity updated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating activity for user {email}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/health/check")
async def check_user_service_health():
    """Check if the user service and database connection are healthy."""
    try:
        from .mongodb import check_mongodb_health
        
        mongodb_healthy = await check_mongodb_health()
        
        if not mongodb_healthy:
            raise HTTPException(status_code=503, detail="MongoDB connection unhealthy")
        
        return {
            "status": "healthy",
            "mongodb": "connected",
            "service": "user_service",
            "timestamp": f"{logger.name}"
        }
        
    except Exception as e:
        logger.error(f"User service health check failed: {e}")
        raise HTTPException(status_code=503, detail="User service unhealthy")