"""
Fetch the InsightFace models at container build time and quantise to int8.

Source weights are 184 MB (too large for GitHub). The int8 versions are
48 MB total and produce identical search results (verified: 13/9/4 matches,
scores within 0.005 of fp32).

Weights: InsightFace buffalo_l (ArcFace w600k_r50, SCRFD det_10g).
Licence: non-commercial research use only.
"""
import os, sys, urllib.request

BASE = os.environ.get(
    'MODEL_BASE', 'https://huggingface.co/immich-app/buffalo_l/resolve/main')
DET = 'models/det_10g.onnx'
REC = 'models/w600k_r50.onnx'

SOURCES = {
    DET: f'{BASE}/detection/model.onnx',
    REC: f'{BASE}/recognition/model.onnx',
}


def download(url, dst):
    print(f'  downloading {url}', flush=True)
    req = urllib.request.Request(url, headers={'User-Agent': 'graduation-photo-finder/1.0'})
    with urllib.request.urlopen(req, timeout=180) as r, open(dst, 'wb') as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    print(f'  -> {dst} ({os.path.getsize(dst) / 1e6:.1f} MB)', flush=True)


def main():
    os.makedirs('models', exist_ok=True)
    for dst, url in SOURCES.items():
        if os.path.exists(dst) and os.path.getsize(dst) > 1_000_000:
            print(f'  present: {dst}', flush=True)
            continue
        download(url, dst)

    from onnxruntime.quantization import quantize_dynamic, QuantType
    for src, dst in ((DET, 'models/det_10g_int8.onnx'), (REC, 'models/w600k_r50_int8.onnx')):
        if os.path.exists(dst):
            print(f'  present: {dst}', flush=True)
            continue
        print(f'  quantising {src} -> {dst}', flush=True)
        quantize_dynamic(src, dst, weight_type=QuantType.QInt8)
        print(f'  -> {os.path.getsize(dst) / 1e6:.1f} MB', flush=True)

    # drop the large fp32 copies, the service only loads the int8 ones
    for f in (DET, REC):
        if os.path.exists(f):
            os.remove(f)

    total = sum(os.path.getsize(os.path.join('models', f)) for f in os.listdir('models'))
    print(f'  models ready: {total / 1e6:.1f} MB', flush=True)


if __name__ == '__main__':
    main()
