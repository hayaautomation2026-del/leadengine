"""Backfill public contact details for LeadEngine leads missing email."""

from niche_worker import SUPABASE_KEY, SUPABASE_URL, discover_contact_details, now, sb

PLACEHOLDER_DOMAINS = {
    "domain.com",
    "example.com",
    "example.org",
    "test.com",
    "email.com",
    "yourdomain.com",
}


def is_placeholder_email(email):
    if not email or "@" not in email:
        return False
    return email.lower().rsplit("@", 1)[-1] in PLACEHOLDER_DOMAINS


def clean_placeholders():
    rows = sb(
        "GET",
        "leads",
        params={"select": "id,email", "email": "not.is.null", "limit": "200"},
    ) or []
    removed = 0
    for row in rows:
        if is_placeholder_email(row.get("email")):
            sb(
                "PATCH",
                "leads",
                params={"id": f"eq.{row['id']}"},
                body={"email": None, "email_source": None, "last_checked_at": now()},
            )
            removed += 1
    return removed


def run(limit=30):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY are required")

    placeholders_removed = clean_placeholders()

    rows = sb(
        "GET",
        "leads",
        params={
            "select": "id,business_name,website_url,email,whatsapp_number",
            "email": "is.null",
            "website_url": "not.is.null",
            "order": "created_at.desc",
            "limit": str(limit),
        },
    ) or []

    checked = email_found = whatsapp_found = updated = 0

    for lead in rows:
        website = str(lead.get("website_url") or "").strip()
        if not website:
            continue

        checked += 1
        email, whatsapp = discover_contact_details(website)
        if is_placeholder_email(email):
            email = None

        patch = {"last_checked_at": now()}
        changed = False

        if email:
            patch["email"] = email
            patch["email_source"] = "website"
            email_found += 1
            changed = True

        if whatsapp and not lead.get("whatsapp_number"):
            patch["whatsapp_number"] = whatsapp
            whatsapp_found += 1
            changed = True

        sb("PATCH", "leads", params={"id": f"eq.{lead['id']}"}, body=patch)
        if changed:
            updated += 1

    print(
        f"CONTACT ENRICHMENT | checked={checked} updated={updated} "
        f"emails={email_found} whatsapp={whatsapp_found} "
        f"placeholders_removed={placeholders_removed}"
    )


if __name__ == "__main__":
    run()
