"""Prompt repository backed by files on disk."""

from __future__ import annotations

from pathlib import Path
from typing import Dict


class PromptRepository:
    """Loads prompt templates stored in the prompts directory."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._cache: Dict[str, str] = {}

    def load(self, name: str) -> str:
        """Returns the prompt text for the given name, caching the result."""
        if name in self._cache:
            return self._cache[name]
        file_path = self._base_dir / f"{name}.txt"
        if not file_path.exists():
            raise FileNotFoundError(f"Prompt file not found: {file_path}")
        prompt_text = file_path.read_text(encoding="utf-8")
        self._cache[name] = prompt_text
        return prompt_text

