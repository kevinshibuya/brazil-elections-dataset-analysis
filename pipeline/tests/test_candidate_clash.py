"""The candidate-identity clash assertion: its meaning, and its cost.

The contract (plan): no ``(uf, race, round, number)`` may map to two candidate
names, and a violation must be caught WITHIN a batch and ACROSS batches.

The cost half of this file exists because the assertion's first spelling --
``staging.votes JOIN dim_candidate ... GROUP BY 1,2,3,4,5,6 LIMIT 5`` -- planned
as a nested-loop CARTESIAN PRODUCT (10 477 x 229 910 = 2.4 e9 rows through the
join filter, 93 GB re-read from a spilled Materialize, 222 s per batch) and
dominated the national ingest.  ``ANALYZE staging.votes`` does NOT fix that; the
shape of the query is what fixes it.  See ``ingest._publish_votes``.
"""

from __future__ import annotations

import json

import polars as pl
import pytest

from observatory import ingest, parse

_MUN = ("RR", "03018", "BOA VISTA")
_ELECTION = 546


def _row(number: int, name: str, *, section: int = 1, kind: str = "candidate") -> tuple:
    uf, mun_code, mun_name = _MUN
    return (
        uf, mun_code, mun_name, 1, section, "GOV", 1, kind,
        number, name, number // 10 or 1, 42, _ELECTION,
    )


def _batch(rows: list[tuple], source: str) -> ingest.Batch:
    return ingest.Batch(
        kind="votes",
        source_file=source,
        good=pl.DataFrame(rows, schema=parse.VOTES_SCHEMA, orient="row"),
        bad=pl.DataFrame(schema=parse.BAD_SCHEMA),
    )


# --------------------------------------------------------------------------
# Meaning
# --------------------------------------------------------------------------


def test_consistent_names_load_cleanly(test_db):
    """The control: the same candidate, same name, many sections -- no raise."""
    rows = [_row(11, "GLADSON CAMELI", section=s) for s in range(1, 20)]
    ingest.load_batch(test_db, _batch(rows, "fixture://clash_none"))
    assert test_db.scalar("SELECT count(*) FROM dim_candidate") == 1


def test_clash_within_one_batch_raises(test_db):
    """Two names for one (uf, race, round, number), both in the SAME batch."""
    rows = [
        _row(11, "GLADSON CAMELI", section=1),
        _row(11, "SOMEBODY ELSE", section=2),
    ]
    with pytest.raises(ingest.IngestValidationError, match="candidate identity clash"):
        ingest.load_batch(test_db, _batch(rows, "fixture://clash_within"))


def test_clash_across_batches_raises(test_db):
    """The cross-batch property: batch 1 stores the name, batch 2 contradicts it.

    This is the half a per-batch self-check would silently lose -- neither
    batch is internally inconsistent, only their combination is.  The second
    batch lands in a DIFFERENT section on purpose, so ``fact_votes``'s natural
    key cannot catch this for us: only the clash assertion can.
    """
    ingest.load_batch(
        test_db,
        _batch([_row(11, "GLADSON CAMELI", section=1)], "fixture://clash_across_1"),
    )
    assert test_db.scalar("SELECT name FROM dim_candidate") == "GLADSON CAMELI"

    with pytest.raises(ingest.IngestValidationError, match="candidate identity clash"):
        ingest.load_batch(
            test_db,
            _batch([_row(11, "SOMEBODY ELSE", section=2)], "fixture://clash_across_2"),
        )


def test_null_name_against_a_stored_name_is_a_clash(test_db):
    """``IS DISTINCT FROM``, not ``<>``: a NULL arriving for a named candidate
    is a contradiction and must not slip through as UNKNOWN."""
    ingest.load_batch(
        test_db,
        _batch([_row(11, "GLADSON CAMELI", section=1)], "fixture://clash_null_1"),
    )
    with pytest.raises(ingest.IngestValidationError, match="candidate identity clash"):
        ingest.load_batch(
            test_db, _batch([_row(11, None, section=2)], "fixture://clash_null_2")
        )


def test_non_candidate_rows_are_not_compared(test_db):
    """Blank/null/legenda rows carry no candidate identity (candidate_id is
    NULL) and must not be dragged into the comparison."""
    rows = [
        _row(11, "GLADSON CAMELI", section=1),
        _row(95, "BRANCO", section=1, kind="branco"),
        _row(96, "NULO", section=1, kind="nulo"),
    ]
    ingest.load_batch(test_db, _batch(rows, "fixture://clash_non_candidate"))
    assert test_db.scalar("SELECT count(*) FROM dim_candidate") == 1


# --------------------------------------------------------------------------
# Cost
# --------------------------------------------------------------------------

#: A batch of this size joined against this many candidates is what the real
#: ingest does (250 k rows, ~10 k candidates), scaled down so the test stays
#: under a second.  It is big enough that the reverted query plans -- and runs
#: -- as a cartesian product, which is what makes the guard below bite.
_GUARD_STAGING_ROWS = 60_000
_GUARD_CANDIDATES = 10_000

#: The join may look at each staged row a constant number of times.  Measured
#: on this fixture: the shipped query pushes 150 000 tuples (2.5x the batch),
#: the reverted one 550 065 000 (9 168x).  A 20x budget therefore sits 8x above
#: what is healthy and 458x below what is broken -- the multiplier is not
#: delicate, and it is not meant to police small drifts.
_GUARD_TUPLE_BUDGET = 20


def _executor_tuples(db, sql: str) -> int:
    """Total tuples the executor pushed through every node: sum(rows x loops).

    Planner-shape agnostic on purpose.  A rescanned inner side reports
    ``loops`` > 1, so a cartesian product shows up here as a number three or
    four orders of magnitude too large no matter which node it hides behind.
    """
    raw = db.fetch(f"EXPLAIN (ANALYZE, TIMING OFF, FORMAT JSON) {sql}")[0][0]
    plan = (raw if isinstance(raw, list) else json.loads(raw))[0]["Plan"]
    total = 0
    stack = [plan]
    while stack:
        node = stack.pop()
        total += node.get("Actual Rows", 0) * node.get("Actual Loops", 1)
        stack.extend(node.get("Plans", ()))
    return total


@pytest.fixture
def _clash_workload(test_db):
    """A realistic, NEVER-ANALYSED ``staging.votes`` beside a full-size
    ``dim_candidate`` -- the exact statistical situation ``_publish_votes``
    hands the planner."""
    test_db.execute(
        """
        INSERT INTO dim_candidate (id, uf, race, round, number, name)
        SELECT 500000 + g, 'RR', 'GOV', 1, g, 'CANDIDATE NAME NUMBER ' || g
        FROM generate_series(1, %s) g
        """,
        (_GUARD_CANDIDATES,),
    )
    test_db.execute("ANALYZE dim_candidate")
    test_db.execute("DROP TABLE IF EXISTS staging.votes")
    test_db.execute(ingest._VOTES_STAGING_DDL)
    test_db.execute(
        """
        INSERT INTO staging.votes
            (uf, mun_code, mun_name, zone, section, race, round, votavel_kind,
             cand_number, cand_name, party_number, votes, election_id,
             section_id, cand_uf, candidate_id)
        SELECT 'RR', '03018', 'BOA VISTA', 1, g, 'GOV', 1,
               CASE WHEN g %% 12 = 0 THEN 'branco' ELSE 'candidate' END,
               c.n, 'CANDIDATE NAME NUMBER ' || c.n, 42, 7, 546,
               3018000100000::bigint + g, 'RR',
               CASE WHEN g %% 12 = 0 THEN NULL ELSE 500000 + c.n END
        FROM generate_series(1, %s) g,
             LATERAL (SELECT 1 + (g %% %s) AS n) c
        """,
        (_GUARD_STAGING_ROWS, _GUARD_CANDIDATES),
    )
    # Deliberately NO `ANALYZE staging.votes`: the ingest does not run one, and
    # adding one does not change the plan anyway (that was the disproved
    # hypothesis).  The guard has to hold with zero statistics.
    assert test_db.scalar(
        "SELECT reltuples FROM pg_class WHERE oid = 'staging.votes'::regclass"
    ) == -1
    yield test_db
    test_db.execute("DROP TABLE IF EXISTS staging.votes")


def test_clash_assertion_is_not_quadratic(_clash_workload):
    """The assertion must cost O(batch), not O(batch x dim_candidate).

    The lower bound is half the point: it pins that the fast query still reads
    the WHOLE batch.  A "fix" that sampled the batch, or that compared only the
    rows dim_candidate happened to gain in this batch, would sail under the
    upper bound -- and would no longer be the assertion the plan contracted for.
    """
    db = _clash_workload
    tuples = _executor_tuples(db, ingest._CANDIDATE_CLASH_SQL)
    assert tuples >= _GUARD_STAGING_ROWS, (
        f"only {tuples:,} tuples for a {_GUARD_STAGING_ROWS:,}-row batch: the "
        "assertion is no longer looking at every staged row"
    )
    budget = _GUARD_TUPLE_BUDGET * _GUARD_STAGING_ROWS
    assert tuples <= budget, (
        f"the clash assertion pushed {tuples:,} tuples through the executor for "
        f"a {_GUARD_STAGING_ROWS:,}-row batch (budget {budget:,}). That is the "
        "cartesian-product plan coming back -- see the comment in "
        "ingest._publish_votes before touching the query."
    )


def test_the_reverted_query_would_fail_this_guard(_clash_workload):
    """The guard above is not vacuous: the ORIGINAL spelling really does blow it.

    Kept as an executable record of the bug, and as proof that this fixture is
    big enough for the guard to bite -- reverting the query here really does
    push 550 M tuples against a 1.2 M budget.  This only PLANS the old query;
    running it takes ~40 s even at test scale.

    This is the one assertion in the file that depends on a planner decision
    rather than on semantics.  If it ever fails on its own, check the Postgres
    version first (docker-compose pins 16): a planner that stopped choosing the
    cross product would be good news, and this test -- not the fix -- is what
    should then be deleted.
    """
    db = _clash_workload
    reverted = """
        SELECT d.uf, d.race, d.round, d.number, d.name, s.cand_name
        FROM staging.votes s
        JOIN dim_candidate d ON d.id = s.candidate_id
        WHERE s.votavel_kind = 'candidate'
          AND s.cand_name IS DISTINCT FROM d.name
        GROUP BY 1, 2, 3, 4, 5, 6
        LIMIT 5
    """
    raw = db.fetch(f"EXPLAIN (FORMAT JSON) {reverted}")[0][0]
    plan = json.dumps(raw if isinstance(raw, list) else json.loads(raw))
    assert "Nested Loop" in plan, plan
    # The equijoin demoted from a join condition to a filter IS the cross
    # product: nothing restricts the inner side per outer row.
    assert "Join Filter" in plan and "candidate_id" in plan, plan
    assert "Hash Cond" not in plan and "Merge Cond" not in plan, plan
