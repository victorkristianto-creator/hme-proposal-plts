"""HME proposal generator.

Usage:  python build.py projects/138-kemilau
Reads <project>/project.json, writes output/<slug>.html and output/<slug>.pdf
"""
import json
import math
import random
import subprocess
import sys
from pathlib import Path

import pymupdf
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]
MM = 72 / 25.4  # PDF points per mm

# Datasheet slot on a datasheet frame page (mm, must match .ds-slot in proposal.css)
DS_SLOT = dict(left=14, top=40, right=14, bottom=22)

C = dict(navy="#104674", navy_deep="#0A3055", amber="#F59A1B", sun="#FDB813",
         ink="#0B2135", muted="#4A6176", line="#D3E0EB", soft="#E7F0F7", bg="#F3F7FB",
         grid="#FF7A6B", pv="#FFB703", eng="#23A874", amber_ink="#B4650A")


# ---------------------------------------------------------------- formatting
def fnum(x, dec=0):
    """Indonesian style: 1.234.567,89"""
    s = f"{x:,.{dec}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def frp(x):
    return "Rp " + fnum(round(x))


def fshort(x):
    a = abs(x)
    if a >= 1e9:
        return f"{fnum(x / 1e9, 2)} billion"
    if a >= 1e6:
        return f"{fnum(x / 1e6, 1)} million"
    return fnum(x)


# ---------------------------------------------------------------- load matching
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKS_PER_MONTH = 365 / 7 / 12


def pv_shape():
    """Normalised hourly PV shape (sums to 1), clear-sky half-sine between 06:00 and 18:00."""
    raw = [max(0.0, math.sin(math.pi * (h + 0.5 - 6) / 12)) if 6 <= h < 18 else 0.0 for h in range(24)]
    tot = sum(raw)
    return [r / tot for r in raw]


def load_analysis(p, y1_energy):
    """Hour-by-hour week simulation of PV vs. site load. Returns None if no load data."""
    ld = p.get("load")
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
    L = week_kwh / (work_h + base * off_h)
    pv_day = y1_energy / 365
    shape = pv_shape()

    profile = {}
    used = pv = load = 0.0
    for d in DAYS:
        prof = []
        for h in range(24):
            l_h = L if is_work(d, h) else L * base
            pv_h = pv_day * shape[h]
            prof.append((l_h, pv_h))
            used += min(l_h, pv_h)
            pv += pv_h
            load += l_h
        profile[d] = prof
    scr = used / pv if pv else 0  # share of PV production used on site
    used_month = used * WEEKS_PER_MONTH
    off_day = next((d for d in DAYS if d not in workdays), None)
    return dict(
        monthly_kwh=monthly_kwh, monthly_bill=monthly_kwh * tariff,
        bill_given=bool(ld.get("monthly_bill_rp")),
        work_hours=(ws, we), workdays=workdays, base_pct=base * 100,
        load_kw=L, pv_peak_kw=pv_day * max(shape),
        scr=scr, solar_share=used / load,
        used_month=used_month, pv_month=pv * WEEKS_PER_MONTH,
        bill_after=(monthly_kwh - used_month) * tariff,
        bill_saving=used_month * tariff,
        day_work=profile[workdays[0]] if workdays else None,
        day_off=profile[off_day] if off_day else None, off_day=off_day,
    )


def describe_days(workdays):
    if len(workdays) == 7:
        return "7 days a week"
    if workdays == DAYS[:6]:
        return "Monday – Saturday"
    if workdays == DAYS[:5]:
        return "Monday – Friday"
    return ", ".join(workdays)


# ---------------------------------------------------------------- finance
def simulate(p, escalation_pct=None, scr=1.0):
    f, s = p["financial"], p["system"]
    esc = (f["tariff_escalation_pct"] if escalation_pct is None else escalation_pct) / 100
    capex = p["price"]["total"]
    om = capex * f["om_pct_of_capex"] / 100
    specific_yield = f["peak_sun_hours"] * f["operating_days"]  # kWh/kWp/yr
    rows, energy, cum = [], s["capacity_kwp"] * specific_yield, -capex
    for y in range(1, f["years"] + 1):
        deg = f["degradation_first_year_pct"] if y == 1 else f["degradation_pct"]
        if y > 1:
            energy *= 1 - deg / 100
        tariff = f["pln_tariff_rp_kwh"] * (1 + esc) ** (y - 1)
        used = energy * scr
        saving = used * tariff - om
        prev = cum
        cum += saving
        rows.append(dict(year=y, deg=deg, energy=energy, used=used, tariff=tariff, saving=saving, cum=cum, prev=prev))
    payback = None
    for r in rows:
        if r["prev"] < 0 <= r["cum"]:
            payback = r["year"] - 1 + (-r["prev"]) / r["saving"]
            break
    return rows, payback, specific_yield


def finance(p):
    f = p["financial"]
    y1 = p["system"]["capacity_kwp"] * f["peak_sun_hours"] * f["operating_days"]
    load = load_analysis(p, y1)
    scr = load["scr"] if load else 1.0
    rows, payback, sy = simulate(p, scr=scr)
    capex = p["price"]["total"]
    total_energy = sum(r["energy"] for r in rows)
    total_saving = sum(r["saving"] for r in rows)
    om_total = capex * f["om_pct_of_capex"] / 100 * f["years"]
    sens = []
    for e in f["sensitivity_escalation_pct"]:
        r2, pb2, _ = simulate(p, e, scr)
        sens.append(dict(esc=e, payback=pb2, net=r2[-1]["cum"], current=e == f["tariff_escalation_pct"]))
    return dict(
        rows=rows, payback=payback, specific_yield=sy, capex=capex,
        vat=capex * p["price"]["vat_pct"] / 100,
        grand=capex * (1 + p["price"]["vat_pct"] / 100),
        y1_energy=rows[0]["energy"], y1_saving=rows[0]["saving"], monthly_saving=rows[0]["saving"] / 12,
        total_energy=total_energy, total_saving=total_saving, net=rows[-1]["cum"],
        roi=rows[-1]["cum"] / capex * 100,
        lcoe=(capex + om_total) / sum(r["used"] for r in rows),
        price_per_wp=capex / (p["system"]["capacity_kwp"] * 1000),
        co2_y1=rows[0]["energy"] / 1000 * f["grid_emission_factor_t_mwh"],
        co2_total=total_energy / 1000 * f["grid_emission_factor_t_mwh"],
        sens=sens, load=load, scr=scr,
        y1_used=rows[0]["used"], total_used=sum(r["used"] for r in rows),
    )


# ---------------------------------------------------------------- charts (inline SVG)
def nice_step(span, n=5):
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def chart_cumulative(fin, w=640, h=290):
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
        if i % 5 == 0:
            out.append(f'<text x="{X(i):.1f}" y="{h - pb + 16}" text-anchor="middle" class="ax">{i}</text>')
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


def chart_bill(ld, w=640, h=118):
    before, after, saved = ld["monthly_bill"], ld["bill_after"], ld["bill_saving"]
    pl = 128
    bw = w - pl - 10

    def W(v):
        return bw * v / before

    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="Monthly PLN bill before and after solar">']
    out.append(f'<text x="0" y="27" class="ax2">Without solar</text>')
    out.append(f'<rect x="{pl}" y="12" width="{W(before):.1f}" height="26" rx="4" fill="{C["grid"]}"/>')
    out.append(f'<text x="{pl + W(before) - 8:.1f}" y="29.5" text-anchor="end" class="barlbl">Rp {fshort(before)} / month</text>')
    out.append(f'<text x="0" y="81" class="ax2">With solar</text>')
    out.append(f'<rect x="{pl}" y="66" width="{W(after):.1f}" height="26" rx="4" fill="{C["navy"]}"/>')
    out.append(f'<text x="{pl + 8}" y="83.5" class="barlbl" style="fill:#fff">Rp {fshort(after)}</text>')
    out.append(f'<rect x="{pl + W(after) + 2:.1f}" y="66" width="{max(W(saved) - 2, 1):.1f}" height="26" rx="4" fill="{C["amber"]}"/>')
    lbl = f"−Rp {fshort(saved)} ({fnum(saved / before * 100)}%)"
    inside = W(saved) > len(lbl) * 6.2 + 16
    if inside:
        out.append(f'<text x="{pl + W(before) - 8:.1f}" y="83.5" text-anchor="end" class="barlbl">{lbl}</text>')
    else:
        out.append(f'<text x="{pl + bw:.1f}" y="110" text-anchor="end" class="barlbl" style="fill:{C["amber_ink"]}">{lbl} saved</text>')
    out.append("</svg>")
    return "\n".join(out)


_GID = [0]


def smooth_day(hourly_load, pv_day, cloudy=False):
    """10-minute (144 pt) load and PV for one day. Load steps are softened over ~1 h."""
    raw = [hourly_load[k // 6] * (1 + 0.03 * math.sin(2 * math.pi * (k / 6 - 4) / 24)) for k in range(144)]
    load = [sum(raw[(k + d) % 144] for d in range(-3, 4)) / 7 for k in range(144)]
    peak = pv_day * math.pi / 24
    pv = [peak * math.sin(math.pi * (k / 6 - 6) / 12) if 36 <= k <= 108 else 0.0 for k in range(144)]
    clear = list(pv)
    if cloudy:
        rnd = random.Random(7)
        x = 0.8
        for k in range(144):
            x = 0.55 * x + 0.45 * rnd.uniform(0.12, 1.0)
            dim = 0.5 if 75 <= k <= 88 else 1.0
            pv[k] *= x * dim
    return load, pv, clear


def chart_flow(load, pv, title, accent, w=300, h=170, hi=None, clear=None):
    """Solar up, PLN down: PLN import line mirrors the solar line; load as thin dashed reference."""
    _GID[0] += 1
    gid = _GID[0]
    solar = [min(l, v) for l, v in zip(load, pv)]
    grid = [l - s_ for l, s_ in zip(load, solar)]
    surplus = any(v > l + 0.5 for l, v in zip(load, pv))
    pl, pr, pt, pb = 34, 30, 36, 20
    if hi is None:
        hi_raw = max(max(load), max(pv)) * 1.18
        step = nice_step(hi_raw, 4)
        hi = math.ceil(hi_raw / step) * step
    step = nice_step(hi, 4)

    def X(k):
        return pl + k / 143 * (w - pl - pr)

    def Y(v):
        return pt + (hi - v) / hi * (h - pt - pb)

    def line(vals):
        return " ".join(f"{X(k):.1f},{Y(v):.1f}" for k, v in enumerate(vals))

    def area(vals):
        return f"{X(0):.1f},{Y(0):.1f} {line(vals)} {X(143):.1f},{Y(0):.1f}"

    fg = C["ink"] if accent == C["amber"] else "#fff"
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{title}">',
           f'<defs><linearGradient id="gs{gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{C["amber"]}" stop-opacity=".55"/><stop offset="1" stop-color="{C["sun"]}" stop-opacity=".08"/></linearGradient>'
           f'<linearGradient id="gg{gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{C["grid"]}" stop-opacity=".22"/><stop offset="1" stop-color="{C["grid"]}" stop-opacity=".02"/></linearGradient></defs>',
           f'<rect x="0" y="0" width="{len(title) * 5.9 + 18:.0f}" height="17" rx="8.5" fill="{accent}"/>',
           f'<text x="9" y="12" class="pilltxt" fill="{fg}">{title}</text>',
           f'<text x="{pl - 5}" y="{pt - 8}" text-anchor="end" class="ax">kW</text>']
    v = 0
    while v <= hi + 1e-9:
        out.append(f'<line x1="{pl}" x2="{w - pr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{C["line"]}" stroke-width="{0.9 if v == 0 else 0.5}"{"" if v == 0 else " stroke-dasharray=\"2 3\""}/>')
        out.append(f'<text x="{pl - 5}" y="{Y(v) + 3:.1f}" text-anchor="end" class="ax">{fnum(v)}</text>')
        v += step
    for hh in (0, 6, 12, 18, 24):
        out.append(f'<text x="{X(min(hh * 6, 143)):.1f}" y="{h - 5}" text-anchor="middle" class="ax">{hh:02d}:00</text>')
    out.append(f'<polygon points="{area(grid)}" fill="url(#gg{gid})"/>')
    out.append(f'<polygon points="{area(solar)}" fill="url(#gs{gid})"/>')
    if clear:
        out.append(f'<polyline points="{line(clear)}" fill="none" stroke="{C["amber"]}" stroke-width="1" stroke-dasharray="2 2.5" opacity=".7"/>')
    if surplus:
        out.append(f'<polyline points="{line(pv)}" fill="none" stroke="{C["amber"]}" stroke-width="1" stroke-dasharray="2 2.5"/>')
    out.append(f'<polyline points="{line(load)}" fill="none" stroke="{C["muted"]}" stroke-width="1" stroke-dasharray="4 3"/>')
    out.append(f'<polyline points="{line(grid)}" fill="none" stroke="{C["grid"]}" stroke-width="2" stroke-linejoin="round"/>')
    out.append(f'<polyline points="{line(solar)}" fill="none" stroke="{C["amber"]}" stroke-width="2" stroke-linejoin="round"/>')
    # end-of-line labels
    out.append(f'<text x="{X(143) + 3:.1f}" y="{Y(grid[-1]) + 3:.1f}" class="lbl" fill="{C["grid"]}">PLN</text>')
    kmax = max(range(144), key=lambda k: solar[k])
    if solar[kmax] > 0 and not clear:
        out.append(f'<text x="{X(kmax):.1f}" y="{Y(solar[kmax]) - 5:.1f}" text-anchor="middle" class="lbl" fill="{C["amber_ink"]}">Solar</text>')
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
    hi_raw = max(max(max(l), max(v)) for l, v, _, _ in series) * 1.18
    step = nice_step(hi_raw, 4)
    hi = math.ceil(hi_raw / step) * step
    size = dict(w=300, h=165) if len(series) > 1 else dict(w=640, h=190)
    return [chart_flow(l, v, t, a, hi=hi, **size) for l, v, t, a in series]


def site_day(fin, cloudy=False):
    ld = fin["load"]
    pv_day = fin["y1_energy"] / 365
    hourly = [l for l, _ in ld["day_work"]] if ld else [pv_day * math.pi / 24 * 1.6] * 24
    return smooth_day(hourly, pv_day, cloudy)


def chart_ongrid(fin, cloudy, w=300, h=170):
    load, pv, clear = site_day(fin, cloudy)
    sunny_load, sunny_pv, _ = site_day(fin)
    hi_raw = max(max(sunny_load), max(sunny_pv)) * 1.18
    step = nice_step(hi_raw, 4)
    hi = math.ceil(hi_raw / step) * step
    title = "Cloudy / rainy day" if cloudy else "Sunny day"
    return chart_flow(load, pv, title, C["navy"] if cloudy else C["amber"], w=w, h=h, hi=hi, clear=clear if cloudy else None)


def chart_spark(fin, w=200, h=60):
    load, pv, _ = site_day(fin)
    hi = max(load) * 1.1
    solar = [min(l, v) for l, v in zip(load, pv)]
    grid = [l - v for l, v in zip(load, solar)]
    pts = lambda vals: " ".join(f"{k / 143 * w:.1f},{h - v / hi * h:.1f}" for k, v in enumerate(vals))
    return (f'<svg viewBox="0 0 {w} {h}" class="chart"><polygon points="0,{h} {pts(solar)} {w},{h}" fill="{C["amber"]}" fill-opacity=".3"/>'
            f'<polyline points="{pts(grid)}" fill="none" stroke="{C["grid"]}" stroke-width="1.6"/>'
            f'<polyline points="{pts(solar)}" fill="none" stroke="{C["amber"]}" stroke-width="1.6"/></svg>')


def monitor_figures(fin):
    load, pv, _ = site_day(fin)
    used = sum(min(l, v) for l, v in zip(load, pv)) / 6
    cons = sum(load) / 6
    return dict(yield_kwh=sum(pv) / 6, cons_kwh=cons, used_kwh=used, share=used / cons * 100)


def chart_schedule(p, w=640):
    tasks = p["schedule"]
    months = math.ceil(max(t["end"] for t in tasks))
    pl, rowh, top = 190, 30, 30
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
def boq_pages(boq, rows_per_page=39):
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


def datasheet_pages(p, pdir):
    out = []
    for ds in p.get("datasheets", []):
        src = (pdir / ds["file"]).resolve()
        if not src.exists():
            print(f"  ! datasheet not found: {src}")
            continue
        with pymupdf.open(src) as d:
            for i in range(len(d)):
                out.append(dict(title=ds["title"], src=str(src), page=i, sub=f"{i + 1} / {len(d)}"))
    return out


def build_pages(p, pdir):
    pages = [("cover", {}), ("overview", {}), ("engineering", {}), ("ongrid", {})]
    if p.get("load"):
        pages.append(("energy", {}))
    pages += [("roi", {}), ("savings", {}), ("quotation", {}), ("scope", {})]
    for i, chunk in enumerate(boq_pages(p["boq"])):
        pages.append(("boq", dict(sections=chunk, part=i)))
    pages.append(("warranty", {}))
    for ds in datasheet_pages(p, pdir):
        pages.append(("datasheet", ds))
    return pages


# ---------------------------------------------------------------- render
def find_chrome():
    for c in CHROME_PATHS:
        if Path(c).exists():
            return c
    sys.exit("Chrome/Edge not found")


def stamp_datasheets(pdf_path, pages):
    doc = pymupdf.open(pdf_path)
    W, H = doc[0].rect.width, doc[0].rect.height
    rect = pymupdf.Rect(DS_SLOT["left"] * MM, DS_SLOT["top"] * MM, W - DS_SLOT["right"] * MM, H - DS_SLOT["bottom"] * MM)
    for idx, (kind, data) in enumerate(pages):
        if kind == "datasheet":
            with pymupdf.open(data["src"]) as src:
                doc[idx].show_pdf_page(rect, src, data["page"], keep_proportion=True)
    tmp = pdf_path.with_suffix(".tmp.pdf")
    doc.save(tmp, garbage=3, deflate=True)
    doc.close()
    tmp.replace(pdf_path)


def main(project_dir):
    pdir = (ROOT / project_dir).resolve()
    p = json.loads((pdir / "project.json").read_text(encoding="utf-8"))
    fin = finance(p)
    pages = build_pages(p, pdir)
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True)
    env.filters.update(num=fnum, rp=frp, short=fshort)
    html = env.get_template("proposal.html.j2").render(
        p=p, fin=fin, pages=pages, total_pages=len(pages), C=C,
        chart_cum=chart_cumulative(fin),
        chart_ongrid=[chart_ongrid(fin, False), chart_ongrid(fin, True)],
        chart_spark=chart_spark(fin),
        mon=monitor_figures(fin),
        chart_sched=chart_schedule(p),
        chart_days=day_charts(fin),
        chart_bill=chart_bill(fin["load"]) if fin["load"] else None,
        days_label=describe_days(fin["load"]["workdays"]) if fin["load"] else "",
        root=ROOT.as_uri(), pdir=pdir.as_uri(),
    )
    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    slug = pdir.name
    html_path = out_dir / f"{slug}.html"
    pdf_path = out_dir / f"{slug}.pdf"
    html_path.write_text(html, encoding="utf-8")
    subprocess.run([find_chrome(), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    "--virtual-time-budget=5000", f"--print-to-pdf={pdf_path}", html_path.as_uri()],
                   check=True, capture_output=True)
    stamp_datasheets(pdf_path, pages)
    print(f"Payback {fin['payback']:.2f} yr · ROI {fin['roi']:.0f}% · LCOE Rp {fin['lcoe']:.0f}/kWh")
    print(f"Wrote {pdf_path} ({len(pages)} pages)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "projects/138-kemilau")
