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
