"""Ollama client — calls Ollama's OpenAI-compatible API via httpx.

No translation needed since Ollama speaks OpenAI format natively.
"""

from __future__ import annotations

from typing import Any

import httpx

from src.intelligence.llm_models import Choice, LLMResponse, Message, Usage
from src.utils.logging import get_logger

log = get_logger("intelligence.ollama_client")


class OllamaClient:
    """Async client for Ollama's OpenAI-compatible /v1/chat/completions endpoint."""

    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        self.base_url = base_url.rstrip("/")

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        timeout: float = 60.0,
        extra_body: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a chat completion request to Ollama and return an LLMResponse.

        Args:
            messages: List of message dicts (role/content).
            model: Ollama model name (e.g. "jarvis-brain").
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            timeout: HTTP request timeout in seconds.
            extra_body: Extra fields merged into the request body.
            **kwargs: Ignored (forward-compat).

        Returns:
            LLMResponse parsed from the OpenAI-format JSON.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses from Ollama.
            httpx.HTTPError: On connection / transport errors.
        """
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if extra_body:
            body.update(extra_body)

        url = f"{self.base_url}/v1/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError:
            log.error("ollama_http_error", url=url, model=model)
            raise
        except httpx.HTTPError as exc:
            log.error("ollama_connection_error", url=url, model=model, error=str(exc))
            raise

        choice_data = data["choices"][0]
        msg_data = choice_data["message"]
        usage_data = data.get("usage", {})

        return LLMResponse(
            choices=[
                Choice(
                    message=Message(
                        content=msg_data.get("content"),
                        role=msg_data.get("role", "assistant"),
                    ),
                    finish_reason=choice_data.get("finish_reason", "stop"),
                ),
            ],
            usage=Usage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
            ),
            model=data.get("model", model),
        )


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------

_client: OllamaClient | None = None


def get_ollama_client() -> OllamaClient:
    """Return the global OllamaClient singleton (lazy-created)."""
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client


def reset_ollama_client() -> None:
    """Reset the singleton so the next call creates a fresh instance."""
    global _client
    _client = None
