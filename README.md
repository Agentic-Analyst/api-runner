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
- `POST /run` - Start a new stock analysis job
- `GET /jobs` - List all jobs
- `GET /jobs/{job_id}` - Get job status and results

### Real-time Monitoring
- `GET /jobs/{job_id}/logs` - Get detailed job logs for debugging
- `GET /jobs/{job_id}/logs/stream` - **NEW!** Stream real-time job logs (Server-Sent Events)
- `GET /jobs/{job_id}/info-log` - **NEW!** Get the complete info.log file content
- `GET /jobs/{job_id}/files/{filename}` - Download output files (supports info.log)

### Example Usage

Start a job:
```bash
curl -X POST "http://localhost:8080/run" \
  -H "Content-Type: application/json" \
  -d '{"ticker": "NVDA", "company": "NVIDIA Corporation", "pipeline": "full"}'
```

Stream real-time logs:
```bash
curl -N "http://localhost:8080/jobs/NVDA_20250803_163401/logs/stream"
```

Get job status:
```bash
curl "http://localhost:8080/jobs/NVDA_20250803_163401" | jq .
```

## New Features

### Real-time Log Streaming
The API now supports real-time streaming of container logs using Server-Sent Events (SSE). This allows frontends to:
- Monitor job progress in real-time
- Display live log updates
- Detect completion status immediately
- Handle errors and warnings as they occur

### Enhanced Status Detection
The API now properly detects:
- `completed` - Job finished successfully
- `failed` - Job failed with errors
- `completed_with_warnings` - Job completed but with warnings (e.g., "No filtered articles found")
- Real-time progress updates based on log analysis

## Environment Variables

- `BACKEND_IMAGE`: Docker image to run for analysis jobs (default: "stock-analyst:latest")
- `DATA_VOLUME`: Docker volume name for persistent data (default: "stockdata")
- `SERPAPI_API_KEY`: **Required** - API key for SerpApi web search
- `OPENAI_API_KEY`: **Required** - API key for OpenAI GPT models
