"""The door-1 CLI parses flags and can run one cell without a network call."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sec.llm import DailyQuotaExceeded

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_door1.py"
_CASES = Path(__file__).resolve().parents[1] / "attacks" / "door1_cases.yaml"


def _load():
    spec = importlib.util.spec_from_file_location("run_door1", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _text_response():
    message = SimpleNamespace(role="assistant", content="done", tool_calls=None)
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=3)
    return SimpleNamespace(choices=[choice], usage=usage, call_stats=None)


def _llm(messages, tools=None, model=None, temperature=None, **_kwargs):
    return _text_response()


def _quota_llm(messages, tools=None, model=None, temperature=None, **_kwargs):
    raise DailyQuotaExceeded("test-model", "2026-11-20", 1, 1.0)


def test_defaults_match_the_full_grid() -> None:
    args = _load().parse_args([])
    assert args.cases == "attacks/door1_cases.yaml"
    assert args.variants == ("clean", "poisoned")
    assert args.seeds == (0,)
    assert args.limit is None
    assert args.only is None
    assert args.concurrency == 2


def test_unknown_variant_is_rejected() -> None:
    with pytest.raises(SystemExit):
        _load().parse_args(["--variants", "rug"])


async def test_launch_runs_one_case_offline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await _load().launch(
        _load().parse_args(
            ["--cases", str(_CASES), "--variants", "clean", "--limit", "1", "--concurrency", "1"]
        ),
        llm_fn=_llm,
        model="test-model",
        runs_dir=tmp_path,
    )
    assert code == 0
    assert "P1 clean:" in capsys.readouterr().out
    rows = [json.loads(line) for line in (tmp_path / "results.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert rows[0]["variant"] == "clean"


async def test_launch_returns_nonzero_when_the_quota_stops_it(tmp_path: Path) -> None:
    code = await _load().launch(
        _load().parse_args(
            ["--cases", str(_CASES), "--variants", "clean", "--limit", "1", "--concurrency", "1"]
        ),
        llm_fn=_quota_llm,
        model="test-model",
        runs_dir=tmp_path,
    )
    assert code == 1
