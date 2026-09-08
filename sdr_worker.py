"""Lean LeadEngine SDR worker.

Two jobs run from GitHub Actions:
1) poll Gmail for replies and classify them with the AI
2) prepare/send a small daily batch of personalized first-touch emails

Sending is OFF by default. Enable only after Gmail OAuth + AI secrets are configured
and the first dry-run looks correct.
"""
from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import formataddr

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
FROM_NAME = os.environ.get("SDR_FROM_NAME", "Ameer")
DRY_RUN = os.environ.get("SDR_DRY_RUN", "true").lower() == "true"


def now():
    return datetime.now(timezone.utc).isoformat()


def sb(method, path, *, params=None, body=None, prefer=None):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    r = requests.request(method, f"{SUPABASE_URL}/rest/v1/{path}", headers=headers, params=params, json=body, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Supabase {method} {path} failed {r.status_code}: {r.text[:500]}")
    return r.json() if r.text else None


def gmail_token():
    if not all([GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN]):
        raise RuntimeError("Missing GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, or GOOGLE_REFRESH_TOKEN")
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": GOOGLE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Google OAuth refresh failed {r.status_code}: {r.text[:500]}")
    return r.json()["access_token"]


def gmail_headers(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def gmail_api(token, method, path, *, params=None, body=None):
    headers = gmail_headers(token)
    if body is not None:
        headers["Content-Type"] = "application/json"
    r = requests.request(method, f"https://gmail.googleapis.com/gmail/v1/users/me/{path}", headers=headers, params=params, json=body, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Gmail {method} {path} failed {r.status_code}: {r.text[:500]}")
    return r.json() if r.text else None


def header_value(headers, name):
    wanted = name.lower()
    for h in headers or []:
        if h.get("name", "").lower() == wanted:
            return h.get("value", "")
    return ""


def decode_b64url(value):
    if not value:
        return ""
    value += "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value.encode()).decode("utf-8", errors="replace")


def extract_text(payload):
    mime = payload.get("mimeType", "")
    if mime == "text/plain" and payload.get("body", {}).get("data"):
        return decode_b64url(payload["body"]["data"])
    parts = payload.get("parts") or []
    for part in parts:
        text = extract_text(part)
        if text:
            return text
    if mime == "text/html" and payload.get("body", {}).get("data"):
        html = decode_b64url(payload["body"]["data"])
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def openai_text(prompt):
    if not OPENAI_API_KEY:
        raise RuntimeError("Missing OPENAI_API_KEY")
    r = requests.post("https://api.openai.com/v1/responses", headers={
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }, json={
        "model": OPENAI_MODEL,
        "input": prompt,
    }, timeout=90)
    if not r.ok:
        raise RuntimeError(f"OpenAI failed {r.status_code}: {r.text[:700]}")
    data = r.json()
    text = data.get("output_text")
    if text:
        return text
    chunks = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                chunks.append(content.get("text", ""))
    return "\n".join(chunks).strip()


def extract_json(text):
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("AI did not return JSON")
    return json.loads(match.group(0))


def qualify_and_write(lead):
    prompt = f"""You are the SDR for a small automation business. Create ONE concise cold email to a business owner.
Do not invent facts. Use only the supplied lead facts. The goal is to start a conversation, not hard-sell.
Return JSON only: {{\"decision\":\"send|skip\",\"subject\":\"...\",\"body\":\"...\"}}.
Skip if the business lacks enough evidence for a relevant message.

Lead facts:
Business: {lead.get('business_name')}
Niche: {lead.get('niche_name')}
City/Country: {lead.get('city')} / {lead.get('country')}
Website: {lead.get('website_url')}
Pain score: {lead.get('pain_score')}
Pain reason: {lead.get('pain_reason')}
WhatsApp: {lead.get('has_whatsapp_button')}
Chatbot: {lead.get('has_website_chatbot')}
Inquiry form: {lead.get('has_inquiry_form')}
After-hours contact: {lead.get('has_afterhours_contact')}
Notes: {lead.get('notes')}

Rules: 70-120 words max. Plain text. No fake compliment. No fabricated observation. No links. No attachments. One clear low-friction call to action."""
    result = extract_json(openai_text(prompt))
    if result.get("decision") != "send":
        return None
    return str(result.get("subject", "")).strip(), str(result.get("body", "")).strip()


def classify_reply(lead, body):
    prompt = f"""Classify this prospect reply for an SDR.
Return JSON only: {{\"classification\":\"hot|warm|cold|unsubscribe|bounce\",\"buying_intent\":0-100,\"summary\":\"one sentence\"}}.
HOT means clear buying intent, request for price/demo/call, or asks how to proceed.
WARM means interested but not ready.
COLD means not interested or irrelevant.
UNSUBSCRIBE means asks to stop/remove/contact no more.
BOUNCE means delivery failure.

Business: {lead.get('business_name')}
Reply:
{body[:6000]}"""
    return extract_json(openai_text(prompt))


def send_email(token, to_email, subject, body):
    msg = MIMEText(body, "plain", "utf-8")
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["From"] = formataddr((FROM_NAME, os.environ.get("GMAIL_FROM_EMAIL", "")))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
    return gmail_api(token, "POST", "messages/send", body={"raw": raw})


def process_replies(token):
    data = gmail_api(token, "GET", "messages", params={"q": "is:unread in:inbox newer_than:2d -from:me", "maxResults": 50})
    processed = 0
    for item in data.get("messages", []):
        msg_id = item["id"]
        msg = gmail_api(token, "GET", f"messages/{msg_id}", params={"format": "full"})
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])
        sender = header_value(headers, "From")
        sender_email = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}", sender, re.I)
        if not sender_email:
            continue
        email = sender_email.group(0).lower()
        leads = sb("GET", "leads", params={"select": "id,business_name,niche_id,email", "email": f"eq.{email}", "limit": "1"}) or []
        if not leads:
            continue
        lead = leads[0]
        body = extract_text(payload).strip()
        subject = header_value(headers, "Subject")
        cls = classify_reply(lead, body)
        classification = cls.get("classification", "cold")
        if classification == "unsubscribe":
            sb("POST", "sdr_suppressions", body={"email": email, "reason": "unsubscribe"}, prefer="resolution=ignore-duplicates")
        outreach_status = {"hot": "hot", "warm": "warm", "cold": "stopped", "unsubscribe": "suppressed", "bounce": "suppressed"}.get(classification, "warm")
        sb("POST", "sdr_email_messages", body={
            "lead_id": lead["id"], "direction": "inbound", "gmail_message_id": msg_id,
            "gmail_thread_id": msg.get("threadId"), "from_email": email,
            "to_email": os.environ.get("GMAIL_FROM_EMAIL"), "subject": subject,
            "body_text": body[:12000], "status": "processed", "ai_classification": classification,
            "ai_summary": str(cls.get("summary", ""))[:500], "buying_intent": cls.get("buying_intent"),
            "received_at": now(), "processed_at": now()
        }, prefer="return=minimal")
        sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body={"outreach_status": outreach_status, "last_checked_at": now()})
        gmail_api(token, "POST", f"messages/{msg_id}/modify", body={"removeLabelIds": ["UNREAD"]})
        processed += 1
    print(f"SDR replies processed: {processed}")


def send_batch(token):
    settings = (sb("GET", "sdr_settings", params={"select": "*", "id": "eq.true", "limit": "1"}) or [{}])[0]
    enabled = bool(settings.get("sending_enabled")) and not DRY_RUN
    cap = int(settings.get("daily_send_cap") or 30)
    today = datetime.now(timezone.utc).date().isoformat()
    sent_today = sb("GET", "sdr_email_messages", params={"select": "id", "direction": "eq.outbound", "status": "eq.sent", "sent_at": f"gte.{today}T00:00:00+00:00"}) or []
    remaining = max(0, cap - len(sent_today))
    if remaining == 0:
        print("Daily send cap reached.")
        return
    leads = sb("GET", "leads", params={
        "select": "id,business_name,full_name,email,website_url,city,country,pain_score,pain_reason,has_whatsapp_button,has_website_chatbot,has_inquiry_form,has_afterhours_contact,notes,niche_id,niches(name)",
        "email": "not.is.null", "outreach_status": "eq.pending", "order": "pain_score.desc,created_at.asc", "limit": str(min(remaining, 50))
    }) or []
    prepared = 0
    for lead in leads:
        email = (lead.get("email") or "").strip().lower()
        if not email:
            continue
        blocked = sb("GET", "sdr_suppressions", params={"select": "id", "email": f"ilike.{email}", "limit": "1"}) or []
        if blocked:
            continue
        niche = lead.get("niches") or {}
        lead["niche_name"] = niche.get("name") if isinstance(niche, dict) else ""
        draft = qualify_and_write(lead)
        if not draft:
            sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body={"outreach_status": "stopped", "notes": ((lead.get("notes") or "") + " | AI skipped outreach")[:1000]})
            continue
        subject, body = draft
        if not enabled:
            print(f"DRY RUN | {email} | {subject}\n{body}\n---")
            prepared += 1
            continue
        result = send_email(token, email, subject, body)
        sb("POST", "sdr_email_messages", body={
            "lead_id": lead["id"], "direction": "outbound", "gmail_message_id": result.get("id"),
            "gmail_thread_id": result.get("threadId"), "from_email": os.environ.get("GMAIL_FROM_EMAIL"),
            "to_email": email, "subject": subject, "body_text": body, "status": "sent", "sent_at": now()
        }, prefer="return=minimal")
        sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body={"outreach_status": "contacted", "first_contact_at": now(), "last_contacted_at": now(), "last_checked_at": now()})
        prepared += 1
    print(f"SDR outbound processed: {prepared} | enabled={enabled} | remaining={remaining}")


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY")
    token = gmail_token()
    process_replies(token)
    send_batch(token)


if __name__ == "__main__":
    main()
