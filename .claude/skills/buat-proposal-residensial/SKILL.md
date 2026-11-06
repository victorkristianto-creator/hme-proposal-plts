---
name: buat-proposal-residensial
description: Buat penawaran awal (preliminary) PLTS rumah tinggal yang ringkas — Cover, Ringkasan Penawaran Awal (sistem, perkiraan produksi, harga + PPN, termasuk, langkah berikutnya), Spesifikasi, datasheet, Terima Kasih — tanpa hemat, balik modal, halaman harga, BOQ, dan garansi. Input BOQ Excel/gambar atau tabel paket harga. Pakai saat user minta "proposal residensial", "penawaran rumah", "preliminary", "penawaran awal", atau memberi paket hybrid/off-grid untuk rumah.
argument-hint: "<BOQ.xlsx | gambar BOQ/paket> [nama klien] [kota] [nomor]"
---

# /buat-proposal-residensial — penawaran awal PLTS rumah tinggal

Varian dari `/buat-proposal-cni` dengan `client.segment = "Residential"` dan `meta.preliminary = true`. Generator, template v2, dan script sama;
aturan lengkap di `proposal-generator/TEMPLATE.md` bagian "Penawaran Awal (preliminary)". Script ada di `.claude/skills/buat-proposal-cni/scripts/`.
Jalankan Python dengan `py -I -X utf8` (Windows) atau `python3 -I -X utf8` (Mac/Linux). Contoh input: `proposal-generator/projects/contoh-rumah-awal/inputs.json`.

Tujuan: PDF siap kirim dalam satu kali jalan. Tanya user **sekali**, dalam satu daftar (AskUserQuestion), hanya untuk yang tidak ada di input.

Hasil (6–8 halaman): Cover · Ringkasan Penawaran Awal · Spesifikasi Sistem · Lampiran Datasheet · Terima Kasih.
Tidak ada: profil perusahaan, angka hemat, balik modal, halaman Penawaran Harga, BOQ, Garansi dan Jadwal.

## 1. Kumpulkan input

| Butuh | Dari mana |
|---|---|
| Sistem dan harga | BOQ Excel (`read_boq.py`), gambar BOQ, atau tabel paket (pilih nomor paket yang user sebut) |
| Nama klien | pesan user; tulis apa adanya, tanpa menambah sapaan (Bapak/Ibu) |
| Kota | pesan user. Tidak ada → tanyakan; bila user tidak menjawab, pakai Surabaya dan sebut di laporan (kota menentukan iradiasi) |
| Nomor penawaran | pesan user / catatan BOQ → `<no>/Q.PLTS/HME/<bulan romawi>/<tahun>`. Tidak ada → tanyakan, sarankan nomor berikutnya dari `proposal-generator/projects/` |
| Ketentuan tambahan | pesan user: franco, tanpa pengiriman, kenaikan harga, dll. |

Tanggal = hari ini (format Indonesia). Revisi "00" (naikkan bila versi sebelumnya sudah dikirim ke klien). Penyusun = `git config user.name`.

## 2. Hitung harga

- `price.total` = harga **sebelum PPN**, `vat_pct` 11. Template menghitung PPN dan total.
- Tabel paket sering punya dua kolom harga: cek rasionya. Kolom kedua = kolom pertama × 1,11 berarti sudah termasuk PPN → pakai jumlah kolom pertama.
  Cocokkan: `price.total` × 1,11 ≈ harga paket di tabel (selisih pembulatan < Rp 1).
- "Exclude pengiriman" / franco: kurangi baris Delivery/Logistik dari total, hapus "pengiriman" dari `id.included`, dan tambahkan
  `id.prelim_notes: ["Harga franco <Kota>, belum termasuk biaya pengiriman ke lokasi."]`.
- Kenaikan/diskon persen ("up 30%"): kalikan harga sebelum PPN, bulatkan ke rupiah. Sebut harga lama → baru di laporan.

## 3. Tulis inputs.json

`proposal-generator/projects/<id>/inputs.json` (id = `<nomor>-<nama-singkat>[-hybrid|-offgrid]`, huruf kecil). Folder proyek tidak masuk git (repo publik).

```json
{"meta": {"number": "...", "date": "...", "date_id": "...", "revision": "00", "prepared_by": "...", "language": "id", "preliminary": true},
 "client": {"name": "...", "short_name": "...", "location_id": "Kota, Provinsi", "city": "Kota", "segment": "Residential"},
 "system": {"type": "Hybrid | Off-Grid | On-Grid", "mounting": "Rooftop", "capacity_kwp": 2.36, "capacity_kva": 3,
            "panel": {"qty": 4, "brand": "...", "model": "...", "wp": 590},
            "inverter": {"qty": 1, "brand": "SolaX", "model": "...", "kva": 3, "price_rp": 0},
            "battery": "SolaX 5 kWh", "battery_kwh": 5, "battery_price_rp": 0, "monitoring": "..."},
 "price": {"total": 0, "vat_pct": 11},
 "datasheets": [{"title": "Inverter Hybrid · ...", "file": "../../../datasheet/..."}],
 "id": {"included": ["5–7 baris ringkas dari BOQ"], "next_steps": ["Survei ...", "Penawaran final ...", "Instalasi ..."], "prelim_notes": []},
 "boq": [{"section": "...", "items": [["Item", 1, "pcs", "Spesifikasi"]]}]}
```

- `capacity_kwp` = qty modul × Wp / 1000; `capacity_kva` = daya inverter.
- `type`: dari BOQ/judul paket ("Offgrid ATS" → `Off-Grid`; inverter hybrid → `Hybrid`). Untuk Off-Grid, kalimat pembuka otomatis menyebut ATS ke PLN.
- Merek modul tulis seperti di BOQ (jangan diganti nama lain); `model` = seri/varian dari BOQ (mis. "Vertex N NEG19RC.20 bifacial").
- Model inverter tidak lengkap di BOQ: cocokkan dengan datasheet yang ada (mis. "Hybrid single phase 3 kW SolaX" + baterai LV → X1-HYB-3.0-LV) dan sebut di laporan sebagai perlu dikonfirmasi.
- `id.included`: material utama + mounting + kabel + panel/proteksi/ATS + monitoring + jasa (± hari dari BOQ). Tanpa harga.
- `boq`: tetap isi (gaya `/buat-proposal-cni`, tanpa harga) — tidak tampil di penawaran awal, tapi dipakai untuk penawaran final.
- Datasheet: cari di `datasheet/<merek>/` (juga subfolder, mis. `solax/x1`, `solax/off grid`, `solax/batrai`), cek model di isi PDF.
  Lampirkan inverter dan baterai; modul hanya bila tipe di PDF sama persis (NE19R ≠ NEG19RC.20). Tidak ketemu → lewati dan sebut di laporan.
- Tanpa `load`, `pln_bills`, dan `financial` (tidak ada hitung hemat).

## 4. Rakit, build, cek

```
py -I -X utf8 .claude/skills/buat-proposal-cni/scripts/assemble.py proposal-generator <id> proposal-generator/projects/<id>/inputs.json
py proposal-generator/build.py projects/<id>
```

Peringatan "tanpa gambar layout" normal (Spesifikasi memakai foto contoh rumah). Selesaikan peringatan lain (`! …`).
Lalu **lihat** PNG di `proposal-generator/output/.check/<id>-v2/` dengan Read, minimal halaman 1–3:
- urutan halaman sesuai di atas, tidak ada `OVERFLOW?`, tidak ada "None"/"{kota}";
- cover: nama klien, kota, kapasitas, modul/inverter/baterai, "Nilai penawaran awal Rp … (belum PPN)";
- ringkasan: harga sebelum PPN, PPN 11%, total = yang dihitung di langkah 2; catatan franco muncul bila ada;
- tidak ada angka hemat, balik modal, atau tagihan.

## 5. Lapor

Singkat, Bahasa Indonesia: path PDF (dan kirim file ke user), sistem, harga sebelum PPN / PPN / total, perkiraan produksi,
yang diasumsikan (kota, model inverter, nomor), dan datasheet yang tidak ketemu.
Beberapa paket untuk klien yang sama → satu proyek per paket, nomor berurutan, laporkan dalam satu tabel.
