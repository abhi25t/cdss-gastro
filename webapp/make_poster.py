#!/usr/bin/env python3
"""Generate a print-ready waiting-room QR poster for the patient check-in app.

The QR encodes the *app URL* (e.g. https://cdss-triage.web.app). A patient scans
it with their phone's normal camera to open the questionnaire. This is distinct
from the UHID barcode the app itself scans — see CLAUDE.md / SETUP_FIREBASE.md.

One-time dependency (ops tool, not a CDSS runtime dep — kept out of
requirements.txt):

    /home/ai/pyenv/cdss/bin/pip install "qrcode[pil]"

Usage:

    /home/ai/pyenv/cdss/bin/python webapp/make_poster.py \
        --url https://cdss-triage.web.app

    # customise text / output path
    /home/ai/pyenv/cdss/bin/python webapp/make_poster.py \
        --url https://cdss-triage.web.app \
        --title "City Hospital — Gastro Check-In" \
        --subtitle "Scan with your phone camera while you wait" \
        --out webapp/poster.png

Writes a PNG (default webapp/poster.png). Pass an --out ending in .pdf to also
emit a print-ready PDF.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_H
    from PIL import Image, ImageDraw, ImageFont
except ModuleNotFoundError:  # pragma: no cover - guidance path
    sys.exit(
        'Missing dependency. Install it into the project venv with:\n'
        '    /home/ai/pyenv/cdss/bin/pip install "qrcode[pil]"'
    )

# A4 portrait at ~150 DPI — comfortable for a printed poster.
PAGE_W, PAGE_H = 1240, 1754
MARGIN = 110
TEAL = (15, 118, 110)      # matches the app theme-color (#0f766e)
INK = (17, 24, 39)
MUTED = (75, 85, 99)
WHITE = (255, 255, 255)


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Best-effort TrueType lookup with a graceful fallback to the PIL default."""
    candidates = [
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans"
        + ("-Bold" if bold else "")
        + ".ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _centered(draw: ImageDraw.ImageDraw, y: int, text: str,
              font: ImageFont.FreeTypeFont, fill) -> int:
    """Draw `text` horizontally centered at vertical position `y`; return next y."""
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    w, h = right - left, bottom - top
    draw.text(((PAGE_W - w) / 2 - left, y), text, font=font, fill=fill)
    return y + h


def build_poster(url: str, title: str, subtitle: str, footer: str) -> Image.Image:
    page = Image.new("RGB", (PAGE_W, PAGE_H), WHITE)
    draw = ImageDraw.Draw(page)

    # Top accent band.
    draw.rectangle([0, 0, PAGE_W, 24], fill=TEAL)

    y = MARGIN
    y = _centered(draw, y, title, _load_font(72, bold=True), TEAL)
    y += 28
    y = _centered(draw, y, subtitle, _load_font(40), MUTED)

    # QR — high error correction so it still scans with a logo or print smudges.
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, box_size=20, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    target = PAGE_W - 2 * MARGIN
    qr_img = qr_img.resize((target, target), Image.NEAREST)
    qr_y = y + 70
    page.paste(qr_img, ((PAGE_W - target) // 2, qr_y))
    y = qr_y + target + 50

    # Human-readable URL as a typing fallback if the camera scan fails.
    y = _centered(draw, y, "or type this address into your browser:",
                  _load_font(30), MUTED)
    y += 12
    y = _centered(draw, y, url, _load_font(38, bold=True), INK)

    if footer:
        _centered(draw, PAGE_H - MARGIN, footer, _load_font(26), MUTED)

    return page


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True,
                    help="App URL the QR opens, e.g. https://cdss-triage.web.app")
    ap.add_argument("--title", default="Patient Check-In")
    ap.add_argument("--subtitle", default="Scan with your phone camera to begin")
    ap.add_argument("--footer",
                    default="Your answers go straight to the doctor — please fill this in while you wait.")
    ap.add_argument("--out", default="webapp/poster.png",
                    help="Output path (.png; use .pdf to emit a PDF instead)")
    args = ap.parse_args()

    poster = build_poster(args.url, args.title, args.subtitle, args.footer)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".pdf":
        poster.save(out, "PDF", resolution=150.0)
    else:
        poster.save(out, "PNG")
    print(f"Wrote {out}  ({poster.width}x{poster.height})  →  QR opens: {args.url}")


if __name__ == "__main__":
    main()
