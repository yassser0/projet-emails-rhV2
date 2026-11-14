import argparse
import csv
import html as htmllib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlparse, unquote
from urllib.robotparser import RobotFileParser

import dns.resolver
import requests
import urllib3
from bs4 import BeautifulSoup
from email_validator import validate_email, EmailNotValidError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

INPUT_FILES = ["companies.csv", "companies_manual.csv"]
OUTPUT_FILE = "emails.csv"
REPORT_FILE = "sites_report.csv"
PROCESSED_FILE = "processed_domains.txt"

WORKERS = 8            # sites traités en parallèle
DELAY = 0.6            # pause (secondes) entre deux pages d'un même site
TIMEOUT = 15
MAX_PAGES = 20         # pages visitées par site
USER_AGENT = "Mozilla/5.0 (compatible; CandidatureBot/1.0; recherche emploi personnelle)"
ROBOT_UA = "CandidatureBot"

# Mots qui rendent un lien intéressant (score)
KEYWORDS = {
    "contact": 5, "contactez": 5, "nous-contacter": 5,
    "recrut": 7, "career": 7, "carri": 7, "candidat": 6, "postul": 6,
    "job": 5, "emploi": 5, "rejoin": 5, "join": 4, "talent": 5, "apply": 5,
    "work-with": 4, "offre": 4, "mention": 3, "legal": 2,
    "equipe": 3, "team": 3, "propos": 2, "about": 2,
    "rh": 7, "hr": 7, "ressources-humaines": 7, "human-resources": 7,
    "drh": 7, "staffing": 5, "onboard": 4,
}
FALLBACK_PATHS = [
    "/contact", "/contact-us", "/contactez-nous", "/nous-contacter",
    "/careers", "/carrieres", "/recrutement", "/jobs", "/join-us",
    "/rejoignez-nous", "/about", "/a-propos", "/mentions-legales",
    "/equipe", "/team", "/notre-equipe", "/qui-sommes-nous",
    "/ressources-humaines", "/rh", "/offres-emploi", "/offres",
    "/postuler", "/apply", "/talent", "/talents",
]
SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".zip",
            ".rar", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".mp4",
            ".mp3", ".css", ".js", ".ico", ".xml")

HR_TOKENS = {"rh", "hr", "drh", "dhr", "hrd", "grh", "humanresources"}
HR_WORDS = (
    "recrut", "career", "carri", "job", "emploi", "talent", "candidat",
    "ressourceshumaines", "humanresources", "hiring", "rejoindre",
    "recruitment", "acquire", "staffing", "onboard", "headhunter",
    "chefferecrutement", "responsablerecrutement", "chargederecrutement",
)
GENERIC_WORDS = ("contact", "info", "hello", "bonjour", "accueil", "office",
                 "secretariat", "direction", "administration", "commercial",
                 "sales", "mail", "communication", "marketing", "partenariat")
BAD_PREFIXES = ("noreply", "no-reply", "donotreply", "do-not-reply", "abuse",
                "postmaster", "privacy", "dpo", "unsubscribe", "mailer-daemon",
                "webmaster", "root", "wordpress")
BAD_WORDS = ("signalement", "investor", "press", "compliance", "ethic",
             "legal", "billing", "facturation", "support", "alerte")
BAD_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js",
                ".woff", ".woff2", ".ttf", ".ico", ".map")
FREE_MAIL = {"gmail", "googlemail", "yahoo", "hotmail", "outlook", "live",
             "icloud", "protonmail", "proton", "aol", "msn", "gmx", "yandex"}
PLACEHOLDER_DOMAINS = {"example.com", "example.org", "email.com", "domain.com",
                       "domaine.com", "yourdomain.com", "mondomaine.com",
                       "votredomaine.com", "w3.org", "schema.org", "test.com"}
PLACEHOLDER_PARTS = ("sentry", "wixpress", "yoursite", "votresite", "godaddy")
PLACEHOLDER_LOCALS = {"name", "nom", "email", "your.email", "votre.email",
                      "example", "exemple", "prenom.nom", "test", "user",
                      "username", "john.doe", "adresse", "mail"}
GENERIC_DOMAIN_PARTS = {"www", "com", "ma", "org", "net", "co", "fr", "eu", "gov",
                        "ac", "info", "biz", "group", "groupe", "maroc", "morocco",
                        "worldwide", "global", "international", "consulting",
                        "tech", "it", "solutions", "services"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
OBF_AT = re.compile(r"\s*[\[\(\{<]\s*(?:at|arobase|@)\s*[\]\)\}>]\s*", re.I)
OBF_DOT = re.compile(r"\s*[\[\(\{<]\s*(?:dot|point)\s*[\]\)\}>]\s*", re.I)
WORD_AT = re.compile(r"\s+arobase\s+", re.I)

mx_cache = {}


# ---------------------------------------------------------------- utilitaires
def get_domain(url):
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "fr,en;q=0.8"})
    retry = Retry(total=2, backoff_factor=0.8, allowed_methods=("GET",),
                  status_forcelist=(429, 500, 502, 503, 504))
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def http_get(session, url):
    try:
        return session.get(url, timeout=TIMEOUT)
    except requests.exceptions.SSLError:
        # certificat expiré ou mal configuré (fréquent sur de petits sites)
        return session.get(url, timeout=TIMEOUT, verify=False)


def decode(r):
    if r.encoding is None or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def same_site(domain, site_domains):
    return any(domain == s or domain.endswith("." + s) or s.endswith("." + domain)
               for s in site_domains)


def score_link(href, text=""):
    t = (unquote(href) + " " + text).lower()
    return sum(w for k, w in KEYWORDS.items() if k in t)


def find_links(base_url, html, site_domains):
    soup = BeautifulSoup(html, "html.parser")
    links = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        p = urlparse(url)
        if p.scheme not in ("http", "https") or p.path.lower().endswith(SKIP_EXT):
            continue
        if not same_site(get_domain(url), site_domains):
            continue
        s = score_link(href, a.get_text(" ", strip=True))
        if s > 0:
            links[url] = max(links.get(url, 0), s)
    return links


# ---------------------------------------------------------------- extraction
def decode_cf_email(encoded):
    try:
        key = int(encoded[:2], 16)
        return "".join(chr(int(encoded[i:i + 2], 16) ^ key)
                       for i in range(2, len(encoded), 2))
    except ValueError:
        return ""


def deobfuscate(text):
    text = htmllib.unescape(text)
    text = text.replace("\\u0040", "@").replace("%40", "@")
    text = OBF_AT.sub("@", text)
    text = WORD_AT.sub("@", text)
    return OBF_DOT.sub(".", text)


def extract_emails(html):
    """Trouve les emails : liens mailto, texte, code source, Cloudflare, formats masqués."""
    soup = BeautifulSoup(html, "html.parser")
    found = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        low = href.lower()
        if low.startswith("mailto:"):
            found.update(EMAIL_RE.findall(
                deobfuscate(unquote(href[7:].split("?")[0]))))
        elif "/cdn-cgi/l/email-protection#" in low:
            found.update(EMAIL_RE.findall(decode_cf_email(href.split("#")[-1])))
    for tag in soup.select("[data-cfemail]"):
        found.update(EMAIL_RE.findall(decode_cf_email(tag["data-cfemail"])))
    found.update(EMAIL_RE.findall(deobfuscate(soup.get_text(" "))))
    found.update(EMAIL_RE.findall(deobfuscate(html)))   # scripts, JSON-LD, attributs
    return {e.lower().strip(".-_") for e in found}


def domain_tokens(domain):
    parts = re.split(r"[.\-]", domain.lower())
    return {p for p in parts if len(p) >= 3 and p not in GENERIC_DOMAIN_PARTS}


def relation(email_domain, site_domains):
    """SAME = même domaine, RELATED = domaine du même groupe (ex. alten.ma / alten.com)."""
    if same_site(email_domain, site_domains):
        return "SAME"
    tokens = domain_tokens(email_domain)
    if any(tokens & domain_tokens(s) for s in site_domains):
        return "RELATED"
    return None


def syntax_ok(email):
    try:
        validate_email(email, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False


def keep_email(email, site_domains):
    """Retourne SAME / RELATED si l'email est à garder, sinon None."""
    local, _, domain = email.partition("@")
    if not local or not domain or len(email) > 80:
        return None
    if email.endswith(BAD_SUFFIXES) or local.startswith(BAD_PREFIXES):
        return None
    if any(w in local for w in BAD_WORDS) or local in PLACEHOLDER_LOCALS:
        return None
    if (domain in PLACEHOLDER_DOMAINS or domain.split(".")[0] in FREE_MAIL
            or any(p in domain for p in PLACEHOLDER_PARTS)):
        return None
    if not syntax_ok(email):
        return None
    return relation(domain, site_domains)


def classify(email):
    local = email.split("@")[0]
    tokens = set(re.split(r"[._\-+0-9]+", local))
    if tokens & HR_TOKENS or any(w in local for w in HR_WORDS):
        return "RH"
    if any(w in local for w in GENERIC_WORDS):
        return "GENERIC"
    return "OTHER"


def has_mx(domain):
    if domain in mx_cache:
        return mx_cache[domain]
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5
    resolver.timeout = 5
    ok = False
    for record in ("MX", "A"):
        try:
            resolver.resolve(domain, record)
            ok = True
            break
        except Exception:
            continue
    mx_cache[domain] = ok
    return ok


# ---------------------------------------------------------------- exploration d'un site
class Site:
    def __init__(self, website):
        self.session = make_session()
        self.website = website
        self.domains = [get_domain(website)]
        self.rp = None
        self.sitemaps = []

    def load_robots(self, origin):
        try:
            r = http_get(self.session, origin + "/robots.txt")
            if r.status_code == 200 and "<html" not in r.text[:300].lower():
                rp = RobotFileParser()
                rp.parse(r.text.splitlines())
                self.rp = rp
                self.sitemaps = rp.site_maps() or []
        except requests.RequestException:
            pass

    def allowed(self, url):
        return self.rp is None or self.rp.can_fetch(ROBOT_UA, url)

    def fetch(self, url, xml=False):
        if not self.allowed(url):
            return None, url
        try:
            r = http_get(self.session, url)
        except requests.RequestException:
            return None, url
        if r.status_code != 200:
            return None, r.url
        ctype = r.headers.get("Content-Type", "").lower()
        if ctype and "html" not in ctype and not (xml and "xml" in ctype):
            return None, r.url
        return decode(r)[:2_000_000], r.url

    def open(self):
        """Ouvre la page d'accueil (essaie https/http, avec/sans www)."""
        host = self.domains[0]
        candidates = []
        for u in (self.website, f"https://www.{host}", f"https://{host}",
                  f"http://{host}", f"http://www.{host}"):
            if u not in candidates:
                candidates.append(u)
        for url in candidates:
            try:
                r = http_get(self.session, url)
            except requests.RequestException:
                continue
            ctype = r.headers.get("Content-Type", "").lower()
            if r.status_code == 200 and "html" in ctype:
                final = r.url
                p = urlparse(final)
                self.load_robots(f"{p.scheme}://{p.netloc}")
                fd = get_domain(final)
                if fd not in self.domains:
                    self.domains.append(fd)
                if not self.allowed(final):
                    return "BLOCKED_ROBOTS", None
                return final, decode(r)[:2_000_000]
        return None, None

    def sitemap_urls(self, origin):
        urls = []
        for sm in (self.sitemaps or [origin + "/sitemap.xml"])[:2]:
            text, _ = self.fetch(sm, xml=True)
            if not text:
                continue
            locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text)
            for sub in [l for l in locs if l.endswith(".xml")][:2]:
                text2, _ = self.fetch(sub, xml=True)
                if text2:
                    locs += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text2)
            urls += [l for l in locs if not l.endswith(".xml")
                     and same_site(get_domain(l), self.domains)]
        return urls[:5000]

    def crawl(self, start_url, start_html):
        p = urlparse(start_url)
        origin = f"{p.scheme}://{p.netloc}"
        seen = {start_url.rstrip("/")}
        pages = [(start_url, start_html)]
        queue = {}

        def push(url, score):
            k = url.rstrip("/")
            if k not in seen:
                queue[k] = max(queue.get(k, 0), score)

        for u, s in find_links(start_url, start_html, self.domains).items():
            push(u, s)
        if len(queue) < 4:
            for path in FALLBACK_PATHS:
                push(origin + path, 1)
        for u in self.sitemap_urls(origin):
            s = score_link(u) - 0.5 * max(0, urlparse(u).path.count("/") - 1)
            if s > 0:
                push(u, s)

        while queue and len(pages) < MAX_PAGES:
            url = max(queue, key=queue.get)
            score = queue.pop(url)
            seen.add(url)
            time.sleep(DELAY)
            html, final = self.fetch(url)
            if not html:
                continue
            pages.append((final, html))
            if score >= 3:
                for u, s in find_links(final, html, self.domains).items():
                    if s >= 3:
                        push(u, s)
        return pages


def collect(pages, site_domains):
    emails = {}
    for url, html in pages:
        for e in extract_emails(html):
            rel = keep_email(e, site_domains)
            if rel:
                emails.setdefault(e, (url, rel))
    return emails


def process_site(website):
    site = Site(website)
    start_url, start_html = site.open()
    if start_url == "BLOCKED_ROBOTS":
        return {"status": "BLOCKED_ROBOTS", "pages": 0, "emails": {}}
    if start_url is None:
        return {"status": "UNREACHABLE", "pages": 0, "emails": {}}
    pages = site.crawl(start_url, start_html)
    emails = collect(pages, site.domains)
    return {"status": "OK" if emails else "NO_EMAIL", "pages": len(pages),
            "emails": emails, "domains": site.domains}


# ---------------------------------------------------------------- option --js (sites dynamiques)
def js_pass(sites):
    """Relit avec un vrai navigateur les sites où rien n'a été trouvé."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("\nOption --js : installe d'abord Playwright :")
        print("   pip install playwright")
        print("   python -m playwright install chromium")
        return {}
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=USER_AGENT, ignore_https_errors=True)
        page = ctx.new_page()
        for website in sites:
            domains = [get_domain(website)]
            pages = []
            try:
                page.goto(website, timeout=25000, wait_until="networkidle")
                home_url, home_html = page.url, page.content()
                fd = get_domain(home_url)
                if fd not in domains:
                    domains.append(fd)
                pages.append((home_url, home_html))
                links = sorted(find_links(home_url, home_html, domains).items(),
                               key=lambda kv: -kv[1])[:5]
                for url, _ in links:
                    time.sleep(DELAY)
                    try:
                        page.goto(url, timeout=25000, wait_until="networkidle")
                        pages.append((page.url, page.content()))
                    except Exception:
                        continue
            except Exception:
                pass
            results[get_domain(website)] = collect(pages, domains) if pages else {}
        browser.close()
    return results


# ---------------------------------------------------------------- fichiers
FIELDS = ["company_name", "company_domain", "website", "email", "type",
          "domain_match", "source_url", "mx_valid", "found_date", "status"]
REPORT_FIELDS = ["domain", "website", "status", "pages", "emails", "date"]


def load_lines(path):
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def load_websites():
    sites, seen = [], set()
    for path in INPUT_FILES:
        if not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                w = (row.get("website") or "").strip()
                if not w:
                    continue
                if not w.startswith("http"):
                    w = "https://" + w
                d = get_domain(w)
                if d in seen:
                    continue
                seen.add(d)
                name = (row.get("company_name") or row.get("title") or "").strip()
                sites.append((w, name or d.split(".")[0]))
    return sites


def load_existing_emails():
    existing = set()
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames and reader.fieldnames != FIELDS:
                raise SystemExit(
                    f"{OUTPUT_FILE} a un ancien format. Renomme-le d'abord "
                    f"(ex. : ren {OUTPUT_FILE} emails_ancien.csv) puis relance.")
            for row in reader:
                existing.add(row["email"].lower())
    return existing


def load_report_status():
    status = {}
    if os.path.exists(REPORT_FILE):
        with open(REPORT_FILE, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                status[row["domain"]] = row["status"]
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="nombre maximum de sites (test)")
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--js", action="store_true",
                        help="relit avec un navigateur les sites sans email (Playwright)")
    parser.add_argument("--retry-empty", action="store_true",
                        help="retraite les sites NO_EMAIL / UNREACHABLE d'un lancement précédent")
    args = parser.parse_args()

    processed = load_lines(PROCESSED_FILE)
    retry = set()
    if args.retry_empty:
        retry = {d for d, st in load_report_status().items()
                 if st in ("NO_EMAIL", "UNREACHABLE", "ERROR")}
    todo = [(w, n) for w, n in load_websites()
            if get_domain(w) not in processed or get_domain(w) in retry]
    if args.limit:
        todo = todo[:args.limit]
    existing = load_existing_emails()

    print(f"{len(todo)} sites à traiter avec {args.workers} en parallèle.\n")
    new_emails_file = not os.path.exists(OUTPUT_FILE)
    new_report_file = not os.path.exists(REPORT_FILE)
    statuses = {}

    with open(OUTPUT_FILE, "a", newline="", encoding="utf-8-sig") as out, \
         open(REPORT_FILE, "a", newline="", encoding="utf-8-sig") as rep, \
         open(PROCESSED_FILE, "a", encoding="utf-8") as proc:
        writer = csv.DictWriter(out, fieldnames=FIELDS)
        report = csv.DictWriter(rep, fieldnames=REPORT_FIELDS)
        if new_emails_file:
            writer.writeheader()
        if new_report_file:
            report.writeheader()

        def save(website, name, res):
            domain = get_domain(website)
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            added = 0
            for email, (source, rel) in sorted(res["emails"].items()):
                if email in existing:
                    continue
                existing.add(email)
                writer.writerow({
                    "company_name": name, "company_domain": domain,
                    "website": website, "email": email, "type": classify(email),
                    "domain_match": rel, "source_url": source,
                    "mx_valid": has_mx(email.split("@")[1]),
                    "found_date": now, "status": "PENDING",
                })
                added += 1
            report.writerow({"domain": domain, "website": website,
                             "status": res["status"], "pages": res["pages"],
                             "emails": added, "date": now})
            out.flush()
            rep.flush()
            return added

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(process_site, w): (w, n) for w, n in todo}
            try:
                for i, fut in enumerate(as_completed(futures), 1):
                    website, name = futures[fut]
                    domain = get_domain(website)
                    try:
                        res = fut.result()
                    except Exception as e:
                        res = {"status": "ERROR", "pages": 0, "emails": {}}
                        print(f"[{i}/{len(todo)}] {domain} : erreur {e}")
                    added = save(website, name, res)
                    statuses[domain] = (website, name, res["status"])
                    if res["status"] != "ERROR":
                        proc.write(domain + "\n")
                        proc.flush()
                    print(f"[{i}/{len(todo)}] {domain:35} {res['status']:15} "
                          f"{added} email(s), {res['pages']} page(s)")
            except KeyboardInterrupt:
                print("\nArrêt demandé, fin des sites en cours...")
                ex.shutdown(wait=False, cancel_futures=True)

        if args.js:
            empty = [v for v in statuses.values() if v[2] == "NO_EMAIL"]
            print(f"\nOption --js : {len(empty)} sites relus avec un navigateur...")
            js_results = js_pass([w for w, _, _ in empty])
            for website, name, _ in empty:
                emails = js_results.get(get_domain(website), {})
                if emails:
                    added = save(website, name,
                                 {"status": "OK_JS", "pages": 0, "emails": emails})
                    print(f"   {get_domain(website):35} OK_JS          {added} email(s)")

    sites_ok = sum(1 for s in statuses.values() if s[2] in ("OK", "NO_EMAIL"))
    print(f"\nTerminé. Emails : {OUTPUT_FILE} | Détail par site : {REPORT_FILE} "
          f"({sites_ok} sites lus).")


if __name__ == "__main__":
    main()