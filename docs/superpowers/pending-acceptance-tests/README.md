# Pre-authored acceptance tests awaiting their slice

These are RED acceptance tests authored by the orchestrator for Tasks 14, 15 and
16, exactly as the plan's slice contracts specify. They live here rather than in
`pipeline/tests/` because they import detector modules that do not exist yet, so
collecting them would redden the suite.

**When starting Task 14 / 15 / 16:** `git mv` the matching file into
`pipeline/tests/`, confirm it fails for the right reason, then dispatch the slice
with the file marked READ-ONLY to the implementer.

| File | Task | Imports it needs |
| --- | --- | --- |
| `test_turnout.py` | 14 | `observatory.detectors.turnout` + module-level `read_scores` |
| `test_fingerprint.py` | 15 | `observatory.detectors.fingerprint` + `read_scores` |
| `test_digits.py` | 16 | `observatory.detectors.digits` + `read_strata` |

Notes carried from authoring:
- `read_scores(db, "turnout")` is called with the SHARD SCORE KEY, not the DB
  detector name (`t2_turnout`). Task 13 solved the same problem by accepting
  either form via `shards.score_key()` — follow that precedent.
- `Fingerprint().run(db)` returns the `DetectorResult`; the test reads
  `r.state_payloads["RR"]`. Verified `BaseDetector.run` returns it.
- `test_planted_corner_smear_raises_corner_mass` asserts `>= 50/550`, so the
  fixture seeds 50 planted sections ON TOP of `sections=500`.
- Tasks 15 and 16 write NO per-section scores (`sections_scored = 0`).
