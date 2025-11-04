"""LLM-backed refinement of collected search results."""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from config import Config
from core.llm_client import LLMClient
from core.model_registry import ModelRegistry
from core.models import NormalizedRow, UserRequest
from core.parser import parse_refined_companies
from core.prompt_repository import PromptRepository


class ResultRefiner:
    """Uses an LLM to deduplicate and clean result rows."""

    def __init__(
        self,
        config: Config,
        prompt_repository: PromptRepository,
        llm_client: LLMClient,
        model_registry: ModelRegistry,
        *,
        max_records: int = 120,
    ) -> None:
        self._config = config
        self._prompts = prompt_repository
        self._llm_client = llm_client
        self._models = model_registry
        self._max_records = max_records
        self._logger = logging.getLogger(self.__class__.__name__)
        self._chunk_size = 20

    def refine(
        self,
        rows: Iterable[NormalizedRow],
        request: UserRequest,
        schema: Sequence[str],
    ) -> List[Dict[str, Any]]:
        """Returns a deduplicated list of company dictionaries."""
        row_list = list(rows)
        if not row_list:
            return []

        schema_fields = [
            column for column in schema if column not in {"source_query_id", "source_strategy"}
        ]
        records = self._rows_to_records(row_list, schema)
        source_index = self._build_source_index(row_list, schema_fields)

        prompt_template = self._prompts.load("refine_results")
        max_workers = max(1, self._config.limits.worker_pool_size)
        chunks = list(self._chunk_records(records, self._chunk_size))
        self._logger.info(
            "Refiner processing %d records across %d chunks.",
            len(records),
            len(chunks),
        )

        chunk_results: Dict[int, List[Dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self._refine_chunk,
                    chunk_index,
                    chunk,
                    request,
                    schema_fields,
                    prompt_template,
                ): chunk_index
                for chunk_index, chunk in enumerate(chunks)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    chunk_output = future.result()
                except Exception as error:  # pragma: no cover - defensive
                    self._logger.error(
                        "Refinement chunk %d failed: %s", idx, error
                    )
                    chunk_output = []
                if not chunk_output:
                    self._logger.warning(
                        "Chunk %d returned no companies; applying heuristic fallback.",
                        idx,
                    )
                    chunk_output = self._dedupe_records(chunks[idx])
                chunk_results[idx] = chunk_output

        combined: List[Dict[str, Any]] = []
        for idx in range(len(chunks)):
            combined.extend(chunk_results.get(idx, []))

        dedupe_field = request.dedupe_field or "name"
        if not combined:
            self._logger.warning(
                "No refined companies returned across all chunks, using fallback."
            )
            combined = self._dedupe_records(records, dedupe_field)
        else:
            combined = self._dedupe_records(combined, dedupe_field)

        normalized = self._normalize_records(combined, schema_fields, source_index)
        self._logger.info("Refined %d companies.", len(normalized))
        return normalized

    def fallback_from_rows(
        self,
        rows: Iterable[NormalizedRow],
        schema: Sequence[str],
    ) -> List[Dict[str, Any]]:
        """Public helper that produces a heuristic fallback from raw rows."""
        schema_fields = [
            column for column in schema if column not in {"source_query_id", "source_strategy"}
        ]
        row_list = list(rows)
        records = self._rows_to_records(row_list, schema)
        source_index = self._build_source_index(row_list, schema_fields)
        return self._normalize_records(
            self._dedupe_records(records, dedupe_field="name"),
            schema_fields,
            source_index,
        )

    def _rows_to_records(
        self,
        rows: Iterable[NormalizedRow],
        schema: Sequence[str],
    ) -> List[Dict[str, Any]]:
        """Converts normalized rows into rough records."""
        return [
            _row_to_record(
                row,
                schema,
            )
            for row in rows
        ]

    def _chunk_records(
        self, records: List[Dict[str, Any]], chunk_size: int
    ) -> Iterable[List[Dict[str, Any]]]:
        for offset in range(0, len(records), chunk_size):
            yield records[offset : offset + chunk_size]

    def _refine_chunk(
        self,
        chunk_index: int,
        records: List[Dict[str, Any]],
        request: UserRequest,
        schema_fields: Sequence[str],
        prompt_template: str,
    ) -> List[Dict[str, Any]]:
        candidate_json = json.dumps(records, indent=2)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a meticulous data curator. You produce clean,"
                    " deduplicated company lists in JSON and never repeat entries."
                ),
            },
            {
                "role": "user",
                "content": prompt_template.format(
                    user_description=request.description,
                    requested_columns=", ".join(request.columns or schema_fields),
                    schema_fields=", ".join(schema_fields),
                    candidate_json=candidate_json,
                ),
            },
        ]
        result = self._llm_client.complete(
            model=self._models.for_postprocess(),
            messages=messages,
            response_format={"type": "json_object"},
            step_name=f"refine_{chunk_index}",
            metadata={"record_count": len(records)},
        )
        refined = parse_refined_companies(result.text)
        return self._dedupe_records(refined, dedupe_field=request.dedupe_field or "name")

    def _dedupe_records(
        self,
        records: Iterable[Dict[str, Any]],
        dedupe_field: str,
    ) -> List[Dict[str, Any]]:
        seen: Dict[str, Dict[str, Any]] = {}
        deduped: List[Dict[str, Any]] = []
        for record in records:
            key = self._dedupe_key(record, dedupe_field)
            if key in seen:
                continue
            seen[key] = record
            deduped.append(record)
        return deduped

    def _dedupe_key(self, record: Dict[str, Any], dedupe_field: str) -> str:
        if dedupe_field in {"website", "link", "url"}:
            email = str(record.get("email") or "").strip().lower()
            if email:
                return email
            value = str(record.get(dedupe_field) or record.get("website") or record.get("url") or record.get("link") or "")
            return self._extract_domain(value)
        if dedupe_field == "email":
            return str(record.get("email") or "").strip().lower()
        if dedupe_field == "description":
            return self._normalize_name(str(record.get("description") or ""))
        return self._normalize_name(str(record.get("name") or record.get("title") or ""))

    @staticmethod
    def _normalize_name(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower()) if text else ""

    @staticmethod
    def _extract_domain(url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        host = parsed.netloc or parsed.path
        return host.lower()

    @staticmethod
    def _build_source_index(
        rows: Iterable[NormalizedRow],
        schema_fields: Sequence[str],
    ) -> Dict[str, Dict[str, Any]]:
        """Builds a mapping from normalized name to original row values."""
        index: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            values = dict(row.values)
            name = (
                values.get("name")
                or values.get("title")
                or values.get("company")
                or ""
            )
            key = name.strip().lower()
            if not key or key in index:
                continue
            record = {field: values.get(field, "") for field in schema_fields}
            url = values.get("website") or values.get("url") or values.get("link") or ""
            if url:
                record.setdefault("website", url)
                record.setdefault("link", url)
            source_domain = values.get("source_domain")
            if source_domain:
                record.setdefault("source_domain", source_domain)
            email = values.get("email")
            if email:
                record.setdefault("email", email)
            index[key] = record
        return index

    @staticmethod
    def _normalize_records(
        records: Iterable[Dict[str, Any]],
        schema_fields: Sequence[str],
        source_index: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Normalizes refined records to match schema fields, filling gaps."""
        normalized: List[Dict[str, Any]] = []
        for record in records:
            name = (
                str(record.get("name") or record.get("title") or "").strip()
            )
            if not name:
                continue
            key = name.lower()
            source_data = source_index.get(key, {})
            merged: Dict[str, Any] = {"name": name}
            for field in schema_fields:
                if field == "name":
                    continue
                value = record.get(field)
                if value in (None, ""):
                    value = source_data.get(field, "")
                merged[field] = _to_text(value)

            website = (
                record.get("website")
                or record.get("url")
                or source_data.get("website")
                or source_data.get("url")
                or ""
            )
            if website:
                merged.setdefault("website", _to_text(website))

            link = record.get("link") or source_data.get("link") or website
            if link:
                merged.setdefault("link", _to_text(link))

            email = record.get("email") or source_data.get("email")
            if email:
                merged["email"] = _to_text(email)

            normalized.append(merged)
        return normalized


def _row_to_record(
    row: NormalizedRow,
    schema: Sequence[str],
) -> Dict[str, Any]:
    """Converts a NormalizedRow into a record for the refiner."""
    values = dict(row.values)
    record: Dict[str, Any] = {
        "source_query_id": row.source_query_id,
        "source_strategy": row.source_strategy,
    }
    for field in schema:
        if field in {"source_query_id", "source_strategy"}:
            continue
        record[field] = values.get(field, "")

    if not record.get("name"):
        record["name"] = (
            values.get("title") or values.get("company") or row.source_query_id
        )

    url = values.get("website") or values.get("url") or values.get("link") or ""
    if url:
        record.setdefault("website", url)
        record.setdefault("link", url)
    source_domain = values.get("source_domain") or values.get("source")
    if source_domain:
        record.setdefault("source_domain", source_domain)
    email = values.get("email")
    if email:
        record.setdefault("email", email)
    return record


def _to_text(value: Any) -> str:
    """Converts any value to a trimmed string."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip()
