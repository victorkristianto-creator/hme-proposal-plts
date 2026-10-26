# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Pembaca utama penawaran PLTS dari PT Hexa Multi Energi (HME):
- **Owner / direktur** perusahaan klien (industri, komersial) — memutuskan investasi: berapa hemat, kapan balik modal, apakah HME kredibel.
- **Finance / purchasing** — membandingkan harga, BOQ, termin, syarat & ketentuan dengan penawaran vendor lain.

Engineer klien membaca bagian teknis & datasheet, tapi bukan target persuasi utama.

Penyusun: sales engineer HME yang membuat penawaran per proyek dari satu `project.json`.

## Product Purpose

Generator dokumen penawaran PLTS (solar PV) yang menghasilkan PDF siap kirim dan versi presentasi dari satu sumber data proyek. Sukses = klien paham nilai investasinya dalam beberapa halaman pertama dan bisa menyetujui tanpa menunggu penjelasan lisan.

## Positioning

Penghematan dihitung dari energi surya yang benar-benar terpakai di lokasi (profil beban dari tagihan bulanan, jam kerja, hari Sabtu/Minggu), tanpa kredit ekspor — angka yang jujur, bukan klaim "hemat 50%" generik.

## Operating Context

- Dokumen dikirim sebagai PDF via email/WhatsApp dan sering dicetak; juga dipresentasikan di layar (versi slides).
- Dibuat dengan `build.py` (Python + Jinja2) → HTML → PDF via Chrome headless. Data per proyek di `projects/<id>/project.json`; datasheet di-append sebagai lampiran.
## Capabilities and Constraints

- Dua format dari satu data: **print** A4 portrait (prioritas pertama), **slides** A4 landscape.
- Bahasa default: **Bahasa Indonesia**.
- Halaman finansial/penghematan selalu sebelum BOQ. Ringkas, KPI tidak diulang-ulang.
- Grafik: garis ("solar naik, PLN turun"), bukan bar blok.
- Nanti: versi interaktif yang link ke app "PLTS super" (layout 3D); AI agent yang membuat penawaran dari BOQ Excel + lokasi.

## Brand Commitments

Identitas visual wajib mengikuti Company Profile 2026: https://adedian.github.io/company-profile/ — palet navy (#104674, #0A3055, #071C30) + amber #F59A1B + sun #FDB813, font Bricolage Grotesque (display) + Figtree (body), motif hexagon. Nama: PT Hexa Multi Energi, tagline "Clean energy for a better future".

## Evidence on Hand

Boleh dipakai (dikonfirmasi user 2026-10-19): 10+ MWp terpasang (angka compro terbaru; sebelumnya 6+), berdiri November 2022, portofolio industri 10,26 MWp (Pei Hai fase 1 & 2, Mitra Saruta, Setia Kawan, Inkasa Jaya), logo klien & brand dari compro. Foto: `assets/img/` dan `assets/img/gallery/`. Kontak dari compro (telepon, email, alamat Citraland Surabaya).
Jangan mengarang testimoni atau angka penghematan klien lain.

## Product Principles

1. Angka jujur dan bisa ditelusuri — setiap klaim penghematan punya asumsi yang terlihat.
2. Keputusan dulu, detail kemudian — owner paham nilai investasi di halaman awal; BOQ & datasheet di belakang.
3. Satu sumber data, dua format — tidak ada edit manual per PDF.
4. Konsisten dengan compro — penawaran terasa sebagai kelanjutan company profile.
