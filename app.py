"""
Graduation Photo Finder - matching service.

POST /api/search   multipart/form-data:
                     selfie   - image file (required)
                     thresh   - float, default 0.42 (optional)
                     topK     - int, default 200 (optional)
                   -> {matches:[{photo, score, box}], ms, faces_detected}

GET  /api/health   -> {ok, faces, dims, model}

The uploaded image is decoded in memory, reduced to a vector, and discarded.
Nothing is written to disk, no logging of image content.
"""
import io, os, sys, json, time, math
from pathlib import Path

import numpy as np
import cv2
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import onnxruntime as ort

HERE = Path(__file__).resolve().parent
DATA = HERE / 'data'                      # index.bin, scale.bin, faces.json
MODELS = HERE / 'models'                  # det_10g.onnx, w600k_r50.onnx
PHOTOS_DIR = os.environ.get('PHOTOS_DIR')  # optional: serve actual photos

MAX_UPLOAD = 12 * 1024 * 1024
MIN_FACE_PX = 40          # reject faces too small to be reliable
DET_MAX = 1600
# Detector input. The model accepts any square size. 480 balances speed and
# recall: at 320 a selfie lost 1-2 genuine matches; at 480 recall matched or
# beat 640 while running ~25% faster on a 0.1-CPU host.
DET_SIZE = int(os.environ.get('DET_SIZE', '480'))

app = FastAPI(title='Graduation Photo Finder')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])

# ---------------------------------------------------------------- engine

class Engine:
    def __init__(self):
        so = ort.SessionOptions()
        # Single-threaded beats multi-threaded on a CPU-starved host: measured
        # on a 0.1-CPU container, detection took 10.2 s at 1 thread vs 18.5 s
        # at 2 and 21.9 s at 4. Threads only help when cores are actually free.
        so.intra_op_num_threads = int(os.environ.get('ORT_THREADS', '1'))
        so.inter_op_num_threads = 1
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # The CPU memory arena grows to serve the largest activation and never
        # returns it (measured: +104 MB per process). We serve short requests on
        # a small host, so bounded allocations are worth the tiny extra work.
        so.enable_cpu_mem_arena = False
        prov = ['CPUExecutionProvider']
        # Load sessions lazily-ish: the detector only runs on request, the
        # recogniser likewise. Keeping both resident is ~200 MB of weights, so
        # we load the detector on first use and keep it thereafter.
        self._so = so
        self._prov = prov
        self._det_sess = None
        self._rec_sess = None
        self.det_in = 'input.1'
        self.rec_in = 'input.1'

        self.scale = np.fromfile(DATA / 'scale.bin', np.float32)
        raw_meta = json.loads((DATA / 'faces.json').read_text())
        # Store compactly: a flat tuple of strings + a parallel array of boxes.
        # Parsing 8 MB of JSON into 82k dicts costs ~60 MB of Python objects;
        # this keeps it near the raw string size.
        self.paths = [m['p'] for m in raw_meta]
        self.boxes = np.array([m['b'] for m in raw_meta], np.int16)
        del raw_meta
        self.n_faces = len(self.paths)
        self.q = np.fromfile(DATA / 'index.bin', np.int8).reshape(self.n_faces, -1)
        # Dequantise in place, in chunks, to keep peak memory near the final size.
        # Final matrix is float16: 82,620 x 512 x 2 B = 84.6 MB.
        n, dims = self.q.shape
        self.emb = np.empty((n, dims), np.float16)
        CHUNK = 4096
        for i in range(0, n, CHUNK):
            blk = self.q[i:i + CHUNK].astype(np.float32)
            blk /= 127.0
            blk *= self.scale
            self.emb[i:i + CHUNK] = blk
            del blk
        del self.q

    # --- lazy model accessors ---
    @property
    def det_sess(self):
        if self._det_sess is None:
            self._det_sess = ort.InferenceSession(str(MODELS / 'det_10g_int8.onnx'), self._so, providers=self._prov)
            self.det_in = self._det_sess.get_inputs()[0].name
        return self._det_sess

    @property
    def rec_sess(self):
        if self._rec_sess is None:
            self._rec_sess = ort.InferenceSession(str(MODELS / 'w600k_r50_int8.onnx'), self._so, providers=self._prov)
            self.rec_in = self._rec_sess.get_inputs()[0].name
        return self._rec_sess

    # --- SCRFD detection (same geometry as the build pipeline) ---
    def detect(self, img):
        h, w = img.shape[:2]
        s = min(1.0, DET_MAX / max(h, w))
        small = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else img
        ih, iw = small.shape[:2]
        R = DET_SIZE
        det_scale = min(R / iw, R / ih)
        nw, nh = int(round(iw * det_scale)), int(round(ih * det_scale))
        canvas = np.zeros((R, R, 3), np.uint8)
        canvas[:nh, :nw] = cv2.resize(small, (nw, nh))
        blob = cv2.dnn.blobFromImage(canvas, 1 / 128.0, (R, R), (127.5,) * 3, swapRB=True)
        outs = self.det_sess.run(None, {self.det_in: blob})
        scores, bboxes, kpss = outs[0:3], outs[3:6], outs[6:9]
        dets, kps = [], []
        for idx, stride in enumerate((8, 16, 32)):
            sc = scores[idx].reshape(-1)
            bb = bboxes[idx].reshape(-1, 4) * stride
            kp = kpss[idx].reshape(-1, 10) * stride
            hs, ws = R // stride, R // stride
            ys, xs = np.mgrid[0:hs, 0:ws]
            ac = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float32) * stride
            ac = np.repeat(ac, 2, axis=0)
            if len(ac) != len(bb):
                # never silently drop detections on a geometry mismatch
                raise RuntimeError(f'anchor mismatch stride {stride}: {len(ac)} vs {len(bb)}')
            pos = np.where(sc >= 0.45)[0]
            if not len(pos):
                continue
            a = ac[pos]; b = bb[pos]; k = kp[pos]
            dets.append(np.stack([a[:, 0] - b[:, 0], a[:, 1] - b[:, 1],
                                  a[:, 0] + b[:, 2], a[:, 1] + b[:, 3],
                                  sc[pos]], 1))
            kps.append(np.stack([a[:, None, 0] + k[:, 0::2], a[:, None, 1] + k[:, 1::2]], -1))
        if not dets:
            return np.zeros((0, 5), np.float32), np.zeros((0, 5, 2), np.float32)
        dets = np.vstack(dets); kps = np.vstack(kps)
        keep = _nms(dets, 0.4)
        dets, kps = dets[keep], kps[keep]
        dets[:, :4] /= det_scale
        kps /= det_scale
        return dets, kps

    # --- ArcFace embedding ---
    def embed(self, img, kps):
        ratio = 112 / 112
        src = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                        [41.5493, 92.3655], [70.7299, 92.2041]], np.float32) * ratio
        M = _umeyama(kps.astype(np.float64), src.astype(np.float64))
        aligned = cv2.warpAffine(img, M[:2], (112, 112), borderValue=0)
        blob = cv2.dnn.blobFromImage(aligned, 1 / 127.5, (112, 112), (127.5,) * 3, swapRB=True)
        v = self.rec_sess.run(None, {self.rec_in: blob})[0].flatten().astype(np.float32)
        return v / max(float(np.linalg.norm(v)), 1e-9)

    def search(self, v, thresh, topk):
        # One contiguous matmul beats chunking by ~6x here (measure on a
        # 0.1-CPU host: 1.1 s vs 7 s). The float16 -> float32 conversion costs
        # ~0.4 s and avoids holding a second 169 MB copy of the matrix.
        s = self.emb.astype(np.float32) @ v
        idx = np.where(s >= thresh)[0]
        idx = idx[np.argsort(-s[idx])]
        seen, out = set(), []
        for i in idx:
            p = self.paths[int(i)]
            if p in seen:
                continue
            seen.add(p)
            out.append({'photo': p, 'score': round(float(s[i]), 3),
                        'box': [int(v) for v in self.boxes[int(i)]]})
            if len(out) >= topk:
                break
        return out


def _nms(d, t):
    if not len(d):
        return []
    x1, y1, x2, y2 = d[:, 0], d[:, 1], d[:, 2], d[:, 3]
    ar = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = d[:, 4].argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]; keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]]); yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]]); yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0, xx2 - xx1 + 1); h = np.maximum(0, yy2 - yy1 + 1)
        ov = (w * h) / (ar[i] + ar[order[1:]] - w * h)
        order = order[1:][ov <= t]
    return keep


def _umeyama(src, dst):
    n, dim = src.shape
    sm, dm = src.mean(0), dst.mean(0)
    sd, dd = src - sm, dst - dm
    A = dd.T @ sd / n
    d = np.ones(dim)
    if np.linalg.det(A) < 0:
        d[-1] = -1
    T = np.eye(dim + 1)
    U, S, V = np.linalg.svd(A)
    T[:dim, :dim] = U @ np.diag(d) @ V
    scale = 1.0 / sd.var(axis=0).sum() * (S @ d)
    T[:dim, dim] = dm - scale * (T[:dim, :dim] @ sm.T)
    T[:dim, :dim] *= scale
    return T


engine = None

@app.on_event('startup')
def _boot():
    global engine
    engine = Engine()
    print(f'[boot] {engine.n_faces:,} faces, dims={engine.emb.shape[1]}', flush=True)


@app.get('/api/health')
def health():
    return {'ok': True, 'faces': engine.n_faces, 'dims': int(engine.emb.shape[1]),
            'model': 'arcface w600k_r50'}


@app.post('/api/search')
async def search(selfie: UploadFile = File(...), thresh: float = Form(0.42), topK: int = Form(200)):
    t0 = time.time()
    raw = await selfie.read()
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, 'image too large (max 12 MB)')
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    del raw
    if img is None:
        raise HTTPException(400, 'could not decode image')

    dets, kps = engine.detect(img)
    if not len(dets):
        raise HTTPException(422, 'no face found - try a clearer, front-facing photo')

    areas = (dets[:, 2] - dets[:, 0]) * (dets[:, 3] - dets[:, 1])
    i = int(np.argmax(areas))
    if (dets[i, 2] - dets[i, 0]) < MIN_FACE_PX:
        raise HTTPException(422, 'face too small - please use a closer photo')

    v = engine.embed(img, kps[i])
    matches = engine.search(v, thresh, min(topK, 500))
    return {'matches': matches, 'faces_detected': int(len(dets)),
            'ms': int((time.time() - t0) * 1000)}


@app.get('/photo/{name:path}')
def photo(name: str):
    """Optional: serve an original photo if PHOTOS_DIR is configured."""
    if not PHOTOS_DIR:
        raise HTTPException(404, 'photo serving disabled')
    base = Path(PHOTOS_DIR).resolve()
    p = (base / name).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get('/thumb/{name:path}')
def thumb(name: str, w: int = 420):
    """Small JPEG preview so results can be seen, not just listed."""
    if not PHOTOS_DIR:
        raise HTTPException(404, 'photo serving disabled')
    base = Path(PHOTOS_DIR).resolve()
    p = (base / name).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(404)
    w = max(120, min(int(w), 900))
    raw = np.frombuffer(p.read_bytes(), np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(415)
    h, iw = img.shape[:2]
    if iw > w:
        img = cv2.resize(img, (w, int(h * w / iw)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return Response(content=buf.tobytes(), media_type='image/jpeg',
                    headers={'cache-control': 'public, max-age=86400'})


@app.get('/')
def root():
    return FileResponse(HERE / 'static' / 'index.html')


@app.get('/{path:path}')
def static(path: str):
    f = HERE / 'static' / path
    if f.is_file() and (HERE / 'static').resolve() in f.resolve().parents:
        return FileResponse(f)
    raise HTTPException(404)