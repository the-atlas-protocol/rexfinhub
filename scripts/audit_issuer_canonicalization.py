"""Audit alpha -- Issuer canonicalization cluster analysis.

Pulls all distinct issuer_display values from mkt_master_data WHERE
market_status='ACTV', clusters them by token-based similarity and
Levenshtein distance, and produces:

  config/rules/issuer_canonicalization.csv  -- AUTO/REVIEW merge proposals
  docs/issuer_canonicalization_report.md    -- human-readable cluster report

This script is READ-ONLY -- it never writes to mkt_master_data.
An apply script is out of scope; coordinator decides apply later.

Usage:
    python scripts/audit_issuer_canonicalization.py
"""
from __future__ import annotations

import csv
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
OUTPUT_CSV = PROJECT_ROOT / "config" / "rules" / "issuer_canonicalization.csv"
OUTPUT_MD = PROJECT_ROOT / "docs" / "issuer_canonicalization_report.md"

# Similarity thresholds
THRESHOLD_AUTO = 0.92       # >= this: AUTO merge proposal
THRESHOLD_REVIEW = 0.75     # >= this AND < AUTO: flag for human REVIEW

# Regex to strip legal/product suffixes before comparison
_SUFFIX_RE = re.compile(
    r"\b(etf trust|etf|trust|etfs|inc\.?|llc\.?|lp\.?|"
    r"fund|funds|shares|group|asset management|"
    r"investments?|capital|financial|management|"
    r"advisors?|solutions?|active|exchange-?traded|"
    r"delaware|sponsor)\b",
    re.IGNORECASE,
)

_NOISE_WORDS = {"the", "a", "an", "of", "and", "&"}

# Generic tokens that are too common to anchor a brand match on their own.
# e.g. "series", "architect", "northern", "lights" shared between unrelated brands.
_GENERIC_TOKENS = {
    "series", "architect", "northern", "lights", "global", "american",
    "international", "market", "digital", "tactical", "total", "core",
    "dynamic", "select", "target", "enhanced", "managed",
}


# ---------------------------------------------------------------------------
# String normalisation
# ---------------------------------------------------------------------------

def _tokens(name: str) -> list[str]:
    """Return a sorted, deduplicated, noise-stripped list of meaningful tokens."""
    s = _SUFFIX_RE.sub(" ", name.lower())
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return sorted(t for t in s.split() if t and t not in _NOISE_WORDS)


def _tokens_ordered(name: str) -> list[str]:
    """Same but preserving original order (for prefix detection)."""
    s = _SUFFIX_RE.sub(" ", name.lower())
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return [t for t in s.split() if t and t not in _NOISE_WORDS]


# ---------------------------------------------------------------------------
# Similarity functions
# ---------------------------------------------------------------------------

def levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + (0 if ca == cb else 1),
            ))
        prev = curr
    return prev[-1]


def levenshtein_similarity(s1: str, s2: str) -> float:
    """Similarity on normalised+sorted token strings."""
    t1 = " ".join(_tokens(s1))
    t2 = " ".join(_tokens(s2))
    if not t1 and not t2:
        return 1.0
    if not t1 or not t2:
        return 0.0
    dist = levenshtein(t1, t2)
    max_len = max(len(t1), len(t2))
    return round(1.0 - dist / max_len, 4)


def token_overlap_similarity(s1: str, s2: str) -> float:
    """Token-containment similarity.

    If all tokens of the shorter name appear in the longer name, that is a
    strong brand-variant signal:
      - "iShares Delaware Trust Sponsor" -> tokens {ishares}  (after suffix strip)
      - "iShares"                        -> tokens {ishares}
      - containment = 1.0

    Guards against false positives:
      - Requires at least one non-generic token in the intersection.
      - Single-letter tokens are ignored.
      - If the only shared tokens are in _GENERIC_TOKENS, returns 0.
    """
    t1 = set(_tokens(s1))
    t2 = set(_tokens(s2))
    # Remove single-character tokens (too noisy)
    t1 = {t for t in t1 if len(t) > 1}
    t2 = {t for t in t2 if len(t) > 1}
    if not t1 and not t2:
        return 1.0
    if not t1 or not t2:
        return 0.0
    intersection = t1 & t2
    if not intersection:
        return 0.0
    # If all shared tokens are generic, don't count this as a containment match
    non_generic = intersection - _GENERIC_TOKENS
    if not non_generic:
        return 0.0
    containment = len(intersection) / min(len(t1), len(t2))
    return round(containment, 4)


def best_similarity(s1: str, s2: str) -> float:
    """Best of Levenshtein similarity and token-containment similarity."""
    lev = levenshtein_similarity(s1, s2)
    cont = token_overlap_similarity(s1, s2)
    return max(lev, cont)


def classify_variant(variant: str, canonical: str) -> tuple[str, float]:
    """Return (confidence_label, similarity) for a variant vs canonical."""
    sim = best_similarity(variant, canonical)
    if sim >= THRESHOLD_AUTO:
        return "AUTO", sim
    elif sim >= THRESHOLD_REVIEW:
        return "REVIEW", sim
    return "DISTINCT", sim


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_issuers(con: sqlite3.Connection) -> list[tuple[str, int]]:
    """Return [(issuer_display, fund_count)] for ACTV non-null issuers."""
    cur = con.cursor()
    cur.execute("""
        SELECT issuer_display, COUNT(DISTINCT ticker) AS fund_count
        FROM mkt_master_data
        WHERE market_status = 'ACTV'
          AND issuer_display IS NOT NULL
          AND issuer_display != ''
        GROUP BY issuer_display
        ORDER BY fund_count DESC
    """)
    return [(row[0], row[1]) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

def build_clusters(issuers: list[tuple[str, int]]) -> list[list[tuple[str, int]]]:
    """Greedy clustering: largest fund_count anchors each cluster.

    A new issuer joins an existing cluster if best_similarity to the
    cluster anchor >= THRESHOLD_REVIEW.
    """
    sorted_issuers = sorted(issuers, key=lambda x: x[1], reverse=True)
    cluster_anchors: list[str] = []
    clusters: list[list[tuple[str, int]]] = []
    assigned: set[str] = set()

    for name, count in sorted_issuers:
        if name in assigned:
            continue
        best_idx = -1
        best_sim = 0.0
        for idx, anchor in enumerate(cluster_anchors):
            sim = best_similarity(name, anchor)
            if sim > best_sim:
                best_sim = sim
                best_idx = idx

        if best_idx >= 0 and best_sim >= THRESHOLD_REVIEW:
            clusters[best_idx].append((name, count))
        else:
            cluster_anchors.append(name)
            clusters.append([(name, count)])
        assigned.add(name)

    return clusters


# ---------------------------------------------------------------------------
# Output generation
# ---------------------------------------------------------------------------

def generate_csv_rows(clusters: list[list[tuple[str, int]]]) -> list[dict]:
    """One CSV row per non-canonical variant in multi-member clusters."""
    rows: list[dict] = []
    for cluster in clusters:
        if len(cluster) == 1:
            continue
        canonical_name = cluster[0][0]
        for variant, count in cluster[1:]:
            confidence, sim = classify_variant(variant, canonical_name)
            rows.append({
                "variant": variant,
                "canonical": canonical_name,
                "fund_count": count,
                "similarity": sim,
                "confidence": confidence,
            })
    return rows


def generate_md_report(
    clusters: list[list[tuple[str, int]]],
    total_distinct: int,
    auto_count: int,
    review_count: int,
) -> str:
    """Build the full markdown cluster report."""
    lines: list[str] = [
        "# Issuer Canonicalization Report",
        "",
        "**Generated**: 2026-05-05",
        "**Source**: `mkt_master_data` WHERE `market_status = 'ACTV'`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total distinct `issuer_display` values | {total_distinct} |",
        f"| Singleton clusters (no action) | {sum(1 for c in clusters if len(c) == 1)} |",
        f"| Multi-member clusters | {sum(1 for c in clusters if len(c) > 1)} |",
        f"| AUTO merge proposals | {auto_count} |",
        f"| REVIEW items (human eyes required) | {review_count} |",
        "",
        "## Confidence Thresholds",
        "",
        "- **AUTO** (similarity >= 0.92 — safe to merge programmatically)",
        "- **REVIEW** (similarity 0.75-0.92 or token-containment match — coordinator must decide)",
        "- **DISTINCT** (similarity < 0.75, genuinely different issuers)",
        "",
        "Similarity uses the max of two scores:",
        "1. Levenshtein distance on normalised+sorted token strings",
        "2. Token-containment: what fraction of the shorter name's tokens appear in the longer",
        "",
        "---",
        "",
        "## Multi-Member Clusters",
        "",
    ]

    multi = [c for c in clusters if len(c) > 1]
    multi.sort(key=lambda c: sum(x[1] for x in c), reverse=True)

    for cluster in multi:
        canonical_name, canonical_count = cluster[0]
        total_funds = sum(x[1] for x in cluster)
        lines.append(f"### Cluster: {canonical_name}")
        lines.append("")
        lines.append(f"- **{canonical_name}** ({canonical_count} funds) <- CANONICAL")
        for variant, count in cluster[1:]:
            confidence, sim = classify_variant(variant, canonical_name)
            if confidence == "AUTO":
                lines.append(
                    f"- {variant} ({count} funds) -> merge "
                    f"[AUTO, similarity {sim:.2f}]"
                )
            elif confidence == "REVIEW":
                lines.append(
                    f"- {variant} ({count} funds) -> **REVIEW** "
                    f"(similarity {sim:.2f})"
                )
            else:
                lines.append(
                    f"- {variant} ({count} funds) -> DISTINCT "
                    f"(similarity {sim:.2f}, leave separate)"
                )
        lines.append(f"  *Total cluster funds: {total_funds}*")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Singleton Clusters (no action needed)")
    lines.append("")
    singletons = sorted(c for c in clusters if len(c) == 1 for _ in [None])
    singletons_sorted = sorted([c for c in clusters if len(c) == 1], key=lambda c: c[0][0])
    lines.append(", ".join(f"`{c[0][0]}`" for c in singletons_sorted))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "*Audit alpha -- read-only. Do not apply without coordinator sign-off.*"
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        return 1

    con = sqlite3.connect(str(DB_PATH))
    print(f"Loading issuers from {DB_PATH}")
    issuers = load_issuers(con)
    con.close()

    total_distinct = len(issuers)
    print(f"  Distinct issuer_display values (ACTV, non-null): {total_distinct}")
    print()

    print("Clustering...")
    clusters = build_clusters(issuers)
    multi_clusters = [c for c in clusters if len(c) > 1]
    print(f"  Clusters total:       {len(clusters)}")
    print(f"  Multi-member:         {len(multi_clusters)}")
    print(f"  Singletons:           {len(clusters) - len(multi_clusters)}")
    print()

    csv_rows = generate_csv_rows(clusters)
    auto_count = sum(1 for r in csv_rows if r["confidence"] == "AUTO")
    review_count = sum(1 for r in csv_rows if r["confidence"] == "REVIEW")

    print(f"  AUTO merge proposals: {auto_count}")
    print(f"  REVIEW items:         {review_count}")
    print()

    if multi_clusters:
        print("Clusters found:")
        for cluster in sorted(multi_clusters, key=lambda c: sum(x[1] for x in c), reverse=True):
            canonical = cluster[0][0]
            variants = []
            for v, cnt in cluster[1:]:
                conf, sim = classify_variant(v, canonical)
                variants.append(f"{v} ({cnt}f, {conf} {sim:.2f})")
            print(f"  [{canonical}] -> {', '.join(variants)}")
        print()

    # Write CSV (AUTO first, then REVIEW, sorted by canonical then variant)
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["variant", "canonical", "fund_count", "similarity", "confidence"]
    ordered = sorted(
        csv_rows,
        key=lambda r: (r["confidence"] != "AUTO", r["canonical"], r["variant"]),
    )
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in ordered:
            writer.writerow(row)
    print(f"Wrote CSV:    {OUTPUT_CSV}")

    # Write Markdown report
    md_content = generate_md_report(clusters, total_distinct, auto_count, review_count)
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_MD.open("w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"Wrote report: {OUTPUT_MD}")
    print()
    print("Next steps:")
    print("  1. Review REVIEW items in docs/issuer_canonicalization_report.md")
    print("  2. Promote REVIEW -> AUTO or demote to DISTINCT as appropriate")
    print("  3. Coordinator runs apply script (out of scope for audit alpha)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
