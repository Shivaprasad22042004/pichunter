# Graduation Photo Finder

Find every photo you appear in, from an archive of ~10,000 graduation photos.

You upload one photo of your face. It returns the **file names** of the photos
you are in. That is all it does.

**Live site:** https://graduation-photo-finder.onrender.com

Built for DSI Graduation 2026. Not an official college service. No accounts, no
payment, no advertising.

---

## The problem it solves

The graduation archive was shared as a Google Drive link containing two zip
files: about **9,957 photos**, **2.5 GB**, from **8 camera folders** across two
archive copies. Folder names repeat between the two copies
(`SHANKAR`, `KRISHNA CAMERA`, `MURALI  CAMERA` … ), and files are named by
camera sequence (`DSC08379.JPG`, `SDS_6006.JPG`).

There is no index. Finding yourself means opening photos one at a time.

This project indexes every face once, then lets anyone search by face in about
fifteen seconds.

---

## What it actually does, precisely

```
selfie
  │
  ├─ 1. detect the face                     SCRFD (det_10g), 480×480 input
  ├─ 2. straighten it to 112×112            similarity transform on 5 landmarks
  ├─ 3. reduce it to 512 numbers            ArcFace (w600k_r50)
  └─ 4. compare with every indexed face     cosine similarity, one matmul
        └─ return file names above a threshold
```

| Quantity | Value |
|---|---|
| Photos in the archive | 9,957 |
| Photos scanned | 9,960 (includes a few duplicates) |
| Faces detected | 115,460 |
| Faces indexed (≥20 px wide) | 82,620 |
| Distinct photos with an indexed face | 9,496 |
| Face vector | 512 numbers |
| Index size on disk | 42 MB (int8) |
| Models, compressed | 48 MB (int8) |
| Resident memory | ~300 MB of 512 MB |
| Warm search | 9–22 s, usually ~15 s |
| Search on a normal laptop | under 1 s |

### Thresholds

| Score | Meaning | Shown as |
|---|---|---|
| ≥ 0.50 | almost certainly the same person | normal match |
| 0.42 – 0.50 | possibly the same person | "possible" |
| < 0.42 | not shown | — |

These were not chosen by theory. 0.42 was picked as the point below which
strangers appear; 0.50 as the point where a match has always been correct on
this archive.

---

## Honest limitations

Everything below is a real limitation, not a disclaimer.

- **Only works for people who were at DSI Graduation 2026.** Anyone else
  correctly receives "no photos found".
- **Returns file names, not images.** The photos are not hosted publicly. This
  was deliberate: they contain thousands of people who did not choose to be
  published.
- **Faces smaller than 20 px wide are discarded.** They are unreadable by humans
  too; keeping them added no matches, only false positives.
- **It misses some photos.** Turned-away faces, heavy shadow, sunglasses and
  very small faces in crowd shots will be missed. This is a limit of the
  technology.
- **It is slow on the free tier.** ~15 s warm. The server also sleeps after
  about 15 minutes idle, so the first visit can take up to a minute. See
  [KEEPALIVE.md](KEEPALIVE.md).
- **Not tested at scale.** It has been verified against three people on this one
  archive. That is not a benchmark.
- **The archive is assumed static.** Re-running the indexer is required if
  photos are added.
- **One host, one region, no redundancy.** If Render is down, the site is down.

---

## Privacy: exactly what is stored

**Uploaded photos are never written to disk.** They are decoded in memory,
reduced to a vector, compared, and discarded when the request ends.

The only thing recorded per search is a counter bump:

| Stored | Not stored |
|---|---|
| the **day** (`2026-11-10`) | the IP address, or any prefix of it |
| a **keyed hash** of the visitor's IP | the uploaded image, or the face vector |
| whether that visitor found matches | user agent, referrer, cookies |
| | timestamps or per-visit records |

The hash is `sha256(secret + day + ip)`, truncated. The secret changes daily,
so the same person on two days produces unrelated codes and cannot be linked
across days.

**One genuine limit:** this hash is *keyed*, not random. Anyone holding the
secret could test a guessed address against a stored code and confirm it. The
secret is a server-side environment variable and is not published. The claim
made here is therefore "the address is not stored and cannot be recovered by a
visitor", not "unrecoverable by anyone".

Aggregate counts are public at [`/stats`](https://graduation-photo-finder.onrender.com/stats).

---

## Deploying your own copy

```bash
git clone https://github.com/Shivaprasad22042004/pichunter
cd pichunter

# 1. index your photos (slow: roughly 1 hour per 5,000 photos)
#    see scripts/ for the indexing pipeline described below

# 2. build and run
docker build -t photo-finder .
docker run -p 8000:8000 photo-finder
```

Then open http://localhost:8000

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `UPSTASH_REDIS_REST_URL` | recommended | durable counters |
| `UPSTASH_REDIS_REST_TOKEN` | recommended | same |
| `STATS_SECRET` | recommended | salt for visitor hashes |
| `EXCLUDE_IPS` | optional | IPs never counted (your own testing) |
| `SITE_URL` | optional | used in `sitemap.xml` / `robots.txt` |
| `ORT_THREADS` | optional | default `1`; more threads are *slower* on 0.1 CPU |
| `DET_SIZE` | optional | detector input, default `480` |
| `PHOTOS_DIR` | optional | serve thumbnails if photos are reachable |

Without a database the counters fall back to a local file that resets on
restart, and `/stats` says so.

### Requirements

- ~500 MB RAM to run
- Python 3.12 for indexing
- Disk for the index: roughly 500 bytes per face

---

## Build notes: what was tried and rejected

These were measured, not assumed. Recorded so nobody repeats them.

| Approach | Result |
|---|---|
| PCA to 192 dims + int8 (16 MB) | **20% of real matches lost** |
| PCA to 256 dims (21 MB) | lost matches at every size |
| int8, one scale per vector | **catastrophic: 51,000 false matches** |
| int8, per-dimension scale, 512 dims | **exact** — this is what ships, 42 MB |
| OpenCV SFace (37 MB, Apache-2.0) | **failed**: found 2,479 faces vs 115,460, and ranked strangers above the real person |
| fp32 models | 553 MB resident — exceeds the free tier |
| int8 models | 48 MB, identical results (scores within 0.005) |
| ORT memory arena enabled | +104 MB, never released |
| ORT arena disabled | saves 130 MB, few ms slower |
| Detector at 640 px | correct but slower |
| Detector at 320 px | **lost 1–2 genuine matches** — 480 is the floor |
| 2 ORT threads on 0.1 CPU | **slower** than 1 thread (18.5 s vs 10.2 s) |

The shipped index was verified to reproduce full-precision ground truth
exactly: identical match sets for all three test people, maximum score
difference 0.002.

---

## Project layout

```
_web/
├── app.py                  FastAPI service: detect → embed → search
├── stats.py                privacy-preserving counters (Redis REST + file fallback)
├── Dockerfile              builds models at deploy time
├── scripts/fetch_models.py downloads + quantises models to int8
├── static/
│   ├── index.html          search page
│   ├── about / how-it-works / privacy / credits / bring-your-own / stats
│   ├── app.js, shell.js    page logic
│   └── win95.css           the look
├── data/
│   ├── index.bin           42 MB int8 face vectors, 82,620 × 512
│   ├── scale.bin           per-dimension dequantisation scales
│   └── faces.json          photo path + box per vector
└── KEEPALIVE.md            how to stop the free server sleeping
```

---

## Credits and licences

Full list with links on the [credits page](https://graduation-photo-finder.onrender.com/credits).

| Component | Licence |
|---|---|
| InsightFace code | MIT |
| **buffalo_l model weights** (SCRFD + ArcFace) | **non-commercial research only** |
| ONNX Runtime | MIT |
| OpenCV | Apache-2.0 |
| NumPy | BSD-3-Clause |
| FastAPI | MIT |
| Uvicorn | BSD-3-Clause |
| ONNX, ml_dtypes | Apache-2.0 |

**Important:** the face models are InsightFace's `buffalo_l` weights, which are
free for **non-commercial research use only**. A free, non-commercial tool for
classmates sits within those terms. Selling a service built on them does not.
Commercial licensing: `recognition-oss-pack@insightface.ai`.

`requirements.txt` pins ONNX Runtime exactly, because the int8 models are
produced by the same version at build time and quantised graphs are not
portable across ORT versions.

---

## Why the interface looks like this

It imitates Windows 95. That is a **trademark of Microsoft Corporation**; this
project is not affiliated with, endorsed by, or connected to Microsoft. No
Microsoft fonts or image files are used — the bevels and gradients are
recreated in CSS, text uses fonts already on the visitor's machine.

---

## Contributing

Issues and corrections are welcome, especially **factual corrections to the
claims in this README or on the site** — accuracy matters more here than
features, because people are trusting it with a photo of their face.
