"""
Server-side PDF rendering for the farmer report.

Uses WeasyPrint when its native libraries are available (they are, in the
provided Dockerfile). If WeasyPrint can't be imported in a given environment,
PDF_AVAILABLE is False and the app falls back to the same document in the
browser's own print path, so nothing breaks and nothing looks different.
"""
import base64
import mimetypes
import os

try:
    from weasyprint import HTML
    from weasyprint.text.fonts import FontConfiguration
    PDF_AVAILABLE = True
except Exception:  # pragma: no cover - depends on system libs
    PDF_AVAILABLE = False
    FontConfiguration = None


def data_uri(path):
    """Read a local image file into a base64 data URI (for portable embedding)."""
    if not path or not os.path.exists(path):
        return ""
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    return f"data:{mime};base64,{b64}"


def render_pdf(html_string, base_url=None):
    """
    Return PDF bytes from a full HTML string. Raises if PDF is unavailable.

    base_url gets a trailing slash: the report's @font-face rules reference the
    bundled faces relatively (static/fonts/...), and urljoin drops the last
    path segment of a base that doesn't end in one — which would silently fall
    the PDF back to a system font and change every line break.
    """
    if not PDF_AVAILABLE:
        raise RuntimeError("WeasyPrint is not available in this environment.")
    if base_url and not base_url.endswith(os.sep) and not base_url.endswith("/"):
        base_url = base_url + "/"
    font_config = FontConfiguration()
    return HTML(string=html_string, base_url=base_url).write_pdf(font_config=font_config)
