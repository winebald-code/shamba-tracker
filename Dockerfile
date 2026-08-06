# SHAMBA Tracker — production image (Railway detects this Dockerfile automatically)
FROM python:3.11-slim

# System libraries WeasyPrint needs to render PDFs (Pango/Cairo/GDK-Pixbuf + fonts)
# libpangoft2 and libglib are pulled in by pango but named explicitly so a base
# image change cannot silently drop them: without one of these WeasyPrint fails
# to import and every PDF download degrades to the browser print dialog.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 \
        libgdk-pixbuf-2.0-0 libcairo2 libffi8 libglib2.0-0 \
        shared-mime-info fonts-dejavu-core fonts-liberation \
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

# Port is resolved inside gunicorn.conf.py from $PORT, so no shell expansion is
# needed here — this avoids the "'$PORT' is not a valid port number" error.
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
