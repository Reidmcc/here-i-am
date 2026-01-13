"""
NavigatorClient for Codebase Navigator.

Handles communication with the Mistral/Devstral API for codebase navigation queries.
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional, Union, List

import httpx

from .models import (
    NavigatorResponse,
    NavigatorQuery,
    RelevantFile,
    CodeSection,
    CodebaseChunk,
    CodebaseIndex,
    QueryType,
)
from .cache import NavigatorCache, CacheKey
from .indexer import CodebaseIndexer
from .exceptions import (
    NavigatorAPIError,
    InvalidResponseError,
    RateLimitError,
    NavigatorNotConfiguredError,
)

logger = logging.getLogger(__name__)

# Navigator system prompts
NAVIGATOR_SYSTEM_PROMPT = """You are a codebase navigation assistant. Your role is to analyze codebases and identify which files and code sections are relevant to a given task.

You will receive:
1. A task description explaining what the developer wants to accomplish
2. The contents of a codebase (or portion thereof) with file paths marked

Your job is to:
1. Identify all files that are relevant to completing the task
2. Explain WHY each file is relevant
3. Point to specific functions, classes, or code sections when possible
4. Note any dependencies or related code that might be affected
5. Provide a brief architectural overview if it helps understand the codebase

You must respond in valid JSON matching the specified schema.

Be thorough but precise:
- Include files that are DIRECTLY relevant (will need modification)
- Include files that provide CONTEXT (help understand how things work)
- Include files that might be AFFECTED (dependencies, tests, etc.)
- Do NOT include files that are merely tangentially related

When uncertain, err on the side of inclusion with a "low" relevance rating."""

QUERY_TYPE_INSTRUCTIONS = {
    QueryType.RELEVANCE: """
Focus on finding files relevant to implementing the task. Consider:
- Files that will need direct modification
- Files that provide context for understanding the existing implementation
- Files that might be affected by changes (tests, dependencies)
""",
    QueryType.STRUCTURE: """
Focus on explaining the architecture and organization of the codebase. Consider:
- How different components relate to each other
- Key entry points and main modules
- Patterns and conventions used
- Important configuration files
""",
    QueryType.DEPENDENCIES: """
Focus on tracing dependencies and imports. Consider:
- What modules/packages the relevant code depends on
- What other code depends on the relevant files
- External dependencies that might be involved
- Import chains and module relationships
""",
    QueryType.ENTRY_POINTS: """
Focus on identifying where to start modifications. Consider:
- Main entry points for the functionality
- Key functions/classes that would need changes
- Configuration that might need updates
- The logical order of changes
""",
    QueryType.IMPACT: """
Focus on assessing the potential impact of changes. Consider:
- What other code depends on the files that would change
- Tests that would need updating
- Documentation that might need changes
- Breaking changes and backwards compatibility
""",
}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "relevant_files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "file path"},
                    "relevance": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reason": {"type": "string", "description": "why this file is relevant"},
                    "specific_sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"},
                                "name": {"type": "string"},
                                "description": {"type": "string"}
                            },
                            "required": ["start_line", "end_line"]
                        }
                    }
                },
                "required": ["path", "relevance", "reason"]
            }
        },
        "architecture_notes": {"type": "string"},
        "suggested_approach": {"type": "string"},
        "dependencies_to_consider": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1}
    },
    "required": ["relevant_files", "confidence"]
}


class NavigatorClient:
    """
    Client for querying the codebase navigator (Devstral 2).

    Responsibilities:
    - Format codebase + query into navigator prompts
    - Call Mistral API with appropriate parameters
    - Parse and validate structured responses
    - Handle errors and retries
    - Cache results for repeated queries
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "devstral-large-2501",
        timeout: int = 120,
        max_retries: int = 3,
        cache_dir: Optional[Path] = None,
        cache_ttl_hours: int = 24,
        cache_enabled: bool = True,
    ):
        """
        Initialize the navigator client.

        Args:
            api_key: Mistral API key (falls back to settings if not provided)
            model: Model ID to use (default: devstral-large-2501)
            timeout: API timeout in seconds
            max_retries: Maximum number of retries for transient errors
            cache_dir: Directory for caching responses
            cache_ttl_hours: Cache TTL in hours
            cache_enabled: Whether to enable caching
        """
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._base_url = "https://api.mistral.ai/v1"

        # Initialize cache
        if cache_dir and cache_enabled:
            self._cache = NavigatorCache(
                cache_dir=cache_dir,
                ttl_hours=cache_ttl_hours,
                enabled=cache_enabled,
            )
        else:
            self._cache = None

    @property
    def api_key(self) -> str:
        """Get the API key, falling back to settings if not provided."""
        if self._api_key:
            return self._api_key
        # Fall back to settings
        from app.config import settings
        if settings.mistral_api_key:
            return settings.mistral_api_key
        raise NavigatorNotConfiguredError("Mistral API key not configured")

    def _build_query_prompt(
        self,
        task: str,
        codebase_content: str,
        query_type: QueryType = QueryType.RELEVANCE,
    ) -> str:
        """Build the user prompt for a query."""
        prompt = f"""## Task Description

{task}

## Query Type Instructions
{QUERY_TYPE_INSTRUCTIONS.get(query_type, "")}

## Codebase Contents

{codebase_content}

## Instructions

Analyze this codebase and identify all files relevant to the task described above.

Respond with JSON in this exact format:
{{
  "relevant_files": [
    {{
      "path": "string - file path",
      "relevance": "high|medium|low",
      "reason": "string - why this file is relevant",
      "specific_sections": [
        {{
          "start_line": number,
          "end_line": number,
          "name": "string or null - function/class name",
          "description": "string - what this section does"
        }}
      ]
    }}
  ],
  "architecture_notes": "string or null - brief explanation of code organization",
  "suggested_approach": "string or null - recommended way to tackle this task",
  "dependencies_to_consider": ["string - other files that might need updates"],
  "confidence": number between 0 and 1
}}

IMPORTANT: Your response must be valid JSON only. Do not include any text before or after the JSON."""
        return prompt

    def _parse_response(self, response_text: str) -> dict:
        """Parse the JSON response from the navigator."""
        # Try to extract JSON from the response
        response_text = response_text.strip()

        # Try direct JSON parse first
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from markdown code blocks
        json_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", response_text)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find JSON object in the response
        json_match = re.search(r'\{[\s\S]*"relevant_files"[\s\S]*\}', response_text)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        raise InvalidResponseError(
            "Could not parse JSON from navigator response",
            raw_response=response_text[:1000]
        )

    async def _call_api(
        self,
        messages: list,
        retry_count: int = 0,
    ) -> tuple[str, int]:
        """
        Call the Mistral API.

        Returns:
            Tuple of (response_text, tokens_used)
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": messages,
                        "temperature": 0,  # Deterministic for consistency
                        "response_format": {"type": "json_object"},
                    },
                )

                if response.status_code == 429:
                    # Rate limited
                    retry_after = int(response.headers.get("Retry-After", 60))
                    raise RateLimitError(retry_after=retry_after)

                if response.status_code != 200:
                    raise NavigatorAPIError(
                        f"API returned status {response.status_code}",
                        status_code=response.status_code,
                        response_body=response.text,
                    )

                data = response.json()
                content = data["choices"][0]["message"]["content"]
                tokens_used = data.get("usage", {}).get("total_tokens", 0)

                return content, tokens_used

        except httpx.TimeoutException:
            if retry_count < self._max_retries:
                # Increase timeout and retry
                await asyncio.sleep(2 ** retry_count)
                return await self._call_api(messages, retry_count + 1)
            raise NavigatorAPIError("API request timed out after retries")

        except RateLimitError as e:
            if retry_count < self._max_retries:
                wait_time = e.retry_after or (2 ** retry_count)
                logger.warning(f"Rate limited, waiting {wait_time}s before retry")
                await asyncio.sleep(wait_time)
                return await self._call_api(messages, retry_count + 1)
            raise

        except httpx.RequestError as e:
            if retry_count < self._max_retries:
                await asyncio.sleep(2 ** retry_count)
                return await self._call_api(messages, retry_count + 1)
            raise NavigatorAPIError(f"API request failed: {str(e)}")

    async def query(
        self,
        task: str,
        codebase: Union[CodebaseChunk, "CodebaseIndex"],
        query_type: QueryType = QueryType.RELEVANCE,
        indexer: Optional["CodebaseIndexer"] = None,
        bypass_cache: bool = False,
    ) -> NavigatorResponse:
        """
        Query the navigator about code relevance for a task.

        Args:
            task: Natural language description of what the AI entity is trying to do
            codebase: The indexed codebase or a specific chunk
            query_type: Type of query (relevance, structure, dependencies, etc.)
            indexer: Optional indexer for formatting chunks (required if codebase is CodebaseChunk)
            bypass_cache: Skip cache lookup (still writes to cache)

        Returns:
            Structured response with relevant files and explanations
        """
        # Determine codebase content
        if isinstance(codebase, CodebaseChunk):
            if not indexer:
                raise ValueError("indexer is required when passing a CodebaseChunk")
            codebase_content = indexer.format_chunk_for_query(codebase)
            codebase_hash = "chunk_" + str(codebase.chunk_id)
            chunks_queried = 1
        else:
            # CodebaseIndex - need an indexer to get the actual content
            raise ValueError("Pass a CodebaseChunk, not CodebaseIndex directly")

        # Check cache
        if self._cache and not bypass_cache:
            cache_key = CacheKey.from_task(codebase_hash, task, query_type)
            cached = self._cache.get(cache_key)
            if cached:
                logger.info("Returning cached navigator response")
                return cached

        # Build messages
        messages = [
            {"role": "system", "content": NAVIGATOR_SYSTEM_PROMPT},
            {"role": "user", "content": self._build_query_prompt(task, codebase_content, query_type)},
        ]

        # Call API
        logger.info(f"Querying navigator for task: {task[:100]}...")
        response_text, tokens_used = await self._call_api(messages)

        # Parse response
        try:
            response_data = self._parse_response(response_text)
        except InvalidResponseError:
            # Retry once with stricter prompt
            logger.warning("Invalid response, retrying with stricter prompt")
            messages.append({"role": "assistant", "content": response_text})
            messages.append({
                "role": "user",
                "content": "Your response was not valid JSON. Please respond with ONLY the JSON object, no other text."
            })
            response_text, additional_tokens = await self._call_api(messages)
            tokens_used += additional_tokens
            response_data = self._parse_response(response_text)

        # Build response object
        response = NavigatorResponse(
            relevant_files=[
                RelevantFile.from_dict(f) for f in response_data.get("relevant_files", [])
            ],
            architecture_notes=response_data.get("architecture_notes"),
            suggested_approach=response_data.get("suggested_approach"),
            dependencies_to_consider=response_data.get("dependencies_to_consider"),
            confidence=response_data.get("confidence", 0.5),
            tokens_used=tokens_used,
            chunks_queried=chunks_queried,
            query_type=query_type,
            cached=False,
        )

        # Cache the response
        if self._cache:
            cache_key = CacheKey.from_task(codebase_hash, task, query_type)
            self._cache.set(cache_key, response)

        return response

    async def query_with_indexer(
        self,
        task: str,
        indexer: CodebaseIndexer,
        query_type: QueryType = QueryType.RELEVANCE,
        bypass_cache: bool = False,
    ) -> NavigatorResponse:
        """
        Query the navigator using an indexer directly.

        This is the recommended method for most use cases.

        Args:
            task: Natural language description of what the AI entity is trying to do
            indexer: The codebase indexer (must be indexed already)
            query_type: Type of query
            bypass_cache: Skip cache lookup

        Returns:
            Structured response with relevant files and explanations
        """
        # Get codebase hash for caching
        codebase_hash = indexer.get_codebase_hash()

        # Check cache
        if self._cache and not bypass_cache:
            cache_key = CacheKey.from_task(codebase_hash, task, query_type)
            cached = self._cache.get(cache_key)
            if cached:
                logger.info("Returning cached navigator response")
                return cached

        # Check if single chunk is sufficient
        single_chunk = indexer.get_single_chunk_if_small()
        if single_chunk:
            response = await self.query(
                task=task,
                codebase=single_chunk,
                query_type=query_type,
                indexer=indexer,
                bypass_cache=True,  # We already checked cache above
            )
        else:
            # Multi-chunk query
            response = await self._query_chunked_internal(
                task=task,
                indexer=indexer,
                query_type=query_type,
            )

        # Cache the merged response
        if self._cache:
            cache_key = CacheKey.from_task(codebase_hash, task, query_type)
            self._cache.set(cache_key, response)

        return response

    async def _query_chunked_internal(
        self,
        task: str,
        indexer: CodebaseIndexer,
        query_type: QueryType,
    ) -> NavigatorResponse:
        """Internal method to query multiple chunks and merge results."""
        chunks = indexer.get_all_chunks()

        logger.info(f"Querying {len(chunks)} chunks")

        # Query each chunk
        responses: List[NavigatorResponse] = []
        total_tokens = 0

        for chunk in chunks:
            response = await self.query(
                task=task,
                codebase=chunk,
                query_type=query_type,
                indexer=indexer,
                bypass_cache=True,
            )
            responses.append(response)
            total_tokens += response.tokens_used

        # Merge responses
        return self._merge_responses(responses, total_tokens, query_type)

    def _merge_responses(
        self,
        responses: List[NavigatorResponse],
        total_tokens: int,
        query_type: QueryType,
    ) -> NavigatorResponse:
        """Merge responses from multiple chunks."""
        if not responses:
            return NavigatorResponse(
                relevant_files=[],
                confidence=0.0,
                tokens_used=total_tokens,
                chunks_queried=0,
                query_type=query_type,
            )

        # Merge relevant files, deduplicating by path
        files_by_path: dict[str, RelevantFile] = {}
        for response in responses:
            for file in response.relevant_files:
                if file.path in files_by_path:
                    existing = files_by_path[file.path]
                    # Take higher relevance
                    relevance_order = {"high": 3, "medium": 2, "low": 1}
                    if relevance_order.get(file.relevance, 0) > relevance_order.get(existing.relevance, 0):
                        files_by_path[file.path] = file
                    else:
                        # Merge reasons if different
                        if file.reason and file.reason not in existing.reason:
                            existing.reason = f"{existing.reason}; {file.reason}"
                        # Merge sections
                        if file.specific_sections:
                            if existing.specific_sections:
                                existing.specific_sections.extend(file.specific_sections)
                            else:
                                existing.specific_sections = file.specific_sections
                else:
                    files_by_path[file.path] = file

        # Collect architecture notes and suggested approaches
        architecture_notes = []
        suggested_approaches = []
        all_dependencies = set()

        for response in responses:
            if response.architecture_notes:
                architecture_notes.append(response.architecture_notes)
            if response.suggested_approach:
                suggested_approaches.append(response.suggested_approach)
            if response.dependencies_to_consider:
                all_dependencies.update(response.dependencies_to_consider)

        # Calculate average confidence
        avg_confidence = sum(r.confidence for r in responses) / len(responses)

        return NavigatorResponse(
            relevant_files=list(files_by_path.values()),
            architecture_notes="\n\n".join(architecture_notes) if architecture_notes else None,
            suggested_approach="\n\n".join(suggested_approaches) if suggested_approaches else None,
            dependencies_to_consider=list(all_dependencies) if all_dependencies else None,
            confidence=avg_confidence,
            tokens_used=total_tokens,
            chunks_queried=len(responses),
            query_type=query_type,
            cached=False,
        )

    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        if self._cache:
            return self._cache.get_stats()
        return {"enabled": False}

    def invalidate_cache(self, codebase_hash: str) -> int:
        """Invalidate cached entries for a codebase."""
        if self._cache:
            return self._cache.invalidate_codebase(codebase_hash)
        return 0

    def close(self) -> None:
        """Close resources."""
        if self._cache:
            self._cache.close()
