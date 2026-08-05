# Deploying the Election Anomaly Observatory to Cloudflare Pages

The site is a fully static bundle: a Vite SPA plus a directory of content-hashed
data files. There is no server, no database, no runtime secret and no build-time
environment variable. Publishing it is "upload `dashboard/dist/`".

**CI/CD is deliberately out of scope for v1.** There is no GitHub Action, no
Pages Git integration, no deploy-on-push. Every deployment is a person running
the commands below, on purpose, after looking at the pre-deploy gate output.
**The final production deploy is the user's action** — an agent may prepare and
verify the build, but does not run `wrangler pages deploy` against production.

---

## 0. What actually gets deployed

```
dashboard/dist/
  index.html                       the SPA shell
  _headers                         Pages config (not served as an asset)
  assets/index-<hash>.js|css       Vite output, content-hashed
  geo/br-municipalities.topo.json  map geometry, NOT hashed, election-independent
  data/data-manifest.json          the ONE mutable file
  data/**/<name>.<hash>.json[.gz]  the export, content-hashed
```

`vite build` copies everything in `dashboard/public/` into `dist/` — including
`public/data/`, which is where `make export UF=…` writes. So `dist/` after a
build is the exact upload set, and it is what the limit gate measures.

### Hosting budget (Cloudflare Pages free tier)

| Limit | Value |
| --- | --- |
| Files per deployment | 20,000 |
| Size of any single file | 25 MiB |

`dashboard/scripts/check-limits.mjs` enforces both. Its measured output goes
into [`baselines.md`](./baselines.md).

---

## 1. Why the caching policy is safe: immutability by construction

The exporter content-hashes every file it writes. `summary.json` is published as
`summary.d1fe50f6.json`; a municipality shard as `shards/03018.f337e906.json.gz`.
The hash is over the file's own bytes, so **a URL under `/data/` can never change
meaning**. That is why `dashboard/public/_headers` gives everything under
`/data/` a one-year `immutable` cache.

`data/data-manifest.json` is the single exception and the single mutable file. It
maps logical names (`summary.json`, `shards/03018.json.gz`) to the hashed paths
of the *current* export. Nothing in the client string-templates a data URL; every
fetch goes through the manifest (`dashboard/src/data/manifest.ts`).

The consequence is the whole re-deploy story:

- A new export produces **new hashed filenames** for anything whose content
  changed, plus a **new manifest** naming exactly those files.
- A reader with a cached manifest fetches the previous export's files — which are
  still at their own immutable URLs, still self-consistent, still valid.
- A reader with the new manifest fetches the new export's files.
- **A deployment therefore cannot mix files from two exports.** There is no
  window in which a manifest points at a file that does not exist, and no way for
  a stale hashed file to be served under a name that now means something else.

The one thing that must never be cached is the manifest itself. If a reader (or
Cloudflare's edge) held a year-old manifest, every drill-down would request the
old export's filenames — which the new deployment does not contain — and 404. So:

- the client fetches it with `cache: "no-store"` (defeats the *browser* cache), and
- `_headers` sends `Cache-Control: no-store` for `/data/data-manifest.json`
  (defeats the *edge* cache).

Both are needed. Neither substitutes for the other.

---

## 2. Pre-deploy gate

Run all of it from `dashboard/`. Everything is env-free — no `.env`, no secrets,
no build-time configuration.

```bash
cd dashboard
npm ci                                 # first time / after a dependency change
npm run typecheck
npm run test -- --run
npm run build                          # writes dist/
node scripts/check-limits.mjs dist     # hosting-limit gate — MUST print PASS
npm run smoke                          # headless browser over the built site
```

`npm run smoke` rebuilds, boots `vite preview` against `dist/`, and drives every
route in both locales asserting zero console errors. It is the last thing that
runs before an upload.

Copy the markdown block `check-limits` prints between its
`--- paste into docs/baselines.md ---` markers into `docs/baselines.md`, with the
date and which export it measured. A limit measurement over a partial export is
not a national measurement and must be labelled as such.

---

## 3. Deploy

Two routes. Both upload the same `dist/`. Neither needs an environment variable.

### 3a. Wrangler CLI (preferred — scriptable, and gives a preview URL)

```bash
# One-time: authenticate the CLI against your Cloudflare account.
npx wrangler login

# One-time: create the Pages project. Choose "Direct Upload" (no Git integration
# — see the CI/CD note at the top of this file).
npx wrangler pages project create election-anomaly-observatory \
  --production-branch main

# Every deploy, from dashboard/:
npx wrangler pages deploy dist --project-name election-anomaly-observatory
```

`wrangler pages deploy` prints two URLs: a per-deployment **preview** URL
(`https://<commit-ish>.election-anomaly-observatory.pages.dev`) and, for a
production deploy, the **production** URL
(`https://election-anomaly-observatory.pages.dev`).

To deploy to a preview URL without touching production, pass a branch name that
is not the production branch:

```bash
npx wrangler pages deploy dist \
  --project-name election-anomaly-observatory \
  --branch preview
```

Do the post-deploy verification (§4) against the preview URL first. Promote by
re-running the deploy without `--branch`.

### 3b. Cloudflare dashboard (no CLI)

1. Cloudflare dashboard → **Workers & Pages** → **Create** → **Pages** →
   **Upload assets**.
2. Name the project `election-anomaly-observatory`.
3. Drag `dashboard/dist` (the directory, not a zip of its parent) into the
   uploader. Confirm the file count it reports matches what `check-limits`
   printed — a mismatch means the wrong directory was selected.
4. **Deploy site**.

There is no build step to configure, because the build happens locally. If you
are ever asked for a build command or an output directory, you are in the Git
integration flow — back out; that is the CI/CD path this project does not use.

---

## 4. Post-deploy verification

Against the **preview URL**, before promoting.

### 4.1 First, the `.json.gz` question — check this before anything else

The municipality shards are served from paths ending in `.json.gz` and hold raw
gzip bytes that the client inflates itself (`dashboard/src/data/shards.ts`). The
decoder sniffs the gzip magic number `1f 8b` and only inflates when the bytes
really are compressed, so it is correct under **both** of the servings a static
host can produce:

| What the host does | What `fetch(...).arrayBuffer()` yields | Client behaviour |
| --- | --- | --- |
| serves the file opaquely, no `Content-Encoding` | gzip bytes, starting `1f 8b` | inflates via `DecompressionStream("gzip")` |
| sets `Content-Encoding: gzip` from the `.gz` suffix | plaintext JSON (browser already inflated) | decodes as UTF-8, no inflate |

`vite preview` (sirv) does the second. Measured on 2026-08-05 against
`dist/data/shards/03018.f337e906.json.gz`:

```
$ curl -sI http://localhost:4199/data/shards/03018.f337e906.json.gz
HTTP/1.1 200 OK
Content-Length: 201577
Content-Type: application/json
Content-Encoding: gzip
Cache-Control: no-cache

$ curl -s --compressed  .../03018.f337e906.json.gz | head -c 4 | xxd
00000000: 7b22 7622                                {"v"          # plaintext
$ curl -s -H 'Accept-Encoding:' .../03018.f337e906.json.gz | head -c 4 | xxd
00000000: 1f8b 0800                                ....          # gzip
```

**What Cloudflare Pages does is NOT established from documentation** (see §5). It
is very likely to be one of the two rows above, both of which work. Check it
anyway, first, because a third behaviour would break every drill-down on the real
site and nothing else on the page would look wrong:

```bash
URL=https://<preview>.pages.dev
MANIFEST=$(curl -s "$URL/data/data-manifest.json")
SUMMARY=$(jq -r '.files["summary.json"].path' <<<"$MANIFEST")
SHARD=$(jq -r '.files | to_entries[] | select(.key | endswith(".json.gz")) | .value.path' <<<"$MANIFEST" | head -1)

# 1. The manifest must NOT be cached, at the browser or at the edge.
curl -sI "$URL/data/data-manifest.json" | grep -i -E 'cache-control|cf-cache-status'
#    expect EXACTLY: cache-control: no-store
#    If any `max-age` or `immutable` appears alongside it, some rule in _headers
#    is also matching this path and Cloudflare has comma-joined the values.
#    Fix the rule, do not rationalise the header. See the comment at the top of
#    dashboard/public/_headers.

# 2. A hashed export file must be immutable — and ONLY immutable.
curl -sI "$URL/data/$SUMMARY" | grep -i cache-control
#    expect: cache-control: public, max-age=31536000, immutable
#    If it reads `public, max-age=0, must-revalidate, public, max-age=31536000,
#    immutable`, then _headers APPENDS to the Pages default instead of replacing
#    it and the leading max-age=0 wins — see §5.2(b). The whole caching policy
#    is then a no-op and needs rewriting around `! Cache-Control`.

# 3. What does Pages say about a .json.gz path?
curl -sI "$URL/data/$SHARD" | grep -i -E 'content-type|content-encoding|cache-control'

# 4. And what bytes does a client actually receive? `--compressed` makes curl
#    behave like a browser: it advertises gzip and transparently decodes
#    whatever Content-Encoding comes back.
curl -s --compressed "$URL/data/$SHARD" | head -c 4 | xxd
#    `1f 8b ...`     -> opaque gzip body; the client's DecompressionStream path runs.
#    `7b 22 76 22`   -> (`{"v"`) Pages inflated it for the browser; the client's
#                       plaintext path runs. Both are fine.
#    anything else   -> STOP. Report it; see the deviation note in §5.
```

### 4.2 Then the site itself

Route this to `codex-computer-use` rather than doing it by hand:

- load the overview `/` — the choropleth renders, zero console errors;
- switch locale pt-BR ↔ en — copy changes, `<html lang>` changes;
- open a municipality drill-down — **the shard fetch succeeds and sections
  render**. This is the check that would fail if `.json.gz` serving were wrong;
- open `/methodology` and one detector page under `/detectors/`;
- screenshot each as evidence.

---

## 5. What the documentation establishes — and what it does not

Researched 2026-08-05 against Cloudflare's own docs. Recorded honestly rather
than guessed, because two of these decide whether the site works at all.

### 5.1 Established from primary documentation

| Fact | Source |
| --- | --- |
| Free plan: **20,000 files** per site; **25 MiB** max per asset | [pages/platform/limits](https://developers.cloudflare.com/pages/platform/limits/) |
| `_headers`: **all matching rules apply**, and a header set twice has its values **joined with a comma**. There is no first-match-wins and no specificity ordering. | [pages/configuration/headers](https://developers.cloudflare.com/pages/configuration/headers/) |
| `_headers` limits: 100 rules, 2,000 characters per line | same |
| A splat (`*`) greedily matches all characters; only one per URL. A placeholder (`:name`) in a **path** is delimited only by `/`, never by `.` | [workers/static-assets/headers](https://developers.cloudflare.com/workers/static-assets/headers/) |
| A header can be removed with a leading `! ` | same |
| Pages default headers include `Content-Type`, `ETag`, `X-Content-Type-Options: nosniff`; cacheable assets default to `Cache-Control: public, max-age=0, must-revalidate` | [pages/configuration/serving-pages](https://developers.cloudflare.com/pages/configuration/serving-pages/) |
| Pages "will also serve Gzip and Brotli responses whenever possible", adding `Content-Encoding` plus `Cache-Control: no-transform` when it encodes | same |
| **A new deployment does NOT purge the CDN cache.** Assets stay cached (TTL one week) until manually purged | same |

The comma-join rule is why `dashboard/public/_headers` has **no `/data/*`
wildcard** — read the comment at the top of that file before editing it. The
no-purge-on-deploy fact is why the manifest's `no-store` is load-bearing rather
than belt-and-braces.

### 5.2 NOT established — verify on the first deployment

**(a) What Pages does with a `.json.gz` path.** No Cloudflare documentation
states how `Content-Type` is inferred from a file extension, nor whether Pages
sets `Content-Encoding: gzip` for an asset whose name ends in `.gz`. Community
threads on pre-compressed assets in Pages exist but are inconclusive and are
secondary sources. **This is unverified.** It is the first thing §4.1 checks.

The client survives both plausible behaviours (see the table in §4.1). The
behaviour that would NOT survive is a `Content-Encoding: gzip` header on a body
that something in the chain has already inflated — the browser then fails the
transfer with a decoding error and `fetch` rejects. That is the same class of
bug reported for pre-gzipped objects in R2.

If it does break — i.e. the body a browser receives is neither valid gzip nor
valid JSON — the fix is **not** to change the decoder or the shard schema. It is
to change what the file is called:

- have the exporter write the same gzipped payload under a name that does not
  end in `.gz` (e.g. `shards/03018.<hash>.jsonz`), so nothing infers an encoding
  from the extension, and update `logical.shard()` / `logical.subshard()` in
  `dashboard/src/data/manifest.ts` to match;
- the decoder needs no change at all: it already sniffs the magic number.

A `_headers` entry is unlikely to be enough on its own. Cloudflare publishes no
list of headers `_headers` may not set, but it documents that it manages
`Content-Encoding` itself, and forcing `! Content-Encoding` onto a body that
really is encoded would break the working case. Do not try it before measuring.

**(b) Whether `_headers` REPLACES or APPENDS to the Pages defaults.** The
default for a cacheable asset is `Cache-Control: public, max-age=0,
must-revalidate`. Overriding it is the documented purpose of `_headers` and
every worked example treats it as a replacement, but Cloudflare does not say so
in those words, and the `! ` negation feature exists precisely because *some*
defaults survive. If it appended, `/data/shards/*` would go out as
`public, max-age=0, must-revalidate, public, max-age=31536000, immutable` and
the leading `max-age=0` would win — the caching policy would silently do
nothing. §4.1 step 2 measures it: the response must carry the immutable value
and only that.

---

## 6. Re-deploying after a new export

```bash
# 1. Regenerate the export (this rewrites dashboard/public/data/ wholesale).
make pipeline UF=<UF>        # or the national driver

# 2. Rebuild and re-gate.
cd dashboard
npm run build
node scripts/check-limits.mjs dist
npm run smoke

# 3. Upload.
npx wrangler pages deploy dist --project-name election-anomaly-observatory
```

Nothing needs purging and nothing needs versioning by hand. The new export
carries new hashed filenames and a new `no-store` manifest; the old hashed files
simply stop being referenced. Cloudflare keeps every previous deployment at its
own preview URL, so rolling back is re-promoting an older deployment in the Pages
dashboard, not re-running the pipeline.

Two things to keep an eye on as the export grows from one state to all 27+1:

- **File count.** One file per municipality, plus per-zone splits for the large
  ones. `check-limits` is the gate; ~5,570 municipalities leaves large headroom
  under 20,000, but zone splitting multiplies the count.
- **The geometry file.** `geo/br-municipalities.topo.json` is unhashed and
  therefore capped at a day of edge staleness by `_headers`. If it is ever
  regenerated, expect up to 24 h of readers on the old boundaries. It is
  election-independent, so this should essentially never happen.
- **New file names in the export.** `_headers` names each top-level file under
  `data/` explicitly (it cannot use a `/data/*` wildcard — §5.1). A new
  top-level export file gets no immutable header until a rule is added for it.
  That costs a revalidation round-trip, not correctness, but it is worth
  catching. New *subdirectories* need a rule too. This is the one way the
  exporter and this config are coupled:

  ```bash
  # every top-level file under data/ should have a rule in _headers
  ls dashboard/dist/data
  grep '^/data/' dashboard/public/_headers
  ```
