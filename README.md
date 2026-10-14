# GlyphX

Turning pixels into characters. Upload a photo and GlyphX rewrites it as ASCII art, in monochrome or full color.

Nothing is stored: uploads are processed in memory only and vanish when the request ends.

## Features

- Monochrome and colored ASCII output
- Adjustable resolution (20 to 300 columns)
- Edge-aware conversion: directional characters (`- | / \`) trace outlines
- Download the result as PNG or JPEG
- Drag and drop, paste, or tap to upload; sample images included
- Supports JPEG, PNG, WEBP, GIF and BMP (up to 4 MB)

## Project structure

    GlyphX/
    ├── app.py               Flask backend (/api/convert, /healthz)
    ├── ascii_engine.py      Image to ASCII conversion engine
    ├── requirements.txt
    ├── public/
    │   ├── index.html       Single-page frontend
    │   ├── samples/         Sample images
    │   └── (icons)          favicon.svg, favicon.ico, apple-touch-icon.png
    └── tests/
        ├── test_app.py
        └── test_ascii_engine.py

## Run locally

    pip install -r requirements.txt
    python app.py                      # http://127.0.0.1:5000

Try the API directly:

    curl -F image=@photo.jpg -F width=100 localhost:5000/api/convert
    curl -F image=@photo.jpg -F width=100 -F color=true localhost:5000/api/convert

## API

`POST /api/convert` (multipart form):

| Field | Description |
|---|---|
| `image` | Required. JPEG, PNG, WEBP, GIF or BMP, max 4 MB |
| `width` | 20 to 300, default 120 |
| `color` | `true` or `false`, default `false` |
| `dark_background` | `true` or `false`, default `true` |

Success: `{"mode", "width", "height", "rows"}`
Errors: `{"error": "..."}` with status 400, 413, 415, 500 or 503.

`GET /healthz` returns `{"status": "ok"}`

## Deploy to Vercel

Import the repo at vercel.com/new. Vercel detects Flask automatically (no config needed) and serves `public/` from its CDN.

## Deploy to Azure App Service (Linux, Python 3.11, F1)

Startup command:

    gunicorn --bind=0.0.0.0 --timeout 120 app:app

## Tests

    python -m unittest discover -s tests -v

## Privacy

Images are sent to the server only to be converted. They are processed in memory, nothing is written to disk, and no filenames or image data are logged.
