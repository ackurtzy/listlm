"""Entry point for the desai-search workflow."""

from __future__ import annotations

import logging
import os

from config import get_config
from core.llm_client import LLMClient
from core.model_registry import ModelRegistry
from core.prompt_repository import PromptRepository
from pipeline.orchestrator import PipelineOrchestrator
from pipeline.user_io import TerminalIO
from postprocess.refiner import ResultRefiner
from search.executor import SearchExecutor
from storage.database import InMemoryDatabase
from storage.exporter import CSVExporter


def _configure_logging() -> None:
    """Configures application-wide logging."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def main() -> None:
    """CLI entry point."""
    _configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting desai-search workflow.")

    config = get_config()

    prompt_repo = PromptRepository(config.paths.prompts_dir)
    llm_client = LLMClient(config.paths.raw_responses_dir)
    model_registry = ModelRegistry(config.models)
    database = InMemoryDatabase()
    debug_exporter = CSVExporter(config.paths.debug_export_dir)
    report_exporter = CSVExporter(config.paths.reports_dir)
    io = TerminalIO()

    search_executor = SearchExecutor(
        llm_client=llm_client,
        model_registry=model_registry,
        strategy_map=config.strategy_map,
        use_mock_search=config.flags.use_mock_search,
    )

    refiner = ResultRefiner(
        config=config,
        prompt_repository=prompt_repo,
        llm_client=llm_client,
        model_registry=model_registry,
    )

    orchestrator = PipelineOrchestrator(
        config=config,
        prompt_repository=prompt_repo,
        llm_client=llm_client,
        model_registry=model_registry,
        search_executor=search_executor,
        db=database,
        debug_exporter=debug_exporter,
        report_exporter=report_exporter,
        refiner=refiner,
        io=io,
    )
    orchestrator.run()
    logger.info("Workflow completed.")


if __name__ == "__main__":
    main()
