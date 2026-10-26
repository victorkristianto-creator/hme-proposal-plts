# Generator Penawaran PLTS HME + skill `/buat-proposal-cni`

Membuat penawaran PLTS (template standar v2, PDF) dari **BOQ Excel**, **foto/PDF rincian rekening PLN**, dan **lokasi**,
memakai [Claude Code](https://claude.com/claude-code) dan skill `/buat-proposal-cni`.

## Isi repo

| Folder | Isi |
|---|---|
| `.claude/skills/buat-proposal-cni/` | Skill Claude Code: `SKILL.md` + script `read_boq.py`, `assemble.py`, `check_pdf.py` |
| `proposal-generator/` | `build.py` (hitung + render PDF), template `templates/v2`, aset (logo, foto, font), tabel iradiasi `data/irradiance.json` |
| `proposal-generator/projects/_base/` | Bagian tetap semua penawaran (syarat, garansi, jadwal, lingkup, termin) |
| `proposal-generator/projects/contoh-*` | Contoh input (anonim): `contoh-industri` (dengan tagihan PLN) dan `contoh-rumah` (rumah tinggal, hybrid) |
| `datasheet/` | Datasheet PDF modul, inverter, dan baterai yang dilampirkan ke penawaran |

Aturan template lengkap ada di [`proposal-generator/TEMPLATE.md`](proposal-generator/TEMPLATE.md).

## Persiapan (sekali)

1. Install **Python 3.10+** ([python.org](https://www.python.org/downloads/), centang "Add python.exe to PATH") dan **Google Chrome** atau Microsoft Edge (dipakai untuk mencetak PDF).
2. Install **Claude Code**: <https://claude.com/claude-code> (aplikasi desktop atau CLI).
3. Clone repo ini:
   ```bash
   git clone https://github.com/victorkristianto-creator/hme-proposal-plts.git
   cd hme-proposal-plts
   ```
   (Tanpa Git: tombol **Code → Download ZIP** di GitHub, lalu ekstrak.)
4. Install library Python:
   ```bash
   py -m pip install -r requirements.txt
   ```
   Mac/Linux: `python3 -m pip install -r requirements.txt`.

## Membuat penawaran

1. Buka Claude Code **di folder repo ini** (folder yang berisi `proposal-generator/` dan `.claude/`). Skill hanya terbaca dari folder ini.
2. Ketik:
   ```
   /buat-proposal-cni BOQ-proyek.xlsx
   ```
   lalu lampirkan foto/PDF rincian rekening PLN (1–12 bulan) dan sebut nama klien + lokasi bila belum ada di BOQ.
3. Claude membaca BOQ dan tagihan, mengisi `proposal-generator/projects/<id>/inputs.json`, membangun PDF, memeriksa tiap halaman,
   dan hanya bertanya sekali untuk data yang kurang. Hasil: `proposal-generator/output/<id>-v2.pdf`.

## Mencoba tanpa Claude (cek instalasi)

```bash
py -I -X utf8 .claude/skills/buat-proposal-cni/scripts/assemble.py proposal-generator contoh-industri proposal-generator/projects/contoh-industri/inputs.json
py proposal-generator/build.py projects/contoh-industri
```

PDF muncul di `proposal-generator/output/contoh-industri-v2.pdf`.

## Catatan

- Data klien (`projects/<id>/`) dan hasil PDF (`output/`) **tidak** ikut ter-commit (lihat `.gitignore`). Jangan menghapus aturan itu: repo ini publik.
- Datasheet baru: taruh PDF di `datasheet/<merek>/`. Logo merek baru: `proposal-generator/assets/img/compro/b-<merek>.webp`.
- Kota yang belum ada di `data/irradiance.json`: lihat bagian "Tabel iradiasi" di `TEMPLATE.md`.
