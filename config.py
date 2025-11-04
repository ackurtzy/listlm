"""Application configuration for the desai-search workflow."""

# ---------------------------------------------------------------------------
# Limit tuning guide
# ---------------------------------------------------------------------------
# The pipeline limits can be customised in two ways:
# 1. Edit DEFAULT_LIMITS below to change the repository-wide defaults.
# 2. Override specific values at runtime with environment variables
#    (INITIAL_BATCHES, SEARCHES_PER_BATCH, FILTERED_COUNT, MAX_RETRY_ROUNDS,
#     SEARCH_GENERATE_WORKERS, SEARCH_EXECUTE_WORKERS).
# Update DEFAULT_LIMITS for persistent changes; use environment variables for
# one-off experiments.
# ---------------------------------------------------------------------------

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping


DEFAULT_LIMITS: Dict[str, int] = {
    "initial_batches": 2,  # Generation batches requested on the initial pass.
    "per_batch": 25,  # Candidate searches expected per generation batch.
    "max_retry_rounds": 3,  # Max regenerate/execute cycles when below quota.
    "worker_pool_size": 6,  # Thread pool size shared across parallel tasks.
}


def _env_bool(var_name: str, default: bool) -> bool:
    """Returns a boolean for the provided environment variable name."""
    raw_value = os.getenv(var_name)
    if raw_value is None:
        return default
    return raw_value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LimitsConfig:
    """Holds numeric limits used throughout the pipeline."""

    initial_batches: int = DEFAULT_LIMITS[
        "initial_batches"
    ]  # How many generation batches run up front.
    per_batch: int = DEFAULT_LIMITS[
        "per_batch"
    ]  # Searches requested from the generator per batch.
    max_retry_rounds: int = DEFAULT_LIMITS[
        "max_retry_rounds"
    ]  # Retry attempts allowed when below minimum.
    worker_pool_size: int = DEFAULT_LIMITS[
        "worker_pool_size"
    ]  # Shared thread pool size for concurrent calls.


@dataclass(frozen=True)
class PathsConfig:
    """Collects filesystem locations used by the application."""

    prompts_dir: Path  # Folder storing all prompt templates.
    export_dir: Path  # Root data directory for exports and logs.
    debug_export_dir: Path  # CSV location capturing raw, unfiltered rows.
    reports_dir: Path  # CSV location for user-facing refined reports.
    raw_responses_dir: Path  # Where raw OpenAI API responses are persisted.


@dataclass(frozen=True)
class FlagsConfig:
    """Boolean feature toggles."""

    use_mock_search: bool = False  # Skip live web calls and return mock rows when True.


@dataclass(frozen=True)
class Config:
    """Top-level configuration object."""

    models: Mapping[str, str]
    limits: LimitsConfig
    output_default_columns: tuple[str, ...]
    paths: PathsConfig
    flags: FlagsConfig
    strategy_map: Mapping[str, Dict[str, str]]


def get_config() -> Config:
    """Instantiates the Config object, honoring environment overrides."""
    prompts_dir = Path(os.getenv("PROMPTS_DIR", "prompts"))
    export_dir = Path(os.getenv("EXPORT_DIR", "data"))
    debug_export_dir = Path(os.getenv("DEBUG_EXPORT_DIR", str(export_dir / "debug")))
    reports_dir = Path(os.getenv("REPORTS_DIR", "reports"))
    raw_responses_dir = Path(os.getenv("RAW_RESPONSE_DIR", str(export_dir / "llm")))

    for directory in (
        prompts_dir,
        export_dir,
        debug_export_dir,
        reports_dir,
        raw_responses_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    models = {
        "search_gen": os.getenv(
            "MODEL_SEARCH_GEN", "gpt-5-mini-2025-08-07"
        ),  # Generates bulk search ideas.
        "search_filter": os.getenv(
            "MODEL_SEARCH_FILTER", "gpt-5-mini-2025-08-07"
        ),  # Chooses the strongest searches.
        "schema_gen": os.getenv(
            "MODEL_SCHEMA_GEN", "gpt-5-mini-2025-08-07"
        ),  # Designs the CSV schema when needed.
        "web": os.getenv(
            "MODEL_WEB", "gpt-5-mini-2025-08-07"
        ),  # Runs web-enabled searches.
        "postprocess": os.getenv(
            "MODEL_POSTPROCESS", "gpt-5-mini-2025-08-07"
        ),  # Cleans and dedupes final rows.
    }

    limits = LimitsConfig(
        initial_batches=int(
            os.getenv(
                "INITIAL_BATCHES",
                str(DEFAULT_LIMITS["initial_batches"]),
            )
        ),
        per_batch=int(
            os.getenv(
                "SEARCHES_PER_BATCH",
                str(DEFAULT_LIMITS["per_batch"]),
            )
        ),
        max_retry_rounds=int(
            os.getenv(
                "MAX_RETRY_ROUNDS",
                str(DEFAULT_LIMITS["max_retry_rounds"]),
            )
        ),
        worker_pool_size=int(
            os.getenv(
                "WORKER_POOL_SIZE",
                os.getenv(
                    "SEARCH_EXECUTE_WORKERS",
                    os.getenv(
                        "SEARCH_GENERATE_WORKERS",
                        str(DEFAULT_LIMITS["worker_pool_size"]),
                    ),
                ),
            )
        ),
    )

    default_columns = tuple(
        col.strip()
        for col in os.getenv(
            "DEFAULT_COLUMNS",
            "title,url,snippet,source",
        ).split(",")
        if col.strip()
    )  # Used when the user does not supply column names.

    flags = FlagsConfig(use_mock_search=_env_bool("USE_MOCK_SEARCH", False))

    strategy_map: Dict[str, Dict[str, str]] = {
        "web": {"max_results": "15"},  # Default general-purpose web search.
        "news": {
            "max_results": "15",
            "recency": "12mo",
        },  # News-biased queries focus on recent items.
        "agg": {
            "max_results": "15",
            "site_bias": "directory",
        },  # Aggregator/directory style lookups.
    }

    return Config(
        models=models,
        limits=limits,
        output_default_columns=default_columns,
        paths=PathsConfig(
            prompts_dir=prompts_dir,
            export_dir=export_dir,
            debug_export_dir=debug_export_dir,
            reports_dir=reports_dir,
            raw_responses_dir=raw_responses_dir,
        ),
        flags=flags,
        strategy_map=strategy_map,
    )
