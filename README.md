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
docker build -t stock-analyst-runner .
docker run -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --env-file .env \
  stock-analyst-runner
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

- `POST /run` - Start a new analysis job
- `GET /jobs/{job_id}` - Get job status and results
- `GET /jobs/{job_id}/logs` - Get job logs
- `GET /jobs/{job_id}/files/{filename}` - Download output files

## Environment Variables

- `BACKEND_IMAGE`: Docker image to run for analysis jobs (default: "stock-analyst:latest")
- `DATA_VOLUME`: Docker volume name for persistent data (default: "stockdata")
- `SERPAPI_API_KEY`: **Required** - API key for SerpApi web search
- `OPENAI_API_KEY`: **Required** - API key for OpenAI GPT models
