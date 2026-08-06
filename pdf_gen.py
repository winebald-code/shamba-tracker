"""
Server-side PDF rendering for the farmer report.

Uses WeasyPrint when its native libraries are available (they are, in the
provided Dockerfile). If WeasyPrint can't be imported in a given environment,
PDF_AVAILABLE is False and the app falls back to the print-friendly web
report (browser "Save as PDF"), so nothing breaks.
"""
import base64
import mimetypes
import os

try:
    from weasyprint import HTML
    PDF_AVAILABLE = True
except Exception:  # pragma: no cover - depends on system libs
    PDF_AVAILABLE = False


def data_uri(path):
    """Read a local image file into a base64 data URI (for portable embedding)."""
    if not path or not os.path.exists(path):
        return ""
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    return f"data:{mime};base64,{b64}"


_FONT_CSS = None


def font_css():
    """
    Embed Montserrat so WeasyPrint renders the report in the brand face rather
    than substituting DejaVu. The variable font covers every weight in one file,
    and base64 means no network and no fontconfig dependency.
    """
    global _FONT_CSS
    if _FONT_CSS is not None:
        return _FONT_CSS
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "static", "fonts", "Montserrat.ttf")
    if not os.path.exists(path):
        _FONT_CSS = ""
        return _FONT_CSS
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    _FONT_CSS = (
        "@font-face{font-family:'Montserrat';font-weight:100 900;font-style:normal;"
        f"src:url(data:font/ttf;base64,{b64}) format('truetype')}}"
    )
    return _FONT_CSS


def render_pdf(html_string, base_url=None):
    """Return PDF bytes from a full HTML string. Raises if PDF is unavailable."""
    if not PDF_AVAILABLE:
        raise RuntimeError("WeasyPrint is not available in this environment.")
    return HTML(string=html_string, base_url=base_url).write_pdf()
