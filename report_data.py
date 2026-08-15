"""
Report analytics for SHAMBA Tracker.

The farmer-facing report has one job: convince the person who owns the field
that the flight found something real, and that acting this week is worth it.
Numbers do that better than paragraphs, so everything the report needs to draw
its charts is computed here, once, and handed to the template as plain data.

Nothing in this module touches the database beyond reading the flight it is
given, and nothing raises: a field with no acreage on file still gets a report,
it just gets the count-based view instead of the acreage-based one.
"""
import math

# ---------------------------------------------------------------- severity
# The Acre colour code (parsing.COLOUR_CODE) is what the agronomist draws on
# the map, so the report inherits it directly: a red zone on the map is a red
# row in the report. Anything unrecognised is treated as "watch" — never as
# "good", because silently upgrading an unknown finding would flatter the
# field.
MEANING_SEVERITY = {
    "Needs testing":  "urgent",
    "Monitor":        "watch",
    "Pending review": "watch",
    "Healthy":        "good",
    "New growth":     "good",
}

SEVERITY = {
    "urgent": {
        "key": "urgent",
        "label": "Needs action now",
        "short": "Urgent",
        "colour": "#D64550",
        "soft": "#FBEAEB",
        "line": "#F0C2C5",
        "weight": 0.0,        # contributes nothing to the health score
        "rank": 0,
    },
    "watch": {
        "key": "watch",
        "label": "Worth watching",
        "short": "Watch",
        "colour": "#E7B416",
        "soft": "#FDF4DC",
        "line": "#F2DFA4",
        "weight": 0.55,
        "rank": 1,
    },
    "good": {
        "key": "good",
        "label": "Doing well",
        "short": "Good",
        "colour": "#3FA34D",
        "soft": "#E7F1DB",
        "line": "#CBE3B4",
        "weight": 1.0,
        "rank": 2,
    },
    "clear": {                # the part of the field nobody flagged
        "key": "clear",
        "label": "Nothing flagged",
        "short": "Clear",
        "colour": "#B0D48C",
        "soft": "#F2F5EF",
        "line": "#D6DBD5",
        "weight": 1.0,
        "rank": 3,
    },
}

BAND_ORDER = ["urgent", "watch", "good", "clear"]

# What each action band tells the farmer to do, and by when.
URGENCY_COPY = {
    "urgent": ("Do this first", "Within 3 days"),
    "watch":  ("Keep watching", "Within 2 weeks"),
    "good":   ("Nothing to do", "Confirmed healthy"),
}


# How many ranked actions the cover sheet has room for.
COVER_ACTIONS = 5


def ac(value):
    """Format an acreage the way a farmer writes it: 42, not 42.0."""
    if value is None:
        return "—"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    return str(int(round(value))) if abs(value - round(value)) < 0.05 else f"{value:.1f}"


def severity_of(finding):
    """urgent / watch / good for one finding. Resolved work never counts against."""
    if getattr(finding, "resolved", False):
        return "good"
    return MEANING_SEVERITY.get((finding.colour_meaning or "").strip(), "watch")


def score_band(score):
    """Plain-language reading of the 0-100 field health score."""
    if score is None:
        return ("Not scored", "#6C6C6C")
    if score >= 85:
        return ("Strong", "#3FA34D")
    if score >= 70:
        return ("Steady", "#7FB65E")
    if score >= 50:
        return ("Under pressure", "#E7B416")
    return ("Needs work", "#D64550")


# ---------------------------------------------------------------- analytics
def analyse(flight, prev=None):
    """
    Everything the report draws. Safe against missing acreage, missing areas,
    zero findings and a missing previous flight.
    """
    findings = list(flight.findings)
    total_acres = _pos(flight.acreage) or _pos(flight.farm.acreage)

    buckets = {"urgent": [], "watch": [], "good": []}
    for f in findings:
        buckets[severity_of(f)].append(f)

    marked_acres = sum(_pos(f.area_acres) or 0.0 for f in findings)
    have_areas = marked_acres > 0

    # Acreage per band. When a finding has no measured area we still want it to
    # register, so it borrows the average of the ones that do.
    fallback = (marked_acres / sum(1 for f in findings if _pos(f.area_acres))) if have_areas else 0.0
    band_acres = {k: 0.0 for k in ("urgent", "watch", "good")}
    for key, items in buckets.items():
        band_acres[key] = sum((_pos(f.area_acres) or fallback) for f in items)

    counted = sum(band_acres.values())
    clear_acres = None
    if total_acres and counted <= total_acres:
        clear_acres = round(total_acres - counted, 2)

    # ---- the score -------------------------------------------------------
    # Share of the field in good shape, with watch areas counted at 55% and
    # urgent areas at nothing. Stated on the report itself so it can be checked.
    score = None
    if total_acres and counted > 0:
        earned = sum(band_acres[k] * SEVERITY[k]["weight"] for k in band_acres)
        earned += (clear_acres or 0.0) * 1.0
        score = int(round(100.0 * earned / total_acres))
        score = max(0, min(100, score))
    elif findings:
        earned = sum(len(v) * SEVERITY[k]["weight"] for k, v in buckets.items())
        score = int(round(100.0 * earned / len(findings)))
    elif not findings:
        score = 100

    label, colour = score_band(score)

    # ---- the stacked band ------------------------------------------------
    basis = total_acres if (total_acres and counted <= total_acres) else (counted or 1.0)
    bands = []
    for key in BAND_ORDER:
        if key == "clear":
            acres, count = (clear_acres or 0.0), 0
        else:
            acres, count = band_acres[key], len(buckets[key])
        if acres <= 0 and count == 0:
            continue
        bands.append({
            **SEVERITY[key],
            "acres": round(acres, 1),
            "count": count,
            "pct": round(100.0 * acres / basis, 1) if basis else 0.0,
        })
    # Bands must fill the bar exactly, so the largest one absorbs any rounding.
    _true_up(bands)

    # ---- trend against the previous flight -------------------------------
    prev_score = delta = None
    if prev is not None and prev.findings:
        prev_analysis = analyse(prev) if prev.id != flight.id else None
        if prev_analysis:
            prev_score = prev_analysis["score"]
            if prev_score is not None and score is not None:
                delta = score - prev_score

    ordered = sorted(findings, key=lambda f: (SEVERITY[severity_of(f)]["rank"],
                                              -(_pos(f.area_acres) or 0.0),
                                              f.sort_order or 0))
    numbers = {f.id: i + 1 for i, f in enumerate(ordered)}

    return {
        "findings_ordered": ordered,
        "numbers": numbers,
        "severity_of": severity_of,
        "SEVERITY": SEVERITY,
        "URGENCY_COPY": URGENCY_COPY,
        "buckets": buckets,
        "urgent_count": len(buckets["urgent"]),
        "watch_count": len(buckets["watch"]),
        "good_count": len(buckets["good"]),
        "score": score,
        "score_label": label,
        "score_colour": colour,
        "prev_score": prev_score,
        "delta": delta,
        "bands": bands,
        "total_acres": round(total_acres, 1) if total_acres else None,
        "marked_acres": round(counted, 1),
        "clear_acres": clear_acres,
        "have_areas": have_areas,
        "acres_at_risk": round(band_acres["urgent"] + band_acres["watch"], 1),
        "pct_at_risk": (round(100.0 * (band_acres["urgent"] + band_acres["watch"]) / total_acres)
                        if total_acres else None),
        "top_actions": (buckets["urgent"] + buckets["watch"])[:COVER_ACTIONS],
        "pages": paginate(ordered),
        "gauge": gauge_arc(score),
        "ac": ac,
    }


def _pos(v):
    try:
        v = float(v)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- season trend
# How many flights the sparkline can show before the points crowd together.
SEASON_MAX_POINTS = 12


def season_trend(flight, season_flights):
    """
    The season so far, flight by flight, rather than only the flight before.

    `season_flights` is every flight of this farm and season, in any order —
    the caller supplies them so this module still touches no database. Flights
    with no findings are skipped: a flight whose CSV has not been imported yet
    has no score to plot, and drawing it as a zero would read as a collapse in
    field health rather than as missing data.

    Returns None when there is nothing to compare against, so the report can
    fall back to the single-flight view exactly as it did before.
    """
    points = []
    for fl in sorted(season_flights, key=lambda f: (f.flight_number or 0, f.id or 0)):
        if not fl.findings:
            continue
        an = analyse(fl) if fl.id != flight.id else None
        score = an["score"] if an else None
        if an is None:                       # this flight — analysed by the caller
            score = None
        points.append({
            "flight": fl,
            "number": fl.flight_number,
            "date": fl.flight_date,
            "is_current": fl.id == flight.id,
            "score": score,
            "urgent": sum(1 for f in fl.findings if severity_of(f) == "urgent"),
            "watch": sum(1 for f in fl.findings if severity_of(f) == "watch"),
            "good": sum(1 for f in fl.findings if severity_of(f) == "good"),
            "zones": len(fl.findings),
            "acres_at_risk": round(sum(
                (_pos(f.area_acres) or 0.0) for f in fl.findings
                if severity_of(f) in ("urgent", "watch")), 1),
        })

    if len(points) < 2:
        return None
    return points


def season_summary(points, current_score):
    """
    Turn the per-flight points into the numbers the season page states.

    `current_score` is passed in rather than recomputed so the season page and
    the cover sheet can never disagree about this flight's score.
    """
    if not points:
        return None

    for p in points:
        if p["is_current"]:
            p["score"] = current_score

    scored = [p for p in points if p["score"] is not None]
    if len(scored) < 2:
        return None

    first, last = scored[0], scored[-1]
    swing = last["score"] - first["score"]
    best = max(scored, key=lambda p: p["score"])
    worst = min(scored, key=lambda p: p["score"])
    average = int(round(sum(p["score"] for p in scored) / len(scored)))

    # Zones closed across the season: what the first scored flight was carrying
    # that the latest one no longer is. Never negative — a rise means new zones
    # opened, which the "opened" figure states instead.
    closed = max(0, (first["urgent"] + first["watch"]) - (last["urgent"] + last["watch"]))
    opened = max(0, (last["urgent"] + last["watch"]) - (first["urgent"] + first["watch"]))

    if swing > 2:
        direction, verdict = "up", "improving"
    elif swing < -2:
        direction, verdict = "down", "slipping"
    else:
        direction, verdict = "flat", "holding steady"

    return {
        "points": points,
        "scored": scored,
        "flights_scored": len(scored),
        "first": first,
        "last": last,
        "swing": swing,
        "direction": direction,
        "verdict": verdict,
        "best": best,
        "worst": worst,
        "average": average,
        "closed": closed,
        "opened": opened,
        "spark": sparkline(scored),
        "bars": season_bars(scored),
    }


def sparkline(scored, width=420.0, height=64.0, pad=6.0):
    """
    SVG geometry for the season score line.

    Plotted from plain numbers, like the score dial, so the browser and
    WeasyPrint draw the identical path from the identical string.
    """
    n = len(scored)
    if n < 2:
        return None

    lo = min(p["score"] for p in scored)
    hi = max(p["score"] for p in scored)
    # Always give the line room to move, even when every flight scored the same.
    lo, hi = max(0, min(lo, hi) - 8), min(100, max(lo, hi) + 8)
    if hi - lo < 12:
        hi = min(100, lo + 12)
    span = float(hi - lo) or 1.0

    inner_w = width - pad * 2
    inner_h = height - pad * 2
    pts = []
    for i, p in enumerate(scored):
        x = pad + (inner_w * i / (n - 1))
        y = pad + inner_h * (1.0 - (p["score"] - lo) / span)
        pts.append({
            "x": round(x, 2), "y": round(y, 2),
            "score": p["score"], "number": p["number"],
            "is_current": p["is_current"],
        })

    line = " ".join(("M" if i == 0 else "L") + f" {p['x']} {p['y']}" for i, p in enumerate(pts))
    area = (line + f" L {pts[-1]['x']} {height - pad} L {pts[0]['x']} {height - pad} Z")
    return {"points": pts, "line": line, "area": area,
            "w": width, "h": height, "lo": lo, "hi": hi}


def season_bars(scored):
    """
    Per-flight zone counts, scaled to the tallest flight in the season, so the
    farmer can see whether the number of problem zones is falling.
    """
    peak = max((p["urgent"] + p["watch"] + p["good"]) for p in scored) or 1
    bars = []
    for p in scored:
        total = p["urgent"] + p["watch"] + p["good"]
        bars.append({
            "number": p["number"],
            "is_current": p["is_current"],
            "total": total,
            "urgent": p["urgent"], "watch": p["watch"], "good": p["good"],
            "pct": round(100.0 * total / peak, 1),
            "urgent_pct": round(100.0 * p["urgent"] / total, 1) if total else 0.0,
            "watch_pct": round(100.0 * p["watch"] / total, 1) if total else 0.0,
            "good_pct": round(100.0 * p["good"] / total, 1) if total else 0.0,
        })
    return bars


def _true_up(bands):
    """Force the stacked band to total exactly 100% so it never leaves a gap."""
    if not bands:
        return
    drift = round(100.0 - sum(b["pct"] for b in bands), 1)
    if drift:
        biggest = max(bands, key=lambda b: b["pct"])
        biggest["pct"] = round(biggest["pct"] + drift, 1)


# ---------------------------------------------------------------- the gauge
def gauge_arc(score, radius=34.0, cx=40.0, cy=40.0):
    """
    SVG path data for the score dial. Returned as plain numbers so the template
    stays free of arithmetic and both the browser and WeasyPrint draw the same
    arc from the same string.
    """
    span = 270.0                      # a three-quarter dial, open at the bottom
    start = 135.0                     # ...starting bottom-left
    frac = 0.0 if score is None else max(0.0, min(1.0, score / 100.0))
    return {
        "track": _arc(cx, cy, radius, start, start + span),
        "value": _arc(cx, cy, radius, start, start + span * frac) if frac > 0.004 else "",
        "cx": cx, "cy": cy, "r": radius,
    }


def _arc(cx, cy, r, a0, a1):
    x0, y0 = _pt(cx, cy, r, a0)
    x1, y1 = _pt(cx, cy, r, a1)
    large = 1 if abs(a1 - a0) > 180 else 0
    return f"M {x0:.2f} {y0:.2f} A {r:.2f} {r:.2f} 0 {large} 1 {x1:.2f} {y1:.2f}"


def _pt(cx, cy, r, deg):
    rad = math.radians(deg)
    return cx + r * math.cos(rad), cy + r * math.sin(rad)


# ---------------------------------------------------------------- pagination
# The report is composed as real A4 sheets so the screen and the paper agree
# exactly. That only holds if findings are packed into sheets here rather than
# left to whatever the renderer decides, so each card's height is estimated in
# millimetres from its text and the sheets are filled greedily.
# These are measured against a real WeasyPrint render, not estimated: a card
# with four wrapped lines of body text occupies ~52 mm, and the column fits
# about 111 characters per line. The values below sit a little under those so
# the packing errs towards leaving white space rather than overflowing a sheet.
SHEET_BODY_MM = 236.0        # usable height inside a findings sheet
CARD_CHROME_MM = 16.0        # card padding, header row and the gap below it
BLOCK_LABEL_MM = 4.0         # the "What we saw" style label
LINE_MM = 4.3                # one wrapped line of body text
CHARS_PER_LINE = 105         # Montserrat 10.5px across the card column


def card_height(finding):
    h = CARD_CHROME_MM
    for text in (finding.observation, finding.likely_cause, finding.recommendation):
        lines = max(1, math.ceil(len((text or "").strip()) / CHARS_PER_LINE))
        h += BLOCK_LABEL_MM + lines * LINE_MM + 1.6
    if finding.annotation_link:
        h += 6.0
    return h


def paginate(findings, budget=SHEET_BODY_MM):
    """
    Pack findings into sheets, filling each one before starting the next.

    An earlier version tried to even the cards out across sheets, which turned
    a full page followed by a short one into two half-empty pages — a document
    that runs on and ends short reads as normal, whereas two pages each with a
    hand's width of white below the last card reads as a mistake. So this fills
    greedily, and the accuracy of card_height() is what keeps each sheet close
    to full.
    """
    if not findings:
        return [[]]

    pages, current, used = [], [], 0.0
    for f in findings:
        h = card_height(f)
        if current and used + h > budget:
            pages.append(current)
            current, used = [], 0.0
        current.append(f)
        used += h
    if current:
        pages.append(current)
    return pages or [[]]
