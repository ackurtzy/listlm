"""Search execution using the OpenAI web-search-capable model."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Sequence

from core.llm_client import LLMClient
from core.model_registry import ModelRegistry
from core.models import NormalizedRow, SearchTask


class SearchExecutor:
    """Executes web searches for the approved search tasks."""

    def __init__(
        self,
        llm_client: LLMClient,
        model_registry: ModelRegistry,
        strategy_map: Dict[str, Dict[str, str]],
        *,
        use_mock_search: bool = False,
    ) -> None:
        self._llm_client = llm_client
        self._model_registry = model_registry
        self._strategy_map = strategy_map
        self._use_mock_search = use_mock_search
        self._logger = logging.getLogger(self.__class__.__name__)

    def run_task(
        self,
        task: SearchTask,
        schema: Sequence[str],
    ) -> List[NormalizedRow]:
        """Runs the provided task and returns normalized rows."""
        if self._use_mock_search:
            return self._mock_results(task, schema)

        strategy_params = self._strategy_map.get(task.strategy, {})
        system_prompt = (
            "You are a researcher using the OpenAI web search tool. "
            "Return JSON with an `items` array. Each item should include "
            "the schema fields plus `title`, `url`, `snippet`, and `source`."
        )
        user_payload = {
            "query": task.query,
            "strategy": task.strategy,
            "schema": list(schema),
            "parameters": strategy_params,
        }
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Execute the query and return results as JSON. "
                    "Payload:\n" + json.dumps(user_payload)
                ),
            },
        ]
        response_format = {"type": "json_object"}
        tools = [{"type": "web_search"}]
        result = self._llm_client.complete(
            model=self._model_registry.for_web(),
            messages=messages,
            response_format=response_format,
            tools=tools,
            step_name=f"web_{task.id}",
            metadata={"query": task.query, "strategy": task.strategy},
        )
        return self._parse_results(result.text, result.raw, schema, task)

    def _parse_results(
        self,
        raw_text: str,
        raw_payload: Dict[str, Any],
        schema: Sequence[str],
        task: SearchTask,
    ) -> List[NormalizedRow]:
        """Parses the payload and returns normalized rows."""
        rows = self._parse_json_items(raw_text, schema, task)
        if rows:
            self._logger.debug(
                "Task %s parsed %d rows via JSON output.", task.id, len(rows)
            )
            return rows
        rows = self._parse_from_annotations(raw_payload, schema, task)
        self._logger.debug(
            "Task %s parsed %d rows via annotations fallback.", task.id, len(rows)
        )
        return rows

    def _parse_json_items(
        self,
        raw_text: str,
        schema: Sequence[str],
        task: SearchTask,
    ) -> List[NormalizedRow]:
        """Attempts to parse structured JSON returned by the model."""
        if raw_text.strip():
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                items = payload.get("items")
                if isinstance(items, list):
                    rows: List[NormalizedRow] = []
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        values = {
                            column: str(item.get(column, "") or "")
                            for column in schema
                        }
                        title = str(
                            item.get("title")
                            or item.get("name")
                            or values.get("name")
                            or ""
                        )
                        title = self._clean_text(title)
                        if title:
                            values.setdefault("name", title)
                            values.setdefault("title", title)
                        snippet = str(item.get("snippet") or "")
                        snippet = self._clean_text(snippet)
                        if "description" in values and not values["description"]:
                            values["description"] = snippet or title
                        url = str(item.get("url") or item.get("link") or "")
                        url = url.strip()
                        if url:
                            values["url"] = url
                            values["source_domain"] = self._extract_domain(url)
                            if "source" in values and not values["source"]:
                                values["source"] = values["source_domain"]
                        rows.append(
                            NormalizedRow(
                                values=values,
                                source_query_id=task.id,
                                source_strategy=task.strategy,
                            )
                        )
                    return rows
        return []

    def _parse_from_annotations(
        self,
        raw_payload: Dict[str, Any],
        schema: Sequence[str],
        task: SearchTask,
    ) -> List[NormalizedRow]:
        """Builds rows from citation annotations when JSON is unavailable."""
        output_blocks = raw_payload.get("output")
        if not isinstance(output_blocks, list):
            return []
        rows: List[NormalizedRow] = []
        for block in output_blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "message":
                continue
            contents = block.get("content")
            if not isinstance(contents, list):
                continue
            for content in contents:
                if not isinstance(content, dict):
                    continue
                text = content.get("text", "")
                annotations = content.get("annotations", [])
                if not isinstance(annotations, list):
                    continue
                for annotation in annotations:
                    if not isinstance(annotation, dict):
                        continue
                    if annotation.get("type") != "url_citation":
                        continue
                    snippet = self._slice_text(
                        text,
                        annotation.get("start_index"),
                        annotation.get("end_index"),
                    )
                    rows.append(
                        NormalizedRow(
                            values=self._build_values(schema, annotation, snippet),
                            source_query_id=task.id,
                            source_strategy=task.strategy,
                        )
                    )
        return rows

    def _build_values(
        self,
        schema: Sequence[str],
        annotation: Dict[str, Any],
        snippet: str,
    ) -> Dict[str, str]:
        """Constructs schema-aligned values from an annotation."""
        values = {column: "" for column in schema}
        title = self._clean_text(str(annotation.get("title", "") or ""))
        url = str(annotation.get("url", "") or "").strip()
        domain = self._extract_domain(url) if url else ""

        if "name" in values:
            values["name"] = title
        else:
            values["name"] = title
        if "title" in values and not values["title"]:
            values["title"] = title
        else:
            values.setdefault("title", title)
        if "url" in values:
            values["url"] = url
        values["url"] = url
        if "link" in values and not values["link"]:
            values["link"] = url
        values.setdefault("link", url)
        if "description" in values:
            values["description"] = self._clean_text(snippet or title)
        else:
            values.setdefault("description", self._clean_text(snippet or title))
        if "source" in values and domain:
            values["source"] = domain
        values["source_domain"] = domain
        return values

    @staticmethod
    def _slice_text(text: str, start: Any, end: Any) -> str:
        """Returns a snippet of text for the annotation span."""
        if not isinstance(start, int) or not isinstance(end, int):
            return text.strip()
        if start < 0 or end <= start or end > len(text):
            return text.strip()
        return text[start:end].strip()

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extracts the domain portion of a URL."""
        if "://" in url:
            url = url.split("://", 1)[1]
        return url.split("/", 1)[0]

    @staticmethod
    def _clean_text(text: str) -> str:
        """Removes basic Markdown markers from a snippet."""
        text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _mock_results(
        self,
        task: SearchTask,
        schema: Sequence[str],
    ) -> List[NormalizedRow]:
        """Returns deterministic mock results for testing."""
        values = {column: f"{task.query} - {column}" for column in schema}
        row = NormalizedRow(
            values=values,
            source_query_id=task.id,
            source_strategy=task.strategy,
        )
        return [row]
