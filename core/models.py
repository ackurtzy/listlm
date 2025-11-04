"""Shared data models for the desai-search workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional


@dataclass
class UserRequest:
    """Captures the user's initial configuration."""

    description: str
    min_items: int
    columns: Optional[List[str]] = None
    dedupe_field: Optional[str] = None


@dataclass
class SearchTask:
    """Represents a single search to execute."""

    id: str
    query: str
    strategy: str
    rationale: Optional[str] = None


@dataclass
class SearchPlan:
    """Collection of search tasks."""

    tasks: List[SearchTask] = field(default_factory=list)

    def add_task(self, task: SearchTask) -> None:
        """Appends a task to the plan, replacing any existing ID."""
        existing_index = next(
            (index for index, current in enumerate(self.tasks) if current.id == task.id),
            -1,
        )
        if existing_index == -1:
            self.tasks.append(task)
        else:
            self.tasks[existing_index] = task

    def ids(self) -> List[str]:
        """Returns the list of task identifiers."""
        return [task.id for task in self.tasks]

    def filter_by_ids(self, keep_ids: Iterable[str]) -> "SearchPlan":
        """Creates a new plan containing only tasks whose IDs are in keep_ids."""
        keep = set(keep_ids)
        return SearchPlan(tasks=[task for task in self.tasks if task.id in keep])

    def remove_ids(self, drop_ids: Iterable[str]) -> None:
        """Removes tasks from the plan whose IDs are listed."""
        drop = set(drop_ids)
        self.tasks = [task for task in self.tasks if task.id not in drop]


@dataclass
class NormalizedRow:
    """Represents a normalized row ready for export."""

    values: Dict[str, str]
    source_query_id: str
    source_strategy: str

    def as_dict(self, schema: Iterable[str]) -> Dict[str, str]:
        """Returns the row as a dict aligned with the provided schema."""
        row = {column: self.values.get(column, "") for column in schema}
        row["source_query_id"] = self.source_query_id
        row["source_strategy"] = self.source_strategy
        return row
