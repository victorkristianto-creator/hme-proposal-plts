"""Akses WebODM lokal (default http://127.0.0.1:29800).

  python webodm.py tasks                                   # daftar proyek & task (nama, uuid, tanggal, status)
  python webodm.py download <project> <task> <out_dir>     # textured_model.glb (+ orthophoto.tif bila ada)
  python webodm.py ortho <project> <task> <lat,lon> <out.png> [--zoom 21] [--tiles 3] [--zoom-offset 0]
        # mosaik tile orthophoto di sekitar titik (untuk CEK IDENTITAS rumah)

Autentikasi: bila WebODM meminta login, set variabel lingkungan WEBODM_TOKEN (JWT), atau WEBODM_USER + WEBODM_PASS
(diisi oleh pengguna sendiri; skrip hanya meneruskannya ke WebODM lokal).
"""
import io, json, math, os, sys, urllib.request, urllib.parse

BASE = os.environ.get('WEBODM_URL', 'http://127.0.0.1:29800').rstrip('/')
_TOKEN = None


def token():
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = os.environ.get('WEBODM_TOKEN', '')
        if not _TOKEN and os.environ.get('WEBODM_USER'):
            data = urllib.parse.urlencode({'username': os.environ['WEBODM_USER'], 'password': os.environ.get('WEBODM_PASS', '')}).encode()
            with urllib.request.urlopen(urllib.request.Request(BASE + '/api/token-auth/', data=data), timeout=30) as r:
                _TOKEN = json.load(r)['token']
    return _TOKEN


def get(path, raw=False, timeout=120):
    req = urllib.request.Request(BASE + path)
    if token():
        req.add_header('Authorization', 'JWT ' + token())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read() if raw else json.load(r)


def deg2tile(lat, lon, z):
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'tasks'
    try:
        if cmd == 'tasks':
            for p in get('/api/projects/'):
                print(f"proyek {p['id']}: {p['name']}")
                for t in get(f"/api/projects/{p['id']}/tasks/"):
                    st = {10: 'antri', 20: 'proses', 30: 'gagal', 40: 'selesai', 50: 'batal'}.get(t.get('status'), t.get('status'))
                    print(f"   {t['id']}  {t.get('name')!r}  {t.get('created_at', '')[:10]}  {st}  foto={t.get('images_count')}")
        elif cmd == 'download':
            p, t, out = sys.argv[2:5]
            os.makedirs(out, exist_ok=True)
            for asset in ('textured_model.glb', 'orthophoto.tif'):
                try:
                    b = get(f'/api/projects/{p}/tasks/{t}/download/{asset}', raw=True, timeout=600)
                    open(os.path.join(out, asset), 'wb').write(b)
                    print(f'{asset}: {len(b) / 1e6:.1f} MB')
                except Exception as e:
                    print(f'{asset}: tidak tersedia ({e})')
        elif cmd == 'ortho':
            p, t, ll, out = sys.argv[2:6]
            args = sys.argv[6:]
            opt = lambda k, d: type(d)(args[args.index(k) + 1]) if k in args else d
            z, k, zo = opt('--zoom', 21), opt('--tiles', 3), opt('--zoom-offset', 0)
            lat, lon = map(float, ll.split(','))
            from PIL import Image, ImageDraw
            fx, fy = deg2tile(lat, lon, z)
            x0, y0 = int(fx) - k // 2, int(fy) - k // 2
            S = 256
            im = Image.new('RGB', (k * S, k * S), (40, 40, 40))
            for i in range(k):
                for j in range(k):
                    try:
                        b = get(f'/api/projects/{p}/tasks/{t}/orthophoto/tiles/{z + zo}/{x0 + i}/{y0 + j}.png', raw=True)
                        im.paste(Image.open(io.BytesIO(b)).convert('RGB').resize((S, S)), (i * S, j * S))
                    except Exception:
                        pass
            d = ImageDraw.Draw(im); cx, cy = (fx - x0) * S, (fy - y0) * S
            d.line([cx - 15, cy, cx + 15, cy], fill=(255, 255, 0), width=2); d.line([cx, cy - 15, cx, cy + 15], fill=(255, 255, 0), width=2)
            im.save(out)
            mpp = 156543.03 * math.cos(math.radians(lat)) / 2 ** z
            print(f'{out}: {k * S}px, ±{mpp * 100:.1f} cm/px, tanda kuning = titik GPS')
        else:
            print(__doc__)
    except urllib.error.URLError as e:
        sys.exit(f'WebODM tidak bisa dihubungi di {BASE} ({e}). Nyalakan WebODM dulu, atau pakai file .glb yang sudah diunduh.')
