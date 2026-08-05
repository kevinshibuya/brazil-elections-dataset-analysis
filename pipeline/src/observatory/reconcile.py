"""The reconciliation gate: our aggregates vs TSE's published totals.

    from observatory import ingest, reconcile
    reconcile.run_gate(ingest.connect())            # the whole warehouse
    reconcile.run_gate(ingest.connect(), uf="RR")   # ... one member of it

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

SCOPE -- WHY ``run_gate`` TAKES A ``uf``
    Every comparison keys on ``mun_code``, and a municipality belongs to
    exactly one UF, so both sides of every comparison come from a single
    member's load.  Reconciliation is therefore ADDITIVE across members: a
    state that reconciled when it landed cannot stop reconciling because a
    later state arrived.

    Re-comparing the whole warehouse after every member is consequently pure
    waste, and it is quadratic waste: measured over the national run the gate
    grew with the warehouse and was independent of the member's own size (BA
    loaded 5.6 M rows and paid 139 s; DF loaded 1.4 M and paid 397 s), and by
    the tenth of 28 members it had overtaken the load itself.

    So ``run_gate(db, uf=...)`` restricts every comparison to that member's
    municipalities.  ``uf=None`` is the whole warehouse, unchanged.

    WHAT SCOPING MUST NOT NARROW.  ``_CAND_VALID_ELSEWHERE`` and
    ``_PARTY_VALID_ELSEWHERE`` deliberately search OUTSIDE the rows being
    compared -- the first nationally for PRES, both within the whole UF for
    everything else.  They are what separates a non-fatal
    ``annulled_candidacy`` from a FATAL ``annulled_but_valid`` (A4), and
    narrowing them would silently downgrade the fatal category into a finding.
    They carry no scope filter, deliberately, and the acceptance test
    ``test_scoping_does_not_narrow_the_valid_elsewhere_lookups`` pins that.

    THE REPORT ACCUMULATES.  ``reconciliation_report`` is national: the export
    reads the WHOLE table (``annulled_candidacies`` and the A4 aggregate), and
    a UF-by-UF pass never runs an unscoped gate of its own.  A scoped run
    therefore replaces only the rows in its own scope -- its municipalities,
    plus the scope-level ``empty_warehouse`` row -- and leaves every other
    member's findings where they are.  An unscoped run still truncates, because
    its scope is everything.  Either way the invariant is the same: after a run
    the table holds exactly one verdict per member reconciled so far.

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
# Scope
#
# One member, or the whole warehouse.  Every comparison below takes a
# ``_Scope`` and pushes its filter DOWN into the per-source aggregation, where
# it shrinks the group-by and the join rather than merely filtering their
# result.  The filter is always on a ``uf`` column that the source already
# carries: ``dim_section.uf`` for our two section-level sources, and the
# rollups' own ``uf`` for TSE's three.
#
# The parameter is named (``%(uf)s``) because it appears several times in one
# statement.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Scope:
    """Which member a run compares.  ``uf=None`` is the whole warehouse."""

    uf: str | None = None

    @property
    def scoped(self) -> bool:
        return self.uf is not None

    @property
    def params(self) -> dict[str, str] | None:
        """Query parameters, or ``None`` when the SQL carries no placeholder."""
        return None if self.uf is None else {"uf": self.uf}

    def where(self, alias: str) -> str:
        """A whole ``WHERE`` clause, for a source that has none of its own."""
        return "" if self.uf is None else f"WHERE {alias}.uf = %(uf)s"

    def and_(self, alias: str) -> str:
        """A conjunct, for a source that already has a ``WHERE``."""
        return "" if self.uf is None else f" AND {alias}.uf = %(uf)s"


# --------------------------------------------------------------------------
# The comparisons
#
# Each is expressed as SQL that SELECTs report rows for MISMATCHES ONLY, so a
# clean run inserts nothing.  Everything stays server-side: nationally these
# join ~100 M fact rows, which is not a thing to pull into Python.
# --------------------------------------------------------------------------


def _ours_nominal(scope: _Scope) -> str:
    """Our nominal votes per (municipality, race, round, candidate number).

    ``dim_candidate.number`` -- the fact row does not carry it.
    """
    return f"""
    SELECT s.mun_code, f.race, f.round, c.number AS key_num,
           SUM(f.votes)::BIGINT AS ours, count(*)::BIGINT AS n_ours
    FROM fact_votes f
    JOIN dim_section   s ON s.id = f.section_id
    JOIN dim_candidate c ON c.id = f.candidate_id
    WHERE f.votavel_kind = 'candidate'{scope.and_('s')}
    GROUP BY 1, 2, 3, 4
"""


def _theirs_nominal(scope: _Scope) -> str:
    """TSE's nominal votes, aggregated over ZONE and over the transit flag.

    Q24: a (mun, race, round) key appears once per zone and once per
    ``ST_VOTO_EM_TRANSITO`` value.  ``votes_nominal`` -- NOT
    ``votes_nominal_valid``: the section file counts every vote the urna
    recorded, including the ones TSE later annulled.
    """
    return f"""
    SELECT t.mun_code, t.race, t.round, t.cand_number AS key_num,
           SUM(t.votes_nominal)::BIGINT AS theirs, count(*)::BIGINT AS n_theirs
    FROM rollup_cand_munzona t
    WHERE t.cand_number IS NOT NULL{scope.and_('t')}
    GROUP BY 1, 2, 3, 4
"""


def _nominal_join(scope: _Scope) -> str:
    return f"""
    joined AS (
        SELECT * FROM ({_ours_nominal(scope)}) o
        FULL JOIN ({_theirs_nominal(scope)}) t USING (mun_code, race, round, key_num)
    )
"""


#: An annulled candidacy is annulled for the whole race: the candidate must be
#: absent from the rollup EVERYWHERE in scope.  Presidential candidates are
#: national, every other race is scoped to one UF (deputy numbers repeat
#: across states).
#:
#: DELIBERATELY UNSCOPED (A4).  This lookup is the whole difference between a
#: non-fatal ``annulled_candidacy`` and a FATAL ``annulled_but_valid``, and it
#: only means anything because it searches OUTSIDE the rows being compared.
#: Adding ``run_gate``'s ``uf`` filter here would narrow the PRES branch from
#: national to one state and turn every cross-member fatal into a finding.  It
#: takes no ``_Scope``, and it must not acquire one.
_CAND_VALID_ELSEWHERE = """
    EXISTS (
        SELECT 1 FROM rollup_cand_munzona r
        WHERE r.race = j.race AND r.round = j.round
          AND r.cand_number = j.key_num
          AND COALESCE(r.votes_nominal_valid, 0) > 0
          AND (j.race = 'PRES' OR r.uf = m.uf)
    )
"""


def _nominal_rows(scope: _Scope) -> str:
    """Both nominal categories out of ONE pass over the join.

    A numeric disagreement on a key TSE published, and a key TSE published no
    row for.
    """
    return f"""
    WITH {_nominal_join(scope)}
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


def _ours_legenda(scope: _Scope) -> str:
    """Legenda: our raw section counts.

    Compared against valid + annulled + sub judice (A5) -- the valid column
    alone is not comparable, because votes for a rejected party list are parked
    in the annulled ones (Q27).
    """
    return f"""
    SELECT s.mun_code, f.race, f.round, f.party_number AS key_num,
           SUM(f.votes)::BIGINT AS ours, count(*)::BIGINT AS n_ours
    FROM fact_votes f
    JOIN dim_section s ON s.id = f.section_id
    WHERE f.votavel_kind = 'legenda' AND f.party_number IS NOT NULL{scope.and_('s')}
    GROUP BY 1, 2, 3, 4
"""


def _theirs_legenda(scope: _Scope) -> str:
    return f"""
    SELECT t.mun_code, t.race, t.round, t.party_number AS key_num,
           SUM(COALESCE(t.votes_legenda_valid, 0)
               + COALESCE(t.votes_legenda_annulled, 0)
               + COALESCE(t.votes_legenda_anul_subjud, 0))::BIGINT AS theirs,
           count(*)::BIGINT AS n_theirs
    FROM rollup_party_munzona t
    WHERE t.party_number IS NOT NULL{scope.and_('t')}
    GROUP BY 1, 2, 3, 4
"""


def _legenda_join(scope: _Scope) -> str:
    return f"""
    joined AS (
        SELECT * FROM ({_ours_legenda(scope)}) o
        FULL JOIN ({_theirs_legenda(scope)}) t USING (mun_code, race, round, key_num)
    )
"""


#: DELIBERATELY UNSCOPED, for the same reason as ``_CAND_VALID_ELSEWHERE``:
#: ``r.uf = m.uf`` already says which UF to search, and it is the MUNICIPALITY's
#: UF, not the run's.  A ``run_gate`` filter here would be redundant at best and
#: a silent downgrade of the fatal category at worst.
_PARTY_VALID_ELSEWHERE = """
    EXISTS (
        SELECT 1 FROM rollup_party_munzona r
        WHERE r.race = j.race AND r.round = j.round
          AND r.party_number = j.key_num
          AND COALESCE(r.votes_legenda_valid, 0) > 0
          AND r.uf = m.uf
    )
"""


def _legenda_rows(scope: _Scope) -> str:
    return f"""
    WITH {_legenda_join(scope)}
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


def _ours_turnout(scope: _Scope) -> str:
    return f"""
    SELECT s.mun_code, f.race, f.round,
           SUM(f.aptos)::BIGINT          AS o_aptos,
           SUM(f.comparecimento)::BIGINT AS o_comparecimento,
           SUM(f.abstentions)::BIGINT    AS o_abstentions,
           SUM(f.blank)::BIGINT          AS o_blank,
           SUM(f.null_votes)::BIGINT     AS o_null_votes,
           count(*)::BIGINT              AS n_ours
    FROM fact_section_stats f
    JOIN dim_section s ON s.id = f.section_id
    {scope.where('s')}
    GROUP BY 1, 2, 3
"""


def _theirs_turnout(scope: _Scope) -> str:
    return f"""
    SELECT t.mun_code, t.race, t.round,
           SUM(t.aptos)::BIGINT          AS t_aptos,
           SUM(t.comparecimento)::BIGINT AS t_comparecimento,
           SUM(t.abstentions)::BIGINT    AS t_abstentions,
           SUM(t.blank)::BIGINT          AS t_blank,
           SUM(t.null_votes)::BIGINT     AS t_null_votes,
           count(*)::BIGINT              AS n_theirs
    FROM rollup_detail_munzona t
    {scope.where('t')}
    GROUP BY 1, 2, 3
"""


def _turnout_rows(scope: _Scope) -> str:
    return f"""
    WITH joined AS (
        SELECT * FROM ({_ours_turnout(scope)}) o
        FULL JOIN ({_theirs_turnout(scope)}) t USING (mun_code, race, round)
    )
    SELECT j.mun_code, j.race, j.round, 'turnout', metric.name,
           metric.ours, metric.theirs, metric.ours - metric.theirs
    FROM joined j
    CROSS JOIN LATERAL (VALUES
        {", ".join(f"('{m}', j.o_{m}, j.t_{m})" for m in _TURNOUT_METRICS)}
    ) AS metric(name, ours, theirs)
    WHERE metric.ours IS DISTINCT FROM metric.theirs
"""


def _internal_rows(scope: _Scope) -> str:
    """Internal cross-check: the votação file's blank/null votável rows must
    equal the detalhe file's blank/null counts.  Both are ours; if they
    disagree we mis-parsed one of them and no external comparison would catch
    it."""
    return f"""
    WITH votes AS (
        SELECT s.mun_code, f.race, f.round,
               COALESCE(SUM(f.votes) FILTER (WHERE f.votavel_kind = 'blank'), 0)::BIGINT AS v_blank,
               COALESCE(SUM(f.votes) FILTER (WHERE f.votavel_kind = 'null'), 0)::BIGINT  AS v_null_votes
        FROM fact_votes f
        JOIN dim_section s ON s.id = f.section_id
        {scope.where('s')}
        GROUP BY 1, 2, 3
    ), stats AS (
        SELECT s.mun_code, f.race, f.round,
               COALESCE(SUM(f.blank), 0)::BIGINT      AS s_blank,
               COALESCE(SUM(f.null_votes), 0)::BIGINT AS s_null_votes
        FROM fact_section_stats f
        JOIN dim_section s ON s.id = f.section_id
        {scope.where('s')}
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


def _disagreement_rows(scope: _Scope) -> str:
    """TSE vs TSE, over keys BOTH files cover.  ``ours`` is the candidato
    file's nominal total, ``theirs`` the partido file's -- neither is ours, and
    we pick no winner.  Informational."""
    return f"""
    WITH cand AS (
        SELECT c.mun_code, c.race, c.round, SUM(c.votes_nominal)::BIGINT AS total
        FROM rollup_cand_munzona c {scope.where('c')} GROUP BY 1, 2, 3
    ), party AS (
        SELECT p.mun_code, p.race, p.round,
               SUM(p.votes_nominal_valid)::BIGINT AS total
        FROM rollup_party_munzona p {scope.where('p')} GROUP BY 1, 2, 3
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


def _keys_internal(scope: _Scope) -> str:
    return f"""
    SELECT count(*) * 2 FROM (
        SELECT s.mun_code, f.race, f.round FROM fact_votes f
        JOIN dim_section s ON s.id = f.section_id {scope.where('s')}
        GROUP BY 1, 2, 3
        UNION
        SELECT s.mun_code, f.race, f.round FROM fact_section_stats f
        JOIN dim_section s ON s.id = f.section_id {scope.where('s')}
        GROUP BY 1, 2, 3
    ) k
"""


def _keys_disagreement(scope: _Scope) -> str:
    return f"""
    SELECT count(*) FROM (
        SELECT c.mun_code, c.race, c.round FROM rollup_cand_munzona c
        {scope.where('c')} GROUP BY 1, 2, 3
        INTERSECT
        SELECT p.mun_code, p.race, p.round FROM rollup_party_munzona p
        {scope.where('p')} GROUP BY 1, 2, 3
    ) k
"""


def _checks(scope: _Scope) -> tuple[_Check, ...]:
    """One entry per COMPARISON (not per category: the nominal and legenda
    comparisons each emit two categories from a single pass over their join)."""
    return (
        _Check(
            "nominal",
            _nominal_rows(scope),
            f"SELECT count(*) FROM ({_ours_nominal(scope)}) o FULL JOIN "
            f"({_theirs_nominal(scope)}) t USING (mun_code, race, round, key_num)",
        ),
        _Check(
            "legenda",
            _legenda_rows(scope),
            f"SELECT count(*) FROM ({_ours_legenda(scope)}) o FULL JOIN "
            f"({_theirs_legenda(scope)}) t USING (mun_code, race, round, key_num)",
        ),
        _Check(
            "turnout",
            _turnout_rows(scope),
            f"SELECT count(*) * {len(_TURNOUT_METRICS)} FROM "
            f"({_ours_turnout(scope)}) o FULL JOIN ({_theirs_turnout(scope)}) t "
            f"USING (mun_code, race, round)",
        ),
        _Check("internal_blank_null", _internal_rows(scope), _keys_internal(scope)),
        _Check("rollup_disagreement", _disagreement_rows(scope), _keys_disagreement(scope)),
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

    @property
    def name(self) -> str:
        """Alias for :attr:`category` -- ``c.name`` reads better in callers."""
        return self.category

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
    #: The member this run compared, or ``None`` for the whole warehouse.
    uf: str | None = None

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
        return (
            f"{'PASS' if self.ok else 'FAIL'} [{self.uf or 'national'}] — "
            f"keys compared: {keys} — {cats}"
        )


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def ensure_report_table(db: Database) -> None:
    db.executemany_sql(_REPORT_DDL)


#: Which report rows belong to a scoped run: its municipalities, plus the rows
#: that describe the SCOPE rather than a municipality (``empty_warehouse``, the
#: only row the gate writes with a NULL ``mun_code``).  This is both what a
#: scoped run clears before it writes and what it counts afterwards -- so a
#: member's verdict is about that member, and no other member's findings are
#: touched.
_REPORT_SCOPE = (
    "(mun_code IS NULL OR mun_code IN "
    "(SELECT code FROM dim_municipality WHERE uf = %(uf)s))"
)


def run_gate(
    db: Database, *, uf: str | None = None, raise_on_failure: bool = True
) -> GateResult:
    """Reconcile the warehouse against TSE's published totals.

    ``uf`` restricts every comparison to one member's municipalities -- what
    ``02_ingest.py`` passes after loading that member, because reconciliation
    is additive across members and re-checking the earlier ones buys nothing
    (see the module docstring).  ``uf=None`` compares the whole warehouse.

    Writes every mismatch to ``reconciliation_report`` FIRST, stamps
    ``meta.gate_runs``, and only then raises :class:`ReconciliationError` if
    anything fatal was found.  Pass ``raise_on_failure=False`` to inspect the
    result instead (the report is written either way).

    A scoped run replaces only its own scope's rows in the report; an unscoped
    one truncates.  The table is national and the export reads all of it.
    """
    scope = _Scope(uf)
    ensure_report_table(db)
    _clear_report(db, scope)

    keys_compared: dict[str, int] = {}
    for check in _checks(scope):
        keys_compared[check.name] = int(db.scalar(check.keys_sql, scope.params) or 0)
        db.execute(
            f"INSERT INTO {REPORT_TABLE} ({_REPORT_COLUMNS}) {check.rows_sql}",
            scope.params,
        )

    if _nothing_to_reconcile(db, scope, keys_compared):
        db.execute(
            f"INSERT INTO {REPORT_TABLE} (category, key, ours, theirs, diff) "
            "VALUES ('empty_warehouse', 'fact_votes', 0, NULL, NULL)"
        )

    counted = {
        str(row[0]): (int(row[1]), int(row[2] or 0))
        for row in db.fetch(
            f"SELECT category, count(*), SUM(ABS(COALESCE(diff, 0))) "
            f"FROM {REPORT_TABLE} {_scope_filter(scope)} GROUP BY 1",
            scope.params,
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
        uf=uf,
    )

    if result.ok:
        db.stamp_gate(GATE_NAME, True, result.summary())
        return result

    detail = result.summary()
    db.stamp_gate_out_of_band(GATE_NAME, False, detail)
    if raise_on_failure:
        raise ReconciliationError(
            f"reconciliation gate failed: {detail}\n"
            f"first mismatches:\n{_excerpt(db, scope)}\n"
            f"full report: SELECT * FROM {REPORT_TABLE}"
        )
    return result


def _scope_filter(scope: _Scope) -> str:
    return f"WHERE {_REPORT_SCOPE}" if scope.scoped else ""


def _clear_report(db: Database, scope: _Scope) -> None:
    """Make room for this run's verdict, and only this run's.

    Unscoped, the scope is everything and TRUNCATE is both cheapest and
    exactly right.  Scoped, the other members' findings must survive: nothing
    else recomputes them, and the export reads the whole table.
    """
    if not scope.scoped:
        db.execute(f"TRUNCATE {REPORT_TABLE} RESTART IDENTITY")
        return
    db.execute(f"DELETE FROM {REPORT_TABLE} WHERE {_REPORT_SCOPE}", scope.params)


def _nothing_to_reconcile(
    db: Database, scope: _Scope, keys_compared: dict[str, int]
) -> bool:
    """Is a pass over this scope vacuous?  A vacuous pass is not a pass.

    Unscoped that is the original question, asked of the warehouse as a whole.
    Scoped, it is asked of the member, and the answer is already in hand: if
    every comparison found zero keys, neither we nor TSE hold anything for this
    member.  Deriving it from ``keys_compared`` rather than probing
    ``fact_votes`` matters -- the probe is a scan of the whole fact table, and
    the point of scoping is not to pay for the whole fact table.
    """
    if scope.scoped:
        return not any(keys_compared.values())
    return not db.scalar("SELECT EXISTS (SELECT 1 FROM fact_votes)")


def _excerpt(db: Database, scope: _Scope, limit: int = 10) -> str:
    where = "WHERE category = ANY(%(categories)s)"
    params: dict[str, object] = {
        "categories": sorted(FATAL_CATEGORIES),
        "limit": limit,
    }
    if scope.scoped:
        where += f" AND {_REPORT_SCOPE}"
        params["uf"] = scope.uf
    rows = db.fetch(
        f"SELECT {_REPORT_COLUMNS} FROM {REPORT_TABLE} {where} "
        f"ORDER BY id LIMIT %(limit)s",
        params,
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
