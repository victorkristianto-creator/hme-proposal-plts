# Template Penawaran PLTS — standar v2

Ditetapkan 22 Oktober 2026. Contoh input: `projects/contoh-industri` (dengan tagihan PLN) dan
`projects/contoh-rumah` (rumah tinggal, tanpa tagihan per blok). Template lama (`print`, `slides`, `slides_v1`, `portrait`) hanya arsip.

```
py build.py projects/<id>              # = v2, lalu otomatis check_pdf.py (PNG di output/.check + tanda OVERFLOW?)
py build.py projects/<id> v2           # sama
py build.py projects/<id> --no-check   # tanpa check_pdf
```

Sebelum render, `build.py` mencetak peringatan validasi (`  ! …`): termin ≠ 100%, jumlah inverter atau modul tidak cocok kapasitas,
`{kota}` tersisa di syarat, payback di luar 2–15 tahun atau tidak kembali, layout tidak ditemukan, harga inverter/baterai kosong,
dan `performance_ratio` dipakai tetapi iradiasi masih default 4,5. Selesaikan semuanya sebelum PDF dikirim.

## Urutan halaman (tetap)

| # | Halaman | Muncul jika | Sumber data |
|---|---|---|---|
| 1 | Cover | selalu | `meta`, `client`, `system`, `images` |
| 2 | Profil Perusahaan | selalu | tetap (company profile HME), `company` |
| 3 | Ringkasan Eksekutif | selalu | hasil hitung `finance()` |
| 4 | Profil Beban dan Energi | ada `pln_bills` | tagihan PLN + simulasi surya |
| 4 | Analisis Profil Energi | ada `load`, tanpa `pln_bills` | `load` (tagihan bulanan saja) |
| 5 | Spesifikasi Sistem | selalu | `system`, `images.layout` (tanpa layout: foto rumah dalam heksagon untuk Residential, kotak 35 mm untuk lainnya) |
| 6 | Analisis Penghematan | selalu | `financial`; dua donut tahun pertama: Rupiah (tagihan dihemat) dan kWh (dipasok PLTS) |
| 7 | Proyeksi Arus Kas 25 Tahun | selalu | `financial` |
| 8 | Penawaran Harga | selalu | `price`, `id.terms`, termin |
| 9 | Lingkup Pekerjaan | selalu | `scope` |
| 10+ | Daftar Material (BOQ) | selalu, dipecah menurut tinggi halaman sebenarnya (baris yang membungkus dihitung; halaman pertama punya judul) | `id.boq` / `boq` |
| … | Garansi dan Jadwal | selalu | `warranty`, `id.schedule` |
| … | Lampiran Datasheet | per file di `datasheets` | PDF di `datasheet/` (root repo) |
| akhir | Terima Kasih | selalu | tetap |

## Input per proyek → `projects/<id>/project.json`

| Input dari sales | Mengisi | Catatan |
|---|---|---|
| **BOQ Excel** (sheet pertama, header `No · Item · Jumlah · Satuan · Spesifikasi · Price/Unit · Total Price`) | `system` (kWp, kVA, modul, inverter), `price.total`, `boq` (tanpa harga satuan) | baris tanpa nomor = judul seksi; baris 1–2 berisi Wp dan kVA |
| **Tagihan PLN** (rincian rekening, 1–12 bulan) | `pln_bills[]`, `load.monthly_kwh`, `load.monthly_bill_rp`, `financial.pln_tariff_rp_kwh` | kWh LWBP/WBP = selisih stand × faktor kali; tarif hemat = LWBP + PPJ. **Jangan** salin NIK/NPWP |
| **Lokasi** | `client.location_id`, `client.city`, `financial.peak_sun_hours` + `irradiance_source` (otomatis dari kota), `id.terms` (Franco …) | `assemble.py` mengisi iradiasi dari `data/irradiance.json` |
| Jam operasi, Sabtu/Minggu | `load.work_hours`, `load.saturday`, `load.sunday` | tanpa tagihan per blok, ini yang membentuk profil |
| Layout atap (gambar) | `images.layout`, `id.layout_caption` | |
| Nomor penawaran, tanggal, revisi | `meta` | |

Bagian yang tetap sama untuk semua proyek: profil perusahaan, logo klien/merek, kontak, lingkup standar, garansi standar.

## Perhitungan dan asumsi (`build.py`: `load_analysis`, `simulate`, `finance`)

**Produksi.** Tahun 1 = kWp × iradiasi (kWh/m²/hari) × `operating_days` × `performance_ratio` × (1 − degradasi tahun 1). Tahun berikutnya × (1 − degradasi tahunan).
`performance_ratio` yang tidak ada di project.json dianggap 1,0 dan `peak_sun_hours` dibaca sebagai jam matahari efektif lama
(4,5 × 360 hari): proyek lama menghasilkan angka yang sama persis. Halaman Penghematan menulis rumusnya, sumber iradiasi, dan PR.

**Terpakai di lokasi (SCR).** Simulasi per jam untuk tiap hari dalam seminggu × tiga tipe hari PV, dibobot menurut frekuensinya:
cerah 50% (1,25× hari rata-rata), berawan 35% (0,90×), hujan 15% (0,40×). Rata-rata terbobot tepat 1,0, jadi produksi tahunan tidak berubah.
Bentuk kurva hari cerah: sin(π(t−6)/12)^1,5 antara 06.00–18.00; daya dipotong di `system.capacity_kva`. Grafik "hari cerah" memakai kurva yang sama.
Baterai: tersimpan = min(surplus hari itu, 90% × `battery_kwh`), kembali × 90% (efisiensi bolak-balik), maksimal sebesar sisa beban hari itu.
Hemat = kWh terpakai × tarif; surplus tidak dikreditkan (tanpa ekspor). CO₂ dan jumlah pohon dihitung dari kWh terpakai.

**Biaya (arus kas bersih).** Tiap tahun: biaya = O&M (`om_pct_of_capex`% × investasi, datar) + penggantian pada tahun yang terdaftar. Hemat bersih = hemat − biaya.
Payback, IRR, NPV, ROI, sensitivitas, dan grafik kas memakai arus bersih; payback = persilangan nol TERAKHIR (cum tetap ≥ 0 sesudahnya), atau
"lebih dari 25 tahun" bila tidak kembali. LCOE = (investasi + Σ biaya) / Σ kWh terpakai. "Hemat per bulan" dan donat tagihan adalah pengurangan tagihan (bruto, sebelum O&M).

| Parameter (`financial`/`system`) | Default `_base` | Catatan |
|---|---|---|
| `peak_sun_hours` | 4,5 (diisi otomatis dari kota) | iradiasi GHI kWh/m²/hari; `irradiance_source` ikut tampil di proposal |
| `operating_days` | 365 | ketersediaan sudah masuk PR |
| `performance_ratio` | 0,80 | tidak ada = 1,0 (rumus lama) |
| `degradation_first_year_pct` / `degradation_pct` | 1 / 0,4 | sesuai datasheet modul N-type; berlaku mulai tahun 1 |
| `om_pct_of_capex` | 1 | % investasi per tahun |
| `inverter_replacement_years` | [12] | daftar tahun; tidak ada = tanpa penggantian |
| `battery_replacement_years` | [10, 20] | hanya bila `system.battery_kwh` |
| `system.inverter.price_rp` | dari BOQ | jumlah baris inverter; kosong → 10% investasi + peringatan |
| `system.battery_price_rp` | dari BOQ | baris baterai; kosong → 25% investasi + peringatan |

Biaya penggantian memakai harga hari ini (tanpa inflasi atau penurunan harga) dan ditulis begitu di halaman Penghematan.

**Tabel iradiasi.** `data/irradiance.json`: `{"<Kota>": {"ghi_kwh_m2_day", "lat", "lon", "source"}}` (GHI jangka panjang Global Solar Atlas ÷ 365; NASA POWER sebagai
pembanding). `assemble.py` mengisi `financial.peak_sun_hours` + `irradiance_source` dari `client.city` bila input tidak menyebut `peak_sun_hours`;
kota yang belum ada → peringatan. Menambah kota: ambil `https://api.globalsolaratlas.info/data/lta?loc=<lat>,<lon>` (`annual.data.GHI` ÷ 365), isi sumbernya.

**Gambar.** `build.py` mengecilkan foto ke ukuran cetak (JPEG q82; layout 2000 px q88; PNG hanya bila benar-benar transparan) dan menyimpan cache di
`output/.img/`; target PDF < 5 MB. Logo merek diambil dari `assets/img/compro/b-<merek>.webp` (huruf kecil tanpa spasi, dengan atau tanpa "solar"):
taruh file baru di folder itu dan logonya muncul; merek tanpa file tampil tanpa logo.

## Aturan isi (jangan dilanggar)

- Bahasa Indonesia. Penghematan hanya dari energi surya yang terpakai di lokasi, tanpa kredit ekspor.
- Halaman finansial selalu sebelum BOQ. KPI hanya diulang di Ringkasan.
- Grafik garis, label langsung, tanpa legenda kotak.
- Data pribadi di tagihan (NIK, NPWP, nama pribadi) tidak masuk penawaran.

## Agent: `/buat-proposal-cni`

Skill di `.claude/skills/buat-proposal-cni/` (buka Claude Code di root repo, folder yang berisi `proposal-generator/`).
Beri BOQ Excel + foto rincian rekening PLN + lokasi; agent membaca BOQ (`read_boq.py`), membaca tagihan,
merakit `projects/<id>/project.json` dari `projects/_base` (`assemble.py`, termasuk cek stand × faktor kali dan kWh × tarif = TTL),
build PDF, lalu mengecek tiap halaman (`check_pdf.py`). Bagian tetap untuk semua proyek ada di `projects/_base/project.json`.

## Versi Residensial (sederhana)

Otomatis bila `client.segment = "Residential"`. Halaman: Cover (foto rumah) · Ringkasan + grafik kas ·
Spesifikasi Sistem · Penawaran Harga · BOQ · Garansi dan Jadwal · Datasheet · Terima Kasih.
Tanpa profil perusahaan, profil beban, analisis penghematan, proyeksi 25 tahun, dan lingkup pekerjaan.

Input: `load.monthly_kwh` (atau tagihan Rp) + `work_hours [0, 24]`; tarif R1 + PBJT (mis. Rp 1.444,70 × 1,1 = Rp 1.589,17);
sistem hybrid isi `system.battery` (teks), `system.battery_kwh`, dan `system.battery_price_rp` — surplus siang yang masuk baterai ikut dihitung hemat
(maks. 90% kapasitas per hari, efisiensi bolak-balik 90%, simulasi per tipe hari). Tanpa gambar layout, halaman Spesifikasi menampilkan foto rumah.
Asumsi biaya rumah (default `assemble.py` untuk proyek baru bila input tidak menyebutnya): `om_pct_of_capex` 0,5 dan
`battery_replacement_years` [15] (± 6.000 siklus ≈ 16 tahun pada 1 siklus/hari; O&M rumah lebih ringan dari pabrik). Segmen lain tetap memakai
default `_base` (O&M 1%, baterai tahun 10 dan 20). Isi `financial.om_pct_of_capex` / `battery_replacement_years` di inputs.json untuk menimpa.
Contoh: `projects/contoh-rumah`.
