#!/usr/bin/env python3
"""
Professional HR email cleaner for scraped company contacts.

Input CSV columns supported:
  email (or hr_email), mx_valid, type, company_name (or company),
  company_domain (optional)

Outputs:
  emails_clean.csv       RH/GENERIC contacts ready for review/import
  emails_review.csv      uncertain contacts (OTHER, unknown MX, bad metadata)
  emails_rejected.csv    duplicates, malformed addresses, excluded contacts
  emails_report.txt      run summary

Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


BASE_FIELDS = [
    "company", "hr_email", "position", "city", "status", "sent_date",
    "email_normalized", "format_status", "mx_valid", "source_type",
    "company_domain", "review_reason",
]

DEFAULT_BAD_WORDS = (
    "signalement", "investor", "press", "compliance", "ethic",
    "legal", "billing", "facturation", "support", "alerte",
)
TYPE_ORDER = {"RH": 0, "GENERIC": 1, "OTHER": 2}
TRUE_VALUES = {"true", "1", "yes", "y", "valid", "ok"}
FALSE_VALUES = {"false", "0", "no", "n", "invalid", "fail"}

# Practical syntax check. This checks formatting, not mailbox existence.
EMAIL_RE = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+$",
    re.IGNORECASE,
)


def first_value(row: dict[str, Any], *keys: str) -> str:
    """Return the first non-empty value from a list of possible column names."""
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def normalize_email(value: str) -> str:
    """Normalize common scraped email representations."""
    email = (value or "").strip().lower()
    email = re.sub(r"^mailto:", "", email)
    email = email.strip(" \t\r\n<>[](){}.,;:'\"")
    # Remove accidental spaces around the @ sign and at the edges.
    email = re.sub(r"\s*@\s*", "@", email)
    return email


def email_is_well_formed(email: str) -> bool:
    if not email or len(email) > 254 or email.count("@") != 1:
        return False
    local, domain = email.rsplit("@", 1)
    if len(local) > 64 or not local or not domain:
        return False
    if ".." in local or local.startswith(".") or local.endswith("."):
        return False
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    return bool(EMAIL_RE.fullmatch(f"{local}@{ascii_domain}"))


def parse_bool(value: str) -> bool | None:
    """Return True/False, or None when the source value is unknown/missing."""
    normalized = (value or "").strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return None


def load_excluded(path: Path | None) -> set[str]:
    excluded: set[str] = set()
    if not path:
        return excluded
    if not path.exists():
        raise FileNotFoundError(f"Fichier d'exclusion introuvable : {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            value = first_value(row, "hr_email", "email", "email_normalized")
            normalized = normalize_email(value)
            if normalized:
                excluded.add(normalized)
    return excluded


def read_input(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Fichier source introuvable : {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("Le fichier CSV est vide ou ne contient pas d'en-têtes.")
        return list(reader)


def make_output_row(
    source: dict[str, Any],
    email: str,
    position: str,
    city: str,
    status: str,
    reason: str = "",
    format_status: str = "VALID",
) -> dict[str, str]:
    company = first_value(source, "company_name", "company", "name")
    company_domain = first_value(source, "company_domain", "domain").lower()
    source_type = first_value(source, "type", "email_type").upper() or "UNKNOWN"
    mx_raw = first_value(source, "mx_valid")
    mx_value = parse_bool(mx_raw)
    mx_text = "TRUE" if mx_value is True else "FALSE" if mx_value is False else "UNKNOWN"

    return {
        "company": company,
        "hr_email": email,
        "position": position,
        "city": city,
        "status": status,
        "sent_date": "",
        "email_normalized": email,
        "format_status": format_status,
        "mx_valid": mx_text,
        "source_type": source_type,
        "company_domain": company_domain,
        "review_reason": reason,
    }


def sort_key(row: dict[str, str]) -> tuple[int, int, int, str]:
    email_type = row["source_type"]
    domain = row["company_domain"]
    email = row["email_normalized"]
    same_domain = bool(domain and email.endswith("@" + domain))
    return (
        TYPE_ORDER.get(email_type, 3),
        0 if same_domain else 1,
        0 if email.endswith(".ma") else 1,
        email,
    )


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write atomically to avoid leaving a half-written output file."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=BASE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Nettoie, déduplique et classe les emails RH issus d'un scraping."
    )
    parser.add_argument("--input", default="emails.csv", help="CSV source (défaut : emails.csv)")
    parser.add_argument("--output", default="emails_clean.csv", help="CSV RH/GENERIC")
    parser.add_argument("--review", default="emails_review.csv", help="CSV à vérifier manuellement")
    parser.add_argument("--rejected", default="emails_rejected.csv", help="CSV des adresses écartées")
    parser.add_argument("--report", default="emails_report.txt", help="Rapport texte")
    parser.add_argument("--exclude", default=None, help="Export CSV du Google Sheet à exclure")
    parser.add_argument("--position", default="Data Engineer / Data Scientist")
    parser.add_argument("--city", default="Casablanca, Maroc")
    parser.add_argument(
        "--per-company", "--max-per-company", dest="per_company",
        type=int, default=3, help="Nombre maximum d'emails par entreprise (défaut : 3)"
    )
    parser.add_argument(
        "--approve", type=int, default=0,
        help="Passe les N premiers emails retenus en APPROVED (défaut : 0/PENDING)"
    )
    parser.add_argument(
        "--exclude-tlds", nargs="*", default=[],
        help="TLD à écarter explicitement, ex. --exclude-tlds .fr .de .uk"
    )
    parser.add_argument(
        "--bad-words", nargs="*", default=list(DEFAULT_BAD_WORDS),
        help="Mots à écarter dans la partie avant @"
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.per_company < 1:
        parser.error("--per-company doit être supérieur ou égal à 1.")
    if args.approve < 0:
        parser.error("--approve ne peut pas être négatif.")

    input_path = Path(args.input)
    output_path = Path(args.output)
    review_path = Path(args.review)
    rejected_path = Path(args.rejected)
    report_path = Path(args.report)

    try:
        source_rows = read_input(input_path)
        excluded = load_excluded(Path(args.exclude) if args.exclude else None)
    except (OSError, ValueError) as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 2

    clean_candidates: list[dict[str, str]] = []
    review_rows: list[dict[str, str]] = []
    rejected_rows: list[dict[str, str]] = []
    seen: set[str] = set()
    counts: Counter[str] = Counter()
    by_company: dict[str, list[dict[str, str]]] = defaultdict(list)

    excluded_tlds = tuple(
        tld.lower() if tld.startswith(".") else "." + tld.lower()
        for tld in args.exclude_tlds
    )
    bad_words = tuple(word.lower() for word in args.bad_words)

    for line_number, source in enumerate(source_rows, start=2):
        counts["input"] += 1
        raw_email = first_value(source, "email", "hr_email", "email_address")
        email = normalize_email(raw_email)

        if not email_is_well_formed(email):
            rejected_rows.append(make_output_row(
                source, email or raw_email, args.position, args.city,
                "REJECTED", f"Format email invalide (ligne {line_number})",
                format_status="INVALID",
            ))
            counts["invalid_format"] += 1
            continue

        if email in seen:
            rejected_rows.append(make_output_row(
                source, email, args.position, args.city,
                "REJECTED", "Doublon dans le fichier source",
            ))
            counts["duplicate"] += 1
            continue
        seen.add(email)

        if email in excluded:
            rejected_rows.append(make_output_row(
                source, email, args.position, args.city,
                "REJECTED", "Adresse déjà présente dans le Google Sheet",
            ))
            counts["already_in_sheet"] += 1
            continue

        local, domain = email.rsplit("@", 1)
        if excluded_tlds and domain.endswith(excluded_tlds):
            rejected_rows.append(make_output_row(
                source, email, args.position, args.city,
                "REJECTED", "TLD exclu par configuration",
            ))
            counts["excluded_tld"] += 1
            continue

        if any(word in local for word in bad_words):
            rejected_rows.append(make_output_row(
                source, email, args.position, args.city,
                "REJECTED", "Adresse non adaptée (mot-clé de filtrage)",
            ))
            counts["filtered_role"] += 1
            continue

        mx = parse_bool(first_value(source, "mx_valid"))
        if mx is False:
            rejected_rows.append(make_output_row(
                source, email, args.position, args.city,
                "REJECTED", "Domaine déclaré sans enregistrement MX",
            ))
            counts["mx_invalid"] += 1
            continue

        email_type = first_value(source, "type", "email_type").upper()
        row = make_output_row(
            source, email, args.position, args.city, "PENDING",
            reason="" if mx is True else "MX inconnu : vérification nécessaire",
            format_status="VALID",
        )

        if email_type not in {"RH", "GENERIC"}:
            row["review_reason"] = row["review_reason"] or "Type d'adresse non classé RH/GENERIC"
            review_rows.append(row)
            counts["manual_review"] += 1
            continue

        if mx is None:
            review_rows.append(row)
            counts["mx_unknown"] += 1
            continue

        company_key = (
            first_value(source, "company_domain", "domain").lower()
            or domain
            or first_value(source, "company_name", "company").lower()
            or f"unknown-company-{line_number}"
        )
        by_company[company_key].append(row)

    # Apply the per-company limit after ranking contacts.
    for company_rows in by_company.values():
        ranked = sorted(company_rows, key=sort_key)
        clean_candidates.extend(ranked[:args.per_company])
        for extra in ranked[args.per_company:]:
            extra["status"] = "REJECTED"
            extra["review_reason"] = f"Limite de {args.per_company} emails par entreprise"
            rejected_rows.append(extra)
            counts["company_limit"] += 1

    clean_candidates.sort(key=sort_key)
    for index, row in enumerate(clean_candidates):
        if index < args.approve:
            row["status"] = "APPROVED"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    rejected_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    write_csv(output_path, clean_candidates)
    write_csv(review_path, review_rows)
    write_csv(rejected_path, rejected_rows)

    summary = [
        "=== RAPPORT DE NETTOYAGE DES EMAILS ===",
        f"Fichier source              : {input_path}",
        f"Emails analysés             : {counts['input']}",
        f"Emails retenus              : {len(clean_candidates)}",
        f"À vérifier manuellement     : {len(review_rows)}",
        f"Écartés                     : {len(rejected_rows)}",
        "",
        "Détail des exclusions :",
    ]
    for key, label in [
        ("invalid_format", "Format invalide"),
        ("duplicate", "Doublons dans le CSV"),
        ("already_in_sheet", "Déjà présents dans le Google Sheet"),
        ("excluded_tld", "TLD explicitement exclus"),
        ("filtered_role", "Mots-clés non adaptés"),
        ("mx_invalid", "Domaine sans MX"),
        ("mx_unknown", "MX inconnu / revue manuelle"),
        ("manual_review", "Type à vérifier"),
        ("company_limit", "Limite par entreprise"),
    ]:
        summary.append(f"  {label:<34}: {counts[key]}")

    summary.extend([
        "",
        f"CSV retenus   : {output_path}",
        f"CSV à vérifier: {review_path}",
        f"CSV écartés   : {rejected_path}",
        "",
        "IMPORTANT : un format correct et un MX valide ne garantissent pas",
        "que la boîte existe ou accepte les messages. Utiliser Abstract API",
        "pour une vérification de délivrabilité avant tout envoi.",
    ])
    report_path.write_text("\n".join(summary) + "\n", encoding="utf-8")

    print("\n".join(summary))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterruption demandée. Les fichiers déjà écrits restent intacts.", file=sys.stderr)
        raise SystemExit(130)
