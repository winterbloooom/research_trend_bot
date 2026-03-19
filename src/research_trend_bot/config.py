"""Configuration loading: YAML file + environment variable secrets."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from research_trend_bot.models import AppConfig


def _find_interests_file(config_path: Path) -> Path | None:
    """Find interests.yaml relative to config_path's directory or cwd."""
    for base in (config_path.parent, Path.cwd()):
        candidate = base / "interests.yaml"
        if candidate.exists():
            return candidate
    return None


def load_config(config_path: str | Path) -> AppConfig:
    """Load and validate configuration from YAML files.

    Loads config.yaml for deployment settings (email, llm, feedback),
    then merges interests.yaml on top if it exists (research_interests,
    filtering, language, days_back, special_instructions).

    Secrets (GEMINI_API_KEY, SMTP_PASSWORD) are loaded from
    environment variables or a .env file.
    """
    load_dotenv()

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    # Merge interests.yaml if present (its fields take precedence)
    interests_path = _find_interests_file(path)
    if interests_path is not None:
        with open(interests_path) as f:
            interests_raw = yaml.safe_load(f) or {}
        raw.update(interests_raw)

    return AppConfig(**raw)


def get_gemini_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set")
    return key


def get_smtp_password() -> str:
    password = os.environ.get("SMTP_PASSWORD", "")
    if not password:
        raise RuntimeError("SMTP_PASSWORD environment variable is not set")
    return password


def get_github_token(env_var: str = "GITHUB_TOKEN") -> str | None:
    """Return GitHub token from env, or None if not set (optional)."""
    return os.environ.get(env_var) or None
