---
name: plts-residensial
description: Siapkan desain PLTS atap rumah tinggal dari model 3D WebODM (atau ukuran manual) menjadi satu file HTML offline "PLTS Roof Designer" — tampilan 3D, sun path & bayangan, dan penempatan modul PV yang diatur sendiri oleh pengguna dengan mouse/jari (tanpa coding). Pakai saat pengguna menyebut nama rumah/survei drone/task WebODM dan ingin desain PV, kapasitas atap, layout panel, atau berkas 3D untuk penawaran.
---

# /plts-residensial — dari survei drone ke editor layout PV

Pembagian peran:
- **Skill ini (Claude)** = persiapan: ambil model, cek identitas rumah, ukur bidang atap dari mesh, ambil PVGIS, isi ke aplikasi.
- **Aplikasi** (`assets/` → satu file HTML) = bagian interaktif yang dipakai pengguna sendiri: taruh/geser/hapus modul,
  isi baris/bidang otomatis, undo, varian (mis. 5/8/12 kWp), sun path, rugi bayangan tahunan, ekspor/impor layout JSON, PNG.

Jangan pernah menulis susunan PV tetap di kode lagi. Layout awal (bila diminta) masuk sebagai *varian* di `variants.json`.

Folder kerja per rumah: `<proyek>/plts-designer/work_<nama>/`. Hasil: `<proyek>/desain_plts_<nama>.html`.
Skrip ada di `~/.claude/skills/plts-residensial/scripts/` (Python 3, numpy, Pillow, DracoPy). Di Windows pakai `PYTHONIOENCODING=utf-8`.

## Langkah

1. **Ambil model.** WebODM lokal `http://127.0.0.1:29800`:
   ```
   python webodm.py tasks
   python webodm.py download <project> <task> work_<nama>/src
   ```
   Bila WebODM mati/tidak bisa dihubungi, minta pengguna menyalakannya, atau pakai `textured_model.glb` yang sudah ada di folder proyek.
   Bila WebODM minta login: pengguna sendiri yang mengisi `WEBODM_TOKEN` atau `WEBODM_USER`/`WEBODM_PASS` — jangan pernah mengetikkan kata sandi.
   Nama task bisa menyesatkan (mis. "SD-SMA Surabaya Intercultural School" ternyata berisi Jo Derma) — identitas dicek di langkah 3.

2. **Pusat rumah dari GPS foto drone.**
   ```
   python photos_gps.py <folder_foto>
   ```
   Foto nadir dipakai bila ada; untuk terbang orbit (pitch tidak tercatat, mis. DJI FC7703) pakai pusat sebaran. Ini hanya perkiraan awal.

3. **Siapkan model + CEK IDENTITAS (wajib, sebelum mengukur apa pun).**
   ```
   python prepare_model.py work_<nama>/src/textured_model.glb --gps=<lat,lon> --radius 20 --out work_<nama>
   ```
   Lihat `work_<nama>/top.png` (citra atas dari tekstur model; utara di atas, pusat potongan = pusat rumah) dan cocokkan dengan
   ciri rumah yang diketahui (bentuk atap, warna, tandon, tulisan di dak, arah jalan) atau orthophoto
   (`python webodm.py ortho <p> <t> <lat,lon> ortho.png`). Bila rumah tidak di tengah, ulangi dengan `--center-local X,Y`.
   **Pelajaran:** task Baruk Utara IX pernah dikira Jo Derma karena atap perisainya mirip. Bila ragu, tanyakan pengguna — jangan lanjut.
   `meta.json` menyimpan pusat UTM, lat/lon, elevasi tanah, konvergensi meridian (koordinat sudah diputar ke utara sejati).

4. **Fit bidang atap dari mesh.**
   ```
   python fit_planes.py work_<nama>                 # semua bidang + pratinjau planes.png
   python fit_planes.py work_<nama> --keep 1,2,3,5  # pilih bidang milik rumah ini (buang tetangga yang menempel)
   ```
   Lihat `planes.png`: tiap bidang bernomor dengan azimut/kemiringan. Rumah deret sering menempel dengan atap tetangga — pilih
   hanya bidang rumah ini. Cek RMS (< 3 cm bagus) dan kewajaran sudut (genteng 25–40°). Halangan otomatis = benda yang menonjol
   > 0,35 m **di dalam** poligon bidang (menara, tandon di atas atap); benda di samping lisplank tidak membatasi penempatan
   tapi tetap ikut perhitungan bayangan lewat mesh. Ketelitian tepi poligon ±10 cm (lihat "Batasan").
   Tumbuh-ulang diulang 3× (bidang benih bisa meleset ±20 cm); bila sebuah bidang di planes.png tampak hanya pita sempit,
   bandingkan luasnya dengan bentuk atap di top.png sebelum lanjut.

5. **PVGIS per bidang** (mengirim lat/lon + kemiringan/azimut ke API PVGIS JRC):
   ```
   python pvgis.py work_<nama>      # loss 14 %, hasil kWh/kWp/th per bidang -> pvgis.json
   ```
   Tanpa internet lewati saja; aplikasi memakai estimasi langit-cerah yang ditandai "estimasi".

6. **Rakit HTML & buka.**
   ```
   python make_site.py work_<nama> --id <slug> --name "PLTS — <Nama>" --subtitle "..." \
          [--setback 0.25] [--variants variants.json] --out "<proyek>/desain_plts_<nama>.html"
   ```
   Buka lewat server `static` (`.claude/launch.json`, port 8765) di panel browser, lalu periksa: model muncul, bidang
   (centang "tampilkan bidang") menempel di atap, kompas U sesuai, kapasitas "Isi bidang" masuk akal.
   Untuk uji otomatis di konsol: `window.__designer.fillPlane(PLANES()[i], 'P')`, `computeShade()`, `sunPosition(doy, menit)`.

7. **(Hanya bila diminta)** unggah ke GitHub Pages seperti `github-plts-baruk-utara` — selalu konfirmasi dulu sebelum push/publish.

### Tanpa WebODM (hanya foto/denah)
Buat `spec.json` (lihat docstring `manual_roof.py`: lebar, panjang, tinggi lisplank, tipe hip/gable/shed, kemiringan, arah muka),
lalu `python manual_roof.py spec.json work_<nama>` → `pvgis.py` → `make_site.py ... --accuracy manual`.
Aplikasi menandai akurasinya lebih rendah. Pengguna juga bisa menyeret file `.glb` sendiri ke template kosong
`<proyek>/plts-designer/index.html` dan membuat bidang dengan "＋ Bidang dari klik" (klik sudut-sudut atap, Enter).

## Format site.json (ENU lokal, meter: x = timur, y = utara, z = atas dari tanah)
```json
{ "id": "jo-derma-c4-19", "name": "...", "subtitle": "...", "lat": -7.28915, "lng": 112.64957, "tz": 7,
  "accuracy": "webodm|manual", "fit": {"center": [x, y, z], "radius": 12}, "setback": 0.25,
  "planes": [{"id": "barat", "name": "Barat", "color": "#f472b6", "plane": {"a": .., "b": .., "c": ..},
              "poly": [[x, y], ...], "yield": 1176, "yieldNote": "PVGIS", "setback": 0.25}],
  "obstacles": [{"name": "menara", "poly": [[x, y], ...], "setback": 0.4}],
  "variants": [{"name": "8 kWp", "modules": [{"p": "timur", "s": 1.44, "t": -9.1, "o": "P"}]}],
  "defaultModule": "trina720", "note": "..." }
```
Bidang: `z = a·x + b·y + c`. Modul: `s` = jarak naik lereng dari lisplank (pusat modul), `t` = sepanjang lisplank, `o` = P/L.
Grid modul disejajarkan dengan tepi lisplank (bukan horizontal murni). Model GLB di-embed dalam frame three.js
(X = x, Y = z, Z = −y), tanpa Draco — `prepare_model.py` yang mengurus.

## Aplikasi — ringkas untuk dijelaskan ke pengguna
- ✎ mode edit (atau tombol E): klik bidang → modul menempel di bidang. **Snap magnet**: bebas per 1 cm, menempel hanya bila
  ≤ 30 cm dari baris lisplank / baris-kolom modul lain (celah 20 mm); tahan Alt = tanpa snap. Bila jatuh di posisi tak valid,
  otomatis digeser ke posisi valid terdekat (≤1,2 m, langkah 5 cm). Seret memegang titik pegangan (tidak meloncat). Seret = geser, klik kanan/Delete = hapus, R = putar P/L,
  Ctrl+Z/Ctrl+Y. "Isi baris" dari modul terpilih, "Isi bidang" untuk bidang terakhir yang diklik.
- Modul merah = keluar bidang / setback / kena halangan / tumpang tindih — tidak dihitung di total.
- Hasil per bidang: jumlah, kWp, kWh/th (PVGIS × kWp − rugi bayangan), azimut/kemiringan.
- ☀ Hitung rugi bayangan tahunan: GPU depth-map dari ±300 posisi matahari (tiap 30 menit, tanggal 15 tiap bulan),
  ±5 detik; hasil = % sinar langsung terhalang × porsi sinar langsung (default 55 %). "warna rugi bayangan" = peta per modul.
- Simpan otomatis per browser (localStorage, kunci `plts-designer:<id>`), ekspor/impor JSON, PNG dengan kotak judul.
- Varian = tab (klik tab aktif untuk ganti nama / hapus; ＋ Varian menyalin varian aktif).

## Batasan & catatan verifikasi (2026-11-12)
- Baruk Utara: "Isi bidang" portrait setback 0,25 m → **timur 15 (6+6+3), barat 18 (8+6+4)** — sama persis dengan fungsi
  kapasitas di file lama `desain_plts_baruk_utara_5_8_12kwp.html` (catatan lama "barat 10" tidak cocok dengan kode itu).
- Jo Derma (fit otomatis, grow-tol 0,12): barat **6+3 = 9** (setback 0,25 dan 0,20); timur utama **3+4 = 7** pada setback
  0,25 m, **4+4 = 8** pada 0,20 m. Penyebab: di y ≈ −1,5…+1,2 m lisplank timur tertutup dak beton datar (+9,45 m, ±1 m di atas
  dak talang 8,45 m) sehingga ujung genteng mundur ±0,5 m. 17 PV (12,24 kWp) muat pada setback 0,20 m; modul ke-18 (P maupun L)
  tidak muat di barat/timur bahkan pada 0,15 m.
  File Jo Derma di-build dengan `--plane-setback barat=0.2,timur_1=0.2` (keputusan pengguna 2026-11-12); bidang lain 0,25 m.
- "Isi bidang" mencoba menggeser baris pertama 0…1 jarak baris dari lisplank terluar dan memilih susunan terbanyak
  (lisplank nyata sering tidak lurus). ±1,4 dtk per bidang.
- Tepi poligon otomatis bisa ±10–15 cm dari ukuran orthophoto; kapasitas peka terhadap ini bila modul pas-pasan.
  Sampaikan ke pengguna dan sarankan cek setback/ukur lapangan untuk baris yang "kurang beberapa cm".
- Arah utara: model WebODM sudah UTM (grid-utara); `prepare_model.py` memutar ke utara sejati (konvergensi ±0,2° di Surabaya).
