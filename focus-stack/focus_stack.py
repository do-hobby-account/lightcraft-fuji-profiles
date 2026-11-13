"""Focus stacking for macro shots, using LightCraft to develop the raws.

Give it the frames of one stack (same subject, focus moved a little between shots). It
  1. develops each raw with lightcraft-cli (identical settings for every frame),
  2. aligns the frames (moving focus changes the magnification slightly),
  3. keeps the sharpest detail from each frame (Laplacian-pyramid fusion),
  4. writes <first frame>-Stack.tif (16-bit) next to the first frame.

usage:
  focus_stack.py FRAMES_OR_FOLDER... [-o OUT.tif] [--size N] [--settings FILE] [--preset ID]
                 [--no-align] [--no-crop] [--overwrite]

Frames are stacked in file-name order. TIFF/JPEG/PNG inputs are used as they are.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

import cv2
import numpy as np

RAW_EXT = {'.raf', '.dng', '.cr2', '.cr3', '.nef', '.arw', '.orf', '.rw2', '.pef'}
IMG_EXT = {'.tif', '.tiff', '.jpg', '.jpeg', '.png'}
DEFAULT_CLI = r'C:\Program Files\ArtCraft\LightCraft\lightcraft-cli.exe'
ALIGN_EDGE = 1600        # long edge of the pictures used to measure alignment


def find_cli():
    for c in (os.environ.get('LIGHTCRAFT_CLI'), shutil.which('lightcraft-cli'), DEFAULT_CLI):
        if c and os.path.isfile(c):
            return c
    sys.exit('lightcraft-cli not found; set LIGHTCRAFT_CLI to its path')


def natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', os.path.basename(s))]


def collect(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += [os.path.join(p, f) for f in os.listdir(p)]
        else:
            files.append(p)
    files = [f for f in files if os.path.splitext(f)[1].lower() in RAW_EXT | IMG_EXT
             and not os.path.splitext(f)[0].endswith('-Stack')]
    # a raw and its camera JPEG of the same name: keep the raw
    raws = {os.path.splitext(f)[0].lower() for f in files if os.path.splitext(f)[1].lower() in RAW_EXT}
    files = [f for f in files if os.path.splitext(f)[1].lower() in RAW_EXT
             or os.path.splitext(f)[0].lower() not in raws]
    return sorted(set(files), key=natural)


def develop(cli, src, dst, args):
    cmd = [cli, 'render', src, '-o', dst, '--opt', 'bitDepth=16']
    if args.size:
        cmd += ['--size', str(args.size)]
    if args.settings:
        cmd += ['--settings', args.settings]
    if args.preset:
        cmd += ['--preset', args.preset]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(dst):
        sys.exit('LightCraft could not develop %s:\n%s' % (src, (r.stderr or r.stdout).strip()))


def read_image(path, size=None):
    """BGR float32 in 0..1."""
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        sys.exit('cannot read ' + path)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    img = img[:, :, :3]
    scale = 65535.0 if img.dtype == np.uint16 else 255.0
    img = img.astype(np.float32) / scale
    if size and max(img.shape[:2]) > size:
        f = size / max(img.shape[:2])
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return img


def small_gray(img):
    f = min(1.0, ALIGN_EDGE / max(img.shape[:2]))
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if f < 1.0:
        g = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return g, f


def pair_transform(prev, cur):
    """3x3 matrix taking coordinates in `prev` to coordinates in `cur` (small-picture pixels), or None."""
    warp = np.eye(2, 3, dtype=np.float32)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6)
    try:
        # coarse first: translation at quarter size, then the full affine fit
        p4 = cv2.resize(prev, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
        c4 = cv2.resize(cur, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
        t = np.eye(2, 3, dtype=np.float32)
        try:
            _, t = cv2.findTransformECC(p4, c4, t, cv2.MOTION_TRANSLATION, crit, None, 5)
            warp[:, 2] = t[:, 2] * 4.0
        except cv2.error:
            pass
        cc, warp = cv2.findTransformECC(prev, cur, warp, cv2.MOTION_AFFINE, crit, None, 5)
    except cv2.error:
        return None
    lin = warp[:, :2]
    det = float(np.linalg.det(lin))
    if cc < 0.5 or not 0.8 < det < 1.25:      # not the same scene, or a wild fit
        return None
    return np.vstack([warp, [0, 0, 1]]).astype(np.float64)


def alignment(grays, f):
    """Per frame: 2x3 matrix taking reference (middle frame) coordinates to that frame, full-size pixels."""
    n = len(grays)
    steps = [None] * n                       # steps[i]: frame i-1 -> frame i
    for i in range(1, n):
        m = pair_transform(grays[i - 1], grays[i])
        if m is None:
            print('  warning: frame %d could not be aligned to frame %d, left as shot' % (i + 1, i))
            m = np.eye(3)
        steps[i] = m
    ref = n // 2
    mats = [None] * n
    mats[ref] = np.eye(3)
    for i in range(ref + 1, n):
        mats[i] = steps[i] @ mats[i - 1]
    for i in range(ref - 1, -1, -1):
        mats[i] = np.linalg.inv(steps[i + 1]) @ mats[i + 1]
    out = []
    for m in mats:
        a = m[:2].copy()
        a[:, 2] /= f                         # small-picture pixels -> full-size pixels
        out.append(a.astype(np.float32))
    return out, ref


def laplacian_pyramid(img, levels):
    pyr = []
    g = img
    for _ in range(levels):
        down = cv2.pyrDown(g)
        up = cv2.pyrUp(down, dstsize=(g.shape[1], g.shape[0]))
        pyr.append(g - up)
        g = down
    pyr.append(g)
    return pyr


class Fusion:
    """Running fusion: per pyramid level and pixel, keep the frame with the most local detail."""

    def __init__(self, shape):
        self.levels = max(1, min(8, int(np.log2(min(shape[:2]) / 32.0))))
        self.best = None
        self.energy = None
        self.base = None
        self.count = 0

    def add(self, img):
        pyr = laplacian_pyramid(img, self.levels)
        en = []
        for lap in pyr[:-1]:
            e = (lap * lap).sum(axis=2)
            en.append(cv2.GaussianBlur(e, (0, 0), 2.0))
        if self.best is None:
            self.best, self.energy, self.base = pyr[:-1], en, pyr[-1].copy()
        else:
            for k in range(self.levels):
                m = en[k] > self.energy[k]
                self.best[k][m] = pyr[k][m]
                self.energy[k][m] = en[k][m]
            self.base += pyr[-1]
        self.count += 1

    def result(self):
        g = self.base / self.count
        for lap in reversed(self.best):
            g = cv2.pyrUp(g, dstsize=(lap.shape[1], lap.shape[0])) + lap
        return np.clip(g, 0.0, 1.0)


def common_area(valid):
    """Box (y0, y1, x0, x1) of the area every frame covers."""
    v = valid > 0
    rows = np.where(v.mean(axis=1) > 0.98)[0]
    if len(rows) == 0:
        return 0, valid.shape[0], 0, valid.shape[1]
    y0, y1 = rows[0], rows[-1] + 1
    cols = np.where(v[y0:y1].mean(axis=0) > 0.999)[0]
    if len(cols) == 0:
        return 0, valid.shape[0], 0, valid.shape[1]
    x0, x1 = cols[0], cols[-1] + 1
    rows = np.where(v[:, x0:x1].mean(axis=1) > 0.999)[0]
    if len(rows):
        y0, y1 = rows[0], rows[-1] + 1
    return int(y0), int(y1), int(x0), int(x1)


def write_image(path, img):
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.tif', '.tiff', '.png'):
        data = np.round(img * 65535.0).astype(np.uint16)
    else:
        data = np.round(img * 255.0).astype(np.uint8)
    ok, buf = cv2.imencode(ext, data)
    if not ok:
        sys.exit('cannot write ' + path)
    buf.tofile(path)


def main():
    ap = argparse.ArgumentParser(description='Focus-stack macro frames with LightCraft.')
    ap.add_argument('inputs', nargs='+', help='the frames of one stack, or a folder holding only them')
    ap.add_argument('-o', '--output', help='output file (.tif, .png or .jpg); default <first frame>-Stack.tif')
    ap.add_argument('--size', type=int, help='long edge in pixels (default full size); small values make a quick test')
    ap.add_argument('--settings', help='LightCraft develop settings (.json, .lcpreset or XMP preset) for every frame')
    ap.add_argument('--preset', help='LightCraft preset id for every frame')
    ap.add_argument('--no-align', action='store_true', help='frames are already aligned')
    ap.add_argument('--no-crop', action='store_true', help='keep the edges not covered by every frame')
    ap.add_argument('--overwrite', action='store_true', help='replace an existing output file')
    args = ap.parse_args()

    files = collect(args.inputs)
    if len(files) < 2:
        sys.exit('need at least two frames; found %d' % len(files))
    first = os.path.splitext(files[0])[0]
    out = args.output or first + '-Stack.tif'
    if os.path.splitext(out)[1].lower() not in ('.tif', '.tiff', '.png', '.jpg', '.jpeg'):
        sys.exit('output must be .tif, .png or .jpg')
    if os.path.abspath(out).lower() in {os.path.abspath(f).lower() for f in files}:
        sys.exit('the output would replace one of the frames')
    if os.path.exists(out) and not args.overwrite:
        sys.exit('%s already exists (use --overwrite to replace it)' % out)

    print('Stacking %d frames: %s ... %s' % (len(files), os.path.basename(files[0]), os.path.basename(files[-1])))
    tmp = tempfile.mkdtemp(prefix='focus-stack-')
    try:
        cli = None
        ready = []
        for i, f in enumerate(files):
            if os.path.splitext(f)[1].lower() in RAW_EXT:
                cli = cli or find_cli()
                dst = os.path.join(tmp, '%04d.tif' % i)
                print('  developing %s' % os.path.basename(f), flush=True)
                develop(cli, f, dst, args)
                ready.append((dst, None))
            else:
                ready.append((f, args.size))

        shape = None
        mats = None
        if not args.no_align:
            print('  aligning', flush=True)
            grays = []
            for p, size in ready:
                img = read_image(p, size)
                if shape is None:
                    shape = img.shape
                elif img.shape != shape:
                    sys.exit('frames differ in size: %s' % os.path.basename(p))
                g, f = small_gray(img)
                grays.append(g)
            mats, ref = alignment(grays, f)
            del grays

        fusion = None
        valid = None
        for i, (p, size) in enumerate(ready):
            print('  merging %d/%d' % (i + 1, len(ready)), flush=True)
            img = read_image(p, size)
            if shape is None:
                shape = img.shape
            elif img.shape != shape:
                sys.exit('frames differ in size: %s' % os.path.basename(p))
            h, w = shape[:2]
            if mats is not None:
                flags = cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP
                img = cv2.warpAffine(img, mats[i], (w, h), flags=flags, borderMode=cv2.BORDER_REFLECT)
                m = cv2.warpAffine(np.full((h, w), 255, np.uint8), mats[i], (w, h),
                                   flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP, borderValue=0)
                valid = m if valid is None else cv2.min(valid, m)
            if fusion is None:
                fusion = Fusion(shape)
            fusion.add(img)
            del img

        res = fusion.result()
        if valid is not None and not args.no_crop:
            y0, y1, x0, x1 = common_area(valid)
            res = res[y0:y1, x0:x1]
        write_image(out, res)
        print('Wrote %s (%d x %d)' % (out, res.shape[1], res.shape[0]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
