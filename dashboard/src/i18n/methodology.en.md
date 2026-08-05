<!--
  METHODOLOGY — EN. This file is the SKELETON; the copy pass writes the prose.
  pt-BR is the authored language; this file is its faithful parallel, not a
  machine-flavoured translation of it.

  STRUCTURE (binding). Each of the five sections below is introduced by an HTML
  comment holding its id, in the form "section:<id>". The five ids, in this
  order, are: sources, detectors, caveats, reproduce, citations.

  Those comments are the contract. The table of contents, and the links the
  detector pages point at (#caveats), address the ID — never the heading text —
  precisely so the headings can be rewritten freely. Reword every heading; do
  not touch, reorder, or remove a marker comment. Every section must open with
  its own "## " heading. `src/pages/Methodology/sections.ts` throws at import
  time if any of that is broken, and the page fails to load rather than
  quietly shipping without a section.

  SUPPORTED MARKDOWN — anything else is rendered as a verbatim paragraph, so
  nothing is ever lost, but it will look wrong:

    `## heading`  — one per section, and its first line
    `### heading` — subheadings inside a section
    paragraphs, separated by a blank line
    `- item` and `1. item` lists — ONE LINE PER ITEM, no wrapping
    [text](https://example.org)   `code`   **bold**   *italic*

  No tables, no block quotes, no fenced code blocks, no images, no raw HTML.

  The reference list under the last section is NOT written here: it is
  structured data in `src/pages/Methodology/citations.ts` and the page renders
  it after this file's prose.
-->

<!-- section:sources -->
## Where the data comes from

TODO (Task 22): the origin of the data, the scope of this publication, and
what each official file actually contains.

TODO (Task 22): what is checked on load — in particular the reconciliation
gate against the totals the electoral court itself published — and what
happens to a row that does not balance.

<!-- section:detectors -->
## How each indicator is computed

TODO (Task 22): the idea every indicator shares — comparing a polling station
against its own neighbourhood rather than against a national expectation.

### Per-section indicators

TODO (Task 22): one paragraph per indicator that scores individual sections,
naming the baseline it uses and what its threshold means.

### Distribution screens

TODO (Task 22): one paragraph per screen that describes a whole distribution
instead of scoring sections, and why it produces no ranking.

<!-- section:caveats -->
## What these numbers do not say

TODO (Task 22): the editorial stance — a statistical signal is not proof of
fraud, and every signal is published with its local baseline, its sample size
and its score or p-value.

TODO (Task 22): the limits of digit tests on election data, engaging
explicitly with the objections raised by Deckert, Myagkov and Ordeshook
(2011).

TODO (Task 22): multiple testing, small samples, and what a finding means when
a section holds only a handful of votes.

<!-- section:reproduce -->
## How to reproduce this

The whole pipeline lives in the project repository and runs on an ordinary
machine, with no paid services. One state at a time:

1. `make pipeline UF=RR` — downloads the official files, loads them into Postgres, runs the reconciliation gate, runs the detectors, and writes the static files this site reads.
2. `make pipeline-all` — the same for every federative unit.
3. `make test-pipeline` and `make test-dashboard` — the pipeline's test suite and the site's.

The repository is in three parts: `pipeline/` (download, load, detectors and
export, in Python), `dashboard/` (this site) and `data/` (the raw files and the
published export). Every published file carries a hash of its own contents in
its name and is listed in `data-manifest.json`, so one export can never mix
files from two different runs.

TODO (Task 22): revise the above and add what a reader who will never run any
of it still needs, namely that they could.

<!-- section:citations -->
## References

TODO (Task 22): a sentence introducing the works below and what each one
contributes to the indicators on this page.
