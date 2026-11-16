#!/usr/bin/env python3
"""
=============================================================================
SEARCH_COMPANIES.PY - Moroccan Tech & Data Company Discovery Engine
=============================================================================
Professional scout for tech companies, startups, ESNs, and data laboratories
operating in Morocco (Casablanca, Rabat, Tanger, Marrakech, Agadir, etc.).

Features:
- Exhaustive curated registry of Moroccan Tech/Data startups & major ESNs.
- High-yield Moroccan search dorks (Data/AI/Cloud/Offshore/HR).
- Robust SearXNG connector with retries and graceful fallback.
- Deep Moroccan geo-validation (TLD .ma, cities, tax/legal & address signals).
- Strict blacklist against job aggregators, directories, and social media.
- Standardized CSV export ready for downstream email scraping.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
import time
import unicodedata
import warnings

# Suppress urllib3 dependency warning before requests loads
warnings.filterwarnings("ignore")

import requests
import urllib3
from bs4 import BeautifulSoup
from typing import Any
from urllib.parse import urljoin, urlparse

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------
DEFAULT_SEARXNG_URL = "http://localhost:8080/search"
OUTPUT_FILE = "companies.csv"
PROCESSED_FILE = "processed_domains.txt"

DEFAULT_PAGES = 5
DEFAULT_MAX_LIST_PAGES = 40
MAX_LINKS_PER_PAGE = 80

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# ---------------------------------------------------------------------------
# 1) Curated Registry: Startups, Scale-ups, ESNs, Consultancies in Morocco
# ---------------------------------------------------------------------------
CURATED_COMPANIES: list[dict[str, str]] = [
    # --- Top ESNs, Software Houses & Tech Consultancies (Morocco Offices) ---
    {"name": "Capgemini Maroc", "query": "Capgemini Maroc recrutement", "category": "ESN"},
    {"name": "CGI Maroc", "query": "CGI Maroc Casablanca Nearshore", "category": "ESN"},
    {"name": "Alten Maroc", "query": "Alten Maroc Fes Casablanca", "category": "ESN"},
    {"name": "Devoteam Maroc", "query": "Devoteam Maroc Casablanca", "category": "ESN"},
    {"name": "Talan Maroc", "query": "Talan Maroc Casablanca Nearshore", "category": "ESN"},
    {"name": "SQLI Maroc", "query": "SQLI Maroc Casablanca Rabat", "category": "ESN"},
    {"name": "Sopra Steria Maroc", "query": "Sopra Steria Maroc Casablanca", "category": "ESN"},
    {"name": "Akkodis Maroc", "query": "Akkodis Maroc Casablanca", "category": "ESN"},
    {"name": "Sofrecom Maroc", "query": "Sofrecom Maroc Orange Group", "category": "ESN"},
    {"name": "Orange Business Maroc", "query": "Orange Business Maroc Casablanca", "category": "Cloud/ESN"},
    {"name": "Inetum Maroc", "query": "Inetum Maroc Casablanca", "category": "ESN"},
    {"name": "DXC Technology Maroc", "query": "DXC Technology Maroc Nearshore", "category": "ESN"},
    {"name": "Leyton Maroc", "query": "Leyton Maroc Casablanca Nearshore", "category": "Consulting"},
    {"name": "HPS", "query": "HPS Hightech Payment Systems Casablanca", "category": "Fintech"},
    {"name": "M2M Group", "query": "M2M Group Casablanca", "category": "Fintech"},
    {"name": "S2M", "query": "S2M Maroc Casablanca", "category": "Fintech"},
    {"name": "Dataprotect", "query": "Dataprotect Casablanca cybersécurité", "category": "Cybersecurity"},
    {"name": "Involys", "query": "Involys Casablanca Maroc", "category": "Software"},
    {"name": "Omnidata", "query": "Omnidata Casablanca", "category": "Data/BI"},
    {"name": "CBI Maroc", "query": "Compagnie Burautique Informatique Maroc CBI", "category": "IT"},
    {"name": "Niji Maroc", "query": "Niji Casablanca Maroc", "category": "Digital"},
    {"name": "Aubay Maroc", "query": "Aubay Maroc Casablanca", "category": "ESN"},
    {"name": "Ippon Technologies Maroc", "query": "Ippon Technologies Casablanca", "category": "Cloud/Data"},
    {"name": "Keyrus Maroc", "query": "Keyrus Maroc Data Analytics", "category": "Data/BI"},
    {"name": "Micropole Maroc", "query": "Micropole Maroc Casablanca", "category": "Data/BI"},
    {"name": "DataValue Consulting Maroc", "query": "DataValue Consulting Casablanca", "category": "Data/BI"},
    {"name": "Wavestone Maroc", "query": "Wavestone Casablanca Maroc", "category": "Consulting"},
    {"name": "Sia Partners Maroc", "query": "Sia Partners Casablanca", "category": "Consulting"},
    {"name": "Deloitte Nearshore", "query": "Deloitte Nearshore Casablanca", "category": "Consulting"},
    {"name": "PwC Digital Maroc", "query": "PwC Digital Services Casablanca", "category": "Consulting"},
    {"name": "EY GDS Maroc", "query": "EY Global Delivery Services Casablanca", "category": "Consulting"},
    {"name": "KPMG Lighthouse Maroc", "query": "KPMG Casablanca Data", "category": "Consulting"},
    {"name": "Thales Maroc", "query": "Thales Maroc Rabat Casablanca", "category": "Defense/Tech"},

    # --- Moroccan Tech Startups & Scale-ups ---
    {"name": "Chari", "query": "Chari.co Casablanca e-commerce B2B", "category": "Startup"},
    {"name": "DabaDoc", "query": "DabaDoc santé Casablanca", "category": "HealthTech"},
    {"name": "Freterium", "query": "Freterium supply chain tech Maroc", "category": "Startup"},
    {"name": "CloudFret", "query": "CloudFret logistique IA Maroc", "category": "Startup"},
    {"name": "YoLa Fresh", "query": "YoLa Fresh Casablanca agri tech", "category": "AgriTech"},
    {"name": "Agenz", "query": "Agenz.ma immobilier data tech Casablanca", "category": "PropTech"},
    {"name": "Kifal Auto", "query": "Kifal Auto Maroc tech", "category": "AutoTech"},
    {"name": "Cathedis", "query": "Cathedis logistique Casablanca", "category": "Logistics"},
    {"name": "Guichet.ma", "query": "Guichet.ma billetterie Casablanca", "category": "Startup"},
    {"name": "Sand to Green", "query": "Sand to Green Maroc climate tech", "category": "ClimateTech"},
    {"name": "Deepecho", "query": "Deepecho IA échographie Casablanca", "category": "MedTech/AI"},
    {"name": "Tookeez", "query": "Tookeez fintech Casablanca", "category": "Fintech"},
    {"name": "VosFactures", "query": "VosFactures.ma facturation SaaS Maroc", "category": "SaaS"},
    {"name": "Payzone Maroc", "query": "Payzone Maroc paiement en ligne", "category": "Fintech"},
    {"name": "Cash Plus Digital", "query": "Cash Plus Casablanca solutions digitales", "category": "Fintech"},
    {"name": "Wafacash Digital", "query": "Wafacash fintech innovation Casablanca", "category": "Fintech"},
    {"name": "Koollys", "query": "Koollys food delivery cloud kitchen Casablanca", "category": "FoodTech"},
    {"name": "Invygo Maroc", "query": "Invygo Maroc location voiture tech", "category": "Mobility"},
    {"name": "Omniup", "query": "Omniup ads WiFi tech Casablanca", "category": "AdTech"},
    {"name": "Kenzup", "query": "Kenzup fidélité Casablanca", "category": "App"},
    {"name": "Farasha Systems", "query": "Farasha Systems drone IA Maroc", "category": "DeepTech"},
    {"name": "ATLAN Space", "query": "ATLAN Space IA drone maritime Maroc", "category": "DeepTech/AI"},

    # --- Cloud, Telecom & Large Tech Hubs ---
    {"name": "Inwi", "query": "Inwi cloud telecom Casablanca", "category": "Telecom/Cloud"},
    {"name": "Orange Maroc", "query": "Orange Maroc Casablanca recrutement", "category": "Telecom"},
    {"name": "Maroc Telecom", "query": "Maroc Telecom Rabat", "category": "Telecom"},
    {"name": "N+ONE Datacenters", "query": "N+ONE Datacenters Casablanca Nouaceur", "category": "Cloud/DataCenter"},
    {"name": "MTDS", "query": "MTDS Rabat internet solutions", "category": "Networks/Cloud"},
    {"name": "Meditel / Orange B2B", "query": "Orange Business Maroc B2B", "category": "Telecom/Cloud"},
]

# ---------------------------------------------------------------------------
# 2) High-Value Thematic Search Queries (Data, AI, Cloud, Software, HR)
# ---------------------------------------------------------------------------
TARGET_QUERIES = [
    # Direct Data & AI focus in Morocco
    'data engineer Maroc recrutement Casablanca',
    'data scientist emploi Casablanca site:.ma',
    'société big data analytics Casablanca Maroc',
    'cabinet intelligence artificielle IA Casablanca Maroc',
    'data platform cloud architecture Maroc Casablanca',
    'machine learning engineer Casablanca site:.ma',
    'société business intelligence BI Casablanca Maroc',
    'centre d expertise data Casablanca Maroc',

    # ESNs & IT Services Companies
    'classement ESN SSII Maroc Casablanca',
    'liste des sociétés informatiques Casanearshore',
    'entreprises Technopark Casablanca informatique',
    'membres APEBI Fédération des technologies Maroc',
    'société de développement informatique Casablanca Maroc',
    'cabinet conseil transformation digitale Casablanca',
    'société cloud DevOps Kubernetes Casablanca Maroc',

    # Regional Tech Hubs in Morocco
    'société informatique data Rabat Technopolis',
    'entreprises tech Tanger Med Nearshore',
    'startup tech incubateur Technopark Marrakech',
    'entreprise informatique Agadir Technopark',
    'centre de services informatique Fes Shore Maroc',

    # High-Yield HR Dorks for Moroccan tech
    'recrutement data engineer site:.ma',
    'recrutement "data scientist" Casablanca site:.ma',
    'recrutement "développeur" Casanearshore site:.ma',
    'recrutement "responsable informatique" Casablanca site:.ma',
    '"rejoignez notre équipe" data Maroc',
    '"nos offres d\'emploi" informatique site:.ma',
    '"candidature spontanée" informatique Casablanca Maroc',
]

# Regex patterns for detecting directories / ranking lists
LIST_HINTS = re.compile(
    r"\b(top|liste|classement|annuaire|meilleur(?:e)?s?|plus grandes?|"
    r"r[ée]pertoire|directory|members?|adh[ée]rents?|catalog(?:ue)?)\b",
    re.IGNORECASE,
)

# Geographic signals for Moroccan affiliation
MA_WORDS = re.compile(
    r"\b(maroc|morocco|marocain\w*|moroccan|casablanca|rabat|tanger|tangier|"
    r"marrakech|agadir|f[èé]s|k[ée]nitra|t[ée]touan|oujda|mohammedia|nouaceur|"
    r"technopark|casanearshore|nearshore|technopolis|fesshore|sidi\s+maarouf|"
    r"benguerir|um6p|1337|apebi|ice\s*\d{6,}|rc\s*casablanca)\b",
    re.IGNORECASE,
)
MA_PATH = re.compile(r"/(ma|maroc|morocco|ma-fr|ma-en|fr-ma|en-ma)(/|$|-)", re.IGNORECASE)

FOREIGN_TLDS = (
    ".fr", ".be", ".eu", ".de", ".uk", ".ch", ".nl", ".it", ".es",
    ".ca", ".us", ".gov", ".edu", ".in", ".ru", ".cn", ".jp",
)

# Aggressive Blacklist: Job boards, directories, socials, government & generic portals
BLACKLIST = {
    # Job boards & recruiting agencies
    "rekrute.com", "emploi.ma", "dreamjob.ma", "marocannonces.com", "anapec.org",
    "indeed.com", "glassdoor.com", "welcometothejungle.com", "jobrapido.com",
    "talent.com", "jooble.org", "neuvoo.com", "monster.com", "michaelpage.ma",
    "stagiaires.ma", "manpower.ma", "adecco.ma", "diorh.com", "novojob.com",
    "mre.ma", "bayt.com", "tanitjobs.com", "marocjob.com",

    # Social Networks & Media Platforms
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "pinterest.com", "quora.com", "reddit.com",
    "medium.com", "github.com", "gitlab.com", "bitbucket.org", "stackoverflow.com",
    "t.me", "telegram.org", "whatsapp.com", "wa.me",

    # Directories & Corporate Aggregators
    "kompass.com", "societe.com", "pagesjaunes.ma", "telecontact.ma", "kerix.net",
    "clutch.co", "goodfirms.co", "sortlist.com", "crunchbase.com", "zoominfo.com",
    "statista.com", "capterra.com", "g2.com", "trustpilot.com", "similarweb.com",
    "semrush.com", "builtwith.com", "owler.com", "dnb.com", "apollo.io",
    "rocketreach.co", "lusha.com", "cybo.com", "europages.com",

    # Press & Media Sites
    "lesechos.fr", "leparisien.fr", "lemonde.fr", "lefigaro.fr", "bfmtv.com",
    "forbes.com", "bloomberg.com", "reuters.com", "techcrunch.com", "h24info.ma",
    "lematin.ma", "leconomiste.com", "medias24.com", "lavieeco.com", "le360.ma",
    "hespress.com", "telquel.ma", "challenge.ma", "lesecos.ma", "bladi.net",
    "yabiladi.com", "lopinion.ma", "maroc.ma", "mapnews.ma", "snrtnews.com",

    # Generic Big Tech platforms & Infrastructure
    "google.com", "google.ma", "bing.com", "apple.com", "amazon.com", "microsoft.com",
    "wordpress.com", "wix.com", "squarespace.com", "shopify.com", "bit.ly", "goo.gl",
    "play.google.com", "wikipedia.org", "w3.org", "schema.org",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("SearchCompanies")


def get_domain(url: str) -> str:
    """Extract clean domain without port or 'www.'."""
    if not url:
        return ""
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def is_blacklisted(domain: str) -> bool:
    """Check if domain or any parent domain is blacklisted."""
    if not domain:
        return True
    return any(domain == b or domain.endswith("." + b) for b in BLACKLIST)


def normalize_text(text: str) -> str:
    """Normalize text removing accents and special characters."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", t.lower())


def clean_company_name(title: str, domain: str) -> str:
    """Deduce clean commercial company name from HTML title or domain."""
    name = re.split(r"\s[-|–—:•»]\s", title or "")[0].strip()
    name = re.sub(r"\b(accueil|home|site officiel|bienvenue|recrutement)\b", "", name, flags=re.I).strip()
    name = re.sub(r"\b(sarl|sa|au|maroc|offshore|group|groupe)\b", "", name, flags=re.I).strip()
    if not (2 <= len(name) <= 50):
        name = domain.split(".")[0].capitalize()
    return name


def looks_moroccan(domain: str, snippet_text: str) -> bool:
    """Confirm whether a domain or page refers to a legitimate Moroccan entity."""
    if domain.endswith(".ma"):
        return True
    if domain.endswith(FOREIGN_TLDS):
        return False
    return bool(MA_WORDS.search(snippet_text))


def create_session() -> requests.Session:
    """Build a robust, browser-mimicking session."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENTS[0],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "DNT": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    })
    return s


session = create_session()


def search_searxng(query: str, page: int = 1, base_url: str = DEFAULT_SEARXNG_URL) -> list[dict[str, Any]]:
    """Query SearXNG JSON API with exponential retries."""
    for attempt in range(3):
        try:
            r = session.get(
                base_url,
                params={"q": query, "format": "json", "pageno": page, "language": "fr"},
                timeout=25,
            )
            if r.status_code == 200:
                data = r.json()
                return data.get("results", [])
            elif r.status_code == 429:
                time.sleep(3 * (attempt + 1))
        except requests.RequestException as exc:
            logger.debug("SearXNG request failed: %s (attempt %d)", exc, attempt + 1)
            time.sleep(2 * (attempt + 1))
    return []


def resolve_official_site(company: dict[str, str], searxng_url: str) -> tuple[str | None, str | None]:
    """Find the verified Moroccan website for a given company name."""
    name = company["name"]
    query = company.get("query", f"{name} Maroc site officiel")

    best_domain: str | None = None
    best_url: str | None = None

    results = search_searxng(query, page=1, base_url=searxng_url)
    norm_name = normalize_text(name.replace("Maroc", ""))

    for item in results:
        url = item.get("url", "")
        domain = get_domain(url)
        if not domain or is_blacklisted(domain):
            continue

        norm_domain = normalize_text(domain)
        # Check domain name matches target company
        if norm_name[:5] not in norm_domain and norm_domain not in norm_name:
            continue

        # If it's a direct .ma domain, top choice
        if domain.endswith(".ma"):
            return domain, f"https://{domain}"

        # Otherwise check Moroccan path or Moroccan keywords in snippet
        text = f"{item.get('title', '')} {item.get('content', '')} {url}"
        if MA_PATH.search(urlparse(url).path) or MA_WORDS.search(text):
            if not best_domain and not domain.endswith(FOREIGN_TLDS):
                best_domain = domain
                best_url = url

    return best_domain, best_url


def extract_links_from_directory(url: str, is_moroccan_context: bool) -> list[tuple[str, str]]:
    """Extract candidate external corporate links from a directory or ranking page."""
    try:
        r = session.get(url, timeout=15, verify=False)
        if r.status_code != 200 or "text/html" not in r.headers.get("Content-Type", ""):
            return []
    except requests.RequestException:
        return []

    parent_domain = get_domain(url)
    soup = BeautifulSoup(r.text, "html.parser")
    found_links: list[tuple[str, str]] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full_url = urljoin(url, href)
        parsed = urlparse(full_url)
        if parsed.scheme not in ("http", "https"):
            continue

        target_domain = get_domain(full_url)
        if not target_domain or target_domain == parent_domain or is_blacklisted(target_domain):
            continue

        # Keep only .ma or sites mentioned inside Moroccan context without foreign country TLD
        if target_domain.endswith(".ma") or (is_moroccan_context and not target_domain.endswith(FOREIGN_TLDS)):
            text = a.get_text(" ", strip=True)
            found_links.append((target_domain, text))

        if len(found_links) >= MAX_LINKS_PER_PAGE:
            break

    return found_links


def load_processed_domains(filepath: str = PROCESSED_FILE) -> set[str]:
    """Load previously processed domains to avoid repeated queries."""
    if not os.path.exists(filepath):
        return set()
    with open(filepath, "r", encoding="utf-8") as f:
        return {line.strip().lower() for line in f if line.strip()}


# ---------------------------------------------------------------------------
# Main Execution Pipeline
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Professional Moroccan Tech & Data Company Scout")
    parser.add_argument("--searxng", default=DEFAULT_SEARXNG_URL, help="SearXNG endpoint URL")
    parser.add_argument("--output", default=OUTPUT_FILE, help="Output CSV path (default: companies.csv)")
    parser.add_argument("--pages", type=int, default=DEFAULT_PAGES, help="Search result pages per query")
    parser.add_argument("--max-list-pages", type=int, default=DEFAULT_MAX_LIST_PAGES, help="Max directory pages to crawl")
    parser.add_argument("--skip-curated", action="store_true", help="Skip the curated registry pass")
    args = parser.parse_args()

    processed_domains = load_processed_domains()
    discovered_companies: dict[str, dict[str, str]] = {}

    def register_company(
        domain: str,
        name: str,
        source: str,
        query: str,
        website: str | None = None,
        category: str = "Tech/Data",
        trusted: bool = False,
    ) -> bool:
        clean_d = domain.lower().strip()
        if not clean_d or is_blacklisted(clean_d) or clean_d in processed_domains or clean_d in discovered_companies:
            return False

        discovered_companies[clean_d] = {
            "domain": clean_d,
            "website": website or f"https://{clean_d}",
            "company_name": name,
            "category": category,
            "is_ma": "1" if clean_d.endswith(".ma") else "0",
            "trusted": "1" if trusted else "0",
            "source": source,
            "query": query,
        }
        return True

    logger.info("=== STEP 1/3: Resolving Curated Moroccan Tech & Startup Registry ===")
    if not args.skip_curated:
        for idx, entry in enumerate(CURATED_COMPANIES, start=1):
            comp_name = entry["name"]
            category = entry.get("category", "Tech")
            domain, official_url = resolve_official_site(entry, args.searxng)

            if domain:
                registered = register_company(
                    domain=domain,
                    name=comp_name,
                    source="curated_registry",
                    query=entry.get("query", comp_name),
                    website=official_url,
                    category=category,
                    trusted=True,
                )
                if registered:
                    logger.info("[%d/%d] Found: %-25s -> %s", idx, len(CURATED_COMPANIES), comp_name, official_url or domain)
            else:
                logger.debug("[%d/%d] Could not resolve site for: %s", idx, len(CURATED_COMPANIES), comp_name)
            time.sleep(0.4)
    else:
        logger.info("Curated registry pass skipped via flag.")

    logger.info("\n=== STEP 2/3: Executing Strategic Thematic Queries ===")
    directory_queue: list[tuple[str, bool]] = []
    seen_directory_urls: set[str] = set()

    for q_idx, base_query in enumerate(TARGET_QUERIES, start=1):
        query_variants = [base_query]
        # Append site:.ma variant if not already present
        if "site:.ma" not in base_query:
            query_variants.append(f"{base_query} site:.ma")

        for query in query_variants:
            for page in range(1, args.pages + 1):
                results = search_searxng(query, page=page, base_url=args.searxng)
                if not results:
                    break

                for item in results:
                    url = item.get("url", "")
                    title = item.get("title", "")
                    domain = get_domain(url)
                    text_blob = f"{title} {item.get('content', '')} {url}"

                    if not domain or is_blacklisted(domain):
                        continue

                    is_ma_valid = looks_moroccan(domain, text_blob)

                    # Check if this result is a directory or list of companies
                    if LIST_HINTS.search(title + " " + url) and len(directory_queue) < args.max_list_pages:
                        if url not in seen_directory_urls:
                            seen_directory_urls.add(url)
                            directory_queue.append((url, is_ma_valid))

                    if is_ma_valid:
                        c_name = clean_company_name(title, domain)
                        register_company(
                            domain=domain,
                            name=c_name,
                            source="search_dork",
                            query=query,
                            category="Tech/Data",
                            trusted=False,
                        )

                time.sleep(0.6)
        logger.info("Query [%d/%d] '%s' -> Total unique companies: %d", q_idx, len(TARGET_QUERIES), base_query, len(discovered_companies))

    logger.info("\n=== STEP 3/3: Mining %d High-Relevance Directories / Lists ===", len(directory_queue))
    for dir_url, is_ma_context in directory_queue:
        dir_domain = get_domain(dir_url)
        candidates = extract_links_from_directory(dir_url, is_moroccan_context=is_ma_context)
        added_count = 0
        for cand_domain, link_text in candidates:
            c_name = link_text if (2 <= len(link_text) <= 40) else cand_domain.split(".")[0].capitalize()
            if register_company(
                domain=cand_domain,
                name=c_name,
                source="directory_mining",
                query=dir_url,
                category="DirectoryLink",
                trusted=False,
            ):
                added_count += 1
        if added_count > 0:
            logger.info("Mined: %-30s -> +%d new companies", dir_domain, added_count)
        time.sleep(0.5)

    # ---------------------------------------------------------------------------
    # Save Results
    # ---------------------------------------------------------------------------
    fieldnames = [
        "domain", "website", "company_name", "category",
        "is_ma", "trusted", "source", "query"
    ]
    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(discovered_companies.values())

    ma_count = sum(1 for c in discovered_companies.values() if c["is_ma"] == "1")
    logger.info("\nSUCCESS: Saved %d Moroccan tech companies to '%s' (including %d directly on .ma)", len(discovered_companies), args.output, ma_count)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.warning("\nExecution stopped by user.")
        sys.exit(130)
