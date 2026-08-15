# SHAMBA Tracker

**Flight Report Automation — a product of Acre Insights.**

Turn a finished DroneDeploy scouting flight into a clean, Acre-branded field
report — generated the same day, delivered over email and WhatsApp, and
confirmed by the farmer with a tap.

The report is **three pages**: a summary that groups the flight's annotations
into the patterns actually present in them, the farm map, and the full detail.
It says what was seen, groups it honestly, and leaves the decision with the
farmer — see [The aggregation layer](#the-aggregation-layer).

Built with Flask · SQLite · Tailwind CSS · vanilla JS · WeasyPrint. Ready for Railway.

---

## What it does

1. **Farms & flights** — keep a record of each farm, its farmer contact, and every scouting flight (unique by *farm + season + flight number*).
2. **Import** — upload the DroneDeploy annotation **CSV**; SHAMBA Tracker parses each pin, classifies its colour into the Acre colour code, normalises the area, and splits the comment into *observation / likely cause / recommendation*.
3. **Review portal** — complete and correct every finding inline. The report can only be generated once all findings are complete.
4. **Generate** — one click produces the branded three-page report (web + **PDF**), named `Farm Name_Crop Name_Season Year_Flight No.pdf`. The summary page is grouped from the agronomist's own annotations, never from anything added on top of them. A report needs both complete findings and the annotated map snapshot: the findings are numbered against the map, so without it they point at nothing.
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
python tests/test_aggregate.py  # the patterns, the voice, and the guardrail
python tests/test_security.py   # response headers, the CSP, and CSRF
python tests/test_storage.py    # S3-backed uploads (needs `pip install moto boto3`)
```

`test_aggregate.py` runs the real IPM Farm flight through the aggregation layer
and checks it produces the four patterns the specification describes — so a
change that quietly re-flattens the report fails the suite rather than reaching
a farmer. `test_security.py` runs with CSRF deliberately switched on, since the
other suites disable it via `TESTING` in order to post forms directly.

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

Three pages, in the farmer's hands:

| Page | What it carries |
|---|---|
| 1 — Field scouting summary | What was scouted, the issue categories with counts and acres, the patterns across them, and the areas that may be worth investigating. No per-zone entries at all. |
| 2 — Farm map | The annotated snapshot, the category legend, and a zone index. |
| 3 — Detailed findings | Every annotation exactly as the agronomist wrote it, grouped under the pattern it belongs to. Runs to a fourth page only if a flight has more findings than three pages hold. |

The report is one document, `templates/report_doc.html`, composed as real A4
sheets. The browser shows those sheets, the browser prints those sheets, and
WeasyPrint turns those sheets into pages — so the screen, the print dialog and
the downloaded PDF cannot drift apart. `report.html` wraps the document in the
app's chrome (all of it marked `.no-print`), and `report_print.html` is a bare
shell with no styling of its own.

Three things hold that guarantee up:

* **Montserrat is bundled**, not fetched from a CDN. If WeasyPrint fell back to
  a system face while the browser used Montserrat, the same paragraph would wrap
  at a different word and the page breaks would stop matching. It is the only
  typeface in the report — readings are set apart by tabular figures
  (`font-variant-numeric` / `font-feature-settings: 'tnum'`, which Montserrat
  carries) and by tracking, rather than by a second family.
* **Findings are paginated in Python** (`aggregate.paginate`), not left to the
  renderer, so a row is never split and both engines break in the same places.
* **No sheet has a fixed height.** WeasyPrint clips whatever overruns a fixed
  height, whatever `overflow` says — so a farm with unusually long annotations
  would have lost a recommendation off the bottom of a page with nothing to show
  for it. The page footer lives in the page's own bottom margin
  (`@page { @bottom-right }`) and takes its number from the renderer's counters,
  which lets a long sheet flow onto another page that is still footed and still
  numbered correctly.

On a phone the sheets stop being sheets. Below 880px the document reflows to the
width of the device: the layout tables become blocks, and the findings table
becomes one labelled card per area. Print and PDF are untouched, because
WeasyPrint never applies a screen media query — what reflows is only ever what
is being read on a phone, which for a report delivered over WhatsApp is most of
the time.

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

## The aggregation layer

`aggregate.py` is the step between the annotations and the report. It exists
because a flight of fifteen pins can be fifteen ways of writing down four
things, and printing all fifteen makes a varied field read as one repeated
sentence while burying the largest finding on the farm in the middle of a list.

It **groups** the agronomist's annotations into the patterns present in their own
text, counts the areas and acres in each, and composes one pattern-level
sentence and one suggested next step per pattern. Three rules govern it:

1. **It only summarises and combines what the agronomist wrote.** No cause,
   diagnosis or recommendation is introduced that is not already in the source
   annotations. Every phrase a generated sentence uses is carried on the pattern
   as `source_phrases`, and `aggregate.unsourced_phrases()` returns any that
   cannot be traced back — an empty list is the pass condition, and
   `tests/test_aggregate.py` asserts it.
2. **It is deterministic.** The same annotations produce the same report every
   time. Classification is evidence counting over the agronomist's own words,
   not a model call, which is what makes rule 1 checkable rather than merely
   intended.
3. **It does not change how the agronomist works.** Input is exactly what
   DroneDeploy already captures and the review screen already stores.

Classification reads the text rather than the category enum, because the enum is
often one broad label across a whole flight — which is the flattening this
layer exists to undo. Evidence is counted one *statement* at a time, not one
keyword at a time: "Nutrient deficiencies, soil condition" is two soil
statements, while "nutrient uptake" is one even though two words in it are on
the soil list. When two categories tie, the cause the agronomist wrote first
leads.

Patterns are then found *within* a category by single-linkage similarity on the
cause and recommendation wording, so nine differently-worded soil notes form one
pattern. A pattern is never folded into a different category: on the reference
flight the 4.1-acre overmounding area stays its own pattern rather than joining
the nine soil areas, because consolidating it away would hide the single largest
finding on the farm.

Zones are numbered largest area first, so zone 1 is the biggest thing on the
field and the number means something when read off the map.

### The report-facing colour system

Separate from the internal severity colour code above, which stays exactly as it
is for triage. The report map is keyed by **category**, because what a farmer
needs to read spatially is what kind of thing was seen and where, not how
urgent somebody graded it. No colour means "healthy" — every colour on the map
marks something the agronomist actually flagged.

| Category | Colour |
|---|---|
| Irrigation / moisture | Blue |
| Soil fertility / nutrition | Amber |
| Crop establishment | Terracotta |
| Pest / disease | Deep red |
| Weeds | Slate green |
| Needs investigation | Purple |

### The review checkpoint

Page 1 is assembled rather than typed, so somebody reads it before a farmer
does. The report screen says which part was assembled and from how many
annotations, and points at the review screen for fixing anything that reads
wrong. This is not a new step — it is the check that already happens before a
report goes out over WhatsApp or email.

## Security

Every response carries the headers a scanner looks for, set in `security.py` and
asserted by name in `tests/test_security.py`:

| Header | Value |
|---|---|
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains`, sent only over https |
| `Content-Security-Policy` | `default-src 'none'` with a per-request script nonce |
| `X-Frame-Options` | `SAMEORIGIN`, alongside `frame-ancestors 'none'` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | every unused browser feature denied |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Resource-Policy` | `same-origin` |

The CSP is **nonce-based rather than `unsafe-inline`**, which is the only kind
that actually stops injected script. Three things follow from that, and they are
the reason the front end looks the way it does:

* **No CDNs.** Tailwind is built to `static/css/app.css` from these same
  templates, and the fonts are the bundled Montserrat faces. A policy that has to
  whitelist a CDN's script is a policy that trusts that CDN with every session in
  the application — and the Tailwind play CDN warns against production use in any
  case. Rebuild the stylesheet with:

  ```bash
  npm install tailwindcss@3.4.17
  npx tailwindcss -c build/tailwind.config.js -i build/tailwind.input.css \
                  -o static/css/app.css --minify
  ```

* **No inline event handlers.** A nonce cannot be attached to an `onclick`, so
  every interaction is declared as a data attribute (`data-open`, `data-confirm`,
  `data-autosubmit`, …) and handled by one delegated listener at the foot of
  `base.html`.

* **`style-src` still allows inline styles.** The report colours every category
  swatch and table header through a style attribute computed from the data, and
  style attributes cannot carry a nonce. It is also the least dangerous inline
  source: CSS cannot exfiltrate a session on its own.

`Cross-Origin-Embedder-Policy` is deliberately not set. It would require every
embedded resource to opt in, and buys an application with no cross-origin assets
nothing but the chance of breaking a farmer's map image on an older browser.

**CSRF.** Every POST, PUT, PATCH and DELETE needs a token: a hidden
`csrf_field()` on all 33 posting forms, an `X-CSRFToken` header on `fetch`, and
the token in the body for `sendBeacon`, which cannot set headers. It is disabled
under `TESTING` so the suites can post directly — `tests/test_security.py`
switches it back on and checks that a request without a token is refused.

**Cookies** are `HttpOnly`, `SameSite=Lax`, and `Secure` outside of tests.
**`SECRET_KEY`** no longer has a shipped default: a published constant lets
anyone forge a session for any deployment that never changed it, so an unset key
becomes a random per-process one and says so loudly in the log.

The right-click and Ctrl+U/Ctrl+S blocker has been removed. It stopped nobody,
it blocked Ctrl+S on the very report the fallback print path asks people to
save, and on a phone `contextmenu` is the long-press that copies text and saves
the map image.

## Project layout
```
shamba-tracker/
├── app.py             # routes + app factory
├── models.py          # User, Farm, Flight, Finding (SQLAlchemy)
├── parsing.py         # CSV parsing + colour classification
├── aggregate.py       # V2 aggregation: patterns, categories, page-3 pagination
├── security.py        # response headers, CSP nonce, CSRF
├── integrations.py    # email + WhatsApp (env-key based, simulate if absent)
├── pdf_gen.py         # WeasyPrint PDF (graceful fallback)
├── report_data.py     # internal read: field health score, banding, season trend
├── bulk_import.py     # CSV/Excel import of farms and flights
├── homepage.py        # editable homepage fields, defaults and helpers
├── schema.py          # additive migrations + share-token backfill
├── templates/         # UI + report templates
│   ├── base.html          # head, flash messages, the delegated behaviour layer
│   ├── report_doc.html    # THE report — screen, print and PDF, one file
│   ├── report.html        # app chrome + the pre-send review checkpoint
│   ├── report_print.html  # bare wrapper WeasyPrint renders
│   ├── import.html        # bulk import upload + preview
│   └── homepage_edit.html # admin editor for the public homepage
├── static/css/app.css # built stylesheet — no CDN at runtime
├── static/img/        # Acre logo (transparent + white)
├── static/fonts/      # Montserrat, bundled (see The report)
├── build/             # Tailwind config + input for rebuilding app.css
├── samples/           # sample DroneDeploy CSV + annotated map
├── tests/             # run each with `python tests/<name>.py`
├── Dockerfile         # Railway build (installs WeasyPrint libs)
├── requirements.txt
└── .env.example
```

`static/css/app.css` is committed, so nothing needs Node at deploy time. It only
needs rebuilding when a template gains a utility class it has never used before
— see **Security** for the command.
