"""Thin LiteLLM wrapper used by the target, attacker, and monitor.

``chat`` sends one non-streaming completion, retries transient API failures,
and logs token usage and latency. The model id defaults to
``Settings.target_model`` and is never hardcoded.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import litellm
from litellm.exceptions import (
    APIConnectionError,
    BadGatewayError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)
from litellm.types.utils import ModelResponse
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agent_sec.config import Settings, get_settings

logger = logging.getLogger(__name__)

_RETRYABLE = (
    RateLimitError,
    APIConnectionError,
    ServiceUnavailableError,
    InternalServerError,
    Timeout,
    BadGatewayError,
)


def _apply_provider_env(settings: Settings) -> None:
    """Expose credentials LiteLLM reads from the environment.

    Existing process values win, so a shell export is not overwritten by ``.env``.
    """
    os.environ.setdefault("VERTEXAI_PROJECT", settings.vertexai_project)
    os.environ.setdefault("VERTEXAI_LOCATION", settings.vertexai_location)
    if settings.gemini_api_key:
        os.environ.setdefault("GEMINI_API_KEY", settings.gemini_api_key)


@retry(
    retry=retry_if_exception_type(_RETRYABLE),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def _completion_with_retry(**kwargs: Any) -> ModelResponse:
    response = litellm.completion(**kwargs)
    if not isinstance(response, ModelResponse):
        raise TypeError(f"expected a ModelResponse, got {type(response).__name__}")
    return response


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
) -> ModelResponse:
    """Call the configured chat model and return the LiteLLM response.

    ``model`` overrides ``Settings.target_model`` for this call. Vertex project
    and location are passed through only for ``vertex_ai/`` model ids.
    """
    settings = get_settings()
    _apply_provider_env(settings)
    chosen = settings.target_model if model is None else model
    kwargs: dict[str, Any] = {"model": chosen, "messages": messages}
    if chosen.startswith("vertex_ai/"):
        kwargs["vertex_project"] = settings.vertexai_project
        kwargs["vertex_location"] = settings.vertexai_location
    if tools is not None:
        kwargs["tools"] = tools

    started = time.perf_counter()
    response = _completion_with_retry(**kwargs)
    latency_s = time.perf_counter() - started
    usage = response.usage
    logger.info(
        "llm.chat model=%s latency_s=%.3f prompt_tokens=%s completion_tokens=%s total_tokens=%s",
        chosen,
        latency_s,
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
        getattr(usage, "total_tokens", None),
    )
    return response
