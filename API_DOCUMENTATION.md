# Stock Analyst API Documentation

## Overview

The Stock Analyst API is a FastAPI-based service that manages and runs stock analysis jobs using Docker containers. It provides real-time monitoring, file downloads, and comprehensive job management capabilities.

**Base URL:** `http://localhost:8080`

---

## Authentication

Currently, no authentication is required. API keys (SERPAPI_API_KEY, OPENAI_API_KEY) are configured server-side.

---

## Core Endpoints

### 1. Health Check

#### `GET /`
Basic health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "service": "Stock Analyst API Runner",
  "docker_available": true,
  "timestamp": "2025-08-07T14:30:22.123456"
}
```

#### `GET /health`
Detailed health check with system status.

**Response:**
```json
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

---

## Job Management

### 2. Start Analysis Job

#### `POST /run`
Starts a new stock analysis job with comprehensive financial modeling options.

**Request Body:**
```json
{
  "ticker": "NVDA",
  "company": "NVIDIA Corporation",
  "query": "recent earnings performance",
  "pipeline": "comprehensive",
  "model": "dcf",
  "years": 5,
  "term_growth": 2.5,
  "wacc": 8.5,
  "strategy": "aggressive",
  "max_articles": 50,
  "min_score": 0.7,
  "scaling": "moderate",
  "adjustment_cap": 15.0
}
```

**Parameters:**
- `ticker` (required): Stock ticker symbol
- `company` (optional): Company name (auto-inferred if not provided)
- `query` (optional): Custom search query for article filtering
- `pipeline` (optional): Analysis pipeline type
  - `"comprehensive"` (default): Full analysis with all components
  - `"financial-only"`: Financial analysis only
  - `"model-only"`: Financial modeling only
  - `"news-only"`: News analysis only  
  - `"model-to-price"`: Financial model to price target
  - `"news-to-price"`: News sentiment to price impact
- `model` (optional): Financial model type
  - `"dcf"` (default): Discounted Cash Flow analysis
  - `"comparable"`: Comparable company analysis
  - `"comprehensive"`: Combined DCF and comparable analysis
- `years` (optional): Projection years for financial modeling (default: 5)
- `term_growth` (optional): Terminal growth rate % (default: 2.5)
- `wacc` (optional): Weighted Average Cost of Capital % (default: 8.0)
- `strategy` (optional): Analysis strategy
  - `"conservative"`: Conservative assumptions
  - `"moderate"` (default): Balanced assumptions
  - `"aggressive"`: Growth-focused assumptions
- `max_articles` (optional): Maximum articles to analyze (default: 30)
- `min_score` (optional): Minimum relevance score for articles (default: 0.5)
- `scaling` (optional): Revenue scaling assumptions
  - `"conservative"`: Low growth scaling
  - `"moderate"` (default): Balanced scaling
  - `"aggressive"`: High growth scaling
- `adjustment_cap` (optional): Maximum price adjustment % (default: 20.0)

**Response:**
```json
{
  "job_id": "NVDA_20250807_143022",
  "status": "pending",
  "message": "Analysis job started"
}
```

**Error Responses:**
- `503`: Docker service not available
- `400`: API keys not configured

### 3. Get Job Status

#### `GET /jobs/{job_id}`
Get basic status of a specific job.

**Response:**
```json
{
  "job_id": "NVDA_20250807_143022",
  "ticker": "NVDA",
  "company": "NVIDIA Corporation",
  "status": "completed",
  "progress": "Analysis completed successfully",
  "created_at": "2025-08-07T14:30:22.123456",
  "completed_at": "2025-08-07T14:45:15.789012",
  "error": null
}
```

**Status Values:**
- `pending`: Job queued
- `running`: Analysis in progress
- `completed`: Successfully finished
- `completed_with_warnings`: Finished with warnings
- `failed`: Analysis failed

#### `GET /jobs/{job_id}/status/detailed`
Get comprehensive status including file availability and progress metrics.

**Response:**
```json
{
  "job_id": "NVDA_20250807_143022",
  "ticker": "NVDA",
  "company": "NVIDIA Corporation",
  "status": "completed",
  "progress": "Analysis completed successfully",
  "progress_percentage": 100,
  "created_at": "2025-08-07T14:30:22.123456",
  "completed_at": "2025-08-07T14:45:15.789012",
  "duration": "14m 53s",
  "completed_stages": "4/4",
  "error": null,
  "analysis_completed": true,
  "last_activity": "2025-08-07T14:45:15.789012",
  "files_available": {
    "info_log": true,
    "screening_report": true,
    "screening_data": true,
    "searched_articles_count": 9,
    "filtered_articles_count": 8,
    "financials_annual": true,
    "financial_model": true,
    "filtered_report": true,
    "price_adjustment_explanation": true
  },
  "recent_logs_count": 45,
  "latest_log": "PIPELINE SESSION COMPLETED successfully"
}
```

#### `GET /jobs`
List all jobs.

**Response:**
```json
{
  "jobs": [
    {
      "job_id": "NVDA_20250807_143022",
      "ticker": "NVDA",
      "status": "completed",
      "created_at": "2025-08-07T14:30:22.123456"
    }
  ]
}
```

---

## Real-time Monitoring

### 4. Stream Job Logs

#### `GET /jobs/{job_id}/logs/stream`
Server-Sent Events (SSE) stream for real-time job monitoring.

**Response Format:** `text/event-stream`

**Event Types:**
```javascript
// Status updates
{
  "type": "status",
  "status": "running",
  "progress": "Scraping articles..."
}

// Individual log messages
{
  "type": "log", 
  "message": "Found 15 relevant articles for NVDA"
}

// Latest activity
{
  "type": "latest",
  "message": "LLM ANALYSIS started for article 3/8",
  "timestamp": "2025-08-07T14:35:10.123456"
}

// Final completion
{
  "type": "final",
  "status": "completed",
  "message": "Job completed"
}
```

**Frontend Implementation Example:**
```javascript
const eventSource = new EventSource(`/jobs/${jobId}/logs/stream`);

eventSource.onmessage = function(event) {
  const data = JSON.parse(event.data);
  
  switch(data.type) {
    case 'status':
      updateJobStatus(data.status, data.progress);
      break;
    case 'log':
      appendToLogDisplay(data.message);
      break;
    case 'latest':
      updateLatestActivity(data.message, data.timestamp);
      break;
    case 'final':
      handleJobCompletion(data.status, data.message);
      eventSource.close();
      break;
  }
};

eventSource.onerror = function(event) {
  console.error('SSE connection error:', event);
};
```

### 5. Get Job Logs

#### `GET /jobs/{job_id}/logs`
Get detailed job logs for debugging.

**Response:**
```json
{
  "job_id": "NVDA_20250807_143022",
  "status": "completed",
  "progress": "Analysis completed successfully",
  "error": null,
  "container_logs": "Docker container output logs...",
  "container_id": "abc123def456"
}
```

---

## File Operations

### 6. List Available Files

#### `GET /jobs/{job_id}/files`
List all available files for a completed job.

**Response:**
```json
{
  "job_id": "NVDA_20250807_143022",
  "ticker": "NVDA",
  "files": {
    "info_log": true,
    "screening_report": true,
    "screening_data": true,
    "searched_articles_count": 9,
    "filtered_articles_count": 8,
    "financials_annual": true,
    "financial_model": true,
    "filtered_report": true,
    "price_adjustment_explanation": true
  },
  "timestamp": "2025-08-07T14:45:30.123456"
}
```

### 7. Download Files

#### `GET /jobs/{job_id}/download/searched-articles`
Download all scraped articles as a ZIP file.

**Response:** Binary ZIP file
**Filename:** `{TICKER}_searched_articles.zip`

#### `GET /jobs/{job_id}/download/filtered-articles`
Download filtered articles and index as a ZIP file.

**Response:** Binary ZIP file
**Filename:** `{TICKER}_filtered_articles.zip`
**Contents:**
- `filtered_01_score_X.X_*.md` - Individual filtered articles
- `filtered_articles_index.csv` - Article index with scores

#### `GET /jobs/{job_id}/download/screening-report`
Download the main screening report.

**Response:** Markdown file
**Filename:** `{TICKER}_screening_report.md`
**Content-Type:** `text/markdown`

#### `GET /jobs/{job_id}/download/financials-annual`
Download annual financials data in JSON format.

**Response:** JSON file
**Filename:** `{TICKER}_financials_annual_modeling_latest.json`
**Content-Type:** `application/json`
**Location:** `financials/` directory

#### `GET /jobs/{job_id}/download/financial-model`
Download comprehensive financial model as Excel file.

**Response:** Excel file
**Filename:** `{TICKER}_financial_model_comprehensive_latest.xlsx`
**Content-Type:** `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
**Location:** `models/` directory

#### `GET /jobs/{job_id}/download/filtered-report`
Download filtered analysis report.

**Response:** Markdown file
**Filename:** `{TICKER}_filtered_report.md`
**Content-Type:** `text/markdown`

#### `GET /jobs/{job_id}/download/price-adjustment-explanation`
Download price adjustment explanation and methodology.

**Response:** Markdown file
**Filename:** `{TICKER}_price_adjustment_explanation_latest.md`
**Content-Type:** `text/markdown`
**Location:** `models/` directory

#### `GET /jobs/{job_id}/download/all-results`
Download complete analysis results as a comprehensive ZIP.

**Response:** Binary ZIP file
**Filename:** `{TICKER}_complete_analysis.zip`
**Contents:**
- `info.log` - Complete analysis logs
- `screening_report.md` - Main report
- `screening_data.json` - Structured analysis data
- `searched/` folder - All scraped articles
- `filtered/` folder - Filtered articles and index
- `financials/` folder - Annual financials JSON data
- `models/` folder - Financial models and price explanations

#### `GET /jobs/{job_id}/info-log`
Get the complete info.log content as JSON.

**Response:**
```json
{
  "job_id": "NVDA_20250807_143022",
  "ticker": "NVDA",
  "log_content": "2025-08-07 14:30:22 | Pipeline initialized for NVDA...",
  "timestamp": "2025-08-07T14:45:30.123456"
}
```

---

## Frontend Integration Examples

### Complete Job Management Class

```javascript
class StockAnalysisAPI {
  constructor(baseUrl = 'http://localhost:8080') {
    this.baseUrl = baseUrl;
  }

  // Start new analysis
  async startAnalysis(ticker, company = null, query = null, options = {}) {
    const requestBody = { 
      ticker, 
      ...company && { company },
      ...query && { query }
    };
    
    // Add optional financial modeling parameters
    const validOptions = [
      'pipeline', 'model', 'years', 'term_growth', 'wacc', 
      'strategy', 'max_articles', 'min_score', 'scaling', 'adjustment_cap'
    ];
    
    validOptions.forEach(key => {
      if (options[key] !== undefined) {
        requestBody[key] = options[key];
      }
    });
    
    // Set defaults if not specified
    if (!requestBody.pipeline) requestBody.pipeline = 'comprehensive';
    if (!requestBody.model) requestBody.model = 'dcf';
    
    const response = await fetch(`${this.baseUrl}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestBody)
    });
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${await response.text()}`);
    }
    
    return await response.json();
  }

  // Monitor job progress with SSE
  monitorJob(jobId, callbacks = {}) {
    const eventSource = new EventSource(`${this.baseUrl}/jobs/${jobId}/logs/stream`);
    
    eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      if (callbacks[data.type]) {
        callbacks[data.type](data);
      }
      
      if (data.type === 'final') {
        eventSource.close();
      }
    };
    
    eventSource.onerror = (error) => {
      if (callbacks.error) callbacks.error(error);
    };
    
    return eventSource; // Return for manual control
  }

  // Get detailed status
  async getJobStatus(jobId) {
    const response = await fetch(`${this.baseUrl}/jobs/${jobId}/status/detailed`);
    return await response.json();
  }

  // Download files
  async downloadFile(jobId, fileType) {
    const response = await fetch(`${this.baseUrl}/jobs/${jobId}/download/${fileType}`);
    
    if (response.ok) {
      const blob = await response.blob();
      const filename = this.extractFilename(response) || `${jobId}_${fileType}`;
      this.triggerDownload(blob, filename);
      return true;
    }
    
    throw new Error(`Download failed: ${response.status}`);
  }

  // Helper methods
  extractFilename(response) {
    const contentDisposition = response.headers.get('Content-Disposition');
    if (contentDisposition) {
      const match = contentDisposition.match(/filename="?([^"]+)"?/);
      return match ? match[1] : null;
    }
    return null;
  }

  triggerDownload(blob, filename) {
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  }
}
```

### Usage Example

```javascript
const api = new StockAnalysisAPI();

// Start comprehensive analysis with financial modeling
const jobResponse = await api.startAnalysis('NVDA', 'NVIDIA Corporation', null, {
  pipeline: 'comprehensive',
  model: 'dcf',
  years: 5,
  term_growth: 2.5,
  wacc: 8.5,
  strategy: 'moderate',
  max_articles: 40,
  min_score: 0.6
});

const jobId = jobResponse.job_id;

// Monitor progress with real-time updates
const eventSource = api.monitorJob(jobId, {
  status: (data) => {
    updateProgressBar(data.progress, getProgressPercentage(data.progress));
    updateStatusText(data.status);
  },
  log: (data) => {
    appendToLogDisplay(data.message);
  },
  final: (data) => {
    if (data.status === 'completed') {
      enableDownloadButtons(jobId);
      showSuccessMessage('Analysis completed successfully!');
    } else {
      showErrorMessage(`Analysis failed: ${data.message}`);
    }
  },
  error: (error) => {
    console.error('Monitoring error:', error);
    showErrorMessage('Connection lost. Refreshing status...');
    // Fallback to polling
    pollJobStatus(jobId);
  }
});

// Download various file types when completed
document.getElementById('download-report').onclick = () => {
  api.downloadFile(jobId, 'screening-report');
};

document.getElementById('download-financials').onclick = () => {
  api.downloadFile(jobId, 'financials-annual');
};

document.getElementById('download-model').onclick = () => {
  api.downloadFile(jobId, 'financial-model');
};

document.getElementById('download-price-explanation').onclick = () => {
  api.downloadFile(jobId, 'price-adjustment-explanation');
};

document.getElementById('download-all').onclick = () => {
  api.downloadFile(jobId, 'all-results');
};
```

---

## Pipeline Options and Financial Modeling

### Analysis Pipelines

The API supports multiple analysis pipelines to meet different use cases:

#### `comprehensive` (Default)
- Complete financial analysis with DCF modeling
- News sentiment analysis and article filtering
- Price target calculations with adjustment explanations
- Full model outputs including Excel files and detailed reports

#### `financial-only`
- Focus on financial data analysis
- Annual financials processing and modeling
- Financial ratios and trend analysis
- No news/article analysis

#### `model-only`
- Pure financial modeling without data scraping
- DCF and comparable company analysis
- Excel model generation
- Price target calculations

#### `news-only`
- News sentiment analysis only
- Article scraping and filtering
- Sentiment scoring and reporting
- No financial modeling

#### `model-to-price`
- Financial model to price target pipeline
- Focus on valuation outputs
- Price adjustment explanations
- Streamlined for price-focused analysis

#### `news-to-price`
- News sentiment to price impact analysis
- Sentiment-driven price adjustments
- Market reaction modeling
- News-based price target modifications

### Financial Modeling Parameters

#### Model Types
- **`dcf`**: Discounted Cash Flow analysis with terminal value calculations
- **`comparable`**: Comparable company analysis using industry multiples
- **`comprehensive`**: Combined DCF and comparable analysis for validation

#### Analysis Strategies
- **`conservative`**: Lower growth assumptions, higher discount rates
- **`moderate`**: Balanced assumptions based on historical performance
- **`aggressive`**: Higher growth projections, optimistic scenarios

#### Scaling Options
- **`conservative`**: 10-15% revenue growth assumptions
- **`moderate`**: 15-25% revenue growth assumptions  
- **`aggressive`**: 25%+ revenue growth assumptions

### Parameter Guidelines

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| `years` | 5 | 3-10 | Projection period for DCF model |
| `term_growth` | 2.5% | 1.0-4.0% | Terminal growth rate assumption |
| `wacc` | 8.0% | 5.0-15.0% | Weighted average cost of capital |
| `max_articles` | 30 | 10-100 | Maximum articles to analyze |
| `min_score` | 0.5 | 0.1-0.9 | Minimum relevance score threshold |
| `adjustment_cap` | 20.0% | 5.0-50.0% | Maximum price adjustment percentage |

---

## Progress Stages

The API tracks these main progress stages depending on the selected pipeline:

### Comprehensive Pipeline
1. **Pipeline initialized** (0-5%)
2. **Scraping articles** (5-20%)
3. **Filtering articles** (20-35%) 
4. **Processing financials** (35-50%)
5. **Running LLM analysis** (50-70%)
6. **Building financial model** (70-85%)
7. **Generating reports** (85-95%)
8. **Pipeline session completed** (100%)

### Financial-Only Pipeline
1. **Pipeline initialized** (0-10%)
2. **Processing financials** (10-40%)
3. **Building financial model** (40-80%)
4. **Generating reports** (80-95%)
5. **Pipeline session completed** (100%)

### Model-Only Pipeline
1. **Pipeline initialized** (0-15%)
2. **Building financial model** (15-80%)
3. **Calculating price targets** (80-95%)
4. **Pipeline session completed** (100%)

---

## File Structure and Organization

The API organizes generated files in a structured directory hierarchy:

```
/data/{TICKER}/
├── info.log                              # Complete analysis logs
├── screening_report.md                   # Main screening report  
├── screening_data.json                  # Structured analysis data
├── filtered_report.md                   # Filtered analysis report
├── searched/                            # Raw scraped articles
│   ├── article_001.md
│   ├── article_002.md
│   └── ...
├── filtered/                           # Filtered articles with scores
│   ├── filtered_01_score_8.5_article.md
│   ├── filtered_02_score_7.2_article.md
│   ├── filtered_articles_index.csv
│   └── ...
├── financials/                         # Financial data files
│   ├── financials_annual_modeling_latest.json
│   ├── financial_ratios_analysis.json
│   └── ...
└── models/                             # Financial models and explanations
    ├── financial_model_comprehensive_latest.xlsx
    ├── price_adjustment_explanation_latest.md
    ├── dcf_model_detailed.xlsx
    └── ...
```

### File Naming Conventions

- **Timestamped files**: Include `_latest` suffix for most recent version
- **Filtered articles**: Named as `filtered_{number}_score_{score}_{title}.md`
- **Model files**: Include model type and analysis date in filename
- **Financial data**: Organized by data type and frequency (annual, quarterly)

---

## Error Handling

### Common Error Codes

- `404`: Job not found
- `503`: Docker service not available
- `400`: Invalid request or missing API keys
- `500`: Internal server error

### Error Response Format

```json
{
  "detail": "Error description"
}
```

### Recommended Error Handling

```javascript
try {
  const response = await fetch('/run', { /* ... */ });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
  return await response.json();
} catch (error) {
  console.error('API Error:', error);
  // Handle specific error cases
  if (error.message.includes('Docker')) {
    showSystemError('Analysis service unavailable');
  } else if (error.message.includes('API keys')) {
    showConfigError('API configuration required');
  } else {
    showGenericError(error.message);
  }
}
```

---

## Best Practices

1. **Always monitor jobs via SSE** for real-time updates
2. **Check file availability** before showing download buttons
3. **Handle connection errors** with fallback polling
4. **Provide progress feedback** using percentage calculations
5. **Cache job status** to avoid unnecessary API calls
6. **Implement retry logic** for failed requests
7. **Show meaningful error messages** to users

---

## Rate Limiting

Currently no rate limiting is implemented, but consider implementing client-side throttling for frequent status checks.

---

## WebSocket Alternative

For applications requiring bidirectional communication, consider extending the API with WebSocket support for more efficient real-time updates.
