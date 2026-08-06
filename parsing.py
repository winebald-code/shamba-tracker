"""
DroneDeploy CSV ingestion for SHAMBA Tracker.

Two jobs:
  1. Classify each annotation's HEX colour (AnnotationLabel) into the Acre
     Insights colour code (Blue/Green/Yellow/Red + Pending), by hue bucket so
     ANY colour the agronomist picks in DroneDeploy maps to the right meaning.
  2. Parse the free-text AnnotationDescription into the structured comment
     fields (Category / Observation / Likely cause / Recommendation). It is
     tolerant of formats: "Label: value" lines, " | " separated, or a plain
     paragraph (which falls back to Observation).
"""
import csv
import io
import re
import colorsys

# ---- Acre Insights annotation colour code (from the internal SOP) ----
# meaning + a canonical swatch used only when we have no real hex to show.
COLOUR_CODE = {
    "New growth":   {"swatch": "#2F6FDB", "hue": "blue",   "note": "New vegetative growth / positive development"},
    "Healthy":      {"swatch": "#3FA34D", "hue": "green",  "note": "No action needed, baseline confirmed"},
    "Monitor":      {"swatch": "#E7B416", "hue": "yellow", "note": "Discoloration or early-stage concern"},
    "Needs testing":{"swatch": "#D64550", "hue": "red",    "note": "Suspicious gap or suspected diagnosis"},
    "Pending review":{"swatch": "#6E8659","hue": "grey",   "note": "Logged, awaiting agronomist review"},
}

# suggested issue category per colour meaning (agronomist can override)
MEANING_TO_CATEGORY = {
    "New growth": "Nutrient / Vigor",
    "Healthy": "Nutrient / Vigor",
    "Monitor": "Needs Investigation",
    "Needs testing": "Pest / Disease",
    "Pending review": "Needs Investigation",
}

CATEGORIES = [
    "Irrigation", "Drainage / Soil", "Nutrient / Vigor",
    "Pest / Disease", "Planting Gap", "Needs Investigation",
]


def hex_to_meaning(hex_code):
    """Bucket a hex colour into an Acre colour-code meaning by hue."""
    if not hex_code:
        return "Pending review"
    h = hex_code.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return "Pending review"
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return "Pending review"
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    hue_deg = hue * 360.0
    if sat < 0.12 or light > 0.92 or light < 0.08:
        return "Pending review"          # near-grey / near-white / near-black
    if hue_deg < 20 or hue_deg >= 300:
        return "Needs testing"           # red / magenta / pink
    if hue_deg < 45:
        return "Needs testing"           # orange-red still reads as concern
    if hue_deg < 70:
        return "Monitor"                 # yellow
    if hue_deg < 170:
        return "Healthy"                 # green
    return "New growth"                  # cyan / blue / indigo


def colour_swatch(hex_code, meaning):
    """Prefer the real DroneDeploy hex; fall back to the canonical swatch."""
    if hex_code and re.fullmatch(r"#?[0-9a-fA-F]{3,6}", hex_code.strip()):
        c = hex_code.strip()
        return c if c.startswith("#") else "#" + c
    return COLOUR_CODE.get(meaning, COLOUR_CODE["Pending review"])["swatch"]


# ---- description parsing ----
# Every label maps to one of four output fields. Synonyms and abbreviations
# are all accepted. Detection works regardless of layout: inline on one line,
# one label per line, pipe/semicolon separated, or "Label - value".
_LABELS = {
    # category
    "category": "category", "issue category": "category", "issue": "category",
    "type": "category", "issue type": "category",
    # observation
    "observation": "observation", "observations": "observation", "obs": "observation",
    "what was seen": "observation", "what i saw": "observation",
    "what we saw": "observation", "finding": "observation", "seen": "observation",
    # cause
    "likely cause": "cause", "suspected cause": "cause", "probable cause": "cause",
    "possible cause": "cause", "root cause": "cause", "cause": "cause",
    "diagnosis": "cause", "working diagnosis": "cause",
    # recommendation
    "recommendation": "recommendation", "recommendations": "recommendation",
    "recommended action": "recommendation", "recommended": "recommendation",
    "recommend": "recommendation", "action": "recommendation",
    "next step": "recommendation", "next steps": "recommendation",
    "advice": "recommendation", "rec": "recommendation", "reco": "recommendation",
}

# Longest labels first so "likely cause" wins over "cause", etc.
_LABEL_ALT = "|".join(re.escape(k) for k in sorted(_LABELS, key=len, reverse=True))
# A label is a known word/phrase, not glued to another word (lookbehind), followed
# by ':' or '-'/'–' and optional surrounding space. Matches anywhere in the string.
_LABEL_RE = re.compile(r"(?i)(?<![A-Za-z])(" + _LABEL_ALT + r")\s*[:\-\u2013]\s*")

_STRIP_EDGES = " \t\r\n|;•·—–-"


def parse_description(text):
    """
    Split a free-text annotation comment into
    dict(category, observation, cause, recommendation).

    Handles all of these (and mixtures):
      • Inline:   "Observation : A. Likely Cause : B. Recommendation : C."
      • Per line: "Observation: A\nLikely Cause: B\nRecommendation: C"
      • Piped:    "Category: X | Observation: A | Suspected cause: B | Action: C"
      • Dashed:   "Observation - A ; Likely cause - B ; Recommendation - C"
    If no recognised labels are present, the whole text becomes the observation.
    """
    out = {"category": "", "observation": "", "cause": "", "recommendation": ""}
    if not text or not str(text).strip():
        return out
    text = str(text).strip()

    matches = list(_LABEL_RE.finditer(text))
    if not matches:
        out["observation"] = text
        return out

    # any text before the first label is treated as observation if unlabelled
    lead = text[:matches[0].start()].strip(_STRIP_EDGES)

    for i, m in enumerate(matches):
        key = _LABELS[m.group(1).strip().lower()]
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        val = text[start:end].strip(_STRIP_EDGES).strip()
        if val and not out[key]:
            out[key] = val

    if lead and not out["observation"]:
        out["observation"] = lead
    return out


def _clean(v):
    return (v or "").strip()


def parse_area(area_text):
    """Return (display_text, acres_float_or_None) from strings like '8.08 ac' or '9992.17 ft²'."""
    if not area_text:
        return "", None
    s = str(area_text).strip()
    m = re.search(r"([-\d.,]+)", s)
    if not m:
        return s, None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return s, None
    low = s.lower()
    if "ft" in low or "²" in low or "sq" in low:
        acres = num / 43560.0
        return f"{acres:.2f} ac", round(acres, 3)
    if "ha" in low:
        acres = num * 2.47105
        return f"{acres:.2f} ac", round(acres, 3)
    return f"{num:.2f} ac", round(num, 3)


def parse_csv(file_bytes):
    """
    Parse a DroneDeploy annotation CSV (bytes) into a list of finding dicts.
    Unknown/renamed columns degrade gracefully.
    """
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    findings = []
    for i, row in enumerate(reader):
        row = { (k or "").strip(): v for k, v in row.items() }
        desc = _clean(row.get("AnnotationDescription"))
        hex_code = _clean(row.get("AnnotationLabel"))
        meaning = hex_to_meaning(hex_code)
        parsed = parse_description(desc)
        area_disp, acres = parse_area(row.get("AnnotationArea") or row.get("AnnotationSurfaceArea"))

        category = parsed["category"]
        if category not in CATEGORIES:
            # try to snap a loose category string onto the enum, else suggest by colour
            snap = next((c for c in CATEGORIES if c.lower() == category.lower()), None)
            category = snap or MEANING_TO_CATEGORY.get(meaning, "Needs Investigation")

        findings.append({
            "annotation_id": _clean(row.get("AnnotationId")) or f"row-{i+1}",
            "label_hex": hex_code,
            "colour_swatch": colour_swatch(hex_code, meaning),
            "colour_meaning": meaning,
            "category": category,
            "observation": parsed["observation"],
            "likely_cause": parsed["cause"],
            "recommendation": parsed["recommendation"],
            "area_text": area_disp,
            "area_acres": acres,
            "measurement_type": _clean(row.get("AnnotationMeasurementType")),
            "geometry_type": _clean(row.get("AnnotationGeometryType")),
            "annotation_link": _clean(row.get("AnnotationLink")),
            "sort_order": i,
        })
    return findings
