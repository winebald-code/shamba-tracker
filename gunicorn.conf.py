"""
Gunicorn configuration for SHAMBA Tracker.

The bind address is built in Python from the PORT environment variable, so it
works whether the platform expands $PORT in the shell or not (this avoids the
Railway "'$PORT' is not a valid port number" error, which happens when the
start command is run without shell variable expansion).
"""
import os

# Railway (and most PaaS) inject PORT at runtime; default to 8080 locally.
bind = "0.0.0.0:" + os.environ.get("PORT", "8080")
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
timeout = 120
accesslog = "-"   # log requests to stdout
errorlog = "-"
