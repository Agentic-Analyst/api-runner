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

### Docker

Build and run with Docker:
```bash
# Create .env file with your API keys first
cp .env.example .env
# Edit .env file

# Then build and run
docker ps -a | grep api-runner
docker stop api-runner && docker rm api-runner
docker build -t stock-analyst-runner . && echo "Build completed successfully"
docker run -d -p 8080:8080 -v /var/run/docker.sock:/var/run/docker.sock --env-file .env --name api-runner stock-analyst-runner
```

### Docker Compose (Recommended)

```bash
# Create .env file with your API keys first
cp .env.example .env
# Edit .env file

# Create the external volume
docker volume create stockdata

# Start all services
docker-compose up --build
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

- `BACKEND_IMAGE`: Docker image to run for analysis jobs (default: "stock-analyst:latest")
- `DATA_VOLUME`: Docker volume name for persistent data (default: "stockdata")
- `SERPAPI_API_KEY`: **Required** - API key for SerpApi web search
- `OPENAI_API_KEY`: **Required** - API key for OpenAI GPT models
