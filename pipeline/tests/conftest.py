"""Shared test helpers.

Fixture files live in ``data/fixtures/`` at the repo root (NOT under
``pipeline/``) so the committed TSE excerpts sit next to ``data/raw/``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import polars as pl
import psycopg
import pytest

from observatory import db as db_module, ingest, parse
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


# ==========================================================================
# Reconciliation-gate fixtures (Task 5)
#
# Hand-seeded miniature warehouses.  They are small on purpose: the gate's
# real acceptance run is the RR pipeline (``-m integration``), and what these
# have to pin down is the *logic* -- that every diff reaches the report before
# the raise, that "absent from the rollup" is a category and not a failure,
# and that it stops being a mere category the moment those votes turn up as
# valid somewhere.  Shapes and values follow the committed TSE excerpts:
# municipality 03018 BOA VISTA / RR, zone 1, CD_ELEICAO 546.
# ==========================================================================

_ELECTION_STATE_R1 = 546


def _section_id(mun_code: str, zone: int, section: int) -> int:
    return (
        int(mun_code) * db_module.SECTION_ID_MUN_STRIDE
        + zone * db_module.SECTION_ID_ZONE_STRIDE
        + section
    )


def _candidate_id(uf: str, race: str, rnd: int, number: int) -> int:
    ident = db_module.UF_INDEX[uf] * db_module.CANDIDATE_ID_RACE_STRIDE
    ident = (ident + db_module.RACE_INDEX[race]) * db_module.CANDIDATE_ID_ROUND_STRIDE
    return (ident + rnd) * db_module.CANDIDATE_ID_NUMBER_STRIDE + number


@dataclass
class Warehouse:
    """A hand-built warehouse, plus what the gate is expected to say about it.

    ``expected_diffs`` is the height of the WHOLE report (every category), not
    just the fatal rows -- the acceptance test asserts on the full report.
    """

    db: Database
    expected_diffs: int = 0
    expected_candidates: int = 0
    expected_votes: int = 0
    _municipalities: set[str] = field(default_factory=set)
    _sections: set[int] = field(default_factory=set)
    _candidates: set[int] = field(default_factory=set)

    # -- dimensions --------------------------------------------------------

    def municipality(self, code: str, name: str = "BOA VISTA", uf: str = "RR") -> str:
        if code not in self._municipalities:
            self.db.execute(
                "INSERT INTO dim_municipality (code, name, uf, region) "
                "VALUES (%s, %s, %s, 'N')",
                (code, name, uf),
            )
            self._municipalities.add(code)
        return code

    def section(self, mun: str, zone: int, section: int, uf: str = "RR") -> int:
        self.municipality(mun)
        sid = _section_id(mun, zone, section)
        if sid not in self._sections:
            self.db.execute(
                "INSERT INTO dim_section (id, uf, mun_code, zone, section) "
                "VALUES (%s, %s, %s, %s, %s)",
                (sid, uf, mun, zone, section),
            )
            self._sections.add(sid)
        return sid

    def candidate(
        self,
        race: str,
        rnd: int,
        number: int,
        name: str,
        *,
        uf: str = "RR",
        party: int | None = None,
        coalition: str | None = None,
    ) -> int:
        cid = _candidate_id(uf, race, rnd, number)
        if cid not in self._candidates:
            self.db.execute(
                "INSERT INTO dim_candidate "
                "(id, uf, race, round, number, name, party_number, coalition) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (cid, uf, race, rnd, number, name, party, coalition),
            )
            self._candidates.add(cid)
        return cid

    # -- facts -------------------------------------------------------------

    def cand_votes(
        self,
        mun: str,
        zone: int,
        section: int,
        race: str,
        rnd: int,
        number: int,
        votes: int,
        *,
        name: str = "CANDIDATE",
        uf: str = "RR",
    ) -> None:
        sid = self.section(mun, zone, section, uf)
        party = int(str(number)[:2])
        cid = self.candidate(race, rnd, number, name, uf=uf, party=party)
        self.db.execute(
            "INSERT INTO fact_votes (section_id, race, round, votavel_kind, "
            "candidate_id, party_number, votes, election_id) "
            "VALUES (%s, %s, %s, 'candidate', %s, %s, %s, %s)",
            (sid, race, rnd, cid, party, votes, _ELECTION_STATE_R1),
        )

    def other_votes(
        self,
        mun: str,
        zone: int,
        section: int,
        race: str,
        rnd: int,
        kind: str,
        votes: int,
        *,
        party: int | None = None,
    ) -> None:
        """A ``legenda``, ``blank`` or ``null`` votável row."""
        sid = self.section(mun, zone, section)
        self.db.execute(
            "INSERT INTO fact_votes (section_id, race, round, votavel_kind, "
            "party_number, votes, election_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (sid, race, rnd, kind, party, votes, _ELECTION_STATE_R1),
        )

    def stats(
        self,
        mun: str,
        zone: int,
        section: int,
        race: str,
        rnd: int,
        *,
        aptos: int,
        comparecimento: int,
        abstentions: int,
        blank: int,
        null_votes: int,
    ) -> None:
        sid = self.section(mun, zone, section)
        self.db.execute(
            "INSERT INTO fact_section_stats (section_id, race, round, aptos, "
            "comparecimento, abstentions, blank, null_votes, election_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (sid, race, rnd, aptos, comparecimento, abstentions, blank,
             null_votes, _ELECTION_STATE_R1),
        )

    # -- TSE's published rollups ------------------------------------------

    def rollup_cand(
        self,
        mun: str,
        zone: int,
        race: str,
        rnd: int,
        number: int,
        *,
        nominal: int,
        valid: int | None = None,
        uf: str = "RR",
    ) -> None:
        self.municipality(mun)
        self.db.execute(
            "INSERT INTO rollup_cand_munzona (uf, mun_code, zone, race, round, "
            "cand_number, party_number, votes_nominal, votes_nominal_valid, "
            "in_transit, election_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, false, %s)",
            (uf, mun, zone, race, rnd, number, int(str(number)[:2]), nominal,
             nominal if valid is None else valid, _ELECTION_STATE_R1),
        )

    def rollup_party(
        self,
        mun: str,
        zone: int,
        race: str,
        rnd: int,
        party: int,
        *,
        legenda_valid: int,
        legenda_annulled: int = 0,
        legenda_subjud: int = 0,
        nominal_valid: int = 0,
        uf: str = "RR",
    ) -> None:
        self.municipality(mun)
        self.db.execute(
            "INSERT INTO rollup_party_munzona (uf, mun_code, zone, race, round, "
            "party_number, votes_legenda_valid, votes_legenda_annulled, "
            "votes_legenda_anul_subjud, votes_nominal_valid, in_transit, "
            "election_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false, %s)",
            (uf, mun, zone, race, rnd, party, legenda_valid, legenda_annulled,
             legenda_subjud, nominal_valid, _ELECTION_STATE_R1),
        )

    def rollup_detail(
        self,
        mun: str,
        zone: int,
        race: str,
        rnd: int,
        *,
        aptos: int,
        comparecimento: int,
        abstentions: int,
        blank: int,
        null_votes: int,
        uf: str = "RR",
    ) -> None:
        self.municipality(mun)
        self.db.execute(
            "INSERT INTO rollup_detail_munzona (uf, mun_code, zone, race, round, "
            "aptos, comparecimento, abstentions, blank, null_votes, in_transit, "
            "election_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false, %s)",
            (uf, mun, zone, race, rnd, aptos, comparecimento, abstentions,
             blank, null_votes, _ELECTION_STATE_R1),
        )


BOA_VISTA = "03018"
IRACEMA = "03026"


@pytest.fixture
def seeded_mismatch(test_db: Database) -> Warehouse:
    """One genuine fatal mismatch in every fatal category, all at once.

    Five diffs, deliberately spread so no single query can find them all:

      * SEN nominal, key present in both sources  (20 vs 25)
      * SEN nominal, key only TSE published        (0 vs 7)
      * DEPFED legenda                             (4 vs 6)
      * SEN turnout, comparecimento                (160 vs 161)
      * SEN internal blank/null cross-check        (4 vs 5)

    Nothing here is an ``annulled_candidacy``: every votável we hold has a
    rollup row.  A gate that stopped at the first diff would report one.
    """
    w = Warehouse(test_db)

    # -- SEN round 1: nominal + turnout + internal ------------------------
    w.cand_votes(BOA_VISTA, 1, 1, "SEN", 1, 130, 12, name="A")
    w.cand_votes(BOA_VISTA, 1, 2, "SEN", 1, 130, 8, name="A")
    w.rollup_cand(BOA_VISTA, 1, "SEN", 1, 130, nominal=25)   # ours 20 -> diff
    w.candidate("SEN", 1, 140, "B", party=14)
    w.rollup_cand(BOA_VISTA, 1, "SEN", 1, 140, nominal=7)    # ours 0 -> diff

    w.other_votes(BOA_VISTA, 1, 1, "SEN", 1, "blank", 3)
    w.other_votes(BOA_VISTA, 1, 1, "SEN", 1, "null", 4)      # stats say 5 -> diff
    w.stats(BOA_VISTA, 1, 1, "SEN", 1, aptos=100, comparecimento=80,
            abstentions=20, blank=3, null_votes=5)
    w.stats(BOA_VISTA, 1, 2, "SEN", 1, aptos=100, comparecimento=80,
            abstentions=20, blank=0, null_votes=0)
    w.rollup_detail(BOA_VISTA, 1, "SEN", 1, aptos=200, comparecimento=161,
                    abstentions=40, blank=3, null_votes=5)   # 160 vs 161 -> diff

    # -- DEPFED round 1: legenda, everything else clean --------------------
    w.other_votes(BOA_VISTA, 1, 1, "DEPFED", 1, "legenda", 4, party=15)
    w.rollup_party(BOA_VISTA, 1, "DEPFED", 1, 15, legenda_valid=6)  # 4 vs 6 -> diff
    w.stats(BOA_VISTA, 1, 1, "DEPFED", 1, aptos=100, comparecimento=80,
            abstentions=20, blank=0, null_votes=0)
    w.rollup_detail(BOA_VISTA, 1, "DEPFED", 1, aptos=100, comparecimento=80,
                    abstentions=20, blank=0, null_votes=0)

    w.expected_diffs = 5
    return w


@pytest.fixture
def seeded_annulled_candidacy(test_db: Database) -> Warehouse:
    """Amendment A4: votes cast at section level, no rollup row anywhere.

    Two candidacies rejected after the election -- one shaped like RR's senate
    case (333 HELDER GIRÃO BARRETO), one like a deputy.  Everything else
    reconciles exactly, so the gate must come back ``ok`` with the report
    carrying both as findings.
    """
    w = Warehouse(test_db)

    # A candidate that reconciles, so "nominal" is not a vacuous comparison.
    w.cand_votes(BOA_VISTA, 1, 1, "SEN", 1, 130, 50, name="A")
    w.rollup_cand(BOA_VISTA, 1, "SEN", 1, 130, nominal=50)

    # Absent from the rollup entirely: 60 + 40 = 100 votes.
    w.cand_votes(BOA_VISTA, 1, 1, "SEN", 1, 333, 60, name="HELDER GIRÃO BARRETO")
    w.cand_votes(BOA_VISTA, 1, 2, "SEN", 1, 333, 40, name="HELDER GIRÃO BARRETO")
    # ... and a deputy, likewise absent: 25 votes.
    w.cand_votes(BOA_VISTA, 1, 1, "DEPFED", 1, 4040, 25, name="ANTÔNIA NECO")

    w.expected_candidates = 2
    w.expected_votes = 125
    return w


@pytest.fixture
def seeded_annulled_but_valid(test_db: Database) -> Warehouse:
    """The one condition under which category 4 is fatal.

    Same shape as ``seeded_annulled_candidacy`` -- candidate 333 has section
    votes in BOA VISTA and no rollup row there -- except that TSE *did*
    publish valid votes for that candidacy in IRACEMA.  So the candidacy was
    never annulled and BOA VISTA's rollup row is simply missing: our "these
    votes were excluded" story is wrong and the gate must say so.
    """
    w = Warehouse(test_db)

    w.cand_votes(BOA_VISTA, 1, 1, "SEN", 1, 333, 100, name="HELDER GIRÃO BARRETO")
    # Same candidate, another municipality -- published, valid, and matching.
    w.municipality(IRACEMA, name="IRACEMA")
    w.cand_votes(IRACEMA, 2, 1, "SEN", 1, 333, 40, name="HELDER GIRÃO BARRETO")
    w.rollup_cand(IRACEMA, 2, "SEN", 1, 333, nominal=40, valid=40)

    return w


@pytest.fixture
def real_db():
    """The REAL ``elections`` database -- for ``@pytest.mark.integration``.

    Skips (never fails) when Postgres is down or the warehouse is empty: this
    fixture asserts on a pipeline run that must have happened first
    (``make pipeline UF=RR``).
    """
    try:
        database = Database.connect(settings.database_url)
    except psycopg.Error as exc:
        pytest.skip(
            f"real database unreachable at {settings.database_url} "
            f"({exc.__class__.__name__}). Run `docker compose up -d`."
        )
    try:
        loaded = database.scalar("SELECT count(*) FROM fact_votes")
    except psycopg.Error:
        loaded = 0
    if not loaded:
        database.close()
        pytest.skip(
            "the real warehouse is empty -- run `make pipeline UF=RR` first."
        )
    try:
        yield database
    finally:
        database.close()
