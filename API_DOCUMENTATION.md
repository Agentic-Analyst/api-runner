# API Endpoints for Stock Analysis Frontend Integration

## Updated API Endpoints

### 1. **Health Check** - `GET /`
```json
{
  "service": "Stock Analyst Runner",
  "status": "healthy", 
  "version": "1.0.0",
  "docker_available": true,
  "api_keys_configured": {
    "serpapi": true,
    "openai": true
  }
}
```

### 2. **Start Analysis** - `POST /run`
**Request:**
```json
{
  "ticker": "AAPL",
  "company": "Apple Inc.",
  "pipeline": "full"
}
```
**Response:**
```json
{
  "job_id": "uuid-here",
  "status": "started"
}
```

### 3. **Get Job Status** - `GET /jobs/{job_id}`
**Response:**
```json
{
  "job_id": "uuid-here",
  "status": "exited",
  "exit_code": 0,
  "ticker": "AAPL",
  "company": "Apple Inc.",
  "pipeline": "full",
  "logs_tail": "Container logs...",
  "outputs": {
    "files": {
      "searched/article1.txt": "/jobs/{job_id}/files/searched/article1.txt",
      "filtered/article2.txt": "/jobs/{job_id}/files/filtered/article2.txt",
      "screening_report.md": "/jobs/{job_id}/files/screening_report.md",
      "info.log": "/jobs/{job_id}/files/info.log"
    },
    "structure": {
      "searched_articles": ["article1.txt", "article2.txt"],
      "filtered_articles": ["article3.txt"],
      "screening_report": "screening_report.md",
      "info_log": "info.log"
    },
    "downloads": {
      "searched_articles_zip": "/jobs/{job_id}/download/searched-articles.zip",
      "filtered_articles_zip": "/jobs/{job_id}/download/filtered-articles.zip",
      "screening_report_pdf": "/jobs/{job_id}/download/screening-report.pdf"
    }
  }
}
```

### 4. **Real-time Thinking Process** - `GET /jobs/{job_id}/thinking/stream`
**Response:** Server-Sent Events (SSE) stream of the AI thinking process from info.log
```
data: Starting analysis for AAPL...
data: Searching for relevant articles...
data: Found 15 articles, filtering...
data: [DONE]
```

### 5. **Get Thinking Process** - `GET /jobs/{job_id}/thinking`
**Response:**
```json
{
  "job_id": "uuid-here",
  "ticker": "AAPL", 
  "thinking_process": "Full content of info.log file..."
}
```

### 6. **Download Individual Files** - `GET /jobs/{job_id}/files/{path}`
Returns the actual file content (searched/article1.txt, filtered/article2.txt, etc.)

### 7. **Download Searched Articles ZIP** - `GET /jobs/{job_id}/download/searched-articles.zip`
Returns a ZIP file containing all articles from `data/TICKER/searched/` folder

### 8. **Download Filtered Articles ZIP** - `GET /jobs/{job_id}/download/filtered-articles.zip`  
Returns a ZIP file containing all articles from `data/TICKER/filtered/` folder

### 9. **Download Screening Report PDF** - `GET /jobs/{job_id}/download/screening-report.pdf`
Converts `screening_report.md` to PDF and returns it

### 10. **List All Jobs** - `GET /jobs`
```json
{
  "jobs": [
    {
      "job_id": "uuid-here",
      "ticker": "AAPL",
      "company": "Apple Inc.", 
      "status": "exited",
      "created_at": 1691025600.123
    }
  ]
}
```

## Frontend Integration Flow

### 1. **Start Analysis & Show Progress**
```javascript
// Start analysis
const response = await fetch('/run', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ ticker: 'AAPL', company: 'Apple Inc.' })
});
const { job_id } = await response.json();

// Stream thinking process in real-time
const eventSource = new EventSource(`/jobs/${job_id}/thinking/stream`);
eventSource.onmessage = function(event) {
  if (event.data === '[DONE]') {
    eventSource.close();
  } else {
    // Display thinking process to user
    document.getElementById('thinking-log').innerHTML += event.data;
  }
};
```

### 2. **Check Status & Get Results**
```javascript
const checkStatus = async () => {
  const response = await fetch(`/jobs/${job_id}`);
  const jobData = await response.json();
  
  if (jobData.status === 'exited' && jobData.outputs) {
    // Show download buttons
    const downloads = jobData.outputs.downloads;
    
    // Create download buttons
    createDownloadButton('Searched Articles (ZIP)', downloads.searched_articles_zip);
    createDownloadButton('Filtered Articles (ZIP)', downloads.filtered_articles_zip);  
    createDownloadButton('Screening Report (PDF)', downloads.screening_report_pdf);
  }
};
```

### 3. **Download Files**
```javascript
const downloadFile = (url, filename) => {
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
};
```

## Key Features for Frontend

1. **Real-time Progress**: Stream the AI thinking process as it happens
2. **Organized Downloads**: 
   - Searched articles → ZIP file
   - Filtered articles → ZIP file  
   - Screening report → PDF file
3. **Individual File Access**: Access any specific file if needed
4. **Job Management**: List and track multiple analysis jobs
5. **Error Handling**: Proper HTTP status codes and error messages
