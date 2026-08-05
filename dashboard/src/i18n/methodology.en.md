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
## Official data, scope, and integrity

The Observatory uses open data published by the Tribunal Superior Eleitoral (TSE) for the 2022 election. The dataset includes section-level results — each section is served by an *urna*, or electronic voting machine — for every race in both rounds, together with section-detail files covering registered voters, turnout, abstentions, nominal and party-list votes, blank votes, and null votes. What the site displays depends on the loaded export: at this stage only Roraima has been processed, so no result on the site should be read as a national conclusion.

Raw files are frozen after download and remain re-downloadable from the TSE sources. Every file published by the project has a content hash in its filename and an entry in `data-manifest.json`. This makes the exact input traceable, allows the load to be repeated, and prevents a page from mixing files produced by different runs.

Before analysis, a reconciliation gate aggregates the data by municipality, race, and round and compares those totals with the TSE's official totals. It also checks the corresponding vote and statistics fields within sections. Every discrepancy is recorded for diagnosis; an unexpected, unexplained difference stops publication instead of being silently adjusted or discarded.

<!-- section:detectors -->
## How the indicators work

The indicators look for results that are unusual against a baseline suited to the quantity being measured. Whenever sections receive scores, the page reports the baseline, sample size, and associated score or probability. A higher score means greater distance from that reference; it does not turn a statistical difference into an account of what caused it.

### Arithmetic consistency and cross-race presence

Arithmetic consistency checks, within each section, race, and round, whether nominal, party-list, blank, and null votes add up to turnout, whether turnout is no greater than registered voters, and whether every count is non-negative. Each failed rule adds one point; missing fields are excluded and counted separately. Cross-race presence derives the expected races from each state's own round files and scores the number missing from a section that reported the others.

### Extreme dominance

For presidential candidates with at least 1% of the national vote in the round, this indicator calculates the probability of a result at least as extreme as the one observed. Its beta-binomial baseline uses the municipality's other sections, leaving out the section being assessed, and allows natural overdispersion; fewer than five reference sections triggers a state baseline. The score is `−log10` of the smaller tail probability, while the detail reports candidate, votes `k`, nominal votes `n`, expected share, correlation, and both tails.

### Split-ticket divergence

In the first round, this indicator subtracts the federal-deputy share of each leading presidential candidate's coalition parties from that candidate's presidential share in the same section. It compares the gap with the municipality's median through a robust z-score based on MAD, the median absolute deviation; municipalities with fewer than ten sections use the state reference. The score is half the absolute z-score, so alliance patterns shared throughout a municipality are not treated as local departures.

### Turnout outliers

Turnout is `voters who turned out ÷ registered voters` for each presidential section. The indicator measures distance from the municipal median with a robust z-score, using `1.4826 × MAD` as its scale and retaining both high and low departures; the score is `|z| ÷ 2`. If there are fewer than ten sections or MAD is zero, the reference falls back to the state. Published detail identifies the observed rate, median, MAD, z-score, and baseline.

### Composite indicator and clustering

For each section and round, scores from arithmetic consistency, cross-race presence, extreme dominance, turnout, and split-ticket divergence are converted to mean-rank percentiles within each indicator. The composite is the mean of available percentiles when at least two exist; otherwise it is not computed. The highest-signal 1% is then tested for concentration in electoral zones and municipalities under a hypergeometric null, with Benjamini-Hochberg correction at `q = 0.05` within each testing family.

### Election fingerprint

For each state and presidential round, this screen builds a 100 × 100 grid crossing turnout with the national winner's share of valid votes. Corner mass is the fraction of sections above 90% on both axes, summarising the signature described by Klimek and co-authors. The output describes a state-wide distribution and produces neither section scores nor a section ranking.

### Digit distributions

For the two leading presidential candidates, second digits in counts of at least ten votes are compared with Benford's distribution; last digits in counts of at least one hundred are compared with a uniform distribution. Chi-square tests are separated by state and municipality-size stratum. Each stratum reports `n`, frequencies, χ², and a p-value, or explains why the sample was insufficient; no section receives a score.

### Urna models

The machine-model comparison uses the second presidential round and only municipalities containing both pre-2020 urnas and 2020-model machines. It estimates Bolsonaro's share difference between generations within each municipality, then combines those differences with vote weighting and a municipality-clustered 95% confidence interval. The raw gap is also shown to expose geographic confounding, but it is not the finding; this is another distribution analysis, with no per-section score.

<!-- section:caveats -->
## Limits of statistical interpretation

A statistical signal is not proof of fraud, just as an absence of signal does not certify that no irregularity occurred. The Observatory shows where a result departs from its chosen baseline and by how much. Interpretation requires the section's context, the sample size, and the score or p-value. When a test was not computed, the site states that separately from a completed test in which nothing crossed the threshold.

Deckert, Myagkov, and Ordeshook (2011) show that Benford-style tests on election returns can yield both false positives and false negatives and are sensitive to how precincts are sized and districts are drawn. A failed second- or last-digit test therefore establishes only disagreement with the reference distribution in that stratum. It is not evidence of fraud, while a high p-value is not a certificate of regularity.

Brazil has roughly 470,000 sections, with several comparisons made for each. Even if every observation followed its model, some would receive high scores by chance. For the composite high-set clustering check, zone and municipality p-values are adjusted with the Benjamini-Hochberg procedure at a false-discovery rate of `q = 0.05`; the other ranked lists are screens, not tests promising a global error rate.

Sections with few voters produce noisier proportions, and small municipal reference sets may represent local variation poorly; details therefore report `n` and identify state-level fallbacks. Anomalies also have ordinary explanations far more often than exotic ones: electorate composition, local alliances, administrative aggregation, recording errors, and geography are possibilities to investigate, neither automatic excuses nor conclusions in advance.

<!-- section:reproduce -->
## Reproducing the analysis

The complete workflow is in the project repository and runs on an ordinary computer without paid services. These commands download official files, build the database, perform the checks, and generate the same static files read by the site:

1. `make pipeline UF=RR` — runs the complete workflow for one state, using Roraima as the example.
2. `make pipeline-all` — repeats that workflow for every federative unit.
3. `make test-pipeline` and `make test-dashboard` — run the processing and website test suites, respectively.

The repository has three parts: `pipeline/` contains download, loading, detectors, and export code in Python; `dashboard/` contains this site; and `data/` contains raw files and the published export. During loading, the reconciliation gate checks reconstructed sums against TSE totals and stops on unexplained differences. After export, files are read back and validated before they can be published.

Running the code is not required to audit a page's provenance. The `data-manifest.json` records the schema version and content-hashed filenames, tying an export to one run without relying on a date or mutable file. Anyone repeating the workflow with the same frozen inputs can check the totals, inspect detector parameters, and compare the resulting artifacts.

<!-- section:citations -->
## References and their role

The works listed below support the beta-binomial model, the turnout–vote-share fingerprint, the digit tests, and the limits of those tests; each entry identifies its specific contribution to this methodology without transferring its conclusions to the data analysed here.
