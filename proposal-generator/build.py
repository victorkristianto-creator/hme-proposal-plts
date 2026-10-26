"""HME proposal generator.

Usage:  py build.py projects/<id> [v2] [--no-check]
Reads <project>/project.json and writes output/<slug>-v2.pdf (A4 portrait, Bahasa Indonesia, templates/v2),
prints validation warnings first and runs the skill's check_pdf.py afterwards (--no-check skips it).
The archived formats (print, slides, ...) are still reachable as the second argument but are no longer maintained.
"""
import datetime
import hashlib
import json
import math
import random
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pymupdf
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]
MM = 72 / 25.4  # PDF points per mm
CHECK_PDF = ROOT.parent / ".claude" / "skills" / "buat-proposal-cni" / "scripts" / "check_pdf.py"
IRRADIANCE_FILE = ROOT / "data" / "irradiance.json"
COMPRO = ROOT / "assets" / "img" / "compro"
IMG_CACHE = ROOT / "output" / ".img"

# Datasheet slot on a datasheet frame page (mm, must match .ds-slot in proposal.css)
# Per-format layout settings. ds_area = datasheet area on a frame page (mm): x, y, w, h
FORMATS = {
    "print": dict(boq_rows=36, ds_area=(16, 46, 178, 222), ds_per_page=1, cum_h=180, ongrid_h=150, sched_rowh=34),
    "slides": dict(boq_rows=25, ds_area=(16, 36, 265, 156), ds_per_page=2, cum_h=400, ongrid_h=200, sched_rowh=24),
    # v2 = redesign following Company Profile 2026, Bahasa Indonesia (A4 portrait)
    "v2": dict(boq_rows=35, ds_area=(18, 40, 174, 232), ds_per_page=1, cum_h=180, ongrid_h=150, sched_rowh=34),
}

C = dict(navy="#104674", navy_deep="#0A3055", amber="#F59A1B", sun="#FDB813",
         ink="#0B2135", muted="#4A6176", line="#D3E0EB", soft="#E7F0F7", bg="#F3F7FB",
         grid="#FF7A6B", pv="#FFB703", eng="#23A874", amber_ink="#9C5507")


# ---------------------------------------------------------------- formatting
def fnum(x, dec=0):
    """Indonesian style: 1.234.567,89 (None -> en dash, so a missing figure never crashes a build)"""
    if x is None:
        return "–"
    s = f"{x:,.{dec}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def signed_rp(x):
    """+Rp 16,21 miliar · −Rp 52,50 miliar"""
    return ("+" if x >= 0 else "−") + "Rp " + fshort_id(abs(x))


def fpayback(x, years=25, short=False):
    """Payback as text: '4,4 tahun', or 'lebih dari 25 tahun' when the investment is not recovered."""
    if x is None:
        return f"> {years} tahun" if short else f"lebih dari {years} tahun"
    return f"{fnum(x, 1)} tahun"


def frp(x):
    return "Rp " + fnum(round(x))


def fshort(x):
    a = abs(x)
    if a >= 1e9:
        return f"{fnum(x / 1e9, 2)} billion"
    if a >= 1e6:
        return f"{fnum(x / 1e6, 1)} million"
    return fnum(x)


def fshort_id(x, dec=None):
    """Indonesian short form: 3,62 miliar · 55,7 juta"""
    a = abs(x)

    def trim(t):  # 300,0 -> 300 ; 3,60 stays (two-decimal billions keep their precision)
        return t[:-2] if t.endswith(",0") else t
    if a >= 1e9:
        return f"{fnum(x / 1e9, 2 if dec is None else dec)} miliar"
    if a >= 1e6:
        return f"{trim(fnum(x / 1e6, 1 if dec is None else dec))} juta"
    if a >= 1e4:  # homes: 394.706 -> 395 ribu
        return f"{fnum(x / 1e3)} ribu"
    return fnum(x)


def fenergy(kwh):
    """Yearly energy: 13.053 MWh · 48,7 MWh · 8.400 kWh"""
    if kwh >= 1e5:
        return f"{fnum(kwh / 1000)} MWh"
    if kwh >= 1e4:
        return f"{fnum(kwh / 1000, 1)} MWh"
    return f"{fnum(kwh)} kWh"


# ---------------------------------------------------------------- load matching
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAYS_ID = dict(zip(DAYS, ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]))
WEEKS_PER_MONTH = 365 / 7 / 12

# Day types for the PV model: (name, share of all days, energy relative to the average day).
# The share-weighted mean is exactly 1.0, so the yearly production is the same as the flat average.
PV_DAY_TYPES = (("cerah", 0.50, 1.25), ("berawan", 0.35, 0.90), ("hujan", 0.15, 0.40))
CLEAR_FACTOR = PV_DAY_TYPES[0][2]
BATT_USABLE, BATT_RT_EFF = 0.9, 0.90  # usable share of capacity per day · round-trip efficiency
PV_SUB = 12  # sub-steps per hour when integrating the clear-sky shape


def _shape_raw(t):
    """Clear-sky PV shape at clock hour t: sin^1.5 between 06:00 and 18:00 (narrower than a half-sine)."""
    return max(0.0, math.sin(math.pi * (t - 6) / 12)) ** 1.5 if 6 <= t <= 18 else 0.0


@lru_cache(maxsize=None)
def _shape_norm():
    """Integral of the raw shape over the day (hours); dividing by it makes a day integrate to 1 kWh."""
    return sum(_shape_raw((k + 0.5) / PV_SUB) for k in range(24 * PV_SUB)) / PV_SUB


def pv_kw(t, day_kwh, kva=None):
    """PV power (kW) at clock hour t on a day that would produce day_kwh, clipped at the inverter AC rating."""
    v = day_kwh * _shape_raw(t) / _shape_norm()
    return min(v, kva) if kva else v


def pv_hourly(day_kwh, kva=None):
    """Average PV power (kW) in each clock hour; integrates to day_kwh when nothing is clipped."""
    return [sum(pv_kw(h + (j + 0.5) / PV_SUB, day_kwh, kva) for j in range(PV_SUB)) / PV_SUB for h in range(24)]


def clear_day_kwh(y1_energy):
    """Energy of a clear day (kWh), from the year-1 production."""
    return y1_energy / 365 * CLEAR_FACTOR


def _bills(p):
    return p.get("pln_bills") or ([p["pln_bill"]] if p.get("pln_bill") else [])


def load_block(p):
    """The site load definition: the project's own `load`, or one derived from the PLN bills
    (24 h, 7 days, flat) when only bills were given."""
    if p.get("load"):
        return p["load"]
    bills = _bills(p)
    if not bills:
        return None
    n = len(bills)
    return dict(work_hours=[0, 24], saturday=True, sunday=True, base_load_pct=100,
                monthly_kwh=sum(b["lwbp_kwh"] + b["wbp_kwh"] for b in bills) / n,
                monthly_bill_rp=sum(b["total_rp"] for b in bills) / n)


def load_analysis(p, y1_energy):
    """Hour-by-hour simulation of PV vs. site load for every weekday and every PV day type
    (clear, cloudy, rainy), weighted by how often each occurs. Returns None if there is no load data."""
    ld = load_block(p)
    if not ld:
        return None
    tariff = p["financial"]["pln_tariff_rp_kwh"]
    monthly_kwh = ld.get("monthly_kwh") or ld["monthly_bill_rp"] / tariff
    ws, we = ld["work_hours"]
    workdays = [d for d in DAYS if not (d == "Sat" and not ld.get("saturday", True)) and not (d == "Sun" and not ld.get("sunday", False))]
    base = ld.get("base_load_pct", 0) / 100

    def is_work(d, h):
        return d in workdays and ws <= h < we

    # solve working-hour load L so the week's consumption matches the monthly bill
    week_kwh = monthly_kwh / WEEKS_PER_MONTH
    work_h = sum(is_work(d, h) for d in DAYS for h in range(24))
    off_h = 7 * 24 - work_h
    if monthly_kwh <= 0:
        sys.exit("Profil beban tidak valid: konsumsi bulanan 0 atau negatif. Isi load.monthly_kwh atau load.monthly_bill_rp.")
    if work_h + base * off_h <= 0:
        sys.exit("Profil beban tidak valid: tidak ada jam operasi (load.work_hours) dan beban di luar jam operasi "
                 "(load.base_load_pct) 0%, jadi beban per jam tidak bisa dihitung. Isi jam operasi atau beban dasar.")
    L = week_kwh / (work_h + base * off_h)
    kva = p["system"].get("capacity_kva")
    batt = p["system"].get("battery_kwh") or 0
    pv_day = y1_energy / 365
    day_pv = [(share, pv_hourly(pv_day * factor, kva)) for _, share, factor in PV_DAY_TYPES]

    profile = {}
    used = pv = load = 0.0  # per average day
    for d in DAYS:
        load_h = [L if is_work(d, h) else L * base for h in range(24)]
        load_tot = sum(load_h)
        for share, pv_h in day_pv:
            direct = sum(min(l, v) for l, v in zip(load_h, pv_h))
            pv_tot = sum(pv_h)
            u = direct
            if batt:  # surplus of the day is stored (usable capacity), comes back at the round-trip efficiency, only up to the evening load
                stored = min(pv_tot - direct, batt * BATT_USABLE)
                u += max(min(stored * BATT_RT_EFF, load_tot - direct), 0.0)
            w = share / 7
            used += w * u
            pv += w * pv_tot
            load += w * load_tot
        profile[d] = list(zip(load_h, day_pv[0][1]))  # charts show the clear day
    scr = used / pv if pv else 0  # share of (inverter-limited) PV production used on site
    # Clipping at the inverter is treated like any other performance-ratio loss: energy and savings
    # stay tied to the year-1 production, SCR only says how much of it the site absorbs.
    used_month = scr * y1_energy / 12
    off_day = next((d for d in DAYS if d not in workdays), None)
    # the real bill when we have it (it carries WBP and PPJ, which kWh × tariff does not)
    monthly_bill = ld.get("monthly_bill_rp") or monthly_kwh * tariff
    return dict(
        monthly_kwh=monthly_kwh, monthly_bill=monthly_bill,
        bill_given=bool(ld.get("monthly_bill_rp")),
        work_hours=(ws, we), workdays=workdays, base_pct=base * 100,
        load_kw=L, pv_peak_kw=pv_kw(12.0, clear_day_kwh(y1_energy), kva),
        scr=scr, solar_share=used_month / monthly_kwh,  # share of the site's kWh that solar replaces
        used_month=used_month, pv_month=y1_energy / 12,
        bill_after=monthly_bill - used_month * tariff,
        bill_saving=used_month * tariff,
        day_work=profile[workdays[0]] if workdays else None,
        day_off=profile[off_day] if off_day else None, off_day=off_day,
    )


WBP_HOURS = (18, 22)  # PLN peak block (Waktu Beban Puncak)


def bill_analysis(p):
    """What one PLN bill (rincian rekening) says about the site: average load per tariff block,
    where the money goes, and which kWh solar can replace. Returns None without a pln_bill block."""
    bills = _bills(p)
    if not bills:
        return None
    b = bills[-1]  # latest bill carries the current tariff and connection data
    days = sum(x["days"] for x in bills)
    wbp_h = WBP_HOURS[1] - WBP_HOURS[0]
    lwbp, wbp = sum(x["lwbp_kwh"] for x in bills), sum(x["wbp_kwh"] for x in bills)
    kwh = lwbp + wbp
    total_rp = sum(x["total_rp"] for x in bills)
    lwbp_rp, wbp_rp = lwbp * b["lwbp_rp_kwh"], wbp * b["wbp_rp_kwh"]
    kw_lwbp, kw_wbp = lwbp / (days * (24 - wbp_h)), wbp / (days * wbp_h)
    kw_avg = kwh / (days * 24)
    kva = b["power_va"] / 1000
    ppj = b.get("ppj_rp", 0)
    history = [dict(x, kwh=x["lwbp_kwh"] + x["wbp_kwh"], kwh_day=(x["lwbp_kwh"] + x["wbp_kwh"]) / x["days"]) for x in bills]
    return dict(
        bills=history, latest=b, n=len(bills), days=days,
        kwh=kwh, lwbp=lwbp, wbp=wbp, wbp_share=wbp / kwh,
        kwh_month=kwh / len(bills), rp_month=total_rp / len(bills),
        lwbp_rp=lwbp_rp, wbp_rp=wbp_rp, wbp_rp_share=wbp_rp / (lwbp_rp + wbp_rp),
        kw_lwbp=kw_lwbp, kw_wbp=kw_wbp, kw_avg=kw_avg, kva=kva, utilisation=kw_avg / kva,
        kwh_day=kwh / days, rp_kwh=total_rp / kwh,
        # solar only ever replaces daytime LWBP kWh, so that is the price it saves (plus PPJ on top)
        solar_rp_kwh=b["lwbp_rp_kwh"] * (1 + ppj / b["ttl_rp"]) if b.get("ttl_rp") else b["lwbp_rp_kwh"],
        flat=abs(kw_wbp - kw_lwbp) / kw_avg < 0.15,
    )


def chart_bill_day_v2(ba, fin, w=680, h=250):
    """One day, all from the bill: load at the LWBP level with the WBP block 18–22 (dashed),
    clear-day solar (amber area) and what is still bought from PLN (navy) = load − solar."""
    a, b_ = WBP_HOURS
    per = 12
    n = 24 * per
    hours = [k / per for k in range(n + 1)]
    load = [ba["kw_wbp"] if a <= t < b_ else ba["kw_lwbp"] for t in hours]
    pv = [pv_kw(t, clear_day_kwh(fin["y1_energy"]), fin.get("kva")) for t in hours]  # same clear-day curve as the load analysis
    solar = [min(l, v) for l, v in zip(load, pv)]
    grid = [l - s_ for l, s_ in zip(load, solar)]
    pl, pr, pt, pb = 46, 18, 22, 28
    hi = max(max(load), max(pv)) * 1.18
    step = nice_step(hi, 4)

    def X(t):
        return pl + t / 24 * (w - pl - pr)

    def Y(v):
        return pt + (hi - v) / hi * (h - pt - pb)

    def line(vals):
        return " ".join(f"{X(t):.1f},{Y(v):.1f}" for t, v in zip(hours, vals))

    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Profil daya harian dari tagihan PLN: beban, surya, dan PLN">']
    out.append(f'<rect x="{X(a):.1f}" y="{pt}" width="{X(b_) - X(a):.1f}" height="{h - pt - pb}" class="band-wbp"/>')
    v = 0
    while v <= hi + 1e-9:
        out.append(f'<line x1="{pl}" x2="{w - pr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="{"zero" if v == 0 else "grid"}"/>')
        out.append(f'<text x="{pl - 7}" y="{Y(v) + 3.5:.1f}" text-anchor="end" class="ax">{fnum(v)}</text>')
        v += step
    out.append(f'<text x="{pl - 7}" y="{pt - 9}" text-anchor="end" class="ax">kW</text>')
    for hh in range(0, 25, 3):
        out.append(f'<text x="{X(hh):.1f}" y="{h - 8}" text-anchor="middle" class="ax">{hh:02d}.00</text>')
    out.append(f'<polygon points="{X(0):.1f},{Y(0):.1f} {line(solar)} {X(24):.1f},{Y(0):.1f}" class="a-solar"/>')
    out.append(f'<polyline points="{line(load)}" class="ln-load"/>')
    out.append(f'<polyline points="{line(grid)}" class="ln-grid"/>')
    out.append(f'<polyline points="{line(solar)}" class="ln-solar"/>')
    out.append(f'<text x="{X(12):.1f}" y="{Y(max(solar) * 0.4):.1f}" text-anchor="middle" class="lbl on-solar">Dari surya</text>')
    k = int(7.5 * per)
    out.append(f'<text x="{X(hours[k]) - 10:.1f}" y="{Y(grid[k]) + 4:.1f}" text-anchor="end" class="lbl grid-l">Dari PLN</text>')
    out.append(f'<text x="{X(3):.1f}" y="{Y(ba["kw_lwbp"]) - 9:.1f}" text-anchor="middle" class="lbl load-l">Beban LWBP ± {fnum(ba["kw_lwbp"])} kW</text>')
    out.append(f'<text x="{X((a + b_) / 2):.1f}" y="{Y(ba["kw_wbp"]) - 9:.1f}" text-anchor="middle" class="lbl load-l">WBP ± {fnum(ba["kw_wbp"])} kW</text>')
    out.append("</svg>")
    return "\n".join(out)


def describe_days(workdays):
    if len(workdays) == 7:
        return "7 days a week"
    if workdays == DAYS[:6]:
        return "Monday – Saturday"
    if workdays == DAYS[:5]:
        return "Monday – Friday"
    return ", ".join(workdays)


def describe_days_id(workdays):
    """Working days in Indonesian: runs of 3+ days as 'Senin – Kamis', pairs as 'Sabtu, Minggu', singles as is."""
    if len(workdays) == 7:
        return "7 hari seminggu"
    if workdays == DAYS[:6]:
        return "Senin – Sabtu"
    if workdays == DAYS[:5]:
        return "Senin – Jumat"
    idx = sorted(DAYS.index(d) for d in workdays)
    runs, cur = [], []
    for i in idx:
        if cur and i == cur[-1] + 1:
            cur.append(i)
        else:
            if cur:
                runs.append(cur)
            cur = [i]
    if cur:
        runs.append(cur)
    parts = []
    for r in runs:
        names = [DAYS_ID[DAYS[i]] for i in r]
        parts.append(f"{names[0]} – {names[-1]}" if len(r) >= 3 else ", ".join(names))
    return ", ".join(parts)


# ---------------------------------------------------------------- finance
def specific_yield_of(f):
    """kWh/kWp/yr before degradation: sun hours × operating days × performance ratio.
    A project without `performance_ratio` is treated as PR 1.0, which reproduces the legacy formula."""
    return f["peak_sun_hours"] * f["operating_days"] * f.get("performance_ratio", 1.0)


def cost_plan(p):
    """Costs that come out of the savings: flat O&M and equipment replacements (priced at today's cost).
    Returns dict(om, items, warnings); items = [dict(kind, label, years, price, fallback)]."""
    f, s = p["financial"], p["system"]
    capex = p["price"]["total"]
    items, warns = [], []
    inv_years = sorted(f.get("inverter_replacement_years") or [])
    if inv_years:
        price = (s.get("inverter") or {}).get("price_rp")
        if not price:
            price = capex * 0.10
            warns.append("system.inverter.price_rp kosong: biaya penggantian inverter dipakai 10% dari investasi. Isi dari total baris inverter di BOQ.")
        items.append(dict(kind="inverter", label="Inverter", years=inv_years, price=price, fallback=not (s.get("inverter") or {}).get("price_rp")))
    batt_years = sorted(f.get("battery_replacement_years") or []) if s.get("battery_kwh") else []
    if batt_years:
        price = s.get("battery_price_rp")
        if not price:
            price = capex * 0.25
            warns.append("system.battery_price_rp kosong: biaya penggantian baterai dipakai 25% dari investasi. Isi dari baris baterai di BOQ.")
        items.append(dict(kind="battery", label="Baterai", years=batt_years, price=price, fallback=not s.get("battery_price_rp")))
    return dict(om=capex * (f.get("om_pct_of_capex") or 0) / 100, items=items, warnings=warns)


def payback_of(rows):
    """Payback in years from net cash flows: the LAST zero crossing after which the cumulative position
    stays at or above zero (a replacement can push it back under), interpolated within that year.
    None when the investment is not recovered within the horizon."""
    if not rows or rows[-1]["cum"] < 0:
        return None, None
    for r in reversed(rows):
        if r["prev"] < 0 <= r["cum"]:
            return r["year"] - 1 + (-r["prev"]) / (r["cum"] - r["prev"]), r["year"]
    return None, None


def simulate(p, escalation_pct=None, scr=1.0):
    """25-year cash flow. Per year: production (first-year degradation applies from year 1, the yearly rate
    after), energy used on site, gross saving (used × tariff), cost (O&M + replacements) and net saving."""
    f, s = p["financial"], p["system"]
    esc = (f["tariff_escalation_pct"] if escalation_pct is None else escalation_pct) / 100
    capex = p["price"]["total"]
    plan = cost_plan(p)
    repl = {}
    for it in plan["items"]:
        for y in it["years"]:
            repl[y] = repl.get(y, 0.0) + it["price"]
    sy = specific_yield_of(f)  # kWh/kWp/yr
    rows, energy, cum = [], s["capacity_kwp"] * sy, -capex
    for y in range(1, f["years"] + 1):
        deg = f.get("degradation_first_year_pct", 0) if y == 1 else f["degradation_pct"]
        energy *= 1 - deg / 100
        tariff = f["pln_tariff_rp_kwh"] * (1 + esc) ** (y - 1)
        used = energy * scr
        gross = used * tariff
        cost = plan["om"] + repl.get(y, 0.0)
        saving = gross - cost  # net
        prev = cum
        cum += saving
        rows.append(dict(year=y, deg=deg, energy=energy, used=used, tariff=tariff, gross=gross, cost=cost,
                         om=plan["om"], repl=repl.get(y, 0.0), saving=saving, cum=cum, prev=prev))
    return rows, payback_of(rows)[0], sy


def irr(flows, lo=-0.99, hi=1.0):
    """Internal rate of return by bisection (flows[0] is the investment, negative)."""
    def npv_at(r):
        return sum(cf / (1 + r) ** t for t, cf in enumerate(flows))
    if npv_at(lo) * npv_at(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv_at(lo) * npv_at(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


TREE_T_CO2_PER_YEAR = 0.022  # ~22 kg CO2 absorbed per mature tree per year


NB = " "  # no-break space, so a number never splits from its unit


def nb(s):
    """Keep a phrase on one line."""
    return s.replace(" ", NB)


def fkwp(x):
    return fnum(x, 0 if float(x).is_integer() else 1 if round(x, 1) == round(x, 2) or x >= 10 else 2)


def fdec(x):
    """4,5 · 4,83 (one decimal when that says it all, else two)"""
    return fnum(x, 1 if round(x, 1) == round(x, 2) else 2)


def fpct(x):
    """0,4 · 1 · 0,6 (a trailing ',0' is dropped)"""
    t = fnum(x, 1)
    return t[:-2] if t.endswith(",0") else t


def _years_text(years):
    ys = [str(y) for y in years]
    return ys[0] if len(ys) == 1 else ", ".join(ys[:-1]) + " dan " + ys[-1]


def describe_assumptions(p, fin):
    """Assumption sentences built from the parameters actually used, so the proposal never states
    something (e.g. 'no O&M') that the calculation did not do."""
    f, s = p["financial"], p["system"]
    pr = f.get("performance_ratio")
    d1, d = f.get("degradation_first_year_pct", 0) or 0, f["degradation_pct"]
    esc = f["tariff_escalation_pct"]
    om = f.get("om_pct_of_capex", 0) or 0
    plan = fin["plan"]
    psh, days = f["peak_sun_hours"], f["operating_days"]
    t = {"psh": fdec(psh), "om_pct": fpct(om)}
    t["tariff"] = f"tarif PLN Rp{NB}{fnum(f['pln_tariff_rp_kwh'], 2)}/kWh " + (f"naik {fpct(esc)}%/tahun" if esc else "tetap (tanpa kenaikan)")
    t["sun"] = f"iradiasi {fdec(psh)}{NB}kWh/m²/hari × PR {fpct(pr * 100)}%" if pr is not None else f"{fdec(psh)} jam matahari efektif/hari"
    t["deg"] = f"degradasi modul {fpct(d1)}% tahun 1, lalu {fpct(d)}%/tahun" if d1 else f"degradasi modul {fpct(d)}%/tahun"
    t["om"] = f"biaya O&M {fpct(om)}% investasi/tahun" if om else "biaya O&M tidak dihitung"
    repl = [f"{it['label'].lower()} tahun {_years_text(it['years'])}" for it in plan["items"]]
    t["repl"] = ("penggantian " + "; ".join(repl)) if repl else ""
    t["line"] = " · ".join(x for x in (t["tariff"], t["sun"], t["deg"], t["om"], t["repl"]) if x)
    kwp = fkwp(s["capacity_kwp"])
    if pr is not None:
        formula = f"{kwp}{NB}kWp × {fdec(psh)}{NB}kWh/m²/hari × {days} hari × PR {fpct(pr * 100)}%"
    else:
        formula = f"{kwp}{NB}kWp × {fdec(psh)} jam × {days} hari"
    if d1:
        formula += f" × (1 − {fpct(d1)}%)"
    t["prod_formula"] = formula
    used = f"{fnum(fin['y1_used'])}{NB}kWh terpakai × Rp{NB}{fnum(f['pln_tariff_rp_kwh'], 2)}/kWh"
    t["saving_formula"] = used + ("; " + nb(f"sebelum biaya O&M Rp {fshort_id(plan['om'])}/tahun") if plan["om"] else "")
    deg_short = f"degradasi {fpct(d1)}% lalu {fpct(d)}%/tahun" if d1 else f"degradasi {fpct(d)}%/tahun"
    basis = ["tarif naik " + fpct(esc) + "%/tahun" if esc else "tarif tetap", deg_short]
    if plan["om"]:
        basis.append(f"O&M {fpct(om)}%/tahun")
    if repl:
        basis.append("ganti " + "; ".join(repl))
    t["payback_basis"] = f"Rp{NB}{fshort_id(fin['capex'])} ÷ penghematan bersih per tahun, dengan " + ", ".join(basis)
    t["y1_monthly"] = f"± Rp{NB}{fshort_id(fin['monthly_saving'])} per bulan" + (" · " + nb(f"sebelum O&M Rp {fshort_id(plan['om'])}/tahun") if plan["om"] else "")
    return t


def finance(p):
    f = p["financial"]
    sy = specific_yield_of(f)
    # year-1 production = base × (1 − first-year degradation); the load analysis works on the same number
    y1 = p["system"]["capacity_kwp"] * sy * (1 - (f.get("degradation_first_year_pct", 0) or 0) / 100)
    load = load_analysis(p, y1)
    scr = load["scr"] if load else 1.0
    rows, payback, sy = simulate(p, scr=scr)
    pb_year = payback_of(rows)[1]
    plan = cost_plan(p)
    capex = p["price"]["total"]
    ef = f["grid_emission_factor_t_mwh"]
    total_energy = sum(r["energy"] for r in rows)
    total_used = sum(r["used"] for r in rows)
    total_gross = sum(r["gross"] for r in rows)
    total_cost = sum(r["cost"] for r in rows)
    disc = f.get("discount_rate_pct", 10)
    sens = []
    for e in f["sensitivity_escalation_pct"]:
        r2, pb2, _ = simulate(p, e, scr)
        sens.append(dict(esc=e, payback=pb2, net=r2[-1]["cum"], current=e == f["tariff_escalation_pct"]))
    fin = dict(
        rows=rows, payback=payback, payback_year=pb_year, specific_yield=sy, capex=capex, plan=plan,
        pr=f.get("performance_ratio"), kva=p["system"].get("capacity_kva"),
        vat=capex * p["price"]["vat_pct"] / 100,
        grand=capex * (1 + p["price"]["vat_pct"] / 100),
        y1_energy=rows[0]["energy"],
        # bill reduction is the gross figure (what the donut and the bill page compare against); costs sit in the cash flow
        y1_saving=rows[0]["gross"], monthly_saving=rows[0]["gross"] / 12, y1_cost=rows[0]["cost"],
        total_energy=total_energy, total_gross=total_gross, total_cost=total_cost,
        total_saving=total_gross - total_cost, net=rows[-1]["cum"],
        roi=rows[-1]["cum"] / capex * 100,
        lcoe=(capex + total_cost) / total_used,
        price_per_wp=capex / (p["system"]["capacity_kwp"] * 1000),
        # avoided emissions follow the kWh the site really uses; surplus is curtailed, not exported
        co2_y1=rows[0]["used"] / 1000 * ef,
        co2_total=total_used / 1000 * ef,
        sens=sens, load=load, scr=scr,
        irr=irr([-capex] + [r["saving"] for r in rows]),
        disc=disc,
        npv=sum(r["saving"] / (1 + disc / 100) ** r["year"] for r in rows) - capex,
        trees=rows[0]["used"] / 1000 * ef / TREE_T_CO2_PER_YEAR,
        y1_used=rows[0]["used"], total_used=total_used,
    )
    fin["txt"] = describe_assumptions(p, fin)
    return fin


# ---------------------------------------------------------------- charts (inline SVG)
def nice_step(span, n=5):
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def chart_cumulative(fin, w=640, h=430):
    rows = fin["rows"]
    pl, pr, pt, pb = 64, 16, 24, 34
    vmin = min(0, min(r["cum"] for r in rows), -fin["capex"])
    vmax = max(0, max(r["cum"] for r in rows))
    step = nice_step(vmax - vmin)
    lo, hi = math.floor(vmin / step) * step, math.ceil(vmax / step) * step
    n = len(rows)
    bw = (w - pl - pr) / (n + 1)

    def X(i):  # i = 0 .. n (0 = investment day)
        return pl + bw * i + bw * 0.5

    def Y(v):
        return pt + (hi - v) / (hi - lo) * (h - pt - pb)

    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Cumulative cash position over {n} years">']
    v = lo
    while v <= hi + 1:
        y = Y(v)
        out.append(f'<line x1="{pl}" x2="{w - pr}" y1="{y:.1f}" y2="{y:.1f}" stroke="{C["line"]}" stroke-width="{1.4 if v == 0 else 0.6}"'
                   f'{"" if v == 0 else " stroke-dasharray=\"2 3\""}/>')
        out.append(f'<text x="{pl - 8}" y="{y + 3.5:.1f}" text-anchor="end" class="ax">{fnum(v / 1e9, 0 if step >= 1e9 else 1)}</text>')
        v += step
    out.append(f'<text x="{pl - 8}" y="{pt - 10}" text-anchor="end" class="ax">Rp billion</text>')
    # year 0 = investment
    vals = [-fin["capex"]] + [r["cum"] for r in rows]
    for i, val in enumerate(vals):
        x0, y0, y1 = X(i) - bw * 0.36, Y(max(val, 0)), Y(min(val, 0))
        col = C["navy"] if val < 0 else C["amber"]
        op = 0.85 if val < 0 else 1
        out.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{bw * 0.72:.1f}" height="{max(y1 - y0, 0.8):.1f}" rx="1.5" fill="{col}" fill-opacity="{op}"/>')
        out.append(f'<text x="{X(i):.1f}" y="{h - pb + 16}" text-anchor="middle" class="ax{"" if i % 5 == 0 else " minor"}">{i}</text>')
    out.append(f'<text x="{(w + pl) / 2:.1f}" y="{h - 4}" text-anchor="middle" class="ax">Year of operation</text>')
    pbk = fin["payback"]
    if pbk:
        bx = X(pbk)
        out.append(f'<line x1="{bx:.1f}" x2="{bx:.1f}" y1="{pt}" y2="{h - pb}" stroke="{C["ink"]}" stroke-width="1.2" stroke-dasharray="4 3"/>')
        out.append(f'<circle cx="{bx:.1f}" cy="{Y(0):.1f}" r="5" fill="#fff" stroke="{C["ink"]}" stroke-width="2"/>')
        lbl = f"Break-even · year {fnum(pbk, 1)}"
        tw = len(lbl) * 6.4 + 20
        out.append(f'<rect x="{bx + 8:.1f}" y="{pt + 2}" width="{tw:.0f}" height="22" rx="11" fill="{C["ink"]}"/>')
        out.append(f'<text x="{bx + 8 + tw / 2:.1f}" y="{pt + 17}" text-anchor="middle" class="pill">{lbl}</text>')
    last = rows[-1]
    out.append(f'<text x="{X(n) - 2:.1f}" y="{Y(last["cum"]) - 8:.1f}" text-anchor="end" class="val">+{fshort(last["cum"])}</text>')
    out.append(f'<text x="{X(0) - bw * 0.36:.1f}" y="{Y(-fin["capex"]) + 14:.1f}" class="val">−{fshort(fin["capex"])} invested</text>')
    out.append("</svg>")
    return "\n".join(out)


def esc_upper(p, fin):
    """Comparison tariff-escalation scenario for the cash chart: the value in
    financial.escalation_range_pct = [low, high] that is not the base escalation."""
    f = p["financial"]
    rng = f.get("escalation_range_pct")
    others = [e for e in (rng or []) if e != f["tariff_escalation_pct"]]
    if not others:
        return None
    rows_alt, _, _ = simulate(p, others[0], fin["scr"])
    return (f["tariff_escalation_pct"], others[0], rows_alt)


def chart_cashline(fin, w=680, h=290, upper=None):
    """Cumulative cash position as one line: starts at -investment, crosses zero at payback.
    Below zero is tinted navy (not yet recovered), above zero amber (net gain). Direct labels, no legend."""
    rows = fin["rows"]
    n = len(rows)
    vals = [-fin["capex"]] + [r["cum"] for r in rows]
    up = None
    if upper:  # comparison escalation scenario: (pct_base, pct_alt, rows_alt)
        esc_base, esc_alt, rows_alt = upper
        up = [-fin["capex"]] + [r["cum"] for r in rows_alt]
    top = max(max(up), max(vals)) if up else max(vals)
    ceil_ = max(top, 0)  # the zero line stays inside the plot even when the investment is never recovered
    pl, pr, pt, pb = 58, (128 if up else 18), 26, 30  # with two scenarios, end labels sit right of the lines
    step = nice_step(ceil_ - min(vals), 5)
    # ceiling hugs the data (+8% for the end label) instead of rounding up a whole step
    pad = (ceil_ - min(vals)) * 0.08
    lo, hi = min(vals) - pad, ceil_ + pad

    def X(i):
        return pl + (w - pl - pr) * i / n

    def Y(v):
        return pt + (hi - v) / (hi - lo) * (h - pt - pb)

    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Posisi kas kumulatif selama {n} tahun{", balik modal tahun " + fnum(fin["payback"], 1) if fin["payback"] else ", investasi belum kembali"}">']
    unit, label = (1e9, "Rp miliar") if max(top, -min(vals)) >= 1e9 else (1e6, "Rp juta")  # homes are in millions
    dec = 0 if step >= unit else 1
    v = math.ceil(lo / step) * step
    while v <= hi + 1:
        y = Y(v)
        out.append(f'<line x1="{pl}" x2="{w - pr}" y1="{y:.1f}" y2="{y:.1f}" class="{"zero" if abs(v) < 1 else "grid"}"/>')
        out.append(f'<text x="{pl - 8}" y="{y + 3.5:.1f}" text-anchor="end" class="ax">{fnum(v / unit, dec)}</text>')
        v += step
    out.append(f'<text x="{pl - 8}" y="{pt - 12}" text-anchor="end" class="ax">{label}</text>')
    for i in range(0, n + 1, 5):
        out.append(f'<text x="{X(i):.1f}" y="{h - pb + 18}" text-anchor="middle" class="ax">{"Tahun " if i == 0 else ""}{i}</text>')
    pts = " ".join(f"{X(i):.1f},{Y(val):.1f}" for i, val in enumerate(vals))
    y0 = Y(0)
    area = f"M{X(0):.1f},{y0:.1f} L{pts.replace(' ', ' L')} L{X(n):.1f},{y0:.1f} Z"
    out.append(f'<defs><clipPath id="cl-neg"><rect x="0" y="{y0:.1f}" width="{w}" height="{h}"/></clipPath>'
               f'<clipPath id="cl-pos"><rect x="0" y="0" width="{w}" height="{y0:.1f}"/></clipPath></defs>')
    out.append(f'<path d="{area}" class="a-neg" clip-path="url(#cl-neg)"/>')
    out.append(f'<path d="{area}" class="a-pos" clip-path="url(#cl-pos)"/>')
    if up:
        upts = " ".join(f"{X(i):.1f},{Y(val):.1f}" for i, val in enumerate(up))
        back = " ".join(f"{X(i):.1f},{Y(val):.1f}" for i, val in reversed(list(enumerate(vals))))
        out.append(f'<polygon points="{upts} {back}" class="a-range"/>')
        out.append(f'<polyline points="{upts}" class="ln-alt"/>')
    out.append(f'<polyline points="{pts}" class="ln"/>')
    pbk = fin["payback"]
    if pbk:
        bx = X(pbk)
        out.append(f'<circle cx="{bx:.1f}" cy="{y0:.1f}" r="5.5" class="be-dot"/>')
        out.append(f'<text x="{bx - 4:.1f}" y="{y0 - 12:.1f}" text-anchor="end" class="lbl">Balik modal · tahun {fnum(pbk, 1)}</text>')
    out.append(f'<circle cx="{X(0):.1f}" cy="{Y(vals[0]):.1f}" r="4" class="start-dot"/>')
    out.append(f'<text x="{X(0) + 8:.1f}" y="{Y(lo) - 6:.1f}" class="lbl neg">−Rp {fshort_id(fin["capex"])} investasi awal</text>')
    out.append(f'<circle cx="{X(n):.1f}" cy="{Y(vals[-1]):.1f}" r="4.5" class="end-dot"/>')
    if up:
        out.append(f'<circle cx="{X(n):.1f}" cy="{Y(up[-1]):.1f}" r="3.5" class="alt-dot"/>')
        lx = X(n) + 12
        for val, cls, sub in ((vals[-1], "pos" if vals[-1] >= 0 else "neg", f"tarif naik {fnum(esc_base)}%/tahun"),
                              (up[-1], "alt", f"jika tarif naik {fnum(esc_alt)}%/tahun")):
            y = Y(val)
            out.append(f'<text x="{lx:.1f}" y="{y + 1:.1f}" class="lbl {cls}">{signed_rp(val)}</text>')
            out.append(f'<text x="{lx:.1f}" y="{y + 15:.1f}" class="ax">{sub}</text>')
    else:
        out.append(f'<text x="{X(n) - 4:.1f}" y="{Y(vals[-1]) - 12:.1f}" text-anchor="end" class="lbl {"pos" if vals[-1] >= 0 else "neg"}">{signed_rp(vals[-1])} setelah {n} tahun</text>')
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------- v2 charts (classes styled by templates/v2/proposal.css)
def _smooth_steps(vals, per=6):
    """Hourly step values -> finer series with eased transitions (no fake noise)."""
    n = len(vals)
    out = []
    for h in range(n):
        a, b = vals[h], vals[(h + 1) % n]
        for k in range(per):
            t = k / per
            w = 0.5 - 0.5 * math.cos(math.pi * (t - 0.5) * 2) if t > 0.5 else 0.0
            out.append(a + (b - a) * w * 0.5)
    return out


def chart_day_v2(fin, w=680, h=270):
    """Typical working day: site load (dashed), solar used (amber area), power still bought from PLN (navy line)."""
    ld = fin["load"]
    per = 6
    n = 24 * per
    pv = [pv_kw(k / per, clear_day_kwh(fin["y1_energy"]), fin.get("kva")) for k in range(n)]  # clear day, same curve as the load analysis
    load = _smooth_steps([l for l, _ in ld["day_work"]], per) if ld else [max(pv) * 1.6] * n
    solar = [min(l, v) for l, v in zip(load, pv)]
    grid = [l - s_ for l, s_ in zip(load, solar)]
    pl, pr, pt, pb = 46, 18, 22, 28
    hi = max(max(load), max(pv)) * 1.15  # headroom for the line labels, no empty top band
    step = nice_step(hi, 3)

    def X(k):
        return pl + k / (n - 1) * (w - pl - pr)

    def Y(v):
        return pt + (hi - v) / hi * (h - pt - pb)

    def line(vals):
        return " ".join(f"{X(k):.1f},{Y(v):.1f}" for k, v in enumerate(vals))

    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Profil daya hari kerja: surya dan PLN">']
    v = 0
    while v <= hi + 1e-9:
        out.append(f'<line x1="{pl}" x2="{w - pr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="{"zero" if v == 0 else "grid"}"/>')
        out.append(f'<text x="{pl - 7}" y="{Y(v) + 3.5:.1f}" text-anchor="end" class="ax">{fnum(v)}</text>')
        v += step
    out.append(f'<text x="{pl - 7}" y="{pt - 9}" text-anchor="end" class="ax">kW</text>')
    for hh in range(0, 25, 3):
        k = min(hh * per, n - 1)
        out.append(f'<text x="{X(k):.1f}" y="{h - 8}" text-anchor="middle" class="ax">{hh:02d}.00</text>')
    out.append(f'<polygon points="{X(0):.1f},{Y(0):.1f} {line(solar)} {X(n - 1):.1f},{Y(0):.1f}" class="a-solar"/>')
    out.append(f'<polyline points="{line(load)}" class="ln-load"/>')
    out.append(f'<polyline points="{line(grid)}" class="ln-grid"/>')
    out.append(f'<polyline points="{line(solar)}" class="ln-solar"/>')
    km = 12 * per
    out.append(f'<text x="{X(km):.1f}" y="{Y(solar[km] * 0.4):.1f}" text-anchor="middle" class="lbl on-solar">Dari surya</text>')
    kg = int(7.5 * per)
    out.append(f'<text x="{X(kg) - 10:.1f}" y="{Y(grid[kg]) + 4:.1f}" text-anchor="end" class="lbl grid-l">Dari PLN</text>')
    k2 = 21 * per
    out.append(f'<text x="{X(k2):.1f}" y="{Y(load[k2]) - 9:.1f}" text-anchor="middle" class="lbl load-l">Beban pabrik</text>')
    out.append("</svg>")
    return "\n".join(out)


def _donut(share, center_sub, rows, aria, w=330, h=158):
    """Half-width donut panel: amber slice = solar's share, navy = what still comes from PLN; legend on the right."""
    share = min(max(share, 0.001), 0.999)  # a 0% or 100% arc has no drawable sweep
    cx, cy, r, ri = 66, h / 2, 64, 42  # the hole carries the one number that matters

    def pt(f, rr):
        a = 2 * math.pi * f - math.pi / 2
        return cx + rr * math.cos(a), cy + rr * math.sin(a)

    def ring(f0, f1):
        (x0, y0), (x1, y1) = pt(f0, r), pt(f1, r)
        (i1x, i1y), (i0x, i0y) = pt(f1, ri), pt(f0, ri)
        large = 1 if f1 - f0 > 0.5 else 0
        return (f"M{x0:.2f},{y0:.2f} A{r},{r} 0 {large} 1 {x1:.2f},{y1:.2f} "
                f"L{i1x:.2f},{i1y:.2f} A{ri},{ri} 0 {large} 0 {i0x:.2f},{i0y:.2f} Z")

    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{aria}">']
    out.append(f'<path d="{ring(share, 1)}" class="pie-pln"/>')
    out.append(f'<path d="{ring(0, share)}" class="pie-saving"/>')
    out.append(f'<text x="{cx}" y="{cy + 5}" text-anchor="middle" class="pie-center sm">{fnum(share * 100)}%</text>')
    out.append(f'<text x="{cx}" y="{cy + 21}" text-anchor="middle" class="pie-sub">{center_sub}</text>')
    x = 150
    for i, (cls, lbl, val, sub) in enumerate(rows):
        y = 16 + i * 50
        out.append(f'<rect x="{x}" y="{y - 9}" width="10" height="10" rx="2" class="{cls}"/>')
        out.append(f'<text x="{x + 17}" y="{y}" class="pie-lbl">{lbl}</text>')
        out.append(f'<text x="{x + 17}" y="{y + 21}" class="pie-val sm{" saving" if i == 2 else ""}">{val}</text>')
        out.append(f'<text x="{x + 17}" y="{y + 35}" class="pie-sub">{sub}</text>')
        if i < 2:
            out.append(f'<line x1="{x}" x2="{w - 4}" y1="{y + 42}" y2="{y + 42}" class="grid"/>')
    out.append("</svg>")
    return "\n".join(out)


def chart_pie_v2(fin):
    """Rupiah: yearly PLN bill without solar = 100%; amber = saved by solar, navy = still paid to PLN (year 1)."""
    ld = fin["load"]
    before = ld["monthly_bill"] * 12 if ld else fin["y1_saving"]
    saved = fin["y1_saving"]
    share = saved / before
    rows = [("pie-key-none", "Tagihan PLN tanpa PLTS", f"Rp {fshort_id(before)}", "per tahun · 100%"),
            ("pie-key-pln", "Masih dibayar ke PLN", f"Rp {fshort_id(before - saved)}", f"{fnum((1 - share) * 100)}% dari tagihan"),
            ("pie-key-saving", "Dihemat oleh PLTS", f"Rp {fshort_id(saved)}", f"{fnum(share * 100)}% dari tagihan")]
    return _donut(share, "hemat tagihan", rows, f"Tagihan PLN tahun pertama: {fnum(share * 100)}% dihemat oleh PLTS")


def chart_pie_kwh(fin):
    """kWh: yearly site consumption = 100%; amber = supplied by solar and used on site, navy = still bought from PLN.
    Uses load_analysis' solar_share so the % matches the one in the Ringkasan."""
    ld = fin["load"]
    if not ld:
        return None
    total = ld["monthly_kwh"] * 12
    share = ld["solar_share"]
    solar = total * share
    rows = [("pie-key-none", "Kebutuhan listrik", fenergy(total), "per tahun · 100%"),
            ("pie-key-pln", "Masih dari PLN", fenergy(total - solar), f"{fnum((1 - share) * 100)}% dari kWh"),
            ("pie-key-saving", "Dipasok PLTS", fenergy(solar), f"{fnum(share * 100)}% dari kWh")]
    return _donut(share, "kWh dari PLTS", rows, f"Kebutuhan listrik tahun pertama: {fnum(share * 100)}% dipasok PLTS")


def chart_schedule_v2(tasks, w=680, rowh=30):
    months = math.ceil(max(t["end"] for t in tasks))
    pl, top = 200, 30
    h = top + rowh * len(tasks) + 4
    cw = (w - pl) / months
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Jadwal awal proyek">']
    for m in range(months):
        x = pl + m * cw
        out.append(f'<text x="{x + cw / 2:.1f}" y="16" text-anchor="middle" class="ax mon">Bulan {m + 1}</text>')
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="24" y2="{h}" class="grid"/>')
    out.append(f'<line x1="0" x2="{w}" y1="24" y2="24" class="zero"/>')
    for i, t in enumerate(tasks):
        y = top + i * rowh
        out.append(f'<text x="0" y="{y + rowh / 2 + 3:.1f}" class="task">{t["task"]}</text>')
        x0 = pl + t["start"] * cw
        out.append(f'<rect x="{x0:.1f}" y="{y + rowh / 2 - 6:.1f}" width="{(t["end"] - t["start"]) * cw:.1f}" height="12" rx="6" class="bar"/>')
        out.append(f'<line x1="0" x2="{w}" y1="{y + rowh:.1f}" y2="{y + rowh:.1f}" class="row"/>')
    out.append("</svg>")
    return "\n".join(out)


def chart_bill(ld, w=520, h=230):
    """Donut: the bill without solar is 100%; amber = saved by solar, navy = still paid to PLN."""
    before, after, saved = ld["monthly_bill"], ld["bill_after"], ld["bill_saving"]
    share = saved / before
    cx, cy, r, sw = 115, 115, 88, 34

    def arc(f0, f1):
        a0, a1 = 2 * math.pi * f0 - math.pi / 2, 2 * math.pi * f1 - math.pi / 2
        x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        large = 1 if f1 - f0 > 0.5 else 0
        return f"M{x0:.2f},{y0:.2f} A{r},{r} 0 {large} 1 {x1:.2f},{y1:.2f}"

    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Monthly PLN bill: {fnum(share * 100)}% saved by solar">']
    out.append(f'<path d="{arc(share + 0.004, 0.996)}" fill="none" stroke="{C["navy"]}" stroke-width="{sw}"/>')
    out.append(f'<path d="{arc(0.004, share - 0.004)}" fill="none" stroke="{C["amber"]}" stroke-width="{sw + 8}"/>')
    for f0, f1, txt, col in ((0, share, f"{fnum(share * 100)}%", C["ink"]), (share, 1, f"{fnum((1 - share) * 100)}%", "#fff")):
        mid = 2 * math.pi * (f0 + f1) / 2 - math.pi / 2
        out.append(f'<text x="{cx + r * math.cos(mid):.1f}" y="{cy + r * math.sin(mid) + 4:.1f}" text-anchor="middle" class="dn-pct" fill="{col}">{txt}</text>')
    out.append(f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" class="dn-big">−{fnum(share * 100)}%</text>')
    out.append(f'<text x="{cx}" y="{cy + 18}" text-anchor="middle" class="dn-sub">on your PLN bill</text>')
    lx = 255
    rows = [(C["grid"], "Without solar", f"Rp {fshort(before)}", "per month"),
            (C["navy"], "With solar · paid to PLN", f"Rp {fshort(after)}", f"{fnum((1 - share) * 100)}% of today's bill"),
            (C["amber"], "Saved by solar", f"Rp {fshort(saved)}", f"≈ Rp {fshort(saved * 12)} per year")]
    for i, (col, lbl, val, sub) in enumerate(rows):
        y = 30 + i * 70
        out.append(f'<rect x="{lx}" y="{y - 11}" width="12" height="12" rx="2" fill="{col}"/>')
        out.append(f'<text x="{lx + 20}" y="{y}" class="dn-lbl">{lbl}</text>')
        out.append(f'<text x="{lx + 20}" y="{y + 27}" class="dn-val" fill="{C["amber_ink"] if i == 2 else C["ink"]}">{val}</text>')
        out.append(f'<text x="{lx + 20}" y="{y + 44}" class="dn-sm">{sub}</text>')
    out.append("</svg>")
    return "\n".join(out)


_GID = [0]
N_DAY = 288  # 5-minute resolution, like inverter monitoring data


def smooth_day(hourly_load, pv_day, cloudy=False, seed=3):
    """One day of 5-minute load and PV (kW) that looks like real monitoring data:
    load wanders and jitters around the hourly profile; PV follows the sun with small
    ripples, and on a cloudy day swings hard when clouds pass. Returns load, pv, clear-sky pv."""
    rnd = random.Random(seed)
    n, per_h = N_DAY, N_DAY // 24
    step = [hourly_load[k // per_h] for k in range(n)]
    base = [sum(step[(k + d) % n] for d in range(-per_h // 2, per_h // 2 + 1)) / (per_h + 1) for k in range(n)]
    load, walk = [], 0.0
    for k in range(n):
        t = k * 24 / n
        walk = 0.97 * walk + rnd.gauss(0, 0.012)
        shape = 1 + 0.06 * math.sin(2 * math.pi * (t - 13) / 24)
        load.append(base[k] * shape * (1 + walk + rnd.gauss(0, 0.012)))
    peak = pv_day * math.pi / 24
    clear = [peak * math.sin(math.pi * (k * 24 / n - 6) / 12) if 6 <= k * 24 / n <= 18 else 0.0 for k in range(n)]
    pv = []
    cloud, storm = 1.0, [0.5]
    for k in range(n):
        t = k * 24 / n
        if cloudy:
            cloud = 0.88 * cloud + 0.12 * rnd.uniform(0.35, 1.0)
            f = cloud * 0.8
            if 12.3 <= t <= 14.8:  # passing storm cells: fast, irregular, deep swings
                if rnd.random() < 0.5:  # flip between sun breaks and dense cloud
                    storm[0] = rnd.uniform(0.85, 1.05) if storm[0] < 0.5 else rnd.uniform(0.08, 0.3)
                f = max(0.05, storm[0] + rnd.gauss(0, 0.05))
        else:
            f = 1 + rnd.gauss(0, 0.022)
            if 10.6 <= t <= 10.8 or 15.1 <= t <= 15.2:
                f *= rnd.uniform(0.8, 0.9)  # small passing clouds
        pv.append(max(0.0, clear[k] * f))
    return load, pv, clear


def chart_flow(load, pv, title, accent, w=300, h=170, hi=None, clear=None, show_load=False):
    """Monitoring-style chart: thin PLN line (red) and solar line (amber), soft fills.
    As solar rises, PLN falls by the same amount."""
    _GID[0] += 1
    gid = _GID[0]
    n = len(load)
    solar = [min(l, v) for l, v in zip(load, pv)]
    grid = [l - s_ for l, s_ in zip(load, solar)]
    surplus = any(v > l * 1.02 for l, v in zip(load, pv))
    pl, pr, pt, pb = 34, 16, 36, 20
    if hi is None:
        hi_raw = max(max(load), max(pv)) * 1.12
        hi = math.ceil(hi_raw / nice_step(hi_raw, 4)) * nice_step(hi_raw, 4)
    step = nice_step(hi, 4)

    def X(k):
        return pl + k / (n - 1) * (w - pl - pr)

    def Y(v):
        return pt + (hi - v) / hi * (h - pt - pb)

    def line(vals):
        return " ".join(f"{X(k):.1f},{Y(v):.1f}" for k, v in enumerate(vals))

    def area(vals):
        return f"{X(0):.1f},{Y(0):.1f} {line(vals)} {X(n - 1):.1f},{Y(0):.1f}"

    fg = C["ink"] if accent == C["amber"] else "#fff"
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{title}">',
           f'<defs><linearGradient id="gs{gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{C["amber"]}" stop-opacity=".38"/><stop offset="1" stop-color="{C["sun"]}" stop-opacity=".04"/></linearGradient>'
           f'<linearGradient id="gg{gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{C["grid"]}" stop-opacity=".2"/><stop offset="1" stop-color="{C["grid"]}" stop-opacity=".02"/></linearGradient></defs>',
           f'<rect x="0" y="0" width="{len(title) * 5.9 + 18:.0f}" height="17" rx="8.5" fill="{accent}"/>',
           f'<text x="9" y="12" class="pilltxt" fill="{fg}">{title}</text>',
           f'<text x="{pl - 5}" y="{pt - 7}" text-anchor="end" class="ax">kW</text>']
    v = 0
    while v <= hi + 1e-9:
        out.append(f'<line x1="{pl}" x2="{w - pr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{C["line"]}" stroke-width="{0.8 if v == 0 else 0.45}"/>')
        out.append(f'<text x="{pl - 5}" y="{Y(v) + 3:.1f}" text-anchor="end" class="ax">{fnum(v)}</text>')
        v += step
    for hh in range(0, 25, 4 if w <= 320 else 3):
        k = min(round(hh / 24 * (n - 1)), n - 1)
        out.append(f'<text x="{X(k):.1f}" y="{h - 5}" text-anchor="middle" class="ax tick">{hh:02d}:00</text>')
    out.append(f'<polygon points="{area(grid)}" fill="url(#gg{gid})"/>')
    out.append(f'<polygon points="{area(solar)}" fill="url(#gs{gid})"/>')
    if clear:
        out.append(f'<polyline points="{line(clear)}" fill="none" stroke="{C["amber"]}" stroke-width=".7" stroke-dasharray="2 2.5" opacity=".6"/>')
    if surplus:
        out.append(f'<polyline points="{line(pv)}" fill="none" stroke="{C["amber"]}" stroke-width=".8" stroke-dasharray="2 2.5"/>')
    if show_load:
        out.append(f'<polyline points="{line(load)}" fill="none" stroke="{C["muted"]}" stroke-width=".7" stroke-dasharray="3 2.5"/>')
    out.append(f'<polyline points="{line(grid)}" fill="none" stroke="{C["grid"]}" stroke-width="1.05" stroke-linejoin="round"/>')
    out.append(f'<polyline points="{line(solar)}" fill="none" stroke="{C["amber"]}" stroke-width="1.05" stroke-linejoin="round"/>')
    out.append("</svg>")
    return "\n".join(out)


def day_charts(fin):
    """Energy page charts: only when the load profile adds something the on-grid page doesn't show."""
    ld = fin["load"]
    if not ld or (not ld["day_off"] and ld["scr"] >= 0.995):
        return []
    pv_day = fin["y1_energy"] / 365
    days = [(ld["day_work"], "Working day", C["amber"])]
    if ld["day_off"]:
        days.append((ld["day_off"], f"Non-working day ({ld['off_day']})", C["navy"]))
    series = [smooth_day([l for l, _ in prof], pv_day)[:2] + (t, a) for prof, t, a in days]
    hi_raw = max(max(max(l), max(v)) for l, v, _, _ in series) * 1.15
    step = nice_step(hi_raw, 4)
    hi = math.ceil(hi_raw / step) * step
    size = dict(w=300, h=165) if len(series) > 1 else dict(w=640, h=190)
    return [chart_flow(l, v, t, a, hi=hi, show_load=True, **size) for l, v, t, a in series]


def site_day(fin, cloudy=False):
    ld = fin["load"]
    pv_day = fin["y1_energy"] / 365
    hourly = [l for l, _ in ld["day_work"]] if ld else [pv_day * math.pi / 24 * 1.6] * 24
    return smooth_day(hourly, pv_day, cloudy)


def chart_ongrid(fin, cloudy, w=300, h=170):
    load, pv, clear = site_day(fin, cloudy)
    sunny_load, sunny_pv, _ = site_day(fin)
    hi_raw = max(max(sunny_load), max(sunny_pv), max(load)) * 1.12
    step = nice_step(hi_raw, 4)
    hi = math.ceil(hi_raw / step) * step
    title = "Cloudy / rainy day" if cloudy else "Sunny day"
    return chart_flow(load, pv, title, C["navy"] if cloudy else C["amber"], w=w, h=h, hi=hi)


def chart_spark(fin, w=200, h=60):
    load, pv, _ = site_day(fin)
    n = len(load)
    hi = max(load) * 1.1
    solar = [min(l, v) for l, v in zip(load, pv)]
    grid = [l - v for l, v in zip(load, solar)]
    pts = lambda vals: " ".join(f"{k / (n - 1) * w:.1f},{h - v / hi * h:.1f}" for k, v in enumerate(vals))
    return (f'<svg viewBox="0 0 {w} {h}" class="chart"><polygon points="0,{h} {pts(solar)} {w},{h}" fill="{C["amber"]}" fill-opacity=".25"/>'
            f'<polyline points="{pts(grid)}" fill="none" stroke="{C["grid"]}" stroke-width="1"/>'
            f'<polyline points="{pts(solar)}" fill="none" stroke="{C["amber"]}" stroke-width="1"/></svg>')


def monitor_figures(fin):
    load, pv, _ = site_day(fin)
    dt = 24 / len(load)
    used = sum(min(l, v) for l, v in zip(load, pv)) * dt
    cons = sum(load) * dt
    return dict(yield_kwh=sum(pv) * dt, cons_kwh=cons, used_kwh=used, share=used / cons * 100)


def chart_schedule(p, w=640, rowh=24):
    tasks = p["schedule"]
    months = math.ceil(max(t["end"] for t in tasks))
    pl, top = 190, 30
    h = top + rowh * len(tasks) + 8
    cw = (w - pl) / months
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Preliminary schedule">']
    for m in range(months):
        x = pl + m * cw
        out.append(f'<rect x="{x:.1f}" y="0" width="{cw:.1f}" height="22" fill="{C["navy"] if m % 2 == 0 else C["navy_deep"]}"/>')
        out.append(f'<text x="{x + cw / 2:.1f}" y="15" text-anchor="middle" class="mon">Month {m + 1}</text>')
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="22" y2="{h}" stroke="{C["line"]}" stroke-width="0.8"/>')
    for i, t in enumerate(tasks):
        y = top + i * rowh
        if i % 2 == 0:
            out.append(f'<rect x="0" y="{y - 4}" width="{w}" height="{rowh}" fill="{C["bg"]}"/>')
        out.append(f'<text x="8" y="{y + 14}" class="ax2">{t["task"]}</text>')
        x0 = pl + t["start"] * cw
        out.append(f'<rect x="{x0:.1f}" y="{y + 2}" width="{(t["end"] - t["start"]) * cw:.1f}" height="16" rx="8" fill="url(#solar)"/>')
    out.insert(1, f'<defs><linearGradient id="solar" x1="0" x2="1"><stop offset="0" stop-color="{C["amber"]}"/><stop offset="1" stop-color="{C["sun"]}"/></linearGradient></defs>')
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------- pages
def boq_pages(boq, rows_per_page=25):
    """Split BOQ sections into pages; a section row counts as 1.4 rows."""
    pages, cur, used, n = [], [], 0.0, 0
    for sec in boq:
        items = list(sec["items"])
        cont = False
        while items:
            room = int(rows_per_page - used - 1.4)
            if room < 2:
                pages.append(cur)
                cur, used = [], 0.0
                continue
            chunk, items = items[:room], items[room:]
            numbered = []
            for i, it in enumerate(chunk):
                numbered.append([n + i + 1] + it)
            n += len(chunk)
            cur.append(dict(section=sec["section"] + (" (cont.)" if cont else ""), items=numbered))
            used += 1.4 + len(chunk)
            cont = True
    if cur:
        pages.append(cur)
    return pages


# ---- BOQ pagination for the v2 template. Heights (mm) are measured from built PDFs: a one-line row
# is ~6.0 mm, a section row ~8.0 mm, each extra wrapped line 4.13 mm (7.8 pt × 1.5). The table starts at
# 42.6 mm on the first page (under the H2 title) and 29 mm on continuation pages; the body ends at 278 mm.
BOQ_ROW_MM, BOQ_LINE_MM, BOQ_SEC_MM, BOQ_HEAD_MM = 6.02, 4.13, 8.0, 7.85
BOQ_TOP_FIRST, BOQ_TOP_CONT, BOQ_BOTTOM, BOQ_NOTE_MM = 42.6, 29.0, 276.0, 8.0
BOQ_ITEM_W, BOQ_SPEC_W = 44.0, 82.0  # inner widths of the fixed Item / Spesifikasi columns (CSS .boq colgroup) in mm
BOQ_MIN_ROWS, BOQ_WIDOW = 3, 5  # rows to keep with a section header · a last page with this many rows or fewer is rebalanced


def _lines(text, width_mm, pt=7.8):
    """How many lines a text takes in a column of width_mm (Helvetica metrics match Figtree within ~3%; +4% spare)."""
    words = str(text or "").split()
    if not words:
        return 1
    k = 1.04 / MM  # points -> mm, with the spare
    space = pymupdf.get_text_length(" ", fontname="helv", fontsize=pt) * k
    lines, cur = 1, 0.0
    for w in words:
        wl = pymupdf.get_text_length(w, fontname="helv", fontsize=pt) * k
        if cur and cur + space + wl > width_mm:
            lines += 1
            cur = wl
        else:
            cur = wl if not cur else cur + space + wl
    return lines


def _row_mm(it):
    """Height of one BOQ row; it = [item, qty, unit, spec]."""
    n = max(_lines(it[0], BOQ_ITEM_W), _lines(it[3], BOQ_SPEC_W))
    return BOQ_ROW_MM + (n - 1) * BOQ_LINE_MM


def boq_pages_v2(boq):
    """Split BOQ sections over pages by real heights. The first page has the H2 title (less room); a section
    never starts with fewer than 3 rows at a page bottom; the last page keeps room for the closing note and
    never ends with a handful of orphan rows (rows are pulled over from the previous page instead)."""
    def top(first):
        return (BOQ_TOP_FIRST if first else BOQ_TOP_CONT) + BOQ_HEAD_MM

    def height(page, first):
        return top(first) + sum(BOQ_SEC_MM + sum(_row_mm(i) for i in c["items"]) for c in page)

    pages, cur, first = [], [], True
    used = top(first)
    last_si = len(boq) - 1
    for si, sec in enumerate(boq):
        items = list(sec["items"])
        cont = False
        limit = BOQ_BOTTOM - (BOQ_NOTE_MM if si == last_si else 0)  # the closing note sits under the last section
        while items:
            need = BOQ_SEC_MM + sum(_row_mm(i) for i in items[:min(BOQ_MIN_ROWS, len(items))])
            if used + need > limit and cur:
                pages.append(cur)
                cur, first = [], False
                used = top(first)
                continue
            chunk = dict(sec=si, name=sec["section"], cont=cont, items=[])
            used += BOQ_SEC_MM
            while items and (not chunk["items"] or used + _row_mm(items[0]) <= limit):
                used += _row_mm(items[0])
                chunk["items"].append(items.pop(0))
            if len(items) == 1 and len(chunk["items"]) > 2:  # a single leftover row would sit alone on the next page
                back = chunk["items"].pop()
                used -= _row_mm(back)
                items.insert(0, back)
            cur.append(chunk)
            cont = True
    if cur:
        pages.append(cur)

    def n_items(page):
        return sum(len(c["items"]) for c in page)

    while len(pages) > 1 and n_items(pages[-1]) <= BOQ_WIDOW and n_items(pages[-2]) > BOQ_WIDOW + 1:
        prev, last = pages[-2], pages[-1]
        pc = prev[-1]
        it = pc["items"][-1]
        if height(last, False) + BOQ_SEC_MM * (last[0]["sec"] != pc["sec"]) + _row_mm(it) + BOQ_NOTE_MM > BOQ_BOTTOM:
            break  # the last page cannot take another row
        pc["items"].pop()
        if last[0]["sec"] == pc["sec"]:
            last[0]["items"].insert(0, it)
            last[0]["cont"] = True
        else:
            last.insert(0, dict(sec=pc["sec"], name=pc["name"], cont=pc["cont"] or bool(pc["items"]), items=[it]))
        if not pc["items"]:
            prev.pop()

    out, n = [], 0
    for page in pages:
        pg = []
        for c in page:
            numbered = [[n + i + 1] + it for i, it in enumerate(c["items"])]
            n += len(c["items"])
            pg.append(dict(section=c["name"] + (" (cont.)" if c["cont"] else ""), items=numbered))
        out.append(pg)
    return out


def datasheet_pages(p, pdir, fmt):
    """Datasheet frame pages, ds_per_page source pages each; slots are (x, y, w, h) in mm."""
    out = []
    ax, ay, aw, ah = FORMATS[fmt]["ds_area"]
    per = FORMATS[fmt]["ds_per_page"]
    gap = 10
    for ds in p.get("datasheets", []):
        src = (pdir / ds["file"]).resolve()
        if not src.exists():
            print(f"  ! datasheet not found: {src}")
            continue
        with pymupdf.open(src) as d:
            idx = list(range(len(d)))
            ratio = d[0].rect.width / d[0].rect.height
        for i in range(0, len(idx), per):
            pair = idx[i:i + per]
            sw = min(ah * ratio, (aw - gap * (len(pair) - 1)) / len(pair))
            sh = sw / ratio
            x0 = ax + (aw - (sw * len(pair) + gap * (len(pair) - 1))) / 2
            slots = [(x0 + k * (sw + gap), ay, sw, sh) for k in range(len(pair))]
            out.append(dict(title=ds["title"], src=str(src), pages=pair, slots=slots))
    return out


def build_pages(p, pdir, fmt):
    if fmt == "v2" and p["client"].get("segment") == "Residential":
        # simple version for homes: decision, system, price, materials — nothing else
        pages = [("cover", {}), ("ringkasan", {}), ("sistem", {}), ("harga", {})]
        boq = p.get("id", {}).get("boq") or p["boq"]
        for i, chunk in enumerate(boq_pages_v2(boq)):
            pages.append(("boq", dict(sections=chunk, part=i)))
        pages.append(("garansi", {}))
        pages += [("datasheet", ds) for ds in datasheet_pages(p, pdir, fmt)]
        pages.append(("penutup", {}))
        return pages
    if fmt == "v2":
        pages = [("cover", {}), ("tentang", {}), ("ringkasan", {})]
        has_bill = bool(p.get("pln_bills") or p.get("pln_bill"))
        if has_bill:
            pages.append(("tagihan", {}))  # bill + energy profile on one page, before the proposed system
        pages.append(("sistem", {}))
        if p.get("load") and not has_bill:
            pages.append(("energi", {}))
        pages += [("penghematan", {}), ("proyeksi", {}), ("harga", {}), ("lingkup", {})]
        boq = p.get("id", {}).get("boq") or p["boq"]
        for i, chunk in enumerate(boq_pages_v2(boq)):
            pages.append(("boq", dict(sections=chunk, part=i)))
        pages.append(("garansi", {}))
        for ds in datasheet_pages(p, pdir, fmt):
            pages.append(("datasheet", ds))
        pages.append(("penutup", {}))
        return pages
    if fmt == "print":
        pages = [("cover", {}), ("intro", {}), ("summary", {}), ("engineering", {}), ("ongrid", {})]
        if p.get("load"):
            pages.append(("energy", {}))
        pages += [("roi", {}), ("projection", {}), ("quotation", {}), ("scope", {})]
    else:
        pages = [("cover", {}), ("contents", {}), ("intro", {}), ("summary", {}), ("engineering", {}), ("ongrid", {}), ("monitoring", {})]
        if p.get("load"):
            pages.append(("energy", {}))
        pages += [("roi", {}), ("calc", {}), ("projection", {}), ("quotation", {}), ("scope", {})]
    for i, chunk in enumerate(boq_pages(p["boq"], FORMATS[fmt]["boq_rows"])):
        pages.append(("boq", dict(sections=chunk, part=i)))
    pages.append(("warranty", {}))
    if fmt == "slides":
        pages.append(("nextsteps", {}))
    for ds in datasheet_pages(p, pdir, fmt):
        pages.append(("datasheet", ds))
    pages.append(("contact", {}))
    return pages


# ---------------------------------------------------------------- render
def find_chrome():
    for c in CHROME_PATHS:
        if Path(c).exists():
            return c
    sys.exit("Chrome/Edge not found")


def stamp_datasheets(pdf_path, pages):
    doc = pymupdf.open(pdf_path)
    for idx, (kind, data) in enumerate(pages):
        if kind == "datasheet":
            with pymupdf.open(data["src"]) as src:
                for pg, (x, y, w, h) in zip(data["pages"], data["slots"]):
                    rect = pymupdf.Rect(x * MM, y * MM, (x + w) * MM, (y + h) * MM)
                    doc[idx].show_pdf_page(rect, src, pg, keep_proportion=True)
    tmp = pdf_path.with_suffix(".tmp.pdf")
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    tmp.replace(pdf_path)


# ---------------------------------------------------------------- back-cover honeycomb
GALLERY = ROOT / "assets" / "img" / "gallery"


def gallery_photos():
    """Project photos for the honeycomb, downscaled to keep the PDF light."""
    from PIL import Image
    cache = ROOT / "output" / ".gallery"
    cache.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(GALLERY.glob("*")):
        if f.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        dst = cache / (f.stem + ".jpg")
        if not dst.exists() or dst.stat().st_mtime < f.stat().st_mtime:
            im = Image.open(f).convert("RGB")
            im.thumbnail((900, 900))
            im.save(dst, quality=82)
        out.append(dst.as_uri())
    return out


def hex_path(cx, cy, w, r=3.2):
    """Flat-top hexagon with rounded corners, as an SVG path (mm units)."""
    h = w * math.sqrt(3) / 2
    v = [(cx - w / 2, cy), (cx - w / 4, cy - h / 2), (cx + w / 4, cy - h / 2),
         (cx + w / 2, cy), (cx + w / 4, cy + h / 2), (cx - w / 4, cy + h / 2)]
    d = ""
    for i in range(6):
        x, y = v[i]
        px, py = v[i - 1]
        nx, ny = v[(i + 1) % 6]
        L = w / 2
        a = (x + (px - x) / L * r, y + (py - y) / L * r)
        b = (x + (nx - x) / L * r, y + (ny - y) / L * r)
        d += f"{'M' if i == 0 else 'L'}{a[0]:.2f},{a[1]:.2f} Q{x:.2f},{y:.2f} {b[0]:.2f},{b[1]:.2f} "
    return d + "Z"


def _in_hex(x, y, cx, cy, w):
    """Point inside a flat-top hexagon of width w centred at (cx, cy)."""
    h = w * math.sqrt(3) / 2
    dx, dy = abs(x - cx), abs(y - cy)
    return dy <= h / 2 and dx <= w / 2 - dy / math.sqrt(3)


def back_honeycomb(page_w=210, page_h=297, big=88, small=36, gap=1.6):
    """Back cover: HME logo hexagon on top, three big hexagons down the centre
    (thank you + two contact cells), and a small honeycomb of project photos around them."""
    photos = gallery_photos()
    cx0 = page_w / 2
    H = big * math.sqrt(3) / 2
    logo_w = 50
    logo_h = logo_w * math.sqrt(3) / 2
    top = logo_h + 6
    cells = [dict(role="logo", cx=cx0, cy=logo_h / 2 + 3, w=logo_w)]
    for i, role in enumerate(("thanks", "contact1", "contact2")):
        cells.append(dict(role=role, cx=cx0, cy=top + H / 2 + i * (H + 2.5), w=big))
    big_cells = list(cells)
    # small honeycomb everywhere else
    h = small * math.sqrt(3) / 2
    solids = ["navy", "amber", "photo", "photo", "navy", "photo", "amber", "photo", "photo"]
    k = j = 0
    for c in range(-6, 7):
        cx = cx0 + c * 0.75 * small
        off = h / 2 if c % 2 else 0
        for r in range(-1, 12):
            cy = r * h + off
            if cx + small / 2 < 0 or cx - small / 2 > page_w or cy + h / 2 < 0 or cy - h / 2 > page_h:
                continue
            # skip small cells that would overlap a big one (test the 6 corners + centre)
            pts = [(cx, cy)] + [(cx + small / 2 * math.cos(math.pi / 3 * t), cy + small / 2 * math.sin(math.pi / 3 * t)) for t in range(6)]
            if any(_in_hex(x, y, bc["cx"], bc["cy"], bc["w"] + 3) for bc in big_cells for x, y in pts):
                continue
            role = solids[j % len(solids)] if photos else ("navy" if j % 2 else "amber")
            j += 1
            cell = dict(role=role, cx=cx, cy=cy, w=small)
            if role == "photo":
                cell["photo"] = photos[k % len(photos)]
                k += 1
            cells.append(cell)
    for cell in cells:
        w = cell["w"]
        hh = w * math.sqrt(3) / 2
        cell["path"] = hex_path(cell["cx"], cell["cy"], w - gap, r=2.4 if w < 50 else 4)
        cell["box"] = (cell["cx"] - w / 2, cell["cy"] - hh / 2, w, hh)
    return dict(cells=cells)


# ---------------------------------------------------------------- images (E1) and brand logos (D3)
def _optimised(src_path, px, q, flatten=False):
    """Downscale a photo/logo to print size and cache it as JPEG (PNG only when it really has transparency).
    Chrome re-encodes WebP/PNG sources losslessly, which is what made the PDFs 15-20 MB. `flatten` puts a
    transparent logo on white and stores it as JPEG, for logos that always sit on a white background. Returns a file URI."""
    from PIL import Image, ImageOps
    src_path = Path(src_path)
    if not src_path.exists():
        return src_path.as_uri()
    IMG_CACHE.mkdir(parents=True, exist_ok=True)
    tag = hashlib.md5(str(src_path.resolve()).encode("utf-8")).hexdigest()[:8]
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", src_path.stem)
    base = str(IMG_CACHE / f"{stem}-{tag}-{px}{'f' if flatten else ''}")
    mtime = src_path.stat().st_mtime
    for ext in (".jpg", ".png"):
        dst = Path(base + ext)
        if dst.exists() and dst.stat().st_mtime >= mtime:
            return dst.as_uri()
    im = Image.open(src_path)
    im.load()
    im = ImageOps.exif_transpose(im)
    has_alpha = "A" in im.getbands() or (im.mode == "P" and "transparency" in im.info)
    if has_alpha:
        im = im.convert("RGBA")
        has_alpha = im.getchannel("A").getextrema()[0] < 250
    im.thumbnail((px, px), Image.LANCZOS)  # never upscales
    if has_alpha and flatten:
        white = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(white, im)
        has_alpha = False
    if has_alpha:
        dst = Path(base + ".png")
        im.save(dst, optimize=True)
    else:
        dst = Path(base + ".jpg")
        im.convert("RGB").save(dst, "JPEG", quality=q, optimize=True)
    return dst.as_uri()


def photo(name, px=1800, q=82, flatten=False):
    """Optimised copy of assets/img/compro/<name>, as a file URI."""
    return _optimised(COMPRO / name, px, q, flatten)


def project_photo(pdir, rel, px=2000, q=88):
    """Optimised copy of an image that belongs to a project (e.g. the roof layout)."""
    return _optimised(Path(pdir) / rel, px, q)


BRAND_ALIASES = {"JA Solar": "jasolar", "Sungrow": "sungrow", "Huawei": "huawei", "Trina": "trina", "Trina Solar": "trina",
                 "Jinko": "jinko", "Jinko Solar": "jinko", "Schneider": "schneider"}


def brand_logo(brand):
    """File name (assets/img/compro/b-<slug>.webp) of a brand logo, or '' when we have no logo file.
    Drop a new b-<slug>.webp into the folder and the brand shows up; the slug is the lower-case brand
    without spaces, with or without the word 'solar'."""
    if not brand:
        return ""
    plain = re.sub(r"[^a-z0-9]", "", brand.lower())
    for slug in (BRAND_ALIASES.get(brand), plain, plain.replace("solar", "")):
        if slug and (COMPRO / f"b-{slug}.webp").exists():
            return f"b-{slug}.webp"
    return ""


# ---------------------------------------------------------------- terms with a concrete validity date (E2)
MONTHS_ID = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]


def final_terms(p):
    """id.terms with 'Harga berlaku N hari' extended by the concrete date (meta.date + N days)."""
    terms = list((p.get("id") or {}).get("terms") or p.get("terms") or [])
    try:
        issued = datetime.datetime.strptime(p["meta"]["date"], "%d %B %Y")
    except (KeyError, ValueError, TypeError):
        return terms
    out = []
    for t in terms:
        m = re.match(r"^Harga berlaku (\d+) hari", t)
        if m:
            end = issued + datetime.timedelta(days=int(m.group(1)))
            t = t[:m.end()] + f", sampai {end.day} {MONTHS_ID[end.month - 1]} {end.year}" + t[m.end():]
        out.append(t)
    return out


# ---------------------------------------------------------------- validation (E3) and irradiance table (K1)
def load_irradiance():
    try:
        return json.loads(IRRADIANCE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def validate(p, fin, pdir):
    """Warnings (Indonesian) about input that would put a wrong or odd statement in the proposal."""
    w = []
    f, s = p["financial"], p["system"]
    ID = p.get("id") or {}
    pay = ID.get("payment_terms")
    if pay:
        try:
            tot = sum(float(str(x[1]).replace("%", "").replace(",", ".").strip()) for x in pay)
            if abs(tot - 100) > 0.01:
                w.append(f"Termin pembayaran berjumlah {tot:g}%, bukan 100%.")
        except (ValueError, IndexError, TypeError):
            w.append("Termin pembayaran: persentase tidak terbaca.")
    inv = s.get("inverter") or {}
    mix = inv.get("mix")
    try:
        if mix and sum(m["qty"] for m in mix) != inv.get("qty"):
            w.append(f"Jumlah inverter {inv.get('qty')} tidak sama dengan jumlah di mix ({sum(m['qty'] for m in mix)}).")
        ac = sum(m["qty"] * m["kva"] for m in mix) if mix else inv["qty"] * inv["kva"]
        if abs(ac - s["capacity_kva"]) > 0.5:
            w.append(f"Inverter berjumlah {ac:g} kVA, tetapi capacity_kva = {s['capacity_kva']}.")
        dc = s["panel"]["qty"] * s["panel"]["wp"] / 1000
        if abs(dc - s["capacity_kwp"]) / s["capacity_kwp"] > 0.02:
            w.append(f"Modul {s['panel']['qty']} × {s['panel']['wp']} Wp = {dc:.2f} kWp, tetapi capacity_kwp = {s['capacity_kwp']}.")
    except (KeyError, TypeError, ZeroDivisionError):
        w.append("Data inverter/modul tidak lengkap, kapasitas tidak bisa dicek.")
    for t in (ID.get("terms") or []) + (p.get("terms") or []):
        if "{kota}" in t:
            w.append("Syarat dan ketentuan masih berisi '{kota}'. Isi client.city.")
            break
    if fin["payback"] is None:
        w.append(f"Investasi belum kembali dalam {f['years']} tahun. Periksa harga dan asumsi sebelum dikirim.")
    elif not 2 <= fin["payback"] <= 15:
        w.append(f"Balik modal {fin['payback']:.1f} tahun, di luar kisaran wajar 2–15 tahun. Periksa harga dan asumsi.")
    lay = (p.get("images") or {}).get("layout")
    if lay and not (Path(pdir) / lay).exists():
        w.append(f"Gambar layout tidak ditemukan: {lay}")
    w += fin["plan"]["warnings"]
    if "performance_ratio" in f and not f.get("irradiance_source") and abs(f["peak_sun_hours"] - 4.5) < 1e-9:
        city = (p.get("client") or {}).get("city")
        where = f"Kota {city} tidak ada di data/irradiance.json" if city and city not in load_irradiance() else "Iradiasi belum diisi dari tabel kota"
        w.append(f"{where}; peak_sun_hours masih 4,5 (default _base). Isi peak_sun_hours dan irradiance_source dari lokasi, "
                 "karena performance_ratio sudah dipakai.")
    return w


# ---------------------------------------------------------------- render
def render(p, fin, pdir, fmt, out_dir=None):
    pages = build_pages(p, pdir, fmt)
    years = p["financial"]["years"]
    env = Environment(loader=FileSystemLoader(ROOT / "templates" / fmt), autoescape=True)
    env.filters.update(num=fnum, rp=frp, kwp=fkwp, short=fshort_id if fmt == "v2" else fshort,
                       payback=lambda x: fpayback(x, years), payback_short=lambda x: fpayback(x, years, True))
    env.globals.update(photo=photo, pphoto=lambda rel, px=2000, q=88: project_photo(pdir, rel, px, q),
                       brand_logo=brand_logo)
    ctx = dict(p=p, fin=fin, pages=pages, total_pages=len(pages), C=C, fmt=fmt,
               root=ROOT.as_uri(), pdir=pdir.as_uri(), hexp=hex_path, terms=final_terms(p))
    if fmt == "v2":
        ba = bill_analysis(p)  # once: the bill page, its chart and the day chart all read the same analysis
        ctx.update(
            ba=ba,
            chart_cash=chart_cashline(fin, upper=esc_upper(p, fin)),
            chart_day2=chart_day_v2(fin),
            chart_pie2=chart_pie_v2(fin),
            chart_pie_kwh=chart_pie_kwh(fin),
            chart_billday2=chart_bill_day_v2(ba, fin) if ba else None,
            chart_sched2=chart_schedule_v2(p.get("id", {}).get("schedule") or p["schedule"]),
            days_id=describe_days_id(fin["load"]["workdays"]) if fin["load"] else "",
        )
    else:  # archived templates (print, slides, ...) still want their own charts
        ctx.update(
            chart_cum=chart_cumulative(fin, h=FORMATS[fmt]["cum_h"]),
            chart_ongrid=[chart_ongrid(fin, False, h=FORMATS[fmt]["ongrid_h"]), chart_ongrid(fin, True, h=FORMATS[fmt]["ongrid_h"])],
            chart_spark=chart_spark(fin),
            mon=monitor_figures(fin),
            chart_sched=chart_schedule(p, rowh=FORMATS[fmt]["sched_rowh"]),
            chart_days=day_charts(fin),
            chart_bill=chart_bill(fin["load"]) if fin["load"] else None,
            days_label=describe_days(fin["load"]["workdays"]) if fin["load"] else "",
            honey=back_honeycomb() if fmt == "print" else None,
            ba=bill_analysis(p),
        )
    html = env.get_template("proposal.html.j2").render(**ctx)
    out_dir = Path(out_dir) if out_dir else ROOT / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{pdir.name}-{fmt}"
    html_path = out_dir / f"{stem}.html"
    pdf_path = out_dir / f"{stem}.pdf"
    html_path.write_text(html, encoding="utf-8")
    subprocess.run([find_chrome(), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    "--virtual-time-budget=5000", f"--print-to-pdf={pdf_path}", html_path.as_uri()],
                   check=True, capture_output=True)
    stamp_datasheets(pdf_path, pages)
    print(f"Wrote {pdf_path} ({len(pages)} pages, {pdf_path.stat().st_size / 1e6:.1f} MB)")
    return pdf_path


def run_check(pdf_path):
    """Page review after the build: PNGs in output/.check and an OVERFLOW? flag per page (skill script)."""
    if not CHECK_PDF.exists():
        print(f"  (check_pdf.py tidak ditemukan: {CHECK_PDF})")
        return
    r = subprocess.run([sys.executable, "-I", "-X", "utf8", str(CHECK_PDF), str(pdf_path)],
                       capture_output=True, text=True, encoding="utf-8")
    print(r.stdout.rstrip())
    if r.returncode:
        print(r.stderr.rstrip())


def main(project_dir, which="v2", out_dir=None, check=True):
    pdir = (ROOT / project_dir).resolve()
    p = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
    fin = finance(p)
    pb = fin["payback"]
    pb_txt = f"{pb:.2f} yr" if pb is not None else f"> {p['financial']['years']} yr"
    print(f"Payback {pb_txt} · ROI {fin['roi']:.0f}% · LCOE Rp {fin['lcoe']:.0f}/kWh")
    for msg in validate(p, fin, pdir):
        print(f"  ! {msg}")
    for fmt in (FORMATS if which == "all" else [which]):
        pdf = render(p, fin, pdir, fmt, out_dir)
        if check and fmt == "v2":
            run_check(pdf)


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")  # a legacy Windows console must not crash on "·" or "×"
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else "projects/contoh-industri",
         args[1] if len(args) > 1 else "v2",  # v2 = standard template since 2026-10-22
         check="--no-check" not in sys.argv)
