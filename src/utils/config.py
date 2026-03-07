"""Configuration loader for JARVIS."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


_ROOT = Path(__file__).resolve().parents[2]  # jarvis/
_config_cache: dict[str, Any] | None = None


def load_config() -> dict[str, Any]:
    """Load and cache the main JARVIS configuration."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    load_dotenv(_ROOT / ".env")

    config_path = _ROOT / "config" / "jarvis.yaml"
    if config_path.exists():
        with open(config_path) as f:
            _config_cache = yaml.safe_load(f) or {}
    else:
        _config_cache = {}

    return _config_cache


def get_env(key: str, default: str = "") -> str:
    """Get environment variable with fallback."""
    return os.getenv(key, default)


def get_database_url() -> str:
    return get_env("DATABASE_URL", "postgresql://jarvis:jarvis@localhost:5432/jarvis")


def get_redis_url() -> str:
    return get_env("REDIS_URL", "redis://localhost:6379/0")


def get_project_root() -> Path:
    return _ROOT
