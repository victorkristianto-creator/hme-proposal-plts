"""Model atap sederhana dari ukuran (tanpa WebODM). Akurasi lebih rendah -> ditandai 'manual' di aplikasi.

  python manual_roof.py spec.json work/

spec.json contoh (meter, derajat):
{
  "lat": -7.2891542, "lng": 112.6495689,
  "front_bearing": 172,          # arah hadap muka rumah (azimut, 0 = utara); jalan di depan
  "width": 10.0, "depth": 14.0,  # lebar (sejajar jalan) x panjang
  "eave": 7.5,                   # tinggi lisplank dari tanah
  "type": "hip",                 # hip (perisai/limas) | gable (pelana, nok sejajar jalan) | gable-deep (nok tegak lurus jalan) | shed
  "pitch": 35,                   # kemiringan atap
  "overhang": 0.6,
  "obstacles": [ {"name": "tandon", "x": -3, "y": -2, "w": 1.2, "d": 1.2, "h": 1.5} ]   # x,y relatif pusat rumah, sumbu lokal rumah
}
Hasil: work/model_local.glb, work/meta.json, work/planes.json (semua bidang terpilih) -> lanjut pvgis.py & make_site.py --accuracy manual
"""
import json, math, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plts_common import write_glb

COLORS = ['#38bdf8', '#f472b6', '#a3e635', '#fb923c']


def pip(poly, x, y):
    ins = False
    for i in range(len(poly)):
        (xi, yi), (xj, yj) = poly[i], poly[i - 1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            ins = not ins
    return ins


def az_name(az):
    names = [(0, 'Utara'), (45, 'Timur Laut'), (90, 'Timur'), (135, 'Tenggara'), (180, 'Selatan'), (225, 'Barat Daya'), (270, 'Barat'), (315, 'Barat Laut')]
    return min(names, key=lambda t: min(abs(az - t[0]), 360 - abs(az - t[0])))[1]


if __name__ == '__main__':
    spec = json.load(open(sys.argv[1], encoding='utf-8')); out = sys.argv[2]; os.makedirs(out, exist_ok=True)
    W, D, H, pitch, ov = spec['width'], spec['depth'], spec['eave'], math.radians(spec.get('pitch', 30)), spec.get('overhang', 0.5)
    fb = math.radians(spec.get('front_bearing', 180))
    # sumbu lokal rumah: u = kanan (dilihat dari jalan), w = ke depan (arah jalan). ENU: depan = (sin fb, cos fb)
    fwd = np.array([math.sin(fb), math.cos(fb)])
    right = np.array([-fwd[1], fwd[0]])                      # kanan bagi orang di jalan yang menghadap rumah
    L = lambda u, w: right * u + fwd * w                      # lokal -> ENU xy
    hw, hd = W / 2 + ov, D / 2 + ov
    t = math.tan(pitch)
    typ = spec.get('type', 'hip')
    faces = []   # (nama sementara, [titik lokal (u,w,z)])
    if typ == 'hip':
        r = min(hw, hd); ridge_h = H + r * t
        if hd >= hw:   # nok sejajar kedalaman
            a, b = (0, -(hd - hw)), (0, hd - hw)
            faces = [[(-hw, -hd, H), (-hw, hd, H), (a[0], b[1], ridge_h), (a[0], a[1], ridge_h)],
                     [(hw, hd, H), (hw, -hd, H), (0, a[1], ridge_h), (0, b[1], ridge_h)],
                     [(-hw, hd, H), (hw, hd, H), (0, b[1], ridge_h)], [(hw, -hd, H), (-hw, -hd, H), (0, a[1], ridge_h)]]
        else:
            a, b = (-(hw - hd), 0), (hw - hd, 0)
            faces = [[(hw, hd, H), (-hw, hd, H), (a[0], 0, ridge_h), (b[0], 0, ridge_h)],
                     [(-hw, -hd, H), (hw, -hd, H), (b[0], 0, ridge_h), (a[0], 0, ridge_h)],
                     [(-hw, -hd, H), (a[0], 0, ridge_h), (-hw, hd, H)], [(hw, hd, H), (b[0], 0, ridge_h), (hw, -hd, H)]]
    elif typ == 'gable':      # nok sejajar jalan (sepanjang u)
        rh = H + hd * t
        faces = [[(-hw, hd, H), (hw, hd, H), (hw, 0, rh), (-hw, 0, rh)], [(hw, -hd, H), (-hw, -hd, H), (-hw, 0, rh), (hw, 0, rh)]]
    elif typ == 'gable-deep':
        rh = H + hw * t
        faces = [[(-hw, -hd, H), (-hw, hd, H), (0, hd, rh), (0, -hd, rh)], [(hw, hd, H), (hw, -hd, H), (0, -hd, rh), (0, hd, rh)]]
    else:  # shed: miring ke depan
        faces = [[(-hw, hd, H), (hw, hd, H), (hw, -hd, H + 2 * hd * t), (-hw, -hd, H + 2 * hd * t)]]
    planes = []
    tris_pos, tris_col = [], []
    for i, f in enumerate(faces):
        P = np.array([[*L(u, w), z] for u, w, z in f])
        A = np.c_[P[:, 0], P[:, 1], np.ones(len(P))]
        a_, b_, c_ = np.linalg.lstsq(A, P[:, 2], rcond=None)[0]
        az = (math.degrees(math.atan2(-a_, -b_)) + 360) % 360
        tilt = math.degrees(math.atan(math.hypot(a_, b_)))
        planes.append(dict(no=i + 1, id=f'b{i + 1}', name=az_name(az), color=COLORS[i % 4], plane=dict(a=round(a_, 5), b=round(b_, 5), c=round(c_, 4)),
                           poly=[[round(x, 3), round(y, 3)] for x, y, _ in P], az=round(az, 1), tilt=round(tilt, 1), rms_m=0.0))
        for k in range(1, len(P) - 1):
            tris_pos += [P[0], P[k], P[k + 1]]; tris_col.append(i)
    # nama unik
    seen = {}
    for p in planes:
        seen[p['name']] = seen.get(p['name'], 0) + 1
    cnt = {}
    for p in planes:
        if seen[p['name']] > 1:
            cnt[p['name']] = cnt.get(p['name'], 0) + 1; p['name'] += f' {cnt[p["name"]]}'
        p['id'] = p['name'].lower().replace(' ', '_')
    # mesh: atap + dinding kotak + halangan (tanpa tekstur)
    def box(cx, cy, w, d, z0, z1, ang):
        pts = []
        for dz in (z0, z1):
            for du, dw in ((-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)):
                xy = np.array([cx, cy]) + right * du + fwd * dw; pts.append([xy[0], xy[1], dz])
        q = [[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6], [0, 4, 5], [0, 5, 1], [1, 5, 6], [1, 6, 2], [2, 6, 7], [2, 7, 3], [3, 7, 4], [3, 4, 0]]
        return [pts[a] for tri in q for a in tri]
    walls = box(0, 0, W, D, 0, H, 0)
    obs_out = []
    for o in spec.get('obstacles', []):
        c = L(o['x'], o['y'])
        # dasar halangan = tinggi atap di titik itu (perkiraan: maks bidang)
        inside = [p for p in planes if pip(p['poly'], c[0], c[1])]
        zb = min(p['plane']['a'] * c[0] + p['plane']['b'] * c[1] + p['plane']['c'] for p in inside) if inside else H
        walls += box(c[0], c[1], o['w'], o['d'], H, zb + o['h'], 0)
        corners = [L(o['x'] + du, o['y'] + dw) for du, dw in ((-o['w'] / 2, -o['d'] / 2), (o['w'] / 2, -o['d'] / 2), (o['w'] / 2, o['d'] / 2), (-o['w'] / 2, o['d'] / 2))]
        obs_out.append(dict(name=o.get('name', 'halangan'), poly=[[round(x, 2), round(y, 2)] for x, y in corners], zTop=round(zb + o['h'], 2)))
    prims = []
    for pos, colr in ((np.array(tris_pos), (0.62, 0.28, 0.18)), (np.array(walls), (0.92, 0.89, 0.83))):
        e = np.asarray(pos, float)
        three = np.c_[e[:, 0], e[:, 2], -e[:, 1]].astype(np.float32)
        prims.append(dict(pos=three, uv=None, idx=np.arange(len(three), dtype=np.uint32).reshape(-1, 3), image=None, color=colr))
    write_glb(os.path.join(out, 'model_local.glb'), prims, 'plts-residensial/manual_roof')
    json.dump(dict(planes=planes, obstacles=obs_out, all=planes), open(os.path.join(out, 'planes.json'), 'w'), indent=1)
    json.dump(dict(lat=spec['lat'], lng=spec['lng'], source='manual', note='model sederhana dari ukuran'), open(os.path.join(out, 'meta.json'), 'w'), indent=1)
    for p in planes:
        print(f"{p['name']:<10} az {p['az']:6.1f} tilt {p['tilt']:5.1f}")
