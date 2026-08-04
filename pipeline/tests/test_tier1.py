import pytest
import polars as pl
from observatory.detectors import tier1

pytestmark = pytest.mark.db


def test_arithmetic_violation_flagged(test_db, seed_sections):
    seed_sections(test_db, [dict(comp=100, blank=5, null=5, nominal=90, legenda=5, aptos=120)])  # 105≠100
    tier1.Arithmetic().run(test_db)
    scores = tier1.read_scores(test_db, "t1_arithmetic")
    assert scores.height == 1 and scores["score"][0] >= 1.0


def test_clean_section_with_legenda_scores_zero(test_db, seed_sections):
    seed_sections(test_db, [dict(comp=100, blank=5, null=5, nominal=85, legenda=5, aptos=120)])
    tier1.Arithmetic().run(test_db)
    assert tier1.read_scores(test_db, "t1_arithmetic")["score"].max() == 0.0


def test_comparecimento_above_aptos_is_flagged(test_db, seed_sections):
    # AMENDED A1: no secoes-agregadas exemption exists -- TSE absorbs aggregated
    # sections into the host, so comp > aptos is always a real finding.
    seed_sections(test_db, [dict(comp=350, blank=5, null=5, nominal=335, legenda=5,
                                 aptos=300)])
    tier1.Arithmetic().run(test_db)
    assert tier1.read_scores(test_db, "t1_arithmetic")["score"].max() >= 1.0


def test_missing_race_flagged_against_derived_matrix(test_db, seed_sections):
    # two sections, same UF/round: one reports PRES+GOV, one only PRES ->
    # the DERIVED matrix expects GOV, so exactly the second section is flagged
    seed_sections(test_db, [dict(races=["PRES", "GOV"]), dict(races=["PRES"])])
    tier1.CrossRacePresence().run(test_db)
    scores = tier1.read_scores(test_db, "t1_presence")
    assert scores.filter(pl.col("score") >= 1.0).height == 1
