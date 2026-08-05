"""A party's votes can be split across two coalition records in the same zone.

Surfaced by the national run, which stopped on AL:

    duplicate natural key while publishing rollup_party

`rollup_party_munzona`'s unique key was
``(mun_code, zone, race, round, party_number, in_transit)``, which assumes a
party appears at most once per zone per race. In 2022 that is false. It was the
first Brazilian general election with **federações partidárias**, and TSE emits
one row per ``SQ_COLIGACAO``: a party can carry two coalition-sequence records
in the same zone, differing in nothing the old key looked at.

Measured across the real archives (all 27 UFs, A3-filtered):

* **3,665 colliding keys in 10 of 27 states** — AL 107, AM 74, BA 900, CE 820,
  DF 76, GO 259, MS 176, MT 584, PB 460, PE 209.
* Only proportional races (Deputado Federal / Estadual / Distrital).
* `FEDERAÇÃO` rows (PSDB, CIDADANIA, PT, PC do B, PV) and `PARTIDO ISOLADO`
  rows (PROS, PDT, PMB).
* Roraima has none, which is why every gate was green until AL.

The rows are **additive, not duplicative** — measured on BA's 900 collisions:
567 pairs carry different `votes_nominal_valid`, **no** pair repeats a non-zero
`votes_nominal_valid`, and **no** pair carries non-zero legenda on both sides.
So the fix keeps TSE's grain — carry `SQ_COLIGACAO` and put it in the key —
rather than aggregating at ingest. Reconciliation already SUMs this table
(`reconcile.py` `_THEIRS_LEGENDA`, and the nominal cross-check), so summing over
the extra row is what those consumers were already doing across zones.

Aggregating instead would be the dangerous choice: it would silently discard the
coalition split that TSE publishes, and there would be no way to tell afterwards
whether a party's total came from one record or two.

Pre-authored by the orchestrator. Read-only to the implementer.
"""

from __future__ import annotations

import pytest

from observatory import parse


def test_rollup_party_schema_carries_the_coalition_sequence():
    """`DS_COMPOSICAO_COLIGACAO` does not disambiguate — it is equal on both rows.

    The colliding pair differs only in `SQ_COLIGACAO`, so that is the field the
    key needs. `coalition` (the composition text) is not a substitute.
    """
    assert "coalition_seq" in parse.ROLLUP_PARTY_SCHEMA


@pytest.mark.db
def test_two_coalition_records_for_one_party_both_persist(test_db):
    """The exact shape that stopped the national run at AL.

    Two rows, same (mun, zone, race, round, party, in_transit), different
    coalition sequence — the real AL case is PROS (90) for Deputado Federal in
    round 1, one record carrying the votes and one carrying none.
    """
    test_db.execute(
        """
        INSERT INTO rollup_party_munzona
            (uf, mun_code, mun_name, zone, race, round, party_number, party_sigla,
             coalition_seq, votes_legenda_valid, votes_nominal_valid,
             in_transit, election_id)
        VALUES
            ('AL', '28711', 'MACEIO', 18, 'DEPFED', 1, 90, 'PROS',
             '20001681369', 3, 0, false, 546),
            ('AL', '28711', 'MACEIO', 18, 'DEPFED', 1, 90, 'PROS',
             '20001684704', 0, 0, false, 546)
        """
    )

    rows = test_db.fetch(
        """
        SELECT coalition_seq, votes_legenda_valid
        FROM rollup_party_munzona
        WHERE mun_code = '28711' AND zone = 18 AND party_number = 90
        ORDER BY coalition_seq
        """
    )
    assert len(rows) == 2, "both coalition records must survive the load"
    assert [r[0] for r in rows] == ["20001681369", "20001684704"]
    # Additive, so the consumers' SUM is the party's real total in that zone.
    assert sum(r[1] for r in rows) == 3


@pytest.mark.db
def test_unique_key_still_rejects_a_true_duplicate(test_db):
    """Widening the key must not disarm it.

    A3 wants a genuine collision to be a loud duplicate-key failure rather than
    a quiet double count, so two rows identical *including* the coalition
    sequence must still be refused.
    """
    row = """
        INSERT INTO rollup_party_munzona
            (uf, mun_code, mun_name, zone, race, round, party_number, party_sigla,
             coalition_seq, votes_legenda_valid, votes_nominal_valid,
             in_transit, election_id)
        VALUES ('AL', '28452', 'ARAPIRACA', 14, 'DEPFED', 1, 90, 'PROS',
                '20001681369', 1, 0, false, 546)
    """
    test_db.execute(row)
    with pytest.raises(Exception):
        test_db.execute(row)
