"""Model registry for mapping pipeline steps to model names."""

from __future__ import annotations

from typing import Mapping


class ModelRegistry:
    """Simple registry that maps semantic names to model identifiers."""

    def __init__(self, models: Mapping[str, str]) -> None:
        self._models = dict(models)

    def for_generate_searches(self) -> str:
        """Returns the model for search generation."""
        return self._models["search_gen"]

    def for_filter_searches(self) -> str:
        """Returns the model for filtering searches."""
        return self._models["search_filter"]

    def for_schema(self) -> str:
        """Returns the model for schema generation."""
        return self._models["schema_gen"]

    def for_web(self) -> str:
        """Returns the model for web search execution."""
        return self._models["web"]

    def for_postprocess(self) -> str:
        """Returns the model for post-processing results."""
        return self._models["postprocess"]
