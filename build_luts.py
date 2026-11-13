"""Re-base Fujifilm X-Trans III film-simulation HaldCLUTs onto PROVIA and write .cube profiles.

Each source table maps a neutral raw render x -> simulation S(x) (display sRGB, 8 bit, Hald level 12).
LightCraft's raw rendering already matches the camera's PROVIA JPEG, so the profile we need is
F = S o P^-1 (PROVIA-rendered colour -> simulation colour).  F is estimated by local linear regression:
every table entry is a pair (P(x), S(x)); for each node of the output grid we fit an affine function to
the pairs within one cell and take its value at the node.  Nodes PROVIA never reaches are filled by
extending the colour shift (F - identity) smoothly from the nodes it does reach.

usage: build_luts.py <hald dir> <out dir> [stride]
"""
import os, sys, pickle, time
from multiprocessing import Pool
from PIL import Image

M = 33            # output grid
C = M - 1
H = 144           # hald level 12 cube edge
PREFIX = 'Fuji XTrans III - '
# (source name, Lightroom-style profile name, file stem, kind)
SIMS = [
    ('Velvia', 'Camera Velvia/VIVID', '02 Camera Velvia-VIVID', 'color'),
    ('Astia', 'Camera ASTIA/SOFT', '03 Camera ASTIA-SOFT', 'color'),
    ('Classic Chrome', 'Camera CLASSIC CHROME', '04 Camera CLASSIC CHROME', 'color'),
    ('Pro Neg Hi', 'Camera PRO Neg Hi', '05 Camera PRO Neg Hi', 'color'),
    ('Pro Neg Std', 'Camera PRO Neg Std', '06 Camera PRO Neg Std', 'color'),
    ('Acros', 'Camera ACROS', '07 Camera ACROS', 'mono'),
    ('Acros+Ye', 'Camera ACROS+Ye FILTER', '08 Camera ACROS+Ye FILTER', 'mono'),
    ('Acros+R', 'Camera ACROS+R FILTER', '09 Camera ACROS+R FILTER', 'mono'),
    ('Acros+G', 'Camera ACROS+G FILTER', '10 Camera ACROS+G FILTER', 'mono'),
    ('Mono', 'Camera MONOCHROME', '11 Camera MONOCHROME', 'mono'),
    ('Mono+Ye', 'Camera MONOCHROME+Ye FILTER', '12 Camera MONOCHROME+Ye FILTER', 'mono'),
    ('Mono+R', 'Camera MONOCHROME+R FILTER', '13 Camera MONOCHROME+R FILTER', 'mono'),
    ('Mono+G', 'Camera MONOCHROME+G FILTER', '14 Camera MONOCHROME+G FILTER', 'mono'),
    ('Sepia', 'Camera SEPIA', '15 Camera SEPIA', 'mono'),
]


def load(hald_dir, name):
    im = Image.open(os.path.join(hald_dir, PREFIX + name + '.png')).convert('RGB')
    assert im.size == (1728, 1728), im.size
    return im.tobytes()


def accumulate(args):
    """Sums for the regression. name None: the design sums S (10 per node), else the target sums T (12)."""
    hald_dir, name, stride = args
    pb = load(hald_dir, 'Provia')
    xb = load(hald_dir, name) if name else None
    k = C / 255.0
    width = 12 if name else 10
    acc = [0.0] * (M * M * M * width)
    for b in range(0, H, stride):
        for g in range(0, H, stride):
            o = 3 * (H * (g + H * b))
            for r in range(0, H, stride):
                q = o + 3 * r
                pr = pb[q] * k; pg = pb[q + 1] * k; pbl = pb[q + 2] * k
                ix = int(pr); iy = int(pg); iz = int(pbl)
                if ix > C - 1: ix = C - 1
                if iy > C - 1: iy = C - 1
                if iz > C - 1: iz = C - 1
                fx = pr - ix; fy = pg - iy; fz = pbl - iz
                base = ix + M * (iy + M * iz)
                if name:
                    xr = xb[q] / 255.0; xg = xb[q + 1] / 255.0; xbl = xb[q + 2] / 255.0
                for cz in (0, 1):
                    wz = fz if cz else 1.0 - fz
                    if wz <= 0.0: continue
                    dz = fz - cz
                    for cy in (0, 1):
                        wy = wz * (fy if cy else 1.0 - fy)
                        if wy <= 0.0: continue
                        dy = fy - cy
                        for cx in (0, 1):
                            w = wy * (fx if cx else 1.0 - fx)
                            if w <= 0.0: continue
                            dx = fx - cx
                            n = (base + cx + M * cy + M * M * cz) * width
                            wx = w * dx; wyy = w * dy; wzz = w * dz
                            if name:
                                acc[n] += w * xr; acc[n + 1] += w * xg; acc[n + 2] += w * xbl
                                acc[n + 3] += wx * xr; acc[n + 4] += wx * xg; acc[n + 5] += wx * xbl
                                acc[n + 6] += wyy * xr; acc[n + 7] += wyy * xg; acc[n + 8] += wyy * xbl
                                acc[n + 9] += wzz * xr; acc[n + 10] += wzz * xg; acc[n + 11] += wzz * xbl
                            else:
                                acc[n] += w; acc[n + 1] += wx; acc[n + 2] += wyy; acc[n + 3] += wzz
                                acc[n + 4] += wx * dx; acc[n + 5] += wx * dy; acc[n + 6] += wx * dz
                                acc[n + 7] += wyy * dy; acc[n + 8] += wyy * dz; acc[n + 9] += wzz * dz
    return name, acc


def solve4(A, B):
    """Solve the 4x4 system A X = B (B: 4 rows of 3) by Gaussian elimination; None when singular."""
    A = [row[:] + B[i][:] for i, row in enumerate(A)]
    for c in range(4):
        p = max(range(c, 4), key=lambda i: abs(A[i][c]))
        if abs(A[p][c]) < 1e-12:
            return None
        A[c], A[p] = A[p], A[c]
        inv = 1.0 / A[c][c]
        for i in range(c + 1, 4):
            f = A[i][c] * inv
            if f:
                for j in range(c, 7):
                    A[i][j] -= f * A[c][j]
    X = [[0.0] * 3 for _ in range(4)]
    for i in range(3, -1, -1):
        for ch in range(3):
            v = A[i][4 + ch] - sum(A[i][j] * X[j][ch] for j in range(i + 1, 4))
            X[i][ch] = v / A[i][i]
    return X


def fit(S, T, min_weight):
    """Node values (list of [r,g,b] or None)."""
    out = [None] * (M * M * M)
    for n in range(M * M * M):
        s = S[n * 10:n * 10 + 10]
        w = s[0]
        if w < min_weight:
            continue
        t = T[n * 12:n * 12 + 12]
        lam = 0.02 * w      # ridge on the slopes: toward the plain weighted mean where data is one-sided
        A = [[s[0], s[1], s[2], s[3]],
             [s[1], s[4] + lam, s[5], s[6]],
             [s[2], s[5], s[7] + lam, s[8]],
             [s[3], s[6], s[8], s[9] + lam]]
        B = [t[0:3], t[3:6], t[6:9], t[9:12]]
        X = solve4(A, B)
        mean = [t[0] / w, t[1] / w, t[2] / w]
        v = X[0] if X else mean
        # an extrapolated intercept may not stray far from the local mean
        out[n] = [min(max(v[c], mean[c] - 0.08), mean[c] + 0.08) for c in range(3)]
    return out


def neighbours(n):
    x = n % M; y = (n // M) % M; z = n // (M * M)
    if x > 0: yield n - 1
    if x < C: yield n + 1
    if y > 0: yield n - M
    if y < C: yield n + M
    if z > 0: yield n - M * M
    if z < C: yield n + M * M


def fill(vals, kind):
    """Extend the colour shift into the nodes without data; returns (values, number filled)."""
    ident = lambda n: (n % M / C, (n // M) % M / C, n // (M * M) / C)
    delta = [None if v is None else [v[c] - ident(n)[c] for c in range(3)] for n, v in enumerate(vals)]
    missing = [n for n, v in enumerate(vals) if v is None]
    todo = set(missing)
    while todo:
        new = {}
        for n in todo:
            nb = [delta[m] for m in neighbours(n) if delta[m] is not None]
            if nb:
                new[n] = [sum(d[c] for d in nb) / len(nb) for c in range(3)]
        assert new, 'no data at all'
        for n, d in new.items():
            delta[n] = d
        todo -= set(new)
    for _ in range(40):     # relax the filled region (harmonic extension)
        upd = {}
        for n in missing:
            nb = [delta[m] for m in neighbours(n)]
            upd[n] = [sum(d[c] for d in nb) / len(nb) for c in range(3)]
        for n, d in upd.items():
            delta[n] = d
    out = [[ident(n)[c] + delta[n][c] for c in range(3)] for n in range(M * M * M)]
    if kind == 'mono':
        # a black-and-white look stays a function of brightness in the filled region:
        # tint curve (mean -> rgb) learned from the measured nodes
        bins = 64
        acc = [[0.0, 0.0, 0.0, 0.0] for _ in range(bins + 1)]
        miss = set(missing)
        for n, v in enumerate(out):
            if n in miss:
                continue
            m = min(max(sum(v) / 3.0, 0.0), 1.0) * bins
            i = min(int(m), bins - 1); f = m - i
            for j, wt in ((i, 1 - f), (i + 1, f)):
                a = acc[j]
                a[0] += wt; a[1] += wt * (v[0] - sum(v) / 3.0); a[2] += wt * (v[1] - sum(v) / 3.0); a[3] += wt * (v[2] - sum(v) / 3.0)
        tint = [[a[1] / a[0], a[2] / a[0], a[3] / a[0]] if a[0] > 0 else [0.0, 0.0, 0.0] for a in acc]
        for n in missing:
            v = out[n]
            mean = min(max(sum(v) / 3.0, 0.0), 1.0)
            m = mean * bins
            i = min(int(m), bins - 1); f = m - i
            out[n] = [mean + tint[i][c] * (1 - f) + tint[i + 1][c] * f for c in range(3)]
    return [[min(max(c, 0.0), 1.0) for c in v] for v in out], len(missing)


def trilinear(vals, c):
    idx = []; fr = []
    for k in range(3):
        x = min(max(c[k], 0.0), 1.0) * C
        i = min(int(x), C - 1)
        idx.append(i); fr.append(x - i)
    out = [0.0, 0.0, 0.0]
    for cz in (0, 1):
        for cy in (0, 1):
            for cx in (0, 1):
                w = (fr[0] if cx else 1 - fr[0]) * (fr[1] if cy else 1 - fr[1]) * (fr[2] if cz else 1 - fr[2])
                v = vals[idx[0] + cx + M * (idx[1] + cy + M * (idx[2] + cz))]
                for k in range(3):
                    out[k] += w * v[k]
    return out


def check(vals, pb, xb):
    """|F(P(x)) - S(x)| over a sample of table entries, in 8-bit levels: (mean, 99th percentile, max)."""
    errs = []
    for b in range(3, H, 7):
        for g in range(2, H, 7):
            for r in range(1, H, 7):
                q = 3 * (r + H * (g + H * b))
                f = trilinear(vals, [pb[q] / 255.0, pb[q + 1] / 255.0, pb[q + 2] / 255.0])
                errs.append(max(abs(f[k] * 255.0 - xb[q + k]) for k in range(3)))
    errs.sort()
    return sum(errs) / len(errs), errs[int(len(errs) * 0.99)], errs[-1]


def write_cube(path, title, vals):
    with open(path, 'w', newline='\n') as f:
        f.write('# Fujifilm X-Trans III film simulation, relative to PROVIA/STANDARD (display sRGB)\n')
        f.write('# Derived from Stuart Sowerby\'s Fuji Film Simulation Profiles (https://blog.sowerby.me/fuji-film-simulation-profiles/)\n')
        f.write('TITLE "%s"\nLUT_3D_SIZE %d\nDOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1.0 1.0 1.0\n' % (title, M))
        for v in vals:
            f.write('%.6f %.6f %.6f\n' % (v[0], v[1], v[2]))


def main():
    hald_dir, out_dir = sys.argv[1], sys.argv[2]
    stride = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    jobs = [(hald_dir, None, stride)] + [(hald_dir, s[0], stride) for s in SIMS]
    with Pool(min(len(jobs), os.cpu_count() or 4)) as pool:
        res = dict(pool.map(accumulate, jobs, chunksize=1))
    print('accumulated in %.0f s' % (time.time() - t0), flush=True)
    S = res[None]
    pb = load(hald_dir, 'Provia')
    min_weight = 4.0 / (stride ** 3)
    ident = [[n % M / C, (n // M) % M / C, n // (M * M) / C] for n in range(M * M * M)]
    write_cube(os.path.join(out_dir, '01 Camera PROVIA-STANDARD.cube'), 'Camera PROVIA/STANDARD', ident)
    for name, title, stem, kind in SIMS:
        vals, filled = fill(fit(S, res[name], min_weight), kind)
        mean, p99, mx = check(vals, pb, load(hald_dir, name))
        greys = [round(255 * vals[i * (1 + M + M * M)][1]) for i in range(0, M, 4)]
        print('%-16s filled %5d of %d nodes  err mean %.2f  p99 %.2f  max %.1f  greys %s' % (name, filled, M ** 3, mean, p99, mx, greys), flush=True)
        write_cube(os.path.join(out_dir, stem + '.cube'), title, vals)
    print('done in %.0f s' % (time.time() - t0))


if __name__ == '__main__':
    main()
