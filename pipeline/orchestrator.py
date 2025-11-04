"""Pipeline orchestrator implementing the system architecture."""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from config import Config
from core.llm_client import LLMClient
from core.model_registry import ModelRegistry
from core.models import NormalizedRow, SearchPlan, SearchTask, UserRequest
from core.parser import parse_filter_ids, parse_schema, parse_search_tasks
from core.prompt_repository import PromptRepository
from pipeline.user_io import TerminalIO
from postprocess.refiner import ResultRefiner
from search.executor import SearchExecutor
from search.performance import SearchSummary, build_performance_report
from storage.database import InMemoryDatabase
from storage.exporter import CSVExporter


@dataclass
class ExecutionResult:
    """Represents the outcome of executing a search plan."""

    rows: List[NormalizedRow]
    summaries: List[SearchSummary]


class PipelineOrchestrator:
    """Coordinates the overall workflow."""

    def __init__(
        self,
        config: Config,
        prompt_repository: PromptRepository,
        llm_client: LLMClient,
        model_registry: ModelRegistry,
        search_executor: SearchExecutor,
        db: InMemoryDatabase,
        debug_exporter: CSVExporter,
        report_exporter: CSVExporter,
        refiner: ResultRefiner,
        io: TerminalIO,
    ) -> None:
        self._config = config
        self._prompts = prompt_repository
        self._llm_client = llm_client
        self._models = model_registry
        self._search_executor = search_executor
        self._db = db
        self._debug_exporter = debug_exporter
        self._report_exporter = report_exporter
        self._refiner = refiner
        self._io = io
        self._feedback_history: List[str] = []
        self._filter_feedback_history: List[str] = []
        self._user_task_counter = 0
        self._task_counter = 0
        self._logger = logging.getLogger(self.__class__.__name__)

    def run(self) -> None:
        """Runs the orchestration loop end-to-end."""
        request = self._io.collect_user_request()
        self._io.display_status("Generating initial search candidates...")
        candidate_tasks = self._generate_initial_searches(request)
        schema = self._resolve_schema(request, candidate_tasks)

        retry_round = 0
        while True:
            plan = self._select_plan(candidate_tasks, request)
            while True:
                review = self._io.review_search_plan(plan)
                feedback = review.get("feedback")
                if feedback:
                    self._feedback_history.append(str(feedback))

                if review.get("regenerate"):
                    self._io.display_status(
                        "Regenerating search plan with new feedback..."
                    )
                    candidate_tasks = self._generate_initial_searches(request)
                    break

                candidate_tasks, new_tasks = self._apply_candidate_changes(
                    candidate_tasks, review
                )

                if review.get("refilter"):
                    filter_feedback = review.get("filter_feedback")
                    if filter_feedback:
                        self._filter_feedback_history.append(str(filter_feedback))
                    plan = self._select_plan(candidate_tasks, request)
                    continue

                plan = self._apply_review(plan, review, new_tasks)
                break

            if review.get("regenerate"):
                continue

            execution = self._execute_plan(plan, schema)
            prev_total = self._db.count()
            self._db.extend(execution.rows)
            total_items = self._db.count()
            self._io.display_status(
                f"Collected {total_items} items (target: {request.min_items})."
            )
            added = total_items - prev_total
            self._logger.info(
                "Round yielded %d new rows; total now %d.",
                added,
                total_items,
            )

            refined_preview = None
            needs_retry = total_items < request.min_items

            if not needs_retry:
                refined_preview = self._refiner.refine(self._db.rows(), request, schema)
                refined_count = len(refined_preview)
                self._logger.info(
                    "Refined output currently has %d items.",
                    refined_count,
                )
                if refined_count >= request.min_items:
                    self._finalize(
                        request,
                        schema,
                        execution.summaries,
                        retry_round,
                        refined_records=refined_preview,
                    )
                    return
                needs_retry = True
                self._io.display_status(
                    f"Refined output has only {refined_count} items; continuing search."
                )

            if retry_round >= self._config.limits.max_retry_rounds:
                self._io.display_status(
                    "Max retry rounds reached. Exporting partial data."
                )
                self._finalize(
                    request,
                    schema,
                    execution.summaries,
                    retry_round,
                    refined_records=refined_preview,
                )
                return

            retry_round += 1
            self._io.display_status(f"Retry round {retry_round}...")
            candidate_tasks = self._generate_retry_searches(
                request=request,
                schema=schema,
                execution=execution,
                total_items=total_items,
            )

    def _generate_initial_searches(self, request: UserRequest) -> List[SearchTask]:
        """Generates the initial set of candidate searches."""
        prompt_template = self._prompts.load("generate_searches")
        total_batches = self._config.limits.initial_batches
        max_workers = max(1, self._config.limits.worker_pool_size)
        futures = {}
        all_tasks: List[SearchTask] = []

        def submit_batch(executor: ThreadPoolExecutor, batch_index: int) -> None:
            future = executor.submit(
                self._generate_search_batch,
                request,
                prompt_template,
                batch_index,
                total_batches,
            )
            futures[future] = batch_index

        with ThreadPoolExecutor(
            max_workers=min(max_workers, max(1, total_batches))
        ) as executor:
            for batch_index in range(total_batches):
                submit_batch(executor, batch_index)

            for future in as_completed(futures):
                batch_index = futures[future]
                try:
                    tasks = future.result()
                except Exception as error:  # pragma: no cover - defensive
                    self._logger.error(
                        "Batch %d generation failed: %s. Retrying sequentially.",
                        batch_index + 1,
                        error,
                    )
                    tasks = self._generate_search_batch(
                        request,
                        prompt_template,
                        batch_index,
                        total_batches,
                )
                all_tasks.extend(tasks)
                self._logger.info(
                    "Generated %d tasks in batch %d.",
                    len(tasks),
                    batch_index + 1,
                )
        self._logger.info("Total candidate tasks: %d", len(all_tasks))
        return all_tasks

    def _generate_search_batch(
        self,
        request: UserRequest,
        prompt_template: str,
        batch_index: int,
        total_batches: int,
    ) -> List[SearchTask]:
        """Runs a single batch generation request."""
        prompt = prompt_template.format(
            description=request.description,
            batch_number=batch_index + 1,
            total_batches=total_batches,
            per_batch=self._config.limits.per_batch,
            feedback="\n".join(self._feedback_history) or "None",
        )
        messages = [
            {"role": "system", "content": "You generate diverse web searches."},
            {"role": "user", "content": prompt},
        ]
        response = self._llm_client.complete(
            model=self._models.for_generate_searches(),
            messages=messages,
            response_format={"type": "json_object"},
            step_name=f"generate_{batch_index}",
            metadata={"batch_index": batch_index},
        )
        tasks = parse_search_tasks(
            response.text,
            batch_index=batch_index,
            default_strategy="web",
        )
        self._assign_task_ids(tasks)
        return tasks

    def _resolve_schema(
        self,
        request: UserRequest,
        candidate_tasks: List[SearchTask],
    ) -> List[str]:
        """Determines the active schema for normalization."""
        if request.columns:
            schema = list(request.columns)
        else:
            prompt_template = self._prompts.load("build_schema")
            prompt = prompt_template.format(
                description=request.description,
                example_queries="\n".join(task.query for task in candidate_tasks[:5]),
            )
            response = self._llm_client.complete(
                model=self._models.for_schema(),
                messages=[
                    {"role": "system", "content": "You design CSV schemas."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                step_name="schema",
            )
            schema = parse_schema(response.text)

        # Ensure metadata columns are always present at the end.
        for column in ("source_query_id", "source_strategy"):
            if column not in schema:
                schema.append(column)
        self._logger.info("Resolved schema columns: %s", schema)
        return schema

    def _select_plan(
        self,
        candidate_tasks: List[SearchTask],
        request: UserRequest,
    ) -> SearchPlan:
        """Filters candidates to build the initial plan."""
        primary_template = self._prompts.load("filter_primary")
        trim_template = self._prompts.load("filter_trim")
        target_filtered = max(
            1,
            min(request.min_items // 4 or 1, len(candidate_tasks)),
        )
        filter_feedback = "\n".join(self._filter_feedback_history) or "None"
        initial_plan = self._execute_filter_prompt(
            primary_template,
            candidate_tasks,
            target_filtered,
            filter_feedback,
        )

        filtered_tasks = initial_plan.tasks
        if len(filtered_tasks) > target_filtered:
            remove_count = len(filtered_tasks) - target_filtered
            self._logger.info(
                "Filter kept %d searches (target %d). Running trim pass to remove %d.",
                len(filtered_tasks),
                target_filtered,
                remove_count,
            )
            trimmed_plan = self._execute_filter_prompt(
                trim_template,
                filtered_tasks,
                target_filtered,
            filter_feedback,
            current_count=len(filtered_tasks),
        )
            filtered_tasks = trimmed_plan.tasks

        self._logger.info(
            "Filter kept %d of %d candidate tasks.",
            len(filtered_tasks),
            len(candidate_tasks),
        )
        return SearchPlan(tasks=filtered_tasks)

    def _execute_filter_prompt(
        self,
        prompt_template: str,
        candidate_tasks: List[SearchTask],
        target_filtered: int,
        filter_feedback: str,
        *,
        current_count: Optional[int] = None,
    ) -> SearchPlan:
        serialized_tasks = [
            {
                "id": task.id,
                "query": task.query,
                "strategy": task.strategy,
                "rationale": task.rationale or "",
            }
            for task in candidate_tasks
        ]
        prompt_kwargs = {
            "filtered_count": target_filtered,
            "filter_feedback": filter_feedback,
            "tasks": json.dumps(serialized_tasks, indent=2),
        }
        if "{current_count}" in prompt_template:
            actual_current = current_count or len(candidate_tasks)
            remove_count = max(0, actual_current - target_filtered)
            prompt_kwargs.update(
                {
                    "current_count": actual_current,
                    "remove_count": remove_count,
                }
            )
        prompt = prompt_template.format(**prompt_kwargs)
        response = self._llm_client.complete(
            model=self._models.for_filter_searches(),
            messages=[
                {
                    "role": "system",
                    "content": "You select the most promising searches.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            step_name="filter",
        )
        keep_ids = parse_filter_ids(response.text)
        if not keep_ids:
            keep_ids = [task.id for task in candidate_tasks]
        filtered_tasks = [task for task in candidate_tasks if task.id in keep_ids]
        deduped = list(OrderedDict((task.id, task) for task in filtered_tasks).values())
        if len(deduped) > target_filtered:
            self._logger.warning(
                "Filter returned %d tasks (target %d). Trimming locally.",
                len(deduped),
                target_filtered,
            )
            deduped = deduped[:target_filtered]
        return SearchPlan(tasks=deduped)

    def _apply_review(
        self,
        plan: SearchPlan,
        review: dict,
        new_tasks: List[SearchTask],
    ) -> SearchPlan:
        """Applies user adjustments to the plan."""
        new_plan = SearchPlan(tasks=list(plan.tasks))
        drop_ids = review.get("drop_ids") or []
        if drop_ids:
            new_plan.remove_ids(drop_ids)
        for task in new_tasks:
            new_plan.add_task(task)
        self._logger.info(
            "Plan after review: %d tasks (dropped %d, added %d).",
            len(new_plan.tasks),
            len(drop_ids),
            len(new_tasks),
        )
        return new_plan

    def _apply_candidate_changes(
        self,
        candidate_tasks: List[SearchTask],
        review: dict,
    ) -> Tuple[List[SearchTask], List[SearchTask]]:
        """Returns updated candidate tasks and any newly created user tasks."""
        drop_ids = set(review.get("drop_ids") or [])
        new_queries = review.get("new_queries") or []

        updated_tasks = [task for task in candidate_tasks if task.id not in drop_ids]

        new_tasks: List[SearchTask] = []
        for query in new_queries:
            self._user_task_counter += 1
            new_task = SearchTask(
                id=f"user_{self._user_task_counter}",
                query=str(query),
                strategy="web",
                rationale="User added search",
            )
            new_tasks.append(new_task)

        updated_tasks.extend(new_tasks)
        return updated_tasks, new_tasks

    def _assign_task_ids(self, tasks: List[SearchTask]) -> None:
        """Assigns deterministic IDs to generated tasks."""
        for task in tasks:
            self._task_counter += 1
            task.id = f"g{self._task_counter:04d}"


    def _execute_plan(
        self,
        plan: SearchPlan,
        schema: Sequence[str],
    ) -> ExecutionResult:
        """Executes the approved search plan."""
        rows: List[NormalizedRow] = []
        summaries: List[SearchSummary] = []

        max_workers = max(1, self._config.limits.worker_pool_size)
        futures = {}
        with ThreadPoolExecutor(
            max_workers=min(max_workers, len(plan.tasks) or 1)
        ) as executor:
            for task in plan.tasks:
                self._logger.info("Submitting task %s (%s).", task.id, task.strategy)
                future = executor.submit(self._search_executor.run_task, task, schema)
                futures[future] = task

            for future in as_completed(futures):
                task = futures[future]
                try:
                    task_rows = future.result()
                    note = ""
                except Exception as exc:  # pragma: no cover - defensive logging
                    self._io.display_status(
                        f"Search task {task.id} failed with error: {exc}"
                    )
                    task_rows = []
                    note = str(exc)
                self._logger.info("Task %s returned %d rows.", task.id, len(task_rows))
                rows.extend(task_rows)
                summaries.append(
                    SearchSummary(
                        task=task,
                        items_found=len(task_rows),
                        note=note or None,
                    )
                )
        return ExecutionResult(rows=rows, summaries=summaries)

    def _generate_retry_searches(
        self,
        *,
        request: UserRequest,
        schema: Sequence[str],
        execution: ExecutionResult,
        total_items: int,
    ) -> List[SearchTask]:
        """Generates additional searches after a shortfall."""
        prompt_template = self._prompts.load("retry_searches")
        performance_report = build_performance_report(
            total_items=total_items,
            summaries=execution.summaries,
            user_feedback=self._feedback_history[-1] if self._feedback_history else "",
        )
        prompt = prompt_template.format(
            description=request.description,
            performance_report=performance_report,
            schema=",".join(schema),
            additional_feedback="\n".join(self._feedback_history),
            per_batch=max(5, self._config.limits.per_batch // 2),
        )
        response = self._llm_client.complete(
            model=self._models.for_generate_searches(),
            messages=[
                {
                    "role": "system",
                    "content": "You refine web searches based on performance data.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            step_name="retry",
        )
        tasks = parse_search_tasks(response.text, batch_index=0, default_strategy="web")
        self._assign_task_ids(tasks)
        self._logger.info("Generated %d retry tasks.", len(tasks))
        return tasks

    def _finalize(
        self,
        request: UserRequest,
        schema: Sequence[str],
        summaries: List[SearchSummary],
        retry_round: int,
        *,
        refined_records: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Exports CSV and prints final status."""
        debug_columns = list(schema)
        for extra in ("url", "source_domain"):
            if extra not in debug_columns:
                debug_columns.append(extra)
        debug_path = self._debug_exporter.export_rows(
            self._db.rows(),
            debug_columns,
            filename_prefix="debug",
        )

        if refined_records is None:
            refined_records = self._refiner.refine(self._db.rows(), request, schema)
        if not refined_records:
            self._logger.warning("Refiner returned no records; using raw rows.")
            refined_records = self._refiner.fallback_from_rows(
                self._db.rows(),
                schema,
            )

        metadata_fields = {"source_query_id", "source_strategy"}
        report_columns = [column for column in schema if column not in metadata_fields]
        key_order: List[str] = []
        for record in refined_records:
            for key in record.keys():
                if key in metadata_fields or key in report_columns or key in key_order:
                    continue
                key_order.append(key)
        report_columns = list(dict.fromkeys(["name"] + report_columns + key_order))

        report_path = self._report_exporter.export_dicts(
            refined_records,
            report_columns,
            filename_prefix="report",
        )

        self._io.display_status(f"Debug CSV written to {debug_path}")
        self._io.display_status(f"Refined report written to {report_path}")

        zero_result_ids = [
            summary.task.id for summary in summaries if not summary.items_found
        ]
        self._io.display_status(
            "Final status: "
            f"requested {request.min_items}, collected {self._db.count()}, "
            f"retries used {retry_round}, zero-result searches {zero_result_ids}"
        )
        self._logger.info(
            "Exported %d raw rows and %d refined rows.",
            self._db.count(),
            len(refined_records),
        )
