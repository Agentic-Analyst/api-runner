# 📈 Stock Analyst API Runner

> **A high-performance FastAPI orchestration service for AI-powered stock analysis, real-time market data streaming, and automated intelligence report generation.**

[![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Containerized-2496ED.svg)](https://www.docker.com/)
[![MongoDB](https://img.shields.io/badge/MongoDB-Async-47A248.svg)](https://www.mongodb.com/)
[![WebSocket](https://img.shields.io/badge/WebSocket-Real--time-FF6F00.svg)](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket)
[![Lines of Code](https://img.shields.io/badge/Lines%20of%20Code-10.6k-brightgreen.svg)](#)

### 📊 Codebase Stats

| Metric | Count |
|--------|-------|
| **Total Lines of Code** | 10,598 |
| **Python Source Lines** | 8,764 |
| **Source Files** | 30 |

---

## Table of Contents

- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Key Features](#key-features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Environment Configuration](#environment-configuration)
  - [Local Development](#local-development)
  - [Docker Deployment](#docker-deployment)
  - [Production Deployment](#production-deployment)
- [API Reference](#api-reference)
  - [Authentication](#authentication)
  - [Analysis Jobs](#analysis-jobs)
  - [Chat Pipeline](#chat-pipeline)
  - [Real-time Stock Prices](#real-time-stock-prices)
  - [News Feed System](#news-feed-system)
  - [Daily Intelligence Reports](#daily-intelligence-reports)
  - [File Downloads](#file-downloads)
  - [Health & Monitoring](#health--monitoring)
- [WebSocket Protocols](#websocket-protocols)
- [Data Architecture](#data-architecture)
- [Troubleshooting](#troubleshooting)

---

## Overview

The **Stock Analyst API Runner** is a production-grade orchestration layer that manages the full lifecycle of AI-driven stock analysis workflows. It acts as the central control plane — receiving analysis requests, spinning up isolated Docker containers for each pipeline run, streaming real-time progress back to clients, and delivering structured outputs (PDF reports, Excel financial models, markdown summaries).

### What It Does

1. **Orchestrates Analysis Pipelines** — Receives a ticker and configuration, spawns a containerized backend engine that performs web scraping, NLP-based article screening, LLM-powered analysis, DCF financial modeling, and professional report generation.
2. **Streams Real-time Market Data** — Maintains persistent WebSocket connections for live stock price updates (via Yahoo Finance), with intelligent caching and background polling every 10 seconds.
3. **Aggregates & Streams News** — Queries MongoDB for curated news articles per ticker, triggers backend scraping jobs for fresh data, and streams results to clients over WebSocket.
4. **Generates Daily Intelligence Reports** — Produces automated company-level and sector-level daily briefings, with markdown-to-PDF conversion for institutional-quality deliverables.
5. **Supports Interactive Chat** — Provides a conversational AI pipeline where users ask natural-language questions about stocks, and the system dynamically identifies tickers and runs targeted analysis.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            CLIENT LAYER                                     │
│   React Frontend  ·  Mobile App  ·  CLI  ·  Third-Party Integrations        │
└──────────┬─────────────────┬──────────────────┬─────────────────────────────┘
           │ HTTP/REST       │ WebSocket         │ SSE (Server-Sent Events)
           ▼                 ▼                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      API RUNNER (FastAPI · Port 8080)                        │
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  Auth Module  │  │  Job Engine  │  │   Realtime   │  │   News Feed    │  │
│  │              │  │              │  │   Module     │  │    Module      │  │
│  │ · Google OAuth│  │ · Run/Stop  │  │              │  │               │  │
│  │ · GitHub OAuth│  │ · SSE Stream│  │ · Price Fetch│  │ · MongoDB DAO │  │
│  │ · Email Code │  │ · Log Monitor│  │ · WebSocket  │  │ · WebSocket   │  │
│  │ · Sessions   │  │ · Downloads │  │ · Historical │  │ · Auto-Update │  │
│  └──────────────┘  └──────┬───────┘  └──────────────┘  └────────────────┘  │
│                           │                                                  │
│  ┌──────────────┐  ┌──────┴───────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  Daily Reports│  │  Chat Engine │  │  PDF Converter│  │   Config       │  │
│  │              │  │              │  │              │  │                │  │
│  │ · Company    │  │ · NL Query   │  │ · Markdown→PDF│  │ · Env Vars    │  │
│  │ · Sector     │  │ · Ticker ID  │  │ · ReportLab  │  │ · API Keys    │  │
│  │ · Scheduler  │  │ · Session    │  │ · TOC/Links  │  │ · Docker Cfg  │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └────────────────┘  │
└──────────┬──────────────────────────────────────────────────────────────────┘
           │ Docker SDK (docker.sock)
           ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     CONTAINER ORCHESTRATION                                  │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐        │
│  │  Backend Analysis Container (stock-analyst:latest)               │        │
│  │                                                                 │        │
│  │  Pipelines: comprehensive · financial-only · model-only         │        │
│  │             news-only · model-to-price · chat                   │        │
│  │                                                                 │        │
│  │  Capabilities: Web Scraping · Article NLP · LLM Analysis        │        │
│  │                DCF Modeling · Price Adjustment · Report Gen      │        │
│  └─────────────────────────┬───────────────────────────────────────┘        │
│                            │                                                 │
│                ┌───────────▼───────────┐                                     │
│                │   stockdata (Volume)   │                                     │
│                │ /data/<email>/<TICKER>/ │                                    │
│                │       /<timestamp>/    │                                     │
│                └───────────────────────┘                                     │
└─────────────────────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL SERVICES                                     │
│                                                                             │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌───────────────────────┐ │
│  │  SerpAPI    │  │   OpenAI   │  │ Anthropic  │  │   MongoDB Atlas       │ │
│  │ Web Search  │  │  GPT-4o    │  │  Claude    │  │  News Articles        │ │
│  └────────────┘  └────────────┘  └────────────┘  └───────────────────────┘ │
│                                                                             │
│  ┌────────────┐                                                             │
│  │  Yahoo     │                                                             │
│  │  Finance   │                                                             │
│  │  (yfinance)│                                                             │
│  └────────────┘                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Docker-in-Docker Pattern

The API Runner itself runs inside a Docker container and uses the host Docker socket (`/var/run/docker.sock`) to spawn ephemeral analysis containers. Each analysis job runs in complete isolation with its own environment, sharing only the persistent `stockdata` volume for I/O. This architecture ensures:

- **Process Isolation** — Each analysis job is sandboxed in its own container
- **Resource Control** — Containers are automatically removed after completion
- **Horizontal Scalability** — Multiple analysis jobs can run concurrently
- **Fault Tolerance** — A crashed analysis container does not affect the API service

---

## Key Features

| Feature | Description |
|---|---|
| **Multi-Pipeline Analysis** | Run comprehensive, financial-only, news-only, or chat-based analysis pipelines in isolated containers |
| **Real-time SSE Streaming** | Server-Sent Events for live log streaming with progress stages, heartbeats, and completion signals |
| **WebSocket Price Feed** | Real-time stock price updates every 10s with subscription management via yfinance |
| **WebSocket News Feed** | Real-time news article streaming with per-ticker subscriptions and auto-updates from MongoDB |
| **Multi-Provider Auth** | Google OAuth 2.0, GitHub OAuth, and email verification code login with session management |
| **PDF Report Generation** | Professional-grade PDF conversion from markdown with TOC, internal links, and custom styling via ReportLab |
| **Daily Intelligence Reports** | Automated company and sector briefings with pre-market scheduling (8:30 AM ET) |
| **Chat Interface** | Conversational AI that identifies tickers from natural language and runs targeted analysis pipelines |
| **Job Lifecycle Management** | Create, monitor, stop (graceful SIGTERM), and clean up analysis jobs with full state tracking |
| **File Downloads** | Excel financial models (.xlsx), PDF reports, markdown summaries, and complete tar archives |
| **Graceful Shutdown** | SIGTERM-based container stopping with 10s timeout, state preservation, and user notification |
| **Health Monitoring** | Multi-level health checks across Docker, real-time services, news feeds, and API key configuration |

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Web Framework** | FastAPI 0.104 + Uvicorn (ASGI) |
| **Language** | Python 3.11 (Miniconda) |
| **Container Runtime** | Docker SDK for Python 7.1 |
| **Database** | MongoDB (async via Motor 3.5 + PyMongo 4.5) |
| **Data Layer** | [vynn_core](https://github.com/Agentic-Analyst/vynn-core) — shared article DAO and indexes |
| **Market Data** | yfinance (Yahoo Finance API) |
| **LLM Providers** | OpenAI (GPT-4o / GPT-4o-mini) · Anthropic (Claude) |
| **Web Scraping** | SerpAPI · BeautifulSoup4 · newspaper3k · feedparser · NLTK |
| **PDF Generation** | ReportLab · pypdf |
| **Authentication** | Authlib (OAuth 2.0 / OIDC) · HMAC-SHA256 tokens · Starlette sessions |
| **Real-time Comms** | FastAPI native WebSocket · Server-Sent Events (SSE) |
| **Deployment** | Docker · Docker Compose · Multi-arch builds (linux/amd64 + linux/arm64) |

---

## Project Structure

```
api-runner/
├── main.py                          # FastAPI app entry point & job orchestration (~1900 lines)
├── config.py                        # Centralized environment variable configuration
├── auth_oauth.py                    # Google & GitHub OAuth 2.0 authentication
├── auth_code_login.py               # Email verification code authentication (dev-friendly)
├── md_pdf_converter.py              # Full-featured Markdown → PDF converter (ReportLab)
├── requirements.txt                 # Python dependencies
├── Dockerfile                       # Docker build (Miniconda + Python 3.11.13)
├── docker-compose.dev.yml           # Development Docker Compose configuration
├── build.sh                         # Docker image build helper script
├── start.sh                         # Local development startup script
├── API_DOCUMENTATION.md             # Extended API documentation with frontend examples
│
├── realtime/                        # 📊 Real-time Stock Price System
│   ├── __init__.py                  # Module exports (PriceManager, stock_router, etc.)
│   ├── models.py                    # Pydantic models (StockPrice, PriceUpdate, HistoricalData, etc.)
│   ├── price_fetcher.py             # yfinance integration + PriceManager background loop (10s interval)
│   ├── stock_api.py                 # REST endpoints (/api/realtime/*) + WebSocket entry point
│   ├── fastapi_websocket.py         # WebSocket connection manager (integrated with main FastAPI app)
│   └── websocket_server.py          # [Deprecated] Legacy standalone WebSocket server (reference only)
│
├── news_feed/                       # 📰 News Feed Aggregation System
│   ├── __init__.py                  # Module exports
│   ├── models.py                    # Pydantic models (NewsArticle, NewsSubscription, etc.)
│   ├── api.py                       # REST endpoints (/api/news/*) with dependency injection
│   ├── news_manager.py              # MongoDB queries via vynn_core + Docker job coordination
│   ├── news_websocket.py            # WebSocket manager for streaming news with dead-connection handling
│   └── news_auto_updater.py         # Background news refresh with smart rate limiting (SerpAPI quotas)
│
└── reports/                         # 📋 Automated Report Generation
    ├── __init__.py
    ├── daily/
    │   ├── __init__.py
    │   ├── models.py                # Pydantic models (CompanyDailyReportRequest, BatchJobResponse, etc.)
    │   ├── api.py                   # REST endpoints (/api/daily-reports/*) for company & sector reports
    │   ├── scheduler.py             # Pre-market cron scheduler (8:30 AM ET, Mon–Fri, auto-skip weekends)
    │   └── daily_report_pdf_converter.py  # Daily report-optimized PDF styling with section boxes
    └── hourly/                      # [Planned] Hourly report generation
```

---

## Getting Started

### Prerequisites

| Requirement | Version | Purpose |
|---|---|---|
| **Docker** | ≥ 20.10 | Container runtime for spawning analysis jobs |
| **Python** | 3.11+ | API runtime (when running locally outside Docker) |
| **MongoDB** | ≥ 6.0 | News article storage (MongoDB Atlas or self-hosted) |
| **Backend Image** | `stock-analyst:latest` | The analysis engine Docker image (separate repo) |

### Environment Configuration

Create a `.env` file in the project root:

```bash
# ═══════════════════════════════════════════════════════════
# API Keys (Required)
# ═══════════════════════════════════════════════════════════
SERPAPI_API_KEY=your_serpapi_key              # https://serpapi.com/
OPENAI_API_KEY=sk-your_openai_key            # https://platform.openai.com/api-keys
ANTHROPIC_API_KEY=sk-ant-your_key            # https://console.anthropic.com/

# ═══════════════════════════════════════════════════════════
# MongoDB Configuration
# ═══════════════════════════════════════════════════════════
MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/
MONGO_DB=your_database_name

# ═══════════════════════════════════════════════════════════
# Docker Configuration
# ═══════════════════════════════════════════════════════════
BACKEND_IMAGE=stock-analyst:latest           # Analysis engine Docker image
DATA_VOLUME=stockdata                        # Persistent data volume name

# ═══════════════════════════════════════════════════════════
# OAuth (Optional — enable for Google/GitHub login)
# ═══════════════════════════════════════════════════════════
OAUTH_GOOGLE_CLIENT_ID=your_google_client_id
OAUTH_GOOGLE_CLIENT_SECRET=your_google_secret
OAUTH_GITHUB_CLIENT_ID=your_github_client_id
OAUTH_GITHUB_CLIENT_SECRET=your_github_secret

# ═══════════════════════════════════════════════════════════
# Session & Security
# ═══════════════════════════════════════════════════════════
SESSION_SECRET=change-me-to-random-string
AUTH_SECRET=change-me-to-another-random-string
SESSION_HTTPS_ONLY=false                     # true in production
SESSION_SAMESITE=lax                         # lax | strict | none
SESSION_STORE_COOKIE=session
COOKIE_DOMAIN=localhost

# ═══════════════════════════════════════════════════════════
# Frontend & Reverse Proxy
# ═══════════════════════════════════════════════════════════
FRONTEND_ORIGIN=http://localhost:3000        # Comma-separated for multiple
ROOT_PATH=                                   # e.g., /api if behind proxy
OAUTH_REDIRECT_BASE=                         # e.g., https://yourdomain.com/api

# ═══════════════════════════════════════════════════════════
# Dev Login (Development Only)
# ═══════════════════════════════════════════════════════════
HARDCODED_LOGIN_CODE=246810
```

### Local Development

```bash
# 1. Clone and setup
git clone <repo-url> && cd api-runner
python -m venv .venv && source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env   # Edit with your API keys

# 4. Ensure Docker is running and data volume exists
docker volume create stockdata

# 5. Start the development server (with hot reload)
./start.sh
# Or manually:
uvicorn main:app --reload --host 0.0.0.0 --port 8080
```

The API will be available at **http://localhost:8080**.  
Interactive Swagger docs at **http://localhost:8080/docs**.

### Docker Deployment

```bash
# Build the API Runner image
./build.sh
# Or manually:
docker build -t stock-analyst-runner:latest .

# Create persistent data volume
docker volume create stockdata

# Run the container
docker run -d \
  -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v stockdata:/data \
  --env-file .env \
  --name api-runner \
  stock-analyst-runner:latest
```

### Docker Compose (Recommended)

```bash
# Development environment
docker compose -f docker-compose.dev.yml up --build -d

# View logs
docker compose -f docker-compose.dev.yml logs -f api-runner

# Rebuild after code changes
docker compose -f docker-compose.dev.yml down
docker compose -f docker-compose.dev.yml up --build -d
```

### Production Deployment

```bash
# Build multi-arch image and push to registry
docker buildx build --platform linux/amd64,linux/arm64 \
  -t fuzanwenn/api-runner:latest \
  --push .

# On the production server
docker compose pull api-runner
docker compose up -d --force-recreate api-runner
```

---

## API Reference

**Base URL:** `http://localhost:8080`  
**Interactive Docs:** `http://localhost:8080/docs` (Swagger UI)

### Authentication

Three authentication methods are supported. All auth endpoints are under `/auth`.

#### Google OAuth 2.0

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/auth/google/login?redirect_url=<url>` | Initiate Google OIDC flow |
| `GET` | `/auth/google/callback` | OAuth callback (automatic) |

#### GitHub OAuth

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/auth/github/login?redirect_url=<url>` | Initiate GitHub OAuth flow |
| `GET` | `/auth/github/callback` | OAuth callback (automatic) |

#### Email Verification Code

```bash
# 1. Request a code
curl -X POST http://localhost:8080/auth/request-code \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com"}'

# 2. Verify the code
curl -X POST http://localhost:8080/auth/verify-code \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "code": "246810"}'
```

#### Session Management

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/auth/session/me` | Get current authenticated user info |
| `POST` | `/auth/logout` | Clear session and log out |

---

### Analysis Jobs

#### Start Analysis

```
POST /run
```

Launch a new stock analysis job in an isolated Docker container.

**Request Body:**
```json
{
  "ticker": "NVDA",
  "company": "NVIDIA Corporation",
  "email": "user@example.com",
  "llm": "gpt-4o-mini",
  "pipeline": "comprehensive",
  "query": "NVIDIA earnings and AI chip demand"
}
```

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `ticker` | string | ✅ | — | Stock ticker symbol (e.g., `NVDA`, `AAPL`) |
| `company` | string | ✅ | — | Company name |
| `email` | string | ✅ | — | User email (used for data path organization) |
| `llm` | string | ❌ | `gpt-4o-mini` | LLM model to use for analysis |
| `pipeline` | string | ❌ | `comprehensive` | Pipeline type (see table below) |
| `query` | string | ❌ | — | Custom search query for news-related pipelines |

**Available Pipelines:**

| Pipeline | Description |
|---|---|
| `comprehensive` | Full end-to-end: scraping → filtering → LLM analysis → financial modeling → report generation |
| `financial-only` | Financial data scraping and DCF modeling only |
| `model-only` | Financial model generation from existing scraped data |
| `news-only` | News scraping and article analysis only |
| `model-to-price` | Financial model to price target derivation |
| `news-to-price` | News sentiment to price impact analysis |

**Response:**
```json
{
  "job_id": "NVDA_20260220_143022",
  "ticker": "NVDA",
  "status": "pending",
  "message": "Analysis job started"
}
```

#### Stop Analysis

```
POST /jobs/{job_id}/stop
```

Gracefully stop a running job. Sends SIGTERM with a 10-second timeout, then force-kills if necessary.

#### Job Status Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/jobs/{job_id}` | Basic job status |
| `GET` | `/jobs/{job_id}/status/detailed` | Full status with file availability, logs count, and latest log |
| `GET` | `/jobs` | List all jobs |
| `GET` | `/jobs/{job_id}/logs` | Snapshot of recent logs |

**Job Status Values:**

| Status | Description | Retryable? |
|---|---|---|
| `pending` | Job queued, waiting to start | — |
| `running` | Analysis actively executing in container | — |
| `completed` | Successfully finished with all results available | — |
| `failed` | Analysis failed — check `error` field for details | ✅ |
| `llm_timeout` | LLM analysis timed out (provider overloaded) | ✅ |
| `stopping` | Stop request received, shutting down container | — |
| `stopped` | Successfully stopped by user | ✅ |

---

### Chat Pipeline

```
POST /chat
```

Start an interactive AI chat session. The backend identifies the relevant ticker from the user's natural language, then runs a targeted analysis pipeline.

**Request Body:**
```json
{
  "email": "user@example.com",
  "timestamp": "2026-02-20T14:30:00",
  "user_prompt": "What's happening with NVIDIA's AI chip business?",
  "session_id": "optional-session-id-for-continuity"
}
```

The `ticker` field in the response starts as `"pending"` and updates to the identified symbol (e.g., `NVDA`) once the backend extracts it. Use SSE streaming (`/jobs/{job_id}/logs/stream`) to receive the identified ticker and NL-type log messages in real time.

---

### Real-time Stock Prices

All endpoints are prefixed with `/api/realtime`.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/realtime/health` | Service health check with connection stats |
| `GET` | `/api/realtime/status` | Detailed service statistics |
| `GET` | `/api/realtime/price/{symbol}` | Current price for one symbol |
| `POST` | `/api/realtime/prices` | Batch prices for multiple symbols (max 50) |
| `GET` | `/api/realtime/historical/{symbol}?timeframe=1M` | Historical OHLCV data |
| `GET` | `/api/realtime/subscriptions` | Active WebSocket subscriptions |
| `POST` | `/api/realtime/subscribe/{symbol}` | Manually subscribe to price updates |
| `DELETE` | `/api/realtime/subscribe/{symbol}` | Unsubscribe from a symbol |
| `GET` | `/api/realtime/market/status` | US market open/closed status |
| `GET` | `/api/realtime/config` | Current fetcher configuration |
| `POST` | `/api/realtime/config` | Update fetcher configuration |
| `POST` | `/api/realtime/test/yfinance` | Test yfinance API connectivity |
| `GET` | `/api/realtime/debug/cache` | Inspect price cache (debug) |
| `WS` | `/api/realtime/ws` | **WebSocket** for real-time price streaming |

**Valid Historical Timeframes:** `1D` · `1W` · `1M` · `3M` · `1Y` · `ALL`

**Example — Get Stock Price:**
```bash
curl http://localhost:8080/api/realtime/price/AAPL
```
```json
{
  "symbol": "AAPL",
  "name": "Apple Inc.",
  "current_price": 187.45,
  "previous_close": 185.92,
  "change_amount": 1.53,
  "change_percent": 0.82,
  "volume": 54230100,
  "market_cap": 2890000000000,
  "pe_ratio": 29.4,
  "sector": "Technology",
  "industry": "Consumer Electronics",
  "high_52w": 199.62,
  "low_52w": 164.08,
  "bid": 187.44,
  "ask": 187.46,
  "is_market_open": true
}
```

---

### News Feed System

All endpoints are prefixed with `/api/news`.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/news/feed` | Fetch news articles for specified tickers |
| `GET` | `/api/news/status/{ticker}` | Check news data availability for a ticker |
| `GET` | `/api/news/jobs` | List active backend news search jobs |
| `GET` | `/api/news/jobs/{job_id}` | Get specific search job status |
| `POST` | `/api/news/refresh/{ticker}` | Force refresh news from backend |
| `GET` | `/api/news/auto-updater/status` | Background auto-updater status |
| `GET` | `/api/news/debug/{ticker}` | Debug: inspect raw database documents |
| `WS` | `/api/news/ws` | **WebSocket** for real-time news streaming |

**Example — Fetch News:**
```bash
curl -X POST http://localhost:8080/api/news/feed \
  -H "Content-Type: application/json" \
  -d '{"tickers": ["NVDA", "AAPL"], "limit": 10, "days_back": 7}'
```

---

### Daily Intelligence Reports

All endpoints are prefixed with `/api/daily-reports`.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/daily-reports/health` | Service health |
| `POST` | `/api/daily-reports/generate/company` | Generate company reports (batch) |
| `POST` | `/api/daily-reports/generate/sector` | Generate sector reports (batch) |
| `GET` | `/api/daily-reports/jobs/{job_id}/status` | Individual job status |
| `GET` | `/api/daily-reports/batch/{batch_id}/status` | Batch status (aggregated) |
| `GET` | `/api/daily-reports/jobs/{job_id}/logs` | Job container logs (debug) |
| `GET` | `/api/daily-reports/available-reports` | List all generated reports |
| `GET` | `/api/daily-reports/company/{ticker}/markdown?timestamp=YYYY-MM-DD` | Get report as markdown |
| `GET` | `/api/daily-reports/company/{ticker}/pdf?timestamp=YYYY-MM-DD` | Download report as PDF |
| `POST` | `/api/daily-reports/companies/markdown` | Batch: multiple company reports |
| `GET` | `/api/daily-reports/sector/{sector}/markdown?timestamp=YYYY-MM-DD` | Get sector report |
| `GET` | `/api/daily-reports/sector/{sector}/pdf?timestamp=YYYY-MM-DD` | Download sector PDF |
| `POST` | `/api/daily-reports/sectors/markdown` | Batch: multiple sector reports |
| `GET` | `/api/daily-reports/scheduler/status` | Scheduler status & next trigger |

**Example — Generate Company Reports:**
```bash
curl -X POST http://localhost:8080/api/daily-reports/generate/company \
  -H "Content-Type: application/json" \
  -d '{"tickers": ["AAPL", "MSFT", "GOOGL"], "timestamp": "2026-02-20"}'
```
```json
{
  "batch_id": "company_batch_2026-02-20_143022",
  "total_jobs": 3,
  "job_ids": [
    "company_report_AAPL_2026-02-20",
    "company_report_MSFT_2026-02-20",
    "company_report_GOOGL_2026-02-20"
  ],
  "status": "pending",
  "tickers_or_sectors": ["AAPL", "MSFT", "GOOGL"],
  "report_type": "company"
}
```

---

### File Downloads

After a job completes, download generated artifacts:

| Method | Endpoint | Output |
|---|---|---|
| `GET` | `/jobs/{job_id}/download/financial-model` | Excel financial model (`.xlsx`) |
| `GET` | `/jobs/{job_id}/download/professional-report` | Professional analysis report (`.pdf`) |
| `GET` | `/jobs/{job_id}/download/financial-summary` | Financial summary (`.pdf`) |
| `GET` | `/jobs/{job_id}/download/news-summary` | News summary (`.pdf`) |
| `GET` | `/jobs/{job_id}/download/all-results` | Complete archive (`.tar`) |
| `GET` | `/jobs/{job_id}/files` | List available files (JSON) |

---

### Health & Monitoring

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Root status check |
| `GET` | `/health` | Comprehensive: Docker, realtime, news, API keys |
| `GET` | `/healthz` | Kubernetes-style liveness probe |

**Example:**
```bash
curl http://localhost:8080/health | python -m json.tool
```
```json
{
  "status": "healthy",
  "docker": "connected",
  "realtime_prices": "running",
  "websocket_server": "integrated",
  "news_feed": "running",
  "backend_image": "stock-analyst:latest",
  "data_volume": "stockdata",
  "api_keys_configured": {
    "serpapi": true,
    "openai": true,
    "anthropic": true
  }
}
```

---

## Real-time Log Streaming (SSE)

```
GET /jobs/{job_id}/logs/stream
```

Server-Sent Events stream for live monitoring of analysis progress. Features automatic heartbeats, final drain logic, and session ID extraction for chat jobs.

**Event Types:**

| Event | Description |
|---|---|
| `connection` | Initial connection established |
| `log` | Log line — includes `type` field: `"NL"` (LLM natural language) or `"LOG"` (system log) |
| `status` | Progress status update |
| `completed` | Analysis finished (may include `session_id` for chat pipelines) |
| `error` | Error occurred |

**Frontend Integration Example:**
```javascript
const eventSource = new EventSource(`/jobs/${jobId}/logs/stream`);

eventSource.addEventListener('log', (event) => {
  const data = JSON.parse(event.data);
  if (data.type === 'NL') {
    // LLM-generated natural language output
    appendToChat(data.message);
  } else {
    // System log message
    appendToLog(data.message);
  }
});

eventSource.addEventListener('completed', (event) => {
  const data = JSON.parse(event.data);
  console.log('Done!', data.session_id);
  eventSource.close();
});
```

---

## WebSocket Protocols

### Stock Price WebSocket — `/api/realtime/ws`

**Subscribe:**
```json
{ "type": "subscribe", "symbols": ["AAPL", "GOOGL", "MSFT"], "user_id": "user@example.com" }
```

**Price Update (received every ~10s for subscribed symbols):**
```json
{
  "type": "price_update",
  "data": {
    "symbol": "AAPL",
    "name": "Apple Inc.",
    "current_price": 187.45,
    "change_amount": 1.53,
    "change_percent": 0.82,
    "volume": 54230100,
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "bid": 187.44,
    "ask": 187.46,
    "high_52w": 199.62,
    "low_52w": 164.08,
    "timestamp": "2026-02-20T14:30:22"
  }
}
```

**Supported Messages:** `subscribe` · `unsubscribe` · `ping` · `get_price`

### News Feed WebSocket — `/api/news/ws`

**Subscribe:**
```json
{
  "type": "subscribe",
  "tickers": ["NVDA", "AAPL"],
  "limit": 20,
  "days_back": 7,
  "force_refresh": false
}
```

**Article Update:**
```json
{
  "type": "news_update",
  "data": {
    "ticker": "NVDA",
    "article": {
      "title": "NVIDIA Reports Record Revenue...",
      "content": "Full article text...",
      "source_url": "https://reuters.com/...",
      "publish_date": "Feb 19, 2026",
      "serpapi_source": "Reuters"
    },
    "source": "cache"
  }
}
```

**Supported Messages:** `subscribe` · `unsubscribe` · `ping` · `refresh`

---

## Data Architecture

### Volume Layout

All analysis data is persisted in the `stockdata` Docker volume:

```
/data/
├── user@example.com/                           # Per-user namespace
│   ├── NVDA/
│   │   └── 2026-02-20T14:30:22.123456/        # Per-run timestamp
│   │       ├── info.log                         # Complete pipeline execution log
│   │       ├── models/
│   │       │   ├── NVDA_financial_model.xlsx    # DCF/comparable Excel model
│   │       │   └── NVDA_price_adjustment_explanation.md
│   │       ├── reports/
│   │       │   └── NVDA_Professional_Analysis_Report.md
│   │       ├── summaries/
│   │       │   ├── NVDA_financial_summary.md
│   │       │   └── NVDA_news_summary.md
│   │       ├── searched/                        # Raw scraped articles (.md)
│   │       └── filtered/                        # NLP-filtered articles + index.csv
│   └── AAPL/
│       └── ...
│
└── reports@vynnai.com/                          # Daily intelligence reports
    ├── AAPL/
    │   └── 2026-02-20/reports/*.md
    ├── TECHNOLOGY/                               # Sector-level reports
    │   └── 2026-02-20/reports/*.md
    └── COMMUNICATION_SERVICES/
        └── ...
```

### MongoDB Collections

News articles are stored via `vynn_core` in per-ticker collections:

| Collection | Document Schema |
|---|---|
| `NVDA`, `AAPL`, etc. | `{ urlHash, title, content, company, ticker, source_url, publish_date, scraped_at, search_category, serpapi_source, serpapi_snippet, serpapi_thumbnail, serpapi_authors, serpapi_source_icon, word_count, createdAt, updatedAt }` |

---

## Troubleshooting

### Docker Issues

| Problem | Diagnosis | Solution |
|---|---|---|
| Docker client not available | `docker ps` fails | Start Docker Desktop or Docker daemon |
| Backend image not found | `docker images \| grep stock-analyst` returns nothing | Build or pull the `stock-analyst:latest` image |
| Permission denied on socket | API can't spawn containers | `chmod 666 /var/run/docker.sock` or add user to `docker` group |
| Volume permission errors | Files not readable | `docker volume rm stockdata && docker volume create stockdata` |
| Port 8080 in use | `lsof -i :8080` | Kill conflicting process or remap port: `-p 8081:8080` |

### Job Failures

| Status | Meaning | Action |
|---|---|---|
| `llm_timeout` | LLM provider is slow/overloaded | Retry — often succeeds on second attempt |
| `failed` (exit code ≠ 0) | Backend container crashed | Check `GET /jobs/{id}/logs` for container error output |
| Empty results | No articles found | Verify ticker/company, use specific `query`, lower score thresholds |

### Useful Debug Commands

```bash
# API health check
curl http://localhost:8080/health | python -m json.tool

# View API Runner container logs
docker logs -f api-runner

# Inspect data volume contents
docker run --rm -v stockdata:/data alpine ls -laR /data/

# Check environment variables are loaded
docker exec api-runner env | grep -E "(SERPAPI|OPENAI|ANTHROPIC|MONGO)"

# Access container shell for debugging
docker exec -it api-runner bash

# Backup all analysis results
docker run --rm -v stockdata:/data -v $(pwd):/backup alpine \
  tar czf /backup/stockdata_backup.tar.gz -C /data .

# Restore from backup
docker run --rm -v stockdata:/data -v $(pwd):/backup alpine \
  tar xzf /backup/stockdata_backup.tar.gz -C /data

# Full clean-slate restart
docker compose down
docker volume rm stockdata
docker volume create stockdata
docker compose up --build -d
```

---

## License

This project is proprietary software. All rights reserved.