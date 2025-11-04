# architecture.md

## 1. Purpose
Build a terminal-based Python system that:
1. collects a user request,
2. generates a large set of candidate searches,
3. lets the user approve, drop, or add searches,
4. executes real web searches via OpenAI,
5. normalizes and deduplicates results with source attribution,
6. retries if minimum items not met, using performance-aware regeneration,
7. exports a CSV.

All prompts in one folder. All parameters in one config. All calls through OpenAI Responses API.

## 2. Constraints
- Python only
- Terminal I/O
- Modular, class-based
- Prompts in `.txt` under a single directory
- Model per step, selected from config
- OpenAI API key from environment
- Web search via OpenAI web-capable model
- CSV export in `data/` with timestamped filename

## 3. Structure
- `main.py` (entry)
- `config.py` (models, limits, paths, flags)
- `core/` (prompt repo, llm client, model registry, parsing)
- `pipeline/` (orchestration, steps)
- `search/` (search executor, strategy map, performance report)
- `storage/` (in-memory DB with dedupe, CSV export)
- `prompts/` (all `.txt`)
- `data/` (raw LLM outputs, CSV exports)

## 4. Config
Config must define:
- models: `search_gen`, `search_filter`, `schema_gen`, `web`
- limits: `initial_batches`, `per_batch`, `filtered_count`, `max_retry_rounds`
- output: `default_columns`
- paths: `prompts_dir`, `export_dir`
- flags: `use_mock_search` (bool)
- search strategy map: strategy → web-search parameters

## 5. Core components

### 5.1 Prompt repository
- Input: prompt name
- Output: prompt text from `prompts/`
- Required names: `generate_searches`, `filter_searches`, `build_schema`, `retry_searches`

### 5.2 LLM client
- Reads API key from environment
- Calls OpenAI Responses API
- Accepts: model, input text, optional `response_format`, optional tools
- Returns parsed JSON
- Persists raw responses to `data/` with timestamp and step name

### 5.3 Model registry
- Maps pipeline step → model from config:
  - generate searches
  - filter searches
  - build schema
  - web search

### 5.4 Parsing utilities
- Parse search-batch output
- Parse filter-ID output
- Parse schema output
- On failure: one automatic retry
- If still failing: fallback parse (lines → simple searches)

## 6. Data objects

### 6.1 UserRequest
- `description: str`
- `min_items: int`
- `columns: list[str] | None`

### 6.2 SearchTask
- `id: str` (required)
- `query: str`
- `strategy: str`
- `rationale: str | None`

### 6.3 SearchPlan
- `tasks: list[SearchTask]`

### 6.4 NormalizedRow
- Contains every field from active schema (missing → empty string)
- Contains `source_query_id`
- Contains `source_strategy`

## 7. Pipeline

### 7.1 Collect user input
- Ask for description
- Ask for minimum items (int)
- Ask for columns (optional)
- Build `UserRequest`

### 7.2 Generate candidate searches
- Run `initial_batches` times
- Each batch calls `generate_searches` prompt
- Each batch requests exactly `per_batch` searches
- Output is JSON with array of objects including `id`, `query`, `strategy`
- If LLM omits `id`, generate deterministic id `batchIndex_itemIndex`
- Concatenate all batches (target ≈ 50)

### 7.3 Generate schema (conditional)
- If user provided columns: use them
- Else: call `build_schema` prompt
- Output is an ordered list of field names
- This becomes the active schema

### 7.4 Filter to final plan
- Call `filter_searches` with the full candidate list (with IDs)
- Ask for exactly `filtered_count` IDs
- If returned ID list is empty, fall back to the original candidate list
- Build `SearchPlan` of only kept IDs

### 7.5 User approval
- Display each kept search: `[id] [strategy] query`
- User options:
  - approve
  - drop: provide one or more IDs to remove
  - add: provide a new query; system creates new `SearchTask` with strategy `web` and unique id and appends it
  - feedback: provide text to be used in next regeneration
- Output: finalized `SearchPlan` and optional `feedback`

### 7.6 Execute searches
- For each task in the finalized plan:
  - If `use_mock_search` is true: return fixed mock results
  - Else: call web model with:
    - query text
    - web-search tool
    - JSON schema for results
    - parameters from strategy map (e.g. news → fewer results, recent; agg → site: style)
  - For each returned item:
    - build a `NormalizedRow`:
      - for each field in schema: if missing → empty string
      - add `source_query_id = task.id`
      - add `source_strategy = task.strategy`
    - insert into DB with dedupe

### 7.7 Check completeness
- If DB count ≥ `UserRequest.min_items`: finish
- Else: go to retry

### 7.8 Retry
- Build a performance report with fixed shape:
  - `total_items: int`
  - `searches: [ { id, query, strategy, items_found, note } ]`
  - `user_feedback: str or empty`
- Call `retry_searches` with:
  - original user description
  - performance report text
  - user feedback
  - request a smaller number of new searches
- Run filter again (same rules; empty → fallback)
- Run user approval again (same options)
- Run execute again
- Stop when:
  - DB count ≥ min
  - OR retries reached `max_retry_rounds`

### 7.9 Export
- Determine columns: user columns if given, else config default, else schema
- Filename: `output_YYYYMMDD_HHMMSS.csv` under `export_dir`
- Write all DB rows in that order
- Print final status:
  - requested items
  - actual items
  - retries used
  - failed/zero-result queries

## 8. Storage

### 8.1 In-memory DB
- Stores list of `NormalizedRow`
- Maintains `seen` set for dedupe
  - primary key: `url` if present
  - fallback key: `(title_lower, source_lower)`
- On insert:
  - if key in `seen`: skip
  - else: add to `seen` and append

### 8.2 Export
- Writes CSV with chosen columns
- No extra metadata columns in file except those in schema/user columns

## 9. Search strategy mapping
- `web`: generic search, max_results=8
- `news`: recency bias, max_results=5
- `agg`: site/directory-style query
- Unknown strategy: default to `web`

## 10. Error and rate handling
- On LLM/parsing error: retry once
- On web search error (network, 429): log, return empty list, mark task as zero-result
- Pipeline continues even if some tasks fail
- Zero-result tasks are included in performance report

## 11. Logging
- Every LLM call writes raw JSON to `data/` with timestamp and step name
- Used for debugging and replay

## 12. Termination conditions
- Success: DB count ≥ user minimum → export CSV → print status
- Partial success: retries exhausted, DB count < minimum → export what exists → print status and performance report
