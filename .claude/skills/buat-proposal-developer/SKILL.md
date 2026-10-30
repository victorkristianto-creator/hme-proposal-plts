---
name: buat-proposal-developer
description: Buat penawaran BOS PLTS ke developer (modul surya dan inverter disuplai developer, HME menyuplai sisanya plus instalasi) dari BOQ Excel. Tanpa tagihan PLN, tanpa penghematan, tanpa balik modal. Pakai saat user minta "proposal developer", "penawaran BOS", atau memberi BOQ yang harga modul dan inverternya nol / disuplai developer.
argument-hint: "<BOQ.xlsx> [nama developer] [nama site]"
---

# /buat-proposal-developer — penawaran BOS PLTS ke developer

Varian dari `/buat-proposal-cni` dengan `client.segment = "Developer"`. Generator, template v2, dan script sama; aturan lengkap di `proposal-generator/TEMPLATE.md` bagian "Versi Developer (BOS)". Script ada di `.claude/skills/buat-proposal-cni/scripts/`. Jalankan Python dengan `py -I -X utf8` (Windows) atau `python3 -I -X utf8` (Mac/Linux).

Tujuan: PDF siap kirim dalam satu kali jalan. Tanya user **sekali**, dalam satu daftar, hanya untuk yang tidak ada di input.

## 1. Kumpulkan input

| Butuh | Dari mana |
|---|---|
| BOQ Excel | argumen / file yang user sebut. Wajib. |
| Developer (penerima penawaran) | pesan user. Ini `client.name`, yang tanda tangan dan muncul di kolom lingkup. |
| Site (lokasi proyek) | pesan user, baris "Delivery" di BOQ. Ini `site.name`, **bukan** klien. |
| Lokasi (kota, provinsi) | baris "Delivery", judul BOQ, pesan user |
| Nomor penawaran | catatan BOQ ("No Proposal 200") → `200/Q.PLTS/HME/<bulan romawi>/<tahun>` |
| Layout atap | sheet lain di workbook BOQ (biasanya `Design++`, gambar di `xl/media/`) atau file dari user |

Penawaran lama ke developer yang sama ada di `contoh proposal yang sudah berjalan/`: pakai nama resmi developer dari sana bila user menulisnya singkat.

## 2. Baca BOQ

```
py -I -X utf8 .claude/skills/buat-proposal-cni/scripts/read_boq.py "<BOQ.xlsx>"
```

`read_boq.py` hanya membaca sheet BOQ; periksa juga sheet lain untuk gambar dan catatan (`openpyxl`: `ws._images`, atau buka xlsx sebagai zip).

Isi `system` seperti `/buat-proposal-cni`, ditambah:
- `panel`: qty, brand, wp dari baris "Modul Solar". `model` boleh kosong bila BOQ hanya menulis merek dan Wp; jangan menebak model.
- `panel.spare`: baris "Spare", dibulatkan ke atas (21,2 → 22).
- `inverter`: baris "Inverter On-Grid", `mix` bila beberapa model.

**Cocokkan sebelum lanjut:**
- Jumlah kolom total = `total` BOQ. `price.total` = `total` BOQ sebelum PPN, `vat_pct` 11.
- Baris modul dan inverter harus berharga 0. Kalau ada harganya, itu bukan BOS murni: tanyakan apakah keluar dari lingkup, karena `price.total` harus sudah tanpa keduanya.
- Inverter di "Main Material" harus sama dengan breaker dan kabel per inverter di seksi lain. Contoh: baris inverter 5 × 150 kVA, tetapi ada breaker dan kabel "100KTL" 3 unit, berarti BOQ tidak konsisten. Hitung juga rasio DC/AC: di atas ± 1,5 patut dicurigai. Tanyakan ke user, jangan memilih sendiri.

`boq` (yang tampil) = gaya sama seperti `/buat-proposal-cni` (tanpa harga, Bahasa Indonesia, kabel AC digabung, Services + Delivery + Permission jadi "Jasa, Pengiriman & Perizinan"), dengan satu perbedaan: **modul, modul cadangan, dan inverter tidak masuk `boq`** (seksi "Main Material" hilang). Rapikan salah ketik BOQ ("Kiplock" → "Kliplok", "Gavalum" → "Galvalum"). Item berqty Wp (mis. jasa instalasi bersatuan "ls") tulis satuannya "Wp".

## 3. Tulis inputs.json lalu rakit

Tulis `proposal-generator/projects/<id>/inputs.json` (id = `<nomor>-<site-singkat>`, huruf kecil, mis. `203-pabrik-baru`). Contoh: `proposal-generator/projects/contoh-developer/inputs.json`.

```json
{"client": {"name": "PT ...", "short_name": "...", "location_id": "Kota, Provinsi", "city": "Kota", "segment": "Developer"},
 "site": {"name": "PT ...", "short_name": "..."},
 "id": {"layout_caption": "...",
        "included": ["... daftar 'Termasuk Penawaran' bila beda dari default ..."],
        "bos_specs": [["Mounting", "Kliplok", "catatan singkat"], ["Panel AC", "2 panel · ACB 1250 A", "2 titik interkoneksi"]]}}
```

- `client.short_name` pendek (1–2 kata): tampil di kolom lingkup, cover, dan footer.
- `id.bos_specs`: 2–3 baris material utama untuk halaman Spesifikasi, dari BOQ. Satu baris per item, tanpa teks panjang, agar halaman tidak meluap.
- Tanpa `pln_bills`, `load`, `financial`, dan harga penggantian.
- `datasheets`: lampirkan modul dan inverter walaupun disuplai developer. Cari PDF di `datasheet/<merek>/`, cek model dan rentang Wp di isi PDF (nama file sering tidak jelas), path relatif `../../../datasheet/...`, judul "Modul Surya · Trina Solar TSM-NE19R", "Inverter · Huawei SUN2000-150KTL-MG0". Satu entri per tipe inverter. Bila BOQ tidak menyebut model, isi `model` di `system` sesuai datasheet yang dilampirkan dan sebut di laporan bahwa model itu perlu dikonfirmasi. Tidak ketemu → lewati dan sebutkan di laporan.
- Gambar layout: simpan ke `projects/<id>/img/`, potong ke rasio 1,74 : 1 (halaman memotong ke rasio ini) agar kompas atau tepi gambar tidak terpotong setengah.

```
py -I -X utf8 .claude/skills/buat-proposal-cni/scripts/assemble.py proposal-generator <id> proposal-generator/projects/<id>/inputs.json
```

`assemble.py` memakai blok `developer` di `projects/_base/project.json` (syarat, garansi, lingkup, jadwal, jaminan mutu). `missing` tidak kosong → tanya user sekali, lalu ulangi.

## 4. Build dan cek

```
py proposal-generator/build.py projects/<id>
```

Baca dan selesaikan semua peringatan (`! …`). `check_pdf.py` hanya mendeteksi teks yang masuk footer, jadi **lihat** tiap halaman dengan Read, render dengan dpi cukup (85+):
- urutan: Cover · Profil Perusahaan · Ringkasan Penawaran · Spesifikasi · Harga · Lingkup · BOQ · Garansi dan Jadwal · Datasheet · Terima Kasih;
- gambar dashboard di Spesifikasi tidak menyentuh footer; kalau mepet, pendekkan `bos_specs` dan `system.monitoring` jadi satu baris;
- developer dan site tidak tertukar: tanda tangan = developer, "Site" = lokasi proyek;
- total = BOQ, PPN 11%, grand total cocok, harga per Wp masuk akal;
- tidak ada modul atau inverter di BOQ; tidak ada kata hemat, balik modal, atau tagihan di halaman mana pun; tidak ada "None"/"{kota}"/"{developer}".

## 5. Lapor

Singkat, Bahasa Indonesia: path PDF (dan kirim file ke user), kapasitas, harga, Rp/Wp, yang tidak konsisten di BOQ, dan yang memakai default (termin, jadwal, syarat tambahan).
