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

The urna-correspondence files (section <-> urna internal number, needed by the
urna-model detector) are published on CKAN with an unpredictable timestamp
baked into the filename, so they carry `url=None, resolver="ckan"` and are
resolved at fetch time via the CKAN `package_show` API. Never hardcode a
timestamped filename.
"""

from __future__ import annotations

from dataclasses import dataclass

CDN = "https://cdn.tse.jus.br/estatistica/sead/odsele"

CKAN_API = "https://dadosabertos.tse.jus.br/api/3/action/package_show?id="
CKAN_CORRESPONDENCE = {
    1: "resultados-2022-correspondencias-esperadas-e-efetivadas-1-turno",
    2: "resultados-2022-correspondencias-esperadas-e-efetivadas-2-turno",
}

#: The 27 federative units (26 states + DF). Exterior ("ZZ") is not a
#: downloadable UF — see the module docstring.
UFS = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS",
    "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC",
    "SE", "SP", "TO",
)

ROUNDS = (1, 2)

#: `CESP` = correspondência esperada (planned), `CEFT` = correspondência
#: efetivada (what was actually deployed). Both are published per UF per round;
#: we take both so the urna-model detector can cross-check them.
CORRESPONDENCE_PREFIXES = ("CESP", "CEFT")


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


def _votes_section(uf: str) -> Dataset:
    return Dataset(
        name=f"votacao_secao_2022_{uf}",
        url=f"{CDN}/votacao_secao/votacao_secao_2022_{uf}.zip",
        kind="votes_section",
        uf=uf,
    )


def _national() -> list[Dataset]:
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
        Dataset(
            name="modelourna_numerointerno",
            url=f"{CDN}/modelo_urna/modelourna_numerointerno.zip",
            kind="modelourna_numerointerno",
            uf=None,
        ),
    ]


def _correspondence(uf: str) -> list[Dataset]:
    return [
        Dataset(
            name=f"{prefix}_{rnd}t_{uf}",
            url=None,
            kind="urna_correspondence",
            uf=uf,
            resolver="ckan",
            round=rnd,
            ckan_id=CKAN_CORRESPONDENCE[rnd],
        )
        for rnd in ROUNDS
        for prefix in CORRESPONDENCE_PREFIXES
    ]


def datasets(uf: str | None = None) -> list[Dataset]:
    """Every source file needed for a run.

    `uf` narrows only the per-UF files (section votes and urna correspondence);
    the national files are always required. A single-UF run therefore still
    pulls ~1.1 GB of national archives (including the BR section file — see
    amendment A2 below) — that is the floor, not a bug.
    """
    out: list[Dataset] = []
    if uf is None:
        out.extend(_votes_section(u) for u in UFS)
    else:
        out.append(_votes_section(uf))

    # AMENDMENT A2 (execution, 2026-08-04): the national BR section file is
    # REQUIRED for every run, not just national ones. Per-UF votacao_secao files
    # carry state races only (CD_CARGO 3/5/6/7, round 1, CD_ELEICAO 546/547);
    # ALL presidential section votes (cargo 1, both rounds) and all ZZ/exterior
    # rows live exclusively here. Without it the presidential detectors
    # (Tasks 7/13/14) and Task 5's president leg have no input at all.
    # Single-UF runs download it and filter to the target UF at ingest.
    out.append(
        Dataset(
            name="votacao_secao_2022_BR",
            url=f"{CDN}/votacao_secao/votacao_secao_2022_BR.zip",
            kind="votes_section",
            uf="BR",
        )
    )

    out.extend(_national())

    for u in UFS if uf is None else [uf]:
        out.extend(_correspondence(u))

    return out
