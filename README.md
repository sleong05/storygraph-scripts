# StoryGraph Data Pipeline

End-to-end documentation for how raw news articles become the clustered, filtered
story timelines shown in the StoryGraph frontend.

This pipeline takes raw news article data from the StoryGraph archive and produces
structured, clustered JSON files ready for the frontend to consume. It runs in two
processing stages — **embed** (fetch articles and generate vector embeddings) and
**cluster** (group articles into stories and produce the final flat files) — after
which the output is **uploaded to the Internet Archive** and **pulled back down by
the frontend**, which filters, orders, and displays it.

If you are new to the project, read this file top to bottom. It links out to the two
component repositories for anything deeper.

---

## Contents

- [Architecture at a glance](#architecture-at-a-glance)
- [Scope: which pipeline this documents](#scope-which-pipeline-this-documents)
- [Stage 1 — Embed](#stage-1--embed)
- [Stage 2 — Cluster](#stage-2--cluster)
- [Stage 3 — Upload to the Internet Archive](#stage-3--upload-to-the-internet-archive)
- [Stage 4 — Frontend: fetch, filter, display](#stage-4--frontend-fetch-filter-display)
- [Local development](#local-development)
- [Reference tables](#reference-tables)
- [Gotchas](#gotchas)
- [Repositories](#repositories)

---

## Architecture at a glance

```
   ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
   │  sgtk embed  │ ──▶ │ sgtk cluster │ ──▶ │ upload script│ ──▶ │   Internet   │
   │              │     │              │     │              │     │    Archive    │
   │ fetch + emb- │     │  HDBSCAN +   │     │  push flat   │     │  (public data │
   │  ed articles │     │  flat files  │     │    files     │     │    store)     │
   └──────────────┘     └──────────────┘     └──────────────┘     └───────┬──────┘
        │                     │                                           │
   .json.gz per day    data/timeline/{interval}/{date}.json.gz           │
                                                                          ▼
                                                                  ┌──────────────┐
                                                                  │   Frontend   │
                                                                  │  (React/D3)  │
                                                                  │              │
                                                                  │ fetch ▶ filter│
                                                                  │  ▶ order ▶ draw│
                                                                  └──────────────┘
```

The whole thing is one directional flow. Nothing writes back upstream: the toolkit
produces files, the upload step publishes them, and the frontend is a read-only
consumer.

---

## Scope: which pipeline this documents

The StoryGraph project actually ships **two** tools on the same React frontend, fed
by **two independent data paths**:

| Tool | What it shows | Data source | Covered here? |
|------|---------------|-------------|---------------|
| **Stories** (timeline) | How stories / topics / entities rise and fall over a day, week, month, or year | Flat JSON files from the `sgtk embed` + `sgtk cluster` pipeline, published to the Internet Archive | ✅ **Yes — this is the pipeline documented below** |
| **StoryGraph** (similarity graph) | A news-similarity network recomputed every 10 minutes | A **separate** graph processor that writes `graphs-*.jsonl.gz` + byte-offset index files to the Internet Archive | ➖ Mentioned for context only |

**This README documents the Stories timeline pipeline** — the `embed → cluster →
upload → Internet Archive → frontend` flow. The similarity-graph path is a parallel
system with its own processor and its own Internet Archive layout; it is noted here
only so that newcomers reading the frontend code aren't surprised to find a second
data source. If you're working on the graph view, that path is out of scope for this
document.

---

## Stage 1 — Embed

Fetches articles from StoryGraph for a date range, generates sentence embeddings and
topic classifications, and saves **one `.json.gz` file per day**.

```bash
# Single day
sgtk embed 2026-01-01 -p ./data/articles

# Date range
sgtk embed 2026-01-01 2026-01-31 -p ./data/articles

# Custom embedding model
sgtk embed 2026-01-01 2026-01-31 -p ./data/articles -m all-mpnet-base-v2
```

**Options**

| Option | Default | Meaning |
|--------|---------|---------|
| `start` | *(required)* | Start date, `YYYY-MM-DD` |
| `end` | = `start` | End date, `YYYY-MM-DD` |
| `-p, --path` | `.` | Where to write the embedded article files |
| `-m, --model` | `all-MiniLM-L6-v2` | Sentence-Transformer model |

**Output** — one array of article objects per day:

```json
[
  {
    "link": "https://example.com/article",
    "title": "Article headline",
    "text": "Full article text...",
    "published": "2026-01-01T12:00:00Z",
    "favicon": "https://example.com/favicon.ico",
    "image": "https://example.com/image.jpg",
    "embedding": [0.123, -0.456, "..."],
    "embedding_model": "all-MiniLM-L6-v2",
    "publisher": "nytimes",
    "leaning": "left",
    "topic": "Politics",
    "entities": [
      { "entity": "Joe Biden", "class": "PERSON" },
      { "entity": "United States", "class": "GPE" }
    ],
    "cluster": null,
    "probability": null,
    "representativeness": null
  }
]
```

`cluster`, `probability`, and `representativeness` are `null` at this stage — they are
filled in by the next stage.

---

## Stage 2 — Cluster

Takes the embedded articles and clusters them with **HDBSCAN**. Produces **one flat
`.json.gz` per interval window** containing *all* articles (including noise) with
their cluster assignments and metadata. This flat file is exactly the shape the
frontend consumes.

```bash
# Single day
sgtk cluster 2026-01-15 days -s ./data/articles -p ./data/timeline

# Date range
sgtk cluster 2026-01-01 2026-01-31 days -s ./data/articles -p ./data/timeline

# Week (date is automatically normalized to the Monday of that week)
sgtk cluster 2026-01-15 weeks -s ./data/articles -p ./data/timeline

# Month (normalized to the first of the month)
sgtk cluster 2026-01-01 months -s ./data/articles -p ./data/timeline

# Override HDBSCAN tuning
sgtk cluster 2026-01-15 days -s ./data/articles -p ./data/timeline \
  --min-cluster-size 4 --min-samples 4
```

**Options**

| Option | Default | Meaning |
|--------|---------|---------|
| `start` | *(required)* | Start date, `YYYY-MM-DD` |
| `end` | = `start` | End date, `YYYY-MM-DD` |
| `interval` | *(required)* | `days` \| `weeks` \| `months` \| `years` |
| `-s, --source` | `.` | Folder of embedded articles (Stage 1 output) |
| `-p, --path` | `.` | Where to write the flat timeline files |
| `--min-cluster-size` | *(see below)* | HDBSCAN tuning |
| `--min-samples` | *(see below)* | HDBSCAN tuning |

**Default HDBSCAN parameters per interval**

| Interval | `min_cluster_size` | `min_samples` |
|----------|--------------------|---------------|
| days | 2 | 2 |
| weeks | 2 | 2 |
| months | 4 | 4 |
| years | 5 | 5 |

**Output file layout**

```
data/timeline/
├── days/
│   ├── 2026-01-01.json.gz
│   ├── 2026-01-02.json.gz
│   └── ...
├── weeks/
│   └── 2026-01-05.json.gz   ← named by the Monday of that week
├── months/
│   └── 2026-01-01.json.gz
└── years/
    └── 2026-01-01.json.gz
```

**Output format** — a single object with a `params` block and a flat `articles`
array. Every article is present; noise articles carry `cluster_id: null`.

```json
{
  "params": {
    "total_left": 50,
    "total_center": 40,
    "total_right": 25,
    "noise_articles": 18,
    "total_articles_processed": 133,
    "date": "2026-01-01",
    "time_interval": "days"
  },
  "articles": [
    {
      "cluster_id": 3,
      "topic": "Crime & Public Safety",
      "representativeness": 0.95,
      "entities": [
        { "entity": "Donald Trump", "class": "PERSON" },
        { "entity": "New Orleans", "class": "GPE" }
      ],
      "title": "Article headline",
      "published": "Wed, 01 Jan 2026 18:40:36 +0000",
      "favicon": "https://example.com/favicon.ico",
      "link": "https://example.com/article",
      "image": "https://example.com/image.jpg",
      "leaning": "left",
      "publisher": "nytimes"
    }
  ]
}
```

**Field notes**

- `cluster_id` — HDBSCAN cluster integer. `null` = noise (didn't fit any cluster) and
  is excluded from Stories mode in the frontend.
- `representativeness` — cosine similarity to the cluster medoid, `[0, 1]`. The
  highest-scoring article in a cluster is used as that cluster's headline label.
  `null` for noise.
- `topic` — always present. One of the 15 classifier labels (see reference below).
- `entities` — filtered NER tags. One article can land in multiple entity groups.
  Empty list if none.
- `publisher` — domain root without TLD (e.g. `nytimes`, not `nytimes.com`).
- `params.total_*` counts include noise.

**Python API** (equivalent to the CLI, if you'd rather script it):

```python
from storygraph_tk import generate_embedded_articles, generate_cluster_data_range

generate_embedded_articles("2026-01-01", "2026-01-31", output_dir="./data/articles")
generate_cluster_data_range(
    "2026-01-01", "2026-01-31",
    interval="days",
    output_folder="./data/timeline",
    source="./data/articles",
)
```

---

## Stage 3 — Upload to the Internet Archive

The clustered flat files from Stage 2 are the frontend's production data source. For
the **deployed** site to see them, they are published to the Internet Archive by
`ia_uploader.py`; the frontend then fetches them directly from there (no application
server sits in the production path — see [Local development](#local-development) for
the dev-only server).

### Running the upload

```bash
python3 ia_uploader.py <interval>     # interval: day | week | month | year  (SINGULAR)
```

The uploader walks a date range, and for each interval-aligned date it uploads the
matching flat file to the Internet Archive in parallel (a `ThreadPoolExecutor` with
10 workers; each upload retries up to 5 times with a 10 s backoff). Missing source
files are skipped with a warning rather than failing the run.

> **The date range is hardcoded, not a CLI argument.** In `__main__` the script calls
> `upload_date_range(interval, '2017-08-08', '2026-04-30')`, so an out-of-the-box run
> re-processes that entire span for the chosen interval. To upload a different range,
> edit those literals or call `upload_date_range(interval, start, end)` yourself. The
> start date is auto-snapped to the interval boundary (Monday for weeks, the 1st for
> months, Jan 1 for years).

### Where it reads from

`SRC_BASE` is hardcoded to **`./tmp`**, and the uploader expects the same
per-interval directory layout that `sgtk cluster` produces. **Point `sgtk cluster` at
`./tmp`** (`-p ./tmp`) — or move/symlink its output there — so the files line up:

| Interval | Source file the uploader reads |
|----------|--------------------------------|
| day | `./tmp/days/YYYY-MM-DD.json.gz` |
| week | `./tmp/weeks/YYYY-MM-DD.json.gz` (Monday of the ISO week) |
| month | `./tmp/months/YYYY-MM-01.json.gz` |
| year | `./tmp/years/YYYY-01-01.json.gz` |

Note the split naming convention: the **CLI argument and the directory names** differ
(singular `day` on the command line vs. plural `days/` on disk). This matches
`sgtk cluster`'s plural output folders.

### Where it writes to (Internet Archive layout)

Every file lands in a **per-month item** named `storygraph-data-usa-YYYY-MM`
(collection `storygraph`, `mediatype: data`). **Files are renamed on upload** — the
in-item object key is *not* the bare date used on disk, but a `stories-{interval}-…`
name under a day-of-month folder:

| Interval | Internet Archive item | In-item path (object key) |
|----------|-----------------------|---------------------------|
| day | `storygraph-data-usa-YYYY-MM` | `DD/stories-day-YYYY-MM-DD.json.gz` |
| week | `storygraph-data-usa-YYYY-MM` | `DD/stories-week-YYYY-MM-wWW.json.gz` (`DD` = Monday, `WW` = ISO week) |
| month | `storygraph-data-usa-YYYY-MM` | `01/stories-month-YYYY-MM.json.gz` |
| year | `storygraph-data-usa-YYYY-01` | `01/stories-year-YYYY.json.gz` |

So a given month's item can hold that month's daily files, its weekly files, and the
monthly rollup side by side. Year rollups live in that year's **January** item
(`…-YYYY-01`). This is the same item convention the separate similarity-graph pipeline
uses (`storygraph-data-usa-YYYY-MM/DD/graphs-*.jsonl.gz`), so both tools' data coexist
under one family of items.

### Credentials

The script uses the [`internetarchive`](https://archive.org/developers/internetarchive/)
Python library's `upload()` and does **not** set keys itself, so it relies on that
library's standard authentication — run `ia configure` once (writes
`~/.config/internetarchive/ia.ini`) or set the `IA_ACCESS_KEY` / `IA_SECRET_KEY`
environment variables before running. Uploads write to the `storygraph` collection and
are attributed to the `uploader` in the script's metadata block; publishing to that
collection requires an account with permission to do so.

### Dry run

`upload_helper()` accepts an `ia_no_upload` flag that prints intended uploads instead
of performing them. The interval branches currently pass `ia_no_upload=False`; flip it
(or thread it through from the CLI) if you want to preview what a run would push
without touching the Archive.

---

## Stage 4 — Frontend: fetch, filter, display

The frontend is a **React + TypeScript** app built with **Vite**, with visualizations
in **D3**. Once it has a flat file, *all* remaining work happens in the browser —
switching view modes or bucket sizes recomputes derived data from state and makes **no
new network request**.

### Fetch

Timeline data is loaded by `getFlatDataForDate()` in `src/requests.ts`, keyed by
`{interval}/{date}`. In production this resolves to the Internet Archive copy of the
flat file published in Stage 3; in local development it points at the dev server
(below). The production file for a given interval/date lives at the Internet Archive
download URL that mirrors the uploader's layout:

```
https://archive.org/download/storygraph-data-usa-YYYY-MM/DD/stories-day-YYYY-MM-DD.json.gz
https://archive.org/download/storygraph-data-usa-YYYY-MM/DD/stories-week-YYYY-MM-wWW.json.gz
https://archive.org/download/storygraph-data-usa-YYYY-MM/01/stories-month-YYYY-MM.json.gz
https://archive.org/download/storygraph-data-usa-YYYY-01/01/stories-year-YYYY.json.gz
```

> **One thing to verify in `src/requests.ts`:** the fetch target as documented in the
> frontend still shows the local dev address (`http://127.0.0.1:5000/api/timeline/...`).
> Confirm the production build points at the Internet Archive URLs above (typically via
> a base-URL env var / build config) and that it requests the **renamed**
> `stories-{interval}-…` object keys — not the bare `YYYY-MM-DD.json.gz` names used on
> disk before upload.

The frontend accepts **both** plain JSON and gzip-compressed JSON — it attempts
decompression first and falls back to raw text. Serving gzipped files is recommended,
especially for year-level windows.

### Filter and order

Once the flat `FlatFileData` arrives, the browser turns it into chart lines:

```
FlatFileData (raw articles)
    │  applyGroupingStrategy(data, mode, bucketHours)
    ▼
StoryItem[]        one item per story / topic / entity
    │  parseStoryItems(items, view, diversity, maxStories)
    ▼
TimelineData[]     one point per time bucket per group
    │
    ▼
D3 chart           one line per group, one circle per bucket
```

**Grouping modes** (same raw data, three strategies):

- **Stories** — group by `cluster_id`; the highest-`representativeness` article in each
  cluster becomes the label. Noise (`cluster_id: null`) is dropped.
- **Topics** — group by the `topic` string; label is the topic name.
- **Entities** — explode each article's `entities[]` so one article can feed several
  groups; label is the entity name. Top 50 entities by article count are kept.

**Ordering and visibility**, applied in this order:

1. **Sort** every group by total article count, descending (most-covered first — this
   also drives legend order and color assignment).
2. **Diversity filter** — hide any group whose publisher-diversity score is below the
   slider minimum (default `0.05`). Diversity is the normalized Shannon entropy of the
   group's publisher distribution: `0` = single outlet, `1` = every article from a
   different outlet.
3. **Threshold** — a default threshold (the peak count of the *Nth* story for the
   interval) greys out and pushes below the line any group that never clears it;
   overridable via a stepper.
4. **Slice** — only the top **15** stories (top **50** for entities) are drawn at all.

**Time bucketing** — every article is snapped to a bucket boundary before counting.
Day/sub-week buckets snap on an epoch grid; weekly and multi-week buckets snap to the
Monday of the article's ISO week. Empty buckets are zero-padded so a line that goes
quiet drops to zero rather than skipping points. Bucket size is user-selectable and
stored in the URL.

**Shareable state** — all interactive state (interval, date, view mode, threshold,
diversity, bucket index, visible leanings, highlighted lines, colors, legend
visibility) is encoded in the URL, so any view can be bookmarked or shared. See the
frontend repo for the full parameter table.

### Run the frontend

```bash
npm install
npm run dev        # dev server at http://localhost:5173
```

Production build (Docker + nginx, serves on port 80):

```bash
docker-compose up --build
```

The deployed site is hosted through W&M IT and released via a GitHub Action that fires
when `main` updates.

---

## Local development

Running the *whole* pipeline on your machine, without touching the Internet Archive:

1. **Generate data locally**

   ```bash
   sgtk embed   2026-01-01 2026-01-07 -p ./data/articles
   sgtk cluster 2026-01-01 2026-01-07 days -s ./data/articles -p ./data/timeline
   ```

2. **Serve the flat files with the local dev server.** In production the frontend
   reads flat files from the Internet Archive. For local development there is instead
   a small HTTP server (the "port-5000 server") that serves the contents of
   `./data/timeline/` to the frontend. **This server is a development convenience /
   stand-in for the Internet Archive — it is not part of the production data path.**

   It exposes four endpoints, one per interval, each returning the matching flat file
   (plain or gzipped JSON):

   | Endpoint | Date format | Covers |
   |----------|-------------|--------|
   | `GET /api/timeline/days/YYYY-MM-DD` | exact day | 24 hours |
   | `GET /api/timeline/weeks/YYYY-MM-DD` | Monday of the ISO week | 7 days |
   | `GET /api/timeline/months/YYYY-MM-01` | first of month | full month |
   | `GET /api/timeline/years/YYYY-01-01` | first of year | full year |

   For months the frontend always sends `dd=01`; for years `mm=01&dd=01`.

   > **Note:** an implementation of this dev server is not included in the two core
   > repositories described here. If your team maintains one, link it and its run
   > command in this section. If not, any static file server that maps the four routes
   > above onto `./data/timeline/{interval}/{date}.json.gz` will work.

3. **Point the frontend at the dev server** (the default `127.0.0.1:5000` target in
   `src/requests.ts`) and run `npm run dev`.

---

## Reference tables

### Topic categories (15)

Assigned by the `sleong105/news-classifier` model; every article gets exactly one.

| Topics | | |
|--------|--------|--------|
| Crime & Public Safety | Health | Education |
| Transportation | Environment & Weather | Economy & Business |
| Politics | Government & Public Services | Housing |
| Social Welfare | Arts & Entertainment | Sports |
| Science & Technology | Lifestyle | Other |

### NER entity classes (kept)

All other classes (`DATE`, `TIME`, `CARDINAL`, `ORDINAL`, `MONEY`, `PERCENT`,
`QUANTITY`, `WORK_OF_ART`, `LAW`, `LANGUAGE`, `TOP_10_TERM`, `TITLE`) are filtered out.

| Class | Description | Examples |
|-------|-------------|----------|
| `PERSON` | Named people | "Donald Trump", "Elon Musk" |
| `GPE` | Countries, cities, states | "United States", "California" |
| `ORG` | Companies, agencies, institutions | "FBI", "Congress" |
| `NORP` | Nationalities, political/religious groups | "Republicans", "Americans" |
| `LOC` | Non-GPE geographic locations | "Chapel Hill", "Atlantic Ocean" |
| `EVENT` | Named events | "Super Bowl", "New Year's Day" |
| `FAC` | Facilities, buildings | "The Pentagon", "LAX" |
| `PRODUCT` | Objects, vehicles, software | "Substack", "Tesla" |

### Political leaning

Every article's `leaning` is one of `left`, `center`, `right`.

### Frontend defaults

| Setting | Default |
|---------|---------|
| Max stories drawn | 15 |
| Max entities drawn | 50 |
| Diversity minimum | 0.05 (shown as 5%) |
| Default bucket — Day / Week / Month / Year | 2 h / 1 day / 1 day / 2 weeks |
| Threshold top-N — Day / Week / Month·Year | 3 / 4 / 5 |

---

## Gotchas

- **`published` timestamp format is inconsistent across the docs.** Stage 1 (embed)
  emits ISO 8601 (`2026-01-01T12:00:00Z`); the Stage 2 (cluster) example shows an
  RFC-822-style string (`Wed, 01 Jan 2026 18:40:36 +0000`); the frontend expects ISO
  8601. Confirm what the cluster step actually writes and what the frontend parser
  accepts, and normalize if needed — a mismatch here silently breaks bucketing.
- **Two data sources, one frontend.** The Stories timeline (this pipeline) and the
  similarity graph are independent. Don't assume a change to one affects the other.
- **The port-5000 server is dev-only.** Production reads flat files from the Internet
  Archive; the local server only mimics that for development. Don't ship it as a
  runtime dependency.
- **Week dates normalize to Monday.** `sgtk cluster ... weeks` and the frontend both
  key weekly windows by the Monday of the ISO week; pass/expect that date, not an
  arbitrary day in the week.
- **`ia_uploader.py` reads from `./tmp`, not `./data/timeline`.** Its `SRC_BASE` is
  hardcoded to `./tmp`, so run `sgtk cluster` with `-p ./tmp` (or move the output)
  before uploading, or every file is silently skipped as "missing."
- **The uploader's date range is hardcoded.** It always runs `2017-08-08 → 2026-04-30`
  for the given interval unless you edit the literals in `__main__`. There is no CLI
  date argument.
- **Singular vs. plural `interval` everywhere.** The uploader CLI and the uploaded
  filenames use the singular (`day`, `stories-day-…`), while the on-disk directories,
  `sgtk cluster`, and the frontend API routes use the plural (`days/`,
  `/api/timeline/days/…`). Mixing them up is the most likely source of "file not found."
- **Files are renamed on upload.** On disk a file is `2026-01-01.json.gz`; in the
  Internet Archive it is `stories-day-2026-01-01.json.gz`. Anything fetching from the
  Archive must use the `stories-{interval}-…` name.

---

## Repositories

- **StoryGraphToolkit** (`sgtk` — embed + cluster) —
  <https://github.com/oduwsdl/storygraph-toolkit>
  ```bash
  git clone https://github.com/oduwsdl/storygraph-toolkit.git
  cd storygraph-toolkit/ && pip install . && cd .. && rm -rf storygraph-toolkit/
  ```
- **`ia_uploader.py`** (Stage 3 — publishes flat files to the Internet Archive) —
  standalone script; depends on the [`internetarchive`](https://archive.org/developers/internetarchive/)
  Python library (`pip install internetarchive`, then `ia configure`).
- **StoryGraph frontend** (React/TS/Vite/D3) —
  `https://code.wm.edu/data-science/news-lab/storygraph`
- **Live site** — <https://newsresearch.lab.wm.edu/tools/storygraph/>
