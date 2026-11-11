---
name: buat-proposal-residensial
description: Buat penawaran PLTS rumah tinggal format residensial lengkap — Cover, Ringkasan Eksekutif (hemat, balik modal, IRR, grafik kas 25 tahun), Spesifikasi + layout, Penawaran Harga, Daftar Material, Garansi dan Jadwal, datasheet, Terima Kasih — dari BOQ Excel/gambar atau tabel paket, plus pemakaian listrik rumah dan golongan tarif. Mode penawaran awal (preliminary, tanpa hemat/harga/BOQ) hanya bila diminta. Pakai saat user minta "proposal residensial", "penawaran rumah", "preliminary", "penawaran awal", atau memberi paket hybrid/off-grid untuk rumah.
argument-hint: "<BOQ.xlsx | gambar BOQ/paket> [nama klien] [kota] [nomor] [golongan tarif / kWh atau tagihan per bulan]"
---

# /buat-proposal-residensial — penawaran PLTS rumah tinggal

Varian dari `/buat-proposal-cni` dengan `client.segment = "Residential"`. Generator, template v2, dan script sama;
aturan lengkap di `proposal-generator/TEMPLATE.md` bagian "Versi Residensial" dan "Penawaran Awal (preliminary)".
Script ada di `.claude/skills/buat-proposal-cni/scripts/`. Jalankan Python dengan `py -I -X utf8` (Windows) atau `python3 -I -X utf8` (Mac/Linux).

Tujuan: PDF siap kirim dalam satu kali jalan. Tanya user **sekali**, dalam satu daftar (AskUserQuestion), hanya untuk yang tidak ada di input.

## Dua mode

| Mode | Kapan | Halaman |
|---|---|---|
| **Lengkap (default)** | selalu, kecuali user minta penawaran awal | Cover · Ringkasan Eksekutif + grafik kas 25 tahun · Spesifikasi Sistem · Penawaran Harga · Daftar Material · Garansi dan Jadwal · Datasheet · Terima Kasih (± 12–14 halaman) |
| Penawaran awal | user bilang "preliminary"/"penawaran awal", atau data pemakaian listrik tidak ada sama sekali | Cover · Ringkasan Penawaran Awal · Spesifikasi · Datasheet · Terima Kasih; tanpa hemat, balik modal, harga, BOQ, garansi |

Contoh input: lengkap → `proposal-generator/projects/contoh-rumah/inputs.json`; penawaran awal → `proposal-generator/projects/contoh-rumah-awal/inputs.json`.
Bila ada penawaran sebelumnya untuk klien yang sama di `proposal-generator/projects/`, salin `load`, `financial`, `id.warranty`, dan `id.schedule` dari sana.

## 1. Kumpulkan input

| Butuh | Dari mana |
|---|---|
| Sistem dan harga | BOQ Excel (`read_boq.py`), gambar BOQ, atau tabel paket (pilih nomor paket yang user sebut). Beberapa versi BOQ → pakai yang terbaru / yang namanya cocok dengan pesan user |
| Pemakaian listrik (mode lengkap) | pesan user / penawaran sebelumnya: kWh per bulan dan/atau tagihan Rp per bulan. Tidak ada → tanyakan |
| Golongan tarif | pesan user ("rumah R3"). Tidak ada → tanyakan; default R1 |
| Desain/layout atap | sheet lain di Excel BOQ, atau model 3D HTML dari survei (lihat bawah) |
| Nama klien | pesan user / penawaran sebelumnya; tulis apa adanya, tanpa menambah sapaan |
| Kota | pesan user. Tidak ada → tanyakan; bila tidak dijawab pakai Surabaya dan sebut di laporan (kota menentukan iradiasi) |
| Nomor penawaran | pesan user / catatan BOQ (mis. "no proposal: 210rev1" → nomor 210, revisi "01") → `<no>/Q.PLTS/HME/<bulan romawi>/<tahun>`. Tidak ada → tanyakan, sarankan nomor berikutnya dari `proposal-generator/projects/` |
| Garansi | catatan BOQ (mis. "garansi solax 5th") dan datasheet |
| Ketentuan tambahan | pesan user: franco, tanpa pengiriman, kenaikan harga, dll. |

**Tarif PLN rumah** (`financial.pln_tariff_rp_kwh` = tarif × 1,1 untuk PBJT 10%):
R1 (1.300–2.200 VA) Rp 1.444,70 → Rp 1.589,17 · R2 (3.500–5.500 VA) dan R3 (≥ 6.600 VA) Rp 1.699,53 → Rp 1.869,48.
`tariff_note` mis. "R3 Rp 1.699,53/kWh + PBJT 10%". `escalation_range_pct: [2, 5]`.

**Layout dari Excel.** `read_boq.py` mencantumkan semua `sheets` dan `images`; jalankan dengan `--images proposal-generator/projects/<id>/img` untuk menyimpan gambarnya, lihat dengan Read, lalu pakai sebagai `images.layout`.

**Layout dari model 3D HTML (Three.js)** di folder survei: salin HTML ke scratchpad, sajikan lewat server lokal kecil (`http.server` dengan handler POST yang menyimpan data URL base64 ke PNG), buka di browser pane, lalu lewat `javascript_tool` render `scene` dengan `THREE.PerspectiveCamera` sendiri (2088×1200, `scene.background` putih sementara, `renderer.setSize(...)` → `render` → `toDataURL` → POST, lalu kembalikan ukuran). Ambil sudut tinggi yang memperlihatkan **semua** modul (cek jumlahnya = BOQ). Matikan server dan reset viewport setelahnya.

Rapikan gambar layout: buang tepi putih, lalu beri latar putih sampai rasio 1,74 : 1 (halaman memotong ke rasio ini). Mode lengkap: caption di `id.layout_caption` (mis. "Layout awal 17 modul surya portrait di dua sisi atap rumah Pak X."); mode awal: `images.layout_caption`. Jangan pakai foto contoh bila ada desain.

Tanggal = hari ini (format Indonesia). Revisi "00" (naikkan bila versi sebelumnya sudah dikirim ke klien). Penyusun = `git config user.name`.

## 2. Hitung harga

- `price.total` = harga **sebelum PPN**, `vat_pct` 11. Template menghitung PPN dan total; cocokkan dengan total + PPN di BOQ bila ada.
- Tabel paket sering punya dua kolom harga: kolom kedua = kolom pertama × 1,11 berarti sudah termasuk PPN → pakai kolom pertama.
- "Exclude pengiriman" / franco: kurangi baris Delivery/Logistik dari total dan sebutkan franco di catatan (mode awal: `id.prelim_notes`).
- Kenaikan/diskon persen ("up 30%"): kalikan harga sebelum PPN, bulatkan ke rupiah. Sebut harga lama → baru di laporan.
- Ongkos kirim/asuransi ke lokasi: `price.breakdown: [["Material dan instalasi", <rp>, ""], ["Ongkos kirim & asuransi", <rp>, "Surabaya – <Kota>"]]`, `price.total` = jumlahnya.
- `system.inverter.price_rp` dan `system.battery_price_rp` dari BOQ (dipakai untuk penggantian di proyeksi 25 tahun).
- Termin: `id.payment_terms` (4 baris `[langkah, "40%", kapan]`, jumlah 100%). Tanpa isian → termin standar _base (30/45/20/5).

## 3. Tulis inputs.json

`proposal-generator/projects/<id>/inputs.json` (id = `<nomor>-<nama-singkat>[-hybrid|-offgrid]`, huruf kecil; revisi → mis. `210rev1-…`). Folder proyek tidak masuk git (repo publik). Jangan menimpa folder proyek versi sebelumnya.

```json
{"meta": {"number": "...", "date": "...", "date_id": "...", "revision": "00", "prepared_by": "...", "language": "id"},
 "client": {"name": "...", "short_name": "...", "location": "Surabaya, East Java", "location_id": "Surabaya, Jawa Timur", "city": "Surabaya", "segment": "Residential"},
 "system": {"type": "Hybrid | Off-Grid | On-Grid", "mounting": "Rooftop", "capacity_kwp": 12.24, "capacity_kva": 8,
            "panel": {"qty": 17, "brand": "Trina Solar", "model": "Vertex N TSM-NEG21C.20", "wp": 720},
            "inverter": {"qty": 1, "brand": "SolaX", "model": "X1-RENO-8.0-LV", "kva": 8, "price_rp": 0},
            "battery": "2 × SolaX LD16S (32 kWh)", "battery_kwh": 32, "battery_price_rp": 0, "monitoring": "..."},
 "price": {"total": 0, "vat_pct": 11, "items": ["EPC PLTS hybrid rumah tinggal", "Material PLTS hybrid + baterai", "Jasa instalasi"]},
 "load": {"monthly_kwh": 0, "monthly_bill_rp": 0, "work_hours": [0, 24], "saturday": true, "sunday": true, "base_load_pct": 100},
 "financial": {"pln_tariff_rp_kwh": 1869.48, "tariff_note": "R3 Rp 1.699,53/kWh + PBJT 10%", "escalation_range_pct": [2, 5]},
 "images": {"layout": "img/layout.png"},
 "datasheets": [{"title": "Inverter Hybrid · ...", "file": "../../../datasheet/..."}],
 "id": {"layout_caption": "...", "price_items": ["...sama dengan price.items"],
        "warranty": [{"item": "Modul surya", "value": "12 tahun", "detail": "..."}, {"item": "Inverter", ...}, {"item": "Baterai", ...}, {"item": "Instalasi", "value": "12 bulan", "detail": "..."}],
        "schedule": [{"task": "Perjanjian & uang muka", "start": 0, "end": 0.25}, {"task": "Pengadaan material", "start": 0.25, "end": 1.5},
                     {"task": "Instalasi", "start": 1.5, "end": 2.25}, {"task": "Pengujian & serah terima", "start": 2.25, "end": 2.5}]},
 "boq": [{"section": "...", "items": [["Item", 1, "pcs", "Spesifikasi"]]}]}
```

- `load`: isi `monthly_kwh` bila ada; hanya tagihan Rp → isi `monthly_bill_rp` dan biarkan template menghitung kWh. Rumah = `work_hours [0, 24]`, sabtu/minggu true, `base_load_pct` 100.
- `capacity_kwp` = qty modul × Wp / 1000; `capacity_kva` = daya inverter.
- `type`: dari BOQ/judul paket ("Offgrid ATS" → `Off-Grid`; inverter hybrid → `Hybrid`).
- Merek modul tulis seperti di BOQ; `model` = seri/varian dari BOQ.
- Model inverter tidak lengkap di BOQ: cocokkan dengan datasheet yang ada dan sebut di laporan sebagai perlu dikonfirmasi.
- Garansi: modul 12 tahun + daya linier (lihat datasheet), inverter/baterai sesuai catatan BOQ atau pabrikan, instalasi 12 bulan.
- `boq`: gaya `/buat-proposal-cni`, tanpa harga, bagian dan spesifikasi dari BOQ (Bahasa Indonesia, satuan mm²).
- Datasheet: cari di `datasheet/<merek>/` (juga subfolder, mis. `solax/x1`, `solax/x3`, `solax/off grid`, `solax/batrai`), cek model di isi PDF (PyMuPDF).
  Lampirkan modul (hanya bila tipe sama persis), inverter, dan baterai. Datasheet baru dari user → salin ke subfolder merek yang sesuai. Tidak ketemu → lewati dan sebut di laporan.
- Mode penawaran awal: tambah `meta.preliminary: true`, ganti `id` dengan `included`, `next_steps`, `prelim_notes`; tanpa `load` dan `financial`.

## 4. Rakit, build, cek

```
py -I -X utf8 .claude/skills/buat-proposal-cni/scripts/assemble.py proposal-generator <id> proposal-generator/projects/<id>/inputs.json
py proposal-generator/build.py projects/<id>
```

Selesaikan peringatan (`! …`). Lalu **lihat** PNG di `proposal-generator/output/.check/<id>/` dengan Read, minimal halaman 1–4 dan Garansi:
- urutan halaman sesuai mode, tidak ada `OVERFLOW?`, tidak ada "None"/"{kota}";
- cover: nama klien, kota, kapasitas, modul/inverter/baterai;
- Ringkasan Eksekutif: tagihan sebelum → sesudah masuk akal, hemat/bulan, balik modal, IRR, produksi per bulan;
- Penawaran Harga: sebelum PPN, PPN 11%, total = BOQ;
- Spesifikasi: layout memperlihatkan semua modul;
- Garansi: sesuai catatan BOQ. Halaman ini memuat baris "SLO dan NIDI diurus HME" dari _base — biarkan kecuali user minta dihapus.

## 5. Lapor

Singkat, Bahasa Indonesia: path PDF (dan kirim file ke user), sistem, harga sebelum PPN / PPN / total, hemat tahun pertama (± per bulan),
tagihan sebelum → sesudah, balik modal, IRR, produksi (kWh/tahun dan ± kWh/bulan), yang diasumsikan (kota, tarif, pemakaian, model inverter, nomor),
dan datasheet yang tidak ketemu. Beberapa paket untuk klien yang sama → satu proyek per paket, nomor berurutan, laporkan dalam satu tabel.
