"""Tests for app.py. Run from the project root:  python -m unittest discover -s tests -v"""
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app as app_module  # noqa: E402


# ---------------------------------------------------------------- helpers ---
def image_bytes(fmt="PNG", size=(40, 30), color=(10, 200, 30), noise=False):
    if noise:
        arr = np.random.default_rng(0).integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)
        img = Image.fromarray(arr)
    else:
        img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def post(client, data=None, filename="photo.png", **fields):
    form = dict(fields)
    if data is not None:
        form["image"] = (io.BytesIO(data), filename)
    return client.post("/api/convert", data=form, content_type="multipart/form-data")


class FakeEngine:
    def __init__(self):
        self.calls = []

    def __call__(self, image, output_width=120, color_mode="mono", *, dark_background=True):
        self.calls.append({
            "size": image.size, "width": output_width, "mode": color_mode,
            "dark": dark_background, "first_pixel": image.convert("RGB").getpixel((0, 0)),
        })
        if color_mode == "color":
            return {"rows": [[{"char": "#", "rgb": [1, 2, 3]}] * 3] * 2}
        return {"rows": ["abc", "def"]}


class BaseCase(unittest.TestCase):
    def setUp(self):
        app_module.app.testing = True
        self.client = app_module.app.test_client()
        self.engine = FakeEngine()
        patcher = mock.patch.object(app_module, "image_to_ascii", self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)


# ------------------------------------------------------------------ tests ---
class ConvertSuccess(BaseCase):
    def test_mono_success(self):
        r = post(self.client, image_bytes(), width="80")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(),
                         {"mode": "mono", "width": 80, "height": 2, "rows": ["abc", "def"]})
        self.assertEqual(self.engine.calls[0]["mode"], "mono")
        self.assertTrue(self.engine.calls[0]["dark"])

    def test_color_success(self):
        r = post(self.client, image_bytes(), color="true", dark_background="false")
        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["mode"], "color")
        self.assertEqual(body["width"], 120)             # default
        self.assertEqual(body["rows"][0][0], {"char": "#", "rgb": [1, 2, 3]})
        self.assertEqual(self.engine.calls[0]["mode"], "color")
        self.assertFalse(self.engine.calls[0]["dark"])

    def test_all_allowed_formats(self):
        for fmt in ("JPEG", "PNG", "WEBP", "GIF", "BMP"):
            r = post(self.client, image_bytes(fmt))
            self.assertEqual(r.status_code, 200, fmt)

    def test_width_clamping(self):
        self.assertEqual(post(self.client, image_bytes(), width="5").get_json()["width"], 20)
        self.assertEqual(self.engine.calls[-1]["width"], 20)
        r = post(self.client, image_bytes(), width="99999")
        self.assertEqual(r.get_json()["width"], app_module.MAX_WIDTH)
        self.assertEqual(self.engine.calls[-1]["width"], app_module.MAX_WIDTH)

    def test_animated_gif_uses_first_frame(self):
        red, blue = Image.new("RGB", (20, 20), (255, 0, 0)), Image.new("RGB", (20, 20), (0, 0, 255))
        buf = io.BytesIO()
        red.save(buf, "GIF", save_all=True, append_images=[blue], duration=100, loop=0)
        r = post(self.client, buf.getvalue(), filename="a.gif")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.engine.calls[0]["first_pixel"], (255, 0, 0))


class ConvertErrors(BaseCase):
    def assertError(self, r, status):
        self.assertEqual(r.status_code, status)
        body = r.get_json()
        self.assertEqual(list(body), ["error"])
        self.assertIsInstance(body["error"], str)
        self.assertNotIn("Traceback", body["error"])

    def test_missing_file(self):
        self.assertError(post(self.client, None), 400)

    def test_empty_file(self):
        self.assertError(post(self.client, b""), 400)

    def test_bad_width(self):
        for bad in ("abc", "12.5", "-"):
            self.assertError(post(self.client, image_bytes(), width=bad), 400)

    def test_bad_bool(self):
        self.assertError(post(self.client, image_bytes(), color="maybe"), 400)
        self.assertError(post(self.client, image_bytes(), dark_background="2"), 400)

    def test_text_file_renamed_png(self):
        self.assertError(post(self.client, b"hello, I am not an image", "fake.png"), 415)

    def test_unsupported_but_real_image_format(self):
        self.assertError(post(self.client, image_bytes("TIFF"), "x.tiff"), 415)

    def test_truncated_image(self):
        data = image_bytes("PNG", (200, 200), noise=True)
        self.assertError(post(self.client, data[: len(data) // 2]), 400)

    def test_decompression_bomb(self):
        with mock.patch.object(Image, "MAX_IMAGE_PIXELS", 100):
            self.assertError(post(self.client, image_bytes(size=(50, 50))), 400)   # > 2x limit
            self.assertError(post(self.client, image_bytes(size=(12, 12))), 400)   # 1x-2x limit

    def test_oversize_upload_413(self):
        big = b"\0" * (6 * 1024 * 1024)
        self.assertError(post(self.client, big), 413)

    def test_slightly_oversize_upload_413(self):
        # Fits inside the multipart-overhead allowance but exceeds 5 MB of file data.
        data = b"\0" * (app_module.MAX_UPLOAD_BYTES + 1000)
        self.assertError(post(self.client, data), 413)

    def test_engine_value_error_is_400_with_message(self):
        with mock.patch.object(app_module, "image_to_ascii", side_effect=ValueError("too tall")):
            r = post(self.client, image_bytes())
        self.assertError(r, 400)
        self.assertEqual(r.get_json()["error"], "too tall")

    def test_unexpected_error_is_generic_500(self):
        with mock.patch.object(app_module, "image_to_ascii", side_effect=RuntimeError("secret /path")):
            with self.assertLogs(app_module.app.logger, level="ERROR"):
                r = post(self.client, image_bytes())
        self.assertError(r, 500)
        self.assertNotIn("secret", r.get_data(as_text=True))

    def test_busy_server_503(self):
        self.assertTrue(app_module._slots.acquire(timeout=1))
        self.assertTrue(app_module._slots.acquire(timeout=1))
        self.addCleanup(app_module._slots.release)
        self.addCleanup(app_module._slots.release)
        with mock.patch.object(app_module, "BUSY_TIMEOUT_S", 0.05):
            self.assertError(post(self.client, image_bytes()), 503)

    def test_slot_released_after_failure(self):
        for _ in range(5):      # more failures than slots would deadlock if slots leaked
            post(self.client, b"not an image")
            with mock.patch.object(app_module, "image_to_ascii", side_effect=ValueError("x")):
                post(self.client, image_bytes())
        self.assertEqual(post(self.client, image_bytes()).status_code, 200)

    def test_json_404_and_405(self):
        r = self.client.get("/nope")
        self.assertEqual((r.status_code, list(r.get_json())), (404, ["error"]))
        r = self.client.get("/api/convert")
        self.assertEqual((r.status_code, list(r.get_json())), (405, ["error"]))


class Misc(BaseCase):
    def test_healthz(self):
        r = self.client.get("/healthz")
        self.assertEqual((r.status_code, r.get_json()), (200, {"status": "ok"}))

    def test_cache_control_everywhere(self):
        responses = [
            self.client.get("/healthz"),
            post(self.client, image_bytes()),            # 200
            post(self.client, b"junk"),                  # 415
            post(self.client, None),                     # 400
            self.client.get("/nope"),                    # 404
        ]
        for r in responses:
            self.assertEqual(r.headers.get("Cache-Control"), "no-store", r.status_code)

    def test_index_serves_static_html(self):
        original = app_module.app.static_folder
        self.addCleanup(setattr, app_module.app, "static_folder", original)
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "index.html"), "w") as f:
                f.write("<h1>hi</h1>")
            app_module.app.static_folder = d
            r = self.client.get("/")
            self.assertEqual((r.status_code, r.get_data(as_text=True)), (200, "<h1>hi</h1>"))
            self.assertEqual(r.headers["Cache-Control"], "no-store")
            r.close()

    def test_nothing_written_to_disk(self):
        def boom(*a, **k):
            raise AssertionError("upload was spooled to a temp file")

        before = sorted(os.listdir("."))
        big = image_bytes("PNG", (700, 700), noise=True)       # well over Werkzeug's 500 KB spool limit
        self.assertGreater(len(big), 600 * 1024)
        with mock.patch("werkzeug.formparser.default_stream_factory", boom):
            r = post(self.client, big)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(before, sorted(os.listdir(".")))


class RealEngineSmoke(unittest.TestCase):
    """One end-to-end check with the real ascii_engine (no monkeypatching)."""

    def test_end_to_end(self):
        client = app_module.app.test_client()
        data = image_bytes("JPEG", (200, 100))
        r = post(client, data, "p.jpg", width="40")
        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual((body["mode"], body["width"], body["height"]), ("mono", 40, 10))
        self.assertTrue(all(len(row) == 40 for row in body["rows"]))
        r = post(client, data, "p.jpg", width="40", color="true")
        self.assertEqual(r.get_json()["rows"][0][0].keys(), {"char", "rgb"})
        r = post(client, image_bytes(size=(10, 5000)), width="200")     # too tall -> engine ValueError
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
