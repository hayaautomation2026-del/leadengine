"""Niche-aware worker for LeadEngine.

Reuses the existing free-first Maps engines from scraper.py, but writes into
public.leads and associates every lead with the selected niche.
"""
from __future__ import annotations

import hashlib
import html
import os
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse

import requests

from scraper import (
    FINDER_SOURCE,
    HARD_CAP_LEADS,
    calculate_pain_score,
    check_website,
    find_businesses,
)

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SEARCH_REQUEST = (os.environ.get("SEARCH_REQUEST") or "").strip()

CONTACT_PATH_HINTS = (
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/team",
    "/staff",
)

CONTACT_LINK_WORDS = (
    "contact",
    "about",
    "team",
    "staff",
    "support",
    "reach-us",
    "get-in-touch",
)

BLOCKED_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "sentry.io",
    "wixpress.com",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def headers(prefer=None):
    h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    return h


def sb(method, path, *, params=None, body=None, prefer=None):
    r = requests.request(method, f"{SUPABASE_URL}/rest/v1/{path}", headers=headers(prefer), params=params, json=body, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Supabase {method} {path} failed {r.status_code}: {r.text[:500]}")
    return r.json() if r.text else None


def clean_phone(value):
    if not value:
        return None
    value = re.sub(r"[^\d+]", "", str(value))
    return value[:30] if len(value) >= 7 else None


def normalize_website(website):
    if not website:
        return None
    value = str(website).strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value


def decode_cfemail(encoded):
    """Decode Cloudflare data-cfemail values without guessing addresses."""
    try:
        raw = bytes.fromhex(encoded)
        if len(raw) < 2:
            return None
        key = raw[0]
        decoded = "".join(chr(value ^ key) for value in raw[1:])
        return decoded if "@" in decoded else None
    except (ValueError, TypeError):
        return None


def extract_emails(text):
    if not text:
        return []
    text = html.unescape(text)
    found = []
    seen = set()

    candidates = list(re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I))
    for encoded in re.findall(r"data-cfemail=[\"']([0-9a-fA-F]+)[\"']", text, re.I):
        decoded = decode_cfemail(encoded)
        if decoded:
            candidates.append(decoded)

    for raw in candidates:
        email = raw.lower().strip(".,;:()[]{}<>\"")
        if "@" not in email:
            continue
        domain = email.split("@")[-1]
        if domain in BLOCKED_EMAIL_DOMAINS or email in seen:
            continue
        seen.add(email)
        found.append(email)
    return found


def extract_whatsapp_number(text):
    """Return a WhatsApp number only when the website explicitly exposes one."""
    if not text:
        return None
    decoded = html.unescape(text)
    patterns = (
        r"https?://wa\.me/([+\d][\d\s().-]{6,25})",
        r"https?://api\.whatsapp\.com/send\?[^\"'<>\s]*phone=([+\d][\d\s().-]{6,25})",
        r"https?://(?:www\.)?whatsapp\.com/send\?[^\"'<>\s]*phone=([+\d][\d\s().-]{6,25})",
    )
    for pattern in patterns:
        match = re.search(pattern, decoded, re.I)
        if match:
            return clean_phone(match.group(1))
    return None


def discover_contact_details(website):
    """Best-effort public contact discovery from a business website.

    Checks the homepage plus a small set of likely contact/about/team pages.
    It never guesses email addresses or assumes a normal phone is WhatsApp.
    """
    base_url = normalize_website(website)
    if not base_url:
        return None, None

    parsed_base = urlparse(base_url)
    base_host = parsed_base.netloc.lower().removeprefix("www.")
    queue = [base_url]
    queued = {base_url}

    for path in CONTACT_PATH_HINTS:
        candidate = urljoin(base_url, path)
        if candidate not in queued:
            queue.append(candidate)
            queued.add(candidate)

    emails = []
    seen_emails = set()
    whatsapp_number = None

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; LeadEngine/1.0; +https://github.com/hayaautomation2026-del/leadengine)"
    })

    visited = set()
    while queue and len(visited) < 8:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        try:
            r = session.get(url, timeout=12, allow_redirects=True)
        except requests.RequestException:
            continue

        if not r.ok:
            continue

        content_type = (r.headers.get("content-type") or "").lower()
        if "text/html" not in content_type and content_type:
            continue

        text = r.text[:1_000_000]

        for email in extract_emails(text):
            if email not in seen_emails:
                seen_emails.add(email)
                emails.append(email)

        if not whatsapp_number:
            whatsapp_number = extract_whatsapp_number(text)

        for href in re.findall(r"href\s*=\s*[\"']([^\"']+)[\"']", text, re.I):
            href = html.unescape(href).strip()
            if not href or href.startswith(("javascript:", "tel:", "mailto:", "#")):
                continue
            absolute = urljoin(r.url, href)
            parsed = urlparse(absolute)
            host = parsed.netloc.lower().removeprefix("www.")
            path_lower = parsed.path.lower()
            if host != base_host:
                continue
            if not any(word in path_lower for word in CONTACT_LINK_WORDS):
                continue
            clean = absolute.split("#", 1)[0]
            if clean not in queued and clean not in visited:
                queue.append(clean)
                queued.add(clean)

    preferred = None
    if emails:
        generic_prefixes = ("info@", "contact@", "hello@", "sales@", "office@", "admin@")
        preferred = next((e for e in emails if e.startswith(generic_prefixes)), emails[0])

    return preferred, whatsapp_number


def fingerprint(name, phone):
    return hashlib.md5(f"{name.lower().strip()}|{phone}".encode()).hexdigest()[:16]


def run():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY are required")

    if SEARCH_REQUEST:
        rows = sb("GET", "search_requests", params={"select": "id,query,location,max_leads,niche_id", "id": f"eq.{SEARCH_REQUEST}", "limit": "1"}) or []
    else:
        rows = sb("GET", "search_requests", params={"select": "id,query,location,max_leads,niche_id", "status": "eq.pending", "order": "created_at.asc", "limit": "1"}) or []
    if not rows:
        print("No pending niche search.")
        return

    req = rows[0]
    if not req.get("niche_id"):
        raise RuntimeError("Search request has no niche_id")

    sb("PATCH", "search_requests", params={"id": f"eq.{req['id']}"}, body={"status": "running", "started_at": now()})
    run_rows = sb("POST", "scraper_runs", body={"status": "running", "started_at": now(), "search_request_id": req["id"], "provider": "free_multi_scraper"}, prefer="return=representation") or []
    run_id = run_rows[0]["run_id"] if run_rows else None

    query = str(req.get("query") or "").strip()
    location = str(req.get("location") or "").strip()
    search_text = f"{query} in {location}" if location else query
    limit = min(int(req.get("max_leads") or HARD_CAP_LEADS), HARD_CAP_LEADS)

    inserted = skipped = duplicates = 0
    errors = []
    engine = None
    try:
        raw, meta = find_businesses(search_text, limit)
        engine = meta.get("engine_used")
        for item in raw:
            name = str(item.get("name") or "").strip()[:150]
            phone = clean_phone(item.get("phone"))
            if not name or not phone:
                skipped += 1
                continue
            existing = sb("GET", "leads", params={"select": "id,email,whatsapp_number,website_url", "phone_number": f"eq.{phone}", "limit": "1"}) or []
            if existing:
                duplicates += 1
                row = existing[0]
                website = str(row.get("website_url") or item.get("site") or "").strip() or None
                if website and (not row.get("email") or not row.get("whatsapp_number")):
                    email, whatsapp_number = discover_contact_details(website)
                    patch = {"last_checked_at": now()}
                    if email and not row.get("email"):
                        patch["email"] = email
                        patch["email_source"] = "website"
                    if whatsapp_number and not row.get("whatsapp_number"):
                        patch["whatsapp_number"] = whatsapp_number
                    sb("PATCH", "leads", params={"id": f"eq.{row['id']}"}, body=patch)
                continue

            website = str(item.get("site") or "").strip() or None
            signals = check_website(website)
            email, whatsapp_number = discover_contact_details(website)
            lead = {
                "niche_id": req["niche_id"],
                "full_name": name,
                "business_name": name,
                "phone_number": phone,
                "email": email,
                "email_source": "website" if email else None,
                "whatsapp_number": whatsapp_number,
                "website_url": website,
                "google_maps_url": item.get("url"),
                "address": str(item.get("address") or "")[:500],
                "source": engine,
                "source_url": item.get("url"),
                "contact_status": "new",
                "outreach_status": "pending",
                "last_checked_at": now(),
                "followup_due_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
                "notes": f"Search: {search_text}"[:1000],
                "lead_fingerprint": fingerprint(name, phone),
                "scoring_version": "v3",
                **signals,
            }
            lead["pain_score"], lead["pain_reason"] = calculate_pain_score(lead)
            sb("POST", "leads", body=lead, prefer="return=minimal")
            inserted += 1
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")

    final_status = "success" if (inserted or duplicates) and not errors else ("partial" if inserted else "fail")
    if run_id:
        sb("PATCH", "scraper_runs", params={"run_id": f"eq.{run_id}"}, body={"finished_at": now(), "leads_fetched": inserted + skipped + duplicates, "leads_inserted": inserted, "leads_skipped": skipped, "duplicates_skipped": duplicates, "error_log": "\n".join(errors) if errors else None, "status": final_status, "engine_used": engine})

    sb("PATCH", "search_requests", params={"id": f"eq.{req['id']}"}, body={"status": "failed" if errors and not inserted else "completed", "finished_at": now(), "result_count": inserted, "source_used": engine, "error_message": "\n".join(errors) if errors else None})
    print(f"NICHE WORKER | niche={req['niche_id']} inserted={inserted} duplicates={duplicates} skipped={skipped} engine={engine}")


if __name__ == "__main__":
    run()
