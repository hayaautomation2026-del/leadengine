"""
Real Estate Broker Lead Scraper
================================
Target: UAE + USA real estate brokers
Source: Outscraper Google Maps API
Destination: Supabase broker_leads table
Pain Signal: Detects brokers with no automation/WhatsApp visible
Schedule: Weekly via GitHub Actions

Built for: Phone-only management, zero budget, Karachi timezone
"""

import os
import re
import time
import random
import requests
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client

# ============================================================
# RETRY CONFIG
# ============================================================
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # Seconds — doubles each retry (2, 4, 8)

# ============================================================
# CONFIG — Set these as GitHub Secrets, never hardcode
# ============================================================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
OUTSCRAPER_API_KEY = os.environ.get("OUTSCRAPER_API_KEY")

# Validate all secrets present
def validate_env():
    missing = []
    if not SUPABASE_URL: missing.append("SUPABASE_URL")
    if not SUPABASE_KEY: missing.append("SUPABASE_KEY")
    if not OUTSCRAPER_API_KEY: missing.append("OUTSCRAPER_API_KEY")
    if missing:
        raise EnvironmentError(f"Missing required secrets: {', '.join(missing)}")

# ============================================================
# TARGETS
# Weekly rotation — update in GitHub web editor from phone
# Swap cities each week to maximize coverage
# ============================================================
TARGETS = [
    {"query": "real estate broker", "location": "Dubai, UAE", "country": "UAE"},
    {"query": "real estate agency",  "location": "Abu Dhabi, UAE", "country": "UAE"},
    {"query": "real estate broker",  "location": "Miami, FL, USA", "country": "USA"},
    {"query": "real estate broker",  "location": "Houston, TX, USA", "country": "USA"},
]

MAX_RESULTS_PER_QUERY = 20   # Keep weekly total under free tier limits
REQUEST_DELAY_MIN = 3        # Seconds — polite scraping
REQUEST_DELAY_MAX = 7
HARD_CAP_LEADS = 20          # First live run cap — do not change until data observed

# ============================================================
# PHONE CLEANING
# ============================================================
def clean_phone(raw):
    if not raw:
        return None
    cleaned = re.sub(r'[^\d+]', '', str(raw))
    if len(cleaned) < 7:
        return None
    return cleaned[:20]

def is_whatsapp_likely(phone):
    """UAE mobile numbers are almost always on WhatsApp"""
    if not phone:
        return False
    return "+971" in phone or phone.startswith("971") or (
        # UAE mobile prefix
        any(phone.startswith(p) for p in ["050", "055", "056", "058", "052", "054"])
    )

# ============================================================
# PAIN SIGNAL DETECTION
# Objective signals only — detectable from public data
# ============================================================
def validate_lead(lead: dict) -> bool:
    """
    Validation gate — blocks garbage from entering DB.
    Returns True only if lead meets minimum quality bar.
    """
    phone = lead.get("phone_number")
    name = lead.get("full_name")

    if not phone or len(phone) < 8:
        print(f"  VALIDATION FAILED: Phone too short or missing — '{phone}'")
        return False
    if not name or len(name.strip()) < 2:
        print(f"  VALIDATION FAILED: Name missing or too short — '{name}'")
        return False
    if not lead.get("city"):
        print(f"  VALIDATION FAILED: City missing for {name}")
        return False

    return True

def make_fingerprint(lead: dict) -> str:
    """
    Composite dedup fingerprint beyond phone number.
    Catches same broker listed under different numbers.
    """
    import hashlib
    # Uses full_name + agency_name + city per spec
    raw = (
        (lead.get('full_name') or '').lower().strip() +
        (lead.get('agency_name') or '').lower().strip() +
        (lead.get('city') or '').lower().strip()
    )
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def calculate_pain_score(lead_data):
    """
    PENALTY-BASED scoring model v2.
    Start at 100. Subtract for detected automation maturity.
    Unknown signals = NEUTRAL. Never inflate due to missing data.

    Score interpretation:
    80-100 = Highly vulnerable — no visible lead handling
    60-79  = Moderate — some automation but gaps exist
    40-59  = Partial — has basic systems
    0-39   = Low priority — well automated, skip
    """
    score = 100
    deductions = []
    notes = []

    if lead_data.get("has_whatsapp_button"):
        score -= 20
        deductions.append("WhatsApp present (-20)")

    if lead_data.get("has_website_chatbot"):
        score -= 25
        deductions.append("Chatbot detected (-25)")

    contact_channels = sum([
        bool(lead_data.get("has_whatsapp_button")),
        bool(lead_data.get("has_inquiry_form")),
        bool(lead_data.get("has_afterhours_contact")),
        bool(lead_data.get("has_website_chatbot")),
    ])
    if contact_channels >= 2:
        score -= 15
        deductions.append("Multiple contact channels (-15)")

    if lead_data.get("has_inquiry_form"):
        score -= 10
        deductions.append("Inquiry form present (-10)")

    if not lead_data.get("website_url"):
        notes.append("No website — signals unknown")

    score = max(0, score)
    pain_reason = " | ".join(deductions) if deductions else "No automation detected"
    if notes:
        pain_reason += " | " + " | ".join(notes)

    return score, pain_reason


# ============================================================
# WEBSITE CHECKER
# Checks public website for pain signals
# ============================================================
def check_website(url):
    """
    Checks a broker website for automation signals.
    Returns dict of boolean flags.
    """
    result = {
        "has_website_chatbot": False,
        "has_inquiry_form": False,
        "has_afterhours_contact": False,
        "has_whatsapp_button": False,
    }

    if not url:
        return result

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"
        }
        response = requests.get(url, headers=headers, timeout=10)
        html = response.text.lower()

        # Chatbot indicators
        chatbot_signals = ["intercom", "drift", "tidio", "crisp", "zendesk", "freshchat", 
                          "livechat", "tawk.to", "chat-widget", "chatbot", "live chat"]
        result["has_website_chatbot"] = any(s in html for s in chatbot_signals)

        # WhatsApp button
        wa_signals = ["wa.me", "whatsapp", "api.whatsapp"]
        result["has_whatsapp_button"] = any(s in html for s in wa_signals)

        # Inquiry form
        form_signals = ["contact-form", "inquiry-form", "enquiry", "<form", "contact us"]
        result["has_inquiry_form"] = any(s in html for s in form_signals)

        # After hours contact
        afterhours_signals = ["after hours", "24/7", "available anytime", "after-hours",
                              "outside office", "emergency contact"]
        result["has_afterhours_contact"] = any(s in html for s in afterhours_signals)

    except Exception as e:
        print(f"    Website check failed for {url}: {e}")

    return result

# ============================================================
# OUTSCRAPER API CALL
# ============================================================
def fetch_from_outscraper(query, location, limit=20):
    """
    Calls Outscraper Google Maps API.
    Returns list of raw place dicts.
    Free tier: 25 searches/month with 20 results each = 500 leads.
    Retries up to MAX_RETRIES times with exponential backoff.
    """
    print(f"\n  Fetching: '{query}' in '{location}'")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                "https://api.app.outscraper.com/maps/search-v3",
                headers={"X-API-KEY": OUTSCRAPER_API_KEY},
                params={
                    "query": f"{query} {location}",
                    "limit": limit,
                    "language": "en",
                    "fields": "name,phone,site,full_address,city,country,place_id,url,type,subtypes"
                },
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                results = data.get("data", [])
                if results and isinstance(results[0], list):
                    results = results[0]
                print(f"  Got {len(results)} results")

                # Zero results = FAILED not success
                if len(results) == 0:
                    raise RuntimeError(f"API returned 0 results for '{query}' in '{location}' — marking run FAILED")

                # Anomaly detection — warn if suspiciously low
                if len(results) < 5 and limit >= 10:
                    print(f"  WARNING: Low result count ({len(results)}) — possible rate limit or API drift")

                return results

            elif response.status_code == 429:
                wait = RETRY_BACKOFF_BASE ** attempt
                print(f"  Rate limited. Waiting {wait}s before retry {attempt}/{MAX_RETRIES}")
                time.sleep(wait)

            else:
                msg = f"API FAILED: Outscraper returned {response.status_code}: {response.text[:200]}"
                print(f"  {msg}")
                raise RuntimeError(msg)  # Hard stop — not silent

        except requests.exceptions.Timeout:
            wait = RETRY_BACKOFF_BASE ** attempt
            print(f"  TIMEOUT on attempt {attempt}/{MAX_RETRIES}. Retrying in {wait}s")
            time.sleep(wait)

        except Exception as e:
            print(f"  ERROR: Outscraper request failed (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt == MAX_RETRIES:
                return []
            time.sleep(RETRY_BACKOFF_BASE ** attempt)

    print(f"  FAILED: All {MAX_RETRIES} retries exhausted for '{query}' in '{location}'")
    return []

# ============================================================
# SUPABASE UPSERT
# ============================================================
def upsert_lead(supabase: Client, lead: dict):
    """
    Inserts or updates lead by phone number.
    Skips duplicates silently.
    Retries on transient errors.
    Returns True if new lead added.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            supabase.table("broker_leads").upsert(
                lead,
                on_conflict="phone_number",
                ignore_duplicates=True
            ).execute()
            return True

        except Exception as e:
            error_str = str(e).lower()
            if "duplicate" in error_str or "unique" in error_str:
                return False  # Expected — already exists
            print(f"  ERROR: Supabase upsert attempt {attempt}/{MAX_RETRIES}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_BASE ** attempt)

    print(f"  FAILED: Could not upsert lead '{lead.get('full_name')}' after {MAX_RETRIES} attempts")
    return False

# ============================================================
# PROCESS ONE RESULT
# ============================================================
def process_result(raw, country):
    """
    Converts raw Outscraper result to clean lead dict.
    Runs website check and pain scoring.
    """
    phone = clean_phone(raw.get("phone"))
    if not phone:
        return None  # No phone = not actionable

    name = (raw.get("name") or "").strip()[:100]
    website = raw.get("site") or ""
    maps_url = raw.get("url") or ""
    city = raw.get("city") or raw.get("full_address", "").split(",")[0]

    # Check website for pain signals
    print(f"  Checking: {name} — {website or 'no website'}")
    website_signals = check_website(website)

    # Build lead dict
    lead = {
        "full_name": name,
        "agency_name": name,
        "phone_number": phone,
        "whatsapp_number": phone if is_whatsapp_likely(phone) else None,
        "website_url": website or None,
        "google_maps_url": maps_url or None,
        "city": city,
        "country": country,
        "source": "outscraper_maps",
        "contact_status": "new",
        "outreach_status": "pending",
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
        "followup_due_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        **website_signals
    }

    # Validation gate — block garbage before scoring or insert
    if not validate_lead(lead):
        return None

    # Calculate pain score
    pain_score, pain_reason = calculate_pain_score(lead)
    lead["pain_score"] = pain_score
    lead["pain_reason"] = pain_reason
    lead["lead_fingerprint"] = make_fingerprint(lead)
    lead["scoring_version"] = "v1"

    return lead

# ============================================================
# RUN TRACKING
# ============================================================
def start_run(supabase: Client) -> str:
    """Creates a run record. Returns run_id."""
    try:
        result = supabase.table("scraper_runs").insert({
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat()
        }).execute()
        run_id = result.data[0]["run_id"]
        print(f"  Run ID: {run_id}")
        return run_id
    except Exception as e:
        print(f"  WARNING: Could not create run record: {e}")
        return None

def finish_run(supabase: Client, run_id: str, stats: dict, errors: list):
    """Updates run record with final stats and status."""
    if not run_id:
        return

    has_errors = len(errors) > 0
    has_data = stats["leads_inserted"] > 0
    status = "success" if has_data and not has_errors else \
             "partial" if has_data and has_errors else \
             "fail"

    try:
        supabase.table("scraper_runs").update({
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "leads_fetched": stats["leads_fetched"],
            "leads_inserted": stats["leads_inserted"],
            "leads_skipped": stats["leads_skipped"],
            "duplicates_skipped": stats["duplicates_skipped"],
            "error_log": "\n".join(errors) if errors else None,
            "status": status
        }).eq("run_id", run_id).execute()
        print(f"  Run status: {status.upper()}")
    except Exception as e:
        print(f"  WARNING: Could not update run record: {e}")

# ============================================================
# MAIN RUNNER
# ============================================================
def main():
    print("=" * 60)
    print("BROKER LEAD SCRAPER v2 — Controlled First Run")
    print(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Hard cap: {HARD_CAP_LEADS} leads max this run")
    print("=" * 60)

    validate_env()

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("Supabase connected OK")

    # Start run tracking — fail fast if table missing
    run_id = start_run(supabase)
    if not run_id:
        raise RuntimeError("CRITICAL: scraper_runs table missing or inaccessible. Run schema.sql first.")

    stats = {
        "leads_fetched": 0,
        "leads_inserted": 0,
        "leads_skipped": 0,
        "duplicates_skipped": 0,
    }
    errors = []

    try:
        for target in TARGETS:

            # Hard cap check before each query
            if stats["leads_inserted"] >= HARD_CAP_LEADS:
                print(f"\n  HARD CAP REACHED ({HARD_CAP_LEADS}). Stopping.")
                break

            raw_results = fetch_from_outscraper(
                query=target["query"],
                location=target["location"],
                limit=MAX_RESULTS_PER_QUERY
            )

            for raw in raw_results:

                # Hard cap check per lead
                if stats["leads_inserted"] >= HARD_CAP_LEADS:
                    print(f"  HARD CAP REACHED. Stopping mid-batch.")
                    break

                stats["leads_fetched"] += 1

                try:
                    lead = process_result(raw, target["country"])
                except Exception as e:
                    msg = f"ERROR processing lead '{raw.get('name', 'unknown')}': {e}"
                    print(f"  {msg}")
                    errors.append(msg)
                    stats["leads_skipped"] += 1
                    continue

                if not lead:
                    stats["leads_skipped"] += 1
                    continue

                try:
                    is_new = upsert_lead(supabase, lead)
                except Exception as e:
                    msg = f"ERROR inserting lead '{lead.get('full_name', 'unknown')}': {e}"
                    print(f"  {msg}")
                    errors.append(msg)
                    stats["leads_skipped"] += 1
                    continue

                if is_new:
                    stats["leads_inserted"] += 1
                    qualified = "✅ QUALIFIED" if lead["pain_score"] >= 50 else "—"
                    print(f"  [{stats['leads_inserted']}/{HARD_CAP_LEADS}] Added: {lead['full_name']} | Score: {lead['pain_score']} | {qualified}")
                else:
                    stats["duplicates_skipped"] += 1
                    print(f"  Duplicate skipped: {lead['full_name']}")

                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

            print(f"\n  Waiting before next target...")
            time.sleep(random.uniform(10, 20))

    except Exception as e:
        msg = f"CRITICAL ERROR in main loop: {e}"
        print(f"\n  {msg}")
        errors.append(msg)

    finally:
        # Always write run results — even on crash
        finish_run(supabase, run_id, stats, errors)

        print("\n" + "=" * 60)
        print("RUN COMPLETE")
        print(f"  Leads fetched:      {stats['leads_fetched']}")
        print(f"  Leads inserted:     {stats['leads_inserted']}")
        print(f"  Duplicates skipped: {stats['duplicates_skipped']}")
        print(f"  Leads skipped:      {stats['leads_skipped']}")
        print(f"  Errors:             {len(errors)}")
        if errors:
            print("\n  ERROR LOG:")
            for err in errors:
                print(f"    - {err}")
        print("=" * 60)
        print("\nNext: Open Supabase, sort broker_leads by pain_score DESC.")
        print("Contact top leads manually on WhatsApp. Do not scale yet.")

if __name__ == "__main__":
    main()
