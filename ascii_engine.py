"""ascii_engine.py - high-quality image -> ASCII art conversion.

Pure function library: no I/O, no Flask, no CLI.
Dependencies: Pillow, numpy, scipy.

Public API
----------
image_to_ascii(image, output_width=120, color_mode="mono") -> dict

    mono  -> {"rows": ["...", "...", ...]}
    color -> {"rows": [[{"char": "x", "rgb": [r, g, b]}, ...], ...]}

Pipeline
--------
1. Normalise to RGB (EXIF rotation, transparency composited on a background).
2. Resize to a working grid that keeps the *original* geometry, S x S source-
   aligned pixels per character cell width, 2S per cell height (characters are
   ~2x taller than wide), so edge angles are measured correctly.
3. Auto-contrast (1st-99th percentile) + gamma on luminance.
4. Brightness -> character via a 70-glyph density ramp.
5. Sobel edges (structure tensor pooled per cell) -> override with - | / \\ .
6. Optional per-cell average colour (box filter = true block average).
"""
from __future__ import annotations

from typing import Dict, List, Union

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage

__all__ = ["image_to_ascii", "RAMP", "EDGE_CHARS", "CHAR_ASPECT", "MAX_WIDTH", "MAX_ROWS"]

# 70 glyphs ordered from darkest/densest ink to lightest (Paul Bourke's ramp).
RAMP = "$@B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/\\|()1{}[]?-_+~<>i!lI;:,\"^`'. "

# Characters used to draw edges, indexed by orientation bin:
# 0: horizontal, 1: backslash-like, 2: vertical, 3: slash-like.
EDGE_CHARS = ("-", "\\", "|", "/")

CHAR_ASPECT = 0.5   # character width / height; must be 0.5 (integer cell ratio)
MAX_WIDTH = 300     # safety limits so a huge request can't exhaust memory
MAX_ROWS = 600

_SUPERSAMPLE = 4                       # working pixels per cell horizontally
_CELL_W = _SUPERSAMPLE
_CELL_H = int(round(_SUPERSAMPLE / CHAR_ASPECT))   # 8 working pixels per cell vertically
_EDGE_BLUR_SIGMA = 1.0
_EDGE_ABS_FLOOR = 0.25                 # ignore gradients weaker than this (noise)

_RES = getattr(Image, "Resampling", Image)
_LANCZOS = _RES.LANCZOS
_BOX = _RES.BOX

_RAMP_ARR = np.array(list(RAMP))
_EDGE_ARR = np.array(EDGE_CHARS)

Row = Union[str, List[Dict]]


def _to_rgb(image: Image.Image, background: tuple) -> Image.Image:
    """Return an RGB copy: EXIF-rotated, transparency flattened onto background."""
    image = ImageOps.exif_transpose(image)
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_alpha:
        rgba = image.convert("RGBA")
        canvas = Image.new("RGBA", rgba.size, background + (255,))
        return Image.alpha_composite(canvas, rgba).convert("RGB")
    return image.convert("RGB")


def _pool(arr: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """Mean-pool a (rows*_CELL_H, cols*_CELL_W) array down to (rows, cols)."""
    return arr.reshape(rows, _CELL_H, cols, _CELL_W).mean(axis=(1, 3))


def _contrast_stretch(gray: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(gray, (1, 99))
    if hi - lo < 0.02:          # (nearly) flat image: stretching would amplify noise
        return gray
    return np.clip((gray - lo) / (hi - lo), 0.0, 1.0)


def _edge_chars(gray: np.ndarray, rows: int, cols: int, rel: float):
    """Return (mask, chars) of per-cell edge decisions.

    Uses a structure tensor pooled over each cell so that opposite-signed
    gradients on either side of a thin line reinforce instead of cancelling.
    """
    smooth = ndimage.gaussian_filter(gray, sigma=_EDGE_BLUR_SIGMA, mode="nearest")
    gx = ndimage.sobel(smooth, axis=1, mode="nearest")
    gy = ndimage.sobel(smooth, axis=0, mode="nearest")

    jxx = _pool(gx * gx, rows, cols)
    jyy = _pool(gy * gy, rows, cols)
    jxy = _pool(gx * gy, rows, cols)

    mag = np.sqrt(jxx + jyy)
    threshold = max(rel * float(np.percentile(mag, 99)), _EDGE_ABS_FLOOR)
    mask = mag > threshold

    # Gradient orientation; the edge runs perpendicular to it.
    theta = 0.5 * np.arctan2(2.0 * jxy, jxx - jyy)          # (-pi/2, pi/2]
    alpha = (np.degrees(theta) + 90.0) % 180.0              # edge direction, [0,180)
    # Image y axis points down: alpha=45 -> "\", alpha=135 -> "/".
    bins = (((alpha + 22.5) % 180.0) // 45.0).astype(np.intp)
    return mask, _EDGE_ARR[bins]


def image_to_ascii(
    image: Image.Image,
    output_width: int = 120,
    color_mode: str = "mono",
    *,
    dark_background: bool = True,
    gamma: float = 0.9,
    edges: bool = True,
    edge_threshold: float = 0.35,
) -> dict:
    """Convert a PIL image to ASCII art.

    Parameters
    ----------
    image            PIL image in any mode.
    output_width     Number of character columns (1..MAX_WIDTH).
    color_mode       "mono" or "color".
    dark_background  True: bright pixels -> dense glyphs (light text on dark page).
                     False: bright pixels -> sparse glyphs (dark text on white page).
    gamma            Applied after contrast stretch; <1 brightens midtones.
    edges            Overlay directional edge characters.
    edge_threshold   Relative edge sensitivity (lower = more edges).

    Returns
    -------
    {"rows": [str, ...]} for mono, or
    {"rows": [[{"char": str, "rgb": [r, g, b]}, ...], ...]} for color.
    """
    if not isinstance(image, Image.Image):
        raise TypeError("image must be a PIL.Image.Image")
    if color_mode not in ("mono", "color"):
        raise ValueError('color_mode must be "mono" or "color"')
    if isinstance(output_width, bool) or not isinstance(output_width, (int, np.integer)):
        raise ValueError("output_width must be an integer")
    if not 1 <= output_width <= MAX_WIDTH:
        raise ValueError(f"output_width must be between 1 and {MAX_WIDTH}")
    if gamma <= 0:
        raise ValueError("gamma must be positive")

    cols = int(output_width)
    background = (0, 0, 0) if dark_background else (255, 255, 255)
    rgb = _to_rgb(image, background)
    src_w, src_h = rgb.size
    if src_w < 1 or src_h < 1:
        raise ValueError("image has no pixels")

    rows = max(1, int(round(src_h / src_w * cols * CHAR_ASPECT)))
    if rows > MAX_ROWS:
        raise ValueError(
            f"image aspect ratio too tall for {cols} columns "
            f"({rows} rows > {MAX_ROWS}); use a smaller output_width or crop the image"
        )

    # Working grid keeps the source geometry (square working pixels).
    work = rgb.resize((cols * _CELL_W, rows * _CELL_H), _LANCZOS)
    gray = np.asarray(work.convert("L"), dtype=np.float32) / 255.0
    gray = _contrast_stretch(gray)
    gray = np.power(gray, gamma, dtype=np.float32)

    # Brightness -> glyph.
    lum = _pool(gray, rows, cols)
    n = len(RAMP)
    pos = (1.0 - lum) if dark_background else lum
    idx = np.clip(np.rint(pos * (n - 1)), 0, n - 1).astype(np.intp)
    chars = _RAMP_ARR[idx]

    # Edge overlay.
    if edges:
        mask, edge_chars = _edge_chars(gray, rows, cols, edge_threshold)
        chars = np.where(mask, edge_chars, chars)

    if color_mode == "mono":
        return {"rows": ["".join(r) for r in chars.tolist()]}

    # Colour: box filter == exact average of each source block.
    cells = np.asarray(rgb.resize((cols, rows), _BOX), dtype=np.uint8)
    char_rows = chars.tolist()
    rgb_rows = cells.tolist()
    return {
        "rows": [
            [{"char": c, "rgb": px} for c, px in zip(crow, prow)]
            for crow, prow in zip(char_rows, rgb_rows)
        ]
    }
