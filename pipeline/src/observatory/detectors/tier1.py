"""Tier 1 -- integrity checks.  Not statistics: arithmetic.

Tier 1 asks the two questions that have a right answer.  Everything above it
(Tiers 2 and 3) asks questions that only have a *distribution*, so a Tier 1
finding is categorically different: it is not "this section is unusual", it is
"these numbers cannot all be true at once".  That is why the score is a count
of broken rules rather than a probability, and why a violation at national
scale is worth a human look even if it is one section in half a million.

WHY THE LEGENDA TERM IS IN THE IDENTITY
    ``nominal + legenda + blank + null == comparecimento``.  For majoritarian
    races (PRES, GOV, SEN) legenda is structurally zero -- you cannot vote for
    a party list.  For DEPFED/DEPEST it is a real and large number, so an
    identity written without it would flag essentially every proportional
    section in Brazil.  The bug would look exactly like a finding.

WHERE EACH TERM COMES FROM
    All four vote terms come from the *votação* file (``fact_votes``, one row
    per votável, blank and null included as their own kinds), and
    ``comparecimento``/``aptos`` from the *detalhe* file
    (``fact_section_stats``).  Deliberately cross-file: the two are published
    separately, and an identity that only ever compared a file to itself would
    be blind to the class of error that actually matters.

SPARSE, NOT ZERO-FILLED
    A section×votável combination with no votes has NO ROW (established at
    Task 3).  So every term is a SUM over what exists, and a section with no
    ``legenda`` row has legenda 0 -- absence is the zero.  A ``LEFT JOIN``
    plus ``COALESCE`` is not defensive here, it is the semantics.

NULL IS NOT ZERO EITHER
    TSE's sentinels (``#NULO#``, ``-1``, ``-3``) parse to NULL.  A row whose
    inputs are NULL is *excluded and counted*, never flagged: "we do not know"
    and "the numbers disagree" are different findings, and conflating them
    would turn every publication gap into a fake anomaly.

ANNULLED CANDIDACIES ARE INCLUDED (amendment A4)
    22 RR candidates have section votes but no rollup row: their candidacies
    were rejected after the election and TSE excluded the votes from the
    official totals.  Those votes stay IN the Tier 1 sums.  The identity is a
    statement about what the urna reported on election night, not about what
    survived judicial validation -- excluding them would break the identity on
    every section where such a candidate got a vote (in RR: all of them, via
    senate candidate 333).  Detectors that measure *outcomes* rather than
    *arithmetic* may make the opposite choice; this one cannot.

CROSS-RACE PRESENCE DERIVES ITS OWN MATRIX
    Which races a UF votes in, per round, is read off the ingested data --
    never hardcoded.  A hardcoded matrix would be wrong for 2022 round 2 (GOV
    in 12 states only), wrong for ZZ/exterior (PRES only), and wrong again for
    any future election.  The derivation costs one GROUP BY and handles all
    three for free.  Comparisons are within-round: round 2 is compared to
    round 2, so a state with no gubernatorial runoff simply has a smaller
    round-2 matrix and nothing is flagged.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from ..db import Database
from .base import (
    CROSS_RACE,
    SCORES_TABLE,
    BaseDetector,
    DetectorResult,
    ScoreWriter,
    read_scores,
)

__all__ = ["Arithmetic", "CrossRacePresence", "read_scores"]


# --------------------------------------------------------------------------
# (a) Arithmetic
# --------------------------------------------------------------------------

#: The four vote terms, summed from the sparse votável rows.
_VOTE_TERMS = """
    SELECT section_id, race, round,
           COALESCE(SUM(votes) FILTER (WHERE votavel_kind = 'candidate'), 0) AS nominal,
           COALESCE(SUM(votes) FILTER (WHERE votavel_kind = 'legenda'),   0) AS legenda,
           COALESCE(SUM(votes) FILTER (WHERE votavel_kind = 'blank'),     0) AS blank,
           COALESCE(SUM(votes) FILTER (WHERE votavel_kind = 'null'),      0) AS null_votes
    FROM fact_votes
    GROUP BY 1, 2, 3
"""

#: Every stats column a rule reads.  A NULL in any of them excludes the row.
_SENTINEL_COLUMNS: tuple[str, ...] = (
    "aptos",
    "comparecimento",
    "abstentions",
    "blank",
    "null_votes",
)

_NOT_NULL = " AND ".join(f"s.{c} IS NOT NULL" for c in _SENTINEL_COLUMNS)

#: One row per scored (section, race, round), carrying the rules it broke.
#: ``ARRAY_REMOVE(..., NULL)`` is how a CASE without ELSE becomes "no rule".
_ARITHMETIC_RULES = f"""
    WITH v AS ({_VOTE_TERMS}), b AS (
        SELECT s.section_id, s.race, s.round,
               s.aptos, s.comparecimento, s.abstentions,
               COALESCE(v.nominal, 0)    AS nominal,
               COALESCE(v.legenda, 0)    AS legenda,
               COALESCE(v.blank, 0)      AS blank,
               COALESCE(v.null_votes, 0) AS null_votes,
               LEAST(s.aptos, s.comparecimento, s.abstentions,
                     s.blank, s.null_votes) AS min_stat
        FROM fact_section_stats s
        LEFT JOIN v ON v.section_id = s.section_id
                   AND v.race = s.race AND v.round = s.round
        WHERE {_NOT_NULL}
    ), r AS (
        SELECT b.*, (b.nominal + b.legenda + b.blank + b.null_votes) AS votes_total
        FROM b
    )
    SELECT r.*, ARRAY_REMOVE(ARRAY[
        CASE WHEN r.votes_total <> r.comparecimento
             THEN 'votes_ne_comparecimento'::TEXT END,
        CASE WHEN r.comparecimento > r.aptos
             THEN 'comparecimento_gt_aptos'::TEXT END,
        CASE WHEN r.min_stat < 0 THEN 'negative_count'::TEXT END
    ], NULL) AS violated
    FROM r
"""

_ARITHMETIC_SCORES = f"""
    SELECT section_id, race, round,
           CARDINALITY(violated)::DOUBLE PRECISION AS score,
           jsonb_build_object(
               'rules', to_jsonb(violated),
               'nominal', nominal, 'legenda', legenda,
               'blank', blank, 'null', null_votes,
               'votes_total', votes_total,
               'comparecimento', comparecimento,
               'aptos', aptos
           ) AS detail
    FROM ({_ARITHMETIC_RULES}) rules
"""


class Arithmetic(BaseDetector):
    """The section's own numbers must add up, and must be possible.

    Three rules, 1.0 each, so the score is the count of things wrong:

    ``votes_ne_comparecimento``  nominal + legenda + blank + null != comparecimento
    ``comparecimento_gt_aptos``  more voters turned out than were registered.
        Unconditional (amendment A1): there is no seções-agregadas exemption,
        because TSE absorbs an aggregated section into its host and the host's
        ``aptos`` already includes the absorbed electorate.  Verified on RR:
        zero violations, and the turnout reconciliation matched on all 90 keys.
        Any violation nationally is a genuine finding.
    ``negative_count``           a count below zero.

    Writes the real ``(race, round)``.
    """

    name = "t1_arithmetic"
    flag_threshold = 1.0

    def params(self) -> dict[str, Any]:
        return {
            "rules": [
                "votes_ne_comparecimento",
                "comparecimento_gt_aptos",
                "negative_count",
            ],
            "score": "1.0 per violated rule",
            "identity": "nominal + legenda + blank + null == comparecimento",
            "vote_terms_from": "fact_votes (votação)",
            "turnout_terms_from": "fact_section_stats (detalhe)",
            "annulled_candidacies": "included -- the urna's report, not TSE's validation (A4)",
            "null_inputs": "excluded and counted, never flagged",
        }

    def compute(self, db: Database, scores: ScoreWriter) -> DetectorResult:
        scores.insert_select(_ARITHMETIC_SCORES)
        excluded = int(
            db.scalar(
                "SELECT count(*) FROM fact_section_stats s WHERE NOT ("
                + _NOT_NULL
                + ")"
            )
            or 0
        )
        by_rule = {
            str(rule): int(n)
            for rule, n in db.fetch(
                f"""
                SELECT r.rule, count(*)
                FROM {SCORES_TABLE} s,
                     LATERAL jsonb_array_elements_text(s.detail -> 'rules') AS r(rule)
                WHERE s.detector = %s
                GROUP BY 1 ORDER BY 1
                """,
                (self.name,),
            )
        }
        return DetectorResult(
            {
                "excluded_null_inputs": excluded,
                "violations_by_rule": by_rule,
                "worst": _worst_sections(db, self.name),
            }
        )


def _worst_sections(db: Database, detector: str, limit: int = 20) -> list[dict[str, Any]]:
    """The head of the ranked list, for the summary (and for eyeballing)."""
    rows = db.fetch(
        f"""
        SELECT s.section_id, d.uf, d.mun_code, s.race, s.round, s.score, s.detail
        FROM {SCORES_TABLE} s
        LEFT JOIN dim_section d ON d.id = s.section_id
        WHERE s.detector = %s AND s.score > 0
        ORDER BY s.score DESC, s.section_id LIMIT %s
        """,
        (detector, limit),
    )
    return [
        {
            "section_id": int(r[0]),
            "uf": r[1],
            "mun_code": r[2],
            "race": r[3],
            "round": int(r[4]),
            "score": float(r[5]),
            "detail": r[6],
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# (b) Cross-race presence
# --------------------------------------------------------------------------

#: A section "reports" a race in a round if it appears in EITHER section-level
#: file for it.  The union matters: a section present in the votação file and
#: missing from the detalhe file (or the reverse) is itself the anomaly, and
#: taking only one source would hide half of that class.
_PRESENT = """
    SELECT section_id, race, round FROM fact_section_stats
    UNION
    SELECT section_id, race, round FROM fact_votes
"""

_SECTION_RACES = f"""
    SELECT p.section_id, d.uf, p.round,
           ARRAY_AGG(DISTINCT p.race ORDER BY p.race) AS races
    FROM ({_PRESENT}) p
    JOIN dim_section d ON d.id = p.section_id
    GROUP BY 1, 2, 3
"""

#: The DERIVED (uf, round) -> races matrix: what this UF's own files say it
#: voted for in this round.  Never hardcoded (see the module docstring).
_MATRIX = f"""
    SELECT uf, round, ARRAY_AGG(DISTINCT race ORDER BY race) AS races
    FROM ({_PRESENT}) p
    JOIN dim_section d ON d.id = p.section_id
    GROUP BY 1, 2
"""

_PRESENCE_SCORES = f"""
    SELECT s.section_id, '{CROSS_RACE}'::TEXT AS race, s.round,
           CARDINALITY(m.missing)::DOUBLE PRECISION AS score,
           jsonb_build_object(
               'uf', s.uf,
               'expected', to_jsonb(x.races),
               'present', to_jsonb(s.races),
               'missing', to_jsonb(m.missing)
           ) AS detail
    FROM ({_SECTION_RACES}) s
    JOIN ({_MATRIX}) x ON x.uf = s.uf AND x.round = s.round
    CROSS JOIN LATERAL (
        SELECT ARRAY(
            SELECT race FROM (
                SELECT UNNEST(x.races) AS race
                EXCEPT
                SELECT UNNEST(s.races)
            ) q ORDER BY race
        ) AS missing
    ) m
"""


class CrossRacePresence(BaseDetector):
    """Every section should report every race its state voted in that round.

    Scored per missing race (so a section missing two races scores 2.0),
    written as ``race = 'ALL'`` with the real round, per the framework's
    cross-race convention.

    A section that reports NOTHING at all in a round is not scored for that
    round.  That is deliberate: sections are created, merged and dissolved
    between rounds, and scoring absence-of-everything would flag that churn as
    fraud while telling us nothing about cross-race consistency.  Coverage of
    a whole round is an ingest question, and the reconciliation gate already
    owns it.
    """

    name = "t1_presence"
    flag_threshold = 1.0

    def params(self) -> dict[str, Any]:
        return {
            "matrix": "derived from the ingested data, per (uf, round)",
            "comparison": "within-round only",
            "score": "1.0 per missing race",
            "race_written": CROSS_RACE,
            "sections_absent_from_a_whole_round": "not scored",
        }

    def compute(self, db: Database, scores: ScoreWriter) -> DetectorResult:
        matrix = self._matrix(db)
        scores.insert_select(_PRESENCE_SCORES)
        missing_counts = {
            str(race): int(n)
            for race, n in db.fetch(
                f"""
                SELECT r.race, count(*)
                FROM {SCORES_TABLE} s,
                     LATERAL jsonb_array_elements_text(s.detail -> 'missing') AS r(race)
                WHERE s.detector = %s
                GROUP BY 1 ORDER BY 1
                """,
                (self.name,),
            )
        }
        return DetectorResult(
            summary={
                "derived_matrix": matrix,
                "missing_by_race": missing_counts,
                "worst": _worst_sections(db, self.name),
            },
            state_payloads={
                uf: {"races_by_round": rounds} for uf, rounds in matrix.items()
            },
        )

    @staticmethod
    def _matrix(db: Database) -> dict[str, dict[str, list[str]]]:
        """``{uf: {round: [races]}}`` -- stamped into the summary so a reader
        can check what the detector believed before trusting what it found."""
        out: dict[str, dict[str, list[str]]] = {}
        for uf, rnd, races in db.fetch(f"SELECT uf, round, races FROM ({_MATRIX}) m "
                                       "ORDER BY uf, round"):
            out.setdefault(str(uf), {})[str(rnd)] = list(races)
        return out


# --------------------------------------------------------------------------
# Read side
# --------------------------------------------------------------------------


def read_matrix(db: Database) -> pl.DataFrame:
    """The derived (uf, round) -> races matrix, straight from the warehouse."""
    return db.fetch_frame(f"SELECT uf, round, races FROM ({_MATRIX}) m ORDER BY uf, round")
