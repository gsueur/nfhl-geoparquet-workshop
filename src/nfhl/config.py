"""Configuration loading. All YAML lives in config/."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


@lru_cache
def load(name: str) -> dict:
    with open(CONFIG_DIR / f"{name}.yaml") as f:
        return yaml.safe_load(f)


def data_root() -> str:
    """Local path or s3:// prefix. Trailing slash stripped."""
    return os.getenv("NFHL_DATA_ROOT", str(REPO_ROOT / "data")).rstrip("/")


def path(stage: str, *parts: str) -> str:
    return "/".join([data_root(), stage, *parts])


def control_db_path() -> str:
    root = data_root()
    if root.startswith("s3://"):
        # The control DB is always local; object storage is not a database.
        return str(REPO_ROOT / "data" / "control" / "fema_control.duckdb")
    return f"{root}/control/fema_control.duckdb"
