"""Render a built proposal for review and flag pages whose content runs into the footer.

Usage: py -I check_pdf.py <proposal.pdf>

Writes PNGs to <pdf dir>/.check/<pdf stem>/pNN.png and prints one line per page:
  NN  <running-head title>  [OVERFLOW?]
"""
import sys
from pathlib import Path

import pymupdf

MM = 72 / 25.4
BODY_BOTTOM, FOOT_TOP = 278 * MM, 285 * MM  # .body ends 20 mm from the edge; footer sits at 10 mm


def main(pdf):
    pdf = Path(pdf)
    out = pdf.parent / ".check" / pdf.stem
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("p*.png"):
        f.unlink()
    d = pymupdf.open(pdf)
    n = len(d)
    for i, page in enumerate(d):
        page.get_pixmap(dpi=60).save(out / f"p{i + 1:02d}.png")
        lines = [l for l in page.get_text().split("\n") if l.strip()]
        title = lines[1] if len(lines) > 1 else (lines[0] if lines else "")
        flag = ""
        if 0 < i < n - 1:
            for b in page.get_text("blocks"):
                # body text must end above the footer zone; footer blocks themselves start below FOOT_TOP
                if b[3] > BODY_BOTTOM and b[1] < FOOT_TOP - 3 * MM:
                    flag = "  OVERFLOW? teks masuk area footer"
                    break
        print(f"{i + 1:02d}  {title[:60]}{flag}")
    print(f"PNG: {out}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1])
