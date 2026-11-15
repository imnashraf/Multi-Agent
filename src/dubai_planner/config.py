"""Application configuration and role-based chat LLM factories (Groq + Gemini)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field

_PROVIDER_API_KEY_ENV_MAP: dict[str, tuple[str, ...]] = {
    "groq": ("GROQ_API_KEY",),
    "google_genai": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "google_vertexai": ("GOOGLE_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "mistralai": ("MISTRAL_API_KEY",),
    "cohere": ("COHERE_API_KEY",),
}

_LOCAL_PROVIDERS = {"ollama", "fake"}
_PLACEHOLDER_PREFIXES = ("your-", "replace-", "changeme")


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _clean_secret(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    if not stripped or stripped.lower().startswith(_PLACEHOLDER_PREFIXES):
        return None
    return stripped


def _resolve_api_key(provider: str, explicit_env_var: str) -> str | None:
    api_key = _clean_secret(os.getenv(explicit_env_var))
    if api_key is not None:
        return api_key
    for env_var in _PROVIDER_API_KEY_ENV_MAP.get(provider.lower(), ()):
        api_key = _clean_secret(os.getenv(env_var))
        if api_key is not None:
            return api_key
    return None


def _instantiate_chat_model(
    *,
    provider: str,
    model: str,
    temperature: float,
    api_key: str | None,
    base_url: str | None = None,
    role_label: str = "LLM",
) -> BaseChatModel:
    normalized_provider = provider.strip().lower()
    if (
        normalized_provider not in _LOCAL_PROVIDERS
        and not base_url
        and _clean_secret(api_key) is None
    ):
        expected_vars = _PROVIDER_API_KEY_ENV_MAP.get(normalized_provider, ("LLM_API_KEY",))
        raise ValueError(
            f"Missing API key for {role_label} provider '{provider}'. "
            f"Set {', '.join(expected_vars)} in your environment or .env file."
        )

    kwargs: dict[str, Any] = {
        "model": model,
        "model_provider": provider,
        "temperature": temperature,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return init_chat_model(**kwargs)


class Settings(BaseModel):
    """Runtime settings for agent LLMs, workflow bounds, and local data paths."""

    # Primary LLM: Orchestrator, Destination Research, Logistics, and Budget agents
    llm_provider: str = Field(default="groq", min_length=1)
    llm_model: str = Field(default="openai/gpt-oss-120b", min_length=1)
    llm_api_key: str | None = None
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_base_url: str | None = None

    # Review LLM: Review Agent
    review_llm_provider: str = Field(default="google_genai", min_length=1)
    review_llm_model: str = Field(default="gemini-3.8-flash", min_length=1)
    review_llm_api_key: str | None = None
    review_llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)

    max_revisions: int = Field(default=2, ge=0, le=5)
    offline_mode: bool = False
    project_root: Path = Field(default_factory=_default_project_root)
    data_dir: Path | None = None
    curated_data_path: Path | None = None
    cache_db_path: Path | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.data_dir is None:
            self.data_dir = self.project_root / "data"
        if self.curated_data_path is None:
            self.curated_data_path = self.data_dir / "dubai_curated.json"
        if self.cache_db_path is None:
            self.cache_db_path = self.data_dir / "cache.db"

    def has_primary_llm_credentials(self) -> bool:
        """Return True if primary agent LLM credentials (e.g. Groq) are available."""
        provider = self.llm_provider.strip().lower()
        if provider in _LOCAL_PROVIDERS or self.llm_base_url:
            return True
        return _clean_secret(self.llm_api_key) is not None

    def has_review_llm_credentials(self) -> bool:
        """Return True if Review agent LLM credentials (e.g. Gemini) are available."""
        provider = self.review_llm_provider.strip().lower()
        if provider in _LOCAL_PROVIDERS:
            return True
        return _clean_secret(self.review_llm_api_key) is not None

    def has_llm_credentials(self) -> bool:
        """Return True if either primary or review LLM credentials are configured."""
        return self.has_primary_llm_credentials() or self.has_review_llm_credentials()


def load_settings(
    env_file: str | Path | None = None,
    *,
    override: bool = False,
) -> Settings:
    """Load environment variables (optionally from a .env file) into Settings."""
    project_root = _default_project_root()
    dotenv_path = Path(env_file) if env_file is not None else project_root / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path=dotenv_path, override=override)

    provider = os.getenv("LLM_PROVIDER", "groq").strip() or "groq"
    model = os.getenv("LLM_MODEL", "openai/gpt-oss-120b").strip() or "openai/gpt-oss-120b"
    api_key = _resolve_api_key(provider, "LLM_API_KEY")

    review_provider = os.getenv("REVIEW_LLM_PROVIDER", "google_genai").strip() or "google_genai"
    review_model = os.getenv("REVIEW_LLM_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash"
    review_api_key = _resolve_api_key(review_provider, "REVIEW_LLM_API_KEY")

    raw_temp = os.getenv("LLM_TEMPERATURE", "0.2")
    raw_review_temp = os.getenv("REVIEW_LLM_TEMPERATURE", "0.1")
    raw_max_revisions = os.getenv("MAX_REVISIONS", "2")
    base_url = os.getenv("LLM_BASE_URL", "").strip() or None

    return Settings(
        llm_provider=provider,
        llm_model=model,
        llm_api_key=api_key,
        llm_temperature=float(raw_temp),
        llm_base_url=base_url,
        review_llm_provider=review_provider,
        review_llm_model=review_model,
        review_llm_api_key=review_api_key,
        review_llm_temperature=float(raw_review_temp),
        max_revisions=int(raw_max_revisions),
        offline_mode=_parse_bool(os.getenv("OFFLINE_MODE"), default=False),
        project_root=project_root,
    )


def get_chat_model(settings: Settings | None = None) -> BaseChatModel:
    """Instantiate the primary chat model (default: Groq openai/gpt-oss-120b)."""
    cfg = settings or load_settings()
    return _instantiate_chat_model(
        provider=cfg.llm_provider,
        model=cfg.llm_model,
        temperature=cfg.llm_temperature,
        api_key=cfg.llm_api_key,
        base_url=cfg.llm_base_url,
        role_label="primary LLM",
    )


def get_review_chat_model(settings: Settings | None = None) -> BaseChatModel:
    """Instantiate the Review Agent chat model (default: Gemini gemini-2.5-flash)."""
    cfg = settings or load_settings()
    return _instantiate_chat_model(
        provider=cfg.review_llm_provider,
        model=cfg.review_llm_model,
        temperature=cfg.review_llm_temperature,
        api_key=cfg.review_llm_api_key,
        role_label="Review LLM",
    )
