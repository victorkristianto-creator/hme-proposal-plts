---
name: buat-proposal-cni
description: Buat penawaran PLTS HME (template standar v2) dari BOQ Excel, foto/scan rincian rekening PLN, dan lokasi. Mengisi project.json, build PDF, cek tiap halaman, dan hanya bertanya hal yang benar-benar kurang. Pakai saat user minta "buat proposal", "buat penawaran", atau memberi BOQ/tagihan PLN untuk dijadikan proposal.
argument-hint: "<BOQ.xlsx> [foto tagihan PLN...] [lokasi / nama klien]"
---

# /buat-proposal-cni — penawaran PLTS dari BOQ + tagihan PLN

Semua path relatif ke root repo (folder tempat Claude Code dibuka, berisi `proposal-generator/`, `datasheet/`, `.claude/`). Generator: `proposal-generator` (disebut `GEN`). Template standar: **v2**, aturan lengkap di `GEN/TEMPLATE.md` — baca dulu bila belum di konteks. Script skill ini ada di `.claude/skills/buat-proposal-cni/scripts/` (disebut `SKILL`). Jalankan Python dengan `py -I -X utf8` (Windows) atau `python3 -I -X utf8` (Mac/Linux).

Tujuan: PDF siap kirim dalam satu kali jalan. Kerjakan sendiri semua yang bisa dibaca dari file; tanya user **sekali**, dalam satu daftar, hanya untuk yang tidak ada di input.

## 1. Kumpulkan input

| Butuh | Dari mana |
|---|---|
| BOQ Excel | argumen / file yang user sebut. Wajib. |
| Rincian rekening PLN (1–12 bulan) | foto/PDF yang user lampirkan. Sangat dianjurkan. |
| Nama klien, lokasi (kota, provinsi), segmen | pesan user, judul BOQ, baris "Delivery" di BOQ, nama sesuai NPWP di rekening |
| Nomor penawaran | catatan BOQ ("Nomor proposal 138") → `138/Q.PLTS/HME/<bulan romawi>/<tahun>` |
| Gambar layout atap | file gambar yang user beri (opsional) |

Tanggal = hari ini (format Indonesia, mis. "22 Oktober 2026"). Revisi "00". Penyusun = nama user (dari git config `user.name` atau tanyakan sekali, sekalian dengan pertanyaan lain).

## 2. Baca BOQ

```
py -I -X utf8 .claude/skills/buat-proposal-cni/scripts/read_boq.py "<BOQ.xlsx>"
```

Isi `system`:
- `capacity_kwp` = `wp`/1000 (atau dari judul "387 kWp / 300 kVA"); `capacity_kva` = `kva`.
- `panel`: dari item "Modul Solar" (bukan "Spare"): qty, brand, model, wp. Normalkan merek: "JA"/"JA Solar" → `JA Solar`, "TRINA"/"Trina Solar" → `Trina Solar`, "SUNGROW" → `Sungrow`, "Huawei", "Jinko Solar", "LONGi".
- `inverter`: item "Inverter On-Grid". Kalau ada beberapa model, isi `mix`: `[{"qty": 4, "model": "X3-FTH-150K-P", "kva": 150}, {"qty": 3, "model": "X3-FTH-125K", "kva": 125}]`; `qty` = total unit, `model`/`kva` = yang terbesar. Template menampilkan "4 × 150 + 3 × 125 kVA".
- `monitoring`: dari seksi monitoring (mis. "Sungrow 100D logger + weather station").
- `type` "On-Grid" (atau dari judul), `mounting` "Rooftop" kecuali BOQ bilang lain.

`price.total` = `total` dari BOQ (sebelum PPN), `vat_pct` 11.

Harga peralatan yang diganti (dipakai untuk biaya penggantian di arus kas, bukan tampil di proposal):
- `system.inverter.price_rp` = jumlah kolom `total` baris inverter dari `read_boq` (baris "Inverter On-Grid/On Grid/Hybrid", semua model bila campuran; **bukan** breaker, busbar, atau shelter inverter).
- `system.battery_price_rp` = `total` baris "Baterai" bila sistem hybrid (isi juga `system.battery_kwh`).
- Kosong → `build.py`/`assemble.py` memberi peringatan dan memakai 10% (inverter) / 25% (baterai) dari investasi. Jangan biarkan kosong di penawaran yang dikirim.

`boq` (yang tampil di proposal) = daftar seksi `{"section", "items": [[nama, qty, satuan, spesifikasi], ...]}`:
- **tanpa harga**, Bahasa Indonesia, gaya seperti `GEN/projects/contoh-industri/inputs.json` → `boq` (contoh: "Main Material" → "Material Utama", "Mounting and Penetration" → "Mounting & Penetrasi Atap", "Cable AC …" → "Kabel AC …", "Box DC/AC Combiner" → "Panel Combiner DC/AC", "Safety Equipment" → "Peralatan Keselamatan"; Services + Delivery + Permission digabung jadi "Jasa, Pengiriman & Perizinan").
- Gabung sub-seksi kabel AC jadi satu "Kabel AC". Rapikan spesifikasi ("NYY 1 X 120 mm" → "NYY 1×120 mm²"). Qty pecahan dari spare (13,8) dibulatkan ke atas.
- Kolom "Supply By" tidak ditampilkan.

Catatan BOQ: garansi ("Garansi PV 12 thn") → `id.warranty`; poin "tolong dimasukkan ke proposal" → tambahkan ke `id.terms` bila belum ada; "harga breakdown di hide" = jangan tampilkan harga satuan (memang tidak pernah).

## 3. Baca rincian rekening PLN

Per bulan, isi satu entri `pln_bills` (urut lama → baru):

```json
{"period": "Juni 2026", "days": 31, "tariff_class": "B3", "power_va": 1385000, "meter_factor": 2000,
 "stand": {"lwbp": [1028.024, 1148.371], "wbp": [226.605, 252.258]},
 "lwbp_kwh": 240694, "wbp_kwh": 51306, "kvarh": 0,
 "lwbp_rp_kwh": 1035.78, "wbp_rp_kwh": 1553.67,
 "ttl_rp": 329018624, "ppj_rp": 32901862, "total_rp": 361920486}
```

- `period` = **bulan pemakaian** = bulan tanggal Stand Awal (stand 01-03 → 01-04 = "Maret 2026", walau rekeningnya "04-2026"); `days` = selisih tanggal Stand Awal → Stand Akhir.
- PDF tagihan yang berisi teks: baca angkanya dengan pymupdf (`get_text()`), bukan dari gambar. PBJT/PPJ bisa 3% (industri) atau 10%; ambil dari rekening.
- Angka di foto pakai titik ribuan: "Rp 329.018.624" → 329018624; stand "1.148,371" atau "1,148.371" → 1148.371.
- **Jangan** salin NIK, NPWP, nama pribadi, atau alamat pribadi. ID pelanggan juga tidak perlu.
- Nama perusahaan "Nama Sesuai NPWP" boleh dipakai sebagai nama klien bila user tidak memberi nama lain — tanyakan bila beda dengan nama di BOQ.

Tanpa rekening: minta tagihan per bulan (Rp) dan jam operasi + Sabtu/Minggu → `load`.

**Rumah tinggal** (segmen `Residential`, BOQ kecil, inverter hybrid/baterai): versi sederhana otomatis — lihat bagian "Versi Residensial" di TEMPLATE.md. Isi `load.monthly_kwh` (kWh/bulan dari tagihan/token), `work_hours: [0, 24]`, tarif R1 + PBJT di `financial.pln_tariff_rp_kwh`, dan untuk baterai `system.battery` + `system.battery_kwh`. Override `id.warranty` (tambah baterai) dan `id.schedule` (± 2 bulan). Contoh: `GEN/projects/contoh-rumah/inputs.json`.

## 4. Tulis inputs.json lalu rakit

Tulis `GEN/projects/<id>/inputs.json` (id = `<nomor>-<nama-singkat>`, huruf kecil, mis. `139-agung-alam-asri`) berisi hanya yang spesifik proyek:
`meta`, `client` (`name`, `short_name`, `location_id`, `city`, `segment` = Industrial/Commercial), `system`, `price`, `boq`, `pln_bills` atau `load`, `images` (bila ada layout: salin ke `projects/<id>/img/`), `datasheets`, dan override `id` (`warranty`, `terms` tambahan, `layout_caption`) bila perlu.

Datasheet: cari PDF yang cocok di `datasheet/<merek>/` (Glob nama model/daya). Path relatif dari folder proyek: `../../../datasheet/...`. Judul: "Modul Surya · JA Solar JAM72D42 LB", "Inverter · Sungrow SG150CX". Tidak ketemu → lewati dan sebutkan di laporan.

```
py -I -X utf8 .claude/skills/buat-proposal-cni/scripts/assemble.py proposal-generator <id> proposal-generator/projects/<id>/inputs.json
```

- `missing` tidak kosong → kumpulkan semua yang kurang, tanya user **sekali** (AskUserQuestion bila pilihannya jelas), lalu ulangi.
- `warnings` soal tagihan (stand × faktor kali, kWh × tarif ≠ TTL) → baca ulang foto bagian itu dan perbaiki sebelum lanjut. Ini tanda salah baca angka.
- Turunan otomatis: `load.monthly_kwh/monthly_bill_rp` = rata-rata rekening, tarif penghematan = LWBP + PPJ.
- **Iradiasi**: `financial.peak_sun_hours` (kWh/m²/hari) dan `financial.irradiance_source` diisi otomatis dari `client.city` lewat `GEN/data/irradiance.json` (Global Solar Atlas). Jangan diisi manual kecuali user memberi angka lain. Warning "Kota … tidak ada di data/irradiance.json" → tambahkan kotanya ke tabel (atau isi `peak_sun_hours` + `irradiance_source` dari sumber lain) sebelum build.
- Asumsi tetap dari `_base` (jangan diubah per proyek tanpa alasan): `performance_ratio` 0,80 · 365 hari · degradasi 1% tahun 1 lalu 0,4%/tahun · O&M 1% investasi/tahun · penggantian inverter tahun 12 · penggantian baterai tahun 10 dan 20 (hanya bila ada baterai) · harga penggantian = harga hari ini.
- **Rumah tinggal** (`client.segment = Residential`): `assemble.py` otomatis memakai O&M 0,5% dan penggantian baterai tahun 15 (± 6.000 siklus ≈ 16 tahun; O&M rumah lebih ringan), kecuali `inputs.json` mengisi `financial.om_pct_of_capex` / `battery_replacement_years`. Sebut ini di laporan.

## 5. Build dan cek

```
py proposal-generator/build.py projects/<id>
py -I -X utf8 .claude/skills/buat-proposal-cni/scripts/check_pdf.py proposal-generator/output/<id>-v2.pdf
```

`build.py` otomatis menjalankan `check_pdf.py` setelah PDF jadi (matikan dengan `--no-check`) dan mencetak peringatan validasi (`! …`: termin ≠ 100%, jumlah inverter/modul tidak cocok kapasitas, `{kota}` tersisa, harga inverter/baterai kosong, payback di luar 2–15 tahun, layout tidak ditemukan). **Baca dan selesaikan semua peringatan** sebelum melapor.

Lalu **lihat** PNG halaman yang berisi data proyek (cover, ringkasan, profil beban, spesifikasi, penghematan, harga, BOQ) dengan Read. Periksa:
- urutan halaman sesuai TEMPLATE.md; tidak ada `OVERFLOW?`;
- nama klien, lokasi, kWp, jumlah modul/inverter sama dengan BOQ;
- total harga = BOQ, PPN 11%, grand total cocok;
- tabel rekening = angka di foto; tidak ada data pribadi;
- angka masuk akal: payback 3–10 tahun ("lebih dari 25 tahun" = harga/asumsi salah, jangan kirim), solar share < 100%, tidak ada "None"/"{kota}"/"nan";
- halaman Penghematan memuat sumber iradiasi, PR, O&M, dan tahun penggantian; kolom Biaya di Proyeksi terisi di tahun penggantian.

Perbaiki dan build ulang sampai bersih. Jangan lapor selesai sebelum PDF dicek.

## 6. Lapor

Singkat, Bahasa Indonesia:
- path PDF (dan kirim file ke user);
- kapasitas, harga, hemat/bulan, payback;
- asumsi yang dipakai: iradiasi kota X kWh/m²/hari (sumber) × performance ratio 80% × 365 hari, degradasi 1% tahun 1 lalu 0,4%/tahun, O&M 1% investasi/tahun, penggantian inverter tahun 12 (Rp …) dan baterai tahun 10 dan 20 bila ada (rumah tinggal: O&M 0,5% dan baterai sekali di tahun 15), eskalasi tarif 2%/tahun, tanpa ekspor;
- balik modal, IRR, dan hemat per bulan (hemat per bulan = pengurangan tagihan, sebelum O&M);
- yang masih placeholder/belum ada (layout, datasheet tidak ketemu).
