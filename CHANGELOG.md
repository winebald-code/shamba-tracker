# SHAMBA Tracker — Changelog

## 2.0 — August 2026

### Access is now approved, not automatic
- Signing up creates a **pending** account that cannot hold a session. `User.is_active` is overridden to `active AND status == 'approved'`, so Flask-Login blocks it on every request rather than only at the login form.
- The very first account is the exception: with nobody to approve it, it is created as an approved admin, and the signup screen says so before you submit.
- New `/pending` screen shows the request, the role asked for, and the status. Signing in before approval lands here rather than on an error.
- Admins are emailed on each new request; the requester is emailed on approval. Both are best-effort — the queue in **People** is the mechanism, the email is a convenience.
- **People** gained an approval queue: approve with a role, or decline. Declined requests are kept, not deleted, and can be approved later.

### Four roles, four dashboards
- Added **Field Operator** alongside Admin, Agronomist and Customer Success.
- `/dashboard` now redirects to the dashboard for your role, so an old bookmark still works if your role changes.
- **Admin** — approval queue first, workspace counts, issues by category, team by role, delivery channel readiness.
- **Agronomist** — findings to complete, flights ready to generate, flights awaiting import.
- **Customer Success** — to review, to deliver, awaiting receipt, farms with no contact on file.
- **Field Operator** — flights logged, acres covered, flights missing a map or an export.
- Authorization moved into a single `PERMISSIONS` table enforced by a `@requires(action)` decorator. `can()` is injected into templates so the interface hides what a role cannot do, but the decorator is the control.

### Profile management
- New `/profile` with full self-service: read, update (name, email, job title, phone, location, about), change password, and delete the account behind an email-confirmation dialog.
- Admins have the same operations over every account from **People**, plus role, activation and removal.
- The last approved admin cannot be demoted, deactivated or deleted, from either screen. Nobody can deactivate themselves.

### Sharing a report actually works now
Provider delivery was failing in production: Resend refused with a 403 because the sending domain was not verified, and Twilio refused with a 422 because no trial number was assigned. Both are outside engineering's control and slow to resolve, so the report could not go out at all.

- Added **hand-off delivery** — three real buttons that open the sender's own applications with everything pre-filled: `wa.me` for WhatsApp, `mailto:` for the default mail client, and Gmail compose for browser mailboxes. No API key, no verified domain, no approved number.
- `normalise_phone()` converts a number as typed into the digits `wa.me` needs: `0712345678` becomes `254712345678` using `DEFAULT_COUNTRY_CODE`; a leading `+` is taken as already international.
- The farmer's report link is shown with a copy button that falls back to a hidden textarea where the clipboard API is unavailable.
- `POST /flights/<id>/mark-shared` records the hand-off through `sendBeacon`, falling back to `fetch` with `keepalive`, since the click is also navigating away. It is fire-and-forget: a failed record never blocks the share.
- Provider delivery now marks a flight **Sent** only when a channel actually succeeded. When every provider refuses, their own explanations are shown and the message points at the hand-off buttons.
- Both dashboards show which providers are configured, so a missing key is visible before a report has to go out.

### The site
- **Hamburger menu on the homepage** below 768 px, opening a drawer with the section links and both calls to action. Closes on selection, on Escape, on the scrim, and on resize to desktop.
- Rebuilt the homepage: full-bleed aerial hero with an annotated report as the signature element, a six-step flow, the colour code, and a delivery section that is honest about which path needs setup.
- Added **IBM Plex Mono** for readings, counts, hex values, seasons and eyebrow labels.
- Scroll reveal is now progressive enhancement. It was `.reveal { opacity: 0 }` in the stylesheet with a script to bring it back — any failure in that script left whole sections blank. Content is now visible by default and only starts hidden once the script has confirmed it can reveal it again.
- Visible focus rings on every interactive element; `prefers-reduced-motion` suppresses all motion.

### Under the hood
- New `schema.py` reconciles columns additively at boot, so this release lands on the live database without a migration tool. It only ever adds, always with a `DEFAULT`, dialect-neutrally, and is safe under concurrent workers. Existing accounts are backfilled to `approved` so nobody is locked out by the new gate.
- `postgres://` URLs are rewritten to `postgresql://` for SQLAlchemy 2.
- The `next` parameter on login is honoured only when it starts with `/`.
- Uploaded filenames pass through `secure_filename()` on the way out as well as in.
- 413 now renders a branded page instead of a stack trace.
- Ready-for-Review notifies every Customer Success user and admin, not only the first admin found.
- New `tests/test_flow.py`: 53 end-to-end checks over the approval flow, role dashboards, permissions, profile CRUD, share links and the public report, plus a sweep asserting no GET route returns 5xx.
