from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Point the package at a throwaway data dir before anything imports settings.
os.environ.setdefault("JOBAPPLY_DATA_DIR", str(ROOT / "data" / "test"))
os.environ.pop("ANTHROPIC_API_KEY", None)  # tests must never hit the network


@pytest.fixture()
def settings():
    from jobapply import settings as settings_mod

    return settings_mod.load(reload=True)


@pytest.fixture()
def db(tmp_path):
    from jobapply import db as db_mod

    engine = db_mod.reset_for_tests(f"sqlite:///{tmp_path/'test.db'}")
    yield db_mod
    engine.dispose()


@pytest.fixture()
def registry():
    from jobapply.resumes import ResumeRegistry

    return ResumeRegistry.load(ROOT / "resumes")
