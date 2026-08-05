"""Manifest of the TSE source files the observatory ingests.

Every URL here was probed live with a HEAD request on 2026-08-04 (see the
implementation notes for Task 2). Two facts are load-bearing and easy to get
wrong:

* There is **no per-UF `detalhe_votacao_secao_2022_{UF}.zip`** — that pattern
  404s. The detail (comparecimento/abstenção) data ships as a single national
  file, so even a single-UF run has to pull it.
* `votacao_secao_2022_ZZ.zip` (exterior) does **not** exist either. Exterior
  sections live only inside the national `votacao_secao_2022_BR.zip`, which is
  why a national run adds that file on top of the 27 per-UF ones.

AMENDMENT A6 (execution, 2026-08-05): the urna-correspondence archives
(CESP/CEFT) and `modelourna_numerointerno` are **no longer downloaded**. The
urna-model detector was written against them, but `detalhe_votacao_secao`
publishes `CD_MODELO_URNA` per section per round directly, so nothing reads
them any more — verified: no ingest kind consumes either, and the only surviving
mention is one explanatory comment in `detectors/urna_model.py`.

Dropping them costs nothing and saves ~0.7 GiB nationally (54 correspondence
zips at ~2.1 MB per UF, plus the 0.3 MB model archive) — the cheapest of the
storage levers the national run needs. Every dataset now resolves to a direct
CDN URL, so the CKAN `package_show` resolver has no callers either.
"""

from __future__ import annotations

from dataclasses import dataclass

CDN = "https://cdn.tse.jus.br/estatistica/sead/odsele"

#: The 27 federative units (26 states + DF). Exterior ("ZZ") is not a
#: downloadable UF — see the module docstring.
UFS = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS",
    "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC",
    "SE", "SP", "TO",
)

ROUNDS = (1, 2)


@dataclass(frozen=True)
class Dataset:
    """One downloadable source file.

    `name` is the stable logical id. For direct downloads it is also the
    on-disk stem (`{name}.zip`); for CKAN-resolved files it is the filename
    prefix that the resolver matches against, and the on-disk name is the
    basename of the resolved URL (which carries the publication timestamp).
    """

    name: str
    url: str | None
    kind: str
    uf: str | None
    resolver: str | None = None
    round: int | None = None
    ckan_id: str | None = None

    @property
    def filename(self) -> str | None:
        """On-disk filename, when it is knowable without resolving."""
        if self.url is None:
            return None
        return self.url.rsplit("/", 1)[-1]


def votes_section(uf: str) -> Dataset:
    """The `votacao_secao_2022_{uf}.zip` archive for one UF (or ``"BR"``)."""
    return Dataset(
        name=f"votacao_secao_2022_{uf}",
        url=f"{CDN}/votacao_secao/votacao_secao_2022_{uf}.zip",
        kind="votes_section",
        uf=uf,
    )


def _rollups() -> list[Dataset]:
    return [
        Dataset(
            name="detalhe_votacao_secao_2022",
            url=f"{CDN}/detalhe_votacao_secao/detalhe_votacao_secao_2022.zip",
            kind="detail_section",
            uf=None,
        ),
        Dataset(
            name="votacao_candidato_munzona_2022",
            url=f"{CDN}/votacao_candidato_munzona/votacao_candidato_munzona_2022.zip",
            kind="rollup_cand_munzona",
            uf=None,
        ),
        Dataset(
            name="votacao_partido_munzona_2022",
            url=f"{CDN}/votacao_partido_munzona/votacao_partido_munzona_2022.zip",
            kind="rollup_party_munzona",
            uf=None,
        ),
        Dataset(
            name="detalhe_votacao_munzona_2022",
            url=f"{CDN}/detalhe_votacao_munzona/detalhe_votacao_munzona_2022.zip",
            kind="rollup_detail_munzona",
            uf=None,
        ),
    ]


def national_datasets() -> list[Dataset]:
    """The archives EVERY run needs, whichever UF it targets.

    AMENDMENT A2 (execution, 2026-08-04): the national BR section file is
    REQUIRED for every run, not just national ones. Per-UF votacao_secao files
    carry state races only (CD_CARGO 3/5/6/7, round 1, CD_ELEICAO 546/547);
    ALL presidential section votes (cargo 1, both rounds) and all ZZ/exterior
    rows live exclusively here. Without it the presidential detectors
    (Tasks 7/13/14) and Task 5's president leg have no input at all.
    Single-UF runs download it and filter to the target UF at ingest.

    Consequence for Task 23's storage lever: none of these may ever be
    reclaimed mid-run — see `observatory.storage.reclaim_uf_archive`.
    """
    # A6: no correspondence or modelourna archives — see the module docstring.
    return [votes_section("BR"), *_rollups()]


def datasets(uf: str | None = None) -> list[Dataset]:
    """Every source file needed for a run.

    `uf` narrows only the per-UF section-votes file; the national files are
    always required. A single-UF run therefore still pulls ~1.1 GB of national
    archives (including the BR section file — see :func:`national_datasets`) —
    that is the floor, not a bug.
    """
    per_uf = [votes_section(u) for u in (UFS if uf is None else (uf,))]
    return [*per_uf, *national_datasets()]
