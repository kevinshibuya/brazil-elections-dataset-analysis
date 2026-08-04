"""The reconciliation gate: our aggregates vs TSE's published totals.

    from observatory import ingest, reconcile
    reconcile.run_gate(ingest.connect())

WHY THREE COMPARISONS AND NOT ONE
    A single candidate-keyed comparison is structurally impossible.  TSE
    publishes the municipality totals in three different files, and none of
    them carries everything:

      * ``votacao_candidato_munzona``  -- nominal votes per candidate.  No
        blank/null, no legenda, no turnout.
      * ``votacao_partido_munzona``    -- legenda votes per party.
      * ``detalhe_votacao_munzona``    -- aptos / comparecimento / abstenções
        / brancos / nulos.

    So the gate runs one comparison per category, each with tolerance ZERO,
    plus an internal cross-check that our own two section-level sources agree
    with each other about blank and null votes.

WHAT "TOLERANCE ZERO" MEANS HERE (amendment A4)
    Zero for every key present in BOTH sources -- and on real data that holds
    exactly, once one honest phenomenon is named rather than absorbed:

    Some candidates received votes at section level and have NO ROW AT ALL in
    the rollup.  Their candidacies were rejected after the election, and TSE
    excluded their votes from the official totals while the urna files still
    carry them.  In RR that is 22 candidates / 137 (mun, race, round, cand)
    keys / 27 160 votes, dominated by senate candidate 333 HELDER GIRÃO
    BARRETO (25 554 votes, third place, present in every section).  The same
    happens to legenda votes of a rejected party list (RR: party 40 in
    DEPFED, 10 municipalities, 95 votes).

    Those are recorded in their own category, ``annulled_candidacy``, with
    ``theirs`` NULL, and are NON-FATAL.  The category means exactly one thing:
    *absent from the rollup entirely*.  It is never a place to put a numeric
    disagreement.

    The single condition under which it IS fatal: if those same votes turn up
    as VALID anywhere in a rollup (same race/round/number, same UF scope),
    then "annulled candidacy" is the wrong story and we have lost data.  Those
    rows go to the fatal category ``annulled_but_valid``.

WHAT ELSE IS SURFACED, NOT RECONCILED
    TSE's own two rollups disagree with each other.  For RR DEPEST the
    candidato file reports 286 631 nominal votes while the partido file's
    nominal universe is 282 365 (and 282 892 once its annulled-nominal
    columns are added).  We pick no winner: every such key is reported under
    the informational category ``rollup_disagreement``.

FAILURE MODE
    A fatal mismatch raises :class:`ReconciliationError` -- but only AFTER the
    FULL report is written.  Failing on the first diff would throw away the
    diagnostic.  ``reconciliation_report`` is the artifact; the exception is
    just the exit code.

TRANSACTIONS
    ``run_gate`` expects an autocommit connection and is meant to run AFTER
    the ingest transaction has committed: the report must survive the failure
    it is describing.  Wrapping it in an outer transaction would make the
    report a savepoint and lose it on the raise.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from .db import Database

__all__ = [
    "CATEGORIES",
    "FATAL_CATEGORIES",
    "CategoryResult",
    "GateResult",
    "ReconciliationError",
    "annulled_candidacies",
    "read_report",
    "run_gate",
]

GATE_NAME = "reconciliation"

#: Every category the gate can write, in report order.
CATEGORIES: tuple[str, ...] = (
    "nominal",              # section candidate votes vs votacao_candidato_munzona
    "legenda",              # section legenda votes vs votacao_partido_munzona
    "turnout",              # section stats vs detalhe_votacao_munzona
    "internal_blank_null",  # our two section-level sources vs each other
    "annulled_candidacy",   # A4: present at section level, absent from the rollup
    "annulled_but_valid",   # A4: ... but valid somewhere -- the story is wrong
    "rollup_disagreement",  # TSE vs TSE, informational
    "empty_warehouse",      # nothing to reconcile: a vacuous pass is not a pass
)

#: Categories whose presence fails the gate.  ``annulled_candidacy`` and
#: ``rollup_disagreement`` are deliberately absent -- they are findings, not
#: defects in our pipeline.
FATAL_CATEGORIES: frozenset[str] = frozenset(
    {
        "nominal",
        "legenda",
        "turnout",
        "internal_blank_null",
        "annulled_but_valid",
        "empty_warehouse",
    }
)

REPORT_TABLE = "reconciliation_report"

_REPORT_DDL = (
    f"""
    CREATE TABLE IF NOT EXISTS {REPORT_TABLE} (
        id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        mun_code TEXT,
        race     TEXT,
        round    SMALLINT,
        category TEXT NOT NULL,
        key      TEXT,
        ours     BIGINT,
        theirs   BIGINT,
        diff     BIGINT,
        at       TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    f"CREATE INDEX IF NOT EXISTS {REPORT_TABLE}_category_idx "
    f"ON {REPORT_TABLE} (category)",
)

_REPORT_COLUMNS = "mun_code, race, round, category, key, ours, theirs, diff"


class ReconciliationError(RuntimeError):
    """Our aggregates do not equal TSE's published totals."""


# --------------------------------------------------------------------------
# The comparisons
#
# Each is expressed as SQL that SELECTs report rows for MISMATCHES ONLY, so a
# clean run inserts nothing.  Everything stays server-side: nationally these
# join ~100 M fact rows, which is not a thing to pull into Python.
# --------------------------------------------------------------------------

#: Our nominal votes per (municipality, race, round, candidate number).
#: ``dim_candidate.number`` -- the fact row does not carry it.
_OURS_NOMINAL = """
    SELECT s.mun_code, f.race, f.round, c.number AS key_num,
           SUM(f.votes)::BIGINT AS ours, count(*)::BIGINT AS n_ours
    FROM fact_votes f
    JOIN dim_section   s ON s.id = f.section_id
    JOIN dim_candidate c ON c.id = f.candidate_id
    WHERE f.votavel_kind = 'candidate'
    GROUP BY 1, 2, 3, 4
"""

#: TSE's nominal votes, aggregated over ZONE and over the transit flag (Q24:
#: a (mun, race, round) key appears once per zone and once per ST_VOTO_EM_-
#: TRANSITO value).  ``votes_nominal`` -- NOT ``votes_nominal_valid``: the
#: section file counts every vote the urna recorded, including the ones TSE
#: later annulled.
_THEIRS_NOMINAL = """
    SELECT mun_code, race, round, cand_number AS key_num,
           SUM(votes_nominal)::BIGINT AS theirs, count(*)::BIGINT AS n_theirs
    FROM rollup_cand_munzona
    WHERE cand_number IS NOT NULL
    GROUP BY 1, 2, 3, 4
"""

_NOMINAL_JOIN = f"""
    joined AS (
        SELECT * FROM ({_OURS_NOMINAL}) o
        FULL JOIN ({_THEIRS_NOMINAL}) t USING (mun_code, race, round, key_num)
    )
"""

#: An annulled candidacy is annulled for the whole race: the candidate must be
#: absent from the rollup EVERYWHERE in scope.  Presidential candidates are
#: national, every other race is scoped to one UF (deputy numbers repeat
#: across states).
_CAND_VALID_ELSEWHERE = """
    EXISTS (
        SELECT 1 FROM rollup_cand_munzona r
        WHERE r.race = j.race AND r.round = j.round
          AND r.cand_number = j.key_num
          AND COALESCE(r.votes_nominal_valid, 0) > 0
          AND (j.race = 'PRES' OR r.uf = m.uf)
    )
"""

#: Both nominal categories come out of ONE pass over the join: a numeric
#: disagreement on a key TSE published, and a key TSE published no row for.
_NOMINAL_ROWS = f"""
    WITH {_NOMINAL_JOIN}
    SELECT mun_code, race, round, 'nominal', 'cand:' || key_num,
           COALESCE(ours, 0), theirs, COALESCE(ours, 0) - COALESCE(theirs, 0)
    FROM joined
    WHERE n_theirs IS NOT NULL
      AND COALESCE(ours, 0) IS DISTINCT FROM theirs
    UNION ALL
    SELECT j.mun_code, j.race, j.round,
           CASE WHEN {_CAND_VALID_ELSEWHERE}
                THEN 'annulled_but_valid' ELSE 'annulled_candidacy' END,
           'cand:' || j.key_num, j.ours, NULL::BIGINT, j.ours
    FROM joined j
    JOIN dim_municipality m ON m.code = j.mun_code
    WHERE j.n_theirs IS NULL AND COALESCE(j.ours, 0) > 0
"""

#: Legenda: our raw section counts vs valid + annulled + sub judice (A5).  The
#: valid column alone is not comparable -- votes for a rejected party list are
#: parked in the annulled ones (Q27).
_OURS_LEGENDA = """
    SELECT s.mun_code, f.race, f.round, f.party_number AS key_num,
           SUM(f.votes)::BIGINT AS ours, count(*)::BIGINT AS n_ours
    FROM fact_votes f
    JOIN dim_section s ON s.id = f.section_id
    WHERE f.votavel_kind = 'legenda' AND f.party_number IS NOT NULL
    GROUP BY 1, 2, 3, 4
"""

_THEIRS_LEGENDA = """
    SELECT mun_code, race, round, party_number AS key_num,
           SUM(COALESCE(votes_legenda_valid, 0)
               + COALESCE(votes_legenda_annulled, 0)
               + COALESCE(votes_legenda_anul_subjud, 0))::BIGINT AS theirs,
           count(*)::BIGINT AS n_theirs
    FROM rollup_party_munzona
    WHERE party_number IS NOT NULL
    GROUP BY 1, 2, 3, 4
"""

_LEGENDA_JOIN = f"""
    joined AS (
        SELECT * FROM ({_OURS_LEGENDA}) o
        FULL JOIN ({_THEIRS_LEGENDA}) t USING (mun_code, race, round, key_num)
    )
"""

_PARTY_VALID_ELSEWHERE = """
    EXISTS (
        SELECT 1 FROM rollup_party_munzona r
        WHERE r.race = j.race AND r.round = j.round
          AND r.party_number = j.key_num
          AND COALESCE(r.votes_legenda_valid, 0) > 0
          AND r.uf = m.uf
    )
"""

_LEGENDA_ROWS = f"""
    WITH {_LEGENDA_JOIN}
    SELECT mun_code, race, round, 'legenda', 'legenda:' || key_num,
           COALESCE(ours, 0), theirs, COALESCE(ours, 0) - COALESCE(theirs, 0)
    FROM joined
    WHERE n_theirs IS NOT NULL
      AND COALESCE(ours, 0) IS DISTINCT FROM theirs
    UNION ALL
    SELECT j.mun_code, j.race, j.round,
           CASE WHEN {_PARTY_VALID_ELSEWHERE}
                THEN 'annulled_but_valid' ELSE 'annulled_candidacy' END,
           'legenda:' || j.key_num, j.ours, NULL::BIGINT, j.ours
    FROM joined j
    JOIN dim_municipality m ON m.code = j.mun_code
    WHERE j.n_theirs IS NULL AND COALESCE(j.ours, 0) > 0
"""

#: Turnout: five metrics per (mun, race, round), compared one at a time so the
#: report says WHICH number is wrong.
_TURNOUT_METRICS: tuple[str, ...] = (
    "aptos",
    "comparecimento",
    "abstentions",
    "blank",
    "null_votes",
)

_OURS_TURNOUT = """
    SELECT s.mun_code, f.race, f.round,
           SUM(f.aptos)::BIGINT          AS o_aptos,
           SUM(f.comparecimento)::BIGINT AS o_comparecimento,
           SUM(f.abstentions)::BIGINT    AS o_abstentions,
           SUM(f.blank)::BIGINT          AS o_blank,
           SUM(f.null_votes)::BIGINT     AS o_null_votes,
           count(*)::BIGINT              AS n_ours
    FROM fact_section_stats f
    JOIN dim_section s ON s.id = f.section_id
    GROUP BY 1, 2, 3
"""

_THEIRS_TURNOUT = """
    SELECT mun_code, race, round,
           SUM(aptos)::BIGINT          AS t_aptos,
           SUM(comparecimento)::BIGINT AS t_comparecimento,
           SUM(abstentions)::BIGINT    AS t_abstentions,
           SUM(blank)::BIGINT          AS t_blank,
           SUM(null_votes)::BIGINT     AS t_null_votes,
           count(*)::BIGINT            AS n_theirs
    FROM rollup_detail_munzona
    GROUP BY 1, 2, 3
"""

_TURNOUT_JOIN = f"""
    joined AS (
        SELECT * FROM ({_OURS_TURNOUT}) o
        FULL JOIN ({_THEIRS_TURNOUT}) t USING (mun_code, race, round)
    )
"""

_TURNOUT_ROWS = f"""
    WITH {_TURNOUT_JOIN}
    SELECT j.mun_code, j.race, j.round, 'turnout', metric.name,
           metric.ours, metric.theirs, metric.ours - metric.theirs
    FROM joined j
    CROSS JOIN LATERAL (VALUES
        {", ".join(f"('{m}', j.o_{m}, j.t_{m})" for m in _TURNOUT_METRICS)}
    ) AS metric(name, ours, theirs)
    WHERE metric.ours IS DISTINCT FROM metric.theirs
"""

#: Internal cross-check: the votação file's blank/null votável rows must equal
#: the detalhe file's blank/null counts.  Both are ours; if they disagree we
#: mis-parsed one of them and no external comparison would catch it.
_INTERNAL_ROWS = """
    WITH votes AS (
        SELECT s.mun_code, f.race, f.round,
               COALESCE(SUM(f.votes) FILTER (WHERE f.votavel_kind = 'blank'), 0)::BIGINT AS v_blank,
               COALESCE(SUM(f.votes) FILTER (WHERE f.votavel_kind = 'null'), 0)::BIGINT  AS v_null_votes
        FROM fact_votes f
        JOIN dim_section s ON s.id = f.section_id
        GROUP BY 1, 2, 3
    ), stats AS (
        SELECT s.mun_code, f.race, f.round,
               COALESCE(SUM(f.blank), 0)::BIGINT      AS s_blank,
               COALESCE(SUM(f.null_votes), 0)::BIGINT AS s_null_votes
        FROM fact_section_stats f
        JOIN dim_section s ON s.id = f.section_id
        GROUP BY 1, 2, 3
    ), joined AS (
        SELECT * FROM votes FULL JOIN stats USING (mun_code, race, round)
    )
    SELECT j.mun_code, j.race, j.round, 'internal_blank_null', metric.name,
           metric.ours, metric.theirs, metric.ours - metric.theirs
    FROM joined j
    CROSS JOIN LATERAL (VALUES
        ('blank',      COALESCE(j.v_blank, 0),      COALESCE(j.s_blank, 0)),
        ('null_votes', COALESCE(j.v_null_votes, 0), COALESCE(j.s_null_votes, 0))
    ) AS metric(name, ours, theirs)
    WHERE metric.ours IS DISTINCT FROM metric.theirs
"""

#: TSE vs TSE, over keys BOTH files cover.  ``ours`` is the candidato file's
#: nominal total, ``theirs`` the partido file's -- neither is ours, and we
#: pick no winner.  Informational.
_DISAGREEMENT_ROWS = """
    WITH cand AS (
        SELECT mun_code, race, round, SUM(votes_nominal)::BIGINT AS total
        FROM rollup_cand_munzona GROUP BY 1, 2, 3
    ), party AS (
        SELECT mun_code, race, round, SUM(votes_nominal_valid)::BIGINT AS total
        FROM rollup_party_munzona GROUP BY 1, 2, 3
    )
    SELECT cand.mun_code, cand.race, cand.round, 'rollup_disagreement',
           'nominal_cand_vs_party', cand.total, party.total,
           cand.total - party.total
    FROM cand JOIN party USING (mun_code, race, round)
    WHERE cand.total IS DISTINCT FROM party.total
"""


# --------------------------------------------------------------------------
# Check registry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Check:
    """One SELECT of report rows, plus how many keys it compared."""

    name: str
    rows_sql: str
    keys_sql: str


_KEYS_NOMINAL = f"SELECT count(*) FROM ({_OURS_NOMINAL}) o " \
                f"FULL JOIN ({_THEIRS_NOMINAL}) t USING (mun_code, race, round, key_num)"
_KEYS_LEGENDA = f"SELECT count(*) FROM ({_OURS_LEGENDA}) o " \
                f"FULL JOIN ({_THEIRS_LEGENDA}) t USING (mun_code, race, round, key_num)"
_KEYS_TURNOUT = f"SELECT count(*) * {len(_TURNOUT_METRICS)} FROM ({_OURS_TURNOUT}) o " \
                f"FULL JOIN ({_THEIRS_TURNOUT}) t USING (mun_code, race, round)"
_KEYS_INTERNAL = """
    SELECT count(*) * 2 FROM (
        SELECT s.mun_code, f.race, f.round FROM fact_votes f
        JOIN dim_section s ON s.id = f.section_id GROUP BY 1, 2, 3
        UNION
        SELECT s.mun_code, f.race, f.round FROM fact_section_stats f
        JOIN dim_section s ON s.id = f.section_id GROUP BY 1, 2, 3
    ) k
"""
_KEYS_DISAGREEMENT = """
    SELECT count(*) FROM (
        SELECT mun_code, race, round FROM rollup_cand_munzona GROUP BY 1, 2, 3
        INTERSECT
        SELECT mun_code, race, round FROM rollup_party_munzona GROUP BY 1, 2, 3
    ) k
"""

#: One entry per COMPARISON (not per category: the nominal and legenda
#: comparisons each emit two categories from a single pass over their join).
CHECKS: tuple[_Check, ...] = (
    _Check("nominal", _NOMINAL_ROWS, _KEYS_NOMINAL),
    _Check("legenda", _LEGENDA_ROWS, _KEYS_LEGENDA),
    _Check("turnout", _TURNOUT_ROWS, _KEYS_TURNOUT),
    _Check("internal_blank_null", _INTERNAL_ROWS, _KEYS_INTERNAL),
    _Check("rollup_disagreement", _DISAGREEMENT_ROWS, _KEYS_DISAGREEMENT),
)


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryResult:
    """What one category found.  ``votes`` is the total |diff| it accounts for."""

    category: str
    rows: int
    votes: int
    fatal: bool

    def __str__(self) -> str:
        mark = "FAIL" if (self.fatal and self.rows) else "ok"
        return (
            f"{self.category}: {self.rows} row(s), {self.votes:,} votes [{mark}]"
        )


@dataclass(frozen=True)
class GateResult:
    """Outcome of a gate run.  ``ok`` is the whole point."""

    ok: bool
    categories: tuple[CategoryResult, ...]
    keys_compared: dict[str, int]

    def category(self, name: str) -> CategoryResult:
        for result in self.categories:
            if result.category == name:
                return result
        return CategoryResult(name, 0, 0, name in FATAL_CATEGORIES)

    @property
    def fatal_rows(self) -> int:
        return sum(c.rows for c in self.categories if c.fatal)

    def summary(self) -> str:
        keys = ", ".join(f"{k}={v:,}" for k, v in sorted(self.keys_compared.items()))
        cats = "; ".join(str(c) for c in self.categories) or "no mismatches"
        return f"{'PASS' if self.ok else 'FAIL'} — keys compared: {keys} — {cats}"


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def ensure_report_table(db: Database) -> None:
    db.executemany_sql(_REPORT_DDL)


def run_gate(db: Database, *, raise_on_failure: bool = True) -> GateResult:
    """Reconcile the warehouse against TSE's published totals.

    Writes every mismatch to ``reconciliation_report`` FIRST, stamps
    ``meta.gate_runs``, and only then raises :class:`ReconciliationError` if
    anything fatal was found.  Pass ``raise_on_failure=False`` to inspect the
    result instead (the report is written either way).
    """
    ensure_report_table(db)
    db.execute(f"TRUNCATE {REPORT_TABLE} RESTART IDENTITY")

    keys_compared: dict[str, int] = {}
    for check in CHECKS:
        keys_compared[check.name] = int(db.scalar(check.keys_sql) or 0)
        db.execute(
            f"INSERT INTO {REPORT_TABLE} ({_REPORT_COLUMNS}) {check.rows_sql}"
        )

    if not db.scalar("SELECT EXISTS (SELECT 1 FROM fact_votes)"):
        db.execute(
            f"INSERT INTO {REPORT_TABLE} (category, key, ours, theirs, diff) "
            "VALUES ('empty_warehouse', 'fact_votes', 0, NULL, NULL)"
        )

    counted = {
        str(row[0]): (int(row[1]), int(row[2] or 0))
        for row in db.fetch(
            f"SELECT category, count(*), SUM(ABS(COALESCE(diff, 0))) "
            f"FROM {REPORT_TABLE} GROUP BY 1"
        )
    }
    categories = tuple(
        CategoryResult(name, *counted[name], name in FATAL_CATEGORIES)
        for name in CATEGORIES
        if name in counted
    )
    result = GateResult(
        ok=not any(c.rows for c in categories if c.fatal),
        categories=categories,
        keys_compared=keys_compared,
    )

    if result.ok:
        db.stamp_gate(GATE_NAME, True, result.summary())
        return result

    detail = result.summary()
    db.stamp_gate_out_of_band(GATE_NAME, False, detail)
    if raise_on_failure:
        raise ReconciliationError(
            f"reconciliation gate failed: {detail}\n"
            f"first mismatches:\n{_excerpt(db)}\n"
            f"full report: SELECT * FROM {REPORT_TABLE}"
        )
    return result


def _excerpt(db: Database, limit: int = 10) -> str:
    rows = db.fetch(
        f"SELECT {_REPORT_COLUMNS} FROM {REPORT_TABLE} "
        f"WHERE category = ANY(%s) ORDER BY id LIMIT %s",
        (sorted(FATAL_CATEGORIES), limit),
    )
    return "\n".join("  " + " | ".join(str(v) for v in row) for row in rows)


# --------------------------------------------------------------------------
# Read side (Task 9 export / Task 20 display consume these)
# --------------------------------------------------------------------------


def read_report(db: Database, category: str | None = None) -> pl.DataFrame:
    """The reconciliation report, in insertion order."""
    ensure_report_table(db)
    where = "WHERE category = %s" if category else ""
    return db.fetch_frame(
        f"SELECT {_REPORT_COLUMNS} FROM {REPORT_TABLE} {where} ORDER BY id",
        (category,) if category else None,
    )


def annulled_candidacies(db: Database) -> pl.DataFrame:
    """One row per candidate whose votes were annulled (amendment A4).

    Joined back to ``dim_candidate`` so the export carries the name, the party
    and the municipalities involved -- this is a published finding in its own
    right, not just gate bookkeeping.
    """
    ensure_report_table(db)
    return db.fetch_frame(
        f"""
        SELECT r.race, r.round,
               split_part(r.key, ':', 2)::INTEGER AS number,
               c.name, c.party_number, c.coalition,
               count(*)::BIGINT                AS municipalities,
               SUM(r.ours)::BIGINT             AS votes
        FROM {REPORT_TABLE} r
        LEFT JOIN dim_candidate c
               ON c.race = r.race AND c.round = r.round
              AND c.number = split_part(r.key, ':', 2)::INTEGER
              AND c.uf = CASE WHEN r.race = 'PRES' THEN 'BR'
                              ELSE (SELECT uf FROM dim_municipality
                                    WHERE code = r.mun_code) END
        WHERE r.category = 'annulled_candidacy' AND r.key LIKE 'cand:%'
        GROUP BY 1, 2, 3, 4, 5, 6
        ORDER BY votes DESC
        """
    )
