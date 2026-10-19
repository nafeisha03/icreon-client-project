"""Reads the plain-text settings and rule files in the config/ folder.

Every other step gets its thresholds, wording and lists from here, so a rule can
be read and changed without touching the code that applies it.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    """Load config/<name>.yaml once and keep it in memory."""
    path = CONFIG_DIR / f"{name}.yaml"
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def settings() -> dict:
    return load("settings")


def project_path(key: str) -> Path:
    """A path from settings.yaml, such as 'raw_dir' or 'messy_db', as an absolute path."""
    return PROJECT_ROOT / settings()["paths"][key]


def review_points() -> list[tuple[str, str]]:
    """[(date, label), ...] in date order."""
    points = [(str(p["date"]), str(p["label"])) for p in settings()["review_points"]]
    return sorted(points)


def latest_review_point() -> str:
    return review_points()[-1][0]


def stage_list() -> list[dict]:
    return sorted(load("stages")["stages"], key=lambda s: s["order"])


@lru_cache(maxsize=None)
def stage_order() -> dict[str, int]:
    """Stage name -> position (1 = Target)."""
    return {s["stage"]: int(s["order"]) for s in stage_list()}


def pipeline_stages() -> list[str]:
    return [s["stage"] for s in stage_list() if s["kind"] == "Pipeline"]


@lru_cache(maxsize=None)
def signal_types() -> dict[str, dict]:
    return load("signal_model")["signal_types"]


def signal_label(signal_type: str) -> str:
    info = signal_types().get(signal_type)
    return info["label"] if info else signal_type


def stage_rules() -> dict:
    return load("stage_rules")


def thresholds() -> dict:
    return load("stage_rules")["thresholds"]


def playbook() -> dict:
    return load("action_playbook")


def budget_rules() -> dict:
    return load("budget_rules")


def identity_settings() -> dict:
    return load("identity")


def identity_overrides() -> list[dict]:
    return load("identity_overrides").get("overrides") or []


def note_phrases() -> dict:
    return load("note_phrases")


def source_mappings() -> dict[str, dict]:
    return load("source_mappings")["sources"]


def scenario() -> dict:
    return load("scenario")
