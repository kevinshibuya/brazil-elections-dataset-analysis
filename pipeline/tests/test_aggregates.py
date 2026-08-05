import pytest

from observatory.export import aggregates
from tests.conftest import db_total, load_json

pytestmark = pytest.mark.db


def test_anomalies_capped_ranked_with_context(analysis_seeded_db, tmp_out):
    aggregates.write_all(analysis_seeded_db, tmp_out)      # seeded with 600 scored sections
    doc = load_json(tmp_out / "anomalies" / "extreme_dominance.json")
    assert doc["v"] == 1 and len(doc["rows"]) == 500
    scores = [r["score"] for r in doc["rows"]]
    assert scores == sorted(scores, reverse=True)
    assert {"mun_name", "uf", "detail"} <= set(doc["rows"][0])


def test_summary_totals_equal_db_totals(analysis_seeded_db, tmp_out):
    aggregates.write_all(analysis_seeded_db, tmp_out)
    doc = load_json(tmp_out / "summary.json")
    assert doc["races"]["PRES_2"]["total_votes"] == db_total(analysis_seeded_db, "PRES", 2)


def test_annulled_candidacies_are_exported(analysis_seeded_db, tmp_out):
    # AMENDED A4: votes cast at section level but excluded from TSE's official
    # totals are a finding in their own right -- they must reach the site, never
    # be silently dropped.
    aggregates.write_all(analysis_seeded_db, tmp_out)
    doc = load_json(tmp_out / "annulled-candidacies.json")
    assert doc["v"] == 1
    assert doc["total_votes"] > 0 and len(doc["rows"]) > 0
    assert {"cand_number", "cand_name", "race", "round", "votes"} <= set(doc["rows"][0])


def test_municipalities_index_is_columnar_with_centroids(analysis_seeded_db, tmp_out):
    aggregates.write_all(analysis_seeded_db, tmp_out)
    doc = load_json(tmp_out / "municipalities-index.json")
    assert doc["v"] == 1
    n = len(doc["code"])
    for key in ("name", "uf", "lat", "lon"):
        assert len(doc[key]) == n
    assert all(len(c) == 5 for c in doc["code"])      # canonical zero-padded TEXT


def test_municipalities_index_carries_turnout(analysis_seeded_db, tmp_out):
    """The overview choropleth's third metric (Task 19) needs turnout at
    MUNICIPALITY grain -- summary.json only has it nationally and per state."""
    aggregates.write_all(analysis_seeded_db, tmp_out)
    doc = load_json(tmp_out / "municipalities-index.json")

    turnout = doc["turnout"]
    assert len(turnout) == len(doc["code"])
    present = [t for t in turnout if t is not None]
    assert present, "every seeded municipality has aptos and comparecimento"
    assert all(0.0 < t <= 1.0 for t in present)

    # It must be the vote-weighted municipal ratio, not a mean of section ratios.
    # SUM(BIGINT) is numeric in Postgres, so cast back or psycopg hands back a
    # Decimal. Tolerance is 1e-6, not 1e-9: _ratio rounds to SHARE_DECIMALS = 6
    # so the municipal column quotes the same rounding as summary.json's
    # national turnout and states/{UF}.json's.
    code = doc["code"][0]
    race, rnd = doc["headline"]["race_id"].split("_")
    aptos, comparecimento = analysis_seeded_db.fetch(
        """
        SELECT SUM(f.aptos)::BIGINT, SUM(f.comparecimento)::BIGINT
        FROM fact_section_stats f JOIN dim_section s ON s.id = f.section_id
        WHERE s.mun_code = %s AND f.race = %s AND f.round = %s
        """,
        (code, race, int(rnd)),
    )[0]
    assert abs(turnout[0] - comparecimento / aptos) < 1e-6
