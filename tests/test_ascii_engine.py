"""Tests for ascii_engine. Run: python -m unittest test_ascii_engine -v  (or pytest)."""
import unittest

import numpy as np
from PIL import Image, ImageDraw

from ascii_engine import CHAR_ASPECT, EDGE_CHARS, RAMP, image_to_ascii


def make_gradient(w=200, h=100):
    row = np.linspace(0, 255, w, dtype=np.uint8)
    return Image.fromarray(np.tile(row, (h, 1))).convert("RGB")


def make_rectangle(w=200, h=100):
    img = Image.new("RGB", (w, h), "black")
    ImageDraw.Draw(img).rectangle([50, 25, 150, 75], fill="white")
    return img


def make_diagonal(slash=True, size=200):
    """White region on one side of a diagonal. slash=True -> boundary looks like '/'."""
    y, x = np.mgrid[0:size, 0:size]
    mask = (x + y > size) if slash else (x - y > 0)
    return Image.fromarray((mask * 255).astype(np.uint8)).convert("RGB")


def ramp_index(ch):
    return RAMP.index(ch)


class AsciiEngineTests(unittest.TestCase):
    def test_ramp_has_70_plus_unique_chars(self):
        self.assertGreaterEqual(len(RAMP), 70)
        self.assertEqual(len(set(RAMP)), len(RAMP))

    def test_output_shape_and_valid_chars(self):
        out = image_to_ascii(make_rectangle(), output_width=80)
        rows = out["rows"]
        expected_rows = round(100 / 200 * 80 * CHAR_ASPECT)
        self.assertEqual(len(rows), expected_rows)
        self.assertTrue(all(len(r) == 80 for r in rows))
        self.assertLessEqual(set("".join(rows)), set(RAMP) | set(EDGE_CHARS))

    def test_rectangle_edges_use_vertical_and_horizontal_chars(self):
        text = "".join(image_to_ascii(make_rectangle(), output_width=80)["rows"])
        self.assertIn("|", text)
        self.assertIn("-", text)

    def test_diagonal_edge_orientation(self):
        slash = "".join(image_to_ascii(make_diagonal(True), output_width=60)["rows"])
        back = "".join(image_to_ascii(make_diagonal(False), output_width=60)["rows"])
        self.assertGreater(slash.count("/"), 2 * slash.count("\\"))
        self.assertGreater(back.count("\\"), 2 * back.count("/"))

    def test_gradient_brightness_mapping_and_inversion(self):
        img = make_gradient()
        for dark_bg in (True, False):
            rows = image_to_ascii(img, 60, edges=False, dark_background=dark_bg)["rows"]
            mid = rows[len(rows) // 2]
            left = np.mean([ramp_index(c) for c in mid[:15]])
            right = np.mean([ramp_index(c) for c in mid[-15:]])
            if dark_bg:   # dark left -> sparse glyphs (high index); bright right -> dense
                self.assertGreater(left, right)
            else:
                self.assertLess(left, right)

    def test_color_mode_structure_and_consistency(self):
        img = make_rectangle()
        mono = image_to_ascii(img, 40, "mono")["rows"]
        color = image_to_ascii(img, 40, "color")["rows"]
        self.assertEqual(len(mono), len(color))
        for mrow, crow in zip(mono, color):
            self.assertEqual(mrow, "".join(cell["char"] for cell in crow))
            for cell in crow:
                self.assertEqual(len(cell["rgb"]), 3)
                self.assertTrue(all(isinstance(v, int) and 0 <= v <= 255 for v in cell["rgb"]))
        centre = color[len(color) // 2][20]["rgb"]
        self.assertGreater(min(centre), 240)           # inside the white rectangle
        self.assertLess(max(color[0][0]["rgb"]), 15)   # black corner

    def test_color_is_block_average(self):
        img = Image.new("RGB", (40, 40), (200, 100, 50))
        cell = image_to_ascii(img, 4, "color")["rows"][0][0]["rgb"]
        self.assertEqual(cell, [200, 100, 50])

    def test_other_image_modes(self):
        base = make_rectangle()
        variants = [
            base.convert("L"),
            base.convert("RGBA"),
            base.convert("P"),
            base.convert("1"),
            Image.new("RGBA", (50, 50), (255, 0, 0, 0)),   # fully transparent
        ]
        for img in variants:
            rows = image_to_ascii(img, 30)["rows"]
            self.assertTrue(rows and all(len(r) == 30 for r in rows), img.mode)

    def test_flat_and_tiny_images_do_not_crash(self):
        for img in (Image.new("RGB", (100, 100), "gray"), Image.new("RGB", (1, 1), "white"),
                    Image.new("RGB", (1000, 3), "white")):
            rows = image_to_ascii(img, 20)["rows"]
            self.assertGreaterEqual(len(rows), 1)
            self.assertTrue(all(len(r) == 20 for r in rows))

    def test_invalid_arguments(self):
        img = make_rectangle()
        with self.assertRaises(ValueError):
            image_to_ascii(img, 0)
        with self.assertRaises(ValueError):
            image_to_ascii(img, 10_000)
        with self.assertRaises(ValueError):
            image_to_ascii(img, 50, "rainbow")
        with self.assertRaises(ValueError):
            image_to_ascii(Image.new("RGB", (10, 5000)), 200)   # too many rows
        with self.assertRaises(TypeError):
            image_to_ascii("not an image", 50)

    def test_input_image_is_not_modified(self):
        img = make_rectangle()
        before = img.tobytes()
        image_to_ascii(img, 50, "color")
        self.assertEqual(before, img.tobytes())


if __name__ == "__main__":
    unittest.main()
