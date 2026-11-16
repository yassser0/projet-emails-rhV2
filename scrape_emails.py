#!/usr/bin/env python3
"""
=============================================================================
SCRAPE_EMAILS.PY - Enterprise-Grade Moroccan Tech & HR Email Scraper
=============================================================================
Specialized crawler for extracting Human Resources, Talent Acquisition,
and Tech Leadership emails from Moroccan IT companies, startups, and ESNs.

Key Features:
- Multi-vector email extractor (DOM, text, JSON-LD, Cloudflare, obfuscations).
- Heuristic priority-based crawler (targets career/job/team pages first).
- Stealth browser simulation (realistic headers, anti-block measures).
- DNS MX validation with thread-safe caching.
- Moroccan HR classification (RH, FOUNDER_TECH, GENERIC, OTHER).
- Resilience: SSL fallback, safe encoding, atomic CSV streaming.
- Optional headless browser pass (--js) for React/Next.js/Angular SPAs.
"""

from __future__ import annotations

import argparse
import csv
import html as htmllib
import json
import logging
import os
import re
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Lock
from typing import Any
from urllib.parse import unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

warnings.filterwarnings("ignore")

import dns.resolver
import requests
import urllib3
from bs4 import BeautifulSoup
from email_validator import EmailNotValidError, validate_email
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", message=".*urllib3.*")

# ---------------------------------------------------------------------------
# Global Configuration & Thresholds
# ---------------------------------------------------------------------------
INPUT_FILES = ["companies.csv", "companies_manual.csv"]
OUTPUT_FILE = "emails.csv"
REPORT_FILE = "sites_report.csv"
PROCESSED_FILE = "processed_domains.txt"

DEFAULT_WORKERS = 8
DEFAULT_DELAY = 0.5
DEFAULT_TIMEOUT = 15
DEFAULT_MAX_PAGES = 20
MAX_RESPONSE_BYTES = 3_000_000  # 3 MB max per page

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# ---------------------------------------------------------------------------
# Link Scoring & Prioritization
# ---------------------------------------------------------------------------
LINK_KEYWORDS: dict[str, int] = {
    # Recruitment & Careers (Highest Priority)
    "recrut": 8, "career": 8, "carri": 8, "emploi": 7, "job": 7,
    "candidat": 7, "postul": 7, "talent": 7, "hiring": 6, "rejoin": 6,
    "join": 5, "apply": 6, "work-with": 5, "offre": 5, "stage": 5,
    "rh": 8, "hr": 8, "drh": 8, "ressources-humaines": 8, "human-resources": 8,
    "people": 6, "staffing": 5, "candidature": 7,

    # Team & Management (Crucial for Startups)
    "equipe": 4, "team": 4, "notre-equipe": 5, "founders": 5,
    "fondateurs": 5, "management": 4, "leadership": 4,

    # Contact & About
    "contact": 5, "contactez": 5, "nous-contacter": 5, "contact-us": 5,
    "propos": 3, "about": 3, "qui-sommes-nous": 3,

    # Legal & Compliance (Often contains DPO or official email)
    "mention": 2, "legal": 2, "politique": 2, "privacy": 2,
}

PRIORITY_PATHS = [
    "/recrutement", "/recrutement-maroc", "/carrieres", "/careers", "/jobs",
    "/emploi", "/rejoignez-nous", "/join-us", "/join", "/travailler-chez-nous",
    "/candidature", "/candidature-spontanee", "/postuler", "/rh",
    "/ressources-humaines", "/talents", "/talent", "/people", "/stages",
    "/equipe", "/team", "/notre-equipe", "/founders",
    "/contact", "/contactez-nous", "/nous-contacter", "/contact-us",
    "/a-propos", "/qui-sommes-nous", "/about", "/mentions-legales",
]

SKIP_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".zip",
    ".rar", ".tar", ".gz", ".doc", ".docx", ".xls", ".xlsx", ".ppt",
    ".pptx", ".mp4", ".mp3", ".avi", ".css", ".js", ".ico", ".woff",
    ".woff2", ".ttf", ".eot", ".xml", ".json",
)

# ---------------------------------------------------------------------------
# Classification Dictionaries
# ---------------------------------------------------------------------------
HR_TOKENS = {
    "rh", "hr", "drh", "dhr", "hrd", "grh", "recrutement", "recruitment",
    "careers", "career", "carrieres", "carriere", "jobs", "job", "emploi",
    "emplois", "talent", "talents", "candidat", "candidats", "candidature",
    "candidatures", "stages", "stage", "internship", "hiring", "people",
    "staffing", "headhunting", "drh-maroc", "rh-maroc", "recrutement-maroc",
}

HR_SUBSTRINGS = (
    "recrut", "career", "carri", "emploi", "talent", "candidat", "hiring",
    "rejoindre", "ressourceshumaine", "humanresource", "staffing", "onboard",
    "headhunter", "chargerecrutement", "responsablerecrutement", "drh",
)

FOUNDER_TOKENS = {
    "ceo", "cto", "coo", "cfo", "founder", "founders", "fondateur",
    "fondateurs", "cofounder", "co-founder", "direction", "dg", "president",
    "dirigeant", "gerant", "management",
}

GENERIC_TOKENS = {
    "contact", "info", "hello", "bonjour", "accueil", "office", "secretariat",
    "commercial", "sales", "mail", "communication", "marketing", "partenariat",
    "presse", "corporate", "bonjour", "inquiries",
}

BAD_LOCAL_PARTS = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "abuse", "postmaster",
    "privacy", "dpo", "unsubscribe", "mailer-daemon", "webmaster", "root",
    "wordpress", "admin", "administrator", "billing", "facturation", "support",
    "help", "sav", "service-client", "hostmaster", "security", "signalement",
}

PLACEHOLDER_DOMAINS = {
    "example.com", "example.org", "email.com", "domain.com", "domaine.com",
    "yourdomain.com", "mondomaine.com", "votredomaine.com", "w3.org",
    "schema.org", "test.com", "sentry.io", "wixpress.com", "cloudflare.com",
}

FREE_MAIL_PROVIDERS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.fr", "hotmail.com",
    "hotmail.fr", "outlook.com", "outlook.fr", "live.com", "live.fr",
    "icloud.com", "protonmail.com", "proton.me", "aol.com", "gmx.com",
}

GENERIC_TLD_PARTS = {
    "www", "com", "ma", "org", "net", "co", "fr", "eu", "gov", "ac",
    "info", "biz", "group", "groupe", "maroc", "morocco", "global",
    "consulting", "tech", "it", "solutions", "services", "digital",
}

# ---------------------------------------------------------------------------
# Regex Patterns
# ---------------------------------------------------------------------------
EMAIL_REGEX = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)
OBFUSCATED_AT = re.compile(
    r"\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|<\s*at\s*>|\s+at\s+|\s+arobase\s+|\[\s*@\s*\]|\(\s*@\s*\))\s*",
    re.IGNORECASE,
)
OBFUSCATED_DOT = re.compile(
    r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|<\s*dot\s*>|\s+dot\s+|\s+point\s+|\[\s*\.\s*\]|\(\s*\.\s*\))\s*",
    re.IGNORECASE,
)
JS_EMAIL_CONCAT = re.compile(
    r"['\"]([A-Za-z0-9._%+\-]+)['\"]\s*\+\s*['\"]@['\"]\s*\+\s*['\"]([A-Za-z0-9.\-]+\.[A-Za-z]{2,})['\"]",
    re.IGNORECASE,
)

# DNS Cache
dns_cache: dict[str, bool] = {}
dns_lock = Lock()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ScrapeEmails")

# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------
def get_domain(url: str) -> str:
    """Extract clean domain without port or 'www.'."""
    if not url:
        return ""
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def create_browser_session() -> requests.Session:
    """Create a persistent requests session with browser-like headers and connection pool."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENTS[0],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Connection": "keep-alive",
    })
    retries = Retry(
        total=2,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
    )
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=30, max_retries=retries)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def safe_http_get(session: requests.Session, url: str, timeout: int = DEFAULT_TIMEOUT) -> requests.Response | None:
    """Execute GET request with automatic SSL verification fallback."""
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        return r
    except requests.exceptions.SSLError:
        try:
            return session.get(url, timeout=timeout, verify=False, allow_redirects=True)
        except requests.RequestException:
            return None
    except requests.RequestException:
        return None


def decode_html_response(response: requests.Response) -> str:
    """Accurately decode response body considering declared and detected encodings."""
    if not response.content:
        return ""
    if response.encoding is None or response.encoding.lower() in ("iso-8859-1", "ascii"):
        response.encoding = response.apparent_encoding or "utf-8"
    try:
        return response.text
    except Exception:
        return response.content.decode("utf-8", errors="replace")


def is_same_or_subdomain(target_domain: str, allowed_domains: list[str]) -> bool:
    """Check if target domain belongs to the primary company domain or subdomains."""
    for base in allowed_domains:
        if target_domain == base or target_domain.endswith("." + base) or base.endswith("." + target_domain):
            return True
    return False


def domain_significant_tokens(domain: str) -> set[str]:
    """Break domain into semantic tokens (e.g. 'capgemini-engineering' -> {'capgemini', 'engineering'})."""
    parts = re.split(r"[.\-_]", domain.lower())
    return {p for p in parts if len(p) >= 3 and p not in GENERIC_TLD_PARTS}


def assess_domain_relation(email_domain: str, site_domains: list[str]) -> str | None:
    """Evaluate relationship between email domain and scanned website domains."""
    clean_email_d = email_domain.lower()
    if is_same_or_subdomain(clean_email_d, site_domains):
        return "SAME"
    email_tokens = domain_significant_tokens(clean_email_d)
    for site_d in site_domains:
        if email_tokens & domain_significant_tokens(site_d):
            return "RELATED"
    return None


# ---------------------------------------------------------------------------
# Email Deobfuscation & Extraction Engines
# ---------------------------------------------------------------------------
def decode_cloudflare_email(hex_encoded: str) -> str:
    """Decode Cloudflare's email obfuscation hex cipher."""
    try:
        key = int(hex_encoded[:2], 16)
        return "".join(
            chr(int(hex_encoded[i:i + 2], 16) ^ key)
            for i in range(2, len(hex_encoded), 2)
        )
    except Exception:
        return ""


def clean_obfuscated_text(text: str) -> str:
    """Convert common obfuscations [at], (dot), HTML entities, etc. into standard form."""
    if not text:
        return ""
    text = htmllib.unescape(text)
    text = text.replace("\\u0040", "@").replace("%40", "@")
    text = text.replace("&commat;", "@").replace("&period;", ".")
    text = OBFUSCATED_AT.sub("@", text)
    text = OBFUSCATED_DOT.sub(".", text)
    return text


def extract_emails_from_page(html: str) -> set[str]:
    """Extract all valid emails using DOM tags, attributes, text, Cloudflare, and JS patterns."""
    if not html:
        return set()

    found_emails: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")

    # 1. Standard mailto links & Cloudflare protection
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        low_href = href.lower()
        if low_href.startswith("mailto:"):
            raw_target = unquote(href[7:].split("?")[0])
            cleaned = clean_obfuscated_text(raw_target)
            found_emails.update(EMAIL_REGEX.findall(cleaned))
        elif "/cdn-cgi/l/email-protection#" in low_href:
            cf_hash = href.split("#")[-1]
            decoded = decode_cloudflare_email(cf_hash)
            if decoded:
                found_emails.update(EMAIL_REGEX.findall(decoded))

    # 2. Cloudflare data tags
    for tag in soup.select("[data-cfemail]"):
        cf_hex = tag.get("data-cfemail", "")
        decoded = decode_cloudflare_email(cf_hex)
        if decoded:
            found_emails.update(EMAIL_REGEX.findall(decoded))

    # 3. Data attributes commonly storing contact info
    for attr in ("data-email", "data-contact", "data-mailto", "data-address"):
        for tag in soup.find_all(attrs={attr: True}):
            val = tag.get(attr, "")
            found_emails.update(EMAIL_REGEX.findall(clean_obfuscated_text(val)))

    # 4. JSON-LD Structured Data (JobPosting & Organization often contain HR emails)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            dumped = json.dumps(data)
            found_emails.update(EMAIL_REGEX.findall(clean_obfuscated_text(dumped)))
        except Exception:
            continue

    # 5. JavaScript string concatenation patterns
    for script in soup.find_all("script"):
        content = script.string or ""
        for match in JS_EMAIL_CONCAT.findall(content):
            found_emails.add(f"{match[0]}@{match[1]}")

    # 6. Rendered Text & Full Raw HTML
    page_text = clean_obfuscated_text(soup.get_text(" "))
    found_emails.update(EMAIL_REGEX.findall(page_text))
    found_emails.update(EMAIL_REGEX.findall(clean_obfuscated_text(html)))

    # Post-process: clean punctuation and convert to lowercase
    normalized = set()
    for email in found_emails:
        e = email.lower().strip(" \t\r\n<>[](){}.,;:'\"")
        if "@" in e and not e.endswith(SKIP_EXTENSIONS):
            normalized.add(e)
    return normalized


def is_valid_email_syntax(email: str) -> bool:
    """Validate RFC email syntax."""
    try:
        validate_email(email, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False


def verify_mx_record(domain: str) -> bool:
    """Check if domain has active MX or fallback A DNS records (thread-safe cache)."""
    with dns_lock:
        if domain in dns_cache:
            return dns_cache[domain]

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 4.0
    resolver.timeout = 4.0
    is_valid = False

    for r_type in ("MX", "A"):
        try:
            resolver.resolve(domain, r_type)
            is_valid = True
            break
        except Exception:
            continue

    with dns_lock:
        dns_cache[domain] = is_valid
    return is_valid


def filter_and_validate_email(email: str, site_domains: list[str]) -> str | None:
    """Verify validity, discard spam traps/placeholders, and assess domain relationship."""
    if not email or len(email) > 90 or "@" not in email:
        return None

    local_part, _, domain = email.partition("@")
    if not local_part or not domain or len(local_part) > 64:
        return None

    # Discard non-human and spam addresses
    if local_part.lower() in BAD_LOCAL_PARTS:
        return None
    if any(local_part.lower().startswith(b) for b in ("noreply", "no-reply", "donotreply")):
        return None

    # Discard placeholder domains and services
    if domain in PLACEHOLDER_DOMAINS or domain in FREE_MAIL_PROVIDERS:
        return None

    if not is_valid_email_syntax(email):
        return None

    return assess_domain_relation(domain, site_domains)


def classify_email_role(email: str) -> str:
    """Classify email into RH, FOUNDER_TECH, GENERIC, or OTHER with Moroccan intelligence."""
    local_part = email.split("@")[0].lower()
    tokens = set(re.split(r"[._\-+0-9]+", local_part))

    # Priority 1: Direct Human Resources & Recruitment
    if tokens & HR_TOKENS or any(sub in local_part for sub in HR_SUBSTRINGS):
        return "RH"

    # Priority 2: Founders & C-Level (Valuable for Startups)
    if tokens & FOUNDER_TOKENS:
        return "FOUNDER_TECH"

    # Priority 3: Generic Corporate Mailboxes
    if tokens & GENERIC_TOKENS:
        return "GENERIC"

    return "OTHER"


# ---------------------------------------------------------------------------
# Crawler Implementation
# ---------------------------------------------------------------------------
class SiteCrawler:
    """Intelligent priority-based crawler for a single corporate site."""

    def __init__(self, website: str, session: requests.Session):
        self.session = session
        self.website = website
        self.primary_domain = get_domain(website)
        self.site_domains = [self.primary_domain]
        self.robot_parser: RobotFileParser | None = None
        self.sitemaps: list[str] = []

    def score_link(self, href: str, text: str) -> int:
        """Score candidate link by keywords."""
        combined = (unquote(href) + " " + text).lower()
        score = sum(weight for kw, weight in LINK_KEYWORDS.items() if kw in combined)
        return score

    def discover_links(self, base_url: str, html: str) -> dict[str, int]:
        """Discover and score in-domain links."""
        soup = BeautifulSoup(html, "html.parser")
        scored_links: dict[str, int] = {}

        for a in soup.find_all("a", href=True):
            raw_href = a["href"].strip()
            if raw_href.lower().startswith(("mailto:", "tel:", "javascript:", "#")):
                continue

            resolved_url = urljoin(base_url, raw_href).split("#")[0]
            parsed = urlparse(resolved_url)
            if parsed.scheme not in ("http", "https") or parsed.path.lower().endswith(SKIP_EXTENSIONS):
                continue

            target_domain = get_domain(resolved_url)
            if not is_same_or_subdomain(target_domain, self.site_domains):
                continue

            link_score = self.score_link(raw_href, a.get_text(" ", strip=True))
            if link_score > 0:
                scored_links[resolved_url] = max(scored_links.get(resolved_url, 0), link_score)

        return scored_links

    def load_robots_and_sitemaps(self, base_origin: str) -> None:
        """Parse robots.txt to discover sitemaps and disallow directives."""
        try:
            r = safe_http_get(self.session, base_origin + "/robots.txt", timeout=6)
            if r and r.status_code == 200 and "<html" not in r.text[:200].lower():
                rp = RobotFileParser()
                rp.parse(r.text.splitlines())
                self.robot_parser = rp
                self.sitemaps = rp.site_maps() or []
        except Exception:
            pass

    def is_fetch_allowed(self, url: str) -> bool:
        """Check if URL fetch is allowed by robots.txt."""
        return self.robot_parser is None or self.robot_parser.can_fetch("*", url)

    def fetch_url(self, url: str) -> tuple[str | None, str]:
        """Fetch page content with safety limits."""
        if not self.is_fetch_allowed(url):
            return None, url
        r = safe_http_get(self.session, url)
        if not r or r.status_code != 200:
            return None, url

        content_type = r.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            return None, r.url

        html_text = decode_html_response(r)
        return html_text[:MAX_RESPONSE_BYTES], r.url

    def open_homepage(self) -> tuple[str | None, str | None]:
        """Open initial homepage checking both HTTPS and HTTP variants."""
        host = self.primary_domain
        candidate_urls = [
            self.website,
            f"https://{host}",
            f"https://www.{host}",
            f"http://{host}",
        ]
        for url in candidate_urls:
            r = safe_http_get(self.session, url, timeout=10)
            if r and r.status_code == 200:
                final_url = r.url
                p = urlparse(final_url)
                origin = f"{p.scheme}://{p.netloc}"
                self.load_robots_and_sitemaps(origin)

                final_domain = get_domain(final_url)
                if final_domain and final_domain not in self.site_domains:
                    self.site_domains.append(final_domain)

                html = decode_html_response(r)[:MAX_RESPONSE_BYTES]
                return final_url, html

        return None, None

    def crawl(self, start_url: str, start_html: str, max_pages: int = DEFAULT_MAX_PAGES, delay: float = DEFAULT_DELAY) -> list[tuple[str, str]]:
        """Perform heuristic-driven breadth-first exploration."""
        p = urlparse(start_url)
        origin = f"{p.scheme}://{p.netloc}"

        visited_urls: set[str] = {start_url.rstrip("/")}
        pages_collected: list[tuple[str, str]] = [(start_url, start_html)]
        queue: dict[str, int] = {}

        def enqueue(target_url: str, score: int):
            norm = target_url.rstrip("/")
            if norm not in visited_urls:
                queue[norm] = max(queue.get(norm, 0), score)

        # 1. Discover links on homepage
        for link_url, score in self.discover_links(start_url, start_html).items():
            enqueue(link_url, score)

        # 2. Inject priority fallback paths (e.g. /recrutement, /carrieres, /contact)
        for path in PRIORITY_PATHS:
            enqueue(origin + path, 4)

        # 3. Explore sitemap career entries if available
        for sitemap_url in self.sitemaps[:2]:
            try:
                sm_resp = safe_http_get(self.session, sitemap_url, timeout=8)
                if sm_resp and sm_resp.status_code == 200:
                    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sm_resp.text)
                    for loc in locs:
                        s = self.score_link(loc, "")
                        if s >= 4:
                            enqueue(loc, s)
            except Exception:
                continue

        # 4. Crawl prioritized queue
        while queue and len(pages_collected) < max_pages:
            next_url = max(queue, key=queue.get)
            link_score = queue.pop(next_url)
            visited_urls.add(next_url)

            time.sleep(delay)
            html, final_url = self.fetch_url(next_url)
            if not html:
                continue

            pages_collected.append((final_url, html))

            # If page is highly relevant, queue deeper links
            if link_score >= 4:
                for sub_url, sub_score in self.discover_links(final_url, html).items():
                    if sub_score >= 4:
                        enqueue(sub_url, sub_score)

        return pages_collected


# ---------------------------------------------------------------------------
# Worker & Orchestrator
# ---------------------------------------------------------------------------
def process_single_company(website: str, company_name: str, max_pages: int, delay: float) -> dict[str, Any]:
    """Scrape and extract emails for one company website."""
    session = create_browser_session()
    crawler = SiteCrawler(website, session)

    start_url, start_html = crawler.open_homepage()
    if not start_url or not start_html:
        return {
            "status": "UNREACHABLE",
            "pages": 0,
            "emails": {},
            "domains": crawler.site_domains,
        }

    pages = crawler.crawl(start_url, start_html, max_pages=max_pages, delay=delay)
    extracted_emails: dict[str, tuple[str, str]] = {}

    for page_url, page_html in pages:
        candidate_emails = extract_emails_from_page(page_html)
        for email in candidate_emails:
            relation = filter_and_validate_email(email, crawler.site_domains)
            if relation:
                extracted_emails.setdefault(email, (page_url, relation))

    status = "OK" if extracted_emails else "NO_EMAIL"
    return {
        "status": status,
        "pages": len(pages),
        "emails": extracted_emails,
        "domains": crawler.site_domains,
    }


def execute_playwright_pass(unresolved_websites: list[str]) -> dict[str, dict[str, tuple[str, str]]]:
    """Execute dynamic browser rendering with Playwright for SPAs (React/Next.js/Vue)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed. Run 'pip install playwright && python -m playwright install chromium' for --js support.")
        return {}

    logger.info("Starting Playwright pass on %d dynamic sites...", len(unresolved_websites))
    results: dict[str, dict[str, tuple[str, str]]] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENTS[0],
            ignore_https_errors=True,
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        for site_url in unresolved_websites:
            d = get_domain(site_url)
            results[d] = {}
            try:
                page.goto(site_url, timeout=25000, wait_until="networkidle")
                content = page.content()
                emails = extract_emails_from_page(content)
                for e in emails:
                    rel = filter_and_validate_email(e, [d])
                    if rel:
                        results[d][e] = (page.url, rel)
            except Exception as exc:
                logger.debug("Playwright error on %s: %s", site_url, exc)
                continue

        browser.close()
    return results


# ---------------------------------------------------------------------------
# File I/O & Main Routine
# ---------------------------------------------------------------------------
FIELDS = [
    "company_name", "company_domain", "website", "email", "type",
    "domain_match", "source_url", "mx_valid", "found_date", "status"
]
REPORT_FIELDS = ["domain", "website", "status", "pages", "emails", "date"]


def load_input_companies() -> list[tuple[str, str]]:
    """Load target companies and domains from input CSV files."""
    companies = []
    seen_domains = set()

    for filepath in INPUT_FILES:
        if not os.path.exists(filepath):
            continue
        with open(filepath, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                site = (row.get("website") or "").strip()
                if not site:
                    continue
                if not site.startswith("http"):
                    site = "https://" + site

                domain = get_domain(site)
                if not domain or domain in seen_domains:
                    continue
                seen_domains.add(domain)

                name = (row.get("company_name") or row.get("title") or domain.split(".")[0]).strip()
                companies.append((site, name))

    return companies


def main() -> int:
    parser = argparse.ArgumentParser(description="Enterprise Moroccan Tech & HR Email Scraper")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Parallel worker threads")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of companies to scrape (for testing)")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Max pages per company site")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Delay between requests to the same site (seconds)")
    parser.add_argument("--js", action="store_true", help="Run secondary Playwright pass on sites with NO_EMAIL")
    parser.add_argument("--retry-empty", action="store_true", help="Retry companies that returned NO_EMAIL previously")
    args = parser.parse_args()

    companies = load_input_companies()
    if not companies:
        logger.error("No companies found in %s. Run search_companies.py first!", INPUT_FILES)
        return 1

    processed_domains = set()
    if os.path.exists(PROCESSED_FILE) and not args.retry_empty:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            processed_domains = {line.strip() for line in f if line.strip()}

    todo = [(w, n) for w, n in companies if get_domain(w) not in processed_domains]
    if args.limit:
        todo = todo[:args.limit]

    logger.info("Starting scrape: %d companies to process with %d workers (max %d pages/site).", len(todo), args.workers, args.max_pages)

    # Initialize CSV files
    out_exists = os.path.exists(OUTPUT_FILE)
    rep_exists = os.path.exists(REPORT_FILE)

    out_file = open(OUTPUT_FILE, "a", newline="", encoding="utf-8-sig")
    rep_file = open(REPORT_FILE, "a", newline="", encoding="utf-8-sig")
    proc_file = open(PROCESSED_FILE, "a", encoding="utf-8")

    out_writer = csv.DictWriter(out_file, fieldnames=FIELDS)
    rep_writer = csv.DictWriter(rep_file, fieldnames=REPORT_FIELDS)

    if not out_exists:
        out_writer.writeheader()
    if not rep_exists:
        rep_writer.writeheader()

    known_emails: set[str] = set()
    no_email_sites: list[str] = []
    total_emails_found = 0
    total_rh_found = 0

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_company = {
                executor.submit(process_single_company, w, n, args.max_pages, args.delay): (w, n)
                for w, n in todo
            }

            for idx, future in enumerate(as_completed(future_to_company), start=1):
                website, name = future_to_company[future]
                domain = get_domain(website)

                try:
                    res = future.result()
                except Exception as exc:
                    logger.error("[%d/%d] Error crawling %s: %s", idx, len(todo), domain, exc)
                    res = {"status": "ERROR", "pages": 0, "emails": {}, "domains": [domain]}

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                added_site_emails = 0

                for email, (src_url, domain_match) in sorted(res["emails"].items()):
                    if email in known_emails:
                        continue
                    known_emails.add(email)

                    role = classify_email_role(email)
                    if role == "RH":
                        total_rh_found += 1
                    total_emails_found += 1
                    added_site_emails += 1

                    email_domain = email.split("@")[1]
                    mx_ok = verify_mx_record(email_domain)

                    out_writer.writerow({
                        "company_name": name,
                        "company_domain": domain,
                        "website": website,
                        "email": email,
                        "type": role,
                        "domain_match": domain_match,
                        "source_url": src_url,
                        "mx_valid": "TRUE" if mx_ok else "FALSE",
                        "found_date": now_str,
                        "status": "PENDING",
                    })

                rep_writer.writerow({
                    "domain": domain,
                    "website": website,
                    "status": res["status"],
                    "pages": res["pages"],
                    "emails": added_site_emails,
                    "date": now_str,
                })

                out_file.flush()
                rep_file.flush()

                if res["status"] != "ERROR":
                    proc_file.write(domain + "\n")
                    proc_file.flush()

                if res["status"] == "NO_EMAIL":
                    no_email_sites.append(website)

                logger.info(
                    "[%d/%d] %-30s | Status: %-10s | Emails: %2d (Pages: %2d)",
                    idx, len(todo), domain, res["status"], added_site_emails, res["pages"]
                )

        # Optional Playwright dynamic rendering pass
        if args.js and no_email_sites:
            logger.info("Executing secondary Playwright pass on %d sites with NO_EMAIL...", len(no_email_sites))
            js_results = execute_playwright_pass(no_email_sites)
            for site in no_email_sites:
                dom = get_domain(site)
                emails = js_results.get(dom, {})
                for em, (u, match) in emails.items():
                    if em not in known_emails:
                        known_emails.add(em)
                        role = classify_email_role(em)
                        out_writer.writerow({
                            "company_name": dom,
                            "company_domain": dom,
                            "website": site,
                            "email": em,
                            "type": role,
                            "domain_match": match,
                            "source_url": u,
                            "mx_valid": "TRUE" if verify_mx_record(em.split("@")[1]) else "FALSE",
                            "found_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "status": "PENDING",
                        })
                        total_emails_found += 1
                        if role == "RH":
                            total_rh_found += 1
            out_file.flush()

    finally:
        out_file.close()
        rep_file.close()
        proc_file.close()

    logger.info(
        "\nScrape Finished! Total Emails Collected: %d (RH/Recruitment: %d). Output: %s",
        total_emails_found, total_rh_found, OUTPUT_FILE
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.warning("\nProcess interrupted by user. Saved data is safe.")
        sys.exit(130)
