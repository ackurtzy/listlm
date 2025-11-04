"""Simple in-memory database with deduplication and logging."""

from __future__ import annotations

import logging
from typing import Iterable, List, Tuple

from core.models import NormalizedRow


class InMemoryDatabase:
    """Stores normalized rows while enforcing deduplication."""

    def __init__(self) -> None:
        self._rows: List[NormalizedRow] = []
        self._seen: set[Tuple[str, ...]] = set()
        self._logger = logging.getLogger(self.__class__.__name__)

    def insert(self, row: NormalizedRow) -> None:
        """Inserts a row unless it has already been observed."""
        key = self._build_key(row)
        if key in self._seen:
            self._logger.debug("Skipping duplicate row with key %s.", key)
            return
        self._seen.add(key)
        self._rows.append(row)
        self._logger.debug("Inserted row with key %s.", key)

    def extend(self, rows: Iterable[NormalizedRow]) -> None:
        """Inserts multiple rows."""
        for row in rows:
            self.insert(row)

    def rows(self) -> List[NormalizedRow]:
        """Returns stored rows."""
        return list(self._rows)

    def count(self) -> int:
        """Returns the number of stored rows."""
        return len(self._rows)

    def _build_key(self, row: NormalizedRow) -> Tuple[str, ...]:
        """Builds a deduplication key following the architecture."""
        url = (
            row.values.get("url")
            or row.values.get("link")
            or ""
        ).strip().lower()
        if url:
            return ("url", url)

        title = (
            row.values.get("title")
            or row.values.get("name")
            or ""
        ).strip().lower()
        source = (
            row.values.get("source")
            or row.values.get("source_domain")
            or ""
        ).strip().lower()
        description = (row.values.get("description") or "").strip().lower()

        if title and source:
            return ("title_source", title, source)
        if title and description:
            return ("title_description", title, description[:100])

        return (
            "fallback",
            title or row.source_query_id,
            row.source_strategy,
            description[:100],
        )

