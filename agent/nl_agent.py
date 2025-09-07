#!/usr/bin/env python3
"""
nl_agent.py - Natural Language Agent for Stock Analysis Pipeline

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

class NLAgent:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
    
    def process_request(self, request: str):
        """Process natural language request and return structured arguments."""
        try:
            # First attempt with LLM
            prompt_path = pathlib.Path(PROCESS_REQUEST)
            with open(prompt_path, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
            
            # Format the prompt with request
            prompt = prompt_template.format(request=request)
            msgs = [
                {"role": "system", "content": "You are a financial analyst that extracts structured information from user requests. Always return valid JSON."},
                {"role": "user", "content": prompt},
            ]
            
            resp, _ = self.llm_function(msgs)
            
            # Parse JSON response
            try:
                json_response = self.parse_llm_json(resp)
            except json.JSONDecodeError as e:
                raise ValueError(f"Failed to parse LLM JSON response: {e}\n{resp}")
            
            # Final validation
            if not self.validate_args(json_response):
                raise ValueError(f"Could not identify ticker and company from request\n{resp}")

            self.logger.info(f"Successfully processed request for {json_response.get('ticker', 'Unknown')}")
            return json_response
            
        except Exception as e:
            raise ValueError(f"Failed to process NL Agent request: {e}")

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
                # Convert single-item lists to their single values
                if isinstance(value, list) and len(value) == 1:
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
        company = args.get("company_name")
        
        if not ticker or not company:
            self.logger.warning("Ticker and/or company name missing from extraction")
            return False
            
        # Basic ticker format validation (2-5 uppercase letters, possibly with dash)
        # if not re.match(r'^[A-Z]{2,5}(-[A-Z])?$', ticker):
        #     self.logger.warning(f"Ticker format invalid: {ticker}")
        #     return False
            
        self.logger.info("All required arguments are identified from the user request.")
        return True
    
