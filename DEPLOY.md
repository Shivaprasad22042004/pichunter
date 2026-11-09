# Deploy guide

Everything is built and tested locally. Here's the remaining work.

---

## What you need to do (3 things)

1. **Create a free Render account** → https://render.com
   - Sign up with GitHub, no credit card needed for the free tier.
2. **Create a GitHub repo** (or let me create it once you give me a username).
3. **Send me:** your GitHub username, and tell me which Render workspace/region
   to target.

That's it. I'll push and deploy.

---

## Memory budget (measured on this machine)

| Change | Resident RAM |
|---|---|
| fp32 models + ORT arena enabled | **553 MB** ❌ over limit |
| int8 models, arena still enabled | 403 MB |
| compact metadata + arena disabled | **274 MB** ✅ |

The final build leaves ~238 MB headroom under Render's 512 MB.

Two changes did almost all the work:

1. **int8 models** — 191 MB of weights → 48 MB, identical search results
   (scores within 0.005 of fp32).
2. **`enable_cpu_mem_arena = False`** — ONNX Runtime's arena grew to serve the
   largest activation and never released it (+104 MB per process, measured).
   Bounded allocation costs a few ms and saves 130 MB.

---

## Where the large files come from

| File | Size | In git? | How it's delivered |
|---|---|---|---|
| `data/index.bin` | 41 MB | ✅ yes | committed (under GitHub's 100 MB limit) |
| `data/scale.bin` | 2 KB | ✅ yes | committed |
| `data/faces.json` | 8 MB | ✅ yes | committed |
| `models/*.onnx` | 191 MB | ❌ no | **downloaded at build time** |

`Dockerfile` runs `scripts/fetch_models.py`, which downloads the 191 MB of
weights, quantises them to 48 MB int8, and deletes the originals. This avoids
needing Git LFS entirely.

---

## Deploy steps

```bash
cd "_web"
git init
git add -A
git commit -m "Graduation photo finder"
git remote add origin https://github.com/<YOU>/graduation-photo-finder.git
git push -u origin main
```

Then: **Render dashboard → New → Blueprint → select repo → Apply**

`render.yaml` configures everything (Docker build, health check, free plan).

---

## Expected behaviour

| | |
|---|---|
| Cold start | ~30–60 s (models load on first request) |
| Warm search | ~350–450 ms |
| First search after idle | slower — the free tier sleeps after 15 min |

---

## After deploying — checklist

- [ ] `GET /api/health` returns `{"ok": true, "faces": 82620}`
- [ ] Upload a known photo → correct photos return
- [ ] Set `PHOTOS_DIR` to serve previews (needs the photo archive on the host —
      not possible on free tier; see below)
- [ ] Check the Render logs for OOM warnings

### About photo previews

The free tier has **no persistent disk**, so the 2.4 GB photo archive can't
live there. Two options:

1. **Filenames only** (current default) — people get the exact filenames and
   look them up in the shared archive. Works today, costs nothing.
2. **Previews via object storage** — upload 420px thumbnails of all 9,960
   photos to Cloudflare R2 (free 10 GB, and thumbs would be ~300 MB).
   Then `PHOTOS_DIR` becomes an R2-backed route.

Option 2 is the nicer experience. It's a small addition once the service is up.

---

## Privacy behaviour (accurate)

- Selfie is decoded in memory, converted to a vector, image bytes dropped.
- Nothing written to disk.
- Access logging disabled.
- The response contains filenames only (plus thumbnails if enabled).
