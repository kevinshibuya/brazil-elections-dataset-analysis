"""Shared test helpers.

Fixture files live in ``data/fixtures/`` at the repo root (NOT under
``pipeline/``) so the committed TSE excerpts sit next to ``data/raw/``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import polars as pl
import psycopg
import pytest

from observatory import ingest, parse
from observatory.config import settings
from observatory.db import Database

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


# ==========================================================================
# Live-database fixtures (marker ``db``)
#
# The ingest tests run against a REAL Postgres -- staging, COPY, partitioning,
# NULLS NOT DISTINCT and transactional TRUNCATE have no honest in-memory
# stand-in.  When no server is reachable they skip with an actionable message
# instead of failing.
# ==========================================================================

TEST_DB_NAME = "elections_test"


def test_database_url() -> str:
    """URL of the throwaway test database (``$TEST_DATABASE_URL`` wins)."""
    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        return override
    parts = urlsplit(settings.database_url)
    return urlunsplit(parts._replace(path=f"/{TEST_DB_NAME}"))


def _admin_url() -> str:
    parts = urlsplit(test_database_url())
    return urlunsplit(parts._replace(path="/postgres"))


@lru_cache(maxsize=1)
def _ensure_test_database() -> str | None:
    """Create the test database if needed.  Returns a skip reason, or None."""
    url = test_database_url()
    name = urlsplit(url).path.lstrip("/")
    try:
        with psycopg.connect(_admin_url(), autocommit=True, connect_timeout=5) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
            ).fetchone()
            if not exists:
                conn.execute(f'CREATE DATABASE "{name}"')
    except psycopg.Error as exc:
        first_line = str(exc).strip().splitlines()[0]
        return (
            f"Postgres is not reachable at {_admin_url()} "
            f"({exc.__class__.__name__}: {first_line}). Start it with "
            "`docker compose up -d` from the repo root, then re-run."
        )
    return None


@pytest.fixture(scope="session")
def _live_db():
    reason = _ensure_test_database()
    if reason:
        pytest.skip(reason)
    db = Database.connect(test_database_url())
    db.create_schema()
    yield db
    db.close()


@pytest.fixture
def test_db(_live_db: Database) -> Database:
    """An empty, schema-created ``elections_test``, fresh for every test."""
    _live_db.reset_data()
    _live_db.execute("TRUNCATE meta.gate_runs RESTART IDENTITY")
    for kind in ingest.KINDS.values():
        _live_db.execute(f"DROP TABLE IF EXISTS {kind.staging_table}")
    return _live_db


# --------------------------------------------------------------------------
# Synthetic batches, grown from the committed TSE excerpts
#
# Every row descends from a real line of `votacao_secao_2022_RR.csv`: the
# excerpt supplies the *votáveis* (candidates of all four widths, legenda
# rows, 95/96, the 2026 supplementary election), and they are replicated
# across synthetic section numbers to reach the row counts the acceptance
# assertions require.
# --------------------------------------------------------------------------

VOTES_EXCERPT = "tse/votacao_secao_2022_RR_excerpt.csv"
MALFORMED_EXCERPT = "tse/votacao_malformed_excerpt.csv"

_FIXTURE_MUN = ("03018", "BOA VISTA", "RR")


@lru_cache(maxsize=1)
def _excerpt_votes() -> pl.DataFrame:
    good, bad = parse.read_votes(fixture(VOTES_EXCERPT))
    assert bad.height == 0, "the votes excerpt is supposed to parse cleanly"
    return good


@lru_cache(maxsize=1)
def _bad_rows() -> pl.DataFrame:
    _good, bad = parse.read_votes(fixture(MALFORMED_EXCERPT))
    assert bad.height >= 2, "the malformed excerpt should yield several bad rows"
    return bad


def _templates(election_ids: tuple[int, ...]) -> pl.DataFrame:
    """One row per distinct votável: the shape a section's ballot can take.

    Deduplicated on (race, round, votável) so no (uf, race, round, number)
    can arrive carrying two candidate names -- ingest hard-asserts that.
    """
    return (
        _excerpt_votes()
        .filter(pl.col("election_id").is_in(election_ids))
        .sort("race", "round", "cand_number", "cand_name")
        .unique(subset=["race", "round", "votavel_kind", "cand_number"], keep="first")
        .sort("race", "round", "votavel_kind", "cand_number")
    )


def _spread(templates: pl.DataFrame, n_sections: int, *, zone: int = 1) -> pl.DataFrame:
    """Replicate ``templates`` across ``n_sections`` synthetic sections."""
    mun_code, mun_name, uf = _FIXTURE_MUN
    sections = pl.DataFrame({"section": list(range(1, n_sections + 1))}, schema={"section": pl.Int32})
    spread = templates.drop("uf", "mun_code", "mun_name", "zone", "section").join(
        sections, how="cross"
    )
    return spread.with_columns(
        pl.lit(uf).alias("uf"),
        pl.lit(mun_code).alias("mun_code"),
        pl.lit(mun_name).alias("mun_name"),
        pl.lit(zone, dtype=pl.Int32).alias("zone"),
        # Deterministic, plausible, never negative.
        ((pl.col("section") * 7 + pl.col("cand_number")) % 97).cast(pl.Int64).alias("votes"),
    ).select(list(parse.VOTES_SCHEMA))


def _batch(good: pl.DataFrame, bad: pl.DataFrame, source: str) -> ingest.Batch:
    return ingest.Batch(kind="votes", source_file=source, good=good, bad=bad)


_NO_BAD = pl.DataFrame(schema=parse.BAD_SCHEMA)


@pytest.fixture
def fixture_batch_ok() -> ingest.Batch:
    """~12 k clean rows, no bad rows at all."""
    good = _spread(_templates(parse.ELECTIONS_2022_GENERAL), 68)
    assert good.height >= 10_000
    return _batch(good, _NO_BAD, "fixture://votes_ok")


@pytest.fixture
def fixture_batch_poison() -> ingest.Batch:
    """A batch whose bad-row share is far above the 0.01 % gate (~0.9 %)."""
    good = _spread(_templates(parse.ELECTIONS_2022_GENERAL), 6, zone=2)
    bad = _bad_rows()
    batch = _batch(good, bad, "fixture://votes_poison")
    rate = bad.height / batch.rows_attempted
    assert rate > ingest.QUARANTINE_GATE, f"poison fixture is not poisonous ({rate:%})"
    return batch


@pytest.fixture
def fixture_batch_one_bad() -> ingest.Batch:
    """>= 10 000 attempted rows with exactly one bad -- stays under the gate."""
    good = _spread(_templates(parse.ELECTIONS_2022_GENERAL), 68)
    bad = _bad_rows().head(1)
    batch = _batch(good, bad, "fixture://votes_one_bad")
    assert batch.rows_attempted >= 10_000
    assert 1 / batch.rows_attempted <= ingest.QUARANTINE_GATE
    return batch


@pytest.fixture
def fixture_batch_mixed_elections() -> ingest.Batch:
    """The A3 trap: 2022-general rows and 2026-supplementary rows that COLLIDE.

    Each supplementary (``CD_ELEICAO=6278``) row from the excerpt is paired
    with a twin that is identical except for its election id, so the two share
    ``(uf, mun, zone, section, race, round, votável)`` exactly.  An ingest that
    forgets to filter therefore cannot merely double-count: it violates
    ``fact_votes_key`` and blows up.
    """
    supplementary = _excerpt_votes().filter(pl.col("election_id") == 6278)
    assert supplementary.height > 0, "excerpt should carry CD_ELEICAO=6278 rows"
    general = supplementary.with_columns(pl.lit(546, dtype=pl.Int32).alias("election_id"))
    good = pl.concat([supplementary, general])
    collisions = good.height - good.unique(
        subset=["uf", "mun_code", "zone", "section", "race", "round", "cand_number"]
    ).height
    assert collisions == supplementary.height, "the two elections must collide"
    return _batch(good, _NO_BAD, "fixture://votes_mixed_elections")
