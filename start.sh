#!/bin/bash

# Development startup script

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
    echo "Activated virtual environment"
fi

# Load environment variables from .env file if it exists
if [ -f ".env" ]; then
    export $(cat .env | grep -v '^#' | xargs)
    echo "Loaded environment variables from .env file"
else
    echo "Warning: .env file not found. Copy .env.example to .env and configure your API keys."
fi

# Set default environment variables
export BACKEND_IMAGE="${BACKEND_IMAGE:-stock-analyst:latest}"
export DATA_VOLUME="${DATA_VOLUME:-stockdata}"

echo "Starting API Runner with:"
echo "  Backend Image: $BACKEND_IMAGE"
echo "  Data Volume: $DATA_VOLUME"
echo "  Port: 8080"
echo "  SERPAPI_API_KEY: ${SERPAPI_API_KEY:+configured}"
echo "  OPENAI_API_KEY: ${OPENAI_API_KEY:+configured}"

# Check if API keys are set
if [ -z "$SERPAPI_API_KEY" ]; then
    echo "Warning: SERPAPI_API_KEY is not set"
fi

if [ -z "$OPENAI_API_KEY" ]; then
    echo "Warning: OPENAI_API_KEY is not set"
fi

# Create Docker volume if it doesn't exist
docker volume create $DATA_VOLUME 2>/dev/null || true

# Start the application
uvicorn main:app --reload --host 0.0.0.0 --port 8080
