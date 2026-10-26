"""Read an HME BOQ Excel and print its contents as JSON.

Usage: py -I read_boq.py <boq.xlsx>

The BOQ files differ per project (English or Indonesian headers, an optional
"Supply By" column, the BOQ sheet not always first, a summary table on the
right, notes at the bottom). This script only extracts structure; mapping it
onto project.json is done by the /buat-proposal-cni skill.
"""
import json
import re
import sys

import openpyxl

HEAD = {  # header text (lowercase, stripped) -> field
    "no": "no", "item": "item", "supply by": "supply_by",
    "qty": "qty", "jumlah": "qty", "unit": "unit", "satuan": "unit",
    "spec": "spec", "spesifikasi": "spec", "specification": "spec",
    "price/unit": "price_unit", "harga satuan": "price_unit",
    "total price": "total", "total": "total", "total harga": "total",
}


def norm(v):
    return re.sub(r"\s+", " ", str(v)).strip().lower() if v is not None else ""


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def find_header(rows):
    for i, r in enumerate(rows):
        labels = [norm(c) for c in r]
        if "item" in labels and ({"qty", "jumlah"} & set(labels)):
            cols = {}
            for j, lab in enumerate(labels):
                if lab in HEAD and HEAD[lab] not in cols:
                    cols[HEAD[lab]] = j
            return i, cols
    return None, None


def read(path):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        h, cols = find_header(rows)
        if h is not None:
            break
    else:
        sys.exit(f"No BOQ header (Item + Qty/Jumlah) found in {path}")

    out = dict(file=str(path), sheet=ws.title, title=None, wp=None, kva=None,
               sections=[], total=None, vat=None, grand_total=None, notes=[])
    for r in rows[:h + 1]:
        for j, c in enumerate(r):
            t = norm(c)
            if "kwp" in t and out["title"] is None:
                out["title"] = str(c).strip()
            nxt = norm(r[j + 1]) if j + 1 < len(r) else ""
            if num(c) and nxt == "wp":
                out["wp"] = c
            if num(c) and nxt == "kva":
                out["kva"] = c

    last = max(cols.values())
    get = lambda r, k: r[cols[k]] if k in cols and cols[k] < len(r) else None
    sec = None
    in_notes = False
    for r in rows[h + 1:]:
        cells = r[:last + 1]
        texts = [norm(c) for c in cells]
        if in_notes:
            line = " ".join(str(c).strip() for c in r if c not in (None, "") and str(c).strip() not in ("-",))
            if line:
                out["notes"].append(line)
            continue
        if any(t.startswith("note") for t in texts):
            in_notes = True
            continue
        label = next((t for t in texts if t in ("total", "vat 11%", "vat11%", "ppn 11%", "grand total")), None)
        if label:
            val = next((c for c in r[texts.index(label) + 1:] if num(c)), None)
            key = "grand_total" if label == "grand total" else "total" if label == "total" else "vat"
            if out[key] is None:
                out[key] = val
            continue
        item, qty = get(r, "item"), get(r, "qty")
        no = get(r, "no")
        if item in (None, "") and no in (None, ""):
            continue
        if num(qty) is None and not num(no):
            name = str(item if item not in (None, "") else no).strip()
            sec = dict(section=name, items=[])
            out["sections"].append(sec)
            continue
        if sec is None:
            sec = dict(section="", items=[])
            out["sections"].append(sec)
        spec = get(r, "spec")
        sec["items"].append(dict(
            no=no, item=str(item).strip() if item is not None else "", qty=num(qty),
            unit=str(get(r, "unit") or "").strip(),
            spec=str(spec).strip() if isinstance(spec, str) else "",
            supply_by=get(r, "supply_by"),
            price_unit=num(get(r, "price_unit")), total=num(get(r, "total")),
        ))
    out["sections"] = [s for s in out["sections"] if s["items"]]
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(read(sys.argv[1]), ensure_ascii=False, indent=1))
