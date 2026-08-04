# SHAMBA Tracker — production image (Railway detects this Dockerfile automatically)
FROM python:3.11-slim

# System libraries WeasyPrint needs to render PDFs (Pango/Cairo/GDK-Pixbuf + fonts)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
        libcairo2 libffi8 shared-mime-info fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persist SQLite + uploads on a mounted volume in production if desired
RUN mkdir -p /app/uploads

EXPOSE 8080

# gunicorn serves the WSGI app object defined in app.py
CMD gunicorn --bind 0.0.0.0:${PORT} --workers 2 --timeout 120 app:app
