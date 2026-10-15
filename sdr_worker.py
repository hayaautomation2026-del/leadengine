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
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

import requests

from conversation_engine import advance, assess, new_conversation, set_owner

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

PLAYBOOK_PATH = Path(__file__).with_name("playbook.md")


def load_playbook():
    """Read owner-approved Markdown on every generation; absent file fails closed."""
    try:
        return PLAYBOOK_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def playbook_offer():
    """Only the exact Approved Buyer Q&A keys are eligible for an AI reply."""
    text = load_playbook()
    section = text.split("## Approved Buyer Q&A", 1)
    catalog = {}
    if len(section) == 2:
        for line in section[1].split("\n## ", 1)[0].splitlines():
            match = re.fullmatch(r"\s*-\s*([a-z][a-z0-9_]*)\s*:\s*(.+?)\s*", line)
            if match:
                catalog[match.group(1)] = match.group(2)
    return {
        "approved_answers": catalog,
        "price_text": catalog.get("pricing", ""),
        "details_text": catalog.get("how_it_works", ""),
        "identity_text": catalog.get("identity", ""),
    }


def lead_pain_signals(lead):
    """Use measured lead fields, not invented website observations."""
    signals = []
    mapping = (
        ("has_website_chatbot", "Website chatbot not detected"),
        ("has_whatsapp_button", "WhatsApp button not detected"),
        ("has_inquiry_form", "Inquiry form not detected"),
        ("has_afterhours_contact", "After-hours contact option not detected"),
        ("unanswered_reviews", "Unanswered reviews observed"),
    )
    reason = str(lead.get("pain_reason") or "").strip()
    if reason:
        signals.append("Recorded pain reason: " + reason[:300])
    for field, observation in mapping:
        val = lead.get(field)
        if (val is False and field != "unanswered_reviews") or (
            val is True and field == "unanswered_reviews"
        ):
            signals.append(observation)
    return signals


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
    signals = lead_pain_signals(lead)
    if not signals:
        return None
    playbook = load_playbook()
    prompt = f"""You are the SDR for a small automation business. Create ONE concise cold email to a business owner.
Do not invent facts. Use only the supplied lead facts. The goal is to start a conversation, not hard-sell.
Return JSON only: {{\"decision\":\"send|skip\",\"subject\":\"...\",\"body\":\"...\"}}.
Skip if the business lacks enough evidence for a relevant message.

Owner sales playbook (follow its rules; never invent missing terms):
{playbook}

Verified lead signals:
{chr(10).join(signals)}

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
Unanswered reviews: {lead.get('unanswered_reviews')}
Notes: {lead.get('notes')}

Rules: Fewer than 90 words. Refer specifically to one recorded pain signal only. Plain text. No fake compliment or fabricated observation. No links or attachments. One clear low-friction question. Never use cold-email clichés."""
    result = extract_json(openai_text(prompt))
    if result.get("decision") != "send":
        return None
    subject, body = str(result.get("subject", "")).strip(), str(result.get("body", "")).strip()
    validate_outbound_email(lead.get("email"), subject, body, first_touch=True)
    return subject, body


def write_followup(lead, previous_subject, previous_body, followup_number):
    playbook = load_playbook()
    prompt = f"""You are the SDR for a small automation business.
Write follow-up #{followup_number} to a prospect who has not replied yet.
Owner playbook (follow it; use only approved terms):
{playbook}

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


def preview_conversation_reply(state, message_id, body, approved_offer, *, paused=False):
    """Phase 1 adapter: returns draft/state only; never sends or writes to Supabase.

Caller supplies saved state and owner-approved offer terms. Deliberately not
called by main(): Phase 2 requires persistent state, threaded sends and gates.
"""
    # Do not call the model for paused, duplicate or human-owned conversations.
    skip_reader = (paused or message_id in state["processed"]
                   or state["owner"] == "human" or state["status"] == "stopped")
    assessment = {} if skip_reader else assess(body, state["history"], openai_text)
    return advance(state, message_id, body, assessment, approved_offer, paused=paused)



INVALID_OUTBOUND_TOKENS = ("[Name]", "{{", "}}", "None", "undefined", "\u0060\u0060\u0060json")
OUTBOUND_CLICHES = (
    "i hope this finds you well",
    "i wanted to reach out",
    "circling back",
    "touching base",
)
AUTOMATED_LOCAL_PARTS = {"noreply", "no-reply", "donotreply", "mailer-daemon"}


def validate_outbound_email(to_email, subject, body, *, first_touch=False):
    """Validate every outbound message before Gmail is called."""
    email = to_email.strip() if isinstance(to_email, str) else ""
    if not re.fullmatch(r"[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}", email):
        raise ValueError("missing or invalid recipient email")
    if email.split("@", 1)[0].lower() in AUTOMATED_LOCAL_PARTS:
        raise ValueError("automated/no-reply recipient is blocked")
    if not isinstance(subject, str) or len(subject.strip()) < 4:
        raise ValueError("subject must have at least 4 characters")
    if not isinstance(body, str) or len(body.strip()) < 25:
        raise ValueError("body must have at least 25 characters")
    for token in INVALID_OUTBOUND_TOKENS:
        if token in subject or token in body:
            raise ValueError(f"unresolved placeholder {token!r}")
    normalized = " ".join((subject + " " + body).lower().split())
    for phrase in OUTBOUND_CLICHES:
        if phrase in normalized:
            raise ValueError("generic cold-email cliché is blocked: " + phrase)
    if first_touch and len(body.split()) >= 90:
        raise ValueError("initial outreach must contain fewer than 90 words")
    return email, subject.strip(), body.strip()


def reserve_outbound(lead, email, subject, body, *, require_sdr=False):
    """Atomically claim the lead, then durably queue the intended send."""
    lead_id = lead["id"]
    original_status = lead["outreach_status"]
    filters = {"id": f"eq.{lead_id}", "outreach_status": f"eq.{original_status}", "select": "id"}
    if require_sdr:
        filters["human_owned"] = "eq.false"
    claimed = sb("PATCH", "leads", params=filters,
                 body={"outreach_status": "sending"}, prefer="return=representation")
    if not claimed:
        raise RuntimeError(f"Pre-send claim failed for lead {lead_id}; Gmail not called")

    # Existing sdr_email_messages CHECK permits 'queued', not 'pending'.
    queued = sb("POST", "sdr_email_messages", body={
        "lead_id": lead_id, "direction": "outbound",
        "from_email": GMAIL_FROM_EMAIL, "to_email": email,
        "subject": subject, "body_text": body, "status": "queued",
    }, prefer="return=representation")
    if not queued or not queued[0].get("id"):
        raise RuntimeError(f"Pre-send outbox record failed for lead {lead_id}; Gmail not called")
    return queued[0]["id"]


def finalize_outbound(lead_id, outbox_id, gmail_result, lead_fields):
    """Retry post-send persistence; retain the queued record and lock on failure."""
    gmail_id = gmail_result.get("id") if isinstance(gmail_result, dict) else None
    if not gmail_id:
        raise RuntimeError(f"Gmail returned no message ID for queued email {outbox_id}; review required")

    sent_time = lead_fields["last_contacted_at"]
    for attempt in range(4):  # initial attempt, then three retries
        try:
            recorded = sb("PATCH", "sdr_email_messages", params={
                "id": f"eq.{outbox_id}", "select": "id",
            }, body={
                "gmail_message_id": gmail_id,
                "gmail_thread_id": gmail_result.get("threadId"),
                "status": "sent", "sent_at": sent_time,
            }, prefer="return=representation")
            if not recorded:
                raise RuntimeError(f"Outbox row {outbox_id} not found")

            updated = sb("PATCH", "leads", params={
                "id": f"eq.{lead_id}", "outreach_status": "eq.sending", "select": "id",
            }, body=lead_fields, prefer="return=representation")
            if not updated:
                raise RuntimeError(f"Lead {lead_id} no longer sending; review its status")
            return
        except Exception as exc:
            print(f"POST-SEND PERSISTENCE FAILED: lead={lead_id} outbox={outbox_id} "
                  f"gmail_message_id={gmail_id} attempt={attempt + 1}/4: {exc}")
            if attempt == 3:
                raise RuntimeError(
                    f"Sent Gmail message {gmail_id} requires reconciliation; "
                    f"outbox {outbox_id} remains recorded and lead is not eligible for resend"
                ) from exc
            time.sleep(2 ** attempt)


def send_email(token, to_email, subject, body, *, thread_id=None, in_reply_to=None, references=None):
    try:
        to_email, subject, body = validate_outbound_email(to_email, subject, body)
    except ValueError as exc:
        print(f"OUTBOUND VALIDATION FAILED for {to_email!r}: {exc}")
        raise
    msg = MIMEText(body, "plain", "utf-8")
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["From"] = formataddr((FROM_NAME, GMAIL_FROM_EMAIL))
    if thread_id is not None:
        if (not re.fullmatch(r"[A-Za-z0-9_-]+", thread_id)
                or not isinstance(in_reply_to, str)
                or not re.fullmatch(r"<[^<>\s]+>", in_reply_to)):
            raise ValueError("reply is missing safe Gmail threading metadata")
        if references and ("\r" in references or "\n" in references):
            raise ValueError("unsafe reply References header")
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = ((references or "") + " " + in_reply_to).strip()
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
    payload = {"raw": raw}
    if thread_id is not None:
        payload["threadId"] = thread_id
    return gmail_api(token, "POST", "messages/send", body=payload)


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


BOUNCE_SUBJECT_MARKERS = (
    "delivery status notification", "undeliverable",
    "mail delivery subsystem", "failure notice",
)
EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
LEAD_REPLY_FIELDS = "*"  # tolerate legacy leads without new conversation fields


def is_delivery_failure(sender_email, subject, headers):
    """Recognize DSNs without trusting an LLM to classify a mailer daemon."""
    local_part = (sender_email or "").split("@", 1)[0].lower()
    if local_part.startswith(("mailer-daemon", "postmaster")):
        return True
    if any(marker in (subject or "").lower() for marker in BOUNCE_SUBJECT_MARKERS):
        return True
    if any(header_value(headers, name) for name in
           ("Final-Recipient", "Original-Recipient", "X-Failed-Recipients")):
        return True
    return "delivery-status" in header_value(headers, "Content-Type").lower()


def bounce_recipient_emails(payload, body):
    """Prefer failed-recipient metadata; support multipart delivery-status and prose."""
    candidates = []

    def collect(text):
        for found in EMAIL_PATTERN.findall(text or ""):
            addr = found.lower()
            if addr not in candidates:
                candidates.append(addr)

    def visit(part):
        headers = part.get("headers") or []
        for name in ("Final-Recipient", "Original-Recipient", "X-Failed-Recipients"):
            collect(header_value(headers, name))
        mime = (part.get("mimeType") or "").lower()
        if mime in ("message/delivery-status", "text/rfc822-headers"):
            collect(decode_b64url((part.get("body") or {}).get("data")))
        for child in part.get("parts") or []:
            visit(child)

    visit(payload)
    # Failure lines are more trustworthy than arbitrary addresses in a quoted email.
    for line in (body or "").splitlines():
        if re.search(r"recipient|undeliver|delivery|fail|address|unknown user|not found|to:", line, re.I):
            collect(line)
    # Some providers supply only a short human-readable bounce with no labeled line.
    collect(body)
    return candidates


def find_bounced_lead(payload, body, thread_id):
    """Resolve a failed recipient by headers/body first, outbound Gmail thread second."""
    for candidate in bounce_recipient_emails(payload, body):
        if candidate == (GMAIL_FROM_EMAIL or "").lower():
            continue
        matches = sb("GET", "leads", params={
            "select": LEAD_REPLY_FIELDS,
            "email": f"eq.{candidate}",
            "limit": "1",
        }) or []
        if matches:
            return matches[0]

    if thread_id:
        outbound = sb("GET", "sdr_email_messages", params={
            "select": "lead_id,to_email",
            "gmail_thread_id": f"eq.{thread_id}",
            "direction": "eq.outbound",
            "status": "eq.sent",
            "limit": "1",
        }) or []
        if outbound and outbound[0].get("lead_id"):
            leads = sb("GET", "leads", params={
                "select": LEAD_REPLY_FIELDS,
                "id": f"eq.{outbound[0]['lead_id']}",
                "limit": "1",
            }) or []
            if leads:
                return leads[0]
    return None



def normalized_conversation(lead):
    """Legacy/broken JSON state must never cause an auto-send or KeyError."""
    saved = lead.get("conversation_state")
    supported = "conversation_state" in lead and "human_owned" in lead
    state = new_conversation()
    if isinstance(saved, dict):
        for key, default in new_conversation().items():
            value = saved.get(key)
            if isinstance(default, list) and isinstance(value, list):
                state[key] = value
            elif isinstance(default, dict) and isinstance(value, dict):
                state[key] = value
            elif isinstance(default, int) and isinstance(value, int) and value >= 0:
                state[key] = value
            elif isinstance(default, str) and isinstance(value, str):
                state[key] = value
            elif key == "defer_until" and (value is None or isinstance(value, str)):
                state[key] = value
    if lead.get("human_owned"):
        state = set_owner(state, "human")
    if state["owner"] not in ("sdr", "human") or state["status"] not in (
        "active", "deferred", "handoff", "stopped"
    ):
        state = set_owner(new_conversation(), "human")
    return state, supported


def handle_prospect_reply(token, lead, sender_email, subject, body, msg_id, thread_id, headers):
    """One inbound prospect turn: decision, durable record, then locked send."""
    cls = classify_reply(lead, body)
    classification = cls.get("classification", "unknown")
    if classification not in ("hot", "warm", "cold", "unsubscribe", "bounce"):
        classification = "unknown"
    state, supported = normalized_conversation(lead)
    offer = playbook_offer()

    # Historical unsubscribe/bounce suppression is deliberately preserved.
    if classification in ("unsubscribe", "bounce"):
        action, next_state = "stop", state
        next_state["status"] = "stopped"
    else:
        skip_reader = state["owner"] == "human" or state["status"] == "stopped"
        assessment = {} if skip_reader else assess(body, state["history"], openai_text, offer)
        next_state, decision = advance(state, msg_id, body, assessment, offer)
        action = decision["action"]

    # Missing columns or damaged historical states fail closed. No auto-reply.
    if not supported and action == "draft":
        action = "handoff"
    if lead.get("outreach_status") in ("suppressed", "stopped", "sending"):
        action = "stop" if lead.get("outreach_status") == "suppressed" else "handoff"

    if action == "stop":
        reason = classification if classification in ("unsubscribe", "bounce") else "unsubscribe"
        sb("POST", "sdr_suppressions", body={"email": sender_email, "reason": reason},
           prefer="resolution=ignore-duplicates")
        next_state["status"] = "stopped"
    if action == "handoff":
        next_state = set_owner(next_state, "human")
    if action == "wait" and next_state.get("status") != "deferred":
        # An acknowledgement is a wait, not permission to send later.
        next_state["defer_until"] = None

    update = {"followup_due_at": None, "last_checked_at": now()}
    if action == "stop":
        update["outreach_status"] = "suppressed"
    elif action == "handoff":
        update["outreach_status"] = "hot" if classification == "hot" else "handoff"
    elif action == "wait":
        update["outreach_status"] = "deferred"
    if supported and action != "draft":
        update["conversation_state"] = next_state
        update["human_owned"] = action == "handoff" or bool(lead.get("human_owned"))
    sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body=update)

    # Durable inbound Gmail ID prevents duplicate classification/send on next poll.
    sb("POST", "sdr_email_messages", body={
        "lead_id": lead["id"], "direction": "inbound",
        "gmail_message_id": msg_id, "gmail_thread_id": thread_id,
        "from_email": sender_email, "to_email": GMAIL_FROM_EMAIL,
        "subject": subject, "body_text": body[:12000],
        "status": "processed", "ai_classification": classification,
        "ai_summary": str(cls.get("summary", ""))[:500],
        "buying_intent": cls.get("buying_intent"),
        "received_at": now(), "processed_at": now(),
    }, prefer="return=minimal")

    if action == "handoff" or (classification == "hot" and action != "stop"):
        try:
            notify_hot_handoff(token, lead, subject, body, cls)
        except Exception as exc:
            print(f"PROSPECT HANDOFF ALERT FAILED for {sender_email}: {exc}")

    if action != "draft":
        return action
    reply_body = decision.get("body") or ""
    reply_subject = subject if re.match(r"(?i)^re:", subject or "") else "Re: " + (subject or "Your enquiry")
    if DRY_RUN:
        print(f"DRY PROSPECT REPLY | {sender_email} | {reply_subject}\n{reply_body}\n---")
        return "draft"

    settings = load_settings()
    if not settings.get("sending_enabled") or settings.get("kill_switch"):
        print(f"PROSPECT REPLY PAUSED BY OWNER for {sender_email}")
        return "wait"
    if not supported or lead.get("outreach_status") != "contacted" or lead.get("human_owned"):
        print(f"PROSPECT REPLY requires manual review: {sender_email}")
        return "handoff"
    remaining, _ = daily_remaining(settings)
    if remaining < 1:
        print(f"PROSPECT REPLY daily cap reached: {sender_email}")
        return "wait"
    parent_message_id = header_value(headers, "Message-ID")
    reference_chain = header_value(headers, "References")
    try:
        validate_outbound_email(sender_email, reply_subject, reply_body)
        if not thread_id or not re.fullmatch(r"<[^<>\s]+>", parent_message_id):
            raise ValueError("missing Gmail threading information")
    except ValueError as exc:
        print(f"PROSPECT REPLY blocked pending human review: {exc}")
        return "handoff"

    # Owner and lock checked again immediately before claiming the send.
    current = sb("GET", "leads", params={
        "select": "id,human_owned,outreach_status", "id": f"eq.{lead['id']}", "limit": "1"
    }) or []
    if not current or current[0].get("human_owned") or current[0].get("outreach_status") != "contacted":
        print(f"PROSPECT REPLY not sent: takeover or lead status changed for {sender_email}")
        return "handoff"
    outbox_id = reserve_outbound(lead, sender_email, reply_subject, reply_body, require_sdr=True)
    try:
        sent = send_email(token, sender_email, reply_subject, reply_body,
                          thread_id=thread_id, in_reply_to=parent_message_id,
                          references=reference_chain)
    except Exception:
        print(f"PROSPECT REPLY send uncertain: outbox={outbox_id}, lead={lead['id']}; manual reconciliation required")
        raise
    sent_at = now()
    finalize_outbound(lead["id"], outbox_id, sent, {
        "outreach_status": "contacted", "followup_due_at": None,
        "last_contacted_at": sent_at, "last_checked_at": sent_at,
        "conversation_state": next_state, "human_owned": False,
    })
    return "sent"


def process_replies(token):
    processed = 0
    next_page = None
    while True:
        params = {
            "q": "newer_than:7d -from:me in:inbox",
            "maxResults": 50,
        }
        if next_page:
            params["pageToken"] = next_page
        data = gmail_api(token, "GET", "messages", params=params) or {}
        for item in data.get("messages") or []:
            msg_id = item["id"]
            already_processed = sb("GET", "sdr_email_messages", params={
                "select": "id",
                "gmail_message_id": f"eq.{msg_id}",
                "direction": "eq.inbound",
                "limit": "1",
            }) or []
            if already_processed:
                continue

            msg = gmail_api(token, "GET", f"messages/{msg_id}", params={"format": "full"})
            payload = msg.get("payload", {})
            headers = payload.get("headers", [])
            sender = header_value(headers, "From")
            sender_match = EMAIL_PATTERN.search(sender)
            sender_email = sender_match.group(0).lower() if sender_match else ""
            body = extract_text(payload).strip()
            subject = header_value(headers, "Subject")
            thread_id = msg.get("threadId")

            if is_delivery_failure(sender_email, subject, headers):
                lead = find_bounced_lead(payload, body, thread_id)
                if not lead or not lead.get("email"):
                    print(f"Unmatched delivery failure {msg_id}; left unread for review")
                    continue
                failed_email = lead["email"].strip().lower()
                # Apply stop controls BEFORE recording the message. On partial
                # Supabase failure, the next run can retry the entire bounce.
                sb("POST", "sdr_suppressions", body={
                    "email": failed_email, "reason": "bounce",
                }, prefer="resolution=ignore-duplicates")
                sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body={
                    "outreach_status": "suppressed",
                    "followup_due_at": None,
                    "last_checked_at": now(),
                })
                sb("POST", "sdr_email_messages", body={
                    "lead_id": lead["id"], "direction": "inbound",
                    "gmail_message_id": msg_id, "gmail_thread_id": thread_id,
                    "from_email": sender_email, "to_email": GMAIL_FROM_EMAIL,
                    "subject": subject, "body_text": body[:12000],
                    "status": "processed", "ai_classification": "bounce",
                    "ai_summary": f"Delivery failure for {failed_email}",
                    "received_at": now(), "processed_at": now(),
                }, prefer="return=minimal")
                gmail_api(token, "POST", f"messages/{msg_id}/modify",
                          body={"removeLabelIds": ["UNREAD"]})
                processed += 1
                continue

            if not sender_email:
                continue
            leads = sb("GET", "leads", params={
                "select": LEAD_REPLY_FIELDS,
                "email": f"eq.{sender_email}",
                "limit": "1",
            }) or []
            if not leads:
                continue
            lead = leads[0]
            handle_prospect_reply(token, lead, sender_email, subject, body, msg_id, thread_id, headers)

            # Gmail is marked read only after the inbound row was saved.
            gmail_api(token, "POST", f"messages/{msg_id}/modify",
                      body={"removeLabelIds": ["UNREAD"]})
            processed += 1
        next_page = data.get("nextPageToken")
        if not next_page:
            break
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

        try:
            email, subject, body = validate_outbound_email(email, subject, body)
        except ValueError as exc:
            print(f"FOLLOW-UP VALIDATION FAILED for lead {lead['id']}: {exc}; review required")
            continue

        # The claimed lead is excluded from the next due-followup query.
        outbox_id = reserve_outbound(lead, email, subject, body)
        try:
            result = send_email(token, email, subject, body)
        except Exception as exc:
            print(f"GMAIL SEND FAILED/UNCERTAIN: lead={lead['id']} outbox={outbox_id}: {exc}; review required")
            raise

        sent_time = now()
        total_touches = len(history) + 1
        if total_touches >= MAX_TOUCHES:
            next_status = "stopped"
            next_due = None
        else:
            next_status = "contacted"
            next_due = after_days(FOLLOWUP_DELAYS_DAYS[total_touches - 1])

        finalize_outbound(lead["id"], outbox_id, result, {
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
        "select": "id,business_name,full_name,email,website_url,city,country,pain_score,pain_reason,has_whatsapp_button,has_website_chatbot,has_inquiry_form,has_afterhours_contact,unanswered_reviews,notes,niche_id,niches(name)",
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

        try:
            email, subject, body = validate_outbound_email(email, subject, body, first_touch=True)
        except ValueError as exc:
            print(f"OUTREACH VALIDATION FAILED for lead {lead['id']}: {exc}; review required")
            continue

        outbox_id = reserve_outbound(lead, email, subject, body)
        try:
            result = send_email(token, email, subject, body)
        except Exception as exc:
            print(f"GMAIL SEND FAILED/UNCERTAIN: lead={lead['id']} outbox={outbox_id}: {exc}; review required")
            raise

        sent_time = now()
        finalize_outbound(lead["id"], outbox_id, result, {
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
    test_check_id = os.environ.get("INBOX_TEST_CHECK_ID")
    if test_check_id:
        from inbox_conversation import run_test_conversation
        run_test_conversation(test_check_id, token=token)
    process_replies(token)
    process_followups(token)
    send_batch(token)


if __name__ == "__main__":
    main()
