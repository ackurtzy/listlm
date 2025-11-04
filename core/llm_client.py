"""LLM client built on the OpenAI Responses API."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - allows import without dependency
    OpenAI = None  # type: ignore


@dataclass
class LLMResult:
    """Container for parsed LLM responses."""

    text: str
    raw: Dict[str, Any]


class LLMClient:
    """Thin wrapper around the OpenAI Responses API."""

    def __init__(self, output_dir: Path) -> None:
        self._api_key = os.getenv("OPENAI_API_KEY")
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._client = None
        if self._api_key and OpenAI is not None:
            self._client = OpenAI(api_key=self._api_key)

    def complete(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        step_name: str = "generic",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LLMResult:
        """Executes a call to the OpenAI Responses API."""
        if not self._api_key:
            raise EnvironmentError(
                "OPENAI_API_KEY is not set. Unable to call the OpenAI API."
            )
        if self._client is None:
            raise ImportError(
                "The openai package is required to call the Responses API."
            )

        kwargs: Dict[str, Any] = {
            "model": model,
            "input": messages,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        if tools is not None:
            kwargs["tools"] = tools

        try:
            response = self._client.responses.create(**kwargs)
        except TypeError as error:
            if "response_format" in str(error) and "response_format" in kwargs:
                # Some client versions do not yet expose response_format. Retry without it.
                kwargs.pop("response_format", None)
                response = self._client.responses.create(**kwargs)
            else:
                raise
        raw_dict = self._response_to_dict(response)
        text = self._extract_text(response, raw_dict)
        record = {
            "timestamp": time.time(),
            "model": model,
            "messages": messages,
            "metadata": metadata or {},
            "response": raw_dict,
        }
        self._persist_record(record, step_name)
        return LLMResult(text=text, raw=raw_dict)

    def _persist_record(self, payload: Dict[str, Any], step_name: str) -> None:
        """Persists raw Responses API payload for debugging."""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        file_name = f"{timestamp}_{step_name}.json"
        file_path = self._output_dir / file_name
        file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _response_to_dict(response: Any) -> Dict[str, Any]:
        """Converts the SDK response object into a dictionary."""
        if hasattr(response, "model_dump"):
            return response.model_dump()
        if hasattr(response, "to_dict"):
            return response.to_dict()  # type: ignore[no-any-return]
        if hasattr(response, "dict"):
            return response.dict()  # type: ignore[no-any-return]
        if hasattr(response, "json"):
            return json.loads(response.json())  # type: ignore[arg-type]
        raise TypeError("Unexpected response object type from OpenAI SDK.")

    @staticmethod
    def _extract_text(response: Any, raw: Dict[str, Any]) -> str:
        """Retrieves the textual content from a Responses API object."""
        if hasattr(response, "output_text"):
            return response.output_text  # type: ignore[return-value]

        # Fallback to raw dictionary structure.
        output = raw.get("output", [])
        if not output:
            return ""
        texts: List[str] = []
        for block in output:
            content = block.get("content", [])
            for item in content:
                if item.get("type") == "output_text":
                    texts.append(item.get("text", ""))
                elif item.get("type") == "text":
                    texts.append(item.get("text", ""))
        return "\n".join(texts).strip()
