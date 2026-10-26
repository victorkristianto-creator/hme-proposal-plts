"""Merge per-project inputs onto projects/_base and validate the result.

Usage: py -I assemble.py <generator_dir> <project_id> <inputs.json>

Writes <generator_dir>/projects/<project_id>/project.json and prints a JSON report:
  {"ok": bool, "missing": [...], "warnings": [...], "derived": {...}}
Exit code 1 when required data is missing (nothing is built from incomplete data).
"""
import copy
import json
import sys
from pathlib import Path

REQUIRED = [
    "meta.number", "meta.date_id", "meta.prepared_by",
    "client.name", "client.short_name", "client.location_id", "client.city", "client.segment",
    "system.type", "system.mounting", "system.capacity_kwp", "system.capacity_kva",
    "system.panel.qty", "system.panel.brand", "system.panel.model", "system.panel.wp",
    "system.inverter.qty", "system.inverter.brand", "system.inverter.model", "system.inverter.kva",
    "system.monitoring", "price.total",
]


def merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in extra.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def get(d, path):
    for k in path.split("."):
        if not isinstance(d, dict) or d.get(k) in (None, ""):
            return None
        d = d[k]
    return d


def check_bills(bills, warn):
    for b in bills:
        tag = b.get("period", "?")
        f = b.get("meter_factor", 1)
        for blk in ("lwbp", "wbp"):
            st = (b.get("stand") or {}).get(blk)
            if st and abs((st[1] - st[0]) * f - b[f"{blk}_kwh"]) > 1:
                warn.append(f"Tagihan {tag}: kWh {blk.upper()} {b[f'{blk}_kwh']} ≠ (stand {st[1]} − {st[0]}) × {f}. Baca ulang foto.")
        if b.get("ttl_rp"):
            calc = b["lwbp_kwh"] * b["lwbp_rp_kwh"] + b["wbp_kwh"] * b["wbp_rp_kwh"]
            if abs(calc - b["ttl_rp"]) / b["ttl_rp"] > 0.001:  # real bills match to ~Rp 1.000
                warn.append(f"Tagihan {tag}: kWh × tarif = Rp {calc:,.0f}, tapi Rupiah TTL = Rp {b['ttl_rp']:,.0f}. Baca ulang kWh/tarif.")
            if b.get("total_rp") and b.get("ppj_rp") and abs(b["ttl_rp"] + b["ppj_rp"] - b["total_rp"]) > 5:
                warn.append(f"Tagihan {tag}: TTL + PPJ ≠ total tagihan (cek biaya lain di rekening).")


def residential_defaults(p, inp, derived):
    """Homes: O&M 0,5% of capex per year and the battery replaced once at year 15 (about 6.000 cycles = 16 years at one
    cycle a day), unless the inputs set them. The factory defaults in _base (O&M 1%, battery years 10 and 20) stay for everyone else."""
    if get(p, "client.segment") != "Residential":
        return
    fin_in = inp.get("financial") or {}
    if "om_pct_of_capex" not in fin_in:
        p["financial"]["om_pct_of_capex"] = 0.5
        derived["om_pct_of_capex"] = 0.5
    if "battery_replacement_years" not in fin_in:
        p["financial"]["battery_replacement_years"] = [15]
        derived["battery_replacement_years"] = [15]


def fill_irradiance(gen, p, inp, city, warn, derived):
    """peak_sun_hours = long-term GHI (kWh/m²/day) of client.city from GEN/data/irradiance.json, unless the inputs set it."""
    if (inp.get("financial") or {}).get("peak_sun_hours"):
        return  # set by hand for this project
    try:
        table = json.loads((gen / "data" / "irradiance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        table = {}
    rec = next((v for k, v in table.items() if k.lower() == city.lower()), None)
    if not rec or not rec.get("ghi_kwh_m2_day"):
        warn.append(f"Kota '{city}' tidak ada di data/irradiance.json (atau nilainya kosong): peak_sun_hours tetap 4,5 (default). "
                    "Tambahkan kota ke tabel, atau isi financial.peak_sun_hours + financial.irradiance_source dari lokasi.")
        return
    src = "Global Solar Atlas" if "Global Solar Atlas" in rec.get("source", "") else "NASA POWER" if "NASA" in rec.get("source", "") else rec.get("source", "")
    p["financial"]["peak_sun_hours"] = rec["ghi_kwh_m2_day"]
    p["financial"]["irradiance_source"] = f"{src}, {city}"
    derived.update(peak_sun_hours=rec["ghi_kwh_m2_day"], irradiance_source=p["financial"]["irradiance_source"])


def check_prices(p, warn):
    """Replacement costs need the real inverter (and battery) prices from the BOQ; without them build.py falls back to 10% / 25% of capex."""
    s = p.get("system", {})
    f = p.get("financial", {})
    if f.get("inverter_replacement_years") and not get(s, "inverter.price_rp"):
        warn.append("system.inverter.price_rp kosong: isi dengan jumlah kolom total baris inverter di BOQ (read_boq). "
                    "Sementara, biaya penggantian inverter dihitung 10% dari investasi.")
    if s.get("battery_kwh") and f.get("battery_replacement_years") and not s.get("battery_price_rp"):
        warn.append("system.battery_price_rp kosong: isi dengan total baris baterai di BOQ (read_boq). "
                    "Sementara, biaya penggantian baterai dihitung 25% dari investasi.")


def main(gen_dir, pid, inputs_path):
    gen = Path(gen_dir)
    base = json.loads((gen / "projects/_base/project.json").read_text(encoding="utf-8"))
    base.pop("_comment", None)
    inp = json.loads(Path(inputs_path).read_text(encoding="utf-8"))
    p = merge(base, inp)
    pdir = gen / "projects" / pid
    missing, warn, derived = [], [], {}

    missing += [k for k in REQUIRED if get(p, k) is None]
    if not p.get("boq"):
        missing.append("boq")
    bills = p.get("pln_bills") or []
    if not bills and not get(p, "load.monthly_bill_rp") and not get(p, "load.monthly_kwh"):
        missing.append("pln_bills ATAU load.monthly_bill_rp (tagihan bulanan)")
    if not bills and p.get("load") and not get(p, "load.work_hours"):
        missing.append("load.work_hours (jam operasi) — wajib bila tanpa rincian rekening")

    city = get(p, "client.city") or "{kota}"
    residential_defaults(p, inp, derived)
    fill_irradiance(gen, p, inp, city, warn, derived)
    check_prices(p, warn)
    for key in ("terms",):
        p[key] = [t.replace("{kota}", city) for t in p.get(key, [])]
    p.setdefault("id", {})["terms"] = [t.replace("{kota}", city) for t in p["id"].get("terms", [])]

    if bills:
        check_bills(bills, warn)
        kwh = sum(b["lwbp_kwh"] + b["wbp_kwh"] for b in bills) / len(bills)
        rp = sum(b["total_rp"] for b in bills) / len(bills)
        last = bills[-1]
        ppj = last.get("ppj_rp", 0) / last["ttl_rp"] if last.get("ttl_rp") else 0
        p["load"] = merge({"work_hours": [0, 24], "saturday": True, "sunday": True, "base_load_pct": 100},
                          p.get("load") or {})
        p["load"].update(monthly_kwh=round(kwh), monthly_bill_rp=round(rp))
        if not inp.get("financial", {}).get("pln_tariff_rp_kwh"):
            p["financial"]["pln_tariff_rp_kwh"] = round(last["lwbp_rp_kwh"] * (1 + ppj), 2)
            p["financial"]["tariff_note"] = f"LWBP Rp {last['lwbp_rp_kwh']:,.2f}/kWh + PPJ {ppj * 100:.0f}%".replace(",", "#").replace(".", ",").replace("#", ".")
        derived.update(monthly_kwh=p["load"]["monthly_kwh"], monthly_bill_rp=p["load"]["monthly_bill_rp"],
                       pln_tariff_rp_kwh=p["financial"]["pln_tariff_rp_kwh"])

    s = p.get("system", {})
    try:
        dc = s["panel"]["qty"] * s["panel"]["wp"] / 1000
        if abs(dc - s["capacity_kwp"]) / s["capacity_kwp"] > 0.02:
            warn.append(f"Modul {s['panel']['qty']} × {s['panel']['wp']} Wp = {dc:.2f} kWp, tapi capacity_kwp = {s['capacity_kwp']}.")
        mix = s["inverter"].get("mix")
        ac = sum(m["qty"] * m["kva"] for m in mix) if mix else s["inverter"]["qty"] * s["inverter"]["kva"]
        if abs(ac - s["capacity_kva"]) > 0.5:
            warn.append(f"Inverter {s['inverter']['qty']} × {s['inverter']['kva']} kVA = {ac}, tapi capacity_kva = {s['capacity_kva']}. (Boleh bila inverter campuran — tulis rinciannya di model.)")
    except (KeyError, TypeError, ZeroDivisionError):
        pass
    for ds in p.get("datasheets", []):
        if not (pdir / ds["file"]).resolve().exists():
            warn.append(f"Datasheet tidak ditemukan: {ds['file']}")
    lay = get(p, "images.layout")
    if lay and not (pdir / lay).exists():
        warn.append(f"Gambar layout tidak ditemukan: {lay}")
    if not lay:
        warn.append("Tanpa gambar layout: halaman Spesifikasi menampilkan foto rumah dalam heksagon (Residential) atau kotak pendek 'Layout atap menyusul setelah survei lokasi'.")

    ok = not missing
    if ok:
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "project.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(ok=ok, project=str(pdir / "project.json") if ok else None,
                          missing=missing, warnings=warn, derived=derived), ensure_ascii=False, indent=1))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(*sys.argv[1:4])
