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
4. **Generate** — one click produces the branded report (web + **PDF**), named `FarmName_CropName_SeasonYear`.
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

Railway auto-detects the **Dockerfile**.

1. Push this folder to a GitHub repo.
2. In Railway: **New Project → Deploy from GitHub repo** → pick the repo.
3. Under **Variables**, set the values from `.env.example`. At minimum:
   - `SECRET_KEY` — a long random string
   - `PUBLIC_BASE_URL` — your Railway URL, e.g. `https://shamba-tracker.up.railway.app` (used to build the shareable links in messages)
4. Deploy. The first boot creates the DB and seeds Cathy.

**Persisting data:** SQLite and uploads live on the container filesystem. For
durable storage across deploys, add a Railway **Volume** mounted at `/app`
(or `/app/uploads`), or set `DATABASE_URL` to a Railway Postgres instance.

### Turning on real delivery
Sends are **simulated** until you add provider keys (the full flow is
demonstrable without them). Add whichever you use to Railway Variables:

- **Email:** `RESEND_API_KEY` *(or)* `SENDGRID_API_KEY`, plus `MAIL_FROM`
- **WhatsApp:** `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` + `TWILIO_WHATSAPP_FROM` *(or)* `WHATSAPP_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID`

No code changes needed — the integrations pick up the keys automatically.

---

## Roles
- **Admin** — everything, including user management (Cathy).
- **Agronomist** — farms, flights, findings, reports.
- **Customer Success** — delivery and acknowledgement tracking.

New public sign-ups become Agronomists (the very first ever account becomes Admin). Admins add and manage users under **Users**.

---

## Project layout
```
shamba-tracker/
├── app.py             # routes + app factory
├── models.py          # User, Farm, Flight, Finding (SQLAlchemy)
├── parsing.py         # CSV parsing + colour classification
├── integrations.py    # email + WhatsApp (env-key based, simulate if absent)
├── pdf_gen.py         # WeasyPrint PDF (graceful fallback)
├── templates/         # Tailwind UI + report templates
├── static/img/        # Acre logo (transparent + white)
├── samples/           # sample DroneDeploy CSV + annotated map
├── Dockerfile         # Railway build (installs WeasyPrint libs)
├── requirements.txt
└── .env.example
```
