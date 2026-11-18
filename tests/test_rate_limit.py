"""Rate-limit window, daily quota, and 429 backoff. No network."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from litellm.exceptions import RateLimitError
from litellm.types.utils import ModelResponse

from agent_sec import llm as llm_module
from agent_sec.llm import (
    DailyQuotaExceeded,
    LimitConfig,
    ModelLimits,
    RateLimiter,
    _invoke,
    clear_system_role_cache,
    fold_system_into_user,
    load_limits,
)


class FakeClock:
    """Sleep moves the timestamp forward and returns immediately."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _limiter(
    path: Path,
    clock: FakeClock,
    *,
    rpm: float = 100,
    tpm: float = 100_000,
    rpd: float = 10_000,
    margin: float = 1.0,
) -> RateLimiter:
    config = LimitConfig(
        safety_margin=margin,
        models={"m": ModelLimits(rpm=rpm, tpm=tpm, rpd=rpd)},
    )
    return RateLimiter(config, clock, path)


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(
        model="m",
        choices=[
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )


def test_rpm_window_blocks_then_releases(tmp_path: Path) -> None:
    clock = FakeClock()
    limiter = _limiter(tmp_path / "quota.json", clock, rpm=2, tpm=10_000, rpd=100)
    limiter.before_call("m", 10)
    limiter.before_call("m", 10)
    assert clock.t == 1_700_000_000.0

    limiter.before_call("m", 10)
    assert clock.t == 1_700_000_000.0 + 60

    held = clock.t
    limiter.before_call("m", 10)
    assert clock.t == held


def test_tpm_window_blocks_then_releases(tmp_path: Path) -> None:
    clock = FakeClock()
    limiter = _limiter(tmp_path / "quota.json", clock, rpm=100, tpm=100, rpd=100)
    limiter.before_call("m", 70)
    limiter.after_call("m", 70)
    limiter.before_call("m", 40)
    assert clock.t == 1_700_000_000.0 + 60

    held = clock.t
    limiter.before_call("m", 40)
    assert clock.t == held


def test_daily_quota_raises_at_the_margin_and_survives_reload(tmp_path: Path) -> None:
    path = tmp_path / ".quota.json"
    clock = FakeClock()
    limiter = _limiter(path, clock, rpm=1000, tpm=1_000_000, rpd=10, margin=0.85)
    for _ in range(8):
        limiter.before_call("m", 1)
    with pytest.raises(DailyQuotaExceeded) as raised:
        limiter.before_call("m", 1)
    assert raised.value.used == 8
    assert raised.value.limit == pytest.approx(8.5)

    reloaded = _limiter(path, FakeClock(clock.t), rpm=1000, tpm=1_000_000, rpd=10, margin=0.85)
    with pytest.raises(DailyQuotaExceeded):
        reloaded.before_call("m", 1)
    saved = path.read_text(encoding="utf-8")
    assert '"m"' in saved
    assert str(raised.value.day) in saved


def test_limits_yaml_has_the_free_tier_models() -> None:
    config = load_limits()
    assert config.safety_margin == pytest.approx(0.85)
    assert set(config.models) == {
        "gemini/gemma-4-31b-it",
        "gemini/gemma-4-26b-a4b-it",
        "gemini/gemini-3.1-flash-lite",
        "gemini/gemini-3.5-flash-lite",
    }


def test_retry_after_is_honored_and_backoff_is_exponential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    limiter = _limiter(tmp_path / "quota.json", clock, rpm=100, tpm=100_000, rpd=100)
    calls = {"n": 0}

    def completion(**_kwargs: object) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimitError("slow down", "gemini", "m", headers={"retry-after": "5"})
        return _text_response("ok")

    monkeypatch.setattr(llm_module.litellm, "completion", completion)
    monkeypatch.setattr(llm_module.litellm, "token_counter", lambda **_kwargs: 4)
    _response, _latency, waited = _invoke(
        "m", [{"role": "user", "content": "hi"}], None, 0, {}, limiter
    )
    assert calls["n"] == 3
    assert clock.t == 1_700_000_000.0 + 10
    assert waited == 10

    clock2 = FakeClock()
    limiter2 = _limiter(tmp_path / "quota2.json", clock2, rpm=100, tpm=100_000, rpd=100)
    calls["n"] = 0

    def completion_without_hint(**_kwargs: object) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimitError("slow down", "gemini", "m")
        return _text_response("ok")

    monkeypatch.setattr(llm_module.litellm, "completion", completion_without_hint)
    _invoke("m", [{"role": "user", "content": "hi"}], None, None, {}, limiter2)
    assert clock2.t == 1_700_000_000.0 + 3


def test_system_prompt_folds_into_the_user_message() -> None:
    folded = fold_system_into_user(
        [
            {"role": "system", "content": "Be careful."},
            {"role": "user", "content": "Look up Jane."},
        ]
    )
    assert [message["role"] for message in folded] == ["user"]
    assert folded[0]["content"] == "Be careful.\n\nLook up Jane."


def test_chat_retries_once_when_the_system_role_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_system_role_cache()
    clock = FakeClock()
    limiter = _limiter(tmp_path / "quota.json", clock, rpm=100, tpm=100_000, rpd=100)
    seen: list[list[str]] = []

    def completion(**kwargs: object) -> ModelResponse:
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        roles = [str(message["role"]) for message in messages]
        seen.append(roles)
        if "system" in roles:
            raise RuntimeError("system role is not supported for this model")
        assert kwargs["temperature"] == 0
        return _text_response("ok")

    monkeypatch.setattr(llm_module, "get_rate_limiter", lambda: limiter)
    monkeypatch.setattr(
        llm_module,
        "get_settings",
        lambda: SimpleNamespace(
            vertexai_project="",
            vertexai_location="",
            target_model="m",
            gemini_api_key=None,
            system_as_user=None,
        ),
    )

    def no_price(*_args: object, **_kwargs: object) -> float:
        raise RuntimeError("no price")

    monkeypatch.setattr(llm_module.litellm, "completion", completion)
    monkeypatch.setattr(llm_module.litellm, "token_counter", lambda **_kwargs: 4)
    monkeypatch.setattr(llm_module.litellm, "completion_cost", no_price)
    messages = [
        {"role": "system", "content": "Be careful."},
        {"role": "user", "content": "Hi"},
    ]
    response = llm_module.chat(messages, model="m", temperature=0)
    cached = llm_module.chat(messages, model="m", temperature=0)

    assert response.choices[0].message.content == "ok"
    assert response.call_stats.cost_usd == 0.0
    assert response.call_stats.system_role_mode == "folded"
    assert cached.call_stats.system_role_mode == "folded"
    assert seen == [["system", "user"], ["user"], ["user"]]
    assert cached.call_stats.model == "m"
    clear_system_role_cache()


def test_unrelated_system_error_does_not_fold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_system_role_cache()
    limiter = _limiter(tmp_path / "quota.json", FakeClock(), rpm=100, tpm=100_000, rpd=100)
    seen: list[list[str]] = []

    def completion(**kwargs: object) -> ModelResponse:
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        seen.append([str(message["role"]) for message in messages])
        raise RuntimeError("the system is overloaded")

    monkeypatch.setattr(llm_module, "get_rate_limiter", lambda: limiter)
    monkeypatch.setattr(
        llm_module,
        "get_settings",
        lambda: SimpleNamespace(
            vertexai_project="",
            vertexai_location="",
            target_model="m",
            gemini_api_key=None,
            system_as_user=None,
        ),
    )
    monkeypatch.setattr(llm_module.litellm, "completion", completion)
    monkeypatch.setattr(llm_module.litellm, "token_counter", lambda **_kwargs: 4)

    messages = [
        {"role": "system", "content": "Be careful."},
        {"role": "user", "content": "Hi"},
    ]
    with pytest.raises(RuntimeError, match="overloaded"):
        llm_module.chat(messages, model="m")
    assert seen == [["system", "user"]]
    clear_system_role_cache()
