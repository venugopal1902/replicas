from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import settings

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
FONT_CANDIDATES = (
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def color(value: str) -> tuple[int, int, int]:
    if not HEX_COLOR.fullmatch(value):
        raise ValueError("Colors must use #RRGGBB format")
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def load_font(size: int) -> ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def render_png(template: dict[str, Any], overrides: dict[str, dict[str, Any]], asset_paths: dict[str, Path], output: Path) -> None:
    width, height = template["width"], template["height"]
    image = Image.new("RGB", (width, height), color(template["background"]))
    draw = ImageDraw.Draw(image)
    for base_layer in template["layers"]:
        layer = {**base_layer, **overrides.get(base_layer["name"], {})}
        if layer["type"] == "text":
            font_size = int(layer.get("font_size", 48))
            draw.text((int(layer["x"]), int(layer["y"])), str(layer.get("text", "")),
                      fill=color(layer.get("color", "#ffffff")), font=load_font(font_size))
        elif layer["type"] == "image":
            asset_path = asset_paths.get(layer["asset_id"])
            if asset_path is None or not asset_path.is_file():
                raise ValueError("Image layer references a missing project asset")
            with Image.open(asset_path) as source:
                source.load()
                fitted = ImageOps.fit(source.convert("RGBA"),
                                      (int(layer["w"]), int(layer["h"])),
                                      method=Image.Resampling.LANCZOS)
                image.paste(fitted, (int(layer["x"]), int(layer["y"])), fitted)
        else:
            raise ValueError("Unsupported layer type")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG", optimize=True)
    with Image.open(output) as check:
        check.verify()


def inspect_image(data: bytes) -> tuple[str, int, int]:
    if len(data) > 5 * 1024 * 1024:
        raise ValueError("Image exceeds the 5 MiB upload limit")
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
        fmt = (image.format or "").upper()
        if fmt not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("Only PNG, JPEG, and WebP images are supported")
        with Image.open(io.BytesIO(data)) as image2:
            width, height = image2.size
    if not (1 <= width <= 8192 and 1 <= height <= 8192):
        raise ValueError("Image dimensions exceed the 8192px limit")
    return {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[fmt], width, height
