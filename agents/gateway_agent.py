#!/usr/bin/env python3
"""
gateway_agent.py - Natural Language Agent for Stock Analysis Pipeline

This module provides a natural language interface layer that interprets user requests
and maps them to appropriate main.py commands with proper parameters.

Features:
- Natural language understanding for stock analysis tasks
- Automatic extraction of ticker symbols, company names, and parameters
- Intelligent task mapping to pipeline stages
- Extensible command registry for easy addition of new capabilities
- Robust parameter validation and default handling

Usage Examples:
- "Analyze NVIDIA stock completely"
- "Do a DCF model for Apple with 7 years projection"
- "Get news analysis for Tesla"
- "Show me financial stats for Microsoft"
"""

import pathlib
import re
import json
import logging
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from llms.gpt import gpt_4o_mini

PROCESS_REQUEST: str = "prompts/process_request.md"

class GatewayAgent:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
    
    def process_request(self, request: str):
        """Process natural language request and return structured arguments."""
        try:
            # Load and format prompt
            prompt_path = pathlib.Path(PROCESS_REQUEST)
            with open(prompt_path, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
            
            prompt = prompt_template.format(request=request)
            msgs = [
                {"role": "system", "content": "You are a financial analyst that extracts structured information from user requests. Always return valid JSON with inferred pipeline and query when needed."},
                {"role": "user", "content": prompt},
            ]
            
            resp, _ = self.llm_function(msgs)
            
            # Parse and validate JSON response
            try:
                json_response = self.parse_llm_json(resp)
                self.logger.info(f"Parsed LLM response: {json_response}")
            except json.JSONDecodeError as e:
                raise ValueError(f"Failed to parse LLM JSON response: {e}\n{resp}")
            
            # Set default pipeline if not provided
            if 'pipeline' not in json_response:
                json_response['pipeline'] = 'comprehensive'
                self.logger.info("No pipeline specified, defaulting to 'comprehensive'")
            
            # Generate query if needed and not provided
            pipeline = json_response.get('pipeline', 'comprehensive')
            if pipeline in ['comprehensive', 'search-news', 'screen-news', 'news-to-price'] and 'query' not in json_response:
                # Auto-generate query based on company and common search patterns
                ticker = json_response.get('ticker', '')
                company = json_response.get('company_name', '')
                json_response['query'] = self.generate_search_query(ticker, company, request)
                self.logger.info(f"Auto-generated query for {pipeline}: {json_response['query']}")
            
            # Final validation
            if not self.validate_args(json_response):
                raise ValueError(f"Could not identify required parameters from request\n{resp}")

            self.logger.info(f"Successfully processed request for {json_response.get('ticker', 'Unknown')} with pipeline: {json_response.get('pipeline', 'Unknown')}")
            return json_response
            
        except Exception as e:
            raise ValueError(f"Failed to process NL Agent request: {e}")
    
    def generate_search_query(self, ticker: str, company: str, original_request: str) -> str:
        """Generate a search query based on ticker, company, and original request."""
        # Extract key terms from the original request
        request_lower = original_request.lower()
        
        # Common financial terms to look for
        financial_terms = {
            'earnings': ['earnings', 'profit', 'income', 'revenue'],
            'growth': ['growth', 'expansion', 'increase'],
            'decline': ['decline', 'drop', 'fall', 'decrease'],
            'merger': ['merger', 'acquisition', 'deal', 'buyout'],
            'product': ['product', 'launch', 'release', 'new'],
            'guidance': ['guidance', 'outlook', 'forecast', 'projection'],
            'regulatory': ['regulation', 'regulatory', 'legal', 'lawsuit'],
            'competition': ['competition', 'competitor', 'rival', 'vs']
        }
        
        # Check for specific terms in the request
        detected_terms = []
        for category, terms in financial_terms.items():
            if any(term in request_lower for term in terms):
                detected_terms.append(category)
        
        # Build query based on detected terms
        if detected_terms:
            primary_term = detected_terms[0]
            query = f"{ticker} {primary_term}"
        else:
            # Default to recent news
            query = f"{ticker} news"
        
        # Add temporal context if mentioned
        if any(word in request_lower for word in ['recent', 'latest', 'new', 'today']):
            query += " latest"
        elif any(word in request_lower for word in ['q1', 'q2', 'q3', 'q4', 'quarter']):
            query += " quarterly"
        elif any(word in request_lower for word in ['2024', '2025']):
            year_match = re.search(r'202[4-9]', original_request)
            if year_match:
                query += f" {year_match.group()}"
        
        return query

    def llm_function(self, msgs: List[Dict[str, str]]) -> Tuple[str, float]:
        """Call the LLM function."""
        return gpt_4o_mini(msgs, temperature=0.1)

    def parse_llm_json(self, text: str):
        if not isinstance(text, str):
            # If your llm_function already returns a dict sometimes
            return text

        s = text.strip()

        # 1) If there is a fenced code block, grab its contents
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s, flags=re.IGNORECASE)
        if m:
            s = m.group(1).strip()

        # 2) If it still doesn't look like JSON, try to extract the first {...}
        if not s.startswith("{"):
            m2 = re.search(r"\{[\s\S]*\}", s)
            if m2:
                s = m2.group(0).strip()

        # 3) Normalize curly/smart quotes just in case
        s = (
            s.replace("\u201c", '"').replace("\u201d", '"')  # double quotes
            .replace("\u2018", "'").replace("\u2019", "'")  # single quotes
            .strip()
        )

        # 4) Parse JSON
        parsed = json.loads(s)
        
        # 5) Post-process to fix common LLM mistakes with data types
        if isinstance(parsed, dict):
            cleaned = {}
            for key, value in parsed.items():
                # Special handling for peers - convert list to comma-separated string
                if key == 'peers' and isinstance(value, list):
                    if len(value) > 0:
                        # Join all values with commas, stripping whitespace and converting to uppercase
                        peer_list = [str(v).strip().upper() for v in value if str(v).strip()]
                        if peer_list:
                            cleaned[key] = ','.join(peer_list)
                            self.logger.info(f"Converting peer list to comma-separated string: {value} -> {cleaned[key]}")
                        else:
                            self.logger.warning(f"Empty peer list after filtering: {value}")
                            # Skip empty peer lists
                            continue
                    else:
                        self.logger.warning(f"Ignoring empty peer list for {key}")
                        # Skip empty lists
                        continue
                # Convert single-item lists to their single values (for non-peers)
                elif isinstance(value, list) and len(value) == 1:
                    self.logger.warning(f"Converting single-item list to value for {key}: {value} -> {value[0]}")
                    cleaned[key] = value[0]
                elif isinstance(value, list) and len(value) == 0:
                    self.logger.warning(f"Ignoring empty list for {key}")
                    # Skip empty lists
                    continue
                elif isinstance(value, list) and len(value) > 1:
                    self.logger.warning(f"Multiple values in list for {key}: {value}, taking first: {value[0]}")
                    cleaned[key] = value[0]
                else:
                    cleaned[key] = value
            return cleaned
        
        return parsed

    def validate_args(self, args: Dict[str, Any]) -> bool:
        """
        Validate the extracted arguments for the pipeline.

        Args:
            args: A dictionary of arguments to validate.

        Returns:
            True if all arguments are valid, False otherwise.
        """
        ticker = args.get("ticker")
        pipeline = args.get("pipeline", "comprehensive")
        
        if not ticker:
            self.logger.warning("Ticker is missing from extraction")
            return False
        
        # Validate pipeline value
        valid_pipelines = ["comprehensive", "financial-statements", "financial-model", 
                          "search-news", "screen-news", "news-to-price"]
        if pipeline not in valid_pipelines:
            self.logger.warning(f"Invalid pipeline: {pipeline}. Must be one of {valid_pipelines}")
            return False
        
        # Check query requirement for news-related pipelines
        news_pipelines = ["comprehensive", "search-news", "screen-news", "news-to-price"]
        if pipeline in news_pipelines and not args.get("query"):
            self.logger.warning(f"Query is required for pipeline: {pipeline}")
            return False
            
        self.logger.info("All required arguments are identified from the user request.")
        return True
    
