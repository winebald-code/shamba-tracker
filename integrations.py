"""
Delivery integrations for SHAMBA Tracker.

Email (Resend or SendGrid) and WhatsApp (Twilio or Meta Cloud API). Keys are
read from environment variables (see .env.example). When a send fails, the
caller receives the provider's own error message so the cause is clear. When a
provider's keys aren't set, the report is simply prepared without sending.
"""
import os
import base64
import json
import urllib.request
import urllib.error


def _env(*names):
    for n in names:
        v = os.environ.get(n)
        if v and v.strip():
            return v.strip()          # trim stray spaces/newlines from pasted keys
    return None


def _http_error(provider, e):
    """
    Turn a urllib error into a short, actionable message that includes the
    provider's own explanation (e.g. Resend telling you the sender domain
    isn't verified) instead of a bare 'HTTP Error 403: Forbidden'.
    """
    code = getattr(e, "code", None)
    body = ""
    try:
        raw = e.read()
        if raw:
            body = raw.decode("utf-8", "replace")
    except Exception:
        pass
    msg = ""
    try:
        j = json.loads(body)
        msg = j.get("message") or j.get("error") or j.get("detail") or ""
        if not msg and isinstance(j.get("errors"), list) and j["errors"]:
            first = j["errors"][0]
            msg = first.get("message") if isinstance(first, dict) else str(first)
        if isinstance(msg, dict):
            msg = msg.get("message") or str(msg)
    except Exception:
        msg = body
    msg = (str(msg).strip() or getattr(e, "reason", "") or str(e))[:300]

    hint = ""
    if code == 401:
        hint = " — check the API key / credentials are correct and have no extra spaces."
    elif code == 403 and provider.startswith(("Resend", "SendGrid")):
        hint = " — set MAIL_FROM to an address on a domain you've verified with the provider (a domain you own, not a free mailbox)."
    elif code == 422 and provider.startswith("Twilio"):
        hint = (" — Twilio trial accounts can only message verified numbers. Verify the recipient in the "
                "Twilio console, or use the WhatsApp sandbox and have the recipient send the join code first.")
    return f"{provider} {code or ''}: {msg}{hint}".replace(" : ", ": ")


def acre_voice_message(flight, kind="whatsapp", base_url=None):
    """Compose the farmer-facing message in Acre Insights voice."""
    farm = flight.farm.name
    farmer = (flight.farm.farmer_name or "there").split()[0] if flight.farm.farmer_name else "there"
    n = len(flight.findings)
    season = flight.season
    fnum, planned = flight.flight_number, flight.flights_planned
    counts = flight.category_counts()
    breakdown = ", ".join(f"{v} {k.lower()}" for k, v in counts.items())
    link = flight_public_url(flight, base_url)

    lead = (f"Hi {farmer}, this is Acre Insights. Your field report for {farm} "
            f"(flight {fnum} of {planned}, season {season}) is ready — "
            f"{n} point{'s' if n != 1 else ''} to look at"
            + (f": {breakdown}." if breakdown else "."))
    if kind == "email":
        return (lead + "\n\nThe full report is attached, and you can open the "
                "annotated map and confirm you received it here:\n" + link +
                "\n\nReply or call us any time to walk through it together.\n\n— Acre Insights")
    return (lead + f" Open your report and confirm receipt: {link}  "
            "Reply here or call us to walk through it together.")


def flight_public_url(flight, base_url=None):
    base = (base_url or os.environ.get("PUBLIC_BASE_URL", "")).rstrip("/")
    return f"{base}/r/{flight.share_token}" if base else f"/r/{flight.share_token}"


# ---------------------------------------------------------------- email
def send_email(to_addr, subject, body, pdf_bytes=None, pdf_name=None):
    resend_key = _env("RESEND_API_KEY")
    sendgrid_key = _env("SENDGRID_API_KEY")
    # onboarding@resend.dev works with any Resend key for testing (to the account
    # owner's address) without verifying a domain. Set MAIL_FROM to your own
    # verified sender for production.
    sender = _env("MAIL_FROM") or "onboarding@resend.dev"

    if not to_addr:
        return False, "No recipient email on file."

    if resend_key:
        try:
            payload = {"from": sender, "to": [to_addr], "subject": subject, "text": body}
            if pdf_bytes:
                payload["attachments"] = [{
                    "filename": pdf_name or "report.pdf",
                    "content": base64.b64encode(pdf_bytes).decode(),
                }]
            req = urllib.request.Request(
                "https://api.resend.com/emails",
                data=json.dumps(payload).encode(),
                headers={"Authorization": f"Bearer {resend_key}",
                         "Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=20)
            return True, "Sent via Resend."
        except urllib.error.HTTPError as e:
            return False, _http_error("Resend", e) + f" (sending from {sender})"
        except urllib.error.URLError as e:
            return False, f"Resend connection error: {getattr(e, 'reason', e)}"

    if sendgrid_key:
        try:
            data = {
                "personalizations": [{"to": [{"email": to_addr}]}],
                "from": {"email": sender},
                "subject": subject,
                "content": [{"type": "text/plain", "value": body}],
            }
            if pdf_bytes:
                data["attachments"] = [{
                    "content": base64.b64encode(pdf_bytes).decode(),
                    "filename": pdf_name or "report.pdf",
                    "type": "application/pdf",
                }]
            req = urllib.request.Request(
                "https://api.sendgrid.com/v3/mail/send",
                data=json.dumps(data).encode(),
                headers={"Authorization": f"Bearer {sendgrid_key}",
                         "Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=20)
            return True, "Sent via SendGrid."
        except urllib.error.HTTPError as e:
            return False, _http_error("SendGrid", e)
        except urllib.error.URLError as e:
            return False, f"SendGrid connection error: {getattr(e, 'reason', e)}"

    # no keys configured
    return True, f"Email prepared for {to_addr} (no email provider key set)."


# ---------------------------------------------------------------- whatsapp
def send_whatsapp(to_phone, body):
    twilio_sid = _env("TWILIO_ACCOUNT_SID")
    twilio_token = _env("TWILIO_AUTH_TOKEN")
    twilio_from = _env("TWILIO_WHATSAPP_FROM")             # e.g. whatsapp:+14155238886
    meta_token = _env("WHATSAPP_TOKEN", "META_WHATSAPP_TOKEN")
    meta_phone_id = _env("WHATSAPP_PHONE_NUMBER_ID")

    if not to_phone:
        return False, "No recipient phone on file."
    to_phone = to_phone.strip()

    if twilio_sid and twilio_token and twilio_from:
        try:
            import urllib.parse
            frm = twilio_from if twilio_from.startswith("whatsapp:") else f"whatsapp:{twilio_from}"
            data = urllib.parse.urlencode({
                "From": frm,
                "To": f"whatsapp:{to_phone}",
                "Body": body,
            }).encode()
            url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
            auth = base64.b64encode(f"{twilio_sid}:{twilio_token}".encode()).decode()
            req = urllib.request.Request(url, data=data,
                                         headers={"Authorization": f"Basic {auth}"})
            urllib.request.urlopen(req, timeout=20)
            return True, "Sent via Twilio WhatsApp."
        except urllib.error.HTTPError as e:
            return False, _http_error("Twilio", e)
        except urllib.error.URLError as e:
            return False, f"Twilio connection error: {getattr(e, 'reason', e)}"

    if meta_token and meta_phone_id:
        try:
            payload = {
                "messaging_product": "whatsapp",
                "to": to_phone.lstrip("+"),
                "type": "text",
                "text": {"body": body},
            }
            url = f"https://graph.facebook.com/v20.0/{meta_phone_id}/messages"
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Authorization": f"Bearer {meta_token}",
                         "Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=20)
            return True, "Sent via Meta WhatsApp Cloud API."
        except urllib.error.HTTPError as e:
            return False, _http_error("Meta WhatsApp", e)
        except urllib.error.URLError as e:
            return False, f"Meta WhatsApp connection error: {getattr(e, 'reason', e)}"

    return True, f"WhatsApp prepared for {to_phone} (no WhatsApp provider key set)."
