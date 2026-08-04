import gzip
import json

import pytest

from observatory.export import shards


def test_round_trip_totals_match_input(shard_input_fixture):
    doc = shards.encode_shard(shard_input_fixture)
    assert doc["v"] == 1
    total = sum(doc["votes"]["PRES_1"]["n"])
    assert total == shard_input_fixture.expected_pres1_total
    assert len(doc["zone"]) == len(doc["section"]) == len(doc["stats"]["PRES_1"]["aptos"])


def test_no_zero_vote_pairs_and_sorted(shard_input_fixture):
    v = shards.encode_shard(shard_input_fixture)["votes"]["PRES_1"]
    assert min(v["n"]) > 0
    assert v["sec"] == sorted(v["sec"])


def test_race_ids_canonical_and_score_keys_valid(shard_input_fixture):
    doc = shards.encode_shard(shard_input_fixture)
    assert all(r.rsplit("_", 1)[1] in {"1", "2"} for r in doc["races"])  # every id carries a round
    assert set(doc["scores"]) <= set(doc["races"]) | {"ALL_1", "ALL_2"}


def test_oversized_municipality_splits_by_zone(big_shard_input, tmp_path):   # synthetic >300KB gz
    out = shards.write_shard(big_shard_input, tmp_path)
    idx = json.loads(gzip.decompress(out.index_bytes))
    assert idx["split"] is True and len(idx["zones"]) >= 2


def test_subshard_over_1mb_hard_fails(giant_zone_input, tmp_path):
    with pytest.raises(shards.ShardBudgetError):
        shards.write_shard(giant_zone_input, tmp_path)
