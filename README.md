# Graduation Photo Finder

Find every photo you appear in from a 10,000-photo graduation archive.

Upload a selfie → get back the filenames of the photos you're in.

Built for **DSI Graduation 2026**. Tested on 9,960 photos / 82,620 indexed faces.

---

## How it works

```
 selfie ──► POST /api/search ──► 1. detect face (SCRFD)
                                 2. align + embed (ArcFace, 512-d)
                                 3. cosine search over 82,620 vectors
             ◄───────────────    4. filenames + scores
```

Three parts:

| Part | What | Size |
|---|---|---|
| Index | int8 face vectors + photo paths | 49 MB |
| Models | SCRFD detector + ArcFace recogniser (int8) | 48 MB |
| Service | FastAPI, ~370 MB RAM | — |

---

## Results (measured, not estimated)

Run against the real archive:

| Person | Photos found |
|---|---|
| Shiva | 13 |
| Shek | 9 |
| Sam | 4 |

Scores: identical photos score ~0.98–1.00. Strangers sit around 0.25–0.35.

- **≥ 0.50** shown as *strong*
- **0.42 – 0.50** shown as *possible* — worth checking by eye

---

## Decisions that were tested, not guessed

Several "obvious" optimisations were tried and **rejected** because they broke
accuracy. Recording them so they don't get re-attempted:

| Approach | Result |
|---|---|
| PCA → 192 dims + int8 (16 MB) | **80% recall** — lost real matches |
| PCA → 256 dims | lost matches at every size |
| int8 with one scale per vector | **catastrophic** — 51,000 false matches |
| int8 per-dimension, full 512-d | ✅ **exact**, 42 MB |
| OpenCV SFace (37 MB, Apache 2.0) | ❌ found 2,479 faces vs 115,460, ranked strangers above the real person |
| int8 quantised ONNX models | ✅ identical search results (scores within 0.005) |
| ORT memory arena enabled | ❌ +104 MB never released |
| ORT memory arena disabled | ✅ saves 130 MB, few ms slower |
| 82k JSON dicts in memory | ❌ ~60 MB of Python objects; replaced with flat lists |

**Shipped: 512-d int8 per-dimension quantisation.** Verified to reproduce
ground-truth matches exactly (max score delta 0.002).

---

## Repo layout

```
_web/
├── app.py                     FastAPI service
├── Dockerfile                 builds models at deploy time
├── scripts/fetch_models.py    download + quantise to int8
├── requirements.txt
├── render.yaml                Render blueprint
├── static/index.html          web UI
├── data/
│   ├── index.bin              41 MB  int8 vectors, 82,620 × 512
│   ├── scale.bin              per-dimension scales
│   └── faces.json             photo path + box per vector
└── models/                    generated at build (not committed)
```

---

## Running locally

```bash
pip install -r requirements.txt
cp models/*.onnx models/            # or run scripts/fetch_models.py
PHOTOS_DIR="C:/path/to/photos" python -m uvicorn app:app --port 8000
```

Open http://localhost:8000

`PHOTOS_DIR` is optional — without it results are filenames only (no
thumbnails). Set it to serve 420px previews.

---

## API

**`GET /api/health`**
```json
{"ok": true, "faces": 82620, "dims": 512, "model": "arcface w600k_r50"}
```

**`POST /api/search`** — `multipart/form-data`

| field | type | default |
|---|---|---|
| `selfie` | file | required |
| `thresh` | float | 0.42 |
| `topK` | int | 200 |

```json
{
  "matches": [{"photo": "…/SDS_6006.JPG", "score": 0.978, "box": [x1,y1,x2,y2]}],
  "faces_detected": 1,
  "ms": 403
}
```

---

## Privacy

- The selfie is decoded **in memory**, reduced to a vector, then discarded.
- Nothing is written to disk. Access logs are disabled (`--no-access-log`).
- Only a unit vector is ever compared — no image storage.
- The index holds face vectors of people who appear in photos that the
  graduation organiser already published.

### Usage counting

A tiny counter records **one line per search**: a timestamp, a daily-salted
hash of the caller's IP, and whether any matches were found.

- **No IP is stored** — only a SHA-256 hash of `secret + day + ip`.
- **The salt rotates daily**, so the same person on two days produces
  unrelated hashes and cannot be linked across days.
- **No cookies, no localStorage, no user agent, no referrer.**
- **No match counts** by default (set `STATS_STORE_MATCH_COUNT=1` to include
  them for debugging).

The numbers are **public** at `/stats` (endpoint `GET /api/stats`). They are
aggregate counts only and reveal nothing about individuals.

Set **`EXCLUDE_IPS`** to a comma-separated list of IPs that are never counted,
so your own testing does not inflate the numbers.

**Limitation on the free tier:** the file lives on ephemeral disk, so counters
reset on redeploy or restart. `stats.export()` returns recoverable rows, and
`STATS_SEED` (a JSON array) restores them on boot if you want to keep
long-term totals.

## Pages

| Route | Contents |
|---|---|
| `/` | search |
| `/about` | what this is, in plain English |
| `/how-it-works` | the pipeline, step by step, with thresholds |
| `/privacy` | exactly what happens to an uploaded photo |
| `/credits` | every open-source dependency, with links and licences |
| `/bring-your-own` | self-host guide + free indexing request |
| `/stats` | public usage counts |
| `/robots.txt`, `/sitemap.xml` | search-engine discovery; API paths disallowed |

Set `GOOGLE_SITE_VERIFICATION` to the code Search Console gives you and
`/google<code>.html` is served for verification. Set `SITE_URL` if the public
URL changes.


---

## Licence

**Code:** MIT.

**Model weights:** InsightFace `buffalo_l` (SCRFD + ArcFace w600k_r50) are
released by InsightFace **for non-commercial research use only**. Using this
for a free, non-commercial tool to share graduation photos is within those
terms; commercial use would require a licence from
`recognition-oss-pack@insightface.ai`.

If that's ever a problem, swap in a commercially licensed embedder — only
`scripts/fetch_models.py` and the model filename in `app.py` need to change.
