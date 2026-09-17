"""Deterministic Indeed extraction with Scrapling's persisted adaptive selectors."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlencode, urlparse

from scrapling import Selector
from scrapling.fetchers import StealthySession


ROOT = Path(__file__).resolve().parent
FIELDS = {
    "title": '[data-testid="jobsearch-JobInfoHeader-title"]',
    "company": '[data-testid="inlineHeader-companyName"]',
    "location": '[data-testid="inlineHeader-companyLocation"]',
    "pay_and_type": '#salaryInfoAndJobType',
    "description": '#jobDescriptionText',
}
REACT_FIELDS = {
    "title": '[data-testid="vj-job-title"]',
    "company": '[data-testid="company-info-metadata"] a[href*="/cmp/"]',
    "location": '[data-testid="company-info-metadata"] > div > div:last-child',
    "pay_and_type": '[data-testid="desktop-job-header"] > div > div[aria-label]:not([role="presentation"])',
    "description": '.simple-job-description-html',
}


class ExtractionError(ValueError):
    """The page cannot safely be treated as the requested job."""


def canonical_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"ca.indeed.com", "www.indeed.com"}:
        raise ExtractionError("This adapter supports HTTPS ca.indeed.com/www.indeed.com URLs only.")
    query = parse_qs(parsed.query)
    keys = query.get("vjk", query.get("jk", []))
    if len(keys) != 1 or not re.fullmatch(r"[a-fA-F0-9]{16}", keys[0]):
        raise ExtractionError("Expected one 16-character Indeed job ID in vjk or jk.")
    return f"https://{parsed.hostname}/viewjob?{urlencode({'jk': keys[0]})}", keys[0]


def extract(html, url, state_dir):
    canonical, job_id = canonical_url(url)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    page = Selector(
        html, url=canonical, adaptive=True,
        storage_args={"storage_file": str(state_dir / "selectors.db"), "url": canonical},
    )
    # Authentication and challenge pages must never train or use fuzzy matching.
    title = page.css("title").get(default="").lower()
    text = page.get_all_text(strip=True).lower()
    if any(s in title for s in ("just a moment", "access denied", "sign in")) or any(
        s in text for s in ("verify you are human", "redirecting to login", "create an account or sign in.")
    ):
        raise ExtractionError("Indeed returned a sign-in/challenge page; no template was saved.")

    # Indeed currently serves both traditional and React Native Web layouts.
    # Keep independent references instead of matching one unrelated DOM to another.
    react = bool(page.css('[data-testid="viewjob-main-content"], [data-testid="vj-job-title"]'))
    fields = REACT_FIELDS if react else FIELDS
    namespace = "indeed:react" if react else "indeed"
    selected, values, methods = {}, {}, {}
    for field, css in fields.items():
        identifier = f"{namespace}:{field}"
        known = page.retrieve(identifier) is not None
        exact = page.css(css)
        matches = exact or (page.css(css, adaptive=True, identifier=identifier, percentage=70) if known else [])
        if len(matches) != 1:
            raise ExtractionError(f"Expected one {field} element, found {len(matches)}; needs review.")
        node = matches[0]
        value = str(node.get_all_text(separator="\n" if field == "description" else " ", strip=True)).strip()
        if not value or node.tag in {"html", "body", "script", "form"}:
            raise ExtractionError(f"Invalid {field} match; needs review.")
        selected[field] = node
        values[field] = value
        methods[field] = "exact" if exact else "adaptive"

    description = values["description"]
    if len(description) < 200 or len(description) > 100_000:
        raise ExtractionError("Description is empty, too short, or implausibly large; needs review.")
    if len(values["title"]) > 250 or len(values["company"]) > 250:
        raise ExtractionError("Metadata matched an oversized container; needs review.")
    if selected["description"].css("h1, form, input, button"):
        raise ExtractionError("Description includes page controls or a job header; needs review.")

    # Train only after ALL fields pass validation. Preserve the original reference
    # on future runs so a fuzzy match cannot silently poison it.
    learned = []
    for field, css in fields.items():
        identifier = f"{namespace}:{field}"
        if page.retrieve(identifier) is None:
            page.css(css, auto_save=True, identifier=identifier)
            learned.append(field)

    return {
        "job_id": job_id, "source_url": url, "canonical_url": canonical,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        **values,
        "extraction": {"library": "scrapling", "llm_used": False, "layout": "react" if react else "classic", "methods": methods, "learned": learned},
    }


def fetch(url, state_dir, headed=False):
    canonical, _ = canonical_url(url)
    origin = f"https://{urlparse(canonical).hostname}/"
    profile = Path(state_dir).resolve() / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    # Use a dedicated persistent profile, never the user's Chrome profile.
    with StealthySession(
        headless=not headed, user_data_dir=str(profile),
        timeout=60000, retries=1, locale="en-CA",
    ) as session:
        session.fetch(origin, google_search=False)
        response = session.fetch(canonical, google_search=False, wait=2000)
        # Retain the latest response for diagnosing a block or layout change.
        (Path(state_dir) / "last-response.html").write_bytes(response.body)
        if response.status != 200 or urlparse(response.url).hostname != urlparse(canonical).hostname:
            raise ExtractionError(f"Indeed did not return the job page (HTTP {response.status}, host {urlparse(response.url).hostname}).")
        actual_url, _ = canonical_url(response.url)
        if actual_url != canonical:
            raise ExtractionError("Indeed redirected to a different job; needs review.")
        return response.body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--html", type=Path, help="Parse an already captured rendered job DOM (no network).")
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".scrapling")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--headed", action="store_true", help="Show the dedicated Scrapling browser.")
    args = parser.parse_args()
    try:
        canonical_url(args.url)
        html = args.html.read_bytes() if args.html else fetch(args.url, args.state_dir, args.headed)
        result = extract(html, args.url, args.state_dir)
        result["extraction"]["source"] = "captured_html" if args.html else "live_scrapling_browser"
        args.output_dir.mkdir(parents=True, exist_ok=True)
        path = args.output_dir / f"indeed-{result['job_id']}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"status": "ok", "output": str(path), "title": result["title"], "extraction": result["extraction"]}, indent=2))
        return 0
    except (ExtractionError, OSError) as exc:
        print(json.dumps({"status": "needs_review", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
