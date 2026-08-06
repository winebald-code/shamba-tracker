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
4. **Generate** — one click produces the branded report (web + **PDF**), named `Farm Name_Crop Name_Season Year.pdf`.
5. **Deliver** — send by email (report attached) and WhatsApp (link), with a message in Acre's voice.
6. **Acknowledge** — the farmer opens a tokenised public link, reads the report, and taps to confirm receipt — logged back to the dashboard.

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
- **Password:** `AcreInsights2026`

(Override with `ADMIN_EMAIL` / `ADMIN_PASSWORD` env vars.)

> **PDF note:** WeasyPrint needs system libraries (Pango/Cairo). If they're not
> installed locally, the app still runs — it falls back to the print-friendly
> web report (your browser's *Save as PDF*). The included **Dockerfile installs
> everything**, so server-side PDF works on Railway out of the box.

### Try it with the sample flight
A sample DroneDeploy export and annotated map are in `samples/`. Create a farm
and a flight, upload `samples/sample_dronedeploy_export.csv`, then
`samples/sample_annotated_map.jpg` as the map — complete the findings and
generate.

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

## The report

The report is one document, `templates/report_doc.html`, composed as real A4
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

**Download** produces a real file named `Farm Name_Crop Name_Season Year.pdf` —
`Content-Disposition: attachment` plus a matching `download` attribute on the
link, so it saves straight to disk with no viewer tab and no Save-as dialog.
Renders take about a second, so each one is cached against a key derived from
the report's own content; edit any finding and the cache invalidates itself.
The boot log states whether server-side PDF is available, and if it is not, the
report page says so rather than letting a print dialog appear unexplained.

The **field health score** on the cover is the share of the field in good shape,
counting watch zones at 55% and zones needing action at 0%. The report prints
that definition on its closing page, so a farmer can check the number rather
than trust it.

## Project layout
```
shamba-tracker/
├── app.py             # routes + app factory
├── models.py          # User, Farm, Flight, Finding (SQLAlchemy)
├── parsing.py         # CSV parsing + colour classification
├── integrations.py    # email + WhatsApp (env-key based, simulate if absent)
├── pdf_gen.py         # WeasyPrint PDF (graceful fallback)
├── report_data.py     # field health score, banding, sheet pagination
├── schema.py          # additive migrations + share-token backfill
├── templates/         # Tailwind UI + report templates
│   ├── report_doc.html    # THE report — screen, print and PDF, one file
│   ├── report.html        # app chrome around the document
│   └── report_print.html  # bare wrapper WeasyPrint renders
├── static/img/        # Acre logo (transparent + white)
├── static/fonts/      # Montserrat, bundled (see below)
├── samples/           # sample DroneDeploy CSV + annotated map
├── Dockerfile         # Railway build (installs WeasyPrint libs)
├── requirements.txt
└── .env.example
```
