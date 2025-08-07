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
Starts a new stock analysis job.

**Request Body:**
```json
{
  "ticker": "NVDA",
  "company": "NVIDIA Corporation",
  "query": "recent earnings performance",
  "pipeline": "full"
}
```

**Parameters:**
- `ticker` (required): Stock ticker symbol
- `company` (optional): Company name (auto-inferred if not provided)
- `query` (optional): Custom search query
- `pipeline` (optional): Analysis pipeline type, default "full"

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
    "searched_articles": 9,
    "filtered_articles": 8
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
    "searched_articles": 9,
    "filtered_articles": 8,
    "articles_index": true,
    "filtered_index": true
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
  async startAnalysis(ticker, company = null, query = null) {
    const response = await fetch(`${this.baseUrl}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        ticker, 
        company, 
        query, 
        pipeline: 'full' 
      })
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

// Start analysis
const jobResponse = await api.startAnalysis('NVDA', 'NVIDIA Corporation');
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

// Download results when completed
document.getElementById('download-report').onclick = () => {
  api.downloadFile(jobId, 'screening-report');
};

document.getElementById('download-all').onclick = () => {
  api.downloadFile(jobId, 'all-results');
};
```

---

## Progress Stages

The API tracks these main progress stages:

1. **Pipeline initialized** (0-10%)
2. **Scraping articles** (10-25%)
3. **Filtering articles** (25-50%) 
4. **Running LLM analysis** (50-90%)
5. **Generating reports** (90-95%)
6. **Pipeline session completed** (100%)

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
