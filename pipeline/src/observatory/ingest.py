"""Staging -> validate -> swap, with the quarantine gate.

    uv run python 02_ingest.py --uf RR

WHAT A RUN IS
    One transaction.  ``reset_data`` (TRUNCATE) empties the warehouse, every
    source archive is streamed through ``parse``'s batched readers into
    UNLOGGED ``staging.*`` tables, each batch is published into the real tables
    and its staging table dropped, and finally the gates run.  Because TRUNCATE
    is transactional in Postgres, a failure anywhere -- a bad row rate over the
    gate, a duplicate key, an FK orphan -- rolls the entire thing back and the
    previous good state is still there, untouched.

WHAT GETS COUNTED
    ``rows_attempted`` is every row the readers handed us, including rows this
    run then discarded as out of scope.  That matters: a ``--uf RR`` run reads
    the national ``votacao_secao_2022_BR.csv`` (5.38 M rows) to recover RR's
    presidential sections (A2), and scoring its unparseable rows against only
    the ~13 k rows we kept would fail the 0.01 % gate on someone else's data.
    The invariant we hold instead is exact:

        rows_attempted == rows_loaded + rows_quarantined + rows_out_of_scope

    Nothing is dropped silently: a row is loaded, quarantined with a reason, or
    counted as out of scope (wrong UF, or wrong election -- see A3 below).

AMENDMENT A3 (binding)
    The 2022 archives contain a second, unrelated election: ``CD_ELEICAO=6278``
    "Eleição Suplementar Governador 2026" carries ``ANO_ELEICAO=2022`` and
    ``NR_TURNO=1`` and collides with the real 2022 general on
    ``(uf, mun, zone, section, race, round)``.  Every load filters to
    ``parse.ELECTIONS_2022_GENERAL``.  ``fact_votes`` has no ON CONFLICT clause
    precisely so that a regression here is a loud duplicate-key failure rather
    than a quiet double count.

AMENDMENT A2 (binding)
    Per-UF ``votacao_secao`` and munzona members carry state races only.  All
    presidential rows (both rounds) live in the ``_BR`` members, so a UF-scoped
    run reads the UF member *and* the UF slice of the BR member.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl
import psycopg

from . import parse
from .config import settings
from .db import (
    CANDIDATE_ID_NUMBER_STRIDE,
    CANDIDATE_ID_RACE_STRIDE,
    CANDIDATE_ID_ROUND_STRIDE,
    RACE_INDEX,
    SECTION_ID_MUN_STRIDE,
    SECTION_ID_ZONE_STRIDE,
    UF_INDEX,
    Database,
    gate_runs as db_gate_runs,
    region_case,
)

__all__ = [
    "Batch",
    "IngestReport",
    "IngestError",
    "IngestValidationError",
    "QuarantineGateError",
    "QUARANTINE_GATE",
    "SourceFile",
    "connect",
    "counts",
    "distinct_election_ids",
    "load_batch",
    "plan_sources",
    "read_gate_runs",
    "read_quarantine",
    "run",
]

#: > 0.01 % quarantined rows is a hard failure (spec + plan global constraint).
QUARANTINE_GATE = 0.0001

DEFAULT_BATCH_SIZE = 250_000

#: Widths the computed ``dim_section.id`` assumes.  Real data is far below
#: both (RR's largest section number is in the hundreds); a violation means the
#: id scheme must widen, so it is a hard failure rather than a quarantine.
MAX_ZONE = SECTION_ID_MUN_STRIDE // SECTION_ID_ZONE_STRIDE - 1
MAX_SECTION = SECTION_ID_ZONE_STRIDE - 1
MAX_CANDIDATE_NUMBER = CANDIDATE_ID_NUMBER_STRIDE - 1


class IngestError(RuntimeError):
    """Base class for ingest failures."""


class IngestValidationError(IngestError):
    """A load broke an invariant the warehouse depends on."""


class QuarantineGateError(IngestError):
    """More than 0.01 % of attempted rows could not be parsed."""


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class IngestReport:
    """Outcome of a load.  ``quarantine_rate`` is quarantined / attempted."""

    rows_attempted: int
    rows_loaded: int
    rows_quarantined: int
    quarantine_rate: float
    rows_out_of_scope: int = 0

    @classmethod
    def of(
        cls,
        *,
        attempted: int,
        loaded: int,
        quarantined: int,
        out_of_scope: int = 0,
    ) -> "IngestReport":
        rate = (quarantined / attempted) if attempted else 0.0
        return cls(attempted, loaded, quarantined, rate, out_of_scope)

    def __add__(self, other: "IngestReport") -> "IngestReport":
        return IngestReport.of(
            attempted=self.rows_attempted + other.rows_attempted,
            loaded=self.rows_loaded + other.rows_loaded,
            quarantined=self.rows_quarantined + other.rows_quarantined,
            out_of_scope=self.rows_out_of_scope + other.rows_out_of_scope,
        )

    @property
    def balanced(self) -> bool:
        return (
            self.rows_attempted
            == self.rows_loaded + self.rows_quarantined + self.rows_out_of_scope
        )

    def summary(self) -> str:
        return (
            f"attempted={self.rows_attempted:,} loaded={self.rows_loaded:,} "
            f"quarantined={self.rows_quarantined:,} "
            f"out_of_scope={self.rows_out_of_scope:,} "
            f"rate={self.quarantine_rate:.6%}"
        )


EMPTY_REPORT = IngestReport.of(attempted=0, loaded=0, quarantined=0)


@dataclass(frozen=True)
class Batch:
    """One unit of work: the ``(good, bad)`` pair a reader yielded.

    ``rows_out_of_scope`` lets a caller declare rows it discarded before
    building the batch (a UF filter, typically) so the run-level accounting
    still balances.
    """

    kind: str
    source_file: str
    good: pl.DataFrame
    bad: pl.DataFrame
    rows_out_of_scope: int = 0

    @property
    def rows_attempted(self) -> int:
        return self.good.height + self.bad.height + self.rows_out_of_scope


# --------------------------------------------------------------------------
# Computed surrogate keys (see db.py for why they are computed, not serial)
# --------------------------------------------------------------------------


def section_id_expr() -> pl.Expr:
    """``mun_code * 10^9 + zone * 10^5 + section``."""
    return (
        pl.col("mun_code").cast(pl.Int64) * SECTION_ID_MUN_STRIDE
        + pl.col("zone").cast(pl.Int64) * SECTION_ID_ZONE_STRIDE
        + pl.col("section").cast(pl.Int64)
    ).alias("section_id")


def candidate_uf_expr() -> pl.Expr:
    """National races are keyed under the pseudo-UF ``BR``; everything else
    under the UF that ran the race (deputy numbers repeat across states)."""
    return (
        pl.when(pl.col("race") == "PRES")
        .then(pl.lit("BR"))
        .otherwise(pl.col("uf"))
        .alias("cand_uf")
    )


def candidate_id_expr() -> pl.Expr:
    """``((uf_idx * 8 + race_idx) * 4 + round) * 10^5 + number``, NULL for
    anything that is not a candidate row (blank / null / legenda)."""
    uf_idx = pl.col("cand_uf").replace_strict(UF_INDEX, default=None, return_dtype=pl.Int64)
    race_idx = pl.col("race").replace_strict(RACE_INDEX, default=None, return_dtype=pl.Int64)
    ident = (
        ((uf_idx * CANDIDATE_ID_RACE_STRIDE + race_idx) * CANDIDATE_ID_ROUND_STRIDE
         + pl.col("round").cast(pl.Int64))
        * CANDIDATE_ID_NUMBER_STRIDE
        + pl.col("cand_number").cast(pl.Int64)
    )
    return (
        pl.when(pl.col("votavel_kind") == "candidate")
        .then(ident)
        .otherwise(None)
        .cast(pl.Int64)
        .alias("candidate_id")
    )


def _check_key_ranges(kind: str, frame: pl.DataFrame) -> None:
    """Guard the assumptions the computed ids rest on."""
    if frame.height == 0:
        return
    if not frame["mun_code"].str.contains(r"^\d{5}$").all():
        bad = frame.filter(~pl.col("mun_code").str.contains(r"^\d{5}$"))["mun_code"][0]
        raise IngestValidationError(f"{kind}: mun_code is not 5 digits: {bad!r}")
    if frame["zone"].max() > MAX_ZONE:  # type: ignore[operator]
        raise IngestValidationError(
            f"{kind}: zone {frame['zone'].max()} exceeds {MAX_ZONE}; the "
            "section-id scheme in db.py must widen"
        )
    if "section" in frame.columns and frame["section"].max() > MAX_SECTION:  # type: ignore[operator]
        raise IngestValidationError(
            f"{kind}: section {frame['section'].max()} exceeds {MAX_SECTION}; the "
            "section-id scheme in db.py must widen"
        )
    if "cand_uf" in frame.columns:
        unknown = frame.filter(
            pl.col("cand_uf").replace_strict(UF_INDEX, default=None, return_dtype=pl.Int64).is_null()
        )
        if unknown.height:
            raise IngestValidationError(
                f"{kind}: unknown UF {unknown['cand_uf'][0]!r}; add it to db.UF_ORDER "
                "(appending is safe, reordering is not)"
            )
        numbers = frame.filter(pl.col("votavel_kind") == "candidate")["cand_number"]
        if numbers.len() and numbers.max() > MAX_CANDIDATE_NUMBER:  # type: ignore[operator]
            raise IngestValidationError(
                f"{kind}: candidate number {numbers.max()} exceeds "
                f"{MAX_CANDIDATE_NUMBER}; the candidate-id scheme must widen"
            )


# --------------------------------------------------------------------------
# Kinds: staging DDL + augmentation + publish SQL
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Kind:
    name: str
    staging_table: str
    staging_ddl: str
    augment: Callable[[pl.DataFrame], pl.DataFrame]
    publish: Callable[[Database], int]
    reader: Callable[..., Iterator[tuple[pl.DataFrame, pl.DataFrame]]]


def _augment_votes(frame: pl.DataFrame) -> pl.DataFrame:
    out = frame.with_columns(section_id_expr(), candidate_uf_expr())
    out = out.with_columns(candidate_id_expr())
    _check_key_ranges("votes", out)
    return out


def _augment_sectionish(frame: pl.DataFrame) -> pl.DataFrame:
    out = frame.with_columns(section_id_expr())
    _check_key_ranges("detail", out)
    return out


def _augment_rollup(frame: pl.DataFrame) -> pl.DataFrame:
    _check_key_ranges("rollup", frame)
    return frame


_MUNICIPALITY_UPSERT = """
INSERT INTO dim_municipality (code, name, uf, region)
SELECT DISTINCT ON (mun_code) mun_code, mun_name, uf, {region}
FROM {staging}
ORDER BY mun_code, mun_name NULLS LAST
ON CONFLICT (code) DO UPDATE
SET name = COALESCE(dim_municipality.name, EXCLUDED.name)
"""

_SECTION_UPSERT = """
INSERT INTO dim_section (id, uf, mun_code, zone, section)
SELECT DISTINCT ON (section_id) section_id, uf, mun_code, zone, section
FROM {staging}
ORDER BY section_id
ON CONFLICT (id) DO NOTHING
"""


def _municipality_upsert(staging: str) -> str:
    return _MUNICIPALITY_UPSERT.format(staging=staging, region=region_case("uf"))


def _publish_votes(db: Database) -> int:
    db.execute(_municipality_upsert("staging.votes"))
    db.execute(_SECTION_UPSERT.format(staging="staging.votes"))

    db.execute(
        """
        INSERT INTO dim_candidate (id, uf, race, round, number, name, party_number)
        SELECT DISTINCT ON (candidate_id)
               candidate_id, cand_uf, race, round, cand_number, cand_name, party_number
        FROM staging.votes
        WHERE votavel_kind = 'candidate'
        ORDER BY candidate_id, cand_name NULLS LAST
        ON CONFLICT (id) DO NOTHING
        """
    )
    # Hard assert (plan contract): no (uf, race, round, number) may map to two
    # candidate names.  Compares the batch against what is already stored, so
    # it catches conflicts within AND across batches.
    clashes = db.fetch(
        """
        SELECT d.uf, d.race, d.round, d.number, d.name, s.cand_name
        FROM staging.votes s
        JOIN dim_candidate d ON d.id = s.candidate_id
        WHERE s.votavel_kind = 'candidate'
          AND s.cand_name IS DISTINCT FROM d.name
        GROUP BY 1, 2, 3, 4, 5, 6
        LIMIT 5
        """
    )
    if clashes:
        raise IngestValidationError(
            "candidate identity clash -- one (uf, race, round, number) maps to "
            f"two names: {clashes}"
        )

    return db.execute(
        """
        INSERT INTO fact_votes
            (section_id, race, round, votavel_kind, candidate_id, party_number,
             votes, election_id)
        SELECT section_id, race, round, votavel_kind, candidate_id,
               CASE WHEN votavel_kind IN ('candidate', 'legenda')
                    THEN party_number END,
               votes, election_id
        FROM staging.votes
        """
    )


def _publish_detail(db: Database) -> int:
    db.execute(_municipality_upsert("staging.detail"))
    db.execute(_SECTION_UPSERT.format(staging="staging.detail"))
    # Electorate per section per round.  TSE repeats it on every race row of a
    # section; MAX collapses that without assuming the races agree.
    for rnd, column in ((1, "aptos_r1"), (2, "aptos_r2")):
        db.execute(
            f"""
            UPDATE dim_section d
            SET {column} = a.aptos
            FROM (SELECT section_id, MAX(aptos) AS aptos
                  FROM staging.detail WHERE round = %s AND aptos IS NOT NULL
                  GROUP BY section_id) a
            WHERE d.id = a.section_id AND d.{column} IS DISTINCT FROM a.aptos
            """,
            (rnd,),
        )
    return db.execute(
        """
        INSERT INTO fact_section_stats
            (section_id, race, round, aptos, comparecimento, abstentions,
             blank, null_votes, election_id)
        SELECT section_id, race, round, aptos, comparecimento, abstentions,
               blank, null_votes, election_id
        FROM staging.detail
        """
    )


def _publish_rollup(table: str, staging: str, columns: tuple[str, ...]) -> Callable[[Database], int]:
    cols = ", ".join(columns)

    def publish(db: Database) -> int:
        db.execute(_municipality_upsert(staging))
        return db.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM {staging}")

    return publish


_VOTES_STAGING_DDL = """
CREATE UNLOGGED TABLE staging.votes (
    uf           TEXT,
    mun_code     TEXT,
    mun_name     TEXT,
    zone         INTEGER,
    section      INTEGER,
    race         TEXT,
    round        SMALLINT,
    votavel_kind TEXT,
    cand_number  INTEGER,
    cand_name    TEXT,
    party_number SMALLINT,
    votes        BIGINT,
    election_id  SMALLINT,
    section_id   BIGINT,
    cand_uf      TEXT,
    candidate_id INTEGER
)
"""

_DETAIL_STAGING_DDL = """
CREATE UNLOGGED TABLE staging.detail (
    uf               TEXT,
    mun_code         TEXT,
    mun_name         TEXT,
    zone             INTEGER,
    section          INTEGER,
    race             TEXT,
    round            SMALLINT,
    aptos            BIGINT,
    comparecimento   BIGINT,
    abstentions      BIGINT,
    blank            BIGINT,
    null_votes       BIGINT,
    agg_secoes       INTEGER,
    nominal          BIGINT,
    legenda          BIGINT,
    annulled_apu_sep BIGINT,
    installed        BOOLEAN,
    annulled         BOOLEAN,
    vote_origin      TEXT,
    urna_model       TEXT,
    local_id         INTEGER,
    election_id      SMALLINT,
    section_id       BIGINT
)
"""

_ROLLUP_CAND_STAGING_DDL = """
CREATE UNLOGGED TABLE staging.rollup_cand (
    uf                  TEXT,
    mun_code            TEXT,
    mun_name            TEXT,
    zone                INTEGER,
    race                TEXT,
    round               SMALLINT,
    cand_seq            TEXT,
    cand_number         INTEGER,
    cand_name           TEXT,
    cand_urna_name      TEXT,
    party_number        SMALLINT,
    party_sigla         TEXT,
    federation_number   INTEGER,
    federation_sigla    TEXT,
    coalition           TEXT,
    votes_nominal       BIGINT,
    votes_nominal_valid BIGINT,
    in_transit          BOOLEAN,
    situation           TEXT,
    election_id         SMALLINT
)
"""

_ROLLUP_PARTY_STAGING_DDL = """
CREATE UNLOGGED TABLE staging.rollup_party (
    uf                        TEXT,
    mun_code                  TEXT,
    mun_name                  TEXT,
    zone                      INTEGER,
    race                      TEXT,
    round                     SMALLINT,
    party_number              SMALLINT,
    party_sigla               TEXT,
    federation_number         INTEGER,
    federation_sigla          TEXT,
    coalition                 TEXT,
    votes_legenda_valid       BIGINT,
    votes_legenda_annulled    BIGINT,
    votes_legenda_anul_subjud BIGINT,
    votes_nom_convr_leg_valid BIGINT,
    votes_total_leg_valid     BIGINT,
    votes_nominal_valid       BIGINT,
    in_transit                BOOLEAN,
    election_id               SMALLINT
)
"""

_ROLLUP_DETAIL_STAGING_DDL = """
CREATE UNLOGGED TABLE staging.rollup_detail (
    uf                 TEXT,
    mun_code           TEXT,
    mun_name           TEXT,
    zone               INTEGER,
    race               TEXT,
    round              SMALLINT,
    aptos              BIGINT,
    comparecimento     BIGINT,
    abstentions        BIGINT,
    blank              BIGINT,
    null_votes         BIGINT,
    total_null_votes   BIGINT,
    nominal_valid      BIGINT,
    legenda_valid      BIGINT,
    total_valid        BIGINT,
    sec_principais     INTEGER,
    agg_secoes         INTEGER,
    sec_nao_instaladas INTEGER,
    sec_total          INTEGER,
    in_transit         BOOLEAN,
    election_id        SMALLINT
)
"""

KINDS: dict[str, Kind] = {
    "votes": Kind(
        name="votes",
        staging_table="staging.votes",
        staging_ddl=_VOTES_STAGING_DDL,
        augment=_augment_votes,
        publish=_publish_votes,
        reader=parse.iter_votes,
    ),
    "detail": Kind(
        name="detail",
        staging_table="staging.detail",
        staging_ddl=_DETAIL_STAGING_DDL,
        augment=_augment_sectionish,
        publish=_publish_detail,
        reader=parse.iter_detail,
    ),
    "rollup_cand": Kind(
        name="rollup_cand",
        staging_table="staging.rollup_cand",
        staging_ddl=_ROLLUP_CAND_STAGING_DDL,
        augment=_augment_rollup,
        publish=_publish_rollup(
            "rollup_cand_munzona",
            "staging.rollup_cand",
            tuple(parse.ROLLUP_CAND_SCHEMA),
        ),
        reader=parse.iter_rollup_cand,
    ),
    "rollup_party": Kind(
        name="rollup_party",
        staging_table="staging.rollup_party",
        staging_ddl=_ROLLUP_PARTY_STAGING_DDL,
        augment=_augment_rollup,
        publish=_publish_rollup(
            "rollup_party_munzona",
            "staging.rollup_party",
            tuple(parse.ROLLUP_PARTY_SCHEMA),
        ),
        reader=parse.iter_rollup_party,
    ),
    "rollup_detail": Kind(
        name="rollup_detail",
        staging_table="staging.rollup_detail",
        staging_ddl=_ROLLUP_DETAIL_STAGING_DDL,
        augment=_augment_rollup,
        publish=_publish_rollup(
            "rollup_detail_munzona",
            "staging.rollup_detail",
            tuple(parse.ROLLUP_DETAIL_SCHEMA),
        ),
        reader=parse.iter_rollup_detail,
    ),
}


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def connect(url: str | None = None) -> Database:
    """Open a connection and make sure the schema exists."""
    db = Database.connect(url)
    db.create_schema()
    return db


def load_batch(db: Database, batch: Batch, *, enforce_gate: bool = True) -> IngestReport:
    """Stage, publish and drop one batch, atomically.

    Called on its own it is a complete miniature run.  Called from :func:`run`
    inside the run's transaction it becomes a SAVEPOINT, so the run as a whole
    stays atomic and the gate is evaluated once over the run's totals
    (``enforce_gate=False`` here, then :func:`check_gate` at the end).
    """
    kind = KINDS.get(batch.kind)
    if kind is None:
        raise IngestValidationError(
            f"unknown batch kind {batch.kind!r}; expected one of {sorted(KINDS)}"
        )

    good = batch.good
    if good.height and "election_id" in good.columns:
        in_scope = good.filter(pl.col("election_id").is_in(parse.ELECTIONS_2022_GENERAL))
    else:
        in_scope = good
    dropped = batch.rows_out_of_scope + (good.height - in_scope.height)

    with db.transaction():
        db.execute(f"DROP TABLE IF EXISTS {kind.staging_table}")
        db.execute(kind.staging_ddl)
        if in_scope.height:
            db.copy_frame(kind.staging_table, kind.augment(in_scope))
        if batch.bad.height:
            _quarantine(db, batch.source_file, batch.bad)
        try:
            loaded = kind.publish(db)
        except psycopg.errors.UniqueViolation as exc:
            raise IngestValidationError(
                f"{batch.source_file}: duplicate natural key while publishing "
                f"{batch.kind}. If this is a (mun, zone, section, race, round) "
                "collision, check the CD_ELEICAO filter (amendment A3)."
            ) from exc
        db.execute(f"DROP TABLE IF EXISTS {kind.staging_table}")

        report = IngestReport.of(
            attempted=batch.rows_attempted,
            loaded=loaded,
            quarantined=batch.bad.height,
            out_of_scope=dropped,
        )
        if enforce_gate:
            check_gate(db, report)
    return report


def _quarantine(db: Database, source_file: str, bad: pl.DataFrame) -> None:
    frame = bad.select(
        pl.lit(source_file).alias("source_file"),
        pl.col("raw_line"),
        pl.col("reason"),
    )
    db.copy_frame("quarantine", frame)


def check_gate(db: Database, report: IngestReport) -> None:
    """The 0.01 % quarantine gate.  Stamps ``meta.gate_runs`` either way."""
    detail = report.summary()
    if report.quarantine_rate > QUARANTINE_GATE:
        # The raise aborts the caller's transaction, taking any in-transaction
        # audit row with it -- so the failure is recorded out of band.
        db.stamp_gate_out_of_band("quarantine", False, detail)
        raise QuarantineGateError(
            f"quarantine rate {report.quarantine_rate:.6%} exceeds "
            f"{QUARANTINE_GATE:.4%}: {detail}"
        )
    db.stamp_gate("quarantine", True, detail)


def validate_load(db: Database) -> None:
    """Post-load integrity checks the constraints cannot express."""
    orphan_sections = db.scalar(
        "SELECT count(*) FROM fact_votes f "
        "LEFT JOIN dim_section s ON s.id = f.section_id WHERE s.id IS NULL"
    )
    orphan_stats = db.scalar(
        "SELECT count(*) FROM fact_section_stats f "
        "LEFT JOIN dim_section s ON s.id = f.section_id WHERE s.id IS NULL"
    )
    orphan_candidates = db.scalar(
        "SELECT count(*) FROM fact_votes f "
        "LEFT JOIN dim_candidate c ON c.id = f.candidate_id "
        "WHERE f.candidate_id IS NOT NULL AND c.id IS NULL"
    )
    stray = sorted(set(distinct_election_ids(db)) - set(parse.ELECTIONS_2022_GENERAL))
    problems = []
    if orphan_sections:
        problems.append(f"{orphan_sections} fact_votes rows with no dim_section")
    if orphan_stats:
        problems.append(f"{orphan_stats} fact_section_stats rows with no dim_section")
    if orphan_candidates:
        problems.append(f"{orphan_candidates} fact_votes rows with no dim_candidate")
    if stray:
        problems.append(f"out-of-scope election ids present: {stray} (amendment A3)")
    detail = "; ".join(problems) if problems else "no orphans, elections in scope"
    if problems:
        db.stamp_gate_out_of_band("fk_integrity", False, detail)
        raise IngestValidationError(f"post-load validation failed: {detail}")
    db.stamp_gate("fk_integrity", True, detail)


def enrich_candidates(db: Database) -> int:
    """Fill ``dim_candidate.coalition`` from the candidate munzona rollup.

    The section-level file carries no coalition at all (Q13); the rollup is the
    only source.  Names are NOT overwritten -- ``dim_candidate.name`` is the
    urna name (``NM_VOTAVEL``) the voter actually saw, and the rollup's
    ``NM_CANDIDATO`` is a different, legal-name field.
    """
    return db.execute(
        """
        UPDATE dim_candidate d
        SET coalition = r.coalition
        FROM (
            SELECT DISTINCT ON (cand_uf, race, round, cand_number)
                   cand_uf, race, round, cand_number, coalition
            FROM (
                SELECT CASE WHEN race = 'PRES' THEN 'BR' ELSE uf END AS cand_uf,
                       race, round, cand_number, coalition
                FROM rollup_cand_munzona
                WHERE coalition IS NOT NULL
            ) t
            ORDER BY cand_uf, race, round, cand_number, coalition
        ) r
        WHERE d.uf = r.cand_uf AND d.race = r.race AND d.round = r.round
          AND d.number = r.cand_number
          AND d.coalition IS DISTINCT FROM r.coalition
        """
    )


# --------------------------------------------------------------------------
# Source planning
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceFile:
    """One CSV member of one archive, and the reader kind it needs."""

    kind: str
    archive: Path
    member: str
    uf_filter: str | None = None

    @property
    def label(self) -> str:
        return f"{self.archive.name}::{self.member}"

    def source(self) -> parse.ZipMember:
        return parse.ZipMember(self.archive, self.member)


#: archive stem -> (reader kind, member stem).  The BR member of each archive
#: is where presidential (and exterior) rows live; the per-UF members carry
#: state races only (A2).
_ARCHIVES: tuple[tuple[str, str, str], ...] = (
    ("votes", "votacao_secao_2022_{uf}", "votacao_secao_2022_{uf}"),
    ("detail", "detalhe_votacao_secao_2022", "detalhe_votacao_secao_2022_{uf}"),
    ("rollup_cand", "votacao_candidato_munzona_2022", "votacao_candidato_munzona_2022_{uf}"),
    ("rollup_party", "votacao_partido_munzona_2022", "votacao_partido_munzona_2022_{uf}"),
    ("rollup_detail", "detalhe_votacao_munzona_2022", "detalhe_votacao_munzona_2022_{uf}"),
)


def plan_sources(uf: str | None, raw_dir: Path | None = None) -> list[SourceFile]:
    """Every (archive, member) a run must read, in load order.

    Order matters: votes create the candidate dimension, detail fills the
    electorate, the rollups come last so ``enrich_candidates`` has them.
    """
    raw = Path(raw_dir or settings.raw_dir)
    ufs = [uf] if uf else list(_ALL_UFS)
    plan: list[SourceFile] = []
    for kind, archive_tpl, member_tpl in _ARCHIVES:
        for member_uf in [*ufs, "BR"]:
            archive = raw / (archive_tpl.format(uf=member_uf) + ".zip")
            member = member_tpl.format(uf=member_uf) + ".csv"
            plan.append(SourceFile(kind, archive, member, uf_filter=uf))
    missing = sorted({str(s.archive) for s in plan if not s.archive.exists()})
    if missing:
        raise IngestError(
            "missing raw archives -- run 01_download.py first:\n  "
            + "\n  ".join(missing)
        )
    return plan


_ALL_UFS: tuple[str, ...] = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS",
    "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC",
    "SE", "SP", "TO",
)


def stream_batches(
    source: SourceFile, batch_size: int = DEFAULT_BATCH_SIZE
) -> Iterator[Batch]:
    """Read one source file as a sequence of :class:`Batch`.

    A UF filter is applied to ``good`` here (not inside ``load_batch``) so the
    national members can be read once and sliced; the discarded rows are still
    counted, never silently forgotten.
    """
    kind = KINDS[source.kind]
    for good, bad in kind.reader(source.source(), batch_size=batch_size):
        dropped = 0
        if source.uf_filter:
            kept = good.filter(pl.col("uf") == source.uf_filter)
            dropped = good.height - kept.height
            good = kept
        yield Batch(
            kind=source.kind,
            source_file=source.label,
            good=good,
            bad=bad,
            rows_out_of_scope=dropped,
        )


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


@dataclass
class _Stage:
    label: str
    kind: str
    seconds: float = 0.0
    report: IngestReport = field(default_factory=lambda: EMPTY_REPORT)


def run(
    uf: str | None = None,
    *,
    database_url: str | None = None,
    raw_dir: Path | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    log: Callable[[str], None] | None = None,
    db: Database | None = None,
) -> IngestReport:
    """Ingest every source for ``uf`` (or all of Brazil) in ONE transaction."""
    say = log or (lambda _msg: None)
    plan = plan_sources(uf, raw_dir)
    owned = db is None
    database = db or connect(database_url)
    started = time.perf_counter()
    stages: list[_Stage] = []
    try:
        with database.transaction():
            database.reset_data()
            total = EMPTY_REPORT
            for source in plan:
                stage = _Stage(source.label, source.kind)
                t0 = time.perf_counter()
                for batch in stream_batches(source, batch_size):
                    stage.report = stage.report + load_batch(
                        database, batch, enforce_gate=False
                    )
                stage.seconds = time.perf_counter() - t0
                stages.append(stage)
                total = total + stage.report
                say(
                    f"  {stage.label}: {stage.report.summary()} "
                    f"[{stage.seconds:.1f}s]"
                )
            enriched = enrich_candidates(database)
            say(f"  dim_candidate coalitions filled: {enriched:,}")
            check_gate(database, total)
            validate_load(database)
            if not total.balanced:
                raise IngestValidationError(
                    "row accounting does not balance: " + total.summary()
                )
        elapsed = time.perf_counter() - started
        say(f"ingest committed in {elapsed:.1f}s -- {total.summary()}")
        database.execute("ANALYZE")
        return total
    finally:
        if owned:
            database.close()


# --------------------------------------------------------------------------
# Read-side helpers (used by the tests, 02_ingest.py and Task 5)
# --------------------------------------------------------------------------


def counts(db: Database) -> dict[str, int]:
    """Row count per data table."""
    return db.counts()


def read_quarantine(db: Database) -> pl.DataFrame:
    """Every quarantined row with its reason, in insertion order."""
    return db.fetch_frame(
        "SELECT source_file, raw_line, reason, at FROM quarantine ORDER BY id"
    )


def distinct_election_ids(db: Database) -> set[int]:
    """Every ``CD_ELEICAO`` present in the warehouse.

    Must never contain anything outside ``parse.ELECTIONS_2022_GENERAL`` --
    that is amendment A3's whole point.
    """
    tables = (
        "fact_votes",
        "fact_section_stats",
        "rollup_cand_munzona",
        "rollup_party_munzona",
        "rollup_detail_munzona",
    )
    sql = " UNION ".join(f"SELECT DISTINCT election_id FROM {t}" for t in tables)
    return {int(row[0]) for row in db.fetch(sql) if row[0] is not None}


def read_gate_runs(db: Database, gate: str | None = None) -> pl.DataFrame:
    """The gate audit trail (``meta.gate_runs``), newest first."""
    return db_gate_runs(db, gate)
