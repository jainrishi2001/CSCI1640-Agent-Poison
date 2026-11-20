"""LiteLLM wrapper with free-tier rate limits and a Gemini system-role fallback.

``chat`` sends one non-streaming completion. Model ids come from the caller or
from ``Settings`` and are never hardcoded. Before each request a per-model
sliding window enforces requests-per-minute and tokens-per-minute, and a
Pacific-time daily counter stops the run before the free-tier daily cap.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import litellm
import yaml
from litellm.exceptions import (
    APIConnectionError,
    BadGatewayError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)
from litellm.types.utils import ModelResponse

from agent_sec.config import Settings, get_settings

litellm.suppress_debug_info = True

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60.0
_PACIFIC = ZoneInfo("America/Los_Angeles")
_RATE_LIMIT_ATTEMPTS = 5
_OVERLOAD_ATTEMPTS = 6
_OVERLOAD_BASE_S = 10.0
_OVERLOAD_CAP_S = 60.0
_RETRYABLE = (
    APIConnectionError,
    ServiceUnavailableError,
    InternalServerError,
    Timeout,
    BadGatewayError,
)

_limiter: RateLimiter | None = None
_limiter_lock = threading.Lock()
_system_role_fold: set[str] = set()


class Clock(Protocol):
    """Timeline used for quota windows, 429 backoff, and overload retries."""

    def now(self) -> float:
        """Return a Unix timestamp in seconds."""

    def sleep(self, seconds: float) -> None:
        """Pause for ``seconds``. Tests advance a fake clock instead of waiting."""


class SystemClock:
    """Wall clock used outside tests."""

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


@dataclass(frozen=True)
class ModelLimits:
    """Free-tier caps for one model, before the safety margin is applied."""

    rpm: float
    tpm: float
    rpd: float


@dataclass(frozen=True)
class LimitConfig:
    """Parsed ``configs/limits.yaml``."""

    safety_margin: float
    models: dict[str, ModelLimits]


@dataclass(frozen=True)
class CallStats:
    """Timing and usage for one successful ``chat`` call."""

    model: str
    latency_s: float
    waited_s: float
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost_usd: float
    system_role_mode: str


class DailyQuotaExceeded(RuntimeError):
    """The next call would pass the daily request cap times the safety margin."""

    def __init__(self, model: str, day: str, used: int, limit: float) -> None:
        super().__init__(
            f"daily quota exceeded for {model} on {day}: "
            f"next call would exceed {limit:.2f} (used {used})"
        )
        self.model = model
        self.day = day
        self.used = used
        self.limit = limit


class TokenEstimateExceedsWindow(RuntimeError):
    """The prompt estimate alone is larger than the tokens-per-minute budget."""

    def __init__(self, model: str, estimated: int, budget: float) -> None:
        super().__init__(
            f"estimated {estimated} tokens for {model} exceeds the window budget of {budget:.0f}"
        )
        self.model = model
        self.estimated = estimated
        self.budget = budget


class UnknownModelLimits(KeyError):
    """``configs/limits.yaml`` has no entry for this model id."""


def default_limits_path() -> Path:
    """Return ``configs/limits.yaml`` next to the repository ``src`` directory."""
    return Path(__file__).resolve().parents[2] / "configs" / "limits.yaml"


def load_limits(path: Path | None = None) -> LimitConfig:
    """Load per-model limits. ``path`` defaults to :func:`default_limits_path`."""
    source = default_limits_path() if path is None else path
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"{source} must contain a mapping")
    margin = float(raw["safety_margin"])
    if margin <= 0:
        raise ValueError("safety_margin must be positive")
    models: dict[str, ModelLimits] = {}
    entries = raw.get("models") or {}
    if not isinstance(entries, dict):
        raise TypeError("limits.yaml models must be a mapping")
    for model, caps in entries.items():
        if not isinstance(caps, dict):
            raise TypeError(f"limits for {model} must be a mapping")
        models[str(model)] = ModelLimits(
            rpm=float(caps["rpm"]),
            tpm=float(caps["tpm"]),
            rpd=float(caps["rpd"]),
        )
    return LimitConfig(safety_margin=margin, models=models)


def pacific_date(timestamp: float) -> str:
    """Return the Pacific calendar date for a Unix timestamp, as ``YYYY-MM-DD``."""
    return datetime.fromtimestamp(timestamp, _PACIFIC).date().isoformat()


class RateLimiter:
    """Per-model minute window plus a persisted Pacific-date request counter.

    The clock is injected so tests can sleep by moving time forward. Token and
    request events share one 60-second window. The daily counter is incremented
    when a call is admitted, and the next call raises :class:`DailyQuotaExceeded`
    once ``used + 1`` would pass ``rpd * safety_margin``.
    """

    def __init__(self, config: LimitConfig, clock: Clock, quota_path: Path) -> None:
        self.config = config
        self.clock = clock
        self.quota_path = quota_path
        self._lock = threading.Lock()
        self._requests: dict[str, list[float]] = {}
        self._tokens: dict[str, list[tuple[float, int]]] = {}

    def before_call(self, model: str, estimated_tokens: int) -> float:
        """Wait until the minute window can hold this call, then reserve it.

        Returns how long the clock was asked to sleep. Raises
        :class:`DailyQuotaExceeded` before waiting when the daily budget is
        already spent, so a run stops instead of blocking until midnight.
        """
        limits = self._limits(model)
        rpm_budget = limits.rpm * self.config.safety_margin
        tpm_budget = limits.tpm * self.config.safety_margin
        if estimated_tokens > tpm_budget:
            raise TokenEstimateExceedsWindow(model, estimated_tokens, tpm_budget)
        waited = 0.0
        while True:
            with self._lock:
                now = self.clock.now()
                self._raise_if_daily_spent(model, now, limits)
                if self._fits(model, now, estimated_tokens, rpm_budget, tpm_budget):
                    self._reserve(model, now, estimated_tokens)
                    self._increment_daily(model, now)
                    return waited
                delay = self._delay_until_fit(model, now, estimated_tokens, rpm_budget, tpm_budget)
            if delay <= 0:
                raise TokenEstimateExceedsWindow(model, estimated_tokens, tpm_budget)
            self.clock.sleep(delay)
            waited += delay

    def after_call(self, model: str, actual_tokens: int) -> None:
        """Replace the reserved estimate for the latest call with actual usage."""
        with self._lock:
            events = self._tokens.get(model)
            if not events:
                return
            stamp, _estimated = events[-1]
            events[-1] = (stamp, max(0, int(actual_tokens)))

    def _limits(self, model: str) -> ModelLimits:
        try:
            return self.config.models[model]
        except KeyError as exc:
            raise UnknownModelLimits(model) from exc

    def _fits(
        self,
        model: str,
        now: float,
        estimated_tokens: int,
        rpm_budget: float,
        tpm_budget: float,
    ) -> bool:
        requests = self._live_requests(model, now)
        tokens = self._live_tokens(model, now)
        return (len(requests) + 1) <= rpm_budget and (tokens + estimated_tokens) <= tpm_budget

    def _delay_until_fit(
        self,
        model: str,
        now: float,
        estimated_tokens: int,
        rpm_budget: float,
        tpm_budget: float,
    ) -> float:
        expiries = [stamp + _WINDOW_SECONDS for stamp in self._requests.get(model, ())]
        expiries.extend(stamp + _WINDOW_SECONDS for stamp, _tokens in self._tokens.get(model, ()))
        for expiry in sorted(expiries):
            if expiry <= now:
                continue
            if self._fits(model, expiry, estimated_tokens, rpm_budget, tpm_budget):
                return expiry - now
        return 0.0

    def _live_requests(self, model: str, now: float) -> list[float]:
        cutoff = now - _WINDOW_SECONDS
        return [stamp for stamp in self._requests.get(model, ()) if stamp > cutoff]

    def _live_tokens(self, model: str, now: float) -> int:
        cutoff = now - _WINDOW_SECONDS
        return sum(tokens for stamp, tokens in self._tokens.get(model, ()) if stamp > cutoff)

    def _reserve(self, model: str, now: float, estimated_tokens: int) -> None:
        self._requests.setdefault(model, []).append(now)
        self._tokens.setdefault(model, []).append((now, estimated_tokens))

    def _raise_if_daily_spent(self, model: str, now: float, limits: ModelLimits) -> None:
        day = pacific_date(now)
        used = self._daily_count(model, day)
        budget = limits.rpd * self.config.safety_margin
        if used + 1 > budget:
            raise DailyQuotaExceeded(model, day, used, budget)

    def _increment_daily(self, model: str, now: float) -> None:
        day = pacific_date(now)
        data = self._read_quota()
        per_model = data.setdefault(model, {})
        per_model[day] = int(per_model.get(day, 0)) + 1
        self._write_quota(data)

    def _daily_count(self, model: str, day: str) -> int:
        data = self._read_quota()
        return int(data.get(model, {}).get(day, 0))

    def _read_quota(self) -> dict[str, dict[str, int]]:
        if not self.quota_path.exists():
            return {}
        raw = json.loads(self.quota_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError(f"{self.quota_path} must contain a JSON object")
        return raw

    def _write_quota(self, data: Mapping[str, Mapping[str, int]]) -> None:
        self.quota_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.quota_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        temporary.replace(self.quota_path)


def get_rate_limiter() -> RateLimiter:
    """Return the process-wide limiter, creating it from settings on first use."""
    global _limiter
    if _limiter is not None:
        return _limiter
    with _limiter_lock:
        if _limiter is None:
            settings = get_settings()
            _limiter = RateLimiter(
                load_limits(),
                clock=SystemClock(),
                quota_path=Path(settings.runs_dir) / ".quota.json",
            )
        return _limiter


def reset_rate_limiter() -> None:
    """Drop the process-wide limiter. Tests use their own instances instead."""
    global _limiter
    _limiter = None


def clear_system_role_cache() -> None:
    """Forget which models rejected a system role. Used by tests."""
    _system_role_fold.clear()


def _apply_provider_env(settings: Settings) -> None:
    """Expose credentials LiteLLM reads from the environment.

    Existing process values win, so a shell export is not overwritten by ``.env``.
    """
    if settings.vertexai_project:
        os.environ.setdefault("VERTEXAI_PROJECT", settings.vertexai_project)
    if settings.vertexai_location:
        os.environ.setdefault("VERTEXAI_LOCATION", settings.vertexai_location)
    if settings.gemini_api_key:
        os.environ.setdefault("GEMINI_API_KEY", settings.gemini_api_key)


def _estimate_tokens(
    model: str,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None,
) -> int:
    try:
        count = litellm.token_counter(
            model=model,
            messages=list(messages),
            tools=list(tools) if tools is not None else None,
        )
    except Exception:  # noqa: BLE001 - tokenizer failures fall back to a character estimate
        logger.warning("token_counter failed for %s; using a character estimate", model)
        blob = json.dumps({"messages": list(messages), "tools": tools}, default=str)
        count = max(1, len(blob) // 4)
    return max(0, int(count))


def _message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, default=str)


def fold_system_into_user(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Move system messages into the start of the first user message.

    Gemma on the Gemini API sometimes rejects the ``system`` role. The system
    text is kept, just no longer as its own role.
    """
    system_parts = [
        _message_text(message.get("content"))
        for message in messages
        if message.get("role") == "system" and _message_text(message.get("content"))
    ]
    rest = [dict(message) for message in messages if message.get("role") != "system"]
    if not system_parts:
        return rest
    prefix = "\n\n".join(system_parts)
    if rest and rest[0].get("role") == "user":
        rest[0]["content"] = prefix + "\n\n" + _message_text(rest[0].get("content"))
        return rest
    return [{"role": "user", "content": prefix}, *rest]


def _rejects_system_role(exc: BaseException) -> bool:
    """Return whether ``exc`` clearly means the system role itself was rejected.

    A bare mention of "system" is not enough: rate-limit and overload errors
    can contain that word. The match is limited to developer instructions,
    system instructions, or a system role the provider says it does not support.
    """
    text = str(exc).lower()
    if "developer instruction" in text or "system instruction" in text:
        return True
    if "system role" not in text:
        return False
    return any(
        phrase in text
        for phrase in ("not support", "unsupported", "not enabled", "invalid", "rejected")
    )


def _should_fold(model: str, settings: Settings) -> bool:
    if settings.system_as_user is True:
        return True
    if settings.system_as_user is False:
        return False
    return model in _system_role_fold


def _retry_after_seconds(exc: RateLimitError, clock: Clock) -> float | None:
    headers: dict[str, str] = {}
    if exc.headers:
        headers.update({str(key).lower(): str(value) for key, value in exc.headers.items()})
    response = getattr(exc, "response", None)
    response_headers = getattr(response, "headers", None)
    if response_headers:
        headers.update(
            {str(key).lower(): str(value) for key, value in dict(response_headers).items()}
        )
    hint = headers.get("retry-after")
    if hint is None:
        return None
    try:
        return max(0.0, float(hint))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(hint)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        return None
    return max(0.0, when.timestamp() - clock.now())


def _overload_jitter() -> float:
    """Up to one extra second so concurrent retries do not wake together."""
    return random.uniform(0.0, 1.0)


def _overload_delay(failed_attempt: int) -> float:
    """Exponential wait after a provider failure. ``failed_attempt`` is 1-based.

    The sequence is 10s, 20s, 40s, then 60s, plus up to one second of jitter.
    Five waits across six attempts total a little over three minutes.
    """
    growth = _OVERLOAD_BASE_S * (2 ** (failed_attempt - 1))
    return min(_OVERLOAD_CAP_S, growth) + _overload_jitter()


def _send_once(
    model: str,
    estimated_tokens: int,
    call_kwargs: dict[str, Any],
    limiter: RateLimiter,
    limiter_waits: list[float],
) -> tuple[ModelResponse, float]:
    limiter_waits.append(limiter.before_call(model, estimated_tokens))
    started = time.perf_counter()
    response = litellm.completion(**call_kwargs)
    latency_s = time.perf_counter() - started
    if not isinstance(response, ModelResponse):
        raise TypeError(f"expected a ModelResponse, got {type(response).__name__}")
    return response, latency_s


def _invoke(
    model: str,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None,
    temperature: float | None,
    extra: Mapping[str, Any],
    limiter: RateLimiter,
) -> tuple[ModelResponse, float, float]:
    """Run one logical chat.

    A 429 is retried up to five times, honoring ``Retry-After`` when present.
    Provider overload (503, 500, 502, timeout, connection error) is retried up
    to six times on the limiter clock.
    """
    outbound = [dict(message) for message in messages]
    estimated = _estimate_tokens(model, outbound, tools)
    call_kwargs: dict[str, Any] = dict(extra)
    call_kwargs["model"] = model
    call_kwargs["messages"] = outbound
    if tools is not None:
        call_kwargs["tools"] = list(tools)
    if temperature is not None:
        call_kwargs["temperature"] = temperature
    backoff = 1.0
    rate_limit_wait = 0.0
    overload_wait = 0.0
    limiter_waits: list[float] = []
    rate_attempt = 0
    overload_attempt = 0
    while True:
        try:
            response, latency_s = _send_once(model, estimated, call_kwargs, limiter, limiter_waits)
        except RateLimitError as exc:
            rate_attempt += 1
            if rate_attempt >= _RATE_LIMIT_ATTEMPTS:
                raise
            delay = _retry_after_seconds(exc, limiter.clock)
            if delay is None:
                delay = backoff
                backoff *= 2
            logger.warning(
                "llm.chat rate limited model=%s attempt=%s sleep_s=%.3f",
                model,
                rate_attempt,
                delay,
            )
            limiter.clock.sleep(delay)
            rate_limit_wait += delay
            continue
        except _RETRYABLE as exc:
            overload_attempt += 1
            if overload_attempt >= _OVERLOAD_ATTEMPTS:
                raise
            delay = _overload_delay(overload_attempt)
            logger.warning(
                "llm.chat overloaded model=%s attempt=%s wait_s=%.1f status=%s",
                model,
                overload_attempt,
                delay,
                getattr(exc, "status_code", None),
            )
            limiter.clock.sleep(delay)
            overload_wait += delay
            continue
        actual = _total_tokens(response, estimated)
        limiter.after_call(model, actual)
        waited = sum(limiter_waits) + rate_limit_wait + overload_wait
        return response, latency_s, waited


def _total_tokens(response: ModelResponse, fallback: int) -> int:
    usage = response.usage
    total = getattr(usage, "total_tokens", None)
    if total is None:
        return fallback
    return int(total)


def _completion_cost(response: ModelResponse) -> float:
    try:
        return float(litellm.completion_cost(response))
    except Exception:  # noqa: BLE001 - unknown free-tier prices are recorded as zero
        return 0.0


def _attach_stats(
    response: ModelResponse,
    model: str,
    latency_s: float,
    waited_s: float,
    system_role_mode: str,
) -> CallStats:
    usage = response.usage
    stats = CallStats(
        model=model,
        latency_s=latency_s,
        waited_s=waited_s,
        prompt_tokens=_optional_int(getattr(usage, "prompt_tokens", None)),
        completion_tokens=_optional_int(getattr(usage, "completion_tokens", None)),
        total_tokens=_optional_int(getattr(usage, "total_tokens", None)),
        cost_usd=_completion_cost(response),
        system_role_mode=system_role_mode,
    )
    response.call_stats = stats
    logger.info(
        "llm.chat model=%s latency_s=%.3f waited_s=%.3f prompt_tokens=%s "
        "completion_tokens=%s total_tokens=%s cost_usd=%.6f",
        stats.model,
        stats.latency_s,
        stats.waited_s,
        stats.prompt_tokens,
        stats.completion_tokens,
        stats.total_tokens,
        stats.cost_usd,
    )
    return stats


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _build_call_kwargs_for_vertex(
    model: str, settings: Settings, extra: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(extra)
    if model.startswith("vertex_ai/"):
        if not settings.vertexai_project:
            raise ValueError(
                f"{model} is a Vertex AI model, so VERTEXAI_PROJECT must be set "
                "(and gcloud application-default credentials must exist). "
                "Use a gemini/ model id for the AI Studio free tier instead."
            )
        merged.setdefault("vertex_project", settings.vertexai_project)
        merged.setdefault("vertex_location", settings.vertexai_location)
    return merged


def chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    temperature: float | None = None,
    **kwargs: Any,
) -> ModelResponse:
    """Call the configured chat model and return the LiteLLM response.

    ``model`` overrides ``Settings.target_model``. ``temperature`` and any
    extra LiteLLM arguments are forwarded. Timing and token usage are stored
    on ``response.call_stats`` as a :class:`CallStats` record.

    When ``system_as_user`` is unset, a model that rejects the system role is
    retried once with that text folded into the first user message, and the
    choice is remembered for that model id.
    """
    settings = get_settings()
    _apply_provider_env(settings)
    chosen = settings.target_model if model is None else model
    limiter = get_rate_limiter()
    extra = _build_call_kwargs_for_vertex(chosen, settings, kwargs)
    folded = _should_fold(chosen, settings)
    outbound: list[dict[str, Any]] = fold_system_into_user(messages) if folded else list(messages)
    try:
        response, latency_s, waited_s = _invoke(
            chosen, outbound, tools, temperature, extra, limiter
        )
    except (RateLimitError, DailyQuotaExceeded, TokenEstimateExceedsWindow):
        raise
    except Exception as exc:
        if (
            settings.system_as_user is not None
            or chosen in _system_role_fold
            or not _rejects_system_role(exc)
        ):
            raise
        logger.info("llm.chat folding system role into the user message for %s", chosen)
        _system_role_fold.add(chosen)
        folded = True
        outbound = fold_system_into_user(messages)
        response, latency_s, waited_s = _invoke(
            chosen, outbound, tools, temperature, extra, limiter
        )
    mode = "folded" if folded else "system"
    _attach_stats(response, chosen, latency_s, waited_s, mode)
    return response
