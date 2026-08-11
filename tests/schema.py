"""Shared access to the canonical fresh-install schema used by tests."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE = PROJECT_ROOT / "migrations_baseline" / "0001_baseline.sql"
MIGRATIONS = (BASELINE,)
