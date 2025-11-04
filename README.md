# Desai Search

Desai Search is a terminal-first Python workflow for harvesting structured data
about companies (or other entities) from the open web using the OpenAI API for
all LLM-assisted steps. The system plans a diverse set of searches, lets the
user approve and adjust the plan, executes web queries, normalizes results, and
iteratively retries until a user-defined minimum number of polished records is
met. The final dataset is exported to CSV files for downstream analysis.

## Features

- **End-to-end workflow**: prompt-driven planning, user approval, execution,
  reflection, retrial, and export.
- **Configurable LLM usage**: models and limits are supplied through
  `config.py`/environment variables so you can swap endpoints quickly.
- **Parallelized execution**: generation, filtering, search execution, and
  post-processing all run in parallel with a tunable worker pool to minimize
  latency.
- **Chunked refinement**: noisy search hits are refined in 20-item batches,
  deduplicated using user-specified keys, and normalized to the target schema.
- **Interactive CLI**: request description, minimum items, column schema, and
  dedupe column are collected up front; plan review supports feedback, manual
  additions, and instant re-filtering.
- **Deterministic IDs**: search tasks are assigned IDs within the pipeline
  (`g0001`, `g0002`, …) so duplicates from the LLM never collide downstream.

## Repository Layout

```
├── README.md
├── config.py               # Central configuration (models, limits, paths)
├── main.py                 # CLI entry point
├── core/                   # Shared data models, parser helpers, model registry
├── pipeline/               # Orchestrator and user I/O logic
├── postprocess/            # Result refiner and supporting utilities
├── search/                 # Web-search executor and performance reporting helpers
├── storage/                # In-memory row store and CSV exporter
├── prompts/                # Prompt templates used for planning/filtering/refinement
├── docs/                   # Additional documentation (`goal.md`, `updated_architecture.md`, `CONFIG_GUIDE.md`)
├── data/                   # Debug outputs, raw LLM responses, CSV exports
└── reports/                # Final refined CSV reports (metadata removed)
```

## Prerequisites

- Python 3.10+
- An OpenAI API key with access to the specified models
- Network access (the web-search model requires external requests)

Install dependencies (requirements file omitted—add your preferred stack as
needed):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Export your OpenAI credentials:

```bash
export OPENAI_API_KEY="sk-..."
```

## Configuration

`config.py` exposes the main knobs. You can override any value via environment
variables; see `docs/CONFIG_GUIDE.md` for a comprehensive table. Key settings:

- **Models**: `MODEL_SEARCH_GEN`, `MODEL_SEARCH_FILTER`, `MODEL_SCHEMA_GEN`,
  `MODEL_WEB`, `MODEL_POSTPROCESS`
- **Limits**: `INITIAL_BATCHES`, `SEARCHES_PER_BATCH`, `MAX_RETRY_ROUNDS`,
  `WORKER_POOL_SIZE`
- **Paths**: `PROMPTS_DIR`, `EXPORT_DIR`, `DEBUG_EXPORT_DIR`, `REPORTS_DIR`,
  `RAW_RESPONSE_DIR`
- **Flags**: `USE_MOCK_SEARCH` (switch to mock data)

## Running the Workflow

```bash
python main.py
```

You will be prompted for:

1. **Description** – text explaining what you’re searching for
2. **Minimum items** – target number of polished rows (capped at 100)
3. **CSV columns** – optional comma-separated list; leave blank to auto-generate
4. **Dedupe column** – choose how duplicates are resolved (`name`, `website`,
   `link`, `url`, `email`, or `description`)

After generation the CLI displays the filtered search plan and allows you to:

- Approve (`A`)
- Drop searches by ID (`D`)
- Add new searches (`N`)
- Provide feedback for new planning cycles (`F`)
- Re-filter the current candidate list with feedback (`G`)
- Refresh/Help (`R`, `H`)

The workflow iterates until the refined output reaches the requested minimum or
`MAX_RETRY_ROUNDS` is exhausted. Debug CSVs (with metadata) are written to
`data/debug/`, while final user-facing reports are stored under `reports/`.

## Prompts

Prompt files live under `prompts/` and are easy to edit for custom behavior:

- `generate_searches.txt` – initial/bulk search generation
- `filter_primary.txt` – strict ID selection to the target count
- `filter_trim.txt` – trimming pass when the primary filter over-selects
- `retry_searches.txt` – regeneration based on performance reports
- `refine_results.txt` – post-processing of raw rows into companies
- `build_schema.txt` – optional schema design when the user skips columns

## Extending

- **Alternate search backends** – implement a new executor under `search/` and
  update `SearchExecutor` to call your service instead of OpenAI’s web model.
- **Custom post-processing** – swap out `ResultRefiner` or add heuristics before
  chunking to enforce additional constraints.
- **Automation/Batching** – wrap `main.py` in scripts that preload inputs or
  cycle through multiple descriptions.

## Documentation

Detailed design notes and configuration tips are in `docs/`:

- `docs/goal.md` – initial system goals
- `docs/updated_architecture.md` – evolved architecture diagram and rationale
- `docs/CONFIG_GUIDE.md` – configuration reference with environment overrides

- **API errors** – confirm `OPENAI_API_KEY` is set and the selected models are
  enabled in your account.

Feel free to tailor prompts, configuration, and storage paths to your specific
use case. Happy searching!
