"""
Editable homepage content.

Every word, number and list item on the public homepage lives here as a keyed
value with a default. `get_content()` returns the defaults overlaid with
whatever an admin has saved, so the page renders identically to the hard-coded
version until somebody changes something, and a key that has never been edited
still renders rather than disappearing.

Values are stored one row per key rather than as a single JSON blob, so adding a
field in a later release does not have to migrate or merge existing rows: the
new key simply has no row yet and falls back to its default.

Repeating blocks (the six steps, the delivery bullet lists, the sample report
findings) are stored as text, one item per line, with fields inside an item
separated by a pipe. That is the plainest editable form for somebody who is not
writing JSON in a textarea, and a malformed line degrades to a shorter item
rather than breaking the page.
"""
import re

from markupsafe import Markup, escape

# ---------------------------------------------------------------- the schema
# group -> [(key, label, kind, default, help)]
#   kind: text | textarea | lines | image
#
# 'lines' fields are one item per line; where an item has several parts they are
# separated by '|', and the help text says what each part is.
SECTIONS = [
    ("Browser tab", "tab", [
        ("meta_title", "Page title", "text",
         "SHAMBA Tracker — Field scouting reports by Acre Insights",
         "Shown in the browser tab and in search results."),
        ("meta_description", "Meta description", "textarea",
         "Turn a DroneDeploy scouting flight into a branded field report the farmer "
         "can read on a phone, delivered over WhatsApp or email.",
         "The summary a search engine shows under the title."),
    ]),

    ("Navigation", "nav", [
        ("brand_name", "Product name", "text", "SHAMBA Tracker",
         "Sits beside the logo in the header."),
        ("brand_logo", "Logo", "image", "img/acre-logo.png",
         "Used across the homepage: header, drawer, footer and both report mock-ups."),
        ("nav_links", "Header links", "lines",
         "#how|How it works\n#code|Colour code\n#deliver|Delivery\n#report|The report",
         "One per line: anchor|label"),
    ]),

    ("Hero", "hero", [
        ("hero_eyebrow", "Eyebrow", "text", "A product of Acre Insights", ""),
        ("hero_title_1", "Headline, first line", "text", "The flight lands at noon.", ""),
        ("hero_title_2", "Headline, second line", "text", "The farmer reads it by three.",
         "Rendered in the accent colour."),
        ("hero_body", "Introduction", "textarea",
         "SHAMBA Tracker turns a finished DroneDeploy scouting flight into an Acre-branded "
         "field report: every marked area carries what we saw, the likely cause and one thing "
         "to do about it. Sent over WhatsApp or email, and confirmed with a tap.", ""),
        ("hero_cta_primary", "Primary button", "text", "Create your account", ""),
        ("hero_cta_secondary", "Secondary button", "text", "See how it works", ""),
        ("hero_stats", "Figures", "lines",
         "6|fields per report\n4|colour meanings\n1 tap|to confirm receipt",
         "One per line: figure|label"),
        ("hero_image", "Background image", "image", "img/field-aerial.jpg", ""),
    ]),

    ("Report preview card", "preview", [
        ("preview_eyebrow", "Card label", "text", "Field scouting report", ""),
        ("preview_image", "Map image", "image", "img/ndvi-analysis.jpg", ""),
        ("preview_rows", "Sample findings", "lines",
         "1|#D64550|Needs testing|Stress along the feeder line. Check pressure at the far bay this week.|33|35\n"
         "2|#D64550|Needs testing|Within-row browning. Move irrigation earlier than the morning sun.|51|43\n"
         "3|#E7B416|Monitor|Colour change at the headland. Photograph again on the next flight.|67|31\n"
         "4|#2F6FDB|New growth|Replanted block coming back evenly.|24|68",
         "One per line: number|colour hex|category|line of text|pin across %|pin down %. "
         "The pin drawn on the map comes from the same line, so the two always agree."),
        ("preview_footnote", "Footnote", "text",
         "Six areas marked on this flight. Every one carries a cause and a next step.", ""),
        ("preview_confirm", "Confirmation strip", "text", "Receipt confirmed by the farmer", ""),
    ]),

    ("How it works", "how", [
        ("how_eyebrow", "Eyebrow", "text", "How a flight becomes a report", ""),
        ("how_title", "Heading", "text", "Six steps, and the order matters", ""),
        ("how_body", "Introduction", "textarea",
         "Nothing here is a hand-off into a spreadsheet. Each step writes into the same record, "
         "so the report assembles itself from work already done.", ""),
        ("how_steps", "Steps", "lines",
         "Fly and upload|The field operator flies the farm and pushes the imagery into the DroneDeploy project.\n"
         "Annotate|The scout marks what they see. Colour carries the meaning, so the map reads at a glance.\n"
         "Import|Export the annotations as CSV. SHAMBA Tracker reads the colours and the free text into structured findings.\n"
         "Complete|The agronomist adds the likely cause and the one action worth taking. Nothing generates until all three fields are filled.\n"
         "Generate|One click builds the Acre-branded PDF: map, findings, colour code, season trend.\n"
         "Deliver and confirm|Out over WhatsApp or email. The farmer taps once to confirm, and it lands back on the dashboard.",
         "One per line: title|description. Numbered automatically."),
    ]),

    ("Colour code", "code", [
        ("code_eyebrow", "Eyebrow", "text", "The annotation colour code", ""),
        ("code_title", "Heading", "text", "One shared language on every map", ""),
        ("code_body", "Introduction", "textarea",
         "Whatever colour the agronomist picks in DroneDeploy, SHAMBA Tracker buckets it by "
         "hue into a fixed meaning. The farmer learns four colours once and reads every "
         "flight after that without help.", ""),
        ("code_image", "Image", "image", "img/canopy.jpg", ""),
        ("code_caption_title", "Caption label", "text", "What the colour decides", ""),
        ("code_caption_body", "Caption", "textarea",
         "The colour sets the suggested category too. A red pin arrives as "
         "**Pest / Disease**, a yellow one as **Needs Investigation**. The agronomist can "
         "always override it, but the first guess is already close.",
         "Wrap words in **double asterisks** to bold them."),
    ]),

    ("Delivery", "deliver", [
        ("deliver_eyebrow", "Eyebrow", "text", "Getting it to the farmer", ""),
        ("deliver_title", "Heading", "text", "Two ways out, and one of them always works", ""),
        ("deliver_body", "Introduction", "textarea",
         "Provider APIs need a verified sending domain and an approved business number. Until "
         "you have both, the report still goes out from your own WhatsApp or mail app, with the "
         "message already written.", ""),
        ("deliver_a_title", "First card, title", "text", "Send it yourself", ""),
        ("deliver_a_tag", "First card, tag", "text", "No setup needed", ""),
        ("deliver_a_body", "First card, text", "textarea",
         "Opens your own WhatsApp or mail app with the farmer's number, the message in Acre's "
         "voice and the report link already filled in. Send it, and the delivery is logged back here.", ""),
        ("deliver_a_points", "First card, bullets", "lines",
         "Works on any phone or laptop\nNothing to configure or verify\n"
         "Recorded on the dashboard like any other send", "One per line."),
        ("deliver_b_title", "Second card, title", "text", "Send automatically", ""),
        ("deliver_b_tag", "Second card, tag", "text", "Provider account", ""),
        ("deliver_b_body", "Second card, text", "textarea",
         "Delivers straight from Acre Insights with the PDF attached, over Resend or SendGrid "
         "for email and Twilio or the Meta Cloud API for WhatsApp.", ""),
        ("deliver_b_points", "Second card, bullets", "lines",
         "The PDF travels as an attachment\nSends without leaving the dashboard\n"
         "Needs a verified domain and business number", "One per line."),
    ]),

    ("The report", "report", [
        ("report_eyebrow", "Eyebrow", "text", "What the farmer receives", ""),
        ("report_title", "Heading", "text", "A page you could read standing in the field", ""),
        ("report_body", "Introduction", "textarea",
         "No jargon, no dashboard to learn. The annotated map, then each marked area in three "
         "short lines: what we saw, why we think so, and what to do about it.", ""),
        ("report_features", "Features", "lines",
         "map|Annotated map|The same image the scout marked, numbered to match the findings below.\n"
         "report|Findings in order|Grouped by category so related work gets done in one visit.\n"
         "chart|Season trend|How field health has moved across the season's flights, from the second flight on.\n"
         "download|Branded PDF|Downloadable and attachable, laid out for A4 and for a phone screen.",
         "One per line: icon|title|description. Icons: map, report, chart, download, "
         "check, send, drone, pin, farm."),
        ("report_sample_farm", "Sample farm name", "text", "Mashuru Onion Farm", ""),
        ("report_sample_facts", "Sample facts", "lines",
         "Crop|Onions\nAcreage|10.7 ac\nFlight|1 of 12\nAreas marked|6",
         "One per line: label|value"),
        ("report_image", "Sample map image", "image", "img/hero-map.jpg", ""),
        ("report_sample_rows", "Sample findings", "lines",
         "#D64550|Irrigation|Crop stress running the length of the eastern feeder line.|Check pressure at the far bay before the next irrigation.\n"
         "#2F6FDB|Nutrient / Vigor|New growth returning across the replanted block.|No action. Photograph again next flight to confirm.",
         "One per line: colour hex|category|observation|action"),
    ]),

    ("Closing call to action", "cta", [
        ("cta_title", "Heading", "text", "Give your farmers a report worth reading.", ""),
        ("cta_body", "Text", "textarea",
         "Set up your farms, import a flight, and send the first report today. New accounts are "
         "approved by your workspace admin, usually the same day.", ""),
        ("cta_primary", "Primary button", "text", "Get started", ""),
        ("cta_secondary", "Secondary button", "text", "I already have an account", ""),
        ("cta_image", "Background image", "image", "img/canopy.jpg", ""),
    ]),

    ("Footer", "footer", [
        ("footer_tagline", "Tagline", "text", "Nature meets intelligence", ""),
        ("footer_links", "Links", "lines",
         "#how|How it works\n#code|Colour code",
         "One per line: anchor|label. The sign-in link is always added."),
        ("footer_legal", "Copyright line", "text", "Acre Insights · Nairobi, Kenya",
         "The year is added automatically in front of this."),
    ]),
]

# key -> default, flattened once at import
DEFAULTS = {k: d for _g, _s, fields in SECTIONS for k, _l, _kd, d, _h in fields}
KINDS = {k: kd for _g, _s, fields in SECTIONS for k, _l, kd, _d, _h in fields}
IMAGE_KEYS = [k for k, kd in KINDS.items() if kd == "image"]


def section_of(key):
    """The anchor of the section a field belongs to, for redirecting back to it."""
    for _group, anchor, fields in SECTIONS:
        if any(k == key for k, _l, _kd, _d, _h in fields):
            return anchor
    return ""


def rich(text):
    """
    Render an editable paragraph with light emphasis.

    The text is escaped first and only then are `**bold**` pairs turned into
    markup, so a stray angle bracket in the editor can never inject HTML. It is
    the one piece of formatting the shipped copy needs, and asking an admin to
    write a span to keep two words bold would be a poor trade.
    """
    out = str(escape(text or ""))
    out = re.sub(r"\*\*(.+?)\*\*", r'<span class="font-semibold">\1</span>', out)
    return Markup(out)


def split_lines(raw, parts=1):
    """
    A 'lines' value as a list of tuples, one per non-empty line.

    Short lines are padded and long ones are truncated to `parts`, so a typo in
    the editor costs one malformed item rather than the whole page.
    """
    out = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        bits = [b.strip() for b in line.split("|")]
        if parts == 1:
            out.append(bits[0])
        else:
            bits = (bits + [""] * parts)[:parts]
            out.append(tuple(bits))
    return out
