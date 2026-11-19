"""GPS dari foto drone DJI: cari foto tegak-lurus ke bawah (nadir) yang paling dekat pusat sebaran -> titik pusat rumah.

  python photos_gps.py <folder_foto> [--max 400]
"""
import os, re, sys, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image


def info(path):
    im = Image.open(path)
    ex = im._getexif() or {}
    g = ex.get(34853)
    if not g:
        return None
    dms = lambda v: float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
    lat = dms(g[2]) * (-1 if g.get(1) == 'S' else 1)
    lon = dms(g[4]) * (-1 if g.get(3) == 'W' else 1)
    raw = open(path, 'rb').read(120000).decode('latin-1')
    f = lambda k: (lambda m: float(m.group(1)) if m else None)(re.search(k + r'="([-+\d.]+)"', raw))
    return dict(file=os.path.basename(path), lat=lat, lon=lon, alt=f('RelativeAltitude'), pitch=f('GimbalPitchDegree'),
                yaw=f('GimbalYawDegree'), date=ex.get(36867))


if __name__ == '__main__':
    folder = sys.argv[1]
    mx = int(sys.argv[sys.argv.index('--max') + 1]) if '--max' in sys.argv else 400
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(('.jpg', '.jpeg')))[:mx]
    rows = [r for r in (info(os.path.join(folder, f)) for f in files) if r]
    if not rows:
        sys.exit('Tidak ada foto ber-GPS.')
    lat0 = sorted(r['lat'] for r in rows)[len(rows) // 2]; lon0 = sorted(r['lon'] for r in rows)[len(rows) // 2]
    k = 111320
    for r in rows:
        r['d'] = math.hypot((r['lat'] - lat0) * k, (r['lon'] - lon0) * k * math.cos(math.radians(lat0)))
    nadir = [r for r in rows if r['pitch'] is not None and r['pitch'] < -80]
    high = [r for r in rows if (r['alt'] or 0) > 15]          # sebagian DJI (mis. FC7703) mencatat pitch 0: pakai foto tinggi
    best = min(nadir or high or rows, key=lambda r: r['d'])
    if not nadir:
        print('Catatan: pitch gimbal tidak tercatat (semua ~0) -> dipilih foto ketinggian >15 m terdekat pusat sebaran. CEK fotonya.')
    print(f"{len(rows)} foto ber-GPS, {len(nadir)} nadir. Tanggal foto pertama: {rows[0]['date']}")
    print(f"median sebaran : {lat0:.7f},{lon0:.7f}")
    print(f"foto nadir pusat: {best['file']}  {best['lat']:.7f},{best['lon']:.7f}  alt {best['alt']} m  pitch {best['pitch']}  yaw {best['yaw']}")
    if nadir:
        print(f"--gps={best['lat']:.7f},{best['lon']:.7f}")
    else:
        print('Tanpa foto nadir: untuk terbang orbit, pusat sebaran lebih mewakili rumah. Pastikan di top.png lalu koreksi dengan --center-local.')
        print(f"--gps={lat0:.7f},{lon0:.7f}")
