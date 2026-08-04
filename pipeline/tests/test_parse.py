import polars as pl
from observatory import parse
from tests.conftest import fixture


def test_votes_excerpt_parses_clean():
    good, bad = parse.read_votes(fixture("tse/votacao_secao_2022_RR_excerpt.csv"))
    assert bad.height == 0
    assert set(good["race"].unique()) <= {"PRES", "GOV", "SEN", "DEPFED", "DEPEST", "DEPDIS"}
    assert set(good["votavel_kind"].unique()) <= {"candidate", "blank", "null", "legenda"}
    assert good.filter(pl.col("votavel_kind") == "legenda").height > 0   # excerpt includes legenda rows
    assert good["votes"].min() >= 0                      # sentinels never leak into counts
    assert (good["mun_code"].str.len_chars() == 5).all()  # canonical zero-padded TEXT code
    assert good.filter(pl.col("cand_name") == "#NULO#").height == 0


def test_detail_excerpt_parses_clean():
    good, bad = parse.read_detail(fixture("tse/detalhe_votacao_secao_2022_excerpt.csv"))
    assert bad.height == 0
    assert {"null_votes", "agg_secoes"} <= set(good.columns)
    assert (good["aptos"].drop_nulls() >= 0).all()


def test_rollup_detail_carries_real_aggregation_counts():
    # AMENDED A1: TSE publishes no section-level aggregation column; the real
    # QT_SECOES_AGREGADAS data exists only at munzona grain.
    good, bad = parse.read_rollup_detail(fixture("tse/detalhe_votacao_munzona_2022_excerpt.csv"))
    assert bad.height == 0
    assert good.filter(pl.col("agg_secoes") > 0).height >= 1


def test_malformed_row_goes_to_bad_not_dropped():
    good, bad = parse.read_votes(fixture("tse/votacao_malformed_excerpt.csv"))
    assert bad.height >= 1 and "reason" in bad.columns
