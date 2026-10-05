"""Deterministic Indeed/LinkedIn extraction with adaptive Scrapling selectors."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time
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
LINKEDIN_FIELDS = {
    "title": (
        (
            "xpath",
            '//div[starts-with(@aria-label, "Company,")]/ancestor::div[3]/div[2]',
        ),
        ("css", "h1.job-details-jobs-unified-top-card__job-title"),
        ("css", ".job-details-jobs-unified-top-card__job-title h1"),
        ("css", "h1.t-24.t-bold.inline"),
    ),
    "company": (
        ("xpath", '//div[starts-with(@aria-label, "Company,")]/p'),
        ("css", ".job-details-jobs-unified-top-card__company-name a"),
        ("css", ".job-details-jobs-unified-top-card__company-name"),
    ),
    "location": (
        (
            "xpath",
            '//div[starts-with(@aria-label, "Company,")]/ancestor::div[3]/p[1]',
        ),
        (
            "css",
            ".job-details-jobs-unified-top-card__tertiary-description-container "
            ".tvm__text--low-emphasis:first-child",
        ),
        ("css", ".job-details-jobs-unified-top-card__primary-description-container"),
    ),
    "description": (
        (
            "xpath",
            '//h2[normalize-space(.)="About the job"]/parent::div/parent::div/p[1]',
        ),
        ("css", "#job-details"),
        ("css", ".jobs-description__content"),
        ("css", ".jobs-box__html-content"),
    ),
}
LINKEDIN_OPTIONAL_FIELDS = {
    "pay_and_type": (
        (
            "xpath",
            '//div[starts-with(@aria-label, "Company,")]/ancestor::div[3]'
            "/following-sibling::div[1]",
        ),
        ("css", ".job-details-preferences-and-skills__pill"),
        ("css", ".job-details-jobs-unified-top-card__job-insight"),
    ),
}


class ExtractionError(ValueError):
    """The page cannot safely be treated as the requested job."""


def canonical_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ExtractionError("Job URLs must use HTTPS.")
    if parsed.hostname in {"ca.indeed.com", "www.indeed.com"}:
        query = parse_qs(parsed.query)
        keys = query.get("vjk", query.get("jk", []))
        if len(keys) != 1 or not re.fullmatch(r"[a-fA-F0-9]{16}", keys[0]):
            raise ExtractionError("Expected one 16-character Indeed job ID in vjk or jk.")
        return f"https://{parsed.hostname}/viewjob?{urlencode({'jk': keys[0]})}", keys[0]
    if parsed.hostname in {"linkedin.com", "www.linkedin.com"}:
        query = parse_qs(parsed.query)
        path_match = re.fullmatch(r"/jobs/view/(?:[^/]*-)?(\d{6,20})/?", parsed.path)
        keys = [path_match.group(1)] if path_match else query.get("currentJobId", [])
        if len(keys) != 1 or not re.fullmatch(r"\d{6,20}", keys[0]):
            raise ExtractionError(
                "Expected a LinkedIn /jobs/view/<job-id> URL or one currentJobId query value."
            )
        return f"https://www.linkedin.com/jobs/view/{keys[0]}/", keys[0]
    raise ExtractionError(
        "This adapter supports Indeed Canada/US and www.linkedin.com job URLs only."
    )


def board_for_url(url):
    return "linkedin" if urlparse(canonical_url(url)[0]).hostname == "www.linkedin.com" else "indeed"


def linkedin_auth_required(url):
    path = urlparse(url).path.rstrip("/")
    return path.startswith(("/login", "/checkpoint", "/authwall", "/uas/login"))


def _select(page, selector, **kwargs):
    method, query = selector
    return getattr(page, method)(query, **kwargs)


def _first_exact(page, selectors):
    for selector in selectors:
        matches = _select(page, selector)
        if len(matches) == 1:
            return selector, matches
    return selectors[0], []


def _extract_linkedin(page):
    selected, values, methods, exact_selectors = {}, {}, {}, {}
    for field, selectors in LINKEDIN_FIELDS.items():
        identifier = f"linkedin:{field}"
        selector, exact = _first_exact(page, selectors)
        known = page.retrieve(identifier) is not None
        matches = exact or (
            _select(
                page, selectors[0], adaptive=True,
                identifier=identifier, percentage=70,
            )
            if known else []
        )
        if len(matches) != 1:
            raise ExtractionError(
                f"Expected one LinkedIn {field} element, found {len(matches)}; needs review."
            )
        node = matches[0]
        if field == "description":
            # LinkedIn nests a visual "... more" button inside the prose node.
            # Extract text nodes outside controls so UI labels never enter data.
            parts = node.xpath(".//text()[not(ancestor::button)]").getall()
            value = "\n".join(part.strip() for part in parts if part.strip())
        else:
            value = str(node.get_all_text(separator=" ", strip=True)).strip()
        if not value or node.tag in {"html", "body", "script", "form"}:
            raise ExtractionError(f"Invalid LinkedIn {field} match; needs review.")
        selected[field], values[field] = node, value
        methods[field], exact_selectors[field] = (
            "exact" if exact else "adaptive"
        ), selector

    # The current LinkedIn header appends posting age and applicant count to
    # the location paragraph. They are volatile metadata, not part of location.
    values["location"] = re.split(r"\s*[\u00b7\u2022]\s*", values["location"], maxsplit=1)[0]

    # Employment details vary widely and are not essential to document generation.
    values["pay_and_type"], methods["pay_and_type"] = "", "missing_optional"
    for field, selectors in LINKEDIN_OPTIONAL_FIELDS.items():
        identifier = f"linkedin:{field}"
        selector, exact = _first_exact(page, selectors)
        known = page.retrieve(identifier) is not None
        matches = exact or (
            _select(
                page, selectors[0], adaptive=True,
                identifier=identifier, percentage=70,
            )
            if known else []
        )
        if len(matches) == 1:
            values[field] = str(matches[0].get_all_text(separator=" ", strip=True)).strip()
            methods[field] = "exact" if exact else "adaptive"
            exact_selectors[field] = selector

    return selected, values, methods, exact_selectors


def extract(html, url, state_dir):
    canonical, job_id = canonical_url(url)
    board = board_for_url(url)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    page = Selector(
        html, url=canonical, adaptive=True,
        storage_args={"storage_file": str(state_dir / "selectors.db"), "url": canonical},
    )
    # Authentication and challenge pages must never train or use fuzzy matching.
    title = page.css("title").get(default="").lower()
    text = page.get_all_text(strip=True).lower()
    login_form = bool(page.css('form[action*="login"], input[name="session_key"]'))
    if any(s in title for s in ("just a moment", "access denied")) or any(
        s in text for s in ("verify you are human", "redirecting to login")
    ):
        raise ExtractionError(f"{board.title()} returned a challenge page; no template was saved.")
    if board == "linkedin" and (login_form or ("linkedin" in title and "sign in" in title)):
        raise ExtractionError(
            "LinkedIn authentication is required. Run this command once with --headed, "
            "sign in in the dedicated browser window, and the session will be reused."
        )
    if board == "indeed" and (
        "sign in" in title or "create an account or sign in." in text
    ):
        raise ExtractionError("Indeed returned a sign-in page; no template was saved.")

    if board == "linkedin":
        selected, values, methods, exact_selectors = _extract_linkedin(page)
        namespace = "linkedin"
        layout = "authenticated-job-view"
    else:
        # Indeed currently serves both traditional and React Native Web layouts.
        # Keep independent references instead of matching one unrelated DOM to another.
        react = bool(page.css('[data-testid="viewjob-main-content"], [data-testid="vj-job-title"]'))
        fields = REACT_FIELDS if react else FIELDS
        namespace = "indeed:react" if react else "indeed"
        selected, values, methods = {}, {}, {}
        exact_selectors = {}
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
            exact_selectors[field] = css

        layout = "react" if react else "classic"

    description = values["description"]
    if len(description) < 200 or len(description) > 100_000:
        raise ExtractionError("Description is empty, too short, or implausibly large; needs review.")
    if len(values["title"]) > 250 or len(values["company"]) > 250:
        raise ExtractionError("Metadata matched an oversized container; needs review.")
    description_controls = selected["description"].css("h1, form, input")
    description_buttons = selected["description"].css("button")
    if board == "linkedin":
        unexpected_buttons = [
            button for button in description_buttons
            if not re.fullmatch(
                r"(?:\u2026|\.\.\.)?\s*(?:show\s+)?(?:more|less)",
                str(button.get_all_text(separator=" ", strip=True)).strip(),
                flags=re.IGNORECASE,
            )
        ]
    else:
        unexpected_buttons = description_buttons
    if description_controls or unexpected_buttons:
        raise ExtractionError("Description includes page controls or a job header; needs review.")

    # Train only after ALL required fields pass validation. Preserve the original
    # reference so a fuzzy match or malformed first response cannot poison it.
    learned = []
    for field, selector in exact_selectors.items():
        identifier = f"{namespace}:{field}"
        if page.retrieve(identifier) is None:
            if board == "linkedin":
                _select(page, selector, auto_save=True, identifier=identifier)
            else:
                page.css(selector, auto_save=True, identifier=identifier)
            learned.append(field)

    return {
        "job_id": job_id, "source_url": url, "canonical_url": canonical,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        **values,
        "extraction": {"library": "scrapling", "llm_used": False, "board": board,
                       "layout": layout, "methods": methods, "learned": learned},
    }


def fetch(url, state_dir, headed=False):
    canonical, _ = canonical_url(url)
    board = board_for_url(url)
    origin = f"https://{urlparse(canonical).hostname}/"
    profile = Path(state_dir).resolve() / f"browser-profile-{board}"
    profile.mkdir(parents=True, exist_ok=True)

    def wait_for_linkedin_login(page):
        def page_requires_authentication():
            if linkedin_auth_required(page.url):
                return True
            try:
                if page.locator('form[action*="login"], input[name="session_key"]').count():
                    return True
                page_title = page.title().lower()
                return "linkedin" in page_title and "sign in" in page_title
            except Exception:
                # Extraction performs the same checks after the browser closes.
                return False

        if board != "linkedin" or not headed:
            return
        if not page_requires_authentication():
            return
        print(
            "LinkedIn sign-in required. Complete sign-in in the dedicated browser window; "
            "credentials are handled only by LinkedIn.", file=sys.stderr,
        )
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if not page_requires_authentication():
                page.goto(canonical, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                return
            page.wait_for_timeout(1000)
        raise ExtractionError("LinkedIn sign-in was not completed within five minutes.")

    # Use a dedicated persistent profile, never the user's Chrome profile.
    with StealthySession(
        headless=not headed, user_data_dir=str(profile),
        timeout=60000, retries=1, locale="en-CA",
    ) as session:
        if board == "indeed":
            session.fetch(origin, google_search=False)
        response = session.fetch(
            canonical, google_search=False, wait=3000,
            page_action=wait_for_linkedin_login if board == "linkedin" else None,
        )
        # Retain the latest response for diagnosing a block or layout change.
        (Path(state_dir) / f"last-response-{board}.html").write_bytes(response.body)
        response_host = urlparse(response.url).hostname
        allowed_hosts = ({"linkedin.com", "www.linkedin.com"} if board == "linkedin"
                         else {urlparse(canonical).hostname})
        if response.status != 200 or response_host not in allowed_hosts:
            raise ExtractionError(
                f"{board.title()} did not return the job page "
                f"(HTTP {response.status}, host {response_host})."
            )
        if board == "linkedin" and linkedin_auth_required(response.url):
            raise ExtractionError(
                "LinkedIn authentication is required. Run this command once with --headed, "
                "sign in in the dedicated browser window, and retry."
            )
        try:
            actual_url, _ = canonical_url(response.url)
        except ExtractionError as error:
            raise ExtractionError(f"{board.title()} redirected away from the requested job.") from error
        if actual_url != canonical:
            raise ExtractionError(f"{board.title()} redirected to a different job; needs review.")
        return response.body


def scrape_job(url, state_dir=ROOT / ".scrapling", output_dir=ROOT / "data", headed=False, html_path=None):
    """Fetch, validate, adaptively extract, and save JSON; return its path and data."""
    canonical_url(url)
    html = Path(html_path).read_bytes() if html_path else fetch(url, state_dir, headed)
    result = extract(html, url, state_dir)
    result["extraction"]["source"] = "captured_html" if html_path else "live_scrapling_browser"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result['extraction']['board']}-{result['job_id']}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--html", type=Path, help="Parse an already captured rendered job DOM (no network).")
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".scrapling")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data")
    parser.add_argument(
        "--headed", action="store_true",
        help="Show the dedicated browser (required for first-time LinkedIn sign-in).",
    )
    args = parser.parse_args()
    try:
        path, result = scrape_job(args.url, args.state_dir, args.output_dir, args.headed, args.html)
        print(json.dumps({"status": "ok", "output": str(path), "title": result["title"], "extraction": result["extraction"]}, indent=2))
        return 0
    except (ExtractionError, OSError) as exc:
        print(json.dumps({"status": "needs_review", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
