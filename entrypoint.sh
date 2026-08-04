#!/bin/sh
set -e

# Fall back to 8080 if PORT isn't set by the platform.
PORT="${PORT:-8080}"

exec gunicorn --bind "0.0.0.0:${PORT}" --workers 2 --timeout 120 app:app
