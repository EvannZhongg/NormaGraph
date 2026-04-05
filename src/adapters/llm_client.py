from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from core.config import AppConfig


logger = logging.getLogger(__name__)


class ResponseAPIError(RuntimeError):
    pass


class ResponsesAPIClient:
    _MARKDOWN_CODE_BLOCK_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.llm.enabled and bool(self.config.llm.api_key)

    def create_structured_output(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> Any:
        if not self.enabled:
            raise ResponseAPIError(
                f"LLM client is not configured. Set {self.config.llm.api_key_env} in the project .env file."
            )

        url = f"{self.config.llm.base_url.rstrip('/')}/responses"
        headers = {
            "Authorization": f"Bearer {self.config.llm.api_key}",
            "Content-Type": "application/json",
        }
        if self.config.llm.organization:
            headers["OpenAI-Organization"] = self.config.llm.organization
        if self.config.llm.project:
            headers["OpenAI-Project"] = self.config.llm.project

        payload: dict[str, Any] = {
            "model": self.config.llm.model,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_prompt}],
                },
            ],
            "temperature": self.config.llm.temperature,
            "max_output_tokens": self.config.llm.max_output_tokens,
        }
        payload.update(self._build_structured_output_payload(schema_name=schema_name, schema=schema))
        if self.config.llm.enable_thinking is not None:
            payload["enable_thinking"] = self.config.llm.enable_thinking

        try:
            with httpx.Client(timeout=self.config.llm.timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ResponseAPIError(str(exc)) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise ResponseAPIError("Responses API did not return valid JSON.") from exc
        self._raise_for_response_status(data)

        raw_text = self._extract_output_text(data)
        return self._parse_json_output(raw_text)

    def _build_structured_output_payload(self, *, schema_name: str, schema: dict[str, Any]) -> dict[str, Any]:
        mode = self._structured_output_mode()
        if mode == "response_format":
            return {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    },
                }
            }
        return {
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            }
        }

    def _structured_output_mode(self) -> str:
        mode = self.config.llm.structured_output_mode
        if mode != "auto":
            return mode
        base_url = self.config.llm.base_url.lower()
        if "dashscope.aliyuncs.com/compatible-mode" in base_url:
            return "response_format"
        return "text_format"

    def _raise_for_response_status(self, payload: dict[str, Any]) -> None:
        response_status = payload.get("status")
        if response_status not in {"failed", "cancelled", "incomplete"}:
            return

        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        details = [f"status={response_status}"]
        code = error.get("code")
        if code:
            details.append(f"code={code}")
        message = error.get("message")
        if message:
            details.append(f"message={message}")
        raise ResponseAPIError("Responses API returned non-success status: " + " | ".join(details))

    def _parse_json_output(self, raw_text: str) -> Any:
        last_exc: json.JSONDecodeError | None = None
        for candidate in self._json_text_candidates(raw_text):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as exc:
                last_exc = exc

        logger.error("Failed to parse structured output text after sanitation: %s", raw_text)
        raise ResponseAPIError("Responses API did not return valid JSON text.") from last_exc

    def _json_text_candidates(self, raw_text: str) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()

        def add(candidate: str) -> None:
            value = candidate.strip().lstrip("\ufeff")
            if not value or value in seen:
                return
            seen.add(value)
            candidates.append(value)

        add(raw_text)
        fenced = self._unwrap_markdown_code_block(raw_text)
        if fenced != raw_text:
            add(fenced)

        for source in list(candidates):
            for opening, closing in (("{", "}"), ("[", "]")):
                start = source.find(opening)
                end = source.rfind(closing)
                if 0 <= start < end:
                    add(source[start : end + 1])

        return candidates

    def _unwrap_markdown_code_block(self, text: str) -> str:
        match = self._MARKDOWN_CODE_BLOCK_RE.match(text.strip())
        if not match:
            return text
        return match.group(1).strip()

    def _extract_output_text(self, payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        text_parts: list[str] = []
        for output_item in payload.get("output", []):
            for content_item in output_item.get("content", []):
                item_type = content_item.get("type")
                if item_type not in {"output_text", "text"}:
                    continue
                value = content_item.get("text")
                if isinstance(value, str) and value.strip():
                    text_parts.append(value)
                elif isinstance(value, dict):
                    nested = value.get("value") or value.get("text")
                    if isinstance(nested, str) and nested.strip():
                        text_parts.append(nested)
        if text_parts:
            return "\n".join(text_parts)
        raise ResponseAPIError("Responses API response did not contain output_text.")


class EmbeddingsAPIClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.embedding.enabled and bool(self.config.embedding.api_key)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.enabled:
            raise ResponseAPIError(
                f"Embedding client is not configured. Set {self.config.embedding.api_key_env} in the project .env file."
            )

        url = f"{self.config.embedding.base_url.rstrip('/')}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.config.embedding.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.config.embedding.model,
            "input": texts,
        }
        if self.config.embedding.dimensions:
            payload["dimensions"] = self.config.embedding.dimensions

        try:
            with httpx.Client(timeout=self.config.embedding.timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ResponseAPIError(str(exc)) from exc

        data = response.json()
        rows = data.get("data") or []
        return [row.get("embedding", []) for row in rows]
