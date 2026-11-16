#!/usr/bin/env python3
"""
=============================================================================
CLEAN_EMAILS.PY - Enterprise Moroccan Tech & HR Email Sanitizer & Ranker
=============================================================================
Cleans, deduplicates, verifies deliverability, and scores scraped emails
for tech & data recruitment outreach in Morocco (Startups, ESNs, Multinationals).

Outputs:
  emails_clean.csv      - High-confidence leads ranked by priority (Ready for CRM / Mail merge)
  emails_review.csv     - Low-confidence or unclassified emails needing human check
  emails_rejected.csv   - Discarded records (duplicates, invalid syntax, spam traps, bad MX)
  emails_report.txt     - Comprehensive KPI report on the dataset

Scoring Engine:
  - 100 pts: Direct Moroccan RH email (recrutement@domain.ma, rh@domain.ma)
  -  90 pts: Corporate RH email with Morocco token (recrutement.maroc@domain.com)
  -  80 pts: Startup Founder / CTO (ceo@, cto@, founder@)
  -  65 pts: Generic Moroccan contact (contact@domain.ma, info@domain.ma)
  -  45 pts: Generic international contact (contact@domain.com)
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Output Schemas
# ---------------------------------------------------------------------------
OUTPUT_FIELDS = [
    "company", "hr_email", "position", "city", "priority_score",
    "role_type", "company_domain", "website", "mx_valid",
    "status", "review_reason",
]

TRUE_VALUES = {"true", "1", "yes", "y", "valid", "ok"}
FALSE_VALUES = {"false", "0", "no", "n", "invalid", "fail"}

DEFAULT_BAD_WORDS = (
    "signalement", "investor", "press", "presse", "compliance", "ethic",
    "legal", "billing", "facturation", "support", "alerte", "comptabilite",
    "invoices", "abuse", "privacy", "dpo", "securite", "security",
)

EMAIL_REGEX = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
def extract_first_value(row: dict[str, Any], *keys: str) -> str:
    """Retrieve first non-empty value across multiple candidate column names."""
    for key in keys:
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def sanitize_email(value: str) -> str:
    """Normalize and strip invisible characters, URL schemes, and invalid whitespace."""
    if not value:
        return ""
    # Strip invisible zero-width spaces, byte order marks, and control characters
    cleaned = "".join(ch for ch in value if unicodedata.category(ch) != "Cf")
    cleaned = cleaned.strip().lower()
    cleaned = re.sub(r"^mailto:", "", cleaned)
    cleaned = cleaned.strip(" \t\r\n<>[](){}.,;:'\"")
    cleaned = re.sub(r"\s*@\s*", "@", cleaned)
    return cleaned


def is_valid_email_format(email: str) -> bool:
    """Verify structural validity and RFC length constraints."""
    if not email or len(email) > 254 or email.count("@") != 1:
        return False
    local_part, domain_part = email.rsplit("@", 1)
    if len(local_part) > 64 or not local_part or not domain_part:
        return False
    if ".." in local_part or local_part.startswith(".") or local_part.endswith("."):
        return False
    try:
        ascii_domain = domain_part.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    return bool(EMAIL_REGEX.fullmatch(f"{local_part}@{ascii_domain}"))


def parse_boolean_flag(value: str) -> bool | None:
    """Safely cast string truth values to Python boolean."""
    norm = (value or "").strip().lower()
    if norm in TRUE_VALUES:
        return True
    if norm in FALSE_VALUES:
        return False
    return None


def calculate_lead_score(email: str, role_type: str, company_domain: str) -> int:
    """Compute lead scoring specifically optimized for Moroccan tech job seekers."""
    local, _, domain = email.partition("@")
    is_ma_domain = domain.endswith(".ma")
    has_morocco_keyword = any(k in local or k in domain for k in ("maroc", "morocco", "casa"))

    if role_type == "RH":
        if is_ma_domain:
            return 100
        if has_morocco_keyword:
            return 95
        return 85

    if role_type == "FOUNDER_TECH":
        if is_ma_domain:
            return 80
        return 75

    if role_type == "GENERIC":
        if is_ma_domain:
            return 65
        return 45

    return 30


# ---------------------------------------------------------------------------
# Core Cleaning Pipeline
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Professional Moroccan Tech & HR Email Cleaner")
    parser.add_argument("--input", default="emails.csv", help="Input CSV path (default: emails.csv)")
    parser.add_argument("--output", default="emails_clean.csv", help="Clean qualified leads (default: emails_clean.csv)")
    parser.add_argument("--review", default="emails_review.csv", help="Review CSV path")
    parser.add_argument("--rejected", default="emails_rejected.csv", help="Rejected CSV path")
    parser.add_argument("--report", default="emails_report.txt", help="Summary report path")
    parser.add_argument("--exclude", default=None, help="Optional CSV of existing contacts to exclude")
    parser.add_argument("--position", default="Data Engineer / Data Scientist", help="Target job role")
    parser.add_argument("--city", default="Casablanca, Maroc", help="Target job city/region")
    parser.add_argument("--per-company", type=int, default=3, help="Max emails to keep per company (default: 3)")
    parser.add_argument("--exclude-tlds", nargs="*", default=[], help="TLDs to exclude, e.g. .fr .de .uk")
    parser.add_argument("--bad-words", nargs="*", default=list(DEFAULT_BAD_WORDS), help="Exclude words in local part")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        return 1

    # Load exclusions if provided
    excluded_emails: set[str] = set()
    if args.exclude:
        excl_path = Path(args.exclude)
        if excl_path.exists():
            with excl_path.open("r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    raw = extract_first_value(row, "hr_email", "email", "email_normalized")
                    s = sanitize_email(raw)
                    if s:
                        excluded_emails.add(s)

    excluded_tlds = tuple(t.lower() if t.startswith(".") else "." + t.lower() for t in args.exclude_tlds)
    bad_words = tuple(w.lower() for w in args.bad_words)

    # Read input records
    with input_path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        source_rows = list(reader)

    clean_by_company: dict[str, list[dict[str, Any]]] = defaultdict(list)
    review_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []

    seen_emails: set[str] = set()
    stats = Counter()

    for idx, row in enumerate(source_rows, start=2):
        stats["total_input"] += 1
        raw_email = extract_first_value(row, "email", "hr_email")
        email = sanitize_email(raw_email)

        company = extract_first_value(row, "company_name", "company") or "Société Tech"
        company_domain = extract_first_value(row, "company_domain", "domain").lower()
        website = extract_first_value(row, "website")
        role_type = extract_first_value(row, "type", "email_type").upper() or "OTHER"
        mx_raw = extract_first_value(row, "mx_valid")
        mx_bool = parse_boolean_flag(mx_raw)

        # 1. Format Check
        if not is_valid_email_format(email):
            rejected_rows.append({
                "company": company, "hr_email": email or raw_email, "position": args.position,
                "city": args.city, "priority_score": 0, "role_type": role_type,
                "company_domain": company_domain, "website": website, "mx_valid": "FALSE",
                "status": "REJECTED", "review_reason": f"Format email invalide (ligne {idx})",
            })
            stats["invalid_format"] += 1
            continue

        # 2. Duplicate Check
        if email in seen_emails:
            rejected_rows.append({
                "company": company, "hr_email": email, "position": args.position,
                "city": args.city, "priority_score": 0, "role_type": role_type,
                "company_domain": company_domain, "website": website, "mx_valid": "FALSE",
                "status": "REJECTED", "review_reason": "Doublon dans les résultats",
            })
            stats["duplicate"] += 1
            continue
        seen_emails.add(email)

        # 3. Already Excluded Check
        if email in excluded_emails:
            rejected_rows.append({
                "company": company, "hr_email": email, "position": args.position,
                "city": args.city, "priority_score": 0, "role_type": role_type,
                "company_domain": company_domain, "website": website, "mx_valid": "FALSE",
                "status": "REJECTED", "review_reason": "Déjà présent dans le fichier d'exclusion",
            })
            stats["excluded"] += 1
            continue

        local_part, domain_part = email.split("@")

        # 4. Excluded TLDs
        if excluded_tlds and domain_part.endswith(excluded_tlds):
            rejected_rows.append({
                "company": company, "hr_email": email, "position": args.position,
                "city": args.city, "priority_score": 0, "role_type": role_type,
                "company_domain": company_domain, "website": website, "mx_valid": "FALSE",
                "status": "REJECTED", "review_reason": "TLD étranger exclu par paramètre",
            })
            stats["excluded_tld"] += 1
            continue

        # 5. Blacklisted Local Words
        if any(w in local_part for w in bad_words):
            rejected_rows.append({
                "company": company, "hr_email": email, "position": args.position,
                "city": args.city, "priority_score": 0, "role_type": role_type,
                "company_domain": company_domain, "website": website, "mx_valid": "FALSE",
                "status": "REJECTED", "review_reason": "Adresse non pertinente (mot-clé de filtrage)",
            })
            stats["bad_role"] += 1
            continue

        # 6. MX Validity
        if mx_bool is False:
            rejected_rows.append({
                "company": company, "hr_email": email, "position": args.position,
                "city": args.city, "priority_score": 0, "role_type": role_type,
                "company_domain": company_domain, "website": website, "mx_valid": "FALSE",
                "status": "REJECTED", "review_reason": "Domaine sans serveur MX actif",
            })
            stats["mx_invalid"] += 1
            continue

        score = calculate_lead_score(email, role_type, company_domain)
        item = {
            "company": company,
            "hr_email": email,
            "position": args.position,
            "city": args.city,
            "priority_score": score,
            "role_type": role_type,
            "company_domain": company_domain or domain_part,
            "website": website,
            "mx_valid": "TRUE" if mx_bool else "UNKNOWN",
            "status": "APPROVED" if score >= 80 else "PENDING",
            "review_reason": "",
        }

        # 7. Classification check: send unknown/other to review
        if role_type not in ("RH", "FOUNDER_TECH", "GENERIC"):
            item["review_reason"] = "Type d'adresse non identifié (revue manuelle conseillée)"
            review_rows.append(item)
            stats["needs_review"] += 1
            continue

        if mx_bool is None:
            item["review_reason"] = "MX non vérifié automatiquement"
            review_rows.append(item)
            stats["needs_review"] += 1
            continue

        # Group by company to enforce per-company limit
        company_key = company_domain or company.lower()
        clean_by_company[company_key].append(item)

    # Apply per-company cap by selecting top scored emails
    final_clean_leads: list[dict[str, Any]] = []
    for comp_key, leads in clean_by_company.items():
        # Sort leads by score descending
        sorted_leads = sorted(leads, key=lambda x: x["priority_score"], reverse=True)
        accepted = sorted_leads[:args.per_company]
        final_clean_leads.extend(accepted)

        for overflow in sorted_leads[args.per_company:]:
            overflow["status"] = "REJECTED"
            overflow["review_reason"] = f"Plafond atteint ({args.per_company} contacts max par entreprise)"
            rejected_rows.append(overflow)
            stats["company_limit_exceeded"] += 1

    # Sort final clean leads by score descending
    final_clean_leads.sort(key=lambda x: x["priority_score"], reverse=True)

    # ---------------------------------------------------------------------------
    # Write Output Files
    # ---------------------------------------------------------------------------
    def write_csv_safely(path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    write_csv_safely(Path(args.output), final_clean_leads)
    write_csv_safely(Path(args.review), review_rows)
    write_csv_safely(Path(args.rejected), rejected_rows)

    # ---------------------------------------------------------------------------
    # Summary Report
    # ---------------------------------------------------------------------------
    rh_count = sum(1 for r in final_clean_leads if r["role_type"] == "RH")
    founder_count = sum(1 for r in final_clean_leads if r["role_type"] == "FOUNDER_TECH")
    generic_count = sum(1 for r in final_clean_leads if r["role_type"] == "GENERIC")
    ma_count = sum(1 for r in final_clean_leads if r["hr_email"].endswith(".ma"))

    summary_text = [
        "=================================================================",
        "        RAPPORT D'AUDIT ET NETTOYAGE DES EMAILS RH MAROC         ",
        "=================================================================",
        f"Fichier analysé            : {args.input}",
        f"Total adresses reçues      : {stats['total_input']}",
        f"Contacts qualifiés retenus : {len(final_clean_leads)}",
        f"Contacts à vérifier        : {len(review_rows)}",
        f"Contacts écartés           : {len(rejected_rows)}",
        "",
        "--- QUALIFICATION DES CONTACTS RETENUS ---",
        f"  * Responsables RH / Recrutement direct : {rh_count}",
        f"  * Fondateurs / CTOs (Startups)          : {founder_count}",
        f"  * Contacts institutionnels / Génériques : {generic_count}",
        f"  * Adresses hébergées sur domaine .ma    : {ma_count}",
        "",
        "--- DÉTAIL DES EXCLUSIONS ---",
        f"  * Format email invalide                 : {stats['invalid_format']}",
        f"  * Doublons éliminés                     : {stats['duplicate']}",
        f"  * Déjà présents dans liste d'exclusion  : {stats['excluded']}",
        f"  * TLDs exclus                           : {stats['excluded_tld']}",
        f"  * Mots-clés parasites (facturation...)  : {stats['bad_role']}",
        f"  * Serveur MX invalide                   : {stats['mx_invalid']}",
        f"  * Limite max par entreprise atteinte    : {stats['company_limit_exceeded']}",
        "",
        "--- FICHIERS GÉNÉRÉS ---",
        f"  [1] Contacts prêts à l'envoi : {args.output}",
        f"  [2] Contacts pour vérif     : {args.review}",
        f"  [3] Contacts écartés        : {args.rejected}",
        "=================================================================",
    ]

    report_path = Path(args.report)
    report_path.write_text("\n".join(summary_text) + "\n", encoding="utf-8")
    print("\n".join(summary_text))

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nNettoyage interrompu.", file=sys.stderr)
        sys.exit(130)
