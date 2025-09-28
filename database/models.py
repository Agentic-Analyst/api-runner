"""
Pydantic models for user-related operations.
Defines data structures for user management and authentication.
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Dict, Any, List
from datetime import datetime
from bson import ObjectId
from typing import Any, Annotated
from pydantic import BeforeValidator, PlainSerializer

def validate_object_id(v):
    """Validate ObjectId from various inputs."""
    if isinstance(v, ObjectId):
        return v
    if isinstance(v, str):
        if ObjectId.is_valid(v):
            return ObjectId(v)
        else:
            raise ValueError("Invalid ObjectId format")
    raise ValueError("ObjectId must be a valid ObjectId string or ObjectId instance")

PyObjectId = Annotated[
    ObjectId,
    BeforeValidator(validate_object_id),
    PlainSerializer(lambda x: str(x), return_type=str)
]

class UserBase(BaseModel):
    """Base user model with common fields."""
    email: EmailStr = Field(..., description="User email address")
    name: Optional[str] = Field(None, description="User full name")
    phone_number: Optional[str] = Field(None, description="User phone number")
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow, description="Account creation timestamp")
    last_active: Optional[datetime] = Field(None, description="Last activity timestamp")
    preferences: Optional[Dict[str, Any]] = Field(default_factory=dict, description="User preferences and settings")

class UserCreate(UserBase):
    """Model for creating a new user."""
    pass

class UserUpdate(BaseModel):
    """Model for updating user information."""
    name: Optional[str] = Field(None, description="User full name")
    phone_number: Optional[str] = Field(None, description="User phone number")
    last_active: Optional[datetime] = Field(None, description="Last activity timestamp")
    preferences: Optional[Dict[str, Any]] = Field(None, description="User preferences and settings")

class UserInDB(UserBase):
    """User model as stored in database."""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class UserResponse(BaseModel):
    """User model for API responses."""
    id: str = Field(..., description="User ID")
    email: str = Field(..., description="User email address")
    name: Optional[str] = Field(None, description="User full name")
    phone_number: Optional[str] = Field(None, description="User phone number")
    created_at: datetime = Field(..., description="Account creation timestamp")
    last_active: Optional[datetime] = Field(None, description="Last activity timestamp")
    preferences: Dict[str, Any] = Field(default_factory=dict, description="User preferences and settings")

class UserIdentifyRequest(BaseModel):
    """Model for user identification requests."""
    email: EmailStr = Field(..., description="User email to identify")
    name: Optional[str] = Field(None, description="User name (for new user creation)")
    phone_number: Optional[str] = Field(None, description="User phone number (for new user creation)")
    auto_create: bool = Field(default=True, description="Automatically create user if not found")

class UserIdentifyResponse(BaseModel):
    """Response model for user identification."""
    user: UserResponse = Field(..., description="User information")
    is_new_user: bool = Field(..., description="Whether this is a newly created user")
    message: str = Field(..., description="Response message")

class UserActivityUpdate(BaseModel):
    """Model for updating user activity."""
    activity_type: str = Field(..., description="Type of activity (login, job_start, etc.)")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional activity metadata")

# Portfolio Models
class PortfolioHoldingBase(BaseModel):
    """Base model for portfolio holdings."""
    symbol: str = Field(..., description="Stock symbol (e.g., AAPL)")
    name: str = Field(..., description="Company name (e.g., Apple Inc.)")
    shares: float = Field(..., ge=0, description="Number of shares owned")
    cost_basis: float = Field(..., ge=0, description="Average cost per share")

class PortfolioHoldingCreate(PortfolioHoldingBase):
    """Model for creating a new portfolio holding."""
    pass

class PortfolioHoldingUpdate(BaseModel):
    """Model for updating portfolio holdings."""
    name: Optional[str] = Field(None, description="Company name")
    shares: Optional[float] = Field(None, ge=0, description="Number of shares owned")
    cost_basis: Optional[float] = Field(None, ge=0, description="Average cost per share")

class PortfolioHoldingInDB(PortfolioHoldingBase):
    """Portfolio holding model as stored in database."""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    user_id: str = Field(..., description="User ID who owns this holding")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="Last update timestamp")
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class PortfolioHoldingResponse(BaseModel):
    """Portfolio holding model for API responses."""
    id: str = Field(..., description="Holding ID")
    symbol: str = Field(..., description="Stock symbol")
    name: str = Field(..., description="Company name")
    shares: float = Field(..., description="Number of shares owned")
    cost_basis: float = Field(..., description="Average cost per share")
    total_value: float = Field(..., description="Total value (shares * cost_basis)")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

class PortfolioSummary(BaseModel):
    """Portfolio summary statistics."""
    total_holdings: int = Field(..., description="Total number of holdings")
    total_value: float = Field(..., description="Total portfolio value")
    total_shares: float = Field(..., description="Total shares across all holdings")
    holdings: List[PortfolioHoldingResponse] = Field(..., description="List of all holdings")

class PortfolioRequest(BaseModel):
    """Request model for portfolio operations requiring user identification."""
    user_email: EmailStr = Field(..., description="User email for identification")

class PortfolioHoldingRequest(PortfolioHoldingCreate):
    """Request model for creating/updating holdings with user identification."""
    user_email: EmailStr = Field(..., description="User email for identification")