"""Runtime settings loaded from the process environment and an optional ``.env``.

Model ids are never hardcoded. Callers read ``target_model``, ``attacker_model``,
and ``monitor_model`` from the environment. The id prefix picks the provider:

- ``gemini/<model-name>``: Google AI Studio (free tier), authenticated with
  ``GEMINI_API_KEY``. This is the default setup.
- ``vertex_ai/<model-name>``: Vertex AI on a GCP project, authenticated with
  gcloud application-default credentials. Needs ``VERTEXAI_PROJECT``; current
  models are served from the ``global`` location.

Each role can use either provider, so models from both can be compared.
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

    # Only needed for vertex_ai/ model ids; AI Studio users can leave it unset.
    vertexai_project: str | None = None
    # Current Gemini 3.x and Gemma 4 models on Vertex answer only on "global".
    vertexai_location: str = "global"
    target_model: str
    attacker_model: str
    monitor_model: str
    gemini_api_key: str | None = None
    runs_dir: str = "runs"
    # None means auto-detect: fold a system prompt into the user message only
    # after a model rejects the system role. True always folds; False never does.
    system_as_user: bool | None = None

    @field_validator("gemini_api_key", "vertexai_project", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
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
