"""Configuration loading: YAML file + environment variable secrets."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from research_trend_bot.models import AppConfig


def load_config(config_path: str | Path) -> AppConfig:
    """Load and validate configuration from a YAML file.

    Secrets (GEMINI_API_KEY, SMTP_PASSWORD) are loaded from
    environment variables or a .env file.
    """
    load_dotenv()

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

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
