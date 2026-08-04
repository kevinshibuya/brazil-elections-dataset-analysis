import pytest

from observatory.detectors import digits

pytestmark = pytest.mark.db


def test_benford_conformant_counts_pass(test_db, seed_counts_benford):
    digits.Digits().run(test_db)
    for s in digits.read_strata(test_db):
        if s["test"] == "second_digit":
            assert s["p"] > 0.01


def test_planted_last_digit_bias_detected(test_db, seed_counts_last_digit_biased):
    # counts rounded to end in 0/5 far above uniform expectation
    digits.Digits().run(test_db)
    ld = [s for s in digits.read_strata(test_db) if s["test"] == "last_digit"]
    assert any(s["p"] < 1e-4 for s in ld)


def test_sparse_strata_skipped_not_nan(test_db, seed_counts_small):
    digits.Digits().run(test_db)                       # all counts < 10 -> nothing eligible
    strata = digits.read_strata(test_db)
    assert strata and all(s.get("skipped") and "p" not in s for s in strata)
