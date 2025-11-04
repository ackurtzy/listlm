"""Utility functions for parsing LLM responses."""

from __future__ import annotations

import json
import re
from typing import Iterable, List

from core.models import SearchTask


def parse_search_tasks(
    raw_text: str,
    *,
    batch_index: int = 0,
    default_strategy: str = "web",
) -> List[SearchTask]:
    """Parses search tasks from a Responses API text payload."""
    tasks: List[SearchTask] = []
    try:
        payload = json.loads(raw_text)
        if isinstance(payload, dict):
            payload = payload.get("searches") or payload.get("tasks") or []
        if not isinstance(payload, list):
            raise ValueError("Expected list payload for search tasks.")
        seen_ids: Dict[str, int] = {}
        for idx, item in enumerate(payload):
            if not isinstance(item, dict):
                continue
            base_id = str(item.get("id") or f"{batch_index}_{idx}")
            suffix = seen_ids.get(base_id, 0)
            task_id = base_id if suffix == 0 else f"{base_id}_{suffix}"
            seen_ids[base_id] = suffix + 1
            query = str(item.get("query", "")).strip()
            strategy = str(item.get("strategy") or default_strategy).strip()
            rationale = item.get("rationale")
            if not query:
                continue
            tasks.append(
                SearchTask(
                    id=task_id,
                    query=query,
                    strategy=strategy or default_strategy,
                    rationale=str(rationale).strip() if rationale else None,
                )
            )
    except json.JSONDecodeError:
        tasks = _fallback_parse_lines(raw_text, batch_index, default_strategy)
    return tasks


def parse_filter_ids(raw_text: str) -> List[str]:
    """Parses an ordered list of IDs from a filter response."""
    try:
        payload = json.loads(raw_text)
        if isinstance(payload, dict):
            payload = payload.get("ids") or payload.get("keep") or []
        if isinstance(payload, list):
            return [str(item).strip() for item in payload if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [
        match.group(1)
        for match in re.finditer(r"\b([A-Za-z0-9_\-]+)\b", raw_text)
    ]


def parse_schema(raw_text: str) -> List[str]:
    """Parses schema column names."""
    try:
        payload = json.loads(raw_text)
        if isinstance(payload, dict):
            payload = payload.get("columns") or payload.get("fields") or []
        if isinstance(payload, list):
            return [str(item).strip() for item in payload if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [segment.strip() for segment in raw_text.split(",") if segment.strip()]


def parse_refined_companies(raw_text: str) -> List[dict]:
    """Parses refined company dictionaries returned by the post-processor."""
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return []

    candidates = None
    if isinstance(payload, dict):
        candidates = (
            payload.get("companies")
            or payload.get("results")
            or payload.get("items")
        )
    elif isinstance(payload, list):
        candidates = payload

    if not isinstance(candidates, list):
        return []

    refined: List[dict] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        data = {}
        for key, value in item.items():
            if value is None:
                continue
            if isinstance(value, (int, float)):
                data[key] = value
            else:
                data[key] = str(value).strip()
        name = data.get("name") or data.get("company") or ""
        if not name:
            continue
        data["name"] = name
        refined.append(data)
    return refined


def _fallback_parse_lines(
    text: str,
    batch_index: int,
    default_strategy: str,
) -> List[SearchTask]:
    """Fallback parser that treats each non-empty line as a search query."""
    tasks: List[SearchTask] = []
    for idx, line in enumerate(text.splitlines()):
        query = line.strip()
        if not query:
            continue
        task_id = f"{batch_index}_{idx}"
        tasks.append(
            SearchTask(
                id=task_id,
                query=query,
                strategy=default_strategy,
            )
        )
    return tasks
