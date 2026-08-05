"""Shared test helpers.

Fixture files live in ``data/fixtures/`` at the repo root (NOT under
``pipeline/``) so the committed TSE excerpts sit next to ``data/raw/``.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import random
from collections.abc import Callable
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
from observatory.export import aggregates, manifest, shards

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
        cand_uf: str | None = None,
    ) -> None:
        """``uf`` keys the SECTION; ``cand_uf`` keys the CANDIDATE.

        They differ for national races: ``dim_candidate`` holds presidential
        candidates under the pseudo-UF ``BR`` while their sections belong to a
        real state.  Defaults to ``uf``, which is right for every state race.
        """
        sid = self.section(mun, zone, section, uf)
        party = int(str(number)[:2])
        cid = self.candidate(race, rnd, number, name, uf=cand_uf or uf, party=party)
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
        urna_model: str | None = None,
        uf: str = "RR",
    ) -> None:
        """One ``fact_section_stats`` row.

        ``urna_model`` is TSE's raw ``CD_MODELO_URNA`` (A6), per (section,
        race, round) because the code is not stable across rounds.  ``None``
        is the honest default: most fixtures say nothing about urnas.
        """
        sid = self.section(mun, zone, section, uf)
        self.db.execute(
            "INSERT INTO fact_section_stats (section_id, race, round, aptos, "
            "comparecimento, abstentions, blank, null_votes, urna_model, "
            "election_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (sid, race, rnd, aptos, comparecimento, abstentions, blank,
             null_votes, urna_model, _ELECTION_STATE_R1),
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


    # -- analysis outputs (Task 6+; read back by the aggregates export) ----

    def score(
        self,
        mun: str,
        zone: int,
        section: int,
        race: str,
        rnd: int,
        detector: str,
        score: float,
        detail: dict | None = None,
        *,
        uf: str = "RR",
    ) -> None:
        sid = self.section(mun, zone, section, uf)
        self.db.execute(
            "INSERT INTO analysis.section_scores "
            "(section_id, race, round, detector, score, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, race, rnd, detector, score,
             None if detail is None else json.dumps(detail)),
        )

    def detector_summary(
        self,
        detector: str,
        *,
        scored: int,
        flagged: int,
        params: dict | None = None,
        summary: dict | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO analysis.detector_summary "
            "(detector, sections_scored, sections_flagged, params, summary, at) "
            "VALUES (%s, %s, %s, %s, %s, now())",
            (detector, scored, flagged, json.dumps(params or {}),
             json.dumps(summary or {})),
        )

    def state_payload(self, detector: str, uf: str, payload: dict) -> None:
        self.db.execute(
            "INSERT INTO analysis.state_payloads (detector, uf, payload) "
            "VALUES (%s, %s, %s)",
            (detector, uf, json.dumps(payload)),
        )

    def report_row(
        self,
        mun: str,
        race: str,
        rnd: int,
        key: str,
        ours: int,
        *,
        category: str = "annulled_candidacy",
    ) -> None:
        self.municipality(mun)
        self.db.execute(
            "INSERT INTO reconciliation_report "
            "(mun_code, race, round, category, key, ours, theirs, diff) "
            "VALUES (%s, %s, %s, %s, %s, %s, 0, %s)",
            (mun, race, rnd, category, key, ours, ours),
        )


BOA_VISTA = "03018"
IRACEMA = "03026"
SAO_LUIZ = "03158"


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


# ==========================================================================
# Detector fixtures (Task 6)
#
# ``seed_sections`` builds the smallest warehouse a Tier 1 detector can read:
# one section per spec, in one UF/zone/round, with both section-level sources
# populated consistently (votação votável rows + the detalhe stats row).  Two
# spec shapes, because Tier 1 asks two unrelated questions:
#
#   dict(comp=, blank=, null=, nominal=, legenda=, aptos=)  -> arithmetic
#   dict(races=["PRES", "GOV"])                             -> which races the
#                                                              section reports
#
# They compose: a ``races`` spec seeds each of its races with the clean
# arithmetic defaults, so a presence fixture never accidentally carries an
# arithmetic violation (and vice versa).  Vote rows are seeded SPARSELY -- a
# zero term gets no row at all, which is how TSE publishes and therefore the
# only faithful way to test the sums.
# ==========================================================================

#: A section that adds up: 90 + 0 + 5 + 5 == 100 <= 120.
_CLEAN_SECTION = dict(comp=100, blank=5, null=5, nominal=90, legenda=0, aptos=120)

#: One plausible candidate number per race (the party is its 2-digit prefix).
_RACE_CANDIDATE: dict[str, int] = {
    "PRES": 13,
    "GOV": 15,
    "SEN": 130,
    "DEPFED": 1300,
    "DEPEST": 13000,
    "DEPDIS": 13001,
}


@pytest.fixture
def seed_sections():
    """Seed one section per spec.  Returns the :class:`Warehouse` used.

    ``seed_sections(db, [dict(...), dict(...)], uf="RR", round=1)`` -- sections
    are numbered 1..n inside one municipality and zone, so specs in the same
    call share a (uf, round) and are directly comparable, which is exactly
    what the cross-race matrix is derived over.
    """

    def _seed(
        db: Database,
        specs: list[dict],
        *,
        uf: str = "RR",
        mun: str = BOA_VISTA,
        zone: int = 1,
        race: str = "PRES",
        round: int = 1,
        first_section: int = 1,
    ) -> Warehouse:
        w = Warehouse(db)
        for offset, spec in enumerate(specs):
            section = first_section + offset
            races = spec.get("races", [race])
            counts = {k: v for k, v in spec.items() if k != "races"}
            for one in races:
                _seed_race(w, mun, zone, section, one, round, uf,
                           {**_CLEAN_SECTION, **counts})
        return w

    return _seed


def _seed_race(
    w: Warehouse,
    mun: str,
    zone: int,
    section: int,
    race: str,
    rnd: int,
    uf: str,
    c: dict,
) -> None:
    """One (section, race, round): its votável rows and its stats row."""
    number = _RACE_CANDIDATE[race]
    w.municipality(mun, uf=uf)
    w.section(mun, zone, section, uf)   # before any votável row, so uf sticks
    if c["nominal"]:
        w.cand_votes(mun, zone, section, race, rnd, number,
                     c["nominal"], name=f"CAND {race}", uf=uf)
    if c["legenda"]:
        w.other_votes(mun, zone, section, race, rnd, "legenda", c["legenda"],
                      party=int(str(number)[:2]))
    for kind, key in (("blank", "blank"), ("null", "null")):
        if c[key]:
            w.other_votes(mun, zone, section, race, rnd, kind, c[key])
    w.stats(mun, zone, section, race, rnd, aptos=c["aptos"],
            comparecimento=c["comp"], abstentions=c["aptos"] - c["comp"],
            blank=c["blank"], null_votes=c["null"])


# ==========================================================================
# Presidential vote fixtures (Task 7 -- extreme dominance)
#
# ``seed_votes`` builds a PRES round-1 warehouse with a controllable
# municipal vote distribution.  What the beta-binomial detector reads is
# exactly two things -- a section's nominal total ``n`` and one candidate's
# ``k`` -- so the fixture's whole job is to make the section-to-section
# SPREAD of k/n what the test says it is:
#
#   baseline_share=0.55       every section drawn Binomial(n, 0.55): no real
#                             overdispersion, so rho lands near its floor and
#                             a planted shutout is astronomically improbable
#   share_range=(0.35, 0.75)  the leader's share drawn uniformly per section:
#                             genuine overdispersion, which a correct detector
#                             absorbs into rho instead of flagging
#   planted=(k, n)            one extra section with exactly that result
#   with_state_context=True   more municipalities, all of them too small for
#                             their own baseline, so every section falls back
#                             to the state one
#
# Three candidates, not two.  With two, the loser is the exact mirror of the
# winner (k -> n-k, p -> 1-p), so a shutout produces two IDENTICAL tail
# probabilities and "which candidate does detail describe" has no answer.
# Three makes the ranking a real question with a checkable answer.
#
# Rows are seeded SPARSELY -- a candidate with zero votes in a section gets no
# row, which is how TSE publishes and therefore the only faithful way to test
# that the detector densifies.
# ==========================================================================

#: Deterministic by contract: the acceptance assertions are numeric.
SEED_VOTES_RNG_SEED = 20221002

#: (number, name).  The first is the leader whose share the fixture controls;
#: the rest split the remainder in the fixed proportions below.
_PRES_CANDIDATES: tuple[tuple[int, str], ...] = (
    (13, "LUIZ INÁCIO LULA DA SILVA"),
    (22, "JAIR MESSIAS BOLSONARO"),
    (12, "CIRO FERREIRA GOMES"),
)

#: How the non-leader vote splits between candidates 2 and 3.
_RUNNER_UP_SHARE = 0.7

#: Municipalities added by ``with_state_context`` -- enough of them that the
#: STATE has a baseline, each small enough that none of them has its own.
_CONTEXT_MUNICIPALITIES = 8


@pytest.fixture
def seed_votes():
    """Seed one presidential round into ``test_db``.  Returns the Warehouse."""

    def _seed(
        db: Database,
        *,
        baseline_share: float = 0.55,
        share_range: tuple[float, float] | None = None,
        sections: int = 40,
        n: int = 300,
        planted: tuple[int, int] | None = None,
        with_state_context: bool = False,
        uf: str = "RR",
        zone: int = 1,
        round: int = 1,
        seed: int = SEED_VOTES_RNG_SEED,
    ) -> Warehouse:
        import numpy as np

        rng = np.random.default_rng(seed)
        w = Warehouse(db)

        municipalities = [(BOA_VISTA, "BOA VISTA")]
        if with_state_context:
            municipalities += [
                (f"{3100 + i:05d}", f"CONTEXT {i}") for i in range(_CONTEXT_MUNICIPALITIES)
            ]

        for mun, mun_name in municipalities:
            w.municipality(mun, name=mun_name, uf=uf)
            for section in range(1, sections + 1):
                share = (
                    float(rng.uniform(*share_range))
                    if share_range is not None
                    else baseline_share
                )
                _seed_pres_section(w, mun, zone, section, round, uf, n,
                                   _draw(rng, n, share))

        if planted is not None:
            k, total = planted
            _seed_pres_section(w, BOA_VISTA, zone, sections + 1, round, uf, total,
                               (k, total - k, 0))
        return w

    return _seed


def _draw(rng, n: int, share: float) -> tuple[int, int, int]:
    """One section's votes for the three candidates, summing to ``n``."""
    lead = int(rng.binomial(n, share))
    rest = n - lead
    second = int(rng.binomial(rest, _RUNNER_UP_SHARE))
    return lead, second, rest - second


def _seed_pres_section(
    w: Warehouse,
    mun: str,
    zone: int,
    section: int,
    rnd: int,
    uf: str,
    n: int,
    votes: tuple[int, ...],
) -> None:
    """One (section, PRES, round): a votável row per candidate with votes.

    The section is created even when every candidate is zero, so a section that
    exists but reports nothing still reaches the detector (which then declines
    to score it -- no denominator, no question).
    """
    w.section(mun, zone, section, uf)
    for (number, name), k in zip(_PRES_CANDIDATES, votes, strict=True):
        if k:
            # PRES candidates live under the pseudo-UF 'BR'; their sections do not.
            w.cand_votes(mun, zone, section, "PRES", rnd, number, k,
                         name=name, uf=uf, cand_uf="BR")


# ==========================================================================
# Split-ticket fixtures (Task 13)
#
# ``seed_split`` builds ONE municipality that reports both a presidential
# round 1 and a federal-deputy round 1, with the two quantities the detector
# subtracts under direct control:
#
#   pres      the leading presidential candidate's share of PRES nominal votes
#   dep       his coalition's share of ALL federal-deputy votes in the section
#   noise     half-width of the per-section, per-quantity uniform jitter
#   planted   one EXTRA section with exact (pres, dep), no jitter -- the single
#             divergent section
#
# The jitter is uniform rather than Gaussian on purpose.  What the first test
# asserts is a bound on the largest robust z in the municipality, and a
# Gaussian's largest of 100 draws is a tail question whose answer moves with
# the seed; a uniform's is bounded by construction (max |z| = 2a / (1.4826 x
# MAD of a triangular) ~ 2.3, i.e. score ~ 1.15), so the assertion is about the
# detector rather than about which random numbers came out.
#
# The deputy vote is spread over SEVERAL real coalition parties and includes a
# ``legenda`` row, because summing exactly those is the thing that can be got
# wrong; a party in NEITHER coalition takes a fixed slice so the two coalition
# shares do not sum to one (which would make d for one candidate the exact
# negation of the other's and hide a sign error).  Rows are seeded SPARSELY --
# a zero term gets no row at all, which is how TSE publishes.
# ==========================================================================

#: Deterministic by contract: the acceptance assertions are numeric.
SEED_SPLIT_RNG_SEED = 20221002

#: The two presidential candidates.  The FIRST is the one ``pres``/``dep``
#: describe; the second takes the remainder of the presidential vote.
_SPLIT_PRES: tuple[tuple[int, str], ...] = (
    (13, "LUIZ INÁCIO LULA DA SILVA"),
    (22, "JAIR MESSIAS BOLSONARO"),
)

#: How each coalition's deputy vote is spread: (party number, kind, weight).
#: Party numbers are real members of the 2022 coalitions (see
#: ``observatory.coalitions``): 13/65/43 are FE BRASIL, 22/11/10 are Pelo Bem
#: do Brasil.  The last weight in each list absorbs the rounding remainder.
_SPLIT_DEP_LEAD = ((13, "candidate", 0.60), (65, "legenda", 0.25), (43, "candidate", 0.15))
_SPLIT_DEP_OTHER = ((22, "candidate", 0.55), (11, "candidate", 0.30), (10, "legenda", 0.15))

#: PDT -- in neither coalition.  It absorbs whatever the two coalitions leave,
#: which is what lets the SECOND coalition's deputy share stay put while the
#: first one's moves: a section where the leader takes 98 % of the presidential
#: vote but his deputies take 10 % is a section whose deputy votes went to
#: nobody in particular, not a section that handed them to the opposition.
#: Without that, ``d`` for one candidate would be almost exactly the negation
#: of the other's, the two |z| would tie to within rounding, and which
#: candidate ``detail`` describes would be a coin flip.
_SPLIT_DEP_NEUTRAL = 12

#: The second coalition's deputy share -- fixed across sections (jittered like
#: everything else), independent of ``dep``.
_SPLIT_OTHER_DEP_SHARE = 0.35

#: Votes cast per section.  Large enough that integer rounding (1/600) is a
#: small fraction of the smallest jitter the tests use (0.02).
_SPLIT_PRES_VOTERS = 600
_SPLIT_DEP_VOTERS = 650


@pytest.fixture
def seed_split():
    """Seed one split-ticket municipality into ``test_db``.  Returns the Warehouse."""

    def _seed(
        db: Database,
        *,
        pres: float,
        dep: float,
        sections: int = 50,
        noise: float = 0.0,
        planted: dict | None = None,
        uf: str = "RR",
        mun: str = BOA_VISTA,
        zone: int = 1,
        seed: int = SEED_SPLIT_RNG_SEED,
    ) -> Warehouse:
        import numpy as np

        rng = np.random.default_rng(seed)
        w = Warehouse(db)
        with db.transaction():
            w.municipality(mun, uf=uf)
            for section in range(1, sections + 1):
                _seed_split_section(
                    w, mun, zone, section, uf,
                    pres_share=pres + float(rng.uniform(-noise, noise)),
                    dep_share=dep + float(rng.uniform(-noise, noise)),
                    other_dep_share=(
                        _SPLIT_OTHER_DEP_SHARE + float(rng.uniform(-noise, noise))
                    ),
                )
            if planted is not None:
                # Exact, unjittered: the divergence is the fixture's statement,
                # not a draw that happened to land far out.  The OTHER
                # coalition keeps its usual deputy share, so the planted
                # section's largest |z| is unambiguously the leader's.
                _seed_split_section(
                    w, mun, zone, sections + 1, uf,
                    pres_share=float(planted["pres"]), dep_share=float(planted["dep"]),
                    other_dep_share=_SPLIT_OTHER_DEP_SHARE,
                )
        return w

    return _seed


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _seed_split_section(
    w: Warehouse,
    mun: str,
    zone: int,
    section: int,
    uf: str,
    *,
    pres_share: float,
    dep_share: float,
    other_dep_share: float,
) -> None:
    """One section's PRES round-1 and DEPFED round-1 votável rows."""
    w.section(mun, zone, section, uf)

    lead = int(round(_SPLIT_PRES_VOTERS * _clamp(pres_share)))
    for (number, name), votes in zip(
        _SPLIT_PRES, (lead, _SPLIT_PRES_VOTERS - lead), strict=True
    ):
        if votes:
            # PRES candidates live under the pseudo-UF 'BR'; their sections do not.
            w.cand_votes(mun, zone, section, "PRES", 1, number, votes,
                         name=name, uf=uf, cand_uf="BR")

    lead_share = _clamp(dep_share)
    other_share = _clamp(other_dep_share, 0.0, 1.0 - lead_share)
    lead_votes = int(round(_SPLIT_DEP_VOTERS * lead_share))
    other_votes = int(round(_SPLIT_DEP_VOTERS * other_share))
    _seed_split_deputies(w, mun, zone, section, uf, lead_votes, _SPLIT_DEP_LEAD)
    _seed_split_deputies(w, mun, zone, section, uf, other_votes, _SPLIT_DEP_OTHER)

    neutral = _SPLIT_DEP_VOTERS - lead_votes - other_votes
    if neutral > 0:
        w.cand_votes(mun, zone, section, "DEPFED", 1, _SPLIT_DEP_NEUTRAL * 100 + 1,
                     neutral, name=f"DEPUTADO {_SPLIT_DEP_NEUTRAL}", uf=uf)


def _seed_split_deputies(
    w: Warehouse,
    mun: str,
    zone: int,
    section: int,
    uf: str,
    total: int,
    spread: tuple[tuple[int, str, float], ...],
) -> None:
    """``total`` deputy votes split across one coalition's parties.

    The last party absorbs the remainder, so the block sums to ``total``
    exactly and the shares the test asks for are the shares the detector sees.
    """
    remaining = total
    for index, (party, kind, weight) in enumerate(spread):
        last = index == len(spread) - 1
        votes = remaining if last else min(remaining, int(round(total * weight)))
        remaining -= votes
        if not votes:
            continue                      # sparse, like TSE: no row for a zero
        if kind == "legenda":
            w.other_votes(mun, zone, section, "DEPFED", 1, "legenda", votes,
                          party=party)
        else:
            w.cand_votes(mun, zone, section, "DEPFED", 1, party * 100 + 1, votes,
                         name=f"DEPUTADO {party}", uf=uf)


# ==========================================================================
# Turnout fixtures (Task 14)
#
# ``seed_turnout`` builds a PRES round-1 warehouse whose only interesting
# content is ``fact_section_stats``: the detector reads nothing but a section's
# ``aptos``/``comparecimento`` pair, so the fixture's whole job is to control
# the section-to-section SPREAD of t = comparecimento / aptos.
#
#   base=0.80, noise=0.02   every section drawn uniformly in 0.80 +/- 0.02, so
#                           the municipality has a small but real scale
#                           (MAD ~ 0.01) to be an outlier against
#   constant=0.80           every section EXACTLY 0.80 -- MAD is 0, which is no
#                           scale at all and must send the detector to the state
#   planted=[0.999, 0.35]   extra sections at exactly those turnouts, one far
#                           above and one far below: both signs, one fixture
#   with_state_context=True more municipalities, spread widely, so the STATE
#                           distribution the fallback lands on is non-degenerate
#
# The jitter is uniform rather than Gaussian for the same reason as
# ``seed_split``: the assertions are about the detector, not about which random
# numbers came out.  ``aptos`` is large enough (2 000) that integer rounding of
# comparecimento (1/2 000) is a twentieth of the smallest MAD the tests use.
# ==========================================================================

#: Deterministic by contract: the acceptance assertions are numeric.
SEED_TURNOUT_RNG_SEED = 20221002

#: Registered voters per section.  See the note above on rounding.
_TURNOUT_APTOS = 2_000

#: Half-width of the context municipalities' turnout spread.  Wide enough that
#: the STATE's MAD is unmistakably non-zero even when the municipality under
#: test is perfectly constant -- a state fallback onto a degenerate state
#: distribution would be a fallback in name only.
_TURNOUT_CONTEXT_SPREAD = 0.10


@pytest.fixture
def seed_turnout():
    """Seed one presidential round's turnout into ``test_db``.

    Returns the :class:`Warehouse` used.  ``constant`` is ``base`` with the
    noise switched off, spelled separately because a test that wants a zero MAD
    is making a statement, not passing ``noise=0``.
    """

    def _seed(
        db: Database,
        *,
        base: float = 0.80,
        noise: float = 0.0,
        constant: float | None = None,
        sections: int = 40,
        planted: list[float] | tuple[float, ...] | float | None = None,
        with_state_context: bool = False,
        aptos: int = _TURNOUT_APTOS,
        uf: str = "RR",
        mun: str = BOA_VISTA,
        zone: int = 1,
        round: int = 1,
        seed: int = SEED_TURNOUT_RNG_SEED,
    ) -> Warehouse:
        import numpy as np

        if constant is not None:
            base, noise = float(constant), 0.0

        rng = np.random.default_rng(seed)
        w = Warehouse(db)
        with db.transaction():
            w.municipality(mun, uf=uf)
            for section in range(1, sections + 1):
                _seed_turnout_section(
                    w, mun, zone, section, uf, round, aptos,
                    base + float(rng.uniform(-noise, noise)) if noise else base,
                )

            if planted is not None:
                # Exact, unjittered: the outlier is the fixture's statement, not
                # a draw that happened to land far out.
                values = [planted] if isinstance(planted, (int, float)) else list(planted)
                for offset, turnout in enumerate(values):
                    _seed_turnout_section(
                        w, mun, zone, sections + 1 + offset, uf, round, aptos,
                        float(turnout),
                    )

            if with_state_context:
                # Other municipalities of the same UF, spread widely, so the
                # state distribution has a scale of its own.
                for index in range(_CONTEXT_MUNICIPALITIES):
                    other = f"{3100 + index:05d}"
                    w.municipality(other, name=f"CONTEXT {index}", uf=uf)
                    for section in range(1, sections + 1):
                        _seed_turnout_section(
                            w, other, zone, section, uf, round, aptos,
                            base + float(rng.uniform(
                                -_TURNOUT_CONTEXT_SPREAD, _TURNOUT_CONTEXT_SPREAD
                            )),
                        )
        return w

    return _seed


def _seed_turnout_section(
    w: Warehouse,
    mun: str,
    zone: int,
    section: int,
    uf: str,
    rnd: int,
    aptos: int,
    turnout: float,
) -> None:
    """One (section, PRES, round) stats row with the requested turnout.

    Only ``fact_section_stats`` is seeded: the detector's whole input is
    ``comparecimento / aptos``, and seeding votável rows as well would add
    numbers no assertion depends on.
    """
    w.section(mun, zone, section, uf)
    comparecimento = int(round(aptos * _clamp(turnout)))
    w.stats(mun, zone, section, "PRES", rnd, aptos=aptos,
            comparecimento=comparecimento, abstentions=aptos - comparecimento,
            blank=0, null_votes=0)


# ==========================================================================
# Fingerprint fixtures (Task 15)
#
# ``seed_state`` builds ONE state's worth of presidential round-1 sections with
# both fingerprint coordinates under direct control:
#
#   turnout = comparecimento / aptos                drawn in _FP_TURNOUT
#   share   = winner's votes / valid (nominal)      drawn in _FP_SHARE
#
# The two draws are INDEPENDENT and both are bounded well away from the
# top-right corner (85 % / 70 % at the very most), so the un-planted population
# contributes exactly zero corner mass and the corner assertion cannot pass
# vacuously.
#
#   sections=500          the ordinary population, sections 1..500
#   corner_planted=50     50 EXTRA sections (501..550) at turnout > 95 % AND
#                         share > 95 %, i.e. the extreme-fraud cluster, seeded
#                         ON TOP OF the 500 -- 550 sections in total
#
# Every seeded section is observable by construction (aptos > 0 and at least
# one valid vote), which is what makes ``sum(grid) == n_sections`` a real
# assertion about the binning rather than about which sections survived.
#
# Blank and null are zero, so valid == comparecimento: the fixture's statement
# is about the JOINT distribution of two coordinates, and a third free
# parameter between them would only blur which one an assertion is about.  Vote
# rows are seeded SPARSELY -- a candidate with no votes gets no row, which is
# how TSE publishes.
# ==========================================================================

#: Deterministic by contract: the acceptance assertions are numeric.
SEED_STATE_RNG_SEED = 20221002

#: The two presidential candidates.  The FIRST wins nationally in every fixture
#: this seeds (his share never drops below 35 %), which is what the detector
#: must independently rediscover from the votes.
_FP_PRES: tuple[tuple[int, str], ...] = (
    (13, "LUIZ INÁCIO LULA DA SILVA"),
    (22, "JAIR MESSIAS BOLSONARO"),
)

#: Registered voters per section.  Large enough that integer rounding (1/2 000)
#: is far finer than the 1 % bins.
_FP_APTOS = 2_000

#: The ordinary population: nowhere near the > 90 % / > 90 % corner.
_FP_TURNOUT = (0.60, 0.85)
_FP_SHARE = (0.35, 0.70)

#: The planted extreme-fraud cluster: both coordinates above 95 %, as the
#: acceptance test's comment says.
_FP_CORNER = (0.955, 0.995)


@pytest.fixture
def seed_state():
    """Seed one UF's presidential round into ``test_db``.  Returns the Warehouse."""

    def _seed(
        db: Database,
        *,
        sections: int = 500,
        corner_planted: int = 0,
        uf: str = "RR",
        mun: str = BOA_VISTA,
        zone: int = 1,
        aptos: int = _FP_APTOS,
        round: int = 1,
        seed: int = SEED_STATE_RNG_SEED,
    ) -> Warehouse:
        import numpy as np

        rng = np.random.default_rng(seed)
        w = Warehouse(db)
        with db.transaction():
            w.municipality(mun, uf=uf)
            for section in range(1, sections + 1):
                _seed_fingerprint_section(
                    w, mun, zone, section, uf, round, aptos,
                    turnout=float(rng.uniform(*_FP_TURNOUT)),
                    share=float(rng.uniform(*_FP_SHARE)),
                )
            for offset in range(corner_planted):
                # On TOP of the ordinary population, never replacing part of
                # it: the corner mass the test asserts is planted/(base+planted).
                _seed_fingerprint_section(
                    w, mun, zone, sections + 1 + offset, uf, round, aptos,
                    turnout=float(rng.uniform(*_FP_CORNER)),
                    share=float(rng.uniform(*_FP_CORNER)),
                )
        return w

    return _seed


def _seed_fingerprint_section(
    w: Warehouse,
    mun: str,
    zone: int,
    section: int,
    uf: str,
    rnd: int,
    aptos: int,
    *,
    turnout: float,
    share: float,
) -> None:
    """One (section, PRES, round): its stats row and its two votável rows.

    Both sources are seeded, because the fingerprint's two axes come from
    different ones: turnout from ``fact_section_stats``, share from
    ``fact_votes``.  ``blank`` and ``null`` are 0, so every voter who turned up
    cast a valid vote and ``valid == comparecimento``.
    """
    w.section(mun, zone, section, uf)
    comparecimento = int(round(aptos * _clamp(turnout)))
    w.stats(mun, zone, section, "PRES", rnd, aptos=aptos,
            comparecimento=comparecimento, abstentions=aptos - comparecimento,
            blank=0, null_votes=0)

    lead = int(round(comparecimento * _clamp(share)))
    for (number, name), votes in zip(
        _FP_PRES, (lead, comparecimento - lead), strict=True
    ):
        if votes:                       # sparse, like TSE: no row for a zero
            # PRES candidates live under the pseudo-UF 'BR'; their sections do not.
            w.cand_votes(mun, zone, section, "PRES", rnd, number, votes,
                         name=name, uf=uf, cand_uf="BR")


# ==========================================================================
# Digit-test fixtures (Task 16)
#
# The digit detector reads ONE thing -- the multiset of section-level nominal
# presidential vote counts for the top two candidates, both rounds -- and asks
# two questions of their digits.  So these fixtures control nothing but the
# DISTRIBUTION OF THE COUNTS themselves:
#
#   seed_counts_benford            counts drawn log-uniformly over 10..10 000,
#                                  which is exactly the process whose
#                                  significand is log-uniform and whose second
#                                  digit therefore follows Benford
#   seed_counts_last_digit_biased  counts in 100..900 with 70 % of them rounded
#                                  to the nearest 5: the classic hand-made-
#                                  number signature, 0 and 5 far above 10 %
#   seed_counts_small              every count in 1..9, so NOTHING clears
#                                  either test's floor (10 and 100) and every
#                                  stratum must come back skipped
#
# All three seed a SINGLE municipality, so a fixture is a single stratum
# (state x size bucket) and an assertion over "every stratum" is an assertion
# about one population rather than about how a pooling rule happened to split
# it.  150 sections puts that municipality in the ``large`` bucket; the 40 of
# ``seed_counts_small`` put it in ``mid``, which is deliberate -- the skip must
# not depend on the bucket.
#
# 150 sections x 2 candidates x 2 rounds = 600 counts, comfortably past the
# 100-count eligibility floor for BOTH tests (the log-uniform draw leaves ~390
# counts >= 100).  The counts are drawn as one seeded array up front, so the
# p-values the acceptance test asserts on are fixed numbers and not a race
# between the RNG and the loop order.
# ==========================================================================

#: Deterministic by contract: the acceptance assertions are numeric.
SEED_COUNTS_RNG_SEED = 20221002

#: The two presidential candidates -- the detector's population is the top two
#: of each round nationally, and with exactly two seeded, both qualify.
_DIGIT_PRES: tuple[tuple[int, str], ...] = (
    (13, "LUIZ INÁCIO LULA DA SILVA"),
    (22, "JAIR MESSIAS BOLSONARO"),
)

#: Both rounds, as the detector's contract says.
_DIGIT_ROUNDS = (1, 2)

#: Sections in the one municipality -- >= 100, so it lands in ``large``.
_DIGIT_SECTIONS = 150

#: The log-uniform draw's support, in votes.  Several orders of magnitude is
#: what makes a significand log-uniform; a narrow range would not be Benford
#: no matter how honestly it was generated.
_DIGIT_BENFORD_RANGE = (10, 10_000)

#: The biased draw: every count clears the last-digit floor of 100, and this
#: share of them is rounded to a multiple of 5.
_DIGIT_BIASED_RANGE = (100, 900)
_DIGIT_ROUNDED_SHARE = 0.7


def _seed_digit_counts(
    db: Database,
    values: list[int],
    *,
    uf: str,
    mun: str,
    mun_name: str,
    zone: int,
    sections: int,
) -> Warehouse:
    """Lay ``values`` out over (section, round, candidate), in that order.

    One value per (section, round, candidate) triple, consumed in a fixed
    order, so the multiset the detector pools is exactly the array drawn above
    and the assertions are reproducible.  A zero count would get no row (TSE is
    sparse), but no fixture here draws one.
    """
    w = Warehouse(db)
    with db.transaction():
        w.municipality(mun, name=mun_name, uf=uf)
        taken = iter(values)
        for section in range(1, sections + 1):
            w.section(mun, zone, section, uf)
            for rnd in _DIGIT_ROUNDS:
                for number, name in _DIGIT_PRES:
                    votes = next(taken)
                    if votes:
                        # PRES candidates live under the pseudo-UF 'BR'.
                        w.cand_votes(mun, zone, section, "PRES", rnd, number,
                                     int(votes), name=name, uf=uf, cand_uf="BR")
    return w


def _digit_draw_count(sections: int) -> int:
    return sections * len(_DIGIT_ROUNDS) * len(_DIGIT_PRES)


@pytest.fixture
def seed_counts_benford(test_db: Database) -> Warehouse:
    """Counts from a genuinely Benford-conformant process: log-uniform.

    ``10 ** U(1, 4)`` floored to an integer -- the significand is log-uniform
    by construction, so the second digit follows Benford's second-digit law and
    the chi-square lands nowhere near significance (p ~ 0.70 at this seed).
    Every count is >= 10, so the whole population is eligible for the
    second-digit test and the stratum is tested rather than skipped.
    """
    import numpy as np

    rng = np.random.default_rng(SEED_COUNTS_RNG_SEED)
    low, high = _DIGIT_BENFORD_RANGE
    draws = rng.uniform(math.log10(low), math.log10(high),
                        _digit_draw_count(_DIGIT_SECTIONS))
    values = [int(v) for v in np.floor(10.0**draws)]
    return _seed_digit_counts(
        test_db, values, uf="RR", mun=BOA_VISTA, mun_name="BOA VISTA",
        zone=1, sections=_DIGIT_SECTIONS,
    )


@pytest.fixture
def seed_counts_last_digit_biased(test_db: Database) -> Warehouse:
    """Counts that end in 0 or 5 far more often than chance allows.

    Uniform over 100..899 -- so every count clears the last-digit floor -- with
    70 % of them rounded to the nearest multiple of 5.  Digits 0 and 5 come out
    at ~37 % each against a null of 10 %, which is a chi-square of ~1 100 on 9
    dof (p ~ 1e-230): unmistakable, and not a tail the fixture has to hope for.
    """
    import numpy as np

    rng = np.random.default_rng(SEED_COUNTS_RNG_SEED)
    n = _digit_draw_count(_DIGIT_SECTIONS)
    base = rng.integers(*_DIGIT_BIASED_RANGE, n)
    rounded = rng.random(n) < _DIGIT_ROUNDED_SHARE
    values = [
        int(round(v / 5) * 5) if flag else int(v)
        for v, flag in zip(base, rounded, strict=True)
    ]
    return _seed_digit_counts(
        test_db, values, uf="RR", mun=BOA_VISTA, mun_name="BOA VISTA",
        zone=1, sections=_DIGIT_SECTIONS,
    )


#: Sections for the sparse fixture: a ``mid``-bucket municipality, so the skip
#: is demonstrably about eligibility and not about the bucket.
_DIGIT_SMALL_SECTIONS = 40


@pytest.fixture
def seed_counts_small(test_db: Database) -> Warehouse:
    """Every count in 1..9 -- below BOTH floors, so nothing is ever eligible.

    The stratum still exists (the municipality has sections, and it was looked
    at), so the detector must emit it for each test as ``skipped`` with no
    p-value at all.  A detector that dropped it, or that reported NaN, fails
    the acceptance test in two different ways.
    """
    import numpy as np

    rng = np.random.default_rng(SEED_COUNTS_RNG_SEED)
    values = [int(v) for v in rng.integers(1, 10, _digit_draw_count(_DIGIT_SMALL_SECTIONS))]
    return _seed_digit_counts(
        test_db, values, uf="RR", mun=BOA_VISTA, mun_name="BOA VISTA",
        zone=1, sections=_DIGIT_SMALL_SECTIONS,
    )


# ==========================================================================
# Urna-model fixtures (Task 17)
#
# The urna-model detector reads three things per section -- which fleet it ran
# in ROUND 2, how many nominal presidential votes it cast, and how many were
# the subject's -- and asks one question: how much of the national pre2020 vs
# ue2020 gap survives conditioning on municipality.  So these fixtures control
# the JOINT arrangement of fleet and vote share, nothing else:
#
#   seed_two_towns      the geography artifact.  Two KINDS of town (subject
#                       share .70 / .30), the old fleet concentrated in the
#                       first and the new one in the second, but WITHIN every
#                       town the two fleets poll identically up to symmetric
#                       noise.  Raw gap ~ .24, honest gap ~ 0.
#   seed_model_effect   a REAL within-town gap of `effect` in favour of
#                       pre2020, present in every town, so the CI excludes 0.
#   seed_model_swap     sections that ran a pre2020 urna in round 1 and a
#                       ue2020 one in round 2 -- 1.1 % of real sections change
#                       code between rounds (A6), and the runoff's own code is
#                       the one that classifies.
#
# THE NOISE IS ANTITHETIC, NOT RANDOM-PER-TOWN.  ``seed_two_towns`` has to
# make two statements at once: the mean of delta_m is ~exactly 0, AND the
# spread of delta_m is not, because a cluster-robust CI around a mean of zero
# with zero spread is a zero-width interval that contains 0 only vacuously.
# Towns therefore come in pairs -- town 2i is seeded at +e, town 2i+1 at -e,
# with identical size and therefore identical weight -- so the weighted mean
# cancels to the last vote while every individual delta_m is far from 0.
#
# Every section casts exactly `_URNA_VOTERS` valid votes, so a town's weight is
# a function of its section count alone and the cancellation is exact rather
# than approximate.
# ==========================================================================

#: Deterministic by contract: the acceptance assertions are numeric.
SEED_URNA_RNG_SEED = 20221002

#: The runoff roster.  The SECOND is the subject the detector resolves
#: (``urna_model.SUBJECT_NUMBER`` -- number 22, Bolsonaro), and the fixtures'
#: "share" always means his.
_URNA_PRES: tuple[tuple[int, str], ...] = (
    (13, "LUIZ INÁCIO LULA DA SILVA"),
    (22, "JAIR MESSIAS BOLSONARO"),
)

#: Raw TSE ``CD_MODELO_URNA`` values, one per fleet.  Deliberately a real
#: pre-2020 generation code rather than the string ``pre2020``: what the
#: warehouse stores is TSE's code, and the mapping is the detector's job.
_URNA_PRE2020_CODE = "2013"
_URNA_UE2020_CODE = "2020"

#: Valid votes per section.  Large enough that integer rounding (1/500 = .002)
#: is far below the smallest gap any assertion turns on (.01).
_URNA_VOTERS = 500

#: Sections per town, and how the fleets split inside a mixed-kind town.
_URNA_TOWN_SECTIONS = 30
_URNA_MAJORITY = 24                      # 80 % of the town, 20 % the other way

#: Antithetic pairs per kind -> 2 kinds x 6 pairs x 2 towns = 24 towns, which
#: clears the acceptance test's ">= 20 clusters" with room to spare.
_URNA_PAIRS_PER_KIND = 6

#: The two kinds of town: (subject share, pre2020 sections out of 30).
_URNA_KINDS: tuple[tuple[float, int], ...] = (
    (0.70, _URNA_MAJORITY),                       # pro-subject, old fleet
    (0.30, _URNA_TOWN_SECTIONS - _URNA_MAJORITY),  # anti-subject, new fleet
)

#: Half-width of the antithetic within-town noise, as a share.  Bounded away
#: from 0 so the spread of delta_m -- and therefore the standard error -- is
#: unmistakably non-zero.
_URNA_NOISE = (0.01, 0.03)

#: Municipality-code bases, one per fixture, so no two fixtures could ever
#: collide on a code (they never run together, but a shared code would be a
#: silent way for that to stop being true).
_URNA_MUN_BASE = 3200
_URNA_EFFECT_MUN_BASE = 3300
_URNA_SWAP_MUN_BASE = 3400


def _urna_section(
    w: Warehouse,
    mun: str,
    zone: int,
    section: int,
    uf: str,
    rnd: int,
    *,
    share: float,
    model: str | None,
    voters: int = _URNA_VOTERS,
    votes: bool = True,
) -> None:
    """One (section, PRES, round): its stats row (carrying the urna model) and,
    unless ``votes`` is False, the two candidates' votável rows.

    ``votes=False`` seeds the model for a round the detector does not read --
    which is what makes ``seed_model_swap``'s round-1 claim true without
    inventing a round-1 election nobody asserts on.
    """
    w.section(mun, zone, section, uf)
    subject = int(round(voters * _clamp(share)))
    w.stats(mun, zone, section, "PRES", rnd, aptos=voters + 40,
            comparecimento=voters, abstentions=40, blank=0, null_votes=0,
            urna_model=model, uf=uf)
    if not votes:
        return
    for (number, name), count in zip(
        _URNA_PRES, (voters - subject, subject), strict=True
    ):
        if count:                       # sparse, like TSE: no row for a zero
            # PRES candidates live under the pseudo-UF 'BR'; their sections do not.
            w.cand_votes(mun, zone, section, "PRES", rnd, number, count,
                         name=name, uf=uf, cand_uf="BR")


def _urna_town(
    w: Warehouse,
    mun: str,
    name: str,
    uf: str,
    zone: int,
    *,
    sections: int,
    pre_sections: int,
    pre_share: float,
    ue_share: float,
) -> None:
    """One municipality: ``pre_sections`` on the old fleet, the rest on the new.

    Both fleets are given a share of their own, so ``delta_m`` for the town is
    ``pre_share - ue_share`` up to integer rounding -- the fixture states the
    within-town gap directly instead of hoping a draw produces one.
    """
    w.municipality(mun, name=name, uf=uf)
    for section in range(1, sections + 1):
        old = section <= pre_sections
        _urna_section(
            w, mun, zone, section, uf, 2,
            share=pre_share if old else ue_share,
            model=_URNA_PRE2020_CODE if old else _URNA_UE2020_CODE,
        )


@pytest.fixture
def seed_two_towns():
    """Seed the geography artifact: fleet sorted BY town, not within one.

    24 towns, 12 of each kind, in antithetic pairs (see the section note).
    Nationally the old fleet's pooled share is .62 against the new fleet's .38
    -- a raw gap of .24 that is entirely geography -- while every town polls
    its two fleets the same up to +/- e, so the vote-weighted mean of delta_m
    is 0 to the last vote and its cluster-robust CI straddles it.
    """

    def _seed(
        db: Database,
        *,
        uf: str = "RR",
        zone: int = 1,
        seed: int = SEED_URNA_RNG_SEED,
    ) -> Warehouse:
        import numpy as np

        rng = np.random.default_rng(seed)
        w = Warehouse(db)
        index = 0
        with db.transaction():
            for kind, (share, pre_sections) in enumerate(_URNA_KINDS):
                for _pair in range(_URNA_PAIRS_PER_KIND):
                    noise = float(rng.uniform(*_URNA_NOISE))
                    # The pair: +e then -e, same size, so the weighted mean of
                    # their two delta_m is exactly 0 -- and neither is.
                    for sign in (1.0, -1.0):
                        mun = f"{_URNA_MUN_BASE + index:05d}"
                        _urna_town(
                            w, mun, f"TOWN {'AB'[kind]}{index}", uf, zone,
                            sections=_URNA_TOWN_SECTIONS,
                            pre_sections=pre_sections,
                            pre_share=share + sign * noise,
                            ue_share=share - sign * noise,
                        )
                        index += 1
        return w

    return _seed


#: Towns the planted-effect fixture seeds, and how their sections split.  An
#: even split, so neither fleet's share is measured over a handful of sections.
_URNA_EFFECT_TOWNS = 20
_URNA_EFFECT_PRE_SECTIONS = _URNA_TOWN_SECTIONS // 2

#: Where the towns themselves sit politically.  Wide, and independent of the
#: planted effect: a detector that recovered the effect only because every town
#: polled the same would be recovering the fixture, not the effect.
_URNA_EFFECT_BASE = (0.35, 0.65)

#: Half-width of the per-town wobble around the planted effect.  Small enough
#: that the 95 % CI stays clear of 0, large enough that the standard error is
#: real rather than a rounding artefact.
_URNA_EFFECT_NOISE = 0.006


@pytest.fixture
def seed_model_effect():
    """Seed a REAL within-town gap: every town favours pre2020 by ``effect``.

    20 towns, each polling somewhere in .35-.65 overall, each with its old
    fleet running ``effect`` (+/- a small wobble) ahead of its new one.  The
    vote-weighted mean of delta_m therefore lands on ``effect`` and the
    cluster-robust CI is nowhere near 0 -- the mirror image of
    ``seed_two_towns``.
    """

    def _seed(
        db: Database,
        *,
        effect: float = 0.05,
        towns: int = _URNA_EFFECT_TOWNS,
        uf: str = "RR",
        zone: int = 1,
        seed: int = SEED_URNA_RNG_SEED,
    ) -> Warehouse:
        import numpy as np

        rng = np.random.default_rng(seed)
        w = Warehouse(db)
        with db.transaction():
            for index in range(towns):
                base = float(rng.uniform(*_URNA_EFFECT_BASE))
                gap = effect + float(
                    rng.uniform(-_URNA_EFFECT_NOISE, _URNA_EFFECT_NOISE)
                )
                _urna_town(
                    w, f"{_URNA_EFFECT_MUN_BASE + index:05d}", f"TOWN {index}",
                    uf, zone,
                    sections=_URNA_TOWN_SECTIONS,
                    pre_sections=_URNA_EFFECT_PRE_SECTIONS,
                    pre_share=base + gap / 2.0,
                    ue_share=base - gap / 2.0,
                )
        return w

    return _seed


#: Towns the swap fixture spreads its swapped sections over.  More than one, so
#: the swapped sections are not one municipality's peculiarity.
_URNA_SWAP_TOWNS = 2

#: Sections per town that keep the OLD fleet in both rounds.  They are what
#: makes each town mixed once the swapped ones move, so the detector has real
#: municipalities to compare rather than a country of single-fleet towns.
_URNA_SWAP_STABLE = 25

#: The swapped sections' shares.  A modest, honest gap: this fixture's
#: assertion is about WHICH ROUND classifies, not about the size of anything.
_URNA_SWAP_PRE_SHARE = 0.52
_URNA_SWAP_UE_SHARE = 0.48


@pytest.fixture
def seed_model_swap():
    """Seed sections that change fleet BETWEEN ROUNDS: pre2020 in r1, ue2020 in r2.

    Every section here reports a pre-2020 code in round 1.  ``swapped`` of them
    report the 2020 code in round 2, spread over two towns; the rest keep the
    old one.  A detector that classified on round 1 would therefore see zero
    ue2020 sections, which is precisely what the acceptance test forbids.

    Round 1 is seeded as stats rows only -- the model lives there, and a
    round-1 election nobody asserts on would only add numbers.
    """

    def _seed(
        db: Database,
        *,
        swapped: int = 30,
        towns: int = _URNA_SWAP_TOWNS,
        stable: int = _URNA_SWAP_STABLE,
        uf: str = "RR",
        zone: int = 1,
    ) -> Warehouse:
        w = Warehouse(db)
        per_town = -(-int(swapped) // int(towns))          # ceil, so >= swapped
        with db.transaction():
            for index in range(towns):
                mun = f"{_URNA_SWAP_MUN_BASE + index:05d}"
                w.municipality(mun, name=f"SWAP TOWN {index}", uf=uf)
                for section in range(1, stable + per_town + 1):
                    swaps = section > stable
                    # Round 1: the whole town ran the old fleet.
                    _urna_section(
                        w, mun, zone, section, uf, 1,
                        share=_URNA_SWAP_PRE_SHARE,
                        model=_URNA_PRE2020_CODE, votes=False,
                    )
                    # Round 2: the swapped ones came back on a 2020 urna.
                    _urna_section(
                        w, mun, zone, section, uf, 2,
                        share=_URNA_SWAP_UE_SHARE if swaps else _URNA_SWAP_PRE_SHARE,
                        model=_URNA_UE2020_CODE if swaps else _URNA_PRE2020_CODE,
                    )
        return w

    return _seed


# ==========================================================================
# Composite fixtures (Task 18)
#
# The composite reads NOTHING but ``analysis.section_scores``: it is a function
# of other detectors' output, not of votes.  So these three fixtures seed score
# rows directly -- running five real detectors to produce a score vector would
# make every assertion here an assertion about those five detectors instead.
#
#   seed_multi_detector   three contributors over twelve sections, with the
#                         score vectors written down, so the top composite row
#                         is a known section carrying three known percentiles
#   seed_single_detector  one contributor, twice over: one detector in round 1
#                         and one (different) in round 2, so "fewer than two"
#                         is demonstrably a rule about a (section, round) and
#                         not about the warehouse as a whole
#   seed_zones            50 zones x 40 sections = 2 000, with the highs placed
#                         by hand -- see its own note
#
# NOTHING here is random.  The other detector fixtures seed an RNG because they
# are shaping a *distribution*; what these shape is a *ranking*, and a ranking
# that depended on a draw would make the acceptance assertions depend on it too.
# ==========================================================================

#: The three contributors ``seed_multi_detector`` supplies, with the race and
#: round each is read under (see ``detectors/composite.py``'s CONTRIBUTORS).
_MULTI_ARITHMETIC = ("t1_arithmetic", "PRES")
_MULTI_PRESENCE = ("t1_presence", "ALL")
_MULTI_TURNOUT = ("t2_turnout", "PRES")

#: Sections 1..10, one entry each.  Chosen -- not drawn -- so that after
#: mean-rank ranking over the 11 rows each contributor ends up with, section 10
#: comes out on top of the composite while holding THREE DIFFERENT percentiles
#: (1.0, 10/11, 8/11 -> composite 0.878788).  That matters: were its three
#: percentiles equal, an implementation returning their max, or their median,
#: would satisfy the acceptance assertion by accident.
_MULTI_SCORES: dict[str, tuple[float, ...]] = {
    "t1_arithmetic": (0, 0, 0, 0, 0, 0, 1, 1, 2, 4),
    "t1_presence": (0, 0, 3, 0, 0, 0, 0, 1, 0, 2),
    "t2_turnout": (0.2, 0.4, 0.6, 0.8, 2.6, 1.0, 1.2, 1.8, 2.2, 1.4),
}

#: Section 11 is scored by ONE contributor and section 12 by two: the first
#: must get no composite row at all, the second a row whose ``percentiles``
#: object has a different key set from every other row's.  That difference is
#: the point -- it is what a struct-schema-inferring reader would silently
#: flatten, and ``n_detectors == len(percentiles)`` is the assertion that
#: catches it.
_MULTI_TURNOUT_ONLY_SECTION = 11
_MULTI_TURNOUT_ONLY_SCORE = 0.5
_MULTI_TWO_CONTRIBUTOR_SECTION = 12


@pytest.fixture
def seed_multi_detector():
    """Seed three contributors' section scores.  Returns the Warehouse."""

    def _seed(
        db: Database,
        *,
        uf: str = "RR",
        mun: str = BOA_VISTA,
        zone: int = 1,
        round: int = 1,
    ) -> Warehouse:
        w = Warehouse(db)
        with db.transaction():
            w.municipality(mun, uf=uf)
            for detector, race in (_MULTI_ARITHMETIC, _MULTI_PRESENCE, _MULTI_TURNOUT):
                for offset, score in enumerate(_MULTI_SCORES[detector]):
                    w.score(mun, zone, offset + 1, race, round, detector,
                            float(score), uf=uf)
                # ... and the two-contributor section, for everything but turnout.
                if detector != _MULTI_TURNOUT[0]:
                    w.score(mun, zone, _MULTI_TWO_CONTRIBUTOR_SECTION, race, round,
                            detector, 0.0, uf=uf)
            w.score(mun, zone, _MULTI_TURNOUT_ONLY_SECTION, _MULTI_TURNOUT[1],
                    round, _MULTI_TURNOUT[0], _MULTI_TURNOUT_ONLY_SCORE, uf=uf)
        return w

    return _seed


@pytest.fixture
def seed_single_detector():
    """Seed exactly ONE contributor per (section, round).  Returns the Warehouse.

    Turnout in round 1 for ten sections, and -- for one of them -- arithmetic in
    round 2.  Neither round can reach two contributors, so a detector that
    counted contributors globally, or that pooled the rounds, would write rows
    where the contract says there are none.
    """

    def _seed(
        db: Database,
        *,
        sections: int = 10,
        uf: str = "RR",
        mun: str = BOA_VISTA,
        zone: int = 1,
    ) -> Warehouse:
        w = Warehouse(db)
        with db.transaction():
            w.municipality(mun, uf=uf)
            for section in range(1, sections + 1):
                w.score(mun, zone, section, _MULTI_TURNOUT[1], 1, _MULTI_TURNOUT[0],
                        section / 10.0, uf=uf)
            w.score(mun, zone, 1, _MULTI_ARITHMETIC[1], 2, _MULTI_ARITHMETIC[0],
                    1.0, uf=uf)
        return w

    return _seed


# --------------------------------------------------------------------------
# ``seed_zones`` -- the clustering fixture
#
# 50 zones x 40 sections = 2 000 sections in ONE municipality, so the national
# high set is exactly ceil(0.01 x 2 000) = 20 sections and the MUNICIPALITY
# level of the same test is trivially unsurprising (one municipality holding
# all 20 of 20 highs has p = 1) -- which keeps the zone-level claim the only
# claim the fixture makes.
#
# Highs are PLACED, never sampled: ``hot_zone_high`` of them fill the first
# sections of zone 1, and ``spread_high`` of them take section 1 of each of the
# next ``spread_high`` zones.  Two contributors score every section, the highs
# at 1.0 and everyone else at 0.0, so the composite is a two-valued column and
# the top 20 is exactly the planted 20 -- not the top 20 of a noise draw.
#
# The two configurations the acceptance test uses:
#
#   hot=10, spread=10   one zone with x = 10 of n_z = 40 (p ~ 4.9e-13, which
#                       clears BH's 0.05/50 = 1e-3 first-rank bar by nine
#                       orders of magnitude) and ten zones with x = 1
#                       (p ~ 0.334, nowhere near their 0.002 bar)
#   hot=0,  spread=20   twenty zones with x = 1, so the smallest p in the
#                       family is 0.334 against a first-rank bar of 0.001
# --------------------------------------------------------------------------

#: The two contributors every seeded section carries: the minimum a composite
#: row needs, so the fixture is also the boundary case of that rule.
_ZONE_CONTRIBUTORS: tuple[tuple[str, str], ...] = (_MULTI_ARITHMETIC, _MULTI_PRESENCE)

#: What a planted high scores, and what everyone else does.  Two values with
#: nothing between them: the top-K cut then falls in a gap rather than inside a
#: tie block, so "the top 20 are exactly the 20 planted" is a fact about the
#: fixture and not a coincidence of the ranking.
_ZONE_HIGH_SCORE = 1.0
_ZONE_CLEAN_SCORE = 0.0

#: The one municipality every zone belongs to.
_ZONE_MUN = BOA_VISTA


@pytest.fixture
def seed_zones():
    """Seed ``zones`` x ``per_zone`` sections with the highs placed by hand."""

    def _seed(
        db: Database,
        *,
        zones: int = 50,
        per_zone: int = 40,
        hot_zone_high: int = 0,
        spread_high: int = 0,
        uf: str = "RR",
        mun: str = _ZONE_MUN,
        round: int = 1,
    ) -> Warehouse:
        planted = hot_zone_high + spread_high
        expected = math.ceil(0.01 * zones * per_zone)
        assert planted == expected, (
            f"{planted} planted highs but the national high set is "
            f"ceil(0.01 x {zones * per_zone}) = {expected}; the fixture only "
            "states what it means if the two agree"
        )
        assert hot_zone_high <= per_zone, "the hot zone cannot hold more than it has"
        assert spread_high < zones, "not enough zones to spread one high each"

        # Zone 1 is the hot one; zones 2..(1 + spread_high) take one high each,
        # always in their section 1.  Written as a set so the loop below stays a
        # single pass over every section.
        highs = {(1, section) for section in range(1, hot_zone_high + 1)}
        highs |= {(zone, 1) for zone in range(2, 2 + spread_high)}

        w = Warehouse(db)
        with db.transaction():
            w.municipality(mun, uf=uf)
            for zone in range(1, zones + 1):
                for section in range(1, per_zone + 1):
                    score = (_ZONE_HIGH_SCORE if (zone, section) in highs
                             else _ZONE_CLEAN_SCORE)
                    for detector, race in _ZONE_CONTRIBUTORS:
                        w.score(mun, zone, section, race, round, detector,
                                score, uf=uf)
        return w

    return _seed


# ==========================================================================
# Shard-encoder fixtures (Task 8)
#
# Three shapes, because the encoder has three separable jobs:
#
#   shard_input_fixture  correctness -- hand-written, three sections across
#                        two zones, deliberately fed OUT of order and with a
#                        zero-vote row, so "sorted by (zone, section)",
#                        "COO sorted by (sec, cand)" and "zero pairs omitted"
#                        are properties of the encoder rather than of the input
#   big_shard_input      the 300 KiB budget -- synthetic and incompressible on
#                        purpose (random vote counts), two zones
#   giant_zone_input     the 1 MiB hard budget -- one zone that no split can
#                        rescue, which is exactly the case the spec says must
#                        stop the export
#
# The two synthetic ones are generated from a seeded RNG: their sizes are
# assertions, so they must not move between runs.
# ==========================================================================

_GOLDEN_PRES: tuple[tuple[int, str, int], ...] = (
    (13, "LULA", 13),
    (22, "JAIR BOLSONARO", 22),
)
_GOLDEN_GOV: tuple[tuple[int, str, int], ...] = (
    (15, "ANTONIO DENARIUM", 15),
    (44, "OTTACI NASCIMENTO", 44),
)


@pytest.fixture
def shard_input_fixture() -> shards.MunicipalityData:
    """A three-section municipality across two zones, PRES_1/PRES_2/GOV_1.

    Sorted order is (8,1), (8,2), (9,1) -> indices 0, 1, 2.  PRES_1 carries a
    zero-vote row (section (8,2), candidate 13) that the encoder must drop,
    so ``expected_pres1_total`` counts only the five non-zero results.
    """
    sections = [(9, 1), (8, 2), (8, 1)]          # out of order, on purpose

    pres1 = {
        (8, 1): {13: 201, 22: 102},
        (8, 2): {13: 0, 22: 150},                # the zero must not survive
        (9, 1): {13: 188, 22: 90},
    }
    pres2 = {
        (8, 1): {13: 220, 22: 110},
        (8, 2): {13: 30, 22: 160},
        (9, 1): {13: 195, 22: 95},
    }
    gov1 = {
        (8, 1): {15: 150, 44: 140},
        (8, 2): {15: 90, 44: 88},
        (9, 1): {15: 130, 44: 120},
    }
    per_race = {"PRES_1": pres1, "PRES_2": pres2, "GOV_1": gov1}

    votes = [
        shards.VoteRow(race, zone, section, number, count)
        # reversed so the COO triplets arrive in the wrong order too
        for race, cells in reversed(list(per_race.items()))
        for (zone, section), results in reversed(list(cells.items()))
        for number, count in results.items()
    ]
    candidates = [
        shards.CandidateRow(race, number, name, party)
        for race, roster in (
            ("PRES_1", _GOLDEN_PRES),
            ("PRES_2", _GOLDEN_PRES),
            ("GOV_1", _GOLDEN_GOV),
        )
        for number, name, party in roster
    ]
    stats = [
        shards.StatsRow(
            race, zone, section,
            aptos=420, comp=sum(results.values()) + 12,
            blank=7, null_votes=5,
            legenda=0 if race.startswith("PRES") else 11,
        )
        for race, cells in per_race.items()
        for (zone, section), results in cells.items()
    ]
    scores = [
        shards.ScoreRow("PRES_1", 8, 1, "extreme_dominance", 2.1,
                        {"k": 201, "n": 303, "cand": 13}),
        shards.ScoreRow("PRES_1", 8, 2, "extreme_dominance", 0.3),
        # (9, 1) deliberately unscored -> a null in the dense array
        shards.ScoreRow("ALL_1", 8, 1, "t1_presence", 0.0),
        shards.ScoreRow("ALL_1", 8, 2, "t1_presence", 0.0),
        shards.ScoreRow("ALL_1", 9, 1, "t1_presence", 0.0),
    ]

    mun = shards.MunicipalityData(
        code="03018", name="BOA VISTA", uf="RR",
        sections=sections, candidates=candidates,
        stats=stats, votes=votes, scores=scores,
    )
    mun.expected_pres1_total = sum(
        count for results in pres1.values() for count in results.values()
    )
    assert mun.expected_pres1_total == 731
    return mun


#: Share of the roster a synthetic section actually reports votes for.  Below
#: 1.0 on purpose: an identical candidate list in every section is a periodic
#: signal gzip erases almost entirely, which would make the fixture's size a
#: statement about DEFLATE rather than about the encoder's output.
_SYNTHETIC_BALLOT_COVERAGE = 0.6


def _synthetic_municipality(
    *,
    code: str,
    zones: int,
    sections_per_zone: int,
    candidates: int,
    seed: int,
) -> shards.MunicipalityData:
    """A DEPEST-shaped municipality with random, poorly-compressible results.

    DEPEST is the widest ballot in the country and therefore the shape that
    actually threatens the size budget: hundreds of candidates x hundreds of
    sections.  Everything is drawn from a seeded RNG so the gzipped size is
    reproducible -- the fixtures assert on it.
    """
    rng = random.Random(seed)
    race = "DEPEST_1"

    numbers = [10_000 + 7 * i for i in range(candidates)]
    roster = [
        shards.CandidateRow(race, number, f"CANDIDATO SINTETICO {number}",
                            number // 1000)
        for number in numbers
    ]
    per_section = max(1, int(candidates * _SYNTHETIC_BALLOT_COVERAGE))

    sections: list[tuple[int, int]] = []
    votes: list[shards.VoteRow] = []
    stats: list[shards.StatsRow] = []
    scores: list[shards.ScoreRow] = []
    for zone in range(1, zones + 1):
        for section in range(1, sections_per_zone + 1):
            sections.append((zone, section))
            cast = 0
            for number in rng.sample(numbers, per_section):
                count = rng.randint(1, 999)
                cast += count
                votes.append(shards.VoteRow(race, zone, section, number, count))
            stats.append(shards.StatsRow(
                race, zone, section,
                aptos=cast + rng.randint(1, 400), comp=cast + rng.randint(1, 90),
                blank=rng.randint(0, 40), null_votes=rng.randint(0, 40),
                legenda=rng.randint(0, 300),
            ))
            scores.append(shards.ScoreRow(
                race, zone, section, "extreme_dominance", rng.random() * 16
            ))
    return shards.MunicipalityData(
        code=code, name="MUNICIPIO SINTETICO", uf="RR",
        sections=sections, candidates=roster,
        stats=stats, votes=votes, scores=scores,
    )


@pytest.fixture(scope="session")
def big_shard_input() -> shards.MunicipalityData:
    """Two zones, comfortably over the 300 KiB target, well under 1 MiB/zone."""
    mun = _synthetic_municipality(
        code="99001", zones=2, sections_per_zone=700, candidates=200, seed=8001
    )
    size = len(shards.encode_bytes(shards.encode_shard(mun)))
    assert size > shards.SHARD_TARGET_BYTES, (
        f"big_shard_input gzips to {size:,} B, which is not over the "
        f"{shards.SHARD_TARGET_BYTES:,} B target it exists to breach"
    )
    return mun


@pytest.fixture(scope="session")
def giant_zone_input() -> shards.MunicipalityData:
    """One zone whose own subshard is over the 1 MiB hard budget."""
    mun = _synthetic_municipality(
        code="99002", zones=1, sections_per_zone=1750, candidates=500, seed=8002
    )
    size = len(shards.encode_bytes(shards.encode_shard(shards.restrict_to_zone(mun, 1))))
    assert size > shards.SHARD_HARD_BYTES, (
        f"giant_zone_input's only subshard gzips to {size:,} B, which is not "
        f"over the {shards.SHARD_HARD_BYTES:,} B hard budget it exists to breach"
    )
    return mun


# ==========================================================================
# Aggregates-export fixtures (Task 9)
#
# The aggregate files are the one place where EVERY upstream product meets:
# facts, stats, detector scores, detector summaries, per-state payloads and the
# A4 reconciliation findings.  So the fixture seeds all six, in a warehouse
# whose only unusual property is its SIZE -- 620 scored sections across two
# real RR municipalities, so the 500-row cap on `anomalies/{detector}.json` is
# actually exercised.  A 10-section fixture would make that assertion vacuous.
#
# Real municipality codes (03018 BOA VISTA, 03158 SÃO LUIZ) on purpose: the
# municipality index resolves coordinates through the committed TSE<->IBGE
# crosswalk, and a synthetic code would silently produce null centroids and
# pass anyway.
# ==========================================================================

#: Sections per municipality.  620 > 500, which is the whole point.
_AGG_SECTIONS: tuple[tuple[str, str, int], ...] = (
    (BOA_VISTA, "BOA VISTA", 500),
    (SAO_LUIZ, "SÃO LUIZ", 120),
)

_AGG_DETECTOR = "t2_extreme_dominance"
_AGG_FLAG_THRESHOLD = 4.0

#: The presidential runoff roster the fixture seeds.
_AGG_PRES_R2: tuple[tuple[int, str], ...] = (
    (13, "LUIZ INÁCIO LULA DA SILVA"),
    (22, "JAIR MESSIAS BOLSONARO"),
)


@pytest.fixture
def analysis_seeded_db(test_db: Database) -> Database:
    """A warehouse with PRES round-2 results AND a full analysis layer.

    Everything is deterministic and closed-form -- vote counts, scores and the
    A4 votes are all functions of the section index -- so the export's numbers
    can be checked against Postgres rather than against a remembered constant.
    """
    w = Warehouse(test_db)
    with test_db.transaction():
        index = 0
        for mun, name, count in _AGG_SECTIONS:
            w.municipality(mun, name=name)
            for section in range(1, count + 1):
                index += 1
                zone = 1 + (section % 3)
                lula = 40 + (index * 7) % 130
                bolso = 60 + (index * 11) % 170
                w.cand_votes(mun, zone, section, "PRES", 2, 13, lula,
                             name=_AGG_PRES_R2[0][1], cand_uf="BR")
                w.cand_votes(mun, zone, section, "PRES", 2, 22, bolso,
                             name=_AGG_PRES_R2[1][1], cand_uf="BR")
                blank, nulls = index % 5, index % 7
                cast = lula + bolso + blank + nulls
                w.stats(mun, zone, section, "PRES", 2, aptos=cast + 40,
                        comparecimento=cast, abstentions=40,
                        blank=blank, null_votes=nulls)
                # Strictly decreasing, so "ranked descending" is a real
                # assertion and the 500-row cut is unambiguous.
                w.score(mun, zone, section, "PRES", 2, _AGG_DETECTOR,
                        16.0 - index * 0.01,
                        {"k": lula, "n": lula + bolso, "cand": 13,
                         "baseline": "municipality"})

        # A second detector, cross-race, so the export is exercised on more
        # than one score file and on the ALL_r race convention.
        for mun, _name, _count in _AGG_SECTIONS:
            for section in range(1, 6):
                w.score(mun, 1 + (section % 3), section, "ALL", 2,
                        "t1_presence", 0.0, {"rules": []})

        w.detector_summary(_AGG_DETECTOR, scored=index, flagged=64,
                           params={"flag_threshold": _AGG_FLAG_THRESHOLD},
                           summary={"score": {"max": 16.0}})
        w.detector_summary("t1_presence", scored=10, flagged=0,
                           params={"flag_threshold": 1.0}, summary={})
        # THE generic hook: a detector-specific per-state payload that the
        # exporter must carry through without knowing what it means.
        w.state_payload("t1_presence", "RR",
                        {"races_by_round": {"1": ["GOV", "PRES"], "2": ["PRES"]}})

        # Amendment A4: candidacies annulled after the election, plus the
        # legenda mirror image.
        from observatory import reconcile

        reconcile.ensure_report_table(test_db)
        w.candidate("SEN", 1, 333, "HELDER GIRÃO BARRETO", party=33)
        w.candidate("DEPFED", 1, 4040, "ANTÔNIA NECO", party=40)
        w.report_row(BOA_VISTA, "SEN", 1, "cand:333", 25_554)
        w.report_row(SAO_LUIZ, "SEN", 1, "cand:333", 1_446)
        w.report_row(BOA_VISTA, "DEPFED", 1, "cand:4040", 196)
        w.report_row(BOA_VISTA, "DEPFED", 1, "legenda:40", 95)

    return test_db


@pytest.fixture
def tmp_out(tmp_path: Path) -> Path:
    """A throwaway export root, the shape ``04_export.py`` writes into."""
    out = tmp_path / "data"
    out.mkdir()
    return out


def load_json(path: Path | str) -> dict:
    """Read one exported aggregate file.

    Reads from DISK by design: what the tests assert on is the artefact the
    dashboard will fetch, never an in-memory document the exporter handed back.
    """
    return json.loads(Path(path).read_text(encoding="utf-8"))


def db_total(db: Database, race: str, round: int) -> int:
    """Nominal (candidate) votes for one race and round, straight from Postgres.

    The definition ``summary.json`` must agree with: ``votavel_kind =
    'candidate'``, which excludes legenda, blank and null votes.
    """
    return int(db.scalar(
        "SELECT COALESCE(SUM(votes), 0) FROM fact_votes "
        "WHERE race = %s AND round = %s AND votavel_kind = 'candidate'",
        (race, round),
    ))


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


# ==========================================================================
# Export-gate fixtures (Task 10)
#
# The gate's whole claim is that it re-reads from DISK, so its fixture has to
# be a real export: shards written by the real writer, aggregates written by
# the real writer, finalized under real content-hashed names.  It goes into a
# tmp_path and never near ``dashboard/public/data/`` -- the tamper helpers
# below deliberately corrupt whatever root they are handed.
#
# The tamper helpers re-hash and re-manifest what they rewrite.  That is the
# point: a tamper that left a stale hash would be caught by the manifest check
# and would say nothing at all about whether the TOTALS check works.
# ==========================================================================


@pytest.fixture
def exported_rr(analysis_seeded_db: Database, tmp_path: Path) -> Path:
    """A complete, finalized export of the seeded RR warehouse."""
    root = tmp_path / "export"
    for mun in shards.read_uf(analysis_seeded_db, "RR"):
        if mun.sections:
            shards.write_shard(mun, root / "shards")
    aggregates.write_all(analysis_seeded_db, root)
    manifest.finalize(root)
    return root


def _rewrite(export_root: Path, logical: str, mutate: Callable[[dict], None]) -> Path:
    """Apply ``mutate`` to one exported document, back to disk, re-addressed.

    Re-encodes with the same writer the export used, re-hashes, renames to the
    new content address and updates the manifest -- so the only thing wrong
    with the result is what ``mutate`` made wrong.
    """
    root = Path(export_root)
    doc = manifest.load(root)
    entry = doc["files"][logical]
    path = root / entry["path"]

    gz = logical.endswith(".gz")
    raw = path.read_bytes()
    parsed = json.loads(gzip.decompress(raw) if gz else raw)
    mutate(parsed)
    blob = shards.encode_bytes(parsed) if gz else aggregates.encode_bytes(parsed)

    path.unlink()
    digest = manifest.sha256_bytes(blob)
    new_path = manifest.hashed_name(logical, digest)
    (root / new_path).write_bytes(blob)

    entry.update(path=new_path, sha256=digest, bytes=len(blob))
    doc["count"] = len(doc["files"])
    doc["bytes"] = sum(int(e["bytes"]) for e in doc["files"].values())
    manifest.dump(root, doc)
    return root / new_path


def tamper_one_vote(export_root: Path) -> str:
    """Add one vote to one COO entry of one shard.  Returns its logical name.

    A single vote, in a single race, in a single municipality: the smallest
    lie the export can tell, and the gate has to find it by re-deriving the
    municipality's totals from Postgres.
    """
    doc = manifest.load(export_root)
    for logical in sorted(doc["files"]):
        if not logical.startswith("shards/"):
            continue
        blob = (Path(export_root) / doc["files"][logical]["path"]).read_bytes()
        shard = json.loads(gzip.decompress(blob))
        race = next(
            (rid for rid, coo in (shard.get("votes") or {}).items() if coo["n"]),
            None,
        )
        if race is None:                      # a split index, or an empty shard
            continue

        def bump(parsed: dict, race: str = race) -> None:
            parsed["votes"][race]["n"][0] += 1

        _rewrite(Path(export_root), logical, bump)
        return logical
    raise AssertionError(f"no shard with votes to tamper with in {export_root}")


def rewrite_version(export_root: Path, logical_name: str, v: int = 2) -> Path:
    """Rewrite one exported file's schema version stamp."""
    return _rewrite(Path(export_root), logical_name, lambda doc: doc.update(v=v))


def physical_files(export_root: Path) -> set[str]:
    """Every file actually on disk, relative to the export root."""
    root = Path(export_root)
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
