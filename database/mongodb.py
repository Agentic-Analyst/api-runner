"""
MongoDB connection and configuration module.
Handles database connection setup and provides shared database client.
"""

import os
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional

logger = logging.getLogger(__name__)

# Global MongoDB client
mongo_client: Optional[AsyncIOMotorClient] = None
database = None

# MongoDB configuration
MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME")

async def init_mongodb():
    """Initialize MongoDB connection."""
    global mongo_client, database
    
    try:
        logger.info("🔌 Connecting to MongoDB...")
        mongo_client = AsyncIOMotorClient(MONGO_URI)
        
        # Test the connection
        await mongo_client.admin.command('ping')
        
        # Get the database
        database = mongo_client[DATABASE_NAME]
        
        logger.info(f"✅ MongoDB connected successfully to database: {DATABASE_NAME}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to connect to MongoDB: {e}")
        mongo_client = None
        database = None
        return False

async def close_mongodb():
    """Close MongoDB connection."""
    global mongo_client
    
    if mongo_client:
        mongo_client.close()
        logger.info("🔌 MongoDB connection closed")

def get_database():
    """Get the MongoDB database instance."""
    if database is None:
        raise RuntimeError("MongoDB not initialized. Call init_mongodb() first.")
    return database

def get_collection(name: str):
    """Get a specific collection from the database."""
    db = get_database()
    return db[name]

# Health check function
async def check_mongodb_health():
    """Check if MongoDB connection is healthy."""
    try:
        if mongo_client is None:
            return False
        
        # Ping the database
        await mongo_client.admin.command('ping')
        return True
        
    except Exception as e:
        logger.error(f"MongoDB health check failed: {e}")
        return False