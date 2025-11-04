"""Performance reporting helpers for retry logic."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional

from core.models import SearchTask


@dataclass
class SearchSummary:
    """Captures the outcome of a single search execution."""

    task: SearchTask
    items_found: int
    note: Optional[str] = None


def build_performance_report(
    total_items: int,
    summaries: List[SearchSummary],
    user_feedback: Optional[str],
) -> str:
    """Builds a JSON string summarizing performance."""
    report = {
        "total_items": total_items,
        "searches": [
            {
                "id": summary.task.id,
                "query": summary.task.query,
                "strategy": summary.task.strategy,
                "items_found": summary.items_found,
                "note": summary.note or "",
            }
            for summary in summaries
        ],
        "user_feedback": user_feedback or "",
    }
    return json.dumps(report, indent=2)

