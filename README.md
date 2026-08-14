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
4. **Generate** — one click produces the branded report (web + **PDF**), named `Farm Name_Crop Name_Season Year_Flight No.pdf`. A report needs both complete findings and the annotated map snapshot: the findings are numbered against the map, so without it they point at nothing.
5. **Deliver** — send by email (report attached) and WhatsApp (link), with a message in Acre's voice.
6. **Acknowledge** — the farmer opens a tokenised public link, reads the report, and taps to confirm receipt — logged back to the dashboard.
7. **Bulk import** — add or update farms and flights in batches from a CSV or Excel sheet, with a row-by-row preview before anything is written.
8. **Record the reply** — when a farmer disputes a finding, customer success logs what they said against that finding. Internal only; it never reaches the report.

### The annotation colour code
| Colour | Meaning |
|---|---|
| Blue | New growth (positive development) |
| Green | Healthy (baseline, no action) |
| Yellow | Monitor (discoloration / early concern) |
| Red | Needs testing (suspicious gap / suspected diagnosis) |
| Grey | Pending review (logged, awaiting agronomist) |

Any hex a pin uses in DroneDeploy is bucketed to the nearest meaning by hue, and the true pin colour is shown on the report as the data swatch.

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
python tests/test_report_v2.py       # pattern grouping, report voice, security headers
python tests/test_responsive.py      # every page on a phone-sized viewport
```

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

### What the farmer receives

The report is **three pages**, and it does not list every annotation on the
summary page:

1. **Field scouting summary** — what was scouted, the categories found with
   counts and acreage, the patterns behind them, and areas worth investigating.
2. **Farm map** — the annotated image, with a legend keyed by category.
3. **Detailed findings** — every annotation the agronomist wrote, unchanged,
   grouped under the pattern it belongs to.

V1 listed all fifteen zones one after another, nine of them near-identical
entries under a single category. That is an annotation dump rather than a
report. `aggregation.py` groups a flight's findings into the few patterns
actually present in them, so the summary says *"reduced crop vigour across nine
areas (~13.4 acres), associated with soil condition or nutrient availability"*
once, instead of nine times.

Two rules govern what the summary is allowed to say:

* **It only summarises and combines what the agronomist actually wrote.** No
  cause, diagnosis or recommendation appears that is not already in the source
  annotations. Sentences are assembled from the agronomist's own counts,
  acreages and words — there is no model in the loop and nothing is invented.
* **It does not sound more certain than its source.** "Associated with", not
  "caused by". "We suggest considering", not "work through these, in this
  order". An annotation records what was seen and what the agronomist suspects,
  not a result.

Clustering reads the **likely cause** rather than the category label, which is
what separates irrigation from soil fertility when both were filed under one
broad heading. Patterns are ordered by acreage so the largest finding leads, and
genuinely different causes stay apart rather than being merged for tidiness — on
the sample flight that is what keeps a 4.1-acre mounding problem from
disappearing into a nine-zone soil group.

The report-facing colours are **by category, not by urgency**, and separate from
the severity colours the agronomists use while annotating. Nothing is coloured to
mean "healthy": every colour on the map marks something that was flagged.

### How it is built

The report is one document, `templates/report_v2.html`, composed as real A4
sheets: each sheet is exactly 210 x 297 mm and the page box has no margin of its
own. The browser shows those sheets, the browser prints those sheets, and
WeasyPrint turns those sheets into pages — so the screen, the print dialog and
the downloaded PDF cannot drift apart. `report.html` wraps the document in the
app's chrome (all of it marked `.no-print`), and `report_print.html` is a bare
shell with no styling of its own.

Two things hold that guarantee up:

* **Montserrat is bundled**, not fetched from a CDN. If WeasyPrint fell back to
  a system face while the browser used Montserrat, the same paragraph would wrap
  at a different word and the page breaks would stop matching. It is the only
  typeface in the report — readings are set apart by tabular figures
  (`font-variant-numeric` / `font-feature-settings: 'tnum'`, which Montserrat
  carries) and by tracking, rather than by a second family.
* **Findings are paginated in Python** (`report_data.paginate`), not left to the
  renderer, so a card is never split and both engines break in the same places.

Below 820px the sheets stop pretending to be paper: fixed millimetre heights
would either clip the content or leave a long blank gap, so the sheet reflows and
the detail tables become cards. The rest of the application is responsive
throughout.

**Download** produces a real file named `Farm Name_Crop Name_Season Year_Flight No.pdf` —
`Content-Disposition: attachment` plus a matching `download` attribute on the
link, so it saves straight to disk with no viewer tab and no Save-as dialog.
Renders take about a second, so each one is cached against a key derived from
the report's own content; edit any finding and the cache invalidates itself.
The boot log states whether server-side PDF is available, and if it is not, the
report page says so rather than letting a print dialog appear unexplained.

Every flight of a season shares a farm, a crop and a season, so the **flight
number is part of the name**: without it each new report would land on top of the
last one in the farmer's downloads folder.

The report also carries a **season page** once two or more flights of a season
have findings: the field health score plotted flight by flight, the mix of marked
zones per flight, and a record of every flight in the season. A single flight
still reads as a baseline, and a flight whose CSV has not been imported yet is
left out of the trend rather than plotted as a zero, which would read as a
collapse in field health rather than as missing data.

The **field health score** on the cover is the share of the field in good shape,
counting watch zones at 55% and zones needing action at 0%. The report prints
that definition on its closing page, so a farmer can check the number rather
than trust it.

## Security headers

Every response carries the headers a browser uses to defend the page. They are
set in `app.py` rather than at the proxy, so they travel with the application to
whatever host it is deployed on.

| Header | Value |
| --- | --- |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains; preload` |
| `Content-Security-Policy` | `default-src 'self'`, with the Tailwind and Google Fonts origins allowed |
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | camera, microphone, geolocation and the rest denied |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Resource-Policy` | `same-origin` |

Two of those are worth explaining.

**HSTS is withheld over plain HTTP** and sent only when the request arrived over
HTTPS, directly or through a proxy's `X-Forwarded-Proto`. A browser ignores it on
an insecure connection anyway, and sending it in development would pin a machine
to `https://localhost`.

**`Referrer-Policy` is not cosmetic here.** A farmer's report link contains a
share token, and that token *is* the credential. Without this header, following
any outbound link from a report page would put the full URL — token included — in
a `Referer` header sent to somebody else's server.

The CSP still permits `'unsafe-inline'` and `'unsafe-eval'` for scripts, because
Tailwind is loaded from its CDN and compiles styles in the browser. Tightening
that means moving to a compiled stylesheet, which is a build-step change rather
than a header change.

## Project layout
```
shamba-tracker/
├── app.py             # routes + app factory
├── models.py          # User, Farm, Flight, Finding (SQLAlchemy)
├── parsing.py         # CSV parsing + colour classification
├── integrations.py    # email + WhatsApp (env-key based, simulate if absent)
├── pdf_gen.py         # WeasyPrint PDF (graceful fallback)
├── report_data.py     # field health score, banding, season trend, pagination
├── bulk_import.py     # CSV/Excel import of farms and flights
├── homepage.py        # editable homepage fields, defaults and helpers
├── aggregation.py     # groups findings into the patterns the report is built from
├── schema.py          # additive migrations + share-token backfill
├── templates/         # Tailwind UI + report templates
│   ├── report_doc.html    # THE report — screen, print and PDF, one file
│   ├── report_v2.html     # THE report — three pages, screen, print and PDF
│   ├── report.html        # app chrome around the document
│   ├── report_print.html  # bare wrapper WeasyPrint renders
│   ├── import.html        # bulk import upload + preview
│   └── homepage_edit.html # admin editor for the public homepage
├── static/img/        # Acre logo (transparent + white)
├── static/fonts/      # Montserrat, bundled (see below)
├── samples/           # sample DroneDeploy CSV + annotated map
├── tests/             # run each with `python tests/<name>.py`
├── Dockerfile         # Railway build (installs WeasyPrint libs)
├── requirements.txt
└── .env.example
```
