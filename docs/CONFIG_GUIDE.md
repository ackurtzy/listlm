# Configuration Guide

This document explains every setting defined in `config.py`, how defaults are
constructed, and how to adapt the values for your environment or workload.

The configuration is built once at startup by calling `get_config()`. It gathers
data from environment variables, establishes filesystem locations, and returns a
frozen `Config` dataclass with five sections:

1. **Models** – mapping from semantic pipeline step to OpenAI model name.
2. **Limits** – numeric or concurrency controls.
3. **Paths** – directories for prompts, raw outputs, and CSVs.
4. **Flags** – runtime feature toggles.
5. **Strategy Map** – hints passed to the web-search model per strategy.

All directories mentioned below are created automatically (`mkdir(parents=True,
exist_ok=True)`) when the config is instantiated.

---

## 1. Models

Defined in `Config.models` as a `Mapping[str, str]`. Each entry corresponds to a
specific stage of the pipeline. Override any model by setting the environment
variable before launching the app.

| Key             | Purpose                                                           | Env var                | Default       |
|-----------------|-------------------------------------------------------------------|------------------------|---------------|
| `search_gen`    | Generates diverse candidate search tasks.                         | `MODEL_SEARCH_GEN`     | `gpt-4.1-mini`|
| `search_filter` | Filters the candidate list down to a focused plan.                | `MODEL_SEARCH_FILTER`  | `gpt-4.1-mini`|
| `schema_gen`    | Chooses CSV columns when the user provides none.                  | `MODEL_SCHEMA_GEN`     | `gpt-4.1-mini`|
| `web`           | Executes approved searches using the web-search toolchain.        | `MODEL_WEB`            | `gpt-4.1-mini`|
| `postprocess`   | Refines and deduplicates raw results into clean company records.  | `MODEL_POSTPROCESS`    | `gpt-4.1-mini`|

**Example override**
```bash
export MODEL_WEB=gpt-4.1
export MODEL_POSTPROCESS=gpt-4o-mini
```

---

## 2. Limits

Collected in the `LimitsConfig` dataclass.

| Field               | Description                                                                                                                                               | Env var              | Default |
|---------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------|---------|
| `initial_batches`   | Number of prompt batches requested during search generation. Each batch is run in parallel (subject to `worker_pool_size`) and produces `per_batch` tasks. | `INITIAL_BATCHES`    | `5`     |
| `per_batch`         | Target number of search tasks returned by the generation model for each batch.                                                                            | `SEARCHES_PER_BATCH` | `10`    |
| `filtered_count`    | Advisory value supplied to the filter prompt (“keep about X searches”). The filter may choose more or fewer IDs, but never reverts to the full list.      | `FILTERED_COUNT`     | `15`    |
| `filter_group_size` | Rounds the filter target up to the nearest multiple of this value (default 15) to preserve broader coverage.                                             | `FILTER_GROUP_SIZE`  | `15`    |
| `max_retry_rounds`  | Maximum number of additional generate→filter→execute cycles when the collected item count stays below the user’s minimum.                                 | `MAX_RETRY_ROUNDS`   | `3`     |
| `worker_pool_size`  | Thread-pool size used for all parallel tasks (generation and search execution).                                                                           | `WORKER_POOL_SIZE`   | `6`     |

**Example overrides**
```bash
export INITIAL_BATCHES=4
export SEARCHES_PER_BATCH=12
export FILTERED_COUNT=18
export FILTER_GROUP_SIZE=12
export WORKER_POOL_SIZE=8
```

---

## 3. Paths

Stored in `PathsConfig`. Every path is eagerly created so downstream modules can
assume it exists.

| Field               | Description                                                                            | Env var            | Default                                |
|---------------------|----------------------------------------------------------------------------------------|--------------------|----------------------------------------|
| `prompts_dir`       | Directory containing all prompt `.txt` files.                                          | `PROMPTS_DIR`      | `prompts/`                             |
| `export_dir`        | Root data directory (the parent for debug, raw, and report outputs).                   | `EXPORT_DIR`       | `data/`                                |
| `debug_export_dir`  | Destination for debug CSVs (full raw rows including metadata).                         | `DEBUG_EXPORT_DIR` | `data/debug/`                          |
| `reports_dir`       | Destination for refined CSVs supplied to the end user (metadata removed).              | `REPORTS_DIR`      | `reports/`                             |
| `raw_responses_dir` | Folder where each raw OpenAI API response is stored as JSON for auditing or debugging. | `RAW_RESPONSE_DIR` | `data/llm/` (inside `export_dir`)      |

**Example: custom location**
```bash
export EXPORT_DIR=/var/tmp/desai-data
export REPORTS_DIR=/var/tmp/desai-data/reports
```

---

## 4. Flags

Currently a single boolean toggle in `FlagsConfig`.

| Field             | Description                                                                                 | Env var          | Default |
|-------------------|---------------------------------------------------------------------------------------------|------------------|---------|
| `use_mock_search` | When `True`, the search executor returns deterministic fake rows (handy for offline tests). | `USE_MOCK_SEARCH`| `False` |

Accepted truthy values (case-insensitive): `1`, `true`, `yes`, `on`.

---

## 5. Strategy Map

`strategy_map` is a `Dict[str, Dict[str, str]]` that informs the web-search
model how to bias each strategy. You can edit the values directly in `config.py`
to match the request format expected by your chosen model.

Default mapping:
```python
{
    "web": {"max_results": "8"},
    "news": {"max_results": "5", "recency": "12mo"},
    "agg": {"max_results": "8", "site_bias": "directory"},
}
```

---

## 6. Default Columns

`output_default_columns` is a tuple used whenever the user omits custom columns.
It is derived from the `DEFAULT_COLUMNS` environment variable (comma-separated).

- Default string: `"title,url,snippet,source"`
- Stored value: `("title", "url", "snippet", "source")`

**Override example**
```bash
export DEFAULT_COLUMNS="name,url,description,source"
```

---

## 7. Directory Creation & Permissions

Before returning the configuration, `get_config()` calls `mkdir(parents=True,
exist_ok=True)` for every path. Ensure the executing user has write permissions
for those directories; otherwise, initialization will raise `PermissionError`.

---

## 8. Putting It All Together

At runtime:

1. `main.py` calls `get_config()` to assemble the dataclasses.
2. The `Config` object is passed into the orchestrator, search executor, and
   other components.
3. Each module reads the relevant section (`config.models`, `config.limits`,
   etc.) without mutating it.

To adjust behavior:

- **Per-run tweaks**: set environment variables before launching
  (`export SEARCH_EXECUTE_WORKERS=10`).
- **Persistent defaults**: edit `config.py` (for example, expand the
  `strategy_map` or change numeric defaults).
- **Structural changes**: if you add new prompts or pipeline stages, extend the
  relevant dataclasses and environment variables following the existing pattern.

This guide should equip you to reason about every knob in `config.py` and tailor
the system to your workload, infrastructure limits, or preferred models.
