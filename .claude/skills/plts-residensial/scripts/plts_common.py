"""Fungsi bersama: UTM <-> lat/lon (WGS84), baca/tulis GLB (dengan Draco via DracoPy), GPS EXIF."""
import json, math, struct

A, F = 6378137.0, 1 / 298.257223563
E2 = F * (2 - F)
K0 = 0.9996


def utm_to_latlon(e, n, zone, south=True):
    ep2 = E2 / (1 - E2)
    x, y = e - 500000.0, n - (10000000.0 if south else 0.0)
    m = y / K0
    mu = m / (A * (1 - E2 / 4 - 3 * E2 ** 2 / 64 - 5 * E2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - E2)) / (1 + math.sqrt(1 - E2))
    p = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
         + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    c1, t1 = ep2 * math.cos(p) ** 2, math.tan(p) ** 2
    n1 = A / math.sqrt(1 - E2 * math.sin(p) ** 2)
    r1 = A * (1 - E2) / (1 - E2 * math.sin(p) ** 2) ** 1.5
    d = x / (n1 * K0)
    lat = p - (n1 * math.tan(p) / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
                                         + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = (d - (1 + 2 * t1 + c1) * d ** 3 / 6 + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120) / math.cos(p)
    return math.degrees(lat), (zone - 1) * 6 - 180 + 3 + math.degrees(lon)


def latlon_to_utm(lat, lon):
    zone = int((lon + 180) / 6) + 1
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    la, lo = math.radians(lat), math.radians(lon)
    ep2 = E2 / (1 - E2)
    n = A / math.sqrt(1 - E2 * math.sin(la) ** 2)
    t, c = math.tan(la) ** 2, ep2 * math.cos(la) ** 2
    a = math.cos(la) * (lo - lon0)
    m = A * ((1 - E2 / 4 - 3 * E2 ** 2 / 64 - 5 * E2 ** 3 / 256) * la - (3 * E2 / 8 + 3 * E2 ** 2 / 32 + 45 * E2 ** 3 / 1024) * math.sin(2 * la)
             + (15 * E2 ** 2 / 256 + 45 * E2 ** 3 / 1024) * math.sin(4 * la) - (35 * E2 ** 3 / 3072) * math.sin(6 * la))
    e = K0 * n * (a + (1 - t + c) * a ** 3 / 6 + (5 - 18 * t + t ** 2 + 72 * c - 58 * ep2) * a ** 5 / 120) + 500000
    y = K0 * (m + n * math.tan(la) * (a ** 2 / 2 + (5 - t + 9 * c + 4 * c ** 2) * a ** 4 / 24 + (61 - 58 * t + t ** 2 + 600 * c - 330 * ep2) * a ** 6 / 720))
    if lat < 0:
        y += 10000000
    return e, y, zone, lat < 0


def meridian_convergence_deg(lat, lon):
    """Sudut grid-utara UTM terhadap utara-sejati (derajat, + = grid berputar searah jarum jam)."""
    zone = int((lon + 180) / 6) + 1
    lon0 = (zone - 1) * 6 - 180 + 3
    return math.degrees(math.atan(math.tan(math.radians(lon - lon0)) * math.sin(math.radians(lat))))


# ---------------- GLB ----------------
COMP = {5120: 'b', 5121: 'B', 5122: 'h', 5123: 'H', 5125: 'I', 5126: 'f'}
NCOMP = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}


def read_glb(path):
    """-> list of primitives: dict(pos=Nx3, uv=Nx2|None, idx=Mx3, image=bytes|None, mime) ; koordinat apa adanya (glTF Y-atas),
    transform node (matrix/translation/rotation/scale) sudah diterapkan."""
    import numpy as np
    b = open(path, 'rb').read()
    assert b[:4] == b'glTF'
    jl = struct.unpack('<I', b[12:16])[0]
    j = json.loads(b[20:20 + jl])
    off = 20 + jl
    bin_ = b''
    if off < len(b):
        bl = struct.unpack('<I', b[off:off + 4])[0]
        bin_ = b[off + 8: off + 8 + bl]

    def view(i):
        v = j['bufferViews'][i]
        return bin_[v.get('byteOffset', 0): v.get('byteOffset', 0) + v['byteLength']], v.get('byteStride')

    def acc(i):
        a = j['accessors'][i]
        raw, stride = view(a['bufferView'])
        nc, ct = NCOMP[a['type']], COMP[a['componentType']]
        dt = np.dtype('<' + ct)
        start = a.get('byteOffset', 0)
        if stride and stride != nc * dt.itemsize:
            arr = np.ndarray((a['count'], nc), dtype=dt, buffer=raw, offset=start, strides=(stride, dt.itemsize))
        else:
            arr = np.frombuffer(raw, dtype=dt, count=a['count'] * nc, offset=start).reshape(a['count'], nc)
        return arr.copy()

    def node_matrix(n):
        if 'matrix' in n:
            return np.array(n['matrix'], dtype=float).reshape(4, 4).T
        M = np.eye(4)
        s = n.get('scale', [1, 1, 1]); x, y, z, w = n.get('rotation', [0, 0, 0, 1]); t = n.get('translation', [0, 0, 0])
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        M[:3, :3] = R * np.array(s)
        M[:3, 3] = t
        return M

    images = []
    for im in j.get('images', []):
        raw, _ = view(im['bufferView'])
        images.append((raw, im.get('mimeType', 'image/jpeg')))

    prims = []

    def walk(ni, parent):
        n = j['nodes'][ni]
        M = parent @ node_matrix(n)
        if 'mesh' in n:
            for pr in j['meshes'][n['mesh']]['primitives']:
                ext = pr.get('extensions', {}).get('KHR_draco_mesh_compression')
                if ext:
                    import DracoPy
                    raw, _ = view(ext['bufferView'])
                    dm = DracoPy.decode(raw)
                    pos = np.asarray(dm.points, dtype=np.float64).reshape(-1, 3)
                    idx = np.asarray(dm.faces, dtype=np.uint32).reshape(-1, 3)
                    uv = None
                    tid = ext['attributes'].get('TEXCOORD_0')
                    if tid is not None:
                        uv = np.asarray(dm.tex_coord, dtype=np.float32).reshape(-1, 2) if dm.tex_coord is not None and len(dm.tex_coord) else None
                else:
                    pos = acc(pr['attributes']['POSITION']).astype(np.float64)
                    uv = acc(pr['attributes']['TEXCOORD_0']).astype(np.float32) if 'TEXCOORD_0' in pr['attributes'] else None
                    idx = acc(pr['indices']).astype(np.uint32).reshape(-1, 3) if 'indices' in pr else np.arange(len(pos), dtype=np.uint32).reshape(-1, 3)
                pos = (np.c_[pos, np.ones(len(pos))] @ M.T)[:, :3]
                img = None
                if 'material' in pr:
                    mat = j['materials'][pr['material']]
                    t = mat.get('pbrMetallicRoughness', {}).get('baseColorTexture')
                    if t is not None:
                        img = images[j['textures'][t['index']]['source']]
                prims.append(dict(pos=pos, uv=uv, idx=idx, image=img[0] if img else None, mime=img[1] if img else None))
        for c in n.get('children', []):
            walk(c, M)

    sc = j.get('scenes', [{'nodes': list(range(len(j['nodes'])))}])[j.get('scene', 0)]
    for ni in sc['nodes']:
        walk(ni, np.eye(4))
    return prims, j


def write_glb(path, prims, generator='plts-residensial'):
    """prims: list of dict(pos float32 Nx3, uv float32 Nx2|None, idx uint32 Mx3, image bytes|None, mime). Material unlit bertekstur."""
    import numpy as np
    bin_ = bytearray()
    j = {'asset': {'version': '2.0', 'generator': generator}, 'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0}],
         'meshes': [{'primitives': []}], 'accessors': [], 'bufferViews': [], 'materials': [], 'textures': [], 'images': [],
         'samplers': [{'magFilter': 9729, 'minFilter': 9987, 'wrapS': 33071, 'wrapT': 33071}], 'extensionsUsed': ['KHR_materials_unlit']}

    def add_view(data, target=None):
        while len(bin_) % 4:
            bin_.append(0)
        v = {'buffer': 0, 'byteOffset': len(bin_), 'byteLength': len(data)}
        if target:
            v['target'] = target
        bin_.extend(data)
        j['bufferViews'].append(v)
        return len(j['bufferViews']) - 1

    def add_acc(arr, ctype, typ, target, minmax=False):
        a = {'bufferView': add_view(arr.tobytes(), target), 'componentType': ctype, 'count': int(arr.shape[0]), 'type': typ}
        if minmax:
            a['min'] = arr.min(0).tolist(); a['max'] = arr.max(0).tolist()
        j['accessors'].append(a)
        return len(j['accessors']) - 1

    for p in prims:
        if len(p['idx']) == 0:
            continue
        pos = np.ascontiguousarray(p['pos'], dtype=np.float32)
        attrs = {'POSITION': add_acc(pos, 5126, 'VEC3', 34962, True)}
        if p.get('uv') is not None:
            attrs['TEXCOORD_0'] = add_acc(np.ascontiguousarray(p['uv'], dtype=np.float32), 5126, 'VEC2', 34962)
        idx = np.ascontiguousarray(p['idx'], dtype=np.uint32).reshape(-1)
        prim = {'attributes': attrs, 'indices': add_acc(idx.reshape(-1, 1), 5125, 'SCALAR', 34963)}
        mat = {'pbrMetallicRoughness': {'metallicFactor': 0, 'roughnessFactor': 1}, 'extensions': {'KHR_materials_unlit': {}}, 'doubleSided': True}
        if p.get('color') is not None:
            mat['pbrMetallicRoughness']['baseColorFactor'] = list(p['color']) + [1]
        if p.get('image') is not None and p.get('uv') is not None:
            j['images'].append({'bufferView': add_view(p['image']), 'mimeType': p.get('mime') or 'image/jpeg'})
            j['textures'].append({'source': len(j['images']) - 1, 'sampler': 0})
            mat['pbrMetallicRoughness']['baseColorTexture'] = {'index': len(j['textures']) - 1}
        j['materials'].append(mat)
        prim['material'] = len(j['materials']) - 1
        j['meshes'][0]['primitives'].append(prim)
    while len(bin_) % 4:
        bin_.append(0)
    j['buffers'] = [{'byteLength': len(bin_)}]
    for k in ('textures', 'images'):
        if not j[k]:
            del j[k]
    js = json.dumps(j, separators=(',', ':')).encode()
    js += b' ' * ((4 - len(js) % 4) % 4)
    out = b'glTF' + struct.pack('<II', 2, 12 + 8 + len(js) + 8 + len(bin_))
    out += struct.pack('<I', len(js)) + b'JSON' + js + struct.pack('<I', len(bin_)) + b'BIN\x00' + bytes(bin_)
    open(path, 'wb').write(out)
    return len(out)


# ---------------- EXIF GPS ----------------
def exif_gps(path):
    """-> (lat, lon, alt_rel|None, gimbal_yaw|None) dari foto DJI."""
    from PIL import Image
    im = Image.open(path)
    ex = im._getexif() or {}
    g = ex.get(34853)
    if not g:
        return None
    dms = lambda v: float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
    lat = dms(g[2]) * (-1 if g.get(1) == 'S' else 1)
    lon = dms(g[4]) * (-1 if g.get(3) == 'W' else 1)
    yaw = alt = None
    try:
        raw = open(path, 'rb').read(200000).decode('latin-1')
        import re
        m = re.search(r'GimbalYawDegree="([-\d.]+)"', raw); yaw = float(m.group(1)) if m else None
        m = re.search(r'RelativeAltitude="([-+\d.]+)"', raw); alt = float(m.group(1)) if m else None
    except Exception:
        pass
    return lat, lon, alt, yaw
