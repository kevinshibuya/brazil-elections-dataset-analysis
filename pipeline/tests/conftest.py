"""Shared test helpers.

Fixture files live in ``data/fixtures/`` at the repo root (NOT under
``pipeline/``) so the committed TSE excerpts sit next to ``data/raw/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from observatory.config import settings

FIXTURES_DIR: Path = settings.fixtures_dir


def fixture(relpath: str | Path) -> Path:
    """Absolute path to a committed fixture under ``data/fixtures/``.

    Raises immediately if the fixture is missing -- a silently-absent fixture
    turns a real assertion into a vacuous one.
    """
    path = (FIXTURES_DIR / Path(relpath)).resolve()
    if not path.exists():
        raise FileNotFoundError(f"missing fixture: {path}")
    return path


@pytest.fixture(name="fixture")
def fixture_fixture():
    """``fixture`` as a pytest fixture, for tests that prefer injection."""
    return fixture


@pytest.fixture
def raw_dir() -> Path:
    """``data/raw/`` -- only for tests marked ``integration``."""
    return settings.raw_dir
