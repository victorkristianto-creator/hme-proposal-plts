"""Siapkan model WebODM untuk PLTS Roof Designer.

  python prepare_model.py textured_model.glb --gps -7.2500000,112.7500000 --radius 22 --out work/
  (atau --center-local X,Y dalam meter relatif pusat CESIUM_RTC)

Hasil di --out:
  model_local.glb  : potongan mesh di sekitar rumah, frame three.js (X timur, Y atas, Z selatan; tanah = 0), tanpa Draco
  meta.json        : pusat UTM, lat/lon, elevasi tanah, konvergensi meridian, dsb.
  raster.npz       : DSM 10 cm + warna (untuk fit_planes.py & render_top.py)
  top.png          : citra tampak atas dari tekstur model (untuk CEK IDENTITAS rumah)
"""
import argparse, io, json, math, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plts_common import read_glb, write_glb, latlon_to_utm, utm_to_latlon, meridian_convergence_deg


def sample_surface(prims, cell, rng):
    """Sampel titik di permukaan semua segitiga (kepadatan ~4 per sel) + warna tekstur. -> xyz (N,3), rgb (N,3)"""
    from PIL import Image
    XYZ, RGB = [], []
    for p in prims:
        if not len(p['idx']):
            continue
        tri = p['pos'][p['idx']]                                    # (M,3,3)
        a = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) / 2
        # luas proyeksi horizontal penting untuk DSM; pakai luas 3D supaya dinding juga tersampel
        k = np.clip(np.ceil(a / (cell * cell) * 4).astype(int), 1, 400)
        ti = np.repeat(np.arange(len(tri)), k)
        r1, r2 = rng.random(len(ti)), rng.random(len(ti))
        s = np.sqrt(r1)
        w0, w1, w2 = 1 - s, s * (1 - r2), s * r2
        T = tri[ti]
        pts = T[:, 0] * w0[:, None] + T[:, 1] * w1[:, None] + T[:, 2] * w2[:, None]
        if p.get('uv') is not None and p.get('image') is not None:
            im = np.asarray(Image.open(io.BytesIO(p['image'])).convert('RGB'))
            uvt = p['uv'][p['idx']][ti]
            uv = uvt[:, 0] * w0[:, None] + uvt[:, 1] * w1[:, None] + uvt[:, 2] * w2[:, None]
            h, w = im.shape[:2]
            px = np.clip((uv[:, 0] % 1) * (w - 1), 0, w - 1).astype(int)
            py = np.clip((uv[:, 1] % 1) * (h - 1), 0, h - 1).astype(int)   # glTF: v=0 di atas gambar
            col = im[py, px]
        else:
            col = np.full((len(pts), 3), 180, np.uint8)
        XYZ.append(pts); RGB.append(col)
    return np.vstack(XYZ), np.vstack(RGB)


def rasterize(xyz, rgb, x0, y0, nx, ny, cell):
    ix = np.floor((xyz[:, 0] - x0) / cell).astype(int)
    iy = np.floor((y0 - xyz[:, 1]) / cell).astype(int)           # baris 0 = utara
    ok = (ix >= 0) & (iy >= 0) & (ix < nx) & (iy < ny)
    ix, iy, z, c = ix[ok], iy[ok], xyz[ok, 2], rgb[ok]
    lin = iy * nx + ix
    order = np.lexsort((z, lin))                                  # per sel, z terakhir = tertinggi
    lin, z, c = lin[order], z[order], c[order]
    last = np.r_[lin[1:] != lin[:-1], True]
    dsm = np.full(nx * ny, np.nan); col = np.zeros((nx * ny, 3), np.uint8)
    dsm[lin[last]] = z[last]; col[lin[last]] = c[last]
    dsm = dsm.reshape(ny, nx); col = col.reshape(ny, nx, 3)
    # tambal lubang kecil (1-2 sel) dengan maksimum tetangga
    for _ in range(2):
        m = np.isnan(dsm)
        if not m.any():
            break
        pad = np.pad(dsm, 1, constant_values=np.nan)
        stack = np.stack([pad[1 + dy:1 + dy + ny, 1 + dx:1 + dx + nx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)])
        with np.errstate(all='ignore'):
            fill = np.nanmax(np.where(np.isnan(stack), -1e9, stack), axis=0)
        fill[fill < -1e8] = np.nan
        dsm[m] = fill[m]
        padc = np.pad(col, ((1, 1), (1, 1), (0, 0)))
        cs = np.stack([padc[1 + dy:1 + dy + ny, 1 + dx:1 + dx + nx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)]).max(0)
        col[m] = cs[m]
    return dsm, col


def crop_prims(prims, cx, cy, R):
    out = []
    for p in prims:
        tri = p['pos'][p['idx']]
        c = tri.mean(1)
        keep = (np.hypot(c[:, 0] - cx, c[:, 1] - cy) <= R)
        if not keep.any():
            continue
        idx = p['idx'][keep]
        used, inv = np.unique(idx.reshape(-1), return_inverse=True)
        out.append(dict(pos=p['pos'][used], uv=None if p['uv'] is None else p['uv'][used], idx=inv.reshape(-1, 3).astype(np.uint32),
                        image=p['image'], mime=p['mime']))
    return out


def shrink_image(data, max_px, quality):
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert('RGB')
    if max(im.size) > max_px:
        s = max_px / max(im.size)
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, 'JPEG', quality=quality, optimize=True)
    return b.getvalue()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('glb')
    ap.add_argument('--gps', help='lat,lon pusat rumah')
    ap.add_argument('--center-local', help='x,y (m) relatif pusat RTC/UTM model')
    ap.add_argument('--radius', type=float, default=22, help='radius potong (m)')
    ap.add_argument('--ground', type=float, help='elevasi tanah/jalan di model (m); default persentil 3 di potongan')
    ap.add_argument('--utm-zone', type=int, default=49)
    ap.add_argument('--tex', type=int, default=2048, help='sisi maks tekstur (px)')
    ap.add_argument('--quality', type=int, default=82)
    ap.add_argument('--cell', type=float, default=0.10)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    prims, j = read_glb(a.glb)
    rtc = (j.get('extensions') or {}).get('CESIUM_RTC', {}).get('center', [0, 0, 0])
    E0, N0 = float(rtc[0]), float(rtc[1])
    if not E0:
        print('PERINGATAN: GLB tanpa CESIUM_RTC — anggap koordinat lokal sudah UTM-relatif tidak diketahui; --gps tidak bisa dipakai.')
    if a.gps:
        lat, lon = map(float, a.gps.split(','))
        e, n, zone, south = latlon_to_utm(lat, lon)
        cx, cy = e - E0, n - N0
    elif a.center_local:
        cx, cy = map(float, a.center_local.split(','))
    else:
        P = np.vstack([p['pos'] for p in prims]); cx, cy = np.median(P[:, 0]), np.median(P[:, 1])
    lat, lon = utm_to_latlon(E0 + cx, N0 + cy, a.utm_zone, True)
    conv = meridian_convergence_deg(lat, lon)

    crop = crop_prims(prims, cx, cy, a.radius)
    P = np.vstack([p['pos'] for p in crop])
    ground = a.ground if a.ground is not None else float(np.percentile(P[:, 2], 3))
    # rotasi grid-UTM -> utara sejati: titik di grid berputar -conv terhadap utara sejati
    th = math.radians(conv)
    ct, st = math.cos(th), math.sin(th)

    def to_enu(pos):
        x, y = pos[:, 0] - cx, pos[:, 1] - cy
        return np.c_[x * ct - y * st, x * st + y * ct, pos[:, 2] - ground]

    loc = []
    for p in crop:
        e = to_enu(p['pos'])
        three = np.c_[e[:, 0], e[:, 2], -e[:, 1]].astype(np.float32)
        img = shrink_image(p['image'], a.tex, a.quality) if p['image'] else None
        loc.append(dict(pos=three, uv=p['uv'], idx=p['idx'], image=img, mime='image/jpeg', enu=e))
    size = write_glb(os.path.join(a.out, 'model_local.glb'), loc, 'plts-residensial/prepare_model')

    # raster DSM + warna (ENU lokal)
    rng = np.random.default_rng(1)
    xyz, rgb = sample_surface([dict(pos=p['enu'], uv=p['uv'], idx=p['idx'], image=p['image']) for p in loc], a.cell, rng)
    R = a.radius
    nx = ny = int(math.ceil(2 * R / a.cell))
    dsm, col = rasterize(xyz, rgb, -R, R, nx, ny, a.cell)
    np.savez_compressed(os.path.join(a.out, 'raster.npz'), dsm=dsm.astype(np.float32), col=col, x0=-R, y0=R, cell=a.cell)
    from PIL import Image
    Image.fromarray(col).save(os.path.join(a.out, 'top.png'))

    meta = dict(source=os.path.abspath(a.glb), rtc_utm=[E0, N0], utm_zone=a.utm_zone, center_local=[cx, cy],
                center_utm=[E0 + cx, N0 + cy], lat=round(lat, 7), lng=round(lon, 7), ground_model_z=ground,
                meridian_convergence_deg=round(conv, 3), radius=R, triangles=int(sum(len(p['idx']) for p in loc)),
                glb_bytes=size, frame='ENU lokal: x timur, y utara (sejati), z atas dari tanah; three.js = (x, z, -y)')
    json.dump(meta, open(os.path.join(a.out, 'meta.json'), 'w'), indent=1)
    print(json.dumps(meta, indent=1))
