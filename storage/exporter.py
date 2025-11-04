"""CSV export utilities."""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from core.models import NormalizedRow


class CSVExporter:
    """Writes normalized rows to a CSV file."""

    def __init__(self, export_dir: Path) -> None:
        self._export_dir = export_dir
        self._export_dir.mkdir(parents=True, exist_ok=True)

    def export_rows(
        self,
        rows: Iterable[NormalizedRow],
        columns: Sequence[str],
        *,
        filename_prefix: str = "output",
    ) -> Path:
        """Exports rows to a timestamped CSV."""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        file_path = self._export_dir / f"{filename_prefix}_{timestamp}.csv"
        with file_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns))
            writer.writeheader()
            for row in rows:
                row_dict = row.as_dict(columns)
                writer.writerow({column: row_dict.get(column, "") for column in columns})
        return file_path

    def export_dicts(
        self,
        records: Iterable[Mapping[str, object]],
        columns: Sequence[str],
        *,
        filename_prefix: str = "output",
    ) -> Path:
        """Exports generic mapping records to CSV."""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        file_path = self._export_dir / f"{filename_prefix}_{timestamp}.csv"
        with file_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns))
            writer.writeheader()
            for record in records:
                row = {column: _stringify(record.get(column, "")) for column in columns}
                writer.writerow(row)
        return file_path


def _stringify(value: object) -> str:
    """Converts a mapping value into a safe string for CSV export."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)
