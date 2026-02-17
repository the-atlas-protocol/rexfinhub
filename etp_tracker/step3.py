from __future__ import annotations
import re
import logging
import pandas as pd
from .sec_client import SECClient
from .utils import safe_str, is_html_doc, is_pdf_doc, norm_key
from .csvio import append_dedupe_csv
from .paths import output_paths_for_trust
from .sgml import parse_sgml_series_classes
from .body_extractors import iter_txt_documents, extract_from_html_string, extract_from_primary_html, extract_from_primary_pdf
from .manifest import (
    load_manifest, save_manifest, get_processed_accessions,
    get_retry_accessions, record_success, record_error, PIPELINE_VERSION,
)
from .ixbrl import extract_ixbrl_facts
from .config import EXTRACTION_STRATEGIES, DEFAULT_EXTRACTION_STRATEGY

log = logging.getLogger(__name__)

_TICKER_STOPWORDS = {"THE","AND","FOR","WITH","ETF","FUND","RISK","USD","MEMBER",
                     "SYMBOL","NAN","NONE","TBD","COM","INC","LLC","TRUST","DAILY","TARGET"}
def _valid_ticker(tok: str) -> bool:
    t = (tok or "").strip().upper()
    if not (2 <= len(t) <= 5): return False
    if t in _TICKER_STOPWORDS: return False
    return any(c.isalpha() for c in t)

def _extract_ticker_for_series_from_texts(series_name: str, texts: list[str]) -> tuple[str, str]:
    if not series_name: return "", ""
    s_norm = re.sub(r"\s+", " ", series_name).strip()
    s_pat = re.escape(s_norm)
    rx_paren = re.compile(fr"{s_pat}\s*\(\s*([A-Z0-9]{{1,6}})\s*\)", flags=re.IGNORECASE)
    for t in texts:
        m = rx_paren.search(t or "")
        if m:
            cand = m.group(1).upper()
            if _valid_ticker(cand): return cand, "TITLE-PAREN"
    label_rx = re.compile(r"(?i)(Ticker|Trading\s*Symbol)\s*[:\-\u2013]\s*([A-Z0-9]{1,6})")
    for t in texts:
        if not t: continue
        for m in re.finditer(s_pat, t, flags=re.IGNORECASE):
            start = max(0, m.start() - 600); end = min(len(t), m.end() + 600)
            window = t[start:end]
            lm = label_rx.search(window)
            if lm:
                cand = lm.group(2).upper()
                if _valid_ticker(cand): return cand, "LABEL-WINDOW"
    return "", ""

def _extract_effectiveness_from_hdr(txt: str) -> str:
    m = re.search(r"EFFECTIVENESS\s+DATE:\s*(\d{8})", txt or "", flags=re.IGNORECASE)
    if m:
        s = m.group(1)
        try:
            return pd.to_datetime(s, format="%Y%m%d").strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""

_DELAYING_PHRASES = [
    "delaying amendment",
    "delay its effective date",
    "delay the effective date",
    "rule 485(a)",
    "rule 473",
]

# High-confidence patterns (checkbox selections, explicit designations)
_DATE_PHRASES_HIGH_CONFIDENCE = [
    r"on\s+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})\s+pursuant\s+to\s+paragraph",
    r"designating\s+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})\s+as\s+the\s+new\s+effective\s+date",
    r"effective\s+date\s+(?:of|is)\s+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
]

# Medium-confidence patterns
_DATE_PHRASES_MEDIUM = [
    r"(?:become|becomes|shall become|will become|will be)\s+effective\s+(?:on|as of)\s+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
    r"effective\s+(?:on|as of)\s+(\d{1,2}/\d{1,2}/\d{2,4})",
    r"effective\s+on\s+or\s+about\s+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
]

def _parse_date_string(date_str: str) -> str | None:
    """Parse various date formats and return YYYY-MM-DD or None."""
    if not date_str:
        return None
    date_str = date_str.strip().replace(",", "")
    formats = [
        "%B %d %Y",    # November 7 2025
        "%B %d, %Y",   # November 7, 2025
        "%m/%d/%Y",    # 11/07/2025
        "%m/%d/%y",    # 11/07/25
        "%Y-%m-%d",    # 2025-11-07
    ]
    for fmt in formats:
        try:
            dt = pd.to_datetime(date_str, format=fmt)
            if not pd.isna(dt):
                return dt.strftime("%Y-%m-%d")
        except Exception:
            continue
    try:
        dt = pd.to_datetime(date_str, errors="coerce")
        if not pd.isna(dt):
            return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    return None

def _find_effective_date_in_text(txt: str) -> tuple[str, str, bool]:
    """Extract effective date from filing text.
    Returns: (date_str, confidence, is_delaying)
    """
    if not isinstance(txt, str) or not txt.strip():
        return "", "", False
    lower = txt.lower()
    delaying = any(p in lower for p in _DELAYING_PHRASES)
    t = re.sub(r"\s+", " ", txt)
    for pat in _DATE_PHRASES_HIGH_CONFIDENCE:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            date_str = _parse_date_string(m.group(1))
            if date_str:
                return date_str, "HIGH", delaying
    for pat in _DATE_PHRASES_MEDIUM:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            date_str = _parse_date_string(m.group(1))
            if date_str:
                return date_str, "MEDIUM", delaying
    return "", "", delaying

_NAME_JUNK_PREFIXES = re.compile(
    r"^(?:SUMMARY\s+PROSPECTUS\s+.*?TRUST\s+SUMMARY\s+PROSPECTUS\s+|"
    r"SUMMARY\s+PROSPECTUS\s+|"
    r"Prospectus\s+for\s+|"
    r"Income\s+ETF\s+|"
    r"Option\s+Strategy\s+ETF\s+)",
    re.IGNORECASE,
)

def _clean_html_fund_name(name: str) -> str:
    cleaned = _NAME_JUNK_PREFIXES.sub("", name).strip()
    return cleaned if len(cleaned) > 5 else ""

def _extract_fund_names_from_html(html_text: str) -> list[str]:
    if not html_text:
        return []
    raw_names = []
    patterns = [
        r"([A-Z][A-Za-z0-9\s\-\.]+(?:ETF|Fund|Trust))",
        r"T-REX\s+[A-Z0-9][A-Za-z0-9\s\-\.]+(?:ETF|Fund)",
        r"Tuttle\s+Capital\s+[A-Za-z0-9\s\-\.]+(?:ETF|Fund)",
        r"REX\s+[A-Za-z0-9\s\-\.]+(?:ETF|Fund)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, html_text):
            name = re.sub(r"\s+", " ", m.group(0)).strip()
            if len(name) > 10 and name not in raw_names:
                raw_names.append(name)
    names = []
    for raw in raw_names:
        cleaned = _clean_html_fund_name(raw)
        if not cleaned:
            continue
        if re.search(r"\b(?:ETF|Fund)\s+and\s+", cleaned, re.IGNORECASE):
            continue
        if cleaned not in names:
            names.append(cleaned)
    return names[:50]

def _find_prospectus_name_for_sgml(sgml_name: str, html_names: list[str]) -> str:
    if not sgml_name or not html_names:
        return ""
    sgml_norm = re.sub(r"\s+", " ", sgml_name).strip().upper()
    sgml_tokens = set(re.findall(r"[A-Z0-9]+", sgml_norm))
    sgml_tokens -= {"ETF", "FUND", "TRUST", "THE", "AND", "FOR", "WITH", "DAILY", "TARGET", "CAPITAL"}
    best_match = ""
    best_score = 0
    for html_name in html_names:
        html_norm = re.sub(r"\s+", " ", html_name).strip().upper()
        if sgml_norm == html_norm:
            continue
        html_tokens = set(re.findall(r"[A-Z0-9]+", html_norm))
        html_tokens -= {"ETF", "FUND", "TRUST", "THE", "AND", "FOR", "WITH", "DAILY", "TARGET", "CAPITAL"}
        if not sgml_tokens or not html_tokens:
            continue
        overlap = len(sgml_tokens & html_tokens)
        total = len(sgml_tokens | html_tokens)
        score = overlap / total if total > 0 else 0
        if score >= 0.5 and score > best_score:
            best_score = score
            best_match = html_name
    return best_match


# ---------------------------------------------------------------------------
# Strategy: header_only  (485BXT, 497J - fast, ~2KB read from cache)
# ---------------------------------------------------------------------------
def _extract_header_only(client: SECClient, txt_url: str, form: str,
                         filing_dt: str, cik: str, registrant: str,
                         accession: str, prim_url: str) -> list[dict]:
    """Parse SGML header only. Used for 485BXT and 497J."""
    txt_header = ""
    try:
        if txt_url:
            txt_header = client.fetch_header_text(txt_url)
    except Exception:
        txt_header = ""

    sgml_rows = parse_sgml_series_classes(txt_header) if txt_header else []
    eff_date = _extract_effectiveness_from_hdr(txt_header) if txt_header else ""
    eff_confidence = "HEADER" if eff_date else ""

    # For 485BXT, also try to find effective date in the header text via regex
    if not eff_date and txt_header:
        ed, conf, _ = _find_effective_date_in_text(txt_header)
        if ed:
            eff_date = ed
            eff_confidence = conf

    rows = []
    if sgml_rows:
        for base in sgml_rows:
            row = dict(base)
            row.update({
                "Form": form, "Filing Date": filing_dt, "Accession Number": accession,
                "Primary Link": prim_url, "Full Submission TXT": txt_url,
                "Registrant": registrant, "CIK": cik,
                "Effective Date": eff_date,
                "Effective Date Confidence": eff_confidence,
                "Delaying Amendment": "",
                "Prospectus Name": "",
                "Extraction Strategy": "header_only",
            })
            rows.append(row)
    else:
        rows.append({
            "Series ID": "", "Series Name": "",
            "Class-Contract ID": "", "Class Contract Name": "", "Class Symbol": "",
            "Form": form, "Filing Date": filing_dt, "Accession Number": accession,
            "Primary Link": prim_url, "Full Submission TXT": txt_url,
            "Registrant": registrant, "CIK": cik,
            "Extracted From": "NONE",
            "Effective Date": eff_date,
            "Effective Date Confidence": eff_confidence,
            "Delaying Amendment": "",
            "Prospectus Name": "",
            "Extraction Strategy": "header_only",
        })
    return rows


# ---------------------------------------------------------------------------
# Strategy: full  (485BPOS, 485APOS, 497, 497K - SGML + body + iXBRL)
# ---------------------------------------------------------------------------
def _extract_full(client: SECClient, txt_url: str, form: str,
                  filing_dt: str, cik: str, registrant: str,
                  accession: str, prim_url: str, is_ixbrl: bool) -> list[dict]:
    """Full extraction: SGML header + body text + optional iXBRL enrichment."""
    txt_text = ""
    try:
        if txt_url:
            txt_text = client.fetch_text(txt_url)
    except Exception:
        txt_text = ""

    sgml_rows = parse_sgml_series_classes(txt_text) if txt_text else []

    # --- Effective date extraction cascade ---
    eff_date_col = _extract_effectiveness_from_hdr(txt_text) if txt_text else ""
    eff_confidence = "HEADER" if eff_date_col else ""
    delaying = False
    strategy_name = "full"

    # iXBRL enrichment: try structured OEF tags first (highest confidence)
    ixbrl_data = {}
    if is_ixbrl and prim_url and is_html_doc(prim_url):
        try:
            _, prim_html = extract_from_primary_html(client, prim_url)
            if prim_html:
                ixbrl_data = extract_ixbrl_facts(prim_html)
        except Exception:
            pass

    if ixbrl_data.get("prospectus_date"):
        parsed = _parse_date_string(ixbrl_data["prospectus_date"])
        if parsed:
            eff_date_col = parsed
            eff_confidence = "IXBRL"
            strategy_name = "full+ixbrl"

    # Regex cascade (skipped if iXBRL already gave us a date)
    if eff_confidence != "IXBRL" and txt_text:
        ed_txt, conf_txt, delay_txt = _find_effective_date_in_text(txt_text)
        if ed_txt and (not eff_date_col or conf_txt == "HIGH"):
            eff_date_col = ed_txt
            eff_confidence = conf_txt
        delaying = delay_txt

    # Collect body texts for ticker search and fund name matching
    all_plain_texts: list[str] = [txt_text] if txt_text else []
    html_fund_names: list[str] = []

    if txt_text:
        for doctype, fname, body_html in iter_txt_documents(txt_text):
            if doctype.upper().startswith(("485A", "485B", "497")):
                _, html_plain2 = extract_from_html_string(body_html)
                if html_plain2:
                    all_plain_texts.append(html_plain2)
                    html_fund_names.extend(_extract_fund_names_from_html(html_plain2))
                    if not eff_date_col or eff_confidence not in ("HIGH", "HEADER", "IXBRL"):
                        ed2, conf2, d2 = _find_effective_date_in_text(html_plain2)
                        if ed2 and (not eff_date_col or conf2 == "HIGH"):
                            eff_date_col = ed2
                            eff_confidence = conf2
                        delaying = delaying or d2

    if is_html_doc(prim_url):
        _, html_plain = extract_from_primary_html(client, prim_url)
        if html_plain:
            all_plain_texts.append(html_plain)
            html_fund_names.extend(_extract_fund_names_from_html(html_plain))
            if not eff_date_col or eff_confidence not in ("HIGH", "HEADER", "IXBRL"):
                ed_h, conf_h, d_h = _find_effective_date_in_text(html_plain)
                if ed_h and (not eff_date_col or conf_h == "HIGH"):
                    eff_date_col = ed_h
                    eff_confidence = conf_h
                delaying = delaying or d_h

    if is_pdf_doc(prim_url):
        _, pdf_plain = extract_from_primary_pdf(client, prim_url)
        if pdf_plain:
            all_plain_texts.append(pdf_plain)
            html_fund_names.extend(_extract_fund_names_from_html(pdf_plain))
            if not eff_date_col or eff_confidence not in ("HIGH", "HEADER", "IXBRL"):
                ed_p, conf_p, d_p = _find_effective_date_in_text(pdf_plain)
                if ed_p and (not eff_date_col or conf_p == "HIGH"):
                    eff_date_col = ed_p
                    eff_confidence = conf_p
                delaying = delaying or d_p

    # Build output rows
    rows: list[dict] = []
    if sgml_rows:
        for base in sgml_rows:
            nm = base.get("Class Contract Name") or base.get("Series Name") or ""
            tkr, tkr_src = _extract_ticker_for_series_from_texts(nm, all_plain_texts)
            row = dict(base)
            if tkr:
                row["Class Symbol"] = tkr
                src = row.get("Extracted From") or "SGML-TXT"
                row["Extracted From"] = f"{src}|{tkr_src}"
            prospectus_name = _find_prospectus_name_for_sgml(nm, html_fund_names)
            row.update({
                "Form": form, "Filing Date": filing_dt, "Accession Number": accession,
                "Primary Link": prim_url, "Full Submission TXT": txt_url,
                "Registrant": registrant, "CIK": cik,
                "Effective Date": eff_date_col,
                "Effective Date Confidence": eff_confidence,
                "Delaying Amendment": "Y" if delaying else "",
                "Prospectus Name": prospectus_name,
                "Extraction Strategy": strategy_name,
            })
            rows.append(row)
    else:
        rows.append({
            "Series ID": "", "Series Name": "",
            "Class-Contract ID": "", "Class Contract Name": "", "Class Symbol": "",
            "Form": form, "Filing Date": filing_dt, "Accession Number": accession,
            "Primary Link": prim_url, "Full Submission TXT": txt_url,
            "Registrant": registrant, "CIK": cik,
            "Extracted From": "NONE",
            "Effective Date": eff_date_col,
            "Effective Date Confidence": eff_confidence,
            "Delaying Amendment": "Y" if delaying else "",
            "Prospectus Name": "",
            "Extraction Strategy": strategy_name,
        })
    return rows


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def step3_extract_for_trust(client: SECClient, output_root, trust_name: str,
                            since: str | None = None, until: str | None = None,
                            forms: list[str] | None = None) -> dict:
    """Extract fund data from prospectus filings for a single trust.

    Returns dict with metrics:
        new: int, skipped: int, errors: int, strategies: dict
    """
    metrics = {"new": 0, "skipped": 0, "errors": 0, "strategies": {}}

    paths = output_paths_for_trust(output_root, trust_name)
    p2 = paths["prospectus_base"]; p3 = paths["extracted_funds"]
    if not p2.exists() or p2.stat().st_size == 0:
        return metrics
    try:
        df2 = pd.read_csv(p2, dtype=str)
    except pd.errors.EmptyDataError:
        return metrics
    if df2.empty:
        return metrics

    if since or until or forms:
        d2 = df2.copy()
        d2["_fdt"] = pd.to_datetime(d2.get("Filing Date", ""), errors="coerce")
        if since: d2 = d2[d2["_fdt"] >= pd.to_datetime(since, errors="coerce")]
        if until: d2 = d2[d2["_fdt"] <= pd.to_datetime(until, errors="coerce")]
        if forms:
            upp = d2.get("Form", pd.Series("", index=d2.index)).fillna("").str.upper()
            d2 = d2[upp.str.startswith(tuple([f.upper() for f in forms]))]
        df2 = d2.drop(columns=["_fdt"], errors="ignore")

    # --- Incremental processing: skip already-processed filings ---
    trust_folder = paths["folder"]
    manifest = load_manifest(trust_folder)
    already_done = get_processed_accessions(manifest)
    retry_set = get_retry_accessions(manifest)

    total_before = len(df2)
    # Keep filings that are: not yet processed OR need retry
    df2 = df2[
        ~df2["Accession Number"].isin(already_done)
        | df2["Accession Number"].isin(retry_set)
    ]
    metrics["skipped"] = total_before - len(df2)

    if df2.empty:
        return metrics

    log.info(
        "%s: %d new filings to process (skipping %d already processed)",
        trust_name, len(df2), metrics["skipped"],
    )

    rows_out: list[dict] = []

    for _, r in df2.iterrows():
        form      = safe_str(r.get("Form", ""))
        filing_dt = safe_str(r.get("Filing Date", ""))
        cik       = safe_str(r.get("CIK", ""))
        registrant = safe_str(r.get("Registrant", ""))
        accession = safe_str(r.get("Accession Number", ""))
        prim_url  = safe_str(r.get("Primary Link", ""))
        txt_url   = safe_str(r.get("Full Submission TXT", ""))
        is_ixbrl  = safe_str(r.get("isInlineXBRL", "0")) == "1"
        if (form or "").strip().upper() == "EFFECT":
            record_success(manifest, accession, form, 0)
            continue

        # Route to extraction strategy
        form_upper = (form or "").strip().upper()
        strategy = EXTRACTION_STRATEGIES.get(form_upper, DEFAULT_EXTRACTION_STRATEGY)

        try:
            if strategy == "header_only":
                extracted_rows = _extract_header_only(
                    client, txt_url, form, filing_dt, cik, registrant, accession, prim_url,
                )
            else:
                extracted_rows = _extract_full(
                    client, txt_url, form, filing_dt, cik, registrant, accession, prim_url, is_ixbrl,
                )

            rows_out.extend(extracted_rows)
            record_success(manifest, accession, form, len(extracted_rows))
            metrics["new"] += 1

            # Track which strategy was used
            strat_label = "header_only" if strategy == "header_only" else (
                "full+ixbrl" if is_ixbrl else "full"
            )
            metrics["strategies"][strat_label] = metrics["strategies"].get(strat_label, 0) + 1

        except Exception as exc:
            record_error(manifest, accession, form, str(exc))
            metrics["errors"] += 1
            log.warning(
                "%s: Error extracting %s (%s): %s",
                trust_name, accession, form, exc,
            )

    # Save manifest after processing all filings for this trust
    save_manifest(trust_folder, manifest)

    if not rows_out:
        return metrics

    df_new = pd.DataFrame(rows_out)
    for col in [
        "Series ID", "Series Name", "Class-Contract ID", "Class Contract Name", "Class Symbol",
        "Form", "Filing Date", "Accession Number", "Primary Link", "Full Submission TXT",
        "Registrant", "CIK", "Extracted From", "Effective Date", "Effective Date Confidence",
        "Delaying Amendment", "Prospectus Name", "Extraction Strategy",
    ]:
        if col not in df_new.columns:
            df_new[col] = ""

    df_new["__key"] = (
        df_new["Accession Number"].fillna("") + "|" +
        df_new["Class-Contract ID"].fillna("") + "|" +
        df_new["Class Contract Name"].fillna("") + "|" +
        df_new["Class Symbol"].fillna("")
    )
    df_new = df_new.drop_duplicates(subset=["__key"], keep="last").drop(columns=["__key"])
    append_dedupe_csv(paths["extracted_funds"], df_new,
                      key_cols=["Accession Number", "Class-Contract ID", "Class Contract Name", "Class Symbol"])
    return metrics
