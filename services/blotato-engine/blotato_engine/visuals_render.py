"""Template renderer: produces real PNG assets with Pillow for the visuals API."""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image, ImageDraw, ImageFont

W_SQUARE = 1080
H_SQUARE = 1080
W_PORTRAIT = 1080
H_PORTRAIT = 1350

PALETTES = {
    "midnight": {"bg": (12, 14, 20), "fg": (240, 244, 252), "accent": (110, 231, 183)},
    "paper": {"bg": (247, 244, 238), "fg": (24, 24, 28), "accent": (176, 92, 42)},
    "chalk": {"bg": (28, 32, 30), "fg": (232, 238, 232), "accent": (168, 214, 180)},
    "whiteboard": {"bg": (252, 252, 252), "fg": (28, 34, 44), "accent": (36, 92, 176)},
    "sunset": {"bg": (38, 18, 40), "fg": (255, 238, 230), "accent": (255, 154, 108)},
}


def _font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    current = ""
    for w in words:
        trial = (current + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


def _draw_wrapped(draw, text, font, box, fill, line_gap=1.25, align="left"):
    x, y, w, h = box
    lines = _wrap(draw, text, font, w)
    line_h = int(font.size * line_gap) if hasattr(font, "size") else 40
    for i, line in enumerate(lines):
        ly = y + i * line_h
        if ly + line_h > y + h:
            break
        lx = x
        if align == "center":
            lx = x + (w - draw.textlength(line, font=font)) / 2
        draw.text((lx, ly), line, font=font, fill=fill)
    return min(len(lines) * line_h, h)


def render_quote_card(text: str, author: str = "", palette: str = "midnight") -> Image.Image:
    p = PALETTES.get(palette, PALETTES["midnight"])
    img = Image.new("RGB", (W_SQUARE, H_SQUARE), p["bg"])
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, 14, H_SQUARE], fill=p["accent"])

    quote_font = _font(66)
    body = f"\u201c{text}\u201d"
    _draw_wrapped(d, body, quote_font, (110, 260, W_SQUARE - 220, 520), p["fg"], 1.28)

    if author:
        af = _font(38)
        d.text((110, H_SQUARE - 200), f"\u2014 {author}", font=af, fill=p["accent"])

    d.text((110, 120), "NOTE", font=_font(30), fill=p["accent"])
    return img


def render_tweet_card(text: str, name: str = "Venu", handle: str = "@venugopal1902",
                      palette: str = "midnight") -> Image.Image:
    p = PALETTES.get(palette, PALETTES["midnight"])
    img = Image.new("RGB", (W_SQUARE, H_SQUARE), p["bg"])
    d = ImageDraw.Draw(img)

    pad = 80
    d.text((pad, 90), name, font=_font(44), fill=p["fg"])
    d.text((pad, 148), handle, font=_font(32), fill=(140, 150, 168))

    _draw_wrapped(d, text, _font(52), (pad, 260, W_SQUARE - pad * 2, 560), p["fg"], 1.3)

    d.line([pad, H_SQUARE - 170, W_SQUARE - pad, H_SQUARE - 170], fill=(60, 66, 78), width=2)
    d.text((pad, H_SQUARE - 130), "\u2665 1.2K     \u21bb 340     \u2691 88",
           font=_font(34), fill=(140, 150, 168))
    return img


def render_carousel_slide(title: str, body: str, index: int, total: int,
                          palette: str = "paper") -> Image.Image:
    p = PALETTES.get(palette, PALETTES["paper"])
    img = Image.new("RGB", (W_PORTRAIT, H_PORTRAIT), p["bg"])
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W_PORTRAIT, 16], fill=p["accent"])

    d.text((90, 90), f"{index}/{total}", font=_font(38), fill=p["accent"])

    h = _draw_wrapped(d, title, _font(72), (90, 200, W_PORTRAIT - 180, 340), p["fg"], 1.2)
    _draw_wrapped(d, body, _font(46), (90, 220 + h, W_PORTRAIT - 180, 700), p["fg"], 1.45)

    # progress dots
    dot_y = H_PORTRAIT - 110
    for i in range(total):
        cx = 90 + i * 46
        color = p["accent"] if i < index else (200, 196, 190)
        d.ellipse([cx, dot_y, cx + 22, dot_y + 22], fill=color)
    return img


def render_infographic(title: str, points: List[str], palette: str = "whiteboard") -> Image.Image:
    p = PALETTES.get(palette, PALETTES["whiteboard"])
    img = Image.new("RGB", (W_PORTRAIT, H_PORTRAIT), p["bg"])
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W_PORTRAIT, 14], fill=p["accent"])
    _draw_wrapped(d, title, _font(64), (80, 90, W_PORTRAIT - 160, 260), p["fg"], 1.2)

    y = 320
    for i, point in enumerate(points[:6], start=1):
        d.ellipse([80, y, 132, y + 52], fill=p["accent"])
        num_font = _font(34)
        d.text((98, y + 7), str(i), font=num_font, fill=(255, 255, 255))
        used = _draw_wrapped(d, point, _font(42), (160, y, W_PORTRAIT - 240, 220), p["fg"], 1.35)
        y += max(used, 70) + 46

    d.text((80, H_PORTRAIT - 90), "SAVE THIS", font=_font(28), fill=p["accent"])
    return img


def save(img: Image.Image, out_dir: Path, name: str) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    img.save(path, format="PNG", optimize=True)
    return path.name
