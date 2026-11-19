"""Rakit satu file HTML offline PLTS Roof Designer.

  python build_html.py --site site.json [--glb model_local.glb] --out desain_plts_<nama>.html

site.json  : data rumah (lihat SKILL.md, bagian "Format site.json")
model GLB  : sudah dalam frame lokal three.js (X = timur, Y = atas, Z = selatan, meter) dan TANPA Draco
             -> hasil prepare_model.py.  Tanpa --glb: aplikasi kosong, pengguna bisa seret .glb sendiri.
"""
import argparse, base64, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, '..', 'assets')


def build(site, glb_bytes, out):
    rd = lambda n: open(os.path.join(ASSETS, n), encoding='utf-8').read()
    shell, libs, app = rd('shell.html'), rd('three-r128-libs.js'), rd('app.js')
    site_js = json.dumps(site, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    glb = base64.b64encode(glb_bytes).decode('ascii') if glb_bytes else '@@GLB@@'
    title = site.get('name', 'PLTS Roof Designer') + ' · PLTS Roof Designer'
    # urutan penting: placeholder kecil dulu, lalu blok besar (dipotong sekali, tidak di-replace global)
    html = shell.replace('@@TITLE@@', title.replace('<', '&lt;'))
    for key, val in (('@@SITE@@', site_js), ('@@GLB@@', glb), ('@@LIBS@@', libs), ('@@APP@@', app)):
        i = html.index(key)
        html = html[:i] + val + html[i + len(key):]
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    return len(html)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--site', help='site.json (boleh kosong: template)')
    ap.add_argument('--glb')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    site = json.load(open(a.site, encoding='utf-8')) if a.site else {}
    glb = open(a.glb, 'rb').read() if a.glb else None
    n = build(site, glb, a.out)
    print(f'{a.out}: {n / 1e6:.1f} MB')
