"""
Shared configuration for the API Runner
Prevents circular imports by centralizing environment variables
"""

import os
from dotenv import load_dotenv

# Load environment variables first
load_dotenv()

# Docker configuration
BACKEND_IMAGE = os.getenv("BACKEND_IMAGE", "stock-analyst:latest")
DATA_VOLUME = os.getenv("DATA_VOLUME", "stockdata")

# API Keys
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Database
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB")

# Frontend/Session configuration
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "")
ROOT_PATH = os.getenv("ROOT_PATH")
SESSION_SECRET = os.getenv("SESSION_SECRET")
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true"
SESSION_STORE_COOKIE = os.getenv("SESSION_STORE_COOKIE")
SESSION_SAMESITE = os.getenv("SESSION_SAMESITE", "lax").lower()

# Helper to parse CSV env vars
def csv_env(name: str, default: str = "") -> list:
    raw = os.getenv(name, default)
    vals = [v.strip() for v in raw.split(",") if v.strip()]
    return vals
