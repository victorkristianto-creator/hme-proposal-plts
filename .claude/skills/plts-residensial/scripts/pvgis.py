"""Ambil hasil PVGIS (kWh/kWp/th) per bidang atap.

  python pvgis.py work/  [--loss 14]
Membaca work/meta.json (lat/lng) + work/planes.json, menulis work/pvgis.json  {id: {yield, monthly[12], angle, aspect}}.
Hanya mengirim lat/lon + kemiringan/azimut ke API PVGIS (JRC, Komisi Eropa). Tanpa internet: lewati, aplikasi memakai estimasi.
"""
import argparse, json, os, sys, time, urllib.request, urllib.parse

API = 'https://re.jrc.ec.europa.eu/api/v5_3/PVcalc'


def pvcalc(lat, lon, tilt, az, loss):
    q = dict(lat=f'{lat:.5f}', lon=f'{lon:.5f}', peakpower=1, loss=loss, angle=round(tilt, 1), aspect=round(((az - 180 + 180) % 360) - 180, 1),
             outputformat='json', mountingplace='building', pvtechchoice='crystSi')
    url = API + '?' + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={'User-Agent': 'plts-residensial/1.0'})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    fx = d['outputs']
    return dict(yield_=round(fx['totals']['fixed']['E_y']), monthly=[round(m['E_m'], 1) for m in fx['monthly']['fixed']], angle=q['angle'], aspect=q['aspect'])


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('work')
    ap.add_argument('--loss', type=float, default=14)
    a = ap.parse_args()
    meta = json.load(open(os.path.join(a.work, 'meta.json')))
    planes = json.load(open(os.path.join(a.work, 'planes.json')))['planes']
    out = {}
    for p in planes:
        try:
            r = pvcalc(meta['lat'], meta['lng'], p['tilt'], p['az'], a.loss)
            out[p['id']] = dict(yield_kwh_kwp=r['yield_'], monthly=r['monthly'], angle=r['angle'], aspect=r['aspect'], loss=a.loss)
            print(f"{p['name']:<10} az {p['az']:5.1f} tilt {p['tilt']:4.1f} -> {r['yield_']} kWh/kWp/th")
        except Exception as e:
            print(f"{p['name']}: PVGIS gagal ({e})", file=sys.stderr)
        time.sleep(0.4)
    json.dump(out, open(os.path.join(a.work, 'pvgis.json'), 'w'), indent=1)
