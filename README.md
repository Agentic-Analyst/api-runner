# API Runner

A FastAPI service to manage and run stock analysis jobs using Docker containers.

## Setup

### Prerequisites

You'll need API keys for the stock analyst backend:
- **SERPAPI_API_KEY**: Get from [SerpApi](https://serpapi.com/)
- **OPENAI_API_KEY**: Get from [OpenAI](https://platform.openai.com/api-keys)

### Local Development

1. Create a virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # On macOS/Linux
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up environment variables:
```bash
cp .env.example .env
# Edit .env file and add your API keys
```

4. Run the application:
```bash
./start.sh
```

Or manually:
```bash
export SERPAPI_API_KEY="your_serpapi_key"
export OPENAI_API_KEY="your_openai_key"
uvicorn main:app --reload --host 0.0.0.0 --port 8080
```

### Docker Setup and Management

This application uses a multi-container Docker architecture:
- **API Runner Container**: FastAPI service (this repository)  
- **Backend Analysis Container**: Stock analysis engine (`stock-analyst:latest` image)
- **Data Volume**: Persistent storage for analysis results (`stockdata` volume)

#### Initial Setup

1. **Create environment file:**
```bash
cp .env.example .env
# Edit .env file and add your API keys:
# SERPAPI_API_KEY=your_serpapi_key_here
# OPENAI_API_KEY=your_openai_key_here
```

2. **Create the data volume:**
```bash
# This volume persists analysis results across container restarts
docker volume create stockdata
```

3. **Ensure backend image exists:**
```bash
# Check if the stock analysis backend image is available
docker images | grep stock-analyst

# If not available, you need to build or pull the stock-analyst image first
# This image should contain the actual stock analysis pipeline
```

#### Building and Running the API Runner

**Option 1: Docker Build (Recommended for development)**
```bash
# Clean up any existing containers
docker ps -a | grep api-runner
docker stop api-runner 2>/dev/null && docker rm api-runner 2>/dev/null

# Build and run the API runner
docker build -t stock-analyst-runner . && echo "Build completed successfully"
docker run -d \
  -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v stockdata:/data \
  --env-file .env \
  --name api-runner \
  stock-analyst-runner
```

**Option 2: Docker Compose (Recommended for production)**
```bash
# Create .env file first (as above)
cp .env.example .env

# Start all services
docker-compose up --build -d

# View logs
docker-compose logs -f api-runner
```

#### Rebuilding After Code Changes

When you make changes to this repository, rebuild and restart:

```bash
# Stop the current container
docker stop api-runner && docker rm api-runner

# Rebuild with no cache to ensure fresh build
docker build --no-cache -t stock-analyst-runner .

# Restart the container
docker run -d \
  -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v stockdata:/data \
  --env-file .env \
  --name api-runner \
  stock-analyst-runner
```

Or with Docker Compose:
```bash
# Rebuild and restart
docker-compose down
docker-compose up --build -d
```

#### Docker Architecture Details

**Volume Mounting:**
- `/var/run/docker.sock` - Allows API runner to control Docker daemon
- `stockdata:/data` - Persistent data storage for analysis results
- Backend containers mount `stockdata:/data` for reading/writing analysis files

**Network Flow:**
1. API Runner receives job requests
2. API Runner spawns Backend Analysis containers with shared volume
3. Backend containers write results to `/data/{TICKER}/` directory
4. API Runner reads results from volume and serves downloads

**Data Structure in Volume:**
```
stockdata/
├── NVDA/                    # One folder per ticker
│   ├── info.log            # Real-time analysis logs
│   ├── screening_report.md  # LLM analysis report
│   ├── screening_data.json  # Structured analysis data
│   ├── searched/           # Raw scraped articles
│   │   ├── article_1.md
│   │   └── article_2.md
│   └── filtered/           # Filtered articles
│       ├── filtered_1.md
│       ├── filtered_2.md
│       └── filtered_articles_index.csv
└── AAPL/                   # Another ticker
    └── ...
```

## API Endpoints

### Core Endpoints
- `GET /` - Basic health check
- `GET /health` - Detailed health check with Docker status
- `POST /run` - Start a new stock analysis job with customizable parameters
- `GET /jobs` - List all jobs
- `GET /jobs/{job_id}` - Get job status and results
- `GET /jobs/{job_id}/status/detailed` - Get detailed job status with progress metrics

### Real-time Monitoring
- `GET /jobs/{job_id}/logs` - Get detailed job logs for debugging
- `GET /jobs/{job_id}/logs/stream` - Stream real-time job logs (Server-Sent Events)
- `GET /jobs/{job_id}/info-log` - Get the complete info.log file content
- `GET /jobs/{job_id}/files/{filename}` - Download output files (info.log, screening_report.md, etc.)

### Download Endpoints
- `GET /jobs/{job_id}/download/searched-articles` - Download scraped articles as ZIP
- `GET /jobs/{job_id}/download/filtered-articles` - Download filtered articles as ZIP
- `GET /jobs/{job_id}/download/screening-report` - Download LLM analysis report
- `GET /jobs/{job_id}/download/all-results` - Download complete analysis results as ZIP
- `GET /jobs/{job_id}/files` - List available files and counts for a job

### Job Parameters

When starting a new analysis job via `POST /run`, you can customize the analysis with these parameters:

**Required:**
- `ticker` (string): Stock ticker symbol (e.g., "NVDA", "AAPL", "TSLA")

**Optional:**
- `company` (string): Company name (if not provided, will be inferred from ticker)
- `query` (string): Custom search query for finding articles
- `pipeline` (string): Analysis pipeline type:
  - `"full"` - Complete analysis (scrape → filter → LLM analysis)
  - `"scrape-only"` - Only scrape articles
  - `"filter-only"` - Scrape and filter articles
  - `"screen-only"` - Only run LLM analysis on existing data
  - `"filter-screen"` - Filter and run LLM analysis

**Advanced Configuration:**
- `max_articles` (integer): Maximum articles to scrape (default: 20)
- `min_score` (float): Minimum relevance score for filtering (default: 3.0)
- `max_filtered` (integer): Maximum filtered articles to keep (default: 10)
- `min_confidence` (float): Minimum confidence for LLM insights (default: 0.5)

### Example Usage

Start a basic job:
```bash
curl -X POST "http://localhost:8080/run" \
  -H "Content-Type: application/json" \
  -d '{"ticker": "NVDA", "company": "NVIDIA Corporation", "pipeline": "full"}'
```

Start a customized job with advanced parameters:
```bash
curl -X POST "http://localhost:8080/run" \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "AAPL",
    "company": "Apple Inc",
    "pipeline": "full",
    "max_articles": 30,
    "min_score": 4.0,
    "max_filtered": 15,
    "min_confidence": 0.7,
    "query": "Apple iPhone sales Q4 earnings"
  }'
```

Start a scrape-only job for data collection:
```bash
curl -X POST "http://localhost:8080/run" \
  -H "Content-Type: application/json" \
  -d '{"ticker": "TSLA", "pipeline": "scrape-only", "max_articles": 50}'
```

Stream real-time logs:
```bash
curl -N "http://localhost:8080/jobs/NVDA_20250803_163401/logs/stream"
```

Get job status:
```bash
curl "http://localhost:8080/jobs/NVDA_20250803_163401" | jq .
```

## Key Features

### Advanced Job Customization
The API now supports fine-grained control over analysis jobs with parameters for:
- **Pipeline Selection**: Choose specific analysis stages (scrape-only, filter-only, full, etc.)
- **Article Limits**: Control maximum articles scraped and filtered
- **Quality Thresholds**: Set minimum relevance scores and confidence levels
- **Custom Queries**: Use specific search terms for targeted article discovery

### Real-time Log Streaming
The API supports real-time streaming of container logs using Server-Sent Events (SSE). This allows frontends to:
- Monitor job progress in real-time
- Display live log updates
- Detect completion status immediately
- Handle errors and warnings as they occur

### LLM Timeout Handling
The API now intelligently handles LLM analysis timeouts:
- **Smart Status Detection**: Distinguishes between LLM timeouts and actual failures
- **Frontend Friendly**: `llm_timeout` status allows frontend to show retry options instead of complete failure
- **Progress Preservation**: Shows 75% progress when LLM timeout occurs (reached analysis stage)
- **Actionable Messages**: Provides clear "retrying may help" guidance to users

### Comprehensive Download Options
Multiple download formats for analysis results:
- Individual file downloads (logs, reports, data)
- Organized ZIP packages for article collections
- Complete analysis packages with all results

## Job Status Types

The API returns different status values to help frontends handle various scenarios:

- **`pending`** - Job is queued and waiting to start
- **`running`** - Job is actively running
- **`completed`** - Job finished successfully with results
- **`failed`** - Job failed due to configuration issues, data errors, etc.
- **`llm_timeout`** - Job timed out during LLM analysis phase (retryable)

## Troubleshooting

### Docker-Related Issues

**Issue: "Docker client not available" error**
```bash
# Check Docker daemon status
docker --version
docker ps

# Ensure Docker socket is accessible
ls -la /var/run/docker.sock

# If using Docker Desktop on macOS, ensure it's running
```

**Issue: "stock-analyst:latest" image not found**
```bash
# Check available images
docker images | grep stock-analyst

# If missing, you need to build or obtain the backend analysis image
# This is a separate repository containing the actual stock analysis pipeline
```

**Issue: Volume permission errors**
```bash
# Recreate the data volume
docker volume rm stockdata
docker volume create stockdata

# Check volume details
docker volume inspect stockdata
```

**Issue: Container fails to start analysis jobs**
```bash
# Check API runner logs
docker logs api-runner

# Verify environment variables are loaded
docker exec api-runner env | grep -E "(SERPAPI|OPENAI)"

# Test Docker-in-Docker functionality
docker exec api-runner docker ps
```

**Issue: Port already in use**
```bash
# Find what's using port 8080
lsof -i :8080

# Kill existing processes
pkill -f "python.*main.py"

# Or use different port
docker run -p 8081:8080 ... stock-analyst-runner
```

**Issue: Analysis results not persisting**
```bash
# Verify volume mount
docker inspect api-runner | grep -A 5 "Mounts"

# Check volume contents
docker run --rm -v stockdata:/data alpine ls -la /data/

# Verify backend containers can write to volume
docker run --rm -v stockdata:/data alpine touch /data/test.txt
```

### Development and Debugging

**View real-time logs:**
```bash
# API Runner logs
docker logs -f api-runner

# Or with Docker Compose
docker-compose logs -f api-runner

# Analysis job logs (when available)
curl "http://localhost:8080/jobs/NVDA_20250809_221954/logs/stream"
```

**Access container shell:**
```bash
# Access API runner container
docker exec -it api-runner bash

# Inspect data volume contents
docker run --rm -it -v stockdata:/data alpine sh
ls -la /data/
```

**Clean slate restart:**
```bash
# Stop all containers and clean up
docker-compose down
docker stop api-runner 2>/dev/null && docker rm api-runner 2>/dev/null

# Remove old images (optional)
docker rmi stock-analyst-runner 2>/dev/null

# Remove data volume (WARNING: deletes all analysis results)
docker volume rm stockdata 2>/dev/null

# Start fresh
docker volume create stockdata
docker-compose up --build
```

### Common Issues

**LLM Timeouts**: If you encounter `llm_timeout` status, this usually means:
- The LLM API is experiencing high load
- The analysis is processing a large number of articles
- Network connectivity issues to the LLM provider

**Solution**: Retry the job or reduce `max_filtered` parameter to process fewer articles.

**Empty Results**: If jobs complete but produce no articles:
- Check if the company name and ticker are correct
- Try a more specific `query` parameter
- Lower the `min_score` threshold for filtering

**Container Issues**: If jobs fail immediately:
- Ensure Docker is running and accessible
- Check that the `stock-analyst:latest` backend image exists
- Verify API keys are properly configured

## Environment Variables

### Required Configuration
- `SERPAPI_API_KEY`: **Required** - API key for SerpApi web search service
  - Get from: https://serpapi.com/
  - Used by backend analysis containers for web scraping
  
- `OPENAI_API_KEY`: **Required** - API key for OpenAI GPT models  
  - Get from: https://platform.openai.com/api-keys
  - Used by backend analysis containers for LLM analysis

### Docker Configuration
- `BACKEND_IMAGE`: Docker image for analysis jobs (default: `stock-analyst:latest`)
  - This should be the Docker image containing the stock analysis pipeline
  - Must be available locally or in a accessible registry
  
- `DATA_VOLUME`: Docker volume name for persistent data (default: `stockdata`)
  - Volume where analysis results are stored
  - Shared between API runner and backend analysis containers

### Example .env File
```bash
# Required API Keys
SERPAPI_API_KEY=your_serpapi_key_here
OPENAI_API_KEY=sk-your_openai_key_here

# Optional Docker Configuration
BACKEND_IMAGE=stock-analyst:latest
DATA_VOLUME=stockdata
```

### Configuration Validation

Check if your configuration is correct:
```bash
# Test API runner health
curl http://localhost:8080/health

# Expected response:
{
  "status": "healthy",
  "docker": "connected",
  "backend_image": "stock-analyst:latest", 
  "data_volume": "stockdata",
  "api_keys_configured": {
    "serpapi": true,
    "openai": true
  }
}
```

## Quick Reference

### Essential Docker Commands

```bash
# Initial setup (run once)
docker volume create stockdata
cp .env.example .env  # Edit with your API keys

# Daily development workflow
docker stop api-runner && docker rm api-runner          # Stop current
docker build -t stock-analyst-runner .                  # Rebuild 
docker run -d -p 8080:8080 \                           # Start fresh
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v stockdata:/data \
  --env-file .env --name api-runner stock-analyst-runner

# Check status
curl http://localhost:8080/health                       # API health
docker logs api-runner                                  # View logs
docker exec api-runner docker ps                       # Test Docker access
```

### Analysis Job Workflow

```bash
# Start analysis
curl -X POST http://localhost:8080/run \
  -H "Content-Type: application/json" \
  -d '{"ticker": "NVDA", "pipeline": "full"}'

# Monitor progress  
curl http://localhost:8080/jobs/NVDA_20250809_221954
curl -N http://localhost:8080/jobs/NVDA_20250809_221954/logs/stream

# Download results
curl http://localhost:8080/jobs/NVDA_20250809_221954/download/screening-report \
  -o nvda_report.md
```

### Data Volume Management

```bash
# Inspect volume contents
docker run --rm -v stockdata:/data alpine ls -la /data/

# Backup analysis results
docker run --rm -v stockdata:/data -v $(pwd):/backup alpine \
  tar czf /backup/stockdata_backup.tar.gz -C /data .

# Restore from backup
docker run --rm -v stockdata:/data -v $(pwd):/backup alpine \
  tar xzf /backup/stockdata_backup.tar.gz -C /data
```
