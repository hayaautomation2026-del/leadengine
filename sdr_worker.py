"""Lean LeadEngine SDR worker.

Four jobs run from GitHub Actions:
1) poll Gmail for replies and classify them with the AI
2) alert the human closer when a reply is HOT
3) process due follow-ups
4) prepare/send a small daily batch of personalized first-touch emails

Sending is OFF by default. Enable only after Gmail OAuth + AI secrets are configured
and the dry-run looks correct.
"""
from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone
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
GMAIL_FROM_EMAIL = os.environ.get("GMAIL_FROM_EMAIL", "")
HANDOFF_EMAIL = os.environ.get("SDR_HANDOFF_EMAIL", GMAIL_FROM_EMAIL)
DRY_RUN = os.environ.get("SDR_DRY_RUN", "true").lower() == "true"

FOLLOWUP_DELAYS_DAYS = (3, 5)
MAX_TOUCHES = 1 + len(FOLLOWUP_DELAYS_DAYS)


def now():
    return datetime.now(timezone.utc).isoformat()


def after_days(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


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


def write_followup(lead, previous_subject, previous_body, followup_number):
    prompt = f"""You are the SDR for a small automation business.
Write follow-up #{followup_number} to a prospect who has not replied yet.

Return JSON only: {{\"body\":\"...\"}}.

Business: {lead.get('business_name')}
Previous subject: {previous_subject}
Previous email:
{previous_body[:4000]}

Rules:
- 40-80 words.
- Plain text.
- Do not invent any new facts.
- Do not use fake urgency, guilt, or pressure.
- Do not add links or attachments.
- Follow-up #1 should be a light reminder with one easy question.
- Follow-up #2 should politely close the loop and leave the door open.
- Do not repeat the full first email."""
    result = extract_json(openai_text(prompt))
    body = str(result.get("body", "")).strip()
    if not body:
        raise ValueError("AI follow-up body was empty")
    return body


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
    msg["From"] = formataddr((FROM_NAME, GMAIL_FROM_EMAIL))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
    return gmail_api(token, "POST", "messages/send", body={"raw": raw})


def build_handoff_alert(lead, reply_subject, reply_body, cls):
    business = (lead.get("business_name") or "Unknown business").strip()
    prospect_email = (lead.get("email") or "unknown").strip()
    phone = (lead.get("phone_number") or "not found").strip()
    whatsapp = (lead.get("whatsapp_number") or "not verified").strip()
    city = (lead.get("city") or "").strip()
    country = (lead.get("country") or "").strip()
    location = ", ".join(x for x in [city, country] if x) or "unknown"
    buying_intent = cls.get("buying_intent")
    score = str(buying_intent) if buying_intent is not None else "unknown"
    summary = str(cls.get("summary") or "HOT reply detected.").strip()
    subject = f"HOT LEAD: {business} | {score}/100"
    body = f"""HOT LEAD — HUMAN ACTION NEEDED

Business: {business}
Location: {location}
Prospect email: {prospect_email}
Phone: {phone}
WhatsApp: {whatsapp}
Buying intent: {score}/100
AI summary: {summary}
Reply subject: {reply_subject or '(no subject)'}

Prospect reply:
{reply_body[:5000]}

Recommended action:
Reply personally now. Answer the prospect's question and move the conversation toward a short call, demo, quote, or booking as appropriate.
"""
    return subject, body


def notify_hot_handoff(token, lead, reply_subject, reply_body, cls):
    subject, body = build_handoff_alert(lead, reply_subject, reply_body, cls)
    if DRY_RUN:
        print(f"DRY HOT HANDOFF | {HANDOFF_EMAIL or '(no handoff email)'} | {subject}\n{body}\n---")
        return {"dry_run": True}
    if not HANDOFF_EMAIL:
        raise RuntimeError("Missing SDR_HANDOFF_EMAIL or GMAIL_FROM_EMAIL for HOT lead alert")
    return send_email(token, HANDOFF_EMAIL, subject, body)


def load_settings():
    return (sb("GET", "sdr_settings", params={"select": "*", "id": "eq.true", "limit": "1"}) or [{}])[0]


def daily_remaining(settings):
    cap = int(settings.get("daily_send_cap") or 30)
    today = datetime.now(timezone.utc).date().isoformat()
    sent_today = sb("GET", "sdr_email_messages", params={
        "select": "id",
        "direction": "eq.outbound",
        "status": "eq.sent",
        "sent_at": f"gte.{today}T00:00:00+00:00",
    }) or []
    return max(0, cap - len(sent_today)), cap


def process_replies(token):
    data = gmail_api(token, "GET", "messages", params={"q": "is:unread in:inbox newer_than:2d -from:me", "maxResults": 50})
    processed = 0
    for item in data.get("messages", []):
        msg_id = item["id"]

        already_processed = sb("GET", "sdr_email_messages", params={
            "select": "id",
            "gmail_message_id": f"eq.{msg_id}",
            "direction": "eq.inbound",
            "limit": "1",
        }) or []
        if already_processed:
            gmail_api(token, "POST", f"messages/{msg_id}/modify", body={"removeLabelIds": ["UNREAD"]})
            continue

        msg = gmail_api(token, "GET", f"messages/{msg_id}", params={"format": "full"})
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])
        sender = header_value(headers, "From")
        sender_email = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", sender, re.I)
        if not sender_email:
            continue
        email = sender_email.group(0).lower()
        leads = sb("GET", "leads", params={
            "select": "id,business_name,niche_id,email,phone_number,whatsapp_number,city,country,website_url",
            "email": f"eq.{email}",
            "limit": "1",
        }) or []
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
            "to_email": GMAIL_FROM_EMAIL, "subject": subject,
            "body_text": body[:12000], "status": "processed", "ai_classification": classification,
            "ai_summary": str(cls.get("summary", ""))[:500], "buying_intent": cls.get("buying_intent"),
            "received_at": now(), "processed_at": now()
        }, prefer="return=minimal")
        sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body={
            "outreach_status": outreach_status,
            "followup_due_at": None,
            "last_checked_at": now(),
        })

        if classification == "hot":
            try:
                notify_hot_handoff(token, lead, subject, body, cls)
            except Exception as exc:
                print(f"HOT handoff alert failed for {email}: {exc}")

        gmail_api(token, "POST", f"messages/{msg_id}/modify", body={"removeLabelIds": ["UNREAD"]})
        processed += 1
    print(f"SDR replies processed: {processed}")


def process_followups(token):
    settings = load_settings()
    enabled = bool(settings.get("sending_enabled")) and not DRY_RUN
    remaining, _ = daily_remaining(settings)
    if remaining == 0:
        print("SDR follow-ups processed: 0 | daily send cap reached")
        return

    leads = sb("GET", "leads", params={
        "select": "id,business_name,email,followup_due_at,outreach_status",
        "email": "not.is.null",
        "outreach_status": "eq.contacted",
        "followup_due_at": f"lte.{now()}",
        "order": "followup_due_at.asc",
        "limit": str(min(remaining, 50)),
    }) or []

    prepared = 0
    for lead in leads:
        if remaining <= 0:
            break

        email = (lead.get("email") or "").strip().lower()
        if not email:
            continue

        blocked = sb("GET", "sdr_suppressions", params={"select": "id", "email": f"ilike.{email}", "limit": "1"}) or []
        if blocked:
            if not DRY_RUN:
                sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body={
                    "outreach_status": "suppressed",
                    "followup_due_at": None,
                    "last_checked_at": now(),
                })
            continue

        inbound = sb("GET", "sdr_email_messages", params={
            "select": "id",
            "lead_id": f"eq.{lead['id']}",
            "direction": "eq.inbound",
            "limit": "1",
        }) or []
        if inbound:
            if not DRY_RUN:
                sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body={
                    "followup_due_at": None,
                    "last_checked_at": now(),
                })
            continue

        history = sb("GET", "sdr_email_messages", params={
            "select": "id,subject,body_text,sent_at",
            "lead_id": f"eq.{lead['id']}",
            "direction": "eq.outbound",
            "status": "eq.sent",
            "order": "sent_at.desc",
            "limit": "10",
        }) or []

        if not history:
            if not DRY_RUN:
                sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body={
                    "followup_due_at": None,
                    "last_checked_at": now(),
                })
            continue

        if len(history) >= MAX_TOUCHES:
            if not DRY_RUN:
                sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body={
                    "outreach_status": "stopped",
                    "followup_due_at": None,
                    "last_checked_at": now(),
                })
            continue

        previous = history[0]
        followup_number = len(history)
        subject = (previous.get("subject") or "Quick follow-up").strip()
        body = write_followup(
            lead,
            subject,
            previous.get("body_text") or "",
            followup_number,
        )

        if not enabled:
            print(f"DRY FOLLOW-UP #{followup_number} | {email} | {subject}\n{body}\n---")
            prepared += 1
            continue

        result = send_email(token, email, subject, body)
        sent_time = now()
        sb("POST", "sdr_email_messages", body={
            "lead_id": lead["id"], "direction": "outbound",
            "gmail_message_id": result.get("id"), "gmail_thread_id": result.get("threadId"),
            "from_email": GMAIL_FROM_EMAIL, "to_email": email,
            "subject": subject, "body_text": body, "status": "sent", "sent_at": sent_time,
        }, prefer="return=minimal")

        total_touches = len(history) + 1
        if total_touches >= MAX_TOUCHES:
            next_status = "stopped"
            next_due = None
        else:
            next_status = "contacted"
            next_due = after_days(FOLLOWUP_DELAYS_DAYS[total_touches - 1])

        sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body={
            "outreach_status": next_status,
            "last_contacted_at": sent_time,
            "last_checked_at": sent_time,
            "followup_due_at": next_due,
        })
        prepared += 1
        remaining -= 1

    print(f"SDR follow-ups processed: {prepared} | enabled={enabled} | remaining={remaining}")


def send_batch(token):
    settings = load_settings()
    enabled = bool(settings.get("sending_enabled")) and not DRY_RUN
    remaining, _ = daily_remaining(settings)
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
            sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body={
                "outreach_status": "stopped",
                "notes": ((lead.get("notes") or "") + " | AI skipped outreach")[:1000],
            })
            continue

        subject, body = draft
        if not enabled:
            print(f"DRY RUN | {email} | {subject}\n{body}\n---")
            prepared += 1
            continue

        result = send_email(token, email, subject, body)
        sent_time = now()
        sb("POST", "sdr_email_messages", body={
            "lead_id": lead["id"], "direction": "outbound", "gmail_message_id": result.get("id"),
            "gmail_thread_id": result.get("threadId"), "from_email": GMAIL_FROM_EMAIL,
            "to_email": email, "subject": subject, "body_text": body, "status": "sent", "sent_at": sent_time,
        }, prefer="return=minimal")
        sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body={
            "outreach_status": "contacted",
            "first_contact_at": sent_time,
            "last_contacted_at": sent_time,
            "last_checked_at": sent_time,
            "followup_due_at": after_days(FOLLOWUP_DELAYS_DAYS[0]),
        })
        prepared += 1

    print(f"SDR outbound processed: {prepared} | enabled={enabled} | remaining={remaining}")


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_KEY")
    token = gmail_token()
    process_replies(token)
    process_followups(token)
    send_batch(token)


if __name__ == "__main__":
    main()
