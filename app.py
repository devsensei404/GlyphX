"""app.py - Flask backend: upload an image, get ASCII art back.

Designed for Vercel (zero-config Flask) or Azure App Service (Linux, Python 3.11).
Nothing is persisted: uploads are parsed and processed entirely in memory,
nothing is written to disk, and no filenames or image data are logged.

Start (Azure):  gunicorn --bind=0.0.0.0 --timeout 120 app:app
Start (local):  python app.py      -> http://127.0.0.1:5000
"""
from __future__ import annotations

import io
import os
import threading

from flask import Flask, abort, jsonify, request, send_from_directory
from PIL import Image, UnidentifiedImageError
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from ascii_engine import MAX_WIDTH, image_to_ascii

# ---------------------------------------------------------------- settings --
MAX_UPLOAD_BYTES = 4 * 1024 * 1024            # 4 MB per image (Vercel body limit is 4.5 MB)
_MULTIPART_OVERHEAD = 64 * 1024               # boundaries/headers/form fields
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP"}
MIN_WIDTH = 20
DEFAULT_WIDTH = 120
MAX_CONCURRENT_JOBS = 2
BUSY_TIMEOUT_S = 10

Image.MAX_IMAGE_PIXELS = 25_000_000           # decompression-bomb guard


class InMemoryRequest(Flask.request_class):
    """Request that never spools uploads to temporary files on disk.

    Werkzeug normally moves any upload larger than ~500 KB into a temp file.
    We cap uploads at 4 MB, so keeping them in RAM is safe and honours the
    "nothing touches the disk" rule.
    """

    def _get_file_stream(self, total_content_length, content_type, filename=None,
                         content_length=None):
        return io.BytesIO()


# Static files live in public/. Vercel serves public/** from its CDN; the explicit
# routes below do the same job when running locally or on Azure.
app = Flask(__name__, static_folder=None)
app.static_folder = os.path.join(app.root_path, "public")
app.request_class = InMemoryRequest
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD

_slots = threading.Semaphore(MAX_CONCURRENT_JOBS)


# ------------------------------------------------------------------ errors --
class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _error(status: int, message: str):
    return jsonify({"error": message}), status


@app.errorhandler(ApiError)
def _handle_api_error(e: ApiError):
    return _error(e.status, e.message)


@app.errorhandler(RequestEntityTooLarge)
def _handle_too_large(_e):
    return _error(413, "Image too large (maximum 4 MB).")


@app.errorhandler(HTTPException)
def _handle_http_exception(e: HTTPException):
    return _error(e.code or 500, e.description or e.name)


@app.errorhandler(Exception)
def _handle_unexpected(e: Exception):
    app.logger.exception("Unhandled error")      # trace stays server-side only
    return _error(500, "Something went wrong while processing the image.")


@app.after_request
def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# ----------------------------------------------------------------- helpers --
def _parse_bool(form, name: str, default: bool) -> bool:
    raw = form.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ApiError(400, f'"{name}" must be "true" or "false".')


def _parse_width(form) -> int:
    raw = form.get("width")
    if raw is None or raw.strip() == "":
        return DEFAULT_WIDTH
    try:
        width = int(raw.strip())
    except ValueError:
        raise ApiError(400, '"width" must be a whole number.') from None
    return max(MIN_WIDTH, min(MAX_WIDTH, width))


def _decode_image(data: bytes) -> Image.Image:
    """Open + fully load an uploaded image from bytes (first frame only)."""
    try:
        img = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError:
        raise ApiError(400, "Image has too many pixels.") from None
    except UnidentifiedImageError:
        raise ApiError(415, "Unsupported file type. Upload a JPEG, PNG, WEBP, GIF or BMP image.") from None
    except (OSError, ValueError, SyntaxError):
        raise ApiError(400, "The image could not be read.") from None

    try:
        if img.format not in ALLOWED_FORMATS:
            raise ApiError(415, "Unsupported file type. Upload a JPEG, PNG, WEBP, GIF or BMP image.")
        if img.width * img.height > Image.MAX_IMAGE_PIXELS:
            raise ApiError(400, "Image has too many pixels.")
        img.load()                                # first frame; catches truncated files
        return img
    except ApiError:
        img.close()
        raise
    except (OSError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError):
        img.close()
        raise ApiError(400, "The image is corrupt or incomplete.") from None


# ------------------------------------------------------------------ routes --
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/samples/<path:filename>")
def sample(filename):
    return send_from_directory(os.path.join(app.static_folder, "samples"), filename)


ICON_FILES = {"favicon.svg", "favicon.ico", "apple-touch-icon.png"}


@app.get("/<filename>")
def icon(filename):
    if filename not in ICON_FILES:
        abort(404)
    return send_from_directory(app.static_folder, filename)


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.post("/api/convert")
def convert():
    width = _parse_width(request.form)
    color = _parse_bool(request.form, "color", False)
    dark_background = _parse_bool(request.form, "dark_background", True)

    upload = request.files.get("image")
    if upload is None:
        raise ApiError(400, 'No image uploaded (expected form field "image").')
    data = upload.read()
    if not data:
        raise ApiError(400, "The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(413, "Image too large (maximum 4 MB).")

    if not _slots.acquire(timeout=BUSY_TIMEOUT_S):
        raise ApiError(503, "Server busy, try again shortly")
    try:
        img = _decode_image(data)
        try:
            mode = "color" if color else "mono"
            try:
                result = image_to_ascii(img, width, mode, dark_background=dark_background)
            except ValueError as e:               # engine's messages are user-safe
                raise ApiError(400, str(e)) from None
        finally:
            img.close()
    finally:
        _slots.release()

    rows = result["rows"]
    return jsonify({"mode": mode, "width": width, "height": len(rows), "rows": rows})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)
