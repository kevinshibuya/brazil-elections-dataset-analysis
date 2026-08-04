"""Tier 2 -- the Klimek fingerprint: turnout against the winner's share.

THE QUESTION
    Klimek, Yegorov, Hanel & Thurner (PNAS 2012) noticed that an election's
    two most basic per-unit numbers -- how many of the registered voters turned
    up, and what share of the valid votes the winner took -- have a
    characteristic JOINT distribution.  Plot one point per polling station,
    turnout on x and the winner's share on y, and a clean election is a single
    blob: turnout varies for local reasons, share varies for political ones,
    and the two are only weakly related.

    Two mechanisms deform that blob in ways nothing else does:

      * **incremental fraud** -- a fraction of the ballots that would have been
        blank, null or absent are moved to the winner.  Turnout and share rise
        together, so the blob grows a diagonal SMEAR towards the top-right.
      * **extreme fraud** -- a unit reports near-total turnout and near-total
        support.  Those units pile up in the top-right CORNER as a second,
        detached cluster.

    So the detector's output is not a score, it is a PICTURE: the 2D histogram
    itself, per state, which the dashboard renders log-scaled.  The one number
    it summarises the picture with is the corner mass.

WHAT IS ON EACH AXIS
    ``x = 100 * comparecimento / aptos``       -- turnout, per cent
    ``y = 100 * winner_votes / valid_votes``   -- the winner's share of the
                                                  VALID vote, per cent

    "Valid" means nominal (``votavel_kind = 'candidate'``) presidential votes:
    blank and null are, by definition, not valid, and there is no legenda in a
    presidential race.  ZERO-VOTE ROWS DO NOT EXIST, so the denominator is
    summed over the section's OWN rows as they come; the numerator is the
    winner's row if he has one in that section and 0 if he does not.  Summing a
    sparse numerator against a sparse denominator would put every section at
    100 %, which is precisely the shape being looked for.

WHO THE "WINNER" IS
    The candidate with the most nominal votes NATIONALLY, in that round --
    never the state's own leader.  A fingerprint drawn against whoever happens
    to lead each state is 27 different pictures that cannot be compared, and
    the top-right corner would stop meaning "the winner swept this section".

THE GRID IS RAW COUNTS
    100 x 100 bins of exactly 1 %, over [0, 100] on both axes, and the cells
    hold RAW SECTION COUNTS.  The log scaling that makes the smear visible is
    the renderer's job (Task 20); a detector that logged here would throw away
    the counts and make the corner mass unrecoverable.

    Bin edges are half-open, ``[b, b+1)``, EXCEPT the last one: a section at
    exactly 100 % turnout (they exist -- small rural sections where every
    registered voter shows up) lands in bin 99 rather than off the end.  That
    is a deliberate clamp, and it is what makes the grid's total equal
    ``n_sections`` exactly.

WHAT COUNTS AS A SECTION HERE
    One that has BOTH coordinates: a stats row with ``aptos > 0`` (else there
    is no turnout) and at least one valid presidential vote (else there is no
    share).  Anything else is not "clean", it is unobservable, and it is
    counted out of ``n_sections`` rather than dropped into a corner cell.

NO PER-SECTION SCORES
    This is a distribution-level detector.  A single section at (98 %, 97 %) is
    not evidence of anything -- the finding is that a THOUSAND of them are --
    so there is nothing honest to write into ``section_scores`` and the
    detector writes none.  ``sections_scored`` is 0 and the composite (Task 18)
    has nothing of ours to combine.  The output lives entirely in
    ``analysis.state_payloads`` and ``detector_summary.summary``.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from ..db import Database
from ..export.shards import score_key
from .base import (
    BaseDetector,
    DetectorResult,
    ScoreWriter,
    read_scores as _read_scores,
)

__all__ = ["Fingerprint", "fingerprint_of", "read_scores"]

DETECTOR = "t2_fingerprint"

#: The shard/aggregate key this detector's output appears under.  Derived, not
#: written down twice -- ``t2_fingerprint`` -> ``fingerprint``.
SCORE_KEY = score_key(DETECTOR)

#: The one race the fingerprint is drawn for: it is the only race with a single
#: national winner, which is what makes 27 state pictures comparable.
PRES = "PRES"

#: Bins per axis, and therefore the bin width in per cent (100 / 100 = 1 %).
BINS = 100

#: A section is "in the corner" above this, on BOTH axes, in per cent.
CORNER_PCT = 90.0


# --------------------------------------------------------------------------
# Reading the warehouse
# --------------------------------------------------------------------------

#: Total nominal presidential vote per (round, candidate), NATIONALLY.  The
#: winner is picked from this and from nothing else.
_NATIONAL = f"""
    SELECT f.round::INT AS round,
           c.number::INT AS cand,
           MIN(c.name) AS name,
           SUM(f.votes)::BIGINT AS votes
    FROM fact_votes f
    JOIN dim_candidate c ON c.id = f.candidate_id
    WHERE f.race = '{PRES}' AND f.votavel_kind = 'candidate'
    GROUP BY 1, 2
"""

#: One UF's ``(turnout, share)`` observations for one round.
#:
#: ``valid`` is an INNER join with ``HAVING SUM(votes) > 0``: a section with no
#: valid presidential vote has no share, so it is not observable and must not
#: reach the grid.  ``lead_votes`` is a LEFT join coalesced to 0: the winner
#: having no row in a section means he got nothing there, not that the section
#: is missing.
_OBSERVATIONS = f"""
    SELECT st.section_id,
           st.aptos::DOUBLE PRECISION AS aptos,
           st.comparecimento::DOUBLE PRECISION AS comparecimento,
           v.valid::DOUBLE PRECISION AS valid,
           COALESCE(w.lead_votes, 0)::DOUBLE PRECISION AS lead_votes
    FROM fact_section_stats st
    JOIN dim_section s ON s.id = st.section_id
    JOIN (
        SELECT f.section_id, SUM(f.votes)::BIGINT AS valid
        FROM fact_votes f
        JOIN dim_section fs ON fs.id = f.section_id
        WHERE f.race = '{PRES}' AND f.round = %s
          AND f.votavel_kind = 'candidate' AND fs.uf = %s
        GROUP BY 1
        HAVING SUM(f.votes) > 0
    ) v ON v.section_id = st.section_id
    LEFT JOIN (
        SELECT f.section_id, SUM(f.votes)::BIGINT AS lead_votes
        FROM fact_votes f
        JOIN dim_candidate c ON c.id = f.candidate_id
        JOIN dim_section ws ON ws.id = f.section_id
        WHERE f.race = '{PRES}' AND f.round = %s
          AND f.votavel_kind = 'candidate' AND ws.uf = %s
          AND c.number = %s
        GROUP BY 1
    ) w ON w.section_id = st.section_id
    WHERE st.race = '{PRES}' AND st.round = %s AND s.uf = %s
      AND st.aptos IS NOT NULL AND st.aptos > 0
      AND st.comparecimento IS NOT NULL
"""


# --------------------------------------------------------------------------
# The maths -- pure, no database
# --------------------------------------------------------------------------


def _bin(value: pl.Expr) -> pl.Expr:
    """A per cent in [0, 100] -> its bin index in [0, 99].

    Clamped at BOTH ends, on purpose.  100 % is a real reading and would
    otherwise index one past the end; a fractionally negative reading cannot
    occur from the SQL above but would silently wrap a Python list index if it
    ever did.
    """
    return (
        value.clip(0.0, 100.0)
        .floor()
        .clip(0.0, float(BINS - 1))
        .cast(pl.Int32)
    )


def fingerprint_of(obs: pl.DataFrame) -> dict[str, Any]:
    """One state-round's observations -> ``{grid, corner_mass, n_sections}``.

    ``obs`` carries ``aptos, comparecimento, valid, lead_votes``.  Pure, so the
    histogram is checkable without a warehouse.

    ``grid[x][y]`` is the number of sections whose turnout is in the 1 % bin
    ``x`` and whose winner-share is in the 1 % bin ``y`` -- x is the turnout
    axis, y the share axis, both raw counts.  It is a plain nested list of
    Python ints because it has to survive a JSON round trip through
    ``analysis.state_payloads``.
    """
    grid: list[list[int]] = [[0] * BINS for _ in range(BINS)]
    if obs.height == 0:
        return {"grid": grid, "corner_mass": 0.0, "n_sections": 0}

    points = obs.select(
        (100.0 * pl.col("comparecimento") / pl.col("aptos")).alias("turnout_pct"),
        (100.0 * pl.col("lead_votes") / pl.col("valid")).alias("share_pct"),
    )
    binned = points.select(
        _bin(pl.col("turnout_pct")).alias("x"),
        _bin(pl.col("share_pct")).alias("y"),
    )
    for x, y, n in binned.group_by(["x", "y"]).len().iter_rows():
        grid[int(x)][int(y)] = int(n)

    n_sections = obs.height
    corner = points.filter(
        (pl.col("turnout_pct") > CORNER_PCT) & (pl.col("share_pct") > CORNER_PCT)
    ).height
    return {
        "grid": grid,
        "corner_mass": corner / n_sections,
        "corner_sections": corner,
        "n_sections": n_sections,
    }


# --------------------------------------------------------------------------
# The detector
# --------------------------------------------------------------------------


class Fingerprint(BaseDetector):
    """Turnout x winner-share, per state, as a 100 x 100 histogram.

    Writes NO section scores (see the module docstring): its whole output is
    ``state_payloads`` plus the per-state corner mass in the summary.
    """

    name = DETECTOR

    def params(self) -> dict[str, Any]:
        return {
            "statistic": (
                "2D histogram of (turnout %, winner's share of valid votes %) "
                "over the sections of one UF, per presidential round"
            ),
            "race": f"{PRES} only -- the one race with a single national winner",
            "leader": (
                "the candidate with the most nominal votes NATIONALLY in that "
                "round, never the state's own leader"
            ),
            "grid": f"{BINS} x {BINS} bins of 1 %, grid[turnout_bin][share_bin]",
            "scale": "RAW counts -- the client log-scales; logging here would "
                     "make the corner mass unrecoverable",
            "bin_edges": "half-open [b, b+1), except 100 % which is clamped into bin 99",
            "valid_votes": (
                "nominal PRES votes summed over the section's OWN rows; blank "
                "and null are not valid and there is no presidential legenda"
            ),
            "sparsity": (
                "the winner's absent row is 0 in the numerator; the denominator "
                "is never restricted to his rows"
            ),
            "observable": (
                "a section needs aptos > 0 AND at least one valid presidential "
                "vote; anything else has no coordinate and is counted out of "
                "n_sections rather than binned"
            ),
            "corner_mass": (
                f"fraction of a state's sections with turnout > {CORNER_PCT:g} % "
                f"AND share > {CORNER_PCT:g} %"
            ),
            "section_scores": (
                "none -- a single extreme section is not evidence, so there is "
                "nothing honest to score; the composite ignores this detector"
            ),
        }

    # -- compute -----------------------------------------------------------

    def compute(self, db: Database, scores: ScoreWriter) -> DetectorResult:
        winners = self._winners(db)
        if not winners:
            return DetectorResult({"score_key": SCORE_KEY, "rounds": {}, "states": {}})

        # The round the top-level picture describes: the LAST presidential
        # round there is, which is the one that decided the election.
        decisive = max(winners)

        payloads: dict[str, dict[str, Any]] = {}
        states: dict[str, dict[str, Any]] = {}
        for (uf,) in db.fetch("SELECT DISTINCT uf FROM dim_section ORDER BY uf"):
            per_round = {}
            for rnd, (cand, name) in sorted(winners.items()):
                one = fingerprint_of(self._observations(db, uf, rnd, cand))
                if not one["n_sections"]:
                    continue
                per_round[str(rnd)] = {"round": rnd, "candidate": cand,
                                       "candidate_name": name, **one}
            if not per_round:
                continue
            # The top level IS one of the rounds -- the decisive one when the
            # state reports it -- so a reader (and Task 20) can draw the
            # fingerprint without first deciding which round to look at.
            top = per_round.get(str(decisive)) or per_round[max(per_round)]
            payloads[uf] = {**top, "bins": BINS, "rounds": per_round}
            states[uf] = {
                str(r): {"corner_mass": p["corner_mass"],
                         "corner_sections": p["corner_sections"],
                         "n_sections": p["n_sections"]}
                for r, p in per_round.items()
            }

        summary = {
            "score_key": SCORE_KEY,
            "bins": BINS,
            "corner_threshold_pct": CORNER_PCT,
            "decisive_round": decisive,
            "rounds": {str(r): {"candidate": c, "candidate_name": n}
                       for r, (c, n) in sorted(winners.items())},
            "states": states,
            "worst_states": _worst_states(states, decisive),
        }
        return DetectorResult(summary, state_payloads=payloads)

    # -- inputs ------------------------------------------------------------

    @staticmethod
    def _winners(db: Database) -> dict[int, tuple[int, str]]:
        """``round -> (candidate number, name)``, decided nationally."""
        best: dict[int, tuple[int, int, str]] = {}
        for rnd, cand, name, votes in db.fetch(_NATIONAL):
            rnd, cand, votes = int(rnd), int(cand), int(votes)
            # Ties break on the lower candidate number, so the pick is stable.
            current = best.get(rnd)
            if current is None or (votes, -cand) > (current[0], -current[1]):
                best[rnd] = (votes, cand, name)
        return {rnd: (cand, name) for rnd, (_v, cand, name) in best.items()}

    @staticmethod
    def _observations(db: Database, uf: str, rnd: int, cand: int) -> pl.DataFrame:
        return db.fetch_frame(_OBSERVATIONS, (rnd, uf, rnd, uf, cand, rnd, uf))


def _worst_states(
    states: dict[str, dict[str, Any]], decisive: int, limit: int = 10
) -> list[dict[str, Any]]:
    """The states with the most corner mass in the decisive round."""
    rows = [
        {"uf": uf, "round": decisive, **by_round[str(decisive)]}
        for uf, by_round in states.items()
        if str(decisive) in by_round
    ]
    rows.sort(key=lambda r: (-float(r["corner_mass"]), r["uf"]))
    return rows[:limit]


# --------------------------------------------------------------------------
# Read side
# --------------------------------------------------------------------------


def read_scores(db: Database, detector: str = DETECTOR) -> pl.DataFrame:
    """This detector's section scores -- always EMPTY, and deliberately so.

    Kept because every detector module exposes it and callers should not have
    to special-case a distribution-level one: it returns a real, empty frame
    rather than raising.  Accepts either the warehouse name
    (``t2_fingerprint``) or the shard score key (``fingerprint``), like every
    other detector.
    """
    return _read_scores(db, DETECTOR if detector in (DETECTOR, SCORE_KEY) else detector)
