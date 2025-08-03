#!/bin/bash

# Build script for stock-analyst-runner Docker image

echo "Building stock-analyst-runner Docker image..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "Error: Docker is not running. Please start Docker and try again."
    exit 1
fi

# Build the image
echo "Building image: stock-analyst-runner:latest"
docker build -t stock-analyst-runner:latest .

if [ $? -eq 0 ]; then
    echo "✅ Successfully built stock-analyst-runner:latest"
    
    # Show the image
    echo "Image details:"
    docker images stock-analyst-runner:latest
    
    echo ""
    echo "🚀 You can now run the image with:"
    echo "   docker run -d -p 8080:8080 -v /var/run/docker.sock:/var/run/docker.sock --env-file .env stock-analyst-runner:latest"
    echo ""
    echo "Or use Docker Compose:"
    echo "   docker-compose up -d"
    
else
    echo "❌ Failed to build stock-analyst-runner image"
    exit 1
fi
