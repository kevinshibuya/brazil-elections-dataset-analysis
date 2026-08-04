"""Postgres connection, schema DDL and bulk-load helpers.

Everything that knows the *shape* of the warehouse lives here; `ingest.py`
knows how to fill it.  The design notes that matter downstream:

SURROGATE KEYS ARE COMPUTED, NOT SEQUENTIAL
    ``dim_section.id`` and ``dim_candidate.id`` are pure functions of their
    natural keys (see :func:`section_id_expr` / :func:`candidate_id_expr` in
    ``ingest.py``).  Three consequences, all deliberate:
      * a re-run from scratch produces byte-identical ids, so exports are
        stable and diffable;
      * fact rows can be built without a dimension lookup join, which is what
        makes a COPY-based load viable at 100 M rows;
      * an id is decodable by hand when debugging
        (``30180000100589`` = municipality 03018, zone 1, section 589).

ATOMICITY
    A run is one transaction: ``reset`` (TRUNCATE, which is transactional in
    Postgres) then every batch, then the gates.  Anything raising anywhere in
    there rolls the whole run back, so a failed run leaves the previous good
    state untouched.  Staging tables are UNLOGGED and dropped as soon as their
    batch has been published (the disk budget in the plan is tight).

PARTITIONING
    ``fact_votes`` is LIST-partitioned by ``race``; every analysis query in
    later slices is race-scoped, so partition pruning is free selectivity.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import polars as pl
import psycopg

from .config import settings

__all__ = [
    "Database",
    "connect",
    "REGIONS",
    "UF_ORDER",
    "DATA_TABLES",
    "SECTION_ID_ZONE_STRIDE",
    "SECTION_ID_MUN_STRIDE",
    "CANDIDATE_ID_NUMBER_STRIDE",
]

#: UF -> macro-region.  ``ZZ`` (exterior) is not a Brazilian region; it gets
#: its own code so the dashboard can style it separately.
REGIONS: dict[str, str] = {
    "AC": "N", "AM": "N", "AP": "N", "PA": "N", "RO": "N", "RR": "N", "TO": "N",
    "AL": "NE", "BA": "NE", "CE": "NE", "MA": "NE", "PB": "NE", "PE": "NE",
    "PI": "NE", "RN": "NE", "SE": "NE",
    "DF": "CO", "GO": "CO", "MS": "CO", "MT": "CO",
    "ES": "SE", "MG": "SE", "RJ": "SE", "SP": "SE",
    "PR": "S", "RS": "S", "SC": "S",
    "ZZ": "EX",
}

#: Every UF that can appear in a row, in a FROZEN order: the index is part of
#: ``dim_candidate.id``.  ``BR`` is the pseudo-UF national (presidential)
#: candidates are keyed under; ``ZZ`` is the exterior.  Appending is safe,
#: reordering is not.
UF_ORDER: tuple[str, ...] = (
    "AC", "AL", "AM", "AP", "BA", "BR", "CE", "DF", "ES", "GO", "MA", "MG",
    "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS",
    "SC", "SE", "SP", "TO", "ZZ",
)
UF_INDEX: dict[str, int] = {uf: i for i, uf in enumerate(UF_ORDER)}

#: Races, in a FROZEN order: the index is part of ``dim_candidate.id``.
RACE_ORDER: tuple[str, ...] = ("PRES", "GOV", "SEN", "DEPFED", "DEPEST", "DEPDIS")
RACE_INDEX: dict[str, int] = {race: i for i, race in enumerate(RACE_ORDER)}

#: ``section_id = mun_code * 10^9 + zone * 10^5 + section``.
SECTION_ID_MUN_STRIDE = 1_000_000_000
SECTION_ID_ZONE_STRIDE = 100_000
#: ``candidate_id = ((uf_idx * 8 + race_idx) * 4 + round) * 10^5 + number``.
CANDIDATE_ID_NUMBER_STRIDE = 100_000
CANDIDATE_ID_ROUND_STRIDE = 4
CANDIDATE_ID_RACE_STRIDE = 8

#: Every table holding run data, in FK-safe truncation order.
DATA_TABLES: tuple[str, ...] = (
    "quarantine",
    "fact_votes",
    "fact_section_stats",
    "rollup_cand_munzona",
    "rollup_party_munzona",
    "rollup_detail_munzona",
    "dim_candidate",
    "dim_section",
    "dim_municipality",
)


def region_case(col: str = "uf") -> str:
    """SQL CASE mapping a UF column to its macro-region."""
    whens = " ".join(f"WHEN '{uf}' THEN '{region}'" for uf, region in REGIONS.items())
    return f"CASE {col} {whens} ELSE NULL END"


# --------------------------------------------------------------------------
# DDL
# --------------------------------------------------------------------------

DDL: tuple[str, ...] = (
    "CREATE SCHEMA IF NOT EXISTS meta",
    "CREATE SCHEMA IF NOT EXISTS staging",
    """
    CREATE TABLE IF NOT EXISTS dim_municipality (
        code      TEXT PRIMARY KEY CHECK (code ~ '^[0-9]{5}$'),
        name      TEXT,
        uf        TEXT NOT NULL,
        region    TEXT,
        ibge_code INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dim_section (
        id         BIGINT PRIMARY KEY,
        uf         TEXT    NOT NULL,
        mun_code   TEXT    NOT NULL REFERENCES dim_municipality(code),
        zone       INTEGER NOT NULL,
        section    INTEGER NOT NULL,
        aptos_r1   BIGINT,
        aptos_r2   BIGINT,
        agg_secoes INTEGER,
        UNIQUE (uf, mun_code, zone, section)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dim_candidate (
        id           INTEGER  PRIMARY KEY,
        uf           TEXT     NOT NULL,
        race         TEXT     NOT NULL,
        round        SMALLINT NOT NULL,
        number       INTEGER  NOT NULL,
        name         TEXT,
        party_number SMALLINT,
        coalition    TEXT,
        UNIQUE (uf, race, round, number)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_votes (
        section_id   BIGINT   NOT NULL REFERENCES dim_section(id),
        race         TEXT     NOT NULL,
        round        SMALLINT NOT NULL,
        votavel_kind TEXT     NOT NULL,
        candidate_id INTEGER  REFERENCES dim_candidate(id),
        party_number SMALLINT,
        votes        BIGINT   NOT NULL CHECK (votes >= 0),
        election_id  SMALLINT NOT NULL,
        CONSTRAINT fact_votes_candidate_id_only_for_candidates
            CHECK ((candidate_id IS NOT NULL) = (votavel_kind = 'candidate')),
        CONSTRAINT fact_votes_party_number_scope
            CHECK (party_number IS NULL OR votavel_kind IN ('candidate', 'legenda')),
        CONSTRAINT fact_votes_key UNIQUE NULLS NOT DISTINCT
            (race, round, section_id, votavel_kind, candidate_id, party_number)
    ) PARTITION BY LIST (race)
    """,
    *(
        f"CREATE TABLE IF NOT EXISTS fact_votes_{race.lower()} "
        f"PARTITION OF fact_votes FOR VALUES IN ('{race}')"
        for race in RACE_ORDER
    ),
    "CREATE TABLE IF NOT EXISTS fact_votes_other PARTITION OF fact_votes DEFAULT",
    """
    CREATE TABLE IF NOT EXISTS fact_section_stats (
        section_id     BIGINT   NOT NULL REFERENCES dim_section(id),
        race           TEXT     NOT NULL,
        round          SMALLINT NOT NULL,
        aptos          BIGINT,
        comparecimento BIGINT,
        abstentions    BIGINT,
        blank          BIGINT,
        null_votes     BIGINT,
        election_id    SMALLINT NOT NULL,
        PRIMARY KEY (section_id, race, round)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rollup_cand_munzona (
        uf                TEXT NOT NULL,
        mun_code          TEXT NOT NULL,
        mun_name          TEXT,
        zone              INTEGER NOT NULL,
        race              TEXT NOT NULL,
        round             SMALLINT NOT NULL,
        cand_seq          TEXT,
        cand_number       INTEGER NOT NULL,
        cand_name         TEXT,
        cand_urna_name    TEXT,
        party_number      SMALLINT,
        party_sigla       TEXT,
        federation_number INTEGER,
        federation_sigla  TEXT,
        coalition         TEXT,
        votes_nominal     BIGINT,
        votes_nominal_valid BIGINT,
        in_transit        BOOLEAN,
        situation         TEXT,
        election_id       SMALLINT NOT NULL,
        CONSTRAINT rollup_cand_munzona_key UNIQUE NULLS NOT DISTINCT
            (mun_code, zone, race, round, cand_number, in_transit)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rollup_party_munzona (
        uf                TEXT NOT NULL,
        mun_code          TEXT NOT NULL,
        mun_name          TEXT,
        zone              INTEGER NOT NULL,
        race              TEXT NOT NULL,
        round             SMALLINT NOT NULL,
        party_number      SMALLINT,
        party_sigla       TEXT,
        federation_number INTEGER,
        federation_sigla  TEXT,
        coalition         TEXT,
        votes_legenda_valid       BIGINT,
        -- A5: legenda votes annulled after the election (the mirror image of
        -- the `annulled_candidacy` reconciliation category -- see reconcile.py)
        votes_legenda_annulled    BIGINT,
        votes_legenda_anul_subjud BIGINT,
        votes_nom_convr_leg_valid BIGINT,
        votes_total_leg_valid     BIGINT,
        votes_nominal_valid       BIGINT,
        in_transit        BOOLEAN,
        election_id       SMALLINT NOT NULL,
        CONSTRAINT rollup_party_munzona_key UNIQUE NULLS NOT DISTINCT
            (mun_code, zone, race, round, party_number, in_transit)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rollup_detail_munzona (
        uf                 TEXT NOT NULL,
        mun_code           TEXT NOT NULL,
        mun_name           TEXT,
        zone               INTEGER NOT NULL,
        race               TEXT NOT NULL,
        round              SMALLINT NOT NULL,
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
        election_id        SMALLINT NOT NULL,
        CONSTRAINT rollup_detail_munzona_key UNIQUE NULLS NOT DISTINCT
            (mun_code, zone, race, round, in_transit)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quarantine (
        id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        source_file TEXT NOT NULL,
        raw_line    TEXT,
        reason      TEXT NOT NULL,
        at          TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta.gate_runs (
        id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        gate   TEXT NOT NULL,
        ok     BOOLEAN NOT NULL,
        detail TEXT,
        at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS fact_votes_candidate_idx ON fact_votes (candidate_id)",
    "CREATE INDEX IF NOT EXISTS dim_section_mun_idx ON dim_section (mun_code, zone)",
    "CREATE INDEX IF NOT EXISTS rollup_cand_munzona_mun_idx "
    "ON rollup_cand_munzona (mun_code, race, round)",
    "CREATE INDEX IF NOT EXISTS rollup_party_munzona_mun_idx "
    "ON rollup_party_munzona (mun_code, race, round)",
    "CREATE INDEX IF NOT EXISTS rollup_detail_munzona_mun_idx "
    "ON rollup_detail_munzona (mun_code, race, round)",
)


# --------------------------------------------------------------------------
# Connection wrapper
# --------------------------------------------------------------------------


@dataclass
class Database:
    """A thin, explicit wrapper over a psycopg connection.

    The connection is opened in ``autocommit`` mode: every transaction in this
    codebase is an explicit ``with db.transaction():`` block, and nesting one
    inside another yields a SAVEPOINT.  That is what lets ``ingest.run`` wrap a
    whole multi-batch load in one atomic unit while ``ingest.load_batch``
    remains atomic on its own.
    """

    conn: psycopg.Connection
    url: str

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def connect(cls, url: str | None = None) -> "Database":
        url = url or settings.database_url
        return cls(psycopg.connect(url, autocommit=True), url)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- statements --------------------------------------------------------

    def transaction(self):
        return self.conn.transaction()

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> int:
        """Run a statement; return the number of rows affected."""
        with self.conn.cursor() as cur:
            cur.execute(sql, params)  # type: ignore[arg-type]
            return cur.rowcount

    def executemany_sql(self, statements: Iterable[str]) -> None:
        with self.conn.cursor() as cur:
            for sql in statements:
                cur.execute(sql)  # type: ignore[arg-type]

    def scalar(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)  # type: ignore[arg-type]
            row = cur.fetchone()
        return None if row is None else row[0]

    def fetch(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)  # type: ignore[arg-type]
            return cur.fetchall()

    def fetch_frame(self, sql: str, params: Sequence[Any] | None = None) -> pl.DataFrame:
        """Query result as a polars frame (column names preserved)."""
        with self.conn.cursor() as cur:
            cur.execute(sql, params)  # type: ignore[arg-type]
            columns = [d.name for d in cur.description or ()]
            rows = cur.fetchall()
        if not columns:
            return pl.DataFrame()
        data = {name: [row[i] for row in rows] for i, name in enumerate(columns)}
        return pl.DataFrame(data)

    # -- bulk load ---------------------------------------------------------

    def copy_frame(self, table: str, frame: pl.DataFrame) -> int:
        """COPY a polars frame into ``table``, matching on column names.

        CSV is the wire format: polars writes it in Rust (one buffer per
        batch), Postgres parses it in C, and the ``NULL``/quoting conventions
        of the two agree -- an unquoted empty field is NULL, which is exactly
        how polars serialises a null.
        """
        if frame.height == 0:
            return 0
        buf = io.BytesIO()
        frame.write_csv(buf, include_header=False)
        columns = ", ".join(f'"{c}"' for c in frame.columns)
        stmt = f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv)"
        with self.conn.cursor() as cur:
            with cur.copy(stmt) as copy:  # type: ignore[arg-type]
                copy.write(buf.getvalue())
        return frame.height

    # -- schema ------------------------------------------------------------

    def create_schema(self) -> None:
        self.executemany_sql(DDL)

    def reset_data(self) -> None:
        """Empty every data table.  TRUNCATE is transactional in Postgres, so
        calling this inside a run's transaction keeps the previous good state
        visible until (and unless) the run commits."""
        tables = ", ".join(DATA_TABLES)
        self.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")

    def counts(self) -> dict[str, int]:
        """Row count per data table."""
        selects = ", ".join(
            f"(SELECT count(*) FROM {t}) AS {t}" for t in sorted(DATA_TABLES)
        )
        row = self.fetch(f"SELECT {selects}")[0]
        return dict(zip(sorted(DATA_TABLES), (int(v) for v in row)))

    # -- gates -------------------------------------------------------------

    def stamp_gate(self, gate: str, ok: bool, detail: str) -> None:
        """Record a gate outcome in the CURRENT transaction."""
        self.execute(
            "INSERT INTO meta.gate_runs (gate, ok, detail) VALUES (%s, %s, %s)",
            (gate, ok, detail),
        )

    def stamp_gate_out_of_band(self, gate: str, ok: bool, detail: str) -> None:
        """Record a gate outcome on a SEPARATE connection.

        A failing gate aborts the run's transaction, which would take the
        evidence of the failure down with it.  This writes the record where
        the rollback cannot reach it.
        """
        try:
            with psycopg.connect(self.url, autocommit=True) as conn:
                conn.execute(
                    "INSERT INTO meta.gate_runs (gate, ok, detail) VALUES (%s, %s, %s)",
                    (gate, ok, detail),
                )
        except psycopg.Error:  # pragma: no cover - best effort audit trail
            pass


def connect(url: str | None = None) -> Database:
    return Database.connect(url)


def gate_runs(db: Database, gate: str | None = None) -> pl.DataFrame:
    """The gate audit trail, newest first."""
    where = "WHERE gate = %s" if gate else ""
    params = (gate,) if gate else None
    return db.fetch_frame(
        f"SELECT gate, ok, detail, at FROM meta.gate_runs {where} ORDER BY id DESC",
        params,
    )
