"""Deteksi & fit bidang atap dari raster.npz (hasil prepare_model.py).

  python fit_planes.py work/ [--keep 1,2,3] [--max-dist 9] [--min-area 3]

Hasil:
  work/planes.json   : bidang (z = a x + b y + c, ENU lokal), poligon denah, azimut, kemiringan, RMS, + halangan
  work/planes.png    : pratinjau — warna per bidang + nomor. WAJIB dilihat: buang bidang tetangga dengan --keep.

Algoritme: gradien DSM (dihaluskan 0,5 m) -> kelas arah hadap (puncak histogram azimut) -> komponen terhubung ->
fit bidang robust -> tumbuhkan ulang berdasarkan jarak ke bidang (supaya tepi jurai/nok tepat) -> kontur -> sederhanakan.
"""
import argparse, json, math, os, sys, warnings
warnings.filterwarnings('ignore')
from collections import deque
import numpy as np

NAMES = [(0, 'Utara'), (45, 'Timur Laut'), (90, 'Timur'), (135, 'Tenggara'), (180, 'Selatan'), (225, 'Barat Daya'), (270, 'Barat'), (315, 'Barat Laut')]
COLORS = ['#38bdf8', '#f472b6', '#a3e635', '#fb923c', '#c084fc', '#facc15', '#2dd4bf', '#f87171', '#60a5fa', '#e879f9']


def box(a, r):
    """rata-rata kotak (2r+1)^2 mengabaikan NaN"""
    v = np.where(np.isnan(a), 0, a); w = (~np.isnan(a)).astype(float)
    def s(x):
        c = np.cumsum(np.cumsum(np.pad(x, ((r + 1, r), (r + 1, r))), 0), 1)
        return c[2 * r + 1:, 2 * r + 1:] - c[:-2 * r - 1, 2 * r + 1:] - c[2 * r + 1:, :-2 * r - 1] + c[:-2 * r - 1, :-2 * r - 1]
    with np.errstate(all='ignore'):
        return s(v) / s(w)


def components(mask, conn8=False):
    lab = np.zeros(mask.shape, int); n = 0
    H, W = mask.shape
    nb = [(1, 0), (-1, 0), (0, 1), (0, -1)] + ([(1, 1), (1, -1), (-1, 1), (-1, -1)] if conn8 else [])
    for y0, x0 in zip(*np.nonzero(mask)):
        if lab[y0, x0]:
            continue
        n += 1; lab[y0, x0] = n; q = deque([(y0, x0)])
        while q:
            y, x = q.popleft()
            for dy, dx in nb:
                yy, xx = y + dy, x + dx
                if 0 <= yy < H and 0 <= xx < W and mask[yy, xx] and not lab[yy, xx]:
                    lab[yy, xx] = n; q.append((yy, xx))
    return lab, n


def dilate(m, r):
    out = m.copy()
    for _ in range(r):
        p = np.pad(out, 1)
        out = p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
    return out


def erode(m, r):
    return ~dilate(~m, r)


def trace(mask):
    """Kontur luar (Moore) komponen terbesar -> list (row, col) sudut sel."""
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return []
    # mulai dari sel paling atas-kiri
    i = np.lexsort((xs, ys))[0]; start = (ys[i], xs[i])
    H, W = mask.shape
    inside = lambda p: 0 <= p[0] < H and 0 <= p[1] < W and mask[p]
    dirs = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]
    cont = [start]; cur = start; back = 6  # datang dari kiri
    for _ in range(4 * mask.sum() + 10):
        found = False
        for k in range(8):
            d = (back + 1 + k) % 8
            nxt = (cur[0] + dirs[d][0], cur[1] + dirs[d][1])
            if inside(nxt):
                back = (d + 4) % 8; cur = nxt; found = True
                break
        if not found or cur == start:
            break
        cont.append(cur)
    return cont


def rdp(pts, eps):
    pts = np.asarray(pts, float)
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]; ab = b - a; L = np.hypot(*ab)
    d = np.abs(ab[0] * (pts[:, 1] - a[1]) - ab[1] * (pts[:, 0] - a[0])) / L if L > 0 else np.hypot(*(pts - a).T)
    i = int(np.argmax(d))
    if d[i] > eps:
        return np.vstack([rdp(pts[:i + 1], eps)[:-1], rdp(pts[i:], eps)])
    return np.vstack([a, b])


def simplify_closed(pts, eps):
    pts = np.asarray(pts, float)
    far = int(np.argmax(np.hypot(*(pts - pts[0]).T)))
    p1 = rdp(pts[:far + 1], eps); p2 = rdp(np.vstack([pts[far:], pts[:1]]), eps)
    poly = np.vstack([p1[:-1], p2[:-1]])
    # buang titik hampir segaris
    keep = []
    for i in range(len(poly)):
        A, B, C = poly[i - 1], poly[i], poly[(i + 1) % len(poly)]
        ab, bc = B - A, C - B
        if np.hypot(*ab) < 1e-6:
            continue
        ang = abs(math.degrees(math.atan2(ab[0] * bc[1] - ab[1] * bc[0], np.dot(ab, bc))))
        if ang > 8:
            keep.append(B)
    return np.array(keep)


def hull(pts):
    pts = sorted(set(map(tuple, pts)))
    if len(pts) < 3:
        return np.array(pts)
    cr = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for p in pts:
        while len(lo) >= 2 and cr(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cr(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return np.array(lo[:-1] + up[:-1])  # CCW


def poly_area(P):
    x, y = P[:, 0], P[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def clean_polygon(m, X, Y, cell):
    """Poligon bersih dari mask bidang: buka (hapus taji), convex hull (bila bidang hampir cembung) atau kontur RDP 0,25 m,
    lalu tiap sisi panjang digeser ke MEDIAN titik tepi (tak bias ke luar) dan sudut = perpotongan garis berurutan."""
    mo = dilate(erode(m, 2), 2)
    if mo.sum() < 50:
        mo = m
    rr, cc = np.nonzero(mo)
    h = cell / 2
    corners = np.vstack([np.c_[X[cc] + dx, Y[rr] + dy] for dx in (-h, h) for dy in (-h, h)])
    P = hull(corners)
    if poly_area(P) > 1.12 * mo.sum() * cell * cell:     # tidak cembung (mis. ada lembah) -> pakai kontur
        cont = trace(mo)
        P = simplify_closed(np.array([[X[c], Y[r]] for r, c in cont]), 0.25)
        if len(P) < 3:
            return None
        if np.dot(P[:, 0], np.roll(P[:, 1], -1)) - np.dot(P[:, 1], np.roll(P[:, 0], -1)) < 0:
            P = P[::-1]
    if len(P) > 3:
        P = simplify_closed(P, 0.15)
    # sel tepi
    edge = mo & ~erode(mo, 1)
    er, ec = np.nonzero(edge)
    B = np.c_[X[ec], Y[er]]
    n = len(P); lines = []
    for i in range(n):
        A_, C_ = P[i], P[(i + 1) % n]
        d = C_ - A_; L = np.hypot(*d)
        if L < 0.6:                                         # sisi pendek dibuang; sudutnya dibentuk ulang oleh tetangga
            continue
        u = d / L; nin = np.array([-u[1], u[0]])            # CCW -> kiri = dalam
        rel = B - A_
        along, off = rel @ u, rel @ nin
        sel = (along > 0.15 * L) & (along < 0.85 * L) & (np.abs(off) < 0.5)
        o = float(np.median(off[sel])) - h if sel.sum() > 8 else 0.0   # -h: pusat sel tepi -> tepi luar sel
        lines.append((A_ + nin * o, u, C_))
    if len(lines) < 3:
        return P
    out = []
    for i in range(len(lines)):
        (p1, u1, end1), (p2, u2, _) = lines[i - 1], lines[i]
        den = u1[0] * u2[1] - u1[1] * u2[0]
        ref = end1                                           # sudut asli di antara dua sisi
        if abs(den) < 0.2:                                   # hampir sejajar (sisi pendek di antaranya dibuang)
            q1 = p1 + u1 * ((ref - p1) @ u1); q2 = p2 + u2 * ((ref - p2) @ u2)
            out += [q1, q2]; continue
        t = ((p2[0] - p1[0]) * u2[1] - (p2[1] - p1[1]) * u2[0]) / den
        q = p1 + u1 * t
        out.append(q if np.hypot(*(q - ref)) < 1.2 else ref)
    return np.array(out)


def fit_plane(x, y, z, iters=4):
    m = np.ones(len(x), bool)
    for _ in range(iters):
        A = np.c_[x[m], y[m], np.ones(m.sum())]
        coef, *_ = np.linalg.lstsq(A, z[m], rcond=None)
        r = z - (coef[0] * x + coef[1] * y + coef[2])
        s = max(0.02, 2.5 * np.std(r[m]))
        m = np.abs(r) < s
    return coef, float(np.sqrt(np.mean(r[m] ** 2))), m


def az_name(az):
    return min(NAMES, key=lambda t: min(abs(az - t[0]), 360 - abs(az - t[0])))[1]


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('work')
    ap.add_argument('--keep', help='nomor bidang yang dipakai (lihat planes.png), mis. 1,2,4')
    ap.add_argument('--max-dist', type=float, default=9, help='default: hanya bidang yang pusatnya <= jarak ini dari pusat rumah')
    ap.add_argument('--min-area', type=float, default=3, help='m²')
    ap.add_argument('--hmin', type=float, default=2.2, help='tinggi minimum atap dari tanah (m)')
    ap.add_argument('--grow-zone', type=float, default=2.0, help='jarak maks dari inti bidang saat tumbuh-ulang (m)')
    ap.add_argument('--grow-tol', type=float, default=0.12, help='jarak maks sel ke bidang saat tumbuh-ulang (m)')
    ap.add_argument('--obs-height', type=float, default=0.35, help='tonjolan di atas bidang yang dianggap halangan (m)')
    a = ap.parse_args()
    R = np.load(os.path.join(a.work, 'raster.npz'))
    dsm, col, x0, y0, cell = R['dsm'].astype(float), R['col'], float(R['x0']), float(R['y0']), float(R['cell'])
    H, W = dsm.shape
    X = x0 + (np.arange(W) + 0.5) * cell; Y = y0 - (np.arange(H) + 0.5) * cell
    XX, YY = np.meshgrid(X, Y)

    sm = box(dsm, int(round(0.25 / cell)))
    k = int(round(0.2 / cell))
    gx = (np.roll(sm, -k, 1) - np.roll(sm, k, 1)) / (2 * k * cell)
    gy = -(np.roll(sm, -k, 0) - np.roll(sm, k, 0)) / (2 * k * cell)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    az = (np.degrees(np.arctan2(-gx, -gy)) + 360) % 360
    cand = (dsm > a.hmin) & (slope > 8) & (slope < 60) & ~np.isnan(slope)
    # puncak histogram azimut
    hist, edges = np.histogram(az[cand], bins=72, range=(0, 360))
    hs = np.convolve(np.r_[hist[-2:], hist, hist[:2]], np.ones(5) / 5, 'valid')
    peaks = [i for i in range(72) if hs[i] >= hs[i - 1] and hs[i] >= hs[(i + 1) % 72] and hs[i] > 0.03 * cand.sum() / 72 * 5]
    peak_az = [(i + 0.5) * 5 for i in peaks]
    # gabung puncak berdekatan (< 25°)
    merged = []
    for p in sorted(peak_az, key=lambda p: -hs[int(p // 5)]):
        if all(min(abs(p - q), 360 - abs(p - q)) > 25 for q in merged):
            merged.append(p)
    diff = lambda A, B: np.abs((A - B + 180) % 360 - 180)
    planes, label = [], np.zeros((H, W), int)
    for pa in merged:
        cls = cand & (diff(az, pa) < 22)
        lab, n = components(cls)
        for i in range(1, n + 1):
            m = lab == i
            if m.sum() * cell * cell < a.min_area * 0.6:
                continue
            coef, rms, inl = fit_plane(XX[m], YY[m], dsm[m])
            seed = np.zeros_like(m); seed[m] = inl          # benih = inlier saja (komponen bisa tercampur bidang lain)
            if seed.sum() * cell * cell >= a.min_area * 0.4:
                planes.append(dict(coef=coef, seed=seed))
    # tumbuhkan ulang: tiap sel ke bidang terdekat (|res| < grow-tol) dalam radius grow-zone dari benihnya.
    # Diulang: bidang di-fit ulang dari hasil tumbuh, lalu ditumbuhkan lagi (bidang benih bisa meleset ±20 cm).
    for it in range(3):
        best = np.full((H, W), np.inf); owner = np.zeros((H, W), int)
        for idx, p in enumerate(planes, 1):
            a_, b_, c_ = p['coef']
            res = np.abs(dsm - (a_ * XX + b_ * YY + c_))
            zone = dilate(p['seed'], int(round(a.grow_zone / cell)))
            ok = zone & (res < a.grow_tol) & (dsm > a.hmin) & (res < best)
            best[ok] = res[ok]; owner[ok] = idx
        if it == 2:
            break
        for idx, p in enumerate(planes, 1):
            m = owner == idx
            if m.sum() < 30:
                continue
            lab, n = components(m)
            sizes = np.bincount(lab.ravel())[1:]; m = lab == (np.argmax(sizes) + 1)
            coef, _, inl = fit_plane(XX[m], YY[m], dsm[m])
            p['coef'] = coef; p['seed'] = np.zeros_like(m); p['seed'][m] = inl
    out, obstacles = [], []
    for idx, p in enumerate(planes, 1):
        m = owner == idx
        lab, n = components(m)
        if not n:
            continue
        sizes = np.bincount(lab.ravel())[1:]; m = lab == (np.argmax(sizes) + 1)
        m = erode(dilate(m, 2), 2)            # tutup lubang kecil
        area_plan = m.sum() * cell * cell
        if area_plan < a.min_area:
            continue
        coef, rms, _ = fit_plane(XX[m], YY[m], dsm[m])
        A_, B_, C_ = coef
        tilt = math.degrees(math.atan(math.hypot(A_, B_)))
        azp = (math.degrees(math.atan2(-A_, -B_)) + 360) % 360
        if tilt < 5:
            continue
        poly = clean_polygon(m, X, Y, cell)
        if poly is None or len(poly) < 3:
            continue
        cxy = [float(XX[m].mean()), float(YY[m].mean())]
        out.append(dict(mask=m, coef=coef, rms=rms, tilt=tilt, az=azp, poly=poly, area=area_plan / math.cos(math.radians(tilt)), c=cxy))
    # urutkan: dekat pusat dulu
    out.sort(key=lambda p: math.hypot(*p['c']))
    keep = set(map(int, a.keep.split(','))) if a.keep else None
    sel = []
    for i, p in enumerate(out, 1):
        p['no'] = i
        p['use'] = (i in keep) if keep else (math.hypot(*p['c']) <= a.max_dist)
        if p['use']:
            sel.append(p)
    # halangan: sel di dalam/menempel bidang terpilih yang menonjol > obs-height di atas bidang terdekat
    if sel:
        house = np.zeros((H, W), bool)
        for p in sel:
            house |= p['mask']
        near = dilate(house, int(round(1.0 / cell))) & ~house
        # tinggi di atas SEMUA bidang yang zonanya (1 m) mencakup sel -> nok/jurai (dua bidang bertemu) tidak terdeteksi palsu
        above = np.full((H, W), np.inf)
        for p in sel:
            a_, b_, c_ = p['coef']
            zone = dilate(p['mask'], int(round(1.0 / cell)))
            h = np.where(zone, dsm - (a_ * XX + b_ * YY + c_), np.inf)
            above = np.minimum(above, h)
        above[np.isinf(above)] = -np.inf
        # hanya yang berdiri DI DALAM poligon bidang (menara, tandon di atas atap, dsb.). Benda di samping lisplank
        # (tandon di dak, parapet, dinding tetangga) cukup ikut perhitungan bayangan lewat mesh, tidak membatasi penempatan.
        from PIL import Image as _I, ImageDraw as _D
        pim = _I.new('L', (W, H), 0); pd = _D.Draw(pim)
        for p in sel:
            pd.polygon([((x - x0) / cell - 0.5, (y0 - y) / cell - 0.5) for x, y in p['poly']], fill=1)
        inpoly = erode(np.asarray(pim, bool), 1)
        om = inpoly & (above > a.obs_height)
        lab, n = components(om, True)
        T = int(round(0.8 / cell))   # pecah tiap komponen jadi ubin 0,8 m supaya kotak mengikuti bentuk (dinding memanjang ≠ kotak besar)
        g = 0
        for i in range(1, n + 1):
            m = lab == i
            if m.sum() * cell * cell < 0.25:
                continue
            g += 1
            rr, cc = np.nonzero(m)
            for key in sorted(set(zip(rr // T, cc // T))):
                sub = np.zeros_like(m); sl = (slice(key[0] * T, key[0] * T + T), slice(key[1] * T, key[1] * T + T))
                sub[sl] = m[sl]
                if sub.sum() < 4:
                    continue
                xs, ys = XX[sub], YY[sub]
                h2 = cell / 2
                obstacles.append(dict(name=f'halangan {g}', poly=[[round(float(xs.min() - h2), 2), round(float(ys.min() - h2), 2)], [round(float(xs.max() + h2), 2), round(float(ys.min() - h2), 2)],
                                                                [round(float(xs.max() + h2), 2), round(float(ys.max() + h2), 2)], [round(float(xs.min() - h2), 2), round(float(ys.max() + h2), 2)]],
                                      zTop=round(float(np.nanmax(dsm[sub])), 2), height_above=round(float(above[sub].max()), 2)))
    # simpan
    res = dict(planes=[], obstacles=obstacles, all=[])
    names = {}
    for p in out:
        nm = az_name(p['az']); names[nm] = names.get(nm, 0) + 1
        rec = dict(no=p['no'], id=f"b{p['no']}", name=nm, color=COLORS[(p['no'] - 1) % len(COLORS)],
                   plane=dict(a=round(float(p['coef'][0]), 5), b=round(float(p['coef'][1]), 5), c=round(float(p['coef'][2]), 4)),
                   poly=[[round(float(x), 3), round(float(y), 3)] for x, y in p['poly']],
                   az=round(p['az'], 1), tilt=round(p['tilt'], 1), rms_m=round(p['rms'], 3), area_m2=round(p['area'], 1),
                   center=[round(v, 2) for v in p['c']], dist=round(math.hypot(*p['c']), 1))
        res['all'].append(rec)
        if p['use']:
            res['planes'].append(rec)
    # nama unik
    cnt = {}
    for r in res['planes']:
        base = r['name']; cnt[base] = cnt.get(base, 0) + 1
    seen = {}
    for r in res['planes']:
        base = r['name']
        if cnt[base] > 1:
            seen[base] = seen.get(base, 0) + 1; r['name'] = f'{base} {seen[base]}'
        r['id'] = r['name'].lower().replace(' ', '_')
    json.dump(res, open(os.path.join(a.work, 'planes.json'), 'w'), indent=1)
    # pratinjau
    from PIL import Image, ImageDraw
    S = 3
    im = Image.fromarray(col).resize((W * S // 1, H * S // 1), Image.NEAREST).convert('RGBA')
    ov = Image.new('RGBA', im.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
    tpx = lambda x, y: ((x - x0) / cell * S, (y0 - y) / cell * S)
    for p, rec in zip(out, res['all']):
        c = rec['color']; rgb = tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
        used = p['use']
        d.polygon([tpx(*q) for q in p['poly']], fill=rgb + ((90,) if used else (25,)), outline=rgb + (255,))
        cx, cy = tpx(*p['c'])
        txt = f"{p['no']}{'' if used else ' (x)'} {rec['az']:.0f}°/{rec['tilt']:.0f}°"
        d.rectangle([cx - 4, cy - 9, cx + 8 * len(txt), cy + 9], fill=(0, 0, 0, 170)); d.text((cx, cy - 6), txt, fill=(255, 255, 255, 255))
    named = set()
    for o in obstacles:
        (xa, ya), (xb, _), (_, yb) = o['poly'][0], o['poly'][1], o['poly'][2]
        d.rectangle([tpx(xa, yb), tpx(xb, ya)], outline=(239, 68, 68, 255), width=2)
        if o['name'] not in named:
            named.add(o['name']); d.text(tpx(xa, yb), o['name'], fill=(255, 80, 80, 255))
    cx, cy = tpx(0, 0); d.line([cx - 10, cy, cx + 10, cy], fill=(255, 255, 0, 255), width=2); d.line([cx, cy - 10, cx, cy + 10], fill=(255, 255, 0, 255), width=2)
    d.text((10, 10), 'U ^', fill=(255, 0, 0, 255))
    Image.alpha_composite(im, ov).convert('RGB').save(os.path.join(a.work, 'planes.png'))
    for r in res['all']:
        print(f"{r['no']:>2} {'*' if r in res['planes'] else ' '} {r['name']:<11} az {r['az']:6.1f}  tilt {r['tilt']:5.1f}  rms {r['rms_m']:.3f}  luas {r['area_m2']:6.1f} m²  jarak {r['dist']:4.1f} m")
    for nm in sorted(set(o['name'] for o in obstacles)):
        os_ = [o for o in obstacles if o['name'] == nm]
        print(f"{nm}: {len(os_)} kotak, tinggi maks +{max(o['height_above'] for o in os_)} m di atas bidang")
