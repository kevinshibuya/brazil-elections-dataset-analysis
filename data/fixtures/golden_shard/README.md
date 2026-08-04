# Golden shard — the cross-language decoder fixture

`03158.json.gz` is a **real** RR shard (SÃO LUIZ, 22 sections, zone 4), produced
by the Python encoder in `pipeline/src/observatory/export/shards.py` from the
reconciled `elections` warehouse on 2026-08-04. `expected.json` records the
totals a decoder must reproduce from those exact bytes.

**Read-only for Task 11.** The TypeScript shard decoder asserts against these
two files; changing either is changing the cross-language contract.

Why this municipality: it is small enough to commit (9 KB) while carrying every
schema feature that can go wrong across the language boundary —

| feature | present as |
| --- | --- |
| non-presidential race id | `GOV_1`, `SEN_1`, `DEPFED_1`, `DEPEST_1` |
| two rounds of one race | `PRES_1` + `PRES_2` |
| sparse COO votes, sorted, no zero pairs | `votes.*.{sec,cand,n}` |
| dense stats aligned with `zone`/`section` | `stats.*` (5 arrays × 22) |
| non-zero `legenda` (proportional races only) | `DEPFED_1` 131, `DEPEST_1` 178 |
| cross-race scores | `ALL_1`, `ALL_2` → `t1_presence` |
| `score_detail` for a flagged section | section index `13`, `PRES_1`/`PRES_2` |
| non-ASCII text | `"SÃO LUIZ"`, `"LUIZ INÁCIO LULA DA SILVA"` |

## Regenerating

```
cd pipeline && uv run python 04_export.py --uf RR --shards-only --golden-shard 03158
```

The encoder is deterministic (compact JSON separators, UTF-8, `mtime=0`), so a
re-run against the same warehouse produces byte-identical output — a diff here
means the data or the encoder changed, and Task 11's expectations must be
re-checked before the fixture is updated.
