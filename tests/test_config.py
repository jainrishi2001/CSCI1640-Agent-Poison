"""Provider selection: AI Studio (gemini/) and Vertex AI (vertex_ai/) side by side.

Settings are built with ``_env_file=None`` and a cleaned environment, so a
developer's local ``.env`` cannot change the outcome.
"""

from __future__ import annotations

import pytest

from agent_sec.config import Settings
from agent_sec.llm import _build_call_kwargs_for_vertex

_PROVIDER_VARS = (
    "VERTEXAI_PROJECT",
    "VERTEXAI_LOCATION",
    "GEMINI_API_KEY",
    "TARGET_MODEL",
    "ATTACKER_MODEL",
    "MONITOR_MODEL",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _PROVIDER_VARS:
        monkeypatch.delenv(name, raising=False)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "target_model": "gemini/gemma-4-31b-it",
        "attacker_model": "gemini/gemma-4-26b-a4b-it",
        "monitor_model": "gemini/gemini-3.1-flash-lite",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_ai_studio_only_setup_needs_no_vertex_project(clean_env: None) -> None:
    settings = _settings(gemini_api_key="test-key")
    assert settings.vertexai_project is None
    assert settings.vertexai_location == "global"


def test_blank_vertex_project_reads_as_unset(clean_env: None) -> None:
    assert _settings(vertexai_project="  ").vertexai_project is None


def test_gemini_model_gets_no_vertex_arguments(clean_env: None) -> None:
    kwargs = _build_call_kwargs_for_vertex("gemini/gemma-4-31b-it", _settings(), {"x": 1})
    assert kwargs == {"x": 1}


def test_vertex_model_without_project_fails_clearly(clean_env: None) -> None:
    with pytest.raises(ValueError, match="VERTEXAI_PROJECT must be set"):
        _build_call_kwargs_for_vertex("vertex_ai/gemini-3.1-flash-lite", _settings(), {})


def test_vertex_model_gets_project_and_global_location(clean_env: None) -> None:
    settings = _settings(vertexai_project="course-project")
    kwargs = _build_call_kwargs_for_vertex("vertex_ai/gemini-3.1-flash-lite", settings, {})
    assert kwargs == {"vertex_project": "course-project", "vertex_location": "global"}


def test_roles_can_mix_providers(clean_env: None) -> None:
    settings = _settings(
        target_model="vertex_ai/gemini/gemma-4-26b-a4b-it-maas",
        vertexai_project="course-project",
    )
    assert settings.target_model.startswith("vertex_ai/")
    assert settings.attacker_model.startswith("gemini/")
