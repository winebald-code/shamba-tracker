"""
Delivery integrations for SHAMBA Tracker.

Email (SendGrid or Resend) and WhatsApp (Twilio or Meta Cloud API).
API keys are read from environment variables. When a provider's keys are
absent, the send is SIMULATED (logged, returns ok) so the whole flow is
demonstrable before real credentials are added. Drop your keys into the
environment (see .env.example) and the same functions send for real.
"""
import os
import base64
import json
import urllib.request
import urllib.error


def _env(*names):
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


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
    sender = os.environ.get("MAIL_FROM", "reports@acre-insights.com")

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
        except urllib.error.URLError as e:
            return False, f"Resend error: {e}"

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
        except urllib.error.URLError as e:
            return False, f"SendGrid error: {e}"

    # no keys -> simulate
    return True, f"[Simulated email] to {to_addr}. Add RESEND_API_KEY or SENDGRID_API_KEY to send for real."


# ---------------------------------------------------------------- whatsapp
def send_whatsapp(to_phone, body):
    twilio_sid = _env("TWILIO_ACCOUNT_SID")
    twilio_token = _env("TWILIO_AUTH_TOKEN")
    twilio_from = os.environ.get("TWILIO_WHATSAPP_FROM")   # e.g. whatsapp:+14155238886
    meta_token = _env("WHATSAPP_TOKEN", "META_WHATSAPP_TOKEN")
    meta_phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")

    if not to_phone:
        return False, "No recipient phone on file."

    if twilio_sid and twilio_token and twilio_from:
        try:
            import urllib.parse
            data = urllib.parse.urlencode({
                "From": twilio_from,
                "To": f"whatsapp:{to_phone}",
                "Body": body,
            }).encode()
            url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
            auth = base64.b64encode(f"{twilio_sid}:{twilio_token}".encode()).decode()
            req = urllib.request.Request(url, data=data,
                                         headers={"Authorization": f"Basic {auth}"})
            urllib.request.urlopen(req, timeout=20)
            return True, "Sent via Twilio WhatsApp."
        except urllib.error.URLError as e:
            return False, f"Twilio error: {e}"

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
        except urllib.error.URLError as e:
            return False, f"Meta WhatsApp error: {e}"

    return True, f"[Simulated WhatsApp] to {to_phone}. Add Twilio or Meta WhatsApp keys to send for real."
