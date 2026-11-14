import csv
import re
import time
import unicodedata
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

SEARXNG_URL = "http://localhost:8080/search"
OUTPUT_FILE = "companies.csv"
PROCESSED_FILE = "processed_domains.txt"
PAGES = 5                    # pages de résultats par recherche
ADD_SITE_MA = True           # lance chaque recherche aussi avec "site:.ma" (plus de sites marocains)
MAX_LIST_PAGES = 40          # nombre max de pages "classement / annuaire" explorées
MAX_LINKS_PER_LIST_PAGE = 80
USER_AGENT = "Mozilla/5.0 (compatible; CandidatureBot/1.0; recherche emploi personnelle)"

# 1) Grandes entreprises : le script cherche leur site officiel au Maroc (liste à compléter)
LARGE_COMPANIES = [
    # ESN / Conseil IT
    "Capgemini", "CGI", "Accenture", "Atos", "Sopra Steria", "NTT Data",
    "Devoteam", "Talan", "SQLI", "Akkodis", "Sofrecom", "Orange Business",
    "Cegedim", "HPS", "M2M Group", "Dataprotect", "Involys",
    "Oracle", "Microsoft", "SAP", "IBM", "Dell Technologies", "Huawei",
    # Nouveaux acteurs Data / IA / Cloud
    "Capgemini Engineering", "Wavestone", "Sia Partners", "Keyrus",
    "Aubay", "Ippon Technologies", "ALTEN", "Altran",
    "Soft Computing", "Micropole", "Dataleon", "DataValue Consulting",
    "Deloitte Digital", "PwC Digital", "KPMG Data", "EY Data",
    "McKinsey Digital", "BCG Gamma",
    # Startups / Scale-ups data marocaines
    "Yassir", "IntelliSense", "Chari", "WafaCash Digital",
    "CashPlus", "Payzone", "NAPS", "CMI Maroc",
    # Télécom, cloud
    "Inwi", "Orange Maroc", "Maroc Telecom",
    # Banques digitales (gros équipes data)
    "Attijariwafa bank", "CIH Bank", "Bank of Africa",
    # BPO tech
    "Teleperformance", "Concentrix", "Intelcia", "Majorel",
    # Nouveaux
    "Viatransit", "Datalake Africa", "Loxiane", "Disruptive Factory",
    "UM6P Ventures", "Fabrique de la Cité Data", "Datashift",
]

# 2) Recherches thématiques : toutes contiennent le Maroc ou une ville marocaine
QUERIES = [
    # --- Data / IA / Cloud ---
    "data engineer Maroc recrutement Casablanca",
    "data scientist emploi Casablanca Maroc",
    "data analyst offre emploi Maroc",
    "machine learning engineer Maroc",
    "cloud architect Casablanca Maroc recrutement",
    "MLOps DevOps data Casablanca Maroc",
    "big data Hadoop Spark Maroc emploi",
    "data platform Casablanca Maroc",
    "intelligence artificielle entreprise Casablanca Maroc",
    "société IA NLP LLM Casablanca Maroc",
    "analytics data Rabat Maroc",
    "data warehouse datalake entreprise Maroc",
    # --- ESN / SSII ---
    "classement ESN Maroc 2024",
    "liste SSII informatiques Casablanca Maroc",
    "membres APEBI entreprises IT Maroc",
    "entreprises Technopark Casablanca liste",
    "entreprises Casanearshore Casablanca",
    "société conseil IT Maroc offre emploi",
    "top cabinets conseil digital Casablanca",
    # --- Villes secondaires ---
    "société informatique data Rabat Maroc recrutement",
    "entreprise data tech Tanger Maroc",
    "société tech Marrakech Maroc",
    "startup tech Agadir Maroc",
    # --- Ciblage RH direct ---
    "recrutement data engineer site:.ma",
    "offre emploi data scientist site:.ma",
    "RH data tech Casablanca Maroc email contact",
    "responsable recrutement tech Maroc email",
    "DRH entreprise informatique Casablanca contact",
    # --- Anciens contextes conservés ---
    "plus grandes entreprises data Casablanca Maroc",
    "top entreprises big data Maroc",
    "société cloud DevOps Casablanca Maroc",
    "centre de services data Casablanca Maroc",
    "banque Maroc data digital recrutement Casablanca",
    "entreprise intelligence artificielle Casablanca Maroc",
    "société business intelligence Casablanca Maroc",
]

# Pages qui ressemblent à un classement ou un annuaire : on en extrait les liens vers les entreprises
LIST_HINTS = re.compile(
    r"\b(top|liste|classement|annuaire|meilleur(?:e)?s?|plus grandes?|"
    r"r[ée]pertoire|directory|members?|adh[ée]rents?)\b", re.I)

# Signaux "Maroc" dans un texte ou une URL
MA_WORDS = re.compile(
    r"\b(maroc|morocco|marocain\w*|moroccan|casablanca|rabat|tanger|tangier|"
    r"marrakech|agadir|f[èé]s|k[ée]nitra|t[ée]touan|oujda|mohammedia|nouaceur|"
    r"technopark|casanearshore)\b", re.I)
MA_PATH = re.compile(r"/(ma|maroc|morocco|ma-fr|ma-en|fr-ma|en-ma)(/|$|-)", re.I)

# Domaines de pays étrangers : exclus sauf s'ils sont validés à la main
FOREIGN_TLDS = (".fr", ".be", ".eu", ".de", ".uk", ".ch", ".nl", ".it", ".es",
                ".ca", ".us", ".gov", ".edu")

BLACKLIST = {
    # réseaux sociaux, emploi, annuaires, outils
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "indeed.com", "glassdoor.com", "wikipedia.org",
    "rekrute.com", "emploi.ma", "dreamjob.ma", "marocannonces.com",
    "anapec.org", "kompass.com", "societe.com", "pagesjaunes.ma",
    "medium.com", "reddit.com", "quora.com", "tiktok.com", "jobrapido.com",
    "talent.com", "jooble.org", "neuvoo.com", "welcometothejungle.com",
    "clutch.co", "goodfirms.co", "sortlist.com", "crunchbase.com",
    "pinterest.com", "scribd.com", "slideshare.net", "github.com",
    "google.com", "bing.com", "apple.com", "amazon.com", "wa.me",
    "whatsapp.com", "t.me", "telegram.org", "wordpress.com", "wix.com",
    "bit.ly", "goo.gl", "play.google.com",
    # bases de données d'entreprises et sites d'avis
    "zoominfo.com", "statista.com", "capterra.com", "g2.com", "trustpilot.com",
    "similarweb.com", "semrush.com", "builtwith.com", "owler.com", "dnb.com",
    "apollo.io", "rocketreach.co", "lusha.com", "cybo.com", "europages.com",
    # presse
    "lesechos.fr", "leparisien.fr", "lemonde.fr", "lefigaro.fr", "bfmtv.com",
    "forbes.com", "bloomberg.com", "reuters.com", "techcrunch.com",
    "h24info.ma", "lematin.ma", "leconomiste.com", "medias24.com",
    "lavieeco.com", "le360.ma", "hespress.com", "telquel.ma", "challenge.ma",
    "lesecos.ma", "bladi.net", "yabiladi.com", "lopinion.ma", "maroc.ma",
}

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "fr,en;q=0.8"})


def get_domain(url):
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def is_blacklisted(domain):
    return any(domain == b or domain.endswith("." + b) for b in BLACKLIST)


def norm(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


def clean_name(title, domain):
    name = re.split(r"\s[-|–—:]\s", title or "")[0].strip()
    if not (2 <= len(name) <= 50):
        name = domain.split(".")[0]
    return name


def looks_moroccan(domain, text):
    """Garde les .ma, et les autres domaines seulement si le texte parle du Maroc."""
    if domain.endswith(".ma"):
        return True
    if domain.endswith(FOREIGN_TLDS):
        return False
    return bool(MA_WORDS.search(text))


def load_processed():
    try:
        with open(PROCESSED_FILE, encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        return set()


def search(query, page=1):
    for attempt in range(3):
        try:
            r = session.get(
                SEARXNG_URL,
                params={"q": query, "format": "json", "pageno": page, "language": "fr"},
                timeout=30,
            )
            r.raise_for_status()
            return r.json().get("results", [])
        except Exception:
            time.sleep(2 * (attempt + 1))
    print(f"   ! recherche impossible : {query} (page {page})")
    return []


def name_matches(name, domain):
    full = norm(name)
    first = norm(name.split()[0])
    d = norm(domain)
    return full in d or (len(first) >= 5 and first in d)


def resolve_company(name):
    """Site officiel marocain d'une entreprise : (domaine, url) ou (None, None)."""
    best = (None, None)
    for res in search(f"{name} Maroc site officiel"):
        url = res.get("url", "")
        domain = get_domain(url)
        if not domain or is_blacklisted(domain) or not name_matches(name, domain):
            continue
        if domain.endswith(".ma"):
            return domain, f"https://{domain}"
        text = " ".join([res.get("title", ""), res.get("content", ""), url])
        if (best[0] is None and not domain.endswith(FOREIGN_TLDS)
                and (MA_PATH.search(urlparse(url).path) or MA_WORDS.search(text))):
            best = (domain, url)   # page marocaine d'un site international
    return best


def links_from_list_page(url, moroccan_page):
    try:
        r = session.get(url, timeout=15)
        if r.status_code != 200 or "html" not in r.headers.get("Content-Type", ""):
            return []
    except requests.RequestException:
        return []
    page_domain = get_domain(url)
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        link = urljoin(url, a["href"])
        if urlparse(link).scheme not in ("http", "https"):
            continue
        d = get_domain(link)
        if not d or d == page_domain or is_blacklisted(d):
            continue
        if d.endswith(".ma") or (moroccan_page and not d.endswith(FOREIGN_TLDS)):
            out.append((d, a.get_text(" ", strip=True)))
    return out[:MAX_LINKS_PER_LIST_PAGE]


def main():
    processed = load_processed()
    companies = {}

    def add(domain, name, source, query, website=None, trusted=False):
        if (not domain or is_blacklisted(domain) or domain in processed
                or domain in companies):
            return False
        companies[domain] = {
            "domain": domain,
            "website": website or f"https://{domain}",
            "company_name": name,
            "is_ma": domain.endswith(".ma"),
            "trusted": "1" if trusted else "0",
            "source": source,
            "query": query,
        }
        return True

    print("=== 1/3 Sites officiels des grandes entreprises au Maroc ===")
    for name in LARGE_COMPANIES:
        domain, url = resolve_company(name)
        if domain and add(domain, name, "grande_entreprise", name, url, trusted=True):
            print(f"   {name:28} -> {url}")
        elif not domain:
            print(f"   {name:28} -> site marocain non trouvé (à ajouter à la main si besoin)")
        time.sleep(1.5)

    print("\n=== 2/3 Recherches thématiques ===")
    list_pages = []
    for base in QUERIES:
        for query in ([base, base + " site:.ma"] if ADD_SITE_MA else [base]):
            for page in range(1, PAGES + 1):
                for res in search(query, page):
                    url = res.get("url", "")
                    title = res.get("title", "")
                    domain = get_domain(url)
                    text = f"{title} {res.get('content', '')} {url}"
                    moroccan = looks_moroccan(domain, text)
                    if (LIST_HINTS.search(title + " " + url) and len(list_pages) < MAX_LIST_PAGES
                            and url not in [u for u, _ in list_pages]):
                        list_pages.append((url, moroccan))
                    if moroccan:
                        add(domain, clean_name(title, domain), "recherche", query)
                print(f"   '{query}' p{page} : {len(companies)} sites")
                time.sleep(2)

    print(f"\n=== 3/3 Exploration de {len(list_pages)} classements / annuaires ===")
    for url, moroccan_page in list_pages:
        before = len(companies)
        for domain, text in links_from_list_page(url, moroccan_page):
            name = text if 2 <= len(text) <= 40 else domain.split(".")[0]
            add(domain, name, "liste", url)
        print(f"   {get_domain(url):30} +{len(companies) - before}")
        time.sleep(1.5)

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "domain", "website", "company_name", "is_ma", "trusted", "source", "query"])
        writer.writeheader()
        writer.writerows(companies.values())

    nb_ma = sum(1 for c in companies.values() if c["is_ma"])
    print(f"\nTerminé : {len(companies)} NOUVEAUX sites dans {OUTPUT_FILE} (dont {nb_ma} en .ma)")
    print("Les sites non .ma seront vérifiés par scrape_emails.py (il écarte ceux qui ne sont pas marocains).")


if __name__ == "__main__":
    main()