"""Crawlee/Playwright Google Maps fallback for LeadEngine.

V1 contract: only returns usable leads with name + phone + address.
Website is included when Google exposes it. Maps URL is always included.

This intentionally avoids Google Maps CSS class names. It prefers semantic
attributes (roles, aria-labels, data-item-id) and raises explicit errors for
block pages or selector drift instead of silently returning an empty list.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus


class MapsBlockedError(RuntimeError):
    pass


class SelectorDriftError(RuntimeError):
    pass


def _strip_prefix(value: str | None, labels: tuple[str, ...]) -> str | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    lowered = text.lower()
    for label in labels:
        prefix = label.lower() + ":"
        if lowered.startswith(prefix):
            return text[len(prefix):].strip() or None
    return text or None


async def _first_attr(page, selectors: list[str], attr: str) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count():
                value = await locator.get_attribute(attr)
                if value:
                    return value.strip()
        except Exception:
            continue
    return None


async def _first_text(page, selectors: list[str]) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count():
                value = (await locator.inner_text()).strip()
                if value:
                    return value
        except Exception:
            continue
    return None


async def _accept_consent_if_present(page) -> None:
    # Google wording can vary by locale. These are best-effort only.
    for pattern in (r"accept all", r"accept", r"agree", r"i agree"):
        try:
            button = page.get_by_role("button", name=re.compile(pattern, re.I)).first
            if await button.count() and await button.is_visible():
                await button.click(timeout=2500)
                await page.wait_for_timeout(1000)
                return
        except Exception:
            continue


async def _detect_block(page) -> None:
    try:
        body = (await page.locator("body").inner_text()).lower()
    except Exception:
        return
    markers = (
        "unusual traffic",
        "automated queries",
        "our systems have detected",
        "sorry, but your computer or network may be sending automated queries",
        "recaptcha",
    )
    if any(marker in body for marker in markers):
        raise MapsBlockedError("google_maps_blocked_or_challenged")


async def _collect_place_urls(page, limit: int) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    # Search-result links expose /maps/place/ URLs. Avoid brittle class names.
    for _ in range(12):
        await _detect_block(page)
        links = page.locator('a[href*="/maps/place/"]')
        count = await links.count()
        for i in range(count):
            href = await links.nth(i).get_attribute("href")
            if not href or href in seen:
                continue
            seen.add(href)
            urls.append(href)
            if len(urls) >= limit:
                return urls

        # Prefer the semantic feed when present; fall back to wheel scrolling.
        try:
            feed = page.get_by_role("feed").first
            if await feed.count():
                await feed.evaluate("el => el.scrollBy(0, Math.max(el.clientHeight * 2, 1200))")
            else:
                await page.mouse.wheel(0, 1800)
        except Exception:
            await page.mouse.wheel(0, 1800)
        await page.wait_for_timeout(1200)

    return urls


async def _extract_detail(page, maps_url: str) -> dict | None:
    await page.goto(maps_url, wait_until="domcontentloaded", timeout=45_000)
    await page.wait_for_timeout(1800)
    await _accept_consent_if_present(page)
    await _detect_block(page)

    name = await _first_text(page, ["h1", '[role="main"] h1'])

    address_label = await _first_attr(
        page,
        [
            'button[data-item-id="address"]',
            '[data-item-id="address"]',
            'button[aria-label^="Address:"]',
        ],
        "aria-label",
    )
    address = _strip_prefix(address_label, ("Address",))

    phone_label = await _first_attr(
        page,
        [
            'button[data-item-id^="phone:tel:"]',
            '[data-item-id^="phone:tel:"]',
            'button[aria-label^="Phone:"]',
        ],
        "aria-label",
    )
    phone = _strip_prefix(phone_label, ("Phone", "Call"))

    website = await _first_attr(
        page,
        [
            'a[data-item-id="authority"]',
            'a[aria-label^="Website:"]',
        ],
        "href",
    )

    # V1 usability contract: no phone or no address = not a usable lead.
    if not name or not phone or not address:
        return None

    return {
        "name": name,
        "phone": phone,
        "site": website,
        "url": maps_url,
        "address": address,
    }


async def _scrape(query: str, limit: int) -> list[dict]:
    try:
        from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
    except Exception as exc:
        raise RuntimeError("crawlee_playwright_not_installed") from exc

    results: list[dict] = []
    diagnostics = {"search_page_loaded": False, "place_urls": 0, "detail_pages": 0}

    crawler = PlaywrightCrawler(
        headless=True,
        max_requests_per_crawl=1,
        max_request_retries=1,
        request_handler_timeout=180,
    )

    @crawler.router.default_handler
    async def handler(context: PlaywrightCrawlingContext) -> None:
        page = context.page
        diagnostics["search_page_loaded"] = True
        await _accept_consent_if_present(page)
        await page.wait_for_timeout(1800)
        await _detect_block(page)

        place_urls = await _collect_place_urls(page, max(limit * 2, limit + 5))
        diagnostics["place_urls"] = len(place_urls)

        if not place_urls:
            # A loaded Maps search with zero listing links is much more likely
            # to be selector/UI drift or a challenge page than a valid success.
            raise SelectorDriftError("selector_drift:no_place_links_found")

        for url in place_urls:
            if len(results) >= limit:
                break
            diagnostics["detail_pages"] += 1
            try:
                row = await _extract_detail(page, url)
                if row:
                    results.append(row)
            except MapsBlockedError:
                raise
            except Exception as exc:
                print(f"CRAWLEE detail skipped | {url} | {type(exc).__name__}: {exc}")
            await page.wait_for_timeout(700)

    search_url = f"https://www.google.com/maps/search/{quote_plus(query)}?hl=en"
    await crawler.run([search_url])

    if not results:
        if diagnostics["place_urls"] > 0:
            raise SelectorDriftError(
                f"selector_drift:place_links={diagnostics['place_urls']},usable_details=0"
            )
        raise SelectorDriftError("selector_drift:no_usable_results")

    print(
        "CRAWLEE MAPS | "
        f"place_urls={diagnostics['place_urls']} "
        f"detail_pages={diagnostics['detail_pages']} "
        f"usable={len(results)}"
    )
    return results[:limit]


def run_crawlee_google_maps(query: str, limit: int = 20) -> list[dict]:
    """Synchronous entry point used by the LeadEngine worker."""
    return asyncio.run(_scrape(query, limit))
