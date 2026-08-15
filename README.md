# SHAMBA Tracker

**Flight Report Automation — a product of Acre Insights.**

Turn a finished DroneDeploy scouting flight into a clean, Acre-branded field
report — generated the same day, delivered over email and WhatsApp, and
confirmed by the farmer with a tap.

Built with Flask · SQLite · Tailwind CSS · vanilla JS · WeasyPrint. Ready for Railway.

---

## What it does

1. **Farms & flights** — keep a record of each farm, its farmer contact, and every scouting flight (unique by *farm + season + flight number*).
2. **Import** — upload the DroneDeploy annotation **CSV**; SHAMBA Tracker parses each pin, classifies its colour into the Acre colour code, normalises the area, and splits the comment into *observation / likely cause / recommendation*.
3. **Review portal** — complete and correct every finding inline. The report can only be generated once all findings are complete.
4. **Generate** — one click produces the branded three-page report (web + **PDF**), named `Farm Name_Crop Name_Season Year_Flight No.pdf`. A report needs both complete findings and the annotated map snapshot: the findings are numbered against the map, so without it they point at nothing.
5. **Deliver** — send by email (report attached) and WhatsApp (link), with a message in Acre's voice.
6. **Acknowledge** — the farmer opens a tokenised public link, reads the report, and taps to confirm receipt — logged back to the dashboard.
7. **Bulk import** — add or update farms and flights in batches from a CSV or Excel sheet, with a row-by-row preview before anything is written.
8. **Record the reply** — when a farmer disputes a finding, customer success logs what they said against that finding. Internal only; it never reaches the report.

### Two colour systems, doing two different jobs

**The annotation colour code** is what the agronomist draws with in DroneDeploy.
It says how urgent a zone looked, and it is for internal triage.

| Colour | Meaning |
|---|---|
| Blue | New growth (positive development) |
| Green | Healthy (baseline, no action) |
| Yellow | Monitor (discoloration / early concern) |
| Red | Needs testing (suspicious gap / suspected diagnosis) |
| Grey | Pending review (logged, awaiting agronomist) |

Any hex a pin uses is bucketed to the nearest meaning by hue, and the true pin
colour is kept on the finding.

**The report category colours** are a separate system, and they are what the
farmer reads. They say *what kind* of observation a zone carries, not how
urgent it is, because that is what is useful to read spatially off a map.

| Category | Colour | What it covers |
|---|---|---|
| Irrigation / Moisture | Blue | Water availability or distribution |
| Soil Fertility / Nutrition | Amber | Soil condition or nutrient availability |
| Crop Establishment | Terracotta | Germination, emergence, spacing or mounding |
| Pest / Disease | Deep red | Suspected pest pressure or disease |
| Weeds | Slate green | Weed pressure |
| Needs Investigation | Purple | Flagged, cause not yet established |

No colour in that table means "healthy" or "fine". Every colour on the report
map marks something the agronomist actually flagged.

---

## Run locally

```bash
cd shamba-tracker
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000

On first run the database is created and the **first admin is seeded**:

- **Email:** `cathy@acre-insights.com`
- **Password:** `Access Denied`

(The seeded password is not published here. Set `ADMIN_PASSWORD` before the first
run to choose it, and `ADMIN_EMAIL` to change the address.)

> **PDF note:** WeasyPrint needs system libraries (Pango/Cairo). If they're not
> installed locally, the app still runs — it falls back to the print-friendly
> web report (your browser's *Save as PDF*). The included **Dockerfile installs
> everything**, so server-side PDF works on Railway out of the box.

### Try it with the sample flight
A sample DroneDeploy export and annotated map are in `samples/`. Create a farm
and a flight, upload `samples/sample_dronedeploy_export.csv`, then
`samples/sample_annotated_map.jpg` as the map — complete the findings and
generate.

### Running the checks
Each suite is a plain script against a throwaway SQLite database and a real
Flask test client, so the templates actually render. Run them individually:

```bash
python tests/test_flow.py       # signup, roles, farms, flights, delivery
python tests/test_report.py     # report links, caching, screen/paper parity
python tests/test_filename.py   # the download name and the on-disk slug
python tests/test_season.py     # the season trend across flights
python tests/test_import.py     # CSV/Excel import of farms and flights
python tests/test_homepage.py   # admin-managed homepage content
python tests/test_farmer_comment.py   # the farmer's reply, and its isolation
python tests/test_generate_gate.py    # what a report needs before it generates
python tests/test_patterns.py   # the V2 grouping, the guardrail, and page fit
python tests/test_security.py   # the response headers and cookie flags
```

356 checks at the time of writing. `test_patterns.py` is the one worth reading
first: section C feeds the report annotations built from a closed vocabulary of
nonsense words and fails if a single word outside that vocabulary — plus the
report's own fixed frame — reaches page 1. That is the guardrail below, tested
rather than asserted.

---

## Deploy to Railway

This project ships with a **Dockerfile**, which is the recommended way to deploy
because it installs the system libraries WeasyPrint needs to generate the PDF
report. `railway.json` already tells Railway to use the Dockerfile builder.

1. Push this folder to a GitHub repo.
2. In Railway: **New Project → Deploy from GitHub repo** → pick the repo.
3. Confirm the build is using the **Dockerfile** (Settings → Build → Builder =
   Dockerfile). If Railway previously set a custom **Start Command**, clear it so
   the Dockerfile's command is used.
4. Under **Variables**, set the values from `.env.example`. At minimum:
   - `SECRET_KEY` — a long random string
   - `PUBLIC_BASE_URL` — your Railway URL, e.g. `https://shamba-tracker.up.railway.app` (used to build the shareable links in messages)
5. Deploy. The first boot creates the DB and seeds Cathy.

> **If "Download PDF" doesn't download a file**, the app is almost certainly
> running without the Dockerfile (e.g. a Nixpacks build), so WeasyPrint's
> libraries aren't installed. Deploy with the Dockerfile as above. As a safety
> net the app will otherwise open a print-ready page so the browser can still
> "Save as PDF" — but the Dockerfile gives you the proper server-generated file.

**Persisting data:** SQLite and uploads live on the container filesystem. For
durable storage across deploys, add a Railway **Volume** mounted at `/app`
(or `/app/uploads`), or set `DATABASE_URL` to a Railway Postgres instance.

### Turning on real delivery
Add the keys for whichever providers you use to Railway **Variables** — no code
changes needed.

- **Email:** `RESEND_API_KEY` *(or)* `SENDGRID_API_KEY`, plus `MAIL_FROM`.
  - Resend returns **403** if `MAIL_FROM` uses a domain you haven't verified.
    Verify your domain at resend.com/domains, or leave `MAIL_FROM` blank to use
    Resend's test sender (delivers to the account owner's address).
- **WhatsApp:** `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` + `TWILIO_WHATSAPP_FROM`
  *(or)* `WHATSAPP_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID`.
  - Twilio returns **401** if the SID/Auth Token are wrong or have trailing
    spaces. With the sandbox, each recipient must first send the `join <code>`
    message to the sandbox number.

If a send fails, the on-screen message now shows the provider's own explanation
(e.g. "domain is not verified") so you can fix it quickly.

---

## Roles
- **Admin** — everything, including user management (Cathy).
- **Agronomist** — farms, flights, findings, reports.
- **Customer Success** — delivery and acknowledgement tracking.

New public sign-ups become Agronomists (the very first ever account becomes Admin). Admins add and manage users under **Users**.

---

## Editing the homepage

**Homepage** in the sidebar (admins only) edits every word on the public site:
titles, headings, paragraphs, button labels, the six steps, the delivery bullet
lists, the sample report, the footer, and the background images. Changes are
live as soon as you save. *View homepage* opens the public page in a new tab —
signing in normally redirects you to your dashboard, so the button adds
`?preview=1` to bypass that without signing out.

Two rules make it safe to leave alone:

* **A field you have not edited keeps its shipped wording.** Only changed
  fields are stored, so a later release that rewords an untouched default
  actually shows that change instead of being masked by a stale copy.
* **Returning a field to its default removes it from storage**, and *Reset all
  to defaults* returns the whole page to the wording it shipped with.

Repeating blocks — the steps, the bullet lists, the sample findings — are edited
one item per line, with `|` between the parts of an item. A malformed line costs
that one item rather than the page. In paragraph fields, `**double asterisks**`
make text bold; everything else is escaped, so nothing typed into the editor can
inject markup.

## Recording what the farmer said back

Sometimes a farmer's own knowledge of a field disagrees with a finding. On the
flight's review page, customer success can record their reply against the
individual finding it concerns.

This is deliberately internal. The report is the record of what was advised, and
a farmer disputing that advice is a note *about* it rather than part of it — so
the comment appears nowhere in the web report, the farmer's share link, or the
PDF. Saving one also does not invalidate a report that has already been
generated and sent, because the PDF is still an accurate copy of what went out.

Recording a comment needs the `record_farmer_comment` permission, which customer
success holds without being able to edit the finding itself.

## Importing farms and flights in bulk

**Import** in the sidebar takes a `.csv`, `.xlsx` or `.xlsm` sheet and adds or
updates farms and flights in batches. Nothing is written until you have seen a
row-by-row preview of what the file would do, and a **template** for each kind
is downloadable from that page with the exact headings the importer reads.

Four rules are worth knowing before you upload:

* **Matching follows the app's own keys.** A farm is matched on its name; a
  flight on *farm + season + flight number*, the same triple the new-flight form
  calls unique. A match updates the record; anything else creates one.
* **The farm must already exist** before its flights are imported. A flight row
  naming an unknown farm is flagged rather than guessed at, so a typo cannot
  quietly create a second farm.
* **A blank cell leaves the value alone.** A part-filled sheet is how you correct
  two phone numbers across forty farms, so blanks are never read as deletions.
  To clear a field, edit the record directly.
* **A bad row is skipped, not fatal.** Each row is validated on its own and any
  problem names the cell at fault. Everything valid still imports; fix the
  flagged rows in your sheet and upload it again.

Headings are matched loosely — case, spacing, punctuation and the common
synonyms all work, so `Farm name`, `farm_name` and `FARM` read the same, as do
`Acreage` / `Acres` / `Size` and `Phone` / `WhatsApp` / `Mobile`. Dates are
accepted as `2026-03-14`, `14/03/2026` and several other common forms.

Excel support needs `openpyxl` (already in `requirements.txt`). Without it the
app still runs and CSV import still works; the upload page says so.

## The report

Three pages, in this order.

The **look** is V1's, unchanged — the same typography, the navy masthead rule,
the bordered facts box, the sage panel, the running foot. Only the structure,
the grouping and the voice changed, which is what the V2 brief asked for. The
six category colours are the one thing added, and they are data colours: a
swatch on a card, a pin colour on the key, a table header.

**Page 1 — Field scouting summary.** What was scouted, how much of it was
marked, which categories it falls into, and what was recommended. It states
counts and acres, not a score: a single number out of 100 claims a precision
the underlying data has not got, and it invites the farmer to argue with the
number instead of walking the field.

**Page 2 — Farm map.** The annotated image, and the full six-colour key, so the
same colour means the same thing on every report a farm ever receives.

**Page 3 — Detailed findings.** Every annotation, exactly as the agronomist
wrote it, in a compact table under the pattern it belongs to. A flight with
more rows than one page holds spills to a fourth sheet, and a group that
continues repeats its heading rather than orphaning rows under someone else's.

### Grouping, and the guardrail

The categories the agronomist picks from on the review page **are** the patterns
the report groups by. That is the whole design: the review screen and page 1
read the same field, so they cannot disagree, and a misfiled zone is fixed in
one click by the person best placed to know.

`patterns.py` suggests a category from the agronomist's own observation, cause
and recommendation text when a flight is imported. It is a weighted keyword
classifier, not a model — deliberately, because the guardrail then holds by
construction rather than by instruction:

> Everything on page 1 is assembled from the agronomist's own words. It counts,
> sums, de-duplicates and joins them. It cannot introduce a cause, a diagnosis
> or a recommendation the annotations do not contain, because there is nowhere
> for a new word to come from.

Section C of `tests/test_patterns.py` enforces that with a closed vocabulary.

The classifier is a suggestion and will not be right every time. It is right on
all fifteen of the IPM Farm annotations that V1 filed under one label, which is
the case it was built against, and anything it cannot read from the text lands
in **Needs Investigation** rather than being guessed at.

### Zone numbers

Numbered in the agronomist's own annotation order, because those are the
numbers already drawn on the map image the farmer is holding. An earlier version
numbered by urgency, which lined up with the map only while every zone happened
to be flagged in descending order of size — a single out-of-order pin would have
had the report pointing at the wrong part of the field.

### Screen, print and PDF are one document

The report is one file, `templates/report_doc.html`, composed as real A4 sheets:
each sheet is exactly 210 x 297 mm and the page box has no margin of its own.
The browser shows those sheets, the browser prints those sheets, and WeasyPrint
turns those sheets into pages. `report.html` wraps it in the app's chrome (all
of it marked `.no-print`), and `report_print.html` is a bare shell with no
styling of its own.

Three things hold that guarantee up:

* **Montserrat is bundled**, not fetched from a CDN. If WeasyPrint fell back to
  a system face while the browser used Montserrat, the same paragraph would wrap
  at a different word and the page breaks would stop matching. It is the only
  typeface in the report — readings are set apart by tabular figures and
  tracking rather than by a second family.
* **Detail tables are paginated in Python** (`patterns.paginate_groups`), not
  left to the renderer, so a row is never split and both engines break in the
  same places. The height constants were measured against a real render, not
  estimated, and they deliberately err towards breaking a page early.
* **Overflow is tested, not assumed.** A sheet is a fixed height and clips what
  does not fit, so a page count on its own cannot see a row that fell off the
  bottom. `test_patterns.py` renders the document twice — once as fixed sheets,
  once with the sheets free to grow — and fails if the second is longer.

### On a phone

Below 820 px the document **reflows into a single readable column**: the facts
go two-up, the category cards stack, and each row of the detail table becomes a
labelled card. Body text lands at 13.5 px and the table cells at 13.5–15 px.

An earlier build scaled the whole A4 sheet down instead. It fitted a 375 px
phone at 44%, which put the body text at about four pixels — the same document,
and unreadable. A farmer opening a WhatsApp link on a handset in a field is the
normal case here, not the edge one, so readable-and-reflowed beats
identical-and-illegible.

The content and its order do not change, and none of this touches print:
`@media print` is a separate context and always renders the fixed
210 × 297 mm sheets, so Download PDF is always the exact document.

### Download

**Download** produces a real file named
`Farm Name_Crop Name_Season Year_Flight No.pdf` — `Content-Disposition:
attachment` plus a matching `download` attribute on the link, so it saves
straight to disk with no viewer tab and no Save-as dialog. Renders take about a
second, so each one is cached against a key derived from the report's own
content; edit any finding and the cache invalidates itself. Every flight of a
season shares a farm, a crop and a season, so the **flight number is part of the
name**: without it each new report would land on top of the last one in the
farmer's downloads folder.

### The season page

Off by default, because the brief for this report was three pages. Set
`REPORT_SEASON_PAGE=1` to append a fourth once a season has two flights with
findings. It reports plain counts — areas marked and acres marked per flight —
for the same reason page 1 does.

### What this version does not do

Worth saying plainly, because none of it is hidden by the layout:

* **The map pins are not recoloured by category.** The map is the image the
  agronomist exported, and its pins carry DroneDeploy's own colour. The key on
  page 2 explains the category system, but making the pins match it needs either
  a DroneDeploy export coloured by category or the map drawn from geometry
  rather than uploaded as a picture. That is the next real piece of work here.
* **The classifier is keyword-based.** It reads the words the agronomist typed.
  It has no understanding of them, and an annotation phrased unusually will need
  correcting on the review page.
* **A summary that says nothing is a summary of an empty annotation.** If a
  finding has an observation but no cause, page 1 says so rather than filling
  the gap.

---

## Security headers

A scan in August 2026 came back with none of the six headers a browser uses to
constrain a page. `security.py` sets them on every response, in one place, so a
route added later cannot ship without them.

| Header | Value | What it is doing here |
|---|---|---|
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | Stops the first request of a later visit going out over plain HTTP, where the farmer's share link — token and all — would travel in clear. Sent only on a request that already arrived over HTTPS. |
| `Content-Security-Policy` | see below | Confines scripts, styles, fonts and images to this origin plus the two CDNs the app really loads. |
| `X-Frame-Options` | `SAMEORIGIN` | With `frame-ancestors`, keeps the app out of a third party's iframe. |
| `X-Content-Type-Options` | `nosniff` | An uploaded map is served as an image and must never be sniffed into a document. |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | The farmer's report URL contains its own access token. This sends the origin and never the path to another site. |
| `Permissions-Policy` | 22 features denied | Turns off the device APIs the app never uses, so injected script cannot reach for them either. |

Also set: `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy`, and
`X-Robots-Tag: noindex` — a tokenised report link in a search result is a token
in the wrong hands. The session cookie is `HttpOnly`, `SameSite=Lax`, expires
after twelve hours, and is `Secure` wherever `PORT` is set — which is Railway
and every other PaaS, and is not a local HTTP machine, where a `Secure` cookie
is silently dropped and the login just stops working with nothing to explain it.

### The CSP is a real constraint, not a complete one

Two allowances are worth naming rather than burying:

* **`'unsafe-inline'`** — the interface is server-rendered Jinja with inline
  `<style>` and `<script>` blocks and inline event handlers. Removing it means a
  per-response nonce on every one of them.
* **`'unsafe-eval'`** — Tailwind's Play CDN compiles utility classes in the
  browser.

Both are on by default and both come off with an environment variable, so the
policy can be tightened without another release. The second one is already
solved and waiting: `static/css/tailwind.css` is the same stylesheet built
ahead of time (27 KB, against a ~110 KB script that then compiles on every page
load). Turn it on with:

```bash
TAILWIND_LOCAL=1  CSP_ALLOW_EVAL=0
```

It is off by default only because this release could not diff it against the
CDN's output pixel for pixel. Look at the app once with it on, and if nothing
has moved, leave it on: it is faster on the connections agronomists actually
have, it survives a CDN outage, and it removes a third-party script origin from
the policy. `tailwind.config.js` documents how to rebuild it.

Use `CSP_REPORT_ONLY=1` to see what a policy change would break before it
breaks it.

---

## Small screens

Every page is checked at 375, 412, 768 and 1366 px — a real headless browser,
walking every route, failing on any horizontal scroll. The app chrome stacks,
the review table becomes labelled fields, and the report sheet scales rather
than reflowing.

Category colours run through the interface too, not only the report: the review
page shows the category's own swatch beside the dropdown and repaints it as the
selection changes, and the admin dashboard's issues-by-category chart draws each
bar in its category colour. One table in `patterns.py` feeds all three, so a
category cannot be one colour on the review screen and another on the farmer's
map.

`base.html` also carries four rules that do not depend on Tailwind having
loaded at all. Without them, a blocked or slow CDN leaves every image at its
natural size and a 611 px logo pushes the whole layout off a phone screen —
which is exactly what happens today if the CDN is unreachable. They cost nothing
when Tailwind does load.

---

## Project layout
```
shamba-tracker/
├── app.py             # routes + app factory
├── models.py          # User, Farm, Flight, Finding (SQLAlchemy)
├── parsing.py         # CSV parsing + colour classification
├── integrations.py    # email + WhatsApp (env-key based, simulate if absent)
├── pdf_gen.py         # WeasyPrint PDF (graceful fallback)
├── report_data.py     # zone numbers, banding, season trend, internal score
├── patterns.py        # the report categories, the grouping, and the guardrail
├── security.py        # response security headers + cookie hardening
├── bulk_import.py     # CSV/Excel import of farms and flights
├── homepage.py        # editable homepage fields, defaults and helpers
├── schema.py          # additive migrations + share-token backfill
├── templates/         # Tailwind UI + report templates
│   ├── report_doc.html    # THE report — three pages, screen/print/PDF, one file
│   ├── report.html        # app chrome around the document
│   ├── report_print.html  # bare wrapper WeasyPrint renders
│   ├── import.html        # bulk import upload + preview
│   └── homepage_edit.html # admin editor for the public homepage
├── static/img/        # Acre logo (transparent + white)
├── static/fonts/      # Montserrat, bundled (see below)
├── static/css/        # the built Tailwind stylesheet (opt-in, see above)
├── tailwind.config.js # how to rebuild it
├── samples/           # sample DroneDeploy CSV + annotated map
├── tests/             # run each with `python tests/<name>.py`
├── Dockerfile         # Railway build (installs WeasyPrint libs)
├── requirements.txt
└── .env.example
```
