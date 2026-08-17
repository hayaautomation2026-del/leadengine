"""
LeadEngine Finder + Qualifier
=============================
LeadEngine owns the workflow. Finders are replaceable data sources.

Current finder: gosom/google-maps-scraper (open-source Docker image)
Destination: Supabase broker_leads + scraper_runs

Manual example:
  SEARCH_REQUEST="dentists in Utah" python scraper.py

No Google Maps API / SerpApi key is required for the current finder.
"""

import csv
import hashlib
import os
import random
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SEARCH_REQUEST = (os.environ.get("SEARCH_REQUEST") or "").strip()
FINDER_SOURCE = (os.environ.get("FINDER_SOURCE") or "gosom_gmaps").strip()

HARD_CAP_LEADS = int(os.environ.get("HARD_CAP_LEADS", "20"))
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2
REQUEST_DELAY_MIN = 1
REQUEST_DELAY_MAX = 3

# Runs when no manual SEARCH_REQUEST is supplied.
SAVED_SEARCHES = [
    "real estate brokers in Dubai UAE",
    "real estate agencies in Abu Dhabi UAE",
    "real estate brokers in Miami Florida USA",
    "real estate brokers in Houston Texas USA",
]


def validate_env():
    missing = []
    if not SUPABASE_URL:
        missing.append("SUPABASE_URL")
    if not SUPABASE_KEY:
        missing.append("SUPABASE_KEY")
    if missing:
        raise EnvironmentError(f"Missing required secrets: {', '.join(missing)}")


def clean_phone(raw):
    if not raw:
        return None
    cleaned = re.sub(r"[^\d+]", "", str(raw))
    if len(cleaned) < 7:
        return None
    return cleaned[:30]


def is_whatsapp_likely(phone):
    if not phone:
        return False
    return "+971" in phone or phone.startswith("971") or any(
        phone.startswith(p) for p in ["050", "052", "054", "055", "056", "058"]
    )


def pick(row, *names):
    """Return the first non-empty field, case-insensitively."""
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def run_gosom_google_maps(search_request, limit=20):
    """Run the open-source Google Maps scraper as one replaceable finder."""
    print(f"\nFINDER [gosom_gmaps] → {search_request}")

    with tempfile.TemporaryDirectory(prefix="leadengine_") as tmp:
        tmp_path = Path(tmp)
        queries = tmp_path / "queries.txt"
        output = tmp_path / "results.csv"
        queries.write_text(search_request + "\n", encoding="utf-8")

        cmd = [
            "docker", "run", "--rm",
            "-v", "gmaps-playwright-cache:/opt",
            "-v", f"{queries}:/queries.txt:ro",
            "-v", f"{tmp_path}:/out",
            "gosom/google-maps-scraper",
            "-input", "/queries.txt",
            "-results", "/out/results.csv",
            "-depth", "1",
            "-c", "1",
            "-exit-on-inactivity", "2m",
        ]

        completed = subprocess.run(cmd, text=True, capture_output=True, timeout=420)
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout or "unknown scraper error")[-1500:]
            raise RuntimeError(f"gosom finder failed: {details}")
        if not output.exists():
            raise RuntimeError("gosom finder completed but produced no results file")

        rows = []
        with output.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Flexible mapping protects us from harmless upstream column naming changes.
                rows.append({
                    "name": pick(row, "title", "name"),
                    "phone": pick(row, "phone", "telephone"),
                    "site": pick(row, "website", "web_site", "site"),
                    "url": pick(row, "link", "google_maps_url", "url"),
                    "address": pick(row, "address", "complete_address"),
                    "category": pick(row, "category", "categories"),
                    "rating": pick(row, "review_rating", "rating"),
                    "reviews": pick(row, "reviews", "review_count", "reviews_count"),
                })
                if len(rows) >= limit:
                    break

        print(f"FINDER returned {len(rows)} rows")
        return rows


def find_businesses(search_request, limit=20):
    """
    Single LeadEngine finder interface.
    Add future sources here without changing scoring/database/dashboard code.
    """
    if FINDER_SOURCE == "gosom_gmaps":
        return run_gosom_google_maps(search_request, limit)
    raise ValueError(f"Unknown FINDER_SOURCE: {FINDER_SOURCE}")


def check_website(url):
    result = {
        "has_website_chatbot": False,
        "has_inquiry_form": False,
        "has_afterhours_contact": False,
        "has_whatsapp_button": False,
    }
    if not url:
        return result

    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 LeadEngine/1.0"},
            timeout=8,
            allow_redirects=True,
        )
        html = response.text.lower()[:1_500_000]
        result["has_website_chatbot"] = any(
            s in html for s in ["intercom", "drift", "tidio", "crisp", "zendesk", "freshchat", "tawk.to", "chatbot", "live chat"]
        )
        result["has_whatsapp_button"] = any(s in html for s in ["wa.me", "whatsapp", "api.whatsapp"])
        result["has_inquiry_form"] = any(s in html for s in ["contact-form", "inquiry-form", "enquiry", "<form", "contact us"])
        result["has_afterhours_contact"] = any(
            s in html for s in ["after hours", "24/7", "available anytime", "after-hours", "emergency contact"]
        )
    except Exception as exc:
        print(f"Website check skipped: {url} ({exc})")
    return result


def calculate_pain_score(lead):
    score = 100
    reasons = []

    if lead.get("has_whatsapp_button"):
        score -= 20
        reasons.append("WhatsApp present")
    if lead.get("has_website_chatbot"):
        score -= 25
        reasons.append("Chatbot detected")
    if lead.get("has_inquiry_form"):
        score -= 10
        reasons.append("Inquiry form present")

    channels = sum(bool(lead.get(k)) for k in [
        "has_whatsapp_button", "has_inquiry_form", "has_afterhours_contact", "has_website_chatbot"
    ])
    if channels >= 2:
        score -= 15
        reasons.append("Multiple contact channels")

    if not lead.get("website_url"):
        reasons.append("No website; automation signals unknown")

    return max(0, score), " | ".join(reasons) if reasons else "No obvious automation detected"


def make_fingerprint(lead):
    raw = "|".join([
        (lead.get("full_name") or "").lower().strip(),
        (lead.get("agency_name") or "").lower().strip(),
        (lead.get("phone_number") or "").strip(),
    ])
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def process_result(raw, search_request):
    phone = clean_phone(raw.get("phone"))
    name = (raw.get("name") or "").strip()[:150]
    if not phone or len(name) < 2:
        return None

    website = (raw.get("site") or "").strip()
    signals = check_website(website)
    lead = {
        "full_name": name,
        "agency_name": name,
        "phone_number": phone,
        "whatsapp_number": phone if is_whatsapp_likely(phone) else None,
        "website_url": website or None,
        "google_maps_url": raw.get("url") or None,
        "city": None,
        "country": None,
        "source": FINDER_SOURCE,
        "source_url": raw.get("url") or None,
        "contact_status": "new",
        "outreach_status": "pending",
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
        "followup_due_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
        "notes": f"Search: {search_request}"[:1000],
        **signals,
    }
    score, reason = calculate_pain_score(lead)
    lead["pain_score"] = score
    lead["pain_reason"] = reason
    lead["lead_fingerprint"] = make_fingerprint(lead)
    lead["scoring_version"] = "v2"
    return lead


def start_run(supabase: Client):
    result = supabase.table("scraper_runs").insert({
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    return result.data[0]["run_id"]


def finish_run(supabase: Client, run_id, stats, errors):
    if not run_id:
        return
    status = "success" if stats["leads_inserted"] > 0 and not errors else (
        "partial" if stats["leads_inserted"] > 0 else "fail"
    )
    supabase.table("scraper_runs").update({
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "leads_fetched": stats["leads_fetched"],
        "leads_inserted": stats["leads_inserted"],
        "leads_skipped": stats["leads_skipped"],
        "duplicates_skipped": stats["duplicates_skipped"],
        "error_log": "\n".join(errors) if errors else None,
        "status": status,
    }).eq("run_id", run_id).execute()


def insert_lead(supabase: Client, lead):
    """Returns True for a newly accepted row; False for an existing phone."""
    existing = supabase.table("broker_leads").select("id").eq("phone_number", lead["phone_number"]).limit(1).execute()
    if existing.data:
        return False
    supabase.table("broker_leads").insert(lead).execute()
    return True


def main():
    validate_env()
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    run_id = start_run(supabase)
    searches = [SEARCH_REQUEST] if SEARCH_REQUEST else SAVED_SEARCHES

    stats = {"leads_fetched": 0, "leads_inserted": 0, "leads_skipped": 0, "duplicates_skipped": 0}
    errors = []

    print("=" * 60)
    print("LEADENGINE FINDER v3")
    print(f"Finder: {FINDER_SOURCE}")
    print(f"Searches: {searches}")
    print(f"Hard cap: {HARD_CAP_LEADS}")
    print("=" * 60)

    try:
        for search_request in searches:
            if stats["leads_inserted"] >= HARD_CAP_LEADS:
                break
            try:
                raw_results = find_businesses(search_request, HARD_CAP_LEADS)
            except Exception as exc:
                errors.append(f"Finder failed for '{search_request}': {exc}")
                continue

            for raw in raw_results:
                if stats["leads_inserted"] >= HARD_CAP_LEADS:
                    break
                stats["leads_fetched"] += 1
                try:
                    lead = process_result(raw, search_request)
                    if not lead:
                        stats["leads_skipped"] += 1
                        continue
                    if insert_lead(supabase, lead):
                        stats["leads_inserted"] += 1
                        print(f"ADDED {lead['full_name']} | score {lead['pain_score']}")
                    else:
                        stats["duplicates_skipped"] += 1
                except Exception as exc:
                    stats["leads_skipped"] += 1
                    errors.append(f"Lead processing failed: {exc}")
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
    finally:
        finish_run(supabase, run_id, stats, errors)
        print(f"DONE: {stats} | errors={len(errors)}")


if __name__ == "__main__":
    main()
