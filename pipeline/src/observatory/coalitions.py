"""2022 presidential coalitions, as party NUMBERS.

WHY NUMBERS AND NOT SIGLAS
    The votação file (Task 3) carries ``NR_PARTIDO`` -- a number -- on every
    votável row, and nothing else.  ``dim_candidate.coalition`` does hold the
    TSE coalition *string* ("PP / REPUBLICANOS / PL"), but it is prose: it
    wraps federations in labels ("Federação Brasil da Esperança - FE
    BRASIL(PT/PC do B/PV)"), it is per-candidacy rather than per-party, and it
    is absent entirely on annulled candidacies.  A detector that has to sum
    "the coalition's federal-deputy votes in this section" needs a set of
    party NUMBERS, so that is what this module ships.

WHAT IS FROZEN HERE AND WHAT IS NOT
    :data:`PRESIDENTIAL_COALITIONS_2022_R1` is a reviewed constant -- the two
    leading round-1 presidential coalitions, as registered with the TSE.  It
    is deliberately NOT derived from the warehouse at run time: a coalition is
    a legal fact about the 2022 election, not a property of whichever UF
    happens to be loaded, and a detector whose reference groups silently move
    with the data is not measuring what it claims to measure.

    :func:`parse_coalition` exists for the other half of that discipline.  It
    reads TSE's own string back into numbers so a detector can CHECK its
    constants against the warehouse and say so in its summary.  It never
    replaces them: if the two disagree that is a finding to report, not a
    conflict to resolve silently.

SOURCE (verified against the loaded warehouse, RR 2022, on 2026-08-04)
    TSE ``consulta_cand_2022`` / the coalition strings carried on
    ``dim_candidate.coalition`` for the presidential candidacies:

      22  JAIR MESSIAS BOLSONARO      "PP / REPUBLICANOS / PL"
          -> coligação "Pelo Bem do Brasil"                    {22, 11, 10}
      13  LUIZ INÁCIO LULA DA SILVA   "Federação Brasil da Esperança - FE
          BRASIL(PT/PC do B/PV) / SOLIDARIEDADE / Federação PSOL REDE(PSOL/
          REDE) / PSB / AGIR / AVANTE / PROS"
          -> coligação "Brasil da Esperança"
             {13, 65, 43, 77, 50, 18, 40, 36, 70, 90}

    Party numbers are TSE's permanent party numbers (the ones a voter types
    into the urna), not the sequential ``NR_PARTIDO`` of any single file.
"""

from __future__ import annotations

import unicodedata

__all__ = [
    "PARTY_NUMBER_BY_SIGLA",
    "PARTY_SIGLA_BY_NUMBER",
    "PRESIDENTIAL_COALITIONS_2022_R1",
    "coalition_name",
    "coalition_parties",
    "parse_coalition",
    "siglas_for",
]

#: Party number -> sigla, 2022 roster (the parties that fielded candidacies or
#: legendas in the 2022 general election).  Used only to make the warehouse
#: cross-check in :func:`parse_coalition` readable; nothing scores off it.
PARTY_SIGLA_BY_NUMBER: dict[int, str] = {
    10: "REPUBLICANOS",
    11: "PP",
    12: "PDT",
    13: "PT",
    14: "PTB",
    15: "MDB",
    16: "PSTU",
    18: "REDE",
    19: "PODE",
    20: "PSC",
    21: "PCB",
    22: "PL",
    23: "CIDADANIA",
    27: "DC",
    28: "PRTB",
    29: "PCO",
    30: "NOVO",
    33: "PMN",
    35: "PMB",
    36: "AGIR",
    40: "PSB",
    43: "PV",
    44: "UNIÃO",
    45: "PSDB",
    50: "PSOL",
    51: "PATRIOTA",
    55: "PSD",
    65: "PC do B",
    70: "AVANTE",
    77: "SOLIDARIEDADE",
    80: "UP",
    90: "PROS",
}


def _key(text: str) -> str:
    """Accent-free, space-free, upper-case -- so "PC do B" == "PCdoB"."""
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in stripped if not unicodedata.combining(ch))
    return "".join(ch for ch in stripped.upper() if ch.isalnum())


#: Sigla (normalised) -> party number, including the aliases TSE's own strings
#: use interchangeably.
PARTY_NUMBER_BY_SIGLA: dict[str, int] = {
    **{_key(sigla): number for number, sigla in PARTY_SIGLA_BY_NUMBER.items()},
    _key("PCdoB"): 65,
    _key("SD"): 77,
    _key("PODEMOS"): 19,
    _key("UNIAO BRASIL"): 44,
    _key("REPUBLICANOS10"): 10,
}

#: Tokens that are federation LABELS rather than parties.  A federation always
#: spells its members out in parentheses right after the label, so dropping the
#: label loses nothing: "Federação PSOL REDE(PSOL/REDE)" still yields {50, 18}.
_FEDERATION_PREFIX = _key("Federacao")


#: The reviewed constant: round-1 presidential candidate number -> the party
#: numbers of the coalition that registered the candidacy.  Both leading
#: coalitions; the minor candidacies each ran on a single party and are not
#: split-ticket questions worth asking, so they are not listed.
PRESIDENTIAL_COALITIONS_2022_R1: dict[int, tuple[int, ...]] = {
    # "Pelo Bem do Brasil" -- PL / PP / REPUBLICANOS
    22: (22, 11, 10),
    # "Brasil da Esperança" -- FE BRASIL (PT/PC do B/PV) + SOLIDARIEDADE +
    # Federação PSOL REDE (PSOL/REDE) + PSB + AGIR + AVANTE + PROS
    13: (13, 65, 43, 77, 50, 18, 40, 36, 70, 90),
}

#: Human-readable coalition names, for summaries and the methodology page.
_COALITION_NAMES: dict[int, str] = {
    22: "Pelo Bem do Brasil",
    13: "Brasil da Esperança",
}


def coalition_parties(candidate_number: int) -> tuple[int, ...]:
    """Party numbers of ``candidate_number``'s coalition.  ``()`` if unknown.

    An empty tuple is a real answer -- "this candidacy has no reviewed
    coalition" -- and a caller must treat it as "do not score", never as "the
    coalition is empty, so its share is 0".
    """
    return PRESIDENTIAL_COALITIONS_2022_R1.get(int(candidate_number), ())


def coalition_name(candidate_number: int) -> str | None:
    return _COALITION_NAMES.get(int(candidate_number))


def siglas_for(numbers: tuple[int, ...] | list[int]) -> list[str]:
    """Siglas for a list of party numbers, in the order given."""
    return [PARTY_SIGLA_BY_NUMBER.get(int(n), str(n)) for n in numbers]


def parse_coalition(text: str | None) -> tuple[set[int], list[str]]:
    """TSE's coalition string -> ``(party numbers, unrecognised tokens)``.

    The separator is ``/``; federations add parentheses around their members.
    Both are flattened, the federation label itself is dropped (its members
    follow it), and anything left that is not a known sigla comes back in the
    second element rather than being silently ignored -- an unrecognised token
    is the signal that the roster below has gone stale.

    ``None`` (an annulled candidacy) yields ``(set(), [])``.
    """
    if not text:
        return set(), []
    flat = text.replace("(", "/").replace(")", "/")
    numbers: set[int] = set()
    unknown: list[str] = []
    for raw in flat.split("/"):
        token = _key(raw)
        if not token or token.startswith(_FEDERATION_PREFIX):
            continue
        number = PARTY_NUMBER_BY_SIGLA.get(token)
        if number is None:
            unknown.append(raw.strip())
        else:
            numbers.add(number)
    return numbers, unknown
