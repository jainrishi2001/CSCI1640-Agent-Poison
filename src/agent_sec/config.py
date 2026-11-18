"""Runtime settings loaded from the process environment and an optional ``.env``.

Model ids are never hardcoded. Callers read ``target_model``, ``attacker_model``,
and ``monitor_model`` from the environment. Gemini free-tier ids look like
``gemini/<model-name>``; Vertex ids look like ``vertex_ai/<model-name>``.
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Project settings. Field names match the variables in ``.env.example``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    vertexai_project: str
    vertexai_location: str = "us-central1"
    target_model: str
    attacker_model: str
    monitor_model: str
    gemini_api_key: str | None = None
    runs_dir: str = "runs"
    # None means auto-detect: fold a system prompt into the user message only
    # after a model rejects the system role. True always folds; False never does.
    system_as_user: bool | None = None

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def _blank_api_key_is_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator("system_as_user", mode="before")
    @classmethod
    def _auto_system_role(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lower() in {"", "auto"}:
            return None
        return value


def get_settings() -> Settings:
    """Load settings from the environment and ``.env`` in the working directory."""
    return Settings()
