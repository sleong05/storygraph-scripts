# StoryGraph Data Pipeline

How raw news articles become the clustered story timelines shown on the StoryGraph
frontend.

Articles are pulled from the StoryGraph archive, embedded, clustered into stories,
uploaded to the Internet Archive, and pulled back down by the frontend, which filters,
orders, and draws them.

This is the **overview** — it covers how the pieces fit together and the conventions
that live *between* them. Each component has its own README with the full detail; see
[Repositories](#repositories).

---

## Contents

- [Architecture](#architecture)
- [Scope: which pipeline this documents](#scope-which-pipeline-this-documents)
- [Stage 1 — Embed](#stage-1--embed)
- [Stage 2 — Cluster](#stage-2--cluster)
- [Stage 3 — Upload to the Internet Archive](#stage-3--upload-to-the-internet-archive)
- [Stage 4 — Frontend](#stage-4--frontend)
- [Running on the HPC cluster (`2017_to_2026/`)](#running-on-the-hpc-cluster-2017_to_2026)
- [Local development](#local-development)
- [Gotchas](#gotchas)
- [Repositories](#repositories)

---

## Architecture

```
  sgtk embed  ──▶  sgtk cluster  ──▶  ia_uploader.py  ──▶  Internet Archive  ──▶  Frontend
      │                 │                    │                     │                  │
  embeddings      flat story files      publish + rename      public storage    filter ▶ order ▶ draw
  1 file/day      1 file/interval        stories-*.json.gz                          (React + D3)
```

One directional flow — nothing writes back upstream. The toolkit produces files, the
uploader publishes them, the frontend is a read-only consumer.

---

## Scope: which pipeline this documents

Two tools share the same React frontend, fed by **two independent data paths**:

| Tool | Shows | Fed by | Here? |
|------|-------|--------|-------|
| **Stories** (timeline) | How stories / topics / entities rise and fall over a day, week, month, or year | `sgtk embed` + `sgtk cluster` → Internet Archive | ✅ documented below |
| **StoryGraph** (similarity graph) | News-similarity network, recomputed every 10 min | A separate graph processor writing `graphs-*.jsonl.gz` + byte-offset indexes | ➖ context only |

This README covers the **Stories timeline** pipeline. The graph path is a parallel
system with its own processor and layout; it's mentioned only so newcomers reading
frontend code aren't surprised to find a second data source.

---

## Stage 1 — Embed

Pulls articles for a date range, generates sentence embeddings, topic labels, and NER
entities. Writes **one `.json.gz` per day**.

```bash
sgtk embed 2026-01-01 2026-01-31 -p ./data/articles
```

Key flags: `-p` output path, `-m` model (default `all-MiniLM-L6-v2`).
`cluster`, `probability`, and `representativeness` are `null` at this stage — Stage 2
fills them in.

→ Full output schema, topic list, and NER classes: **StoryGraphToolkit README**.

---

## Stage 2 — Cluster

Clusters the embedded articles with **HDBSCAN** into one flat `.json.gz` per interval
window. Every article is kept, including noise (`cluster_id: null`). This flat file is
exactly what the frontend consumes.

```bash
sgtk cluster 2026-01-01 2026-01-31 days -s ./data/articles -p ./data/timeline
```

`interval` is required and **plural**: `days | weeks | months | years`. Week dates
normalize to the Monday of that ISO week, months to the 1st, years to Jan 1.

**Output layout** (the shape Stage 3 expects):

```
data/timeline/
├── days/    2026-01-01.json.gz
├── weeks/   2026-01-05.json.gz   ← Monday of that week
├── months/  2026-01-01.json.gz
└── years/   2026-01-01.json.gz
```

Each file is `{ params, articles[] }`. The two fields that matter most downstream:
`cluster_id` (groups articles into a story; `null` = noise, dropped by the frontend)
and `representativeness` (highest-scoring article in a cluster becomes its label).

→ Full schema, field notes, and default HDBSCAN parameters per interval:
**StoryGraphToolkit README**.

---

## Stage 3 — Upload to the Internet Archive

`ia_uploader.py` publishes the Stage 2 files. This is the glue step — its conventions
aren't documented in either repo, so they're spelled out here.

```bash
python3 ia_uploader.py <interval>    # day | week | month | year  — SINGULAR
```

Uploads run in parallel (10 workers, 5 retries with 10 s backoff). Missing source
files are skipped, not fatal.

**Reads from `./tmp`** — `SRC_BASE` is hardcoded, so run `sgtk cluster -p ./tmp` (or
move/symlink the output there) or every file is skipped as missing.

**Writes to per-month items**, renaming files on the way up:

| Interval | Item | In-item path |
|----------|------|--------------|
| day | `storygraph-data-usa-YYYY-MM` | `DD/stories-day-YYYY-MM-DD.json.gz` |
| week | `storygraph-data-usa-YYYY-MM` | `DD/stories-week-YYYY-MM-wWW.json.gz` |
| month | `storygraph-data-usa-YYYY-MM` | `01/stories-month-YYYY-MM.json.gz` |
| year | `storygraph-data-usa-YYYY-01` | `01/stories-year-YYYY.json.gz` |

Collection `storygraph`, `mediatype: data`. A month's item holds its daily, weekly,
and monthly files side by side; year rollups live in that year's January item.

**Credentials** come from the `internetarchive` library's standard auth — run
`ia configure` once, or set `IA_ACCESS_KEY` / `IA_SECRET_KEY`.

> **The date range is hardcoded.** `__main__` calls
> `upload_date_range(interval, '2017-08-08', '2026-04-30')`. Edit those literals or
> call `upload_date_range()` directly for a different span. `upload_helper()` also
> takes an `ia_no_upload` flag for dry runs.

---

## Stage 4 — Frontend

React + TypeScript (Vite), visualizations in D3. It fetches one flat file, then does
**everything else in the browser** — changing view mode or bucket size recomputes from
state and makes no new request.

```
flat file ──▶ applyGroupingStrategy() ──▶ parseStoryItems() ──▶ D3 chart
              group by story/topic/       sort, filter, slice    line per group
              entity                                             circle per bucket
```

**Fetch** — `getFlatDataForDate()` in `src/requests.ts`, keyed by `{interval}/{date}`,
resolving to the Archive URLs from Stage 3:

```
https://archive.org/download/storygraph-data-usa-YYYY-MM/DD/stories-day-YYYY-MM-DD.json.gz
```

Both plain and gzipped JSON are accepted (decompression is tried first).

**Grouping** — three modes over the same data: **stories** (by `cluster_id`), **topics**
(by `topic` string), **entities** (explodes `entities[]`, so one article can feed
several groups).

**Ordering and visibility**, in order: sort by total article count → drop groups below
the diversity minimum (normalized Shannon entropy of the publisher mix, default 0.05)
→ grey out groups under the threshold → keep only the top 15 (50 for entities).

All interactive state is encoded in the URL, so any view is shareable.

```bash
npm install && npm run dev     # http://localhost:5173
docker-compose up --build      # production build, port 80
```

→ File map, URL parameters, bucketing math, and how to add a view mode:
**frontend README**.

---

## Running on the HPC cluster (`2017_to_2026/`)

Backfilling the full archive is far too slow to run interactively, so `2017_to_2026/`
holds a set of **Slurm batch scripts** that run the pipeline on the HPC cluster.

Submit with `sbatch`:

```bash
cd 2017_to_2026/
sbatch <script>.slurm
squeue -u $USER        # check status
```

These wrap the same `sgtk` commands documented above — the stage semantics, intervals,
and output layout are identical; only the execution environment differs. Because the
uploader reads from `./tmp`, make sure the cluster jobs write their cluster output
where the upload step will look for it (or stage it there afterwards).

> **Fill in per script:** what each script covers (stage and date span), the partition
> / time / memory it requests, and where its logs and output land. Also note any module
> loads or virtualenv activation needed for `sgtk` on the cluster.

---

## Local development

Run the whole thing without touching the Internet Archive:

1. Generate data — `sgtk embed …` then `sgtk cluster … -p ./data/timeline`.
2. Serve it with the local dev server on **port 5000**, which exposes
   `GET /api/timeline/{days|weeks|months|years}/{YYYY-MM-DD}` and returns the matching
   flat file. Months always use `dd=01`, years `mm=01&dd=01`.
3. Point the frontend at it and `npm run dev`.

> This server is a **development stand-in for the Internet Archive** — it is not part
> of the production path and shouldn't be treated as a runtime dependency. No
> implementation ships in the repos above; any static server mapping those four routes
> onto `./data/timeline/{interval}/{date}.json.gz` works.

---

## Gotchas

- **`ia_uploader.py` reads `./tmp`, not `./data/timeline`.** Hardcoded `SRC_BASE`.
  Mismatch = every file silently skipped as missing.
- **Singular vs. plural `interval`.** Uploader CLI and uploaded filenames are singular
  (`day`, `stories-day-…`); directories, `sgtk cluster`, and frontend routes are plural
  (`days/`). Most likely source of "file not found."
- **Files are renamed on upload.** `2026-01-01.json.gz` on disk becomes
  `stories-day-2026-01-01.json.gz` in the Archive. Fetchers must use the latter.
- **The uploader's date range is hardcoded**, with no CLI date argument.
- **`published` timestamp format is inconsistent in the docs** — ISO 8601 out of embed,
  RFC-822-style in the cluster example, ISO 8601 expected by the frontend. Worth
  confirming; a mismatch silently breaks bucketing.
- **Week dates normalize to Monday** everywhere — pass the Monday, not an arbitrary day.
- **Two data sources, one frontend.** Timeline and similarity graph are independent;
  changing one doesn't affect the other.

---

## Repositories

- **StoryGraphToolkit** (`sgtk` — embed + cluster) —
  <https://github.com/oduwsdl/storygraph-toolkit>
  Branch with the stories changes: <https://github.com/sleong05/storygraph-toolkit>
  *(still needs to be merged in)*
  ```bash
  git clone https://github.com/oduwsdl/storygraph-toolkit.git
  cd storygraph-toolkit/ && pip install . && cd .. && rm -rf storygraph-toolkit/
  ```
- **`ia_uploader.py`** — Stage 3, standalone script. Needs
  [`internetarchive`](https://archive.org/developers/internetarchive/)
  (`pip install internetarchive`, then `ia configure`).
- **`2017_to_2026/`** — Slurm batch scripts for running the pipeline on the HPC cluster.
- **StoryGraph frontend** (React/TS/Vite/D3) —
  `https://code.wm.edu/data-science/news-lab/storygraph`
- **Live site** — <https://newsresearch.lab.wm.edu/tools/storygraph/>
