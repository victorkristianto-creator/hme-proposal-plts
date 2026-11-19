"""Gabungkan hasil kerja menjadi site.json lalu rakit HTML.

  python make_site.py work/ --id jo-derma --name "PLTS — Jo Derma (C4-19)" --out "D:/.../desain_plts_jo_derma.html"
       [--subtitle "..."] [--setback 0.25] [--plane-setback barat=0.2,timur_1=0.2] [--variants variants.json] [--note "..."] [--accuracy webodm|manual]

Membaca work/meta.json, work/planes.json (hanya 'planes' = bidang terpilih), work/pvgis.json (opsional),
work/model_local.glb (opsional), menulis work/site.json dan file HTML.
variants.json (opsional): [{"name": "8 kWp", "modules": [{"p": "<id bidang>", "s": .., "t": .., "o": "P"}]}]
"""
import argparse, json, math, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_html import build

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('work')
    ap.add_argument('--id', required=True)
    ap.add_argument('--name', required=True)
    ap.add_argument('--subtitle', default='')
    ap.add_argument('--note', default='')
    ap.add_argument('--setback', type=float, default=0.25)
    ap.add_argument('--plane-setback', default='', help='setback per bidang, mis. barat=0.2,timur_1=0.2')
    ap.add_argument('--obs-setback', type=float, default=0.30)
    ap.add_argument('--variants')
    ap.add_argument('--module', default='trina720')
    ap.add_argument('--accuracy', default='webodm')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    W = lambda n: os.path.join(a.work, n)
    meta = json.load(open(W('meta.json')))
    pl = json.load(open(W('planes.json')))
    pv = json.load(open(W('pvgis.json'))) if os.path.exists(W('pvgis.json')) else {}
    planes = []
    for p in pl['planes']:
        rec = dict(id=p['id'], name=p['name'], color=p['color'], plane=p['plane'], poly=p['poly'])
        psb = dict(kv.split('=') for kv in a.plane_setback.split(',') if '=' in kv)
        if p['id'] in psb:
            rec['setback'] = float(psb[p['id']])
        if p['id'] in pv:
            rec['yield'] = pv[p['id']]['yield_kwh_kwp']; rec['yieldNote'] = 'PVGIS'
        planes.append(rec)
    xs = [q[0] for p in planes for q in p['poly']]; ys = [q[1] for p in planes for q in p['poly']]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    r = max(math.hypot(x - cx, y - cy) for x, y in zip(xs, ys)) + 1.5
    zmid = max(p['plane']['a'] * cx + p['plane']['b'] * cy + p['plane']['c'] for p in planes) * 0.6 if planes else 4
    obstacles = [dict(name=o['name'], poly=o['poly'], zTop=o.get('zTop'), setback=a.obs_setback) for o in pl.get('obstacles', [])]
    site = dict(id=a.id, name=a.name, subtitle=a.subtitle, lat=meta['lat'], lng=meta['lng'], tz=7, accuracy=a.accuracy,
                fit=dict(center=[round(cx, 2), round(cy, 2), round(zmid, 2)], radius=round(r, 1)), setback=a.setback,
                planes=planes, obstacles=obstacles, defaultModule=a.module,
                variants=json.load(open(a.variants, encoding='utf-8')) if a.variants else [{'name': 'Desain 1', 'modules': []}],
                note=a.note or f"Bidang di-fit otomatis dari mesh WebODM (RMS {', '.join(str(p['rms_m']) for p in pl['planes'])} m). "
                               f"Produksi: PVGIS (loss 14%) per bidang. Lokasi {meta['lat']:.5f}, {meta['lng']:.5f}.",
                source=dict(glb=meta.get('source'), center_utm=meta.get('center_utm'), ground_model_z=meta.get('ground_model_z')))
    json.dump(site, open(W('site.json'), 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    glb = open(W('model_local.glb'), 'rb').read() if os.path.exists(W('model_local.glb')) else None
    n = build(site, glb, a.out)
    print(f'{a.out}: {n / 1e6:.1f} MB · {len(planes)} bidang · {len(obstacles)} kotak halangan')
