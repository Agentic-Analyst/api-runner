# Natural Language API Integration

## Overview

The API now includes natural language processing capabilities through the `/nl/request` endpoint, allowing users to submit analysis requests in plain English.

## How It Works

1. **Natural Language Processing**: User requests are processed by the NLAgent using OpenAI to extract structured parameters
2. **Parameter Merging**: Extracted parameters are merged with any explicitly provided parameters (provided parameters take priority)
3. **Job Execution**: The processed request follows the standard job analysis flow

## API Endpoints

### `/nl/request` - Process Natural Language Request

**POST** `/nl/request`

Process a natural language request for stock analysis.

#### Request Body

```json
{
  "request": "Analyze NVIDIA stock completely with 7 years projection",
  "email": "user@example.com",
  "ticker": "NVDA",  // Optional override
  "company": "NVIDIA Corporation",  // Optional override
  "years": 5  // Optional override - will use 7 from NL if not provided
}
```

#### Key Fields

- `request` (required): Natural language description of the analysis task
- `email` (required): User email for data organization
- All other fields are optional overrides that take priority over NL-extracted values

#### Response

```json
{
  "job_id": "NVDA_20250901_143022",
  "status": "pending", 
  "message": "Natural language analysis job started for NVDA"
}
```

### `/nl/test` - Test NL Processing (Debug)

**POST** `/nl/test`

Test what parameters would be extracted from a natural language request without starting a job.

#### Request Body
Same as `/nl/request`

#### Response
```json
{
  "original_request": "Analyze NVIDIA stock completely with 7 years projection",
  "extracted_parameters": {
    "ticker": "NVDA",
    "company": "NVIDIA Corporation", 
    "pipeline": "comprehensive",
    "years": 7,
    // ... other parameters
  },
  "provided_overrides": {
    "email": "user@example.com"
    // ... any explicitly provided parameters
  }
}
```

## Example Usage

### Basic Natural Language Request
```bash
curl -X POST "http://localhost:8080/nl/request" \
  -H "Content-Type: application/json" \
  -d '{
    "request": "I want a comprehensive analysis of Tesla stock with DCF modeling",
    "email": "analyst@company.com"
  }'
```

### With Parameter Overrides
```bash
curl -X POST "http://localhost:8080/nl/request" \
  -H "Content-Type: application/json" \
  -d '{
    "request": "Analyze Apple stock", 
    "email": "analyst@company.com",
    "ticker": "AAPL",
    "years": 10,
    "pipeline": "financial-only"
  }'
```

### Test NL Processing
```bash
curl -X POST "http://localhost:8080/nl/test" \
  -H "Content-Type: application/json" \
  -d '{
    "request": "Do a quick DCF analysis for Microsoft with 3 years projection",
    "email": "test@example.com"
  }'
```

## Parameter Priority

1. **Explicitly provided parameters** (in the request JSON) - highest priority
2. **NL-extracted parameters** (from the `request` field) - medium priority  
3. **System defaults** - lowest priority (used when neither provided nor extracted)

## Error Handling

- **400 Bad Request**: Invalid request format or NL processing failed
- **401 Unauthorized**: Invalid authentication (if auth is required)
- **503 Service Unavailable**: Docker or API keys not configured

## Requirements

- OpenAI API key must be configured for NL processing
- All standard job analysis requirements (Docker, SERPAPI, etc.)
