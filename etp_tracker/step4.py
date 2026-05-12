from __future__ import annotations
import re
import pandas as pd
from datetime import datetime
from .paths import output_paths_for_trust
from .utils import clean_fund_name_for_rollup

_BAD_TICKERS = {"SYMBOL", "NAN", "N/A", "NA", "NONE", "TBD", ""}

def _determine_status(row: pd.Series) -> tuple[str, str]:
    """
    Determine fund status based on filing type and dates.

    Returns: (status, status_reason)
        Status values:
        - EFFECTIVE: Fund has launched (485BPOS filed)
        - PENDING: Initial filing, waiting for effectiveness
        - DELAYED: Has delaying amendment
        - UNKNOWN: Cannot determine
    """
    form = str(row.get("Form", "")).upper()
    eff_date = str(row.get("Effective Date", "")).strip()
    delaying = str(row.get("Delaying Amendment", "")).upper() == "Y"
    filing_date = str(row.get("Filing Date", "")).strip()

    # Parse effective date if present
    eff_dt = None
    if eff_date:
        try:
            eff_dt = pd.to_datetime(eff_date, errors="coerce")
            if pd.isna(eff_dt):
                eff_dt = None
        except Exception:
            pass

    today = datetime.now()

    # 485BPOS = Post-effective amendment (fund is trading)
    if form.startswith("485B") and "POS" in form:
        return "EFFECTIVE", "485BPOS filed (fund trading)"

    # 485BXT = Extension of time with new effective date
    if form.startswith("485B") and "XT" in form:
        if delaying:
            return "DELAYED", "485BXT with delaying amendment"
        if eff_dt:
            if eff_dt.date() <= today.date():
                return "EFFECTIVE", f"485BXT effective as of {eff_date}"
            else:
                return "PENDING", f"485BXT effective date {eff_date} is future"
        # 485BXT extensions are typically 45-120 days from filing
        # If 150+ days have passed, the extension has elapsed and fund is effective
        if filing_date:
            try:
                fdt = pd.to_datetime(filing_date, errors="coerce")
                if not pd.isna(fdt):
                    extension_deadline = fdt + pd.Timedelta(days=150)
                    if extension_deadline.date() <= today.date():
                        return "EFFECTIVE", "485BXT presumed effective (extension period elapsed)"
            except Exception:
                pass
        return "PENDING", "485BXT filed (awaiting effectiveness)"

    # 485APOS = Initial filing (post-effective amendment FORM but used for
    # both new funds AND material changes to existing funds).
    #
    # SEC Rule 485(a) effectiveness clock:
    #   - 75 days for a NEW fund (initial registration via 485APOS)
    #   - 60 days for a MATERIAL CHANGE to an existing effective fund
    # Default here is 75 days because REX overwhelmingly files 485APOS for
    # new-fund launches; the 60-day material-change path requires parsing
    # the filing text (cover page wording or item 9 cross-reference) and
    # is not implemented yet. When/if filing-text parsing lands, branch
    # on the parsed signal and use 60 days for material-change rows.
    if form.startswith("485A"):
        if delaying:
            return "DELAYED", "485APOS with delaying amendment"
        if eff_dt:
            if eff_dt.date() <= today.date():
                return "EFFECTIVE", f"485APOS effective as of {eff_date}"
            else:
                return "PENDING", f"485APOS effective date {eff_date} is future"
        # Default: 75 days from filing (new-fund 485APOS clock).
        if filing_date:
            try:
                fdt = pd.to_datetime(filing_date, errors="coerce")
                if not pd.isna(fdt):
                    default_eff = fdt + pd.Timedelta(days=75)
                    if default_eff.date() <= today.date():
                        return "EFFECTIVE", f"485APOS presumed effective (+75 days)"
                    else:
                        return "PENDING", f"485APOS +75 day period not elapsed"
            except Exception:
                pass
        return "PENDING", "485APOS filed (awaiting effectiveness)"

    # 497/497K = Supplement (fund must already be effective to file these)
    if form.startswith("497"):
        return "EFFECTIVE", "497/497K filed (fund is trading)"

    # POS AM = Post-effective amendment (33 Act annual renewal, trust is trading)
    if form == "POS AM":
        return "EFFECTIVE", "POS AM filed (post-effective amendment)"

    # S-1 = Registration statement (33 Act filers: crypto, commodity, volatility)
    if form.startswith("S-1"):
        if eff_dt and eff_dt.date() <= today.date():
            return "EFFECTIVE", f"S-1 effective as of {eff_date}"
        # Ticker assignment = SEC approved the registration (strong effectiveness signal)
        ticker_val = str(row.get("Class Symbol", "")).strip().upper()
        if ticker_val and ticker_val not in _BAD_TICKERS and len(ticker_val) >= 2:
            return "EFFECTIVE", "S-1 effective (ticker assigned)"
        return "PENDING", "S-1 filed (registration pending)"

    # S-3 = Post-effective registration (already effective issuer)
    if form.startswith("S-3"):
        return "EFFECTIVE", "S-3 filed (registered offering)"

    # EFFECT = SEC effectiveness notice (confirms registration is effective)
    if form == "EFFECT":
        return "EFFECTIVE", "EFFECT notice (SEC confirmed effective)"

    return "UNKNOWN", f"Unrecognized form type: {form}"


def step4_rollup_for_trust(output_root, trust_name: str) -> int:
    """
    Roll up extracted fund data to show current status of each fund.

    Output columns (simplified):
    - Series ID: SEC permanent identifier
    - Fund Name: Current canonical name
    - Ticker: Trading symbol (if known)
    - Trust: Trust name (registrant)
    - Status: PENDING | EFFECTIVE | DELAYED
    - Effective Date: When fund became/becomes effective
    - Latest Form: Most recent filing type
    - Prospectus Link: Link to latest prospectus
    - Status Reason: Explanation of status determination
    """
    paths = output_paths_for_trust(output_root, trust_name)
    p3 = paths["extracted_funds"]
    p4 = paths["latest_record"]

    if not p3.exists() or p3.stat().st_size == 0:
        return 0

    df = pd.read_csv(p3, dtype=str, on_bad_lines="skip", engine="python")
    if df.empty:
        return 0

    # Parse filing date for sorting
    df["_fdt"] = pd.to_datetime(df.get("Filing Date", ""), errors="coerce")
    df = df.sort_values("_fdt", ascending=True)

    # Build grouping key (prefer Class-Contract ID, then Series ID, then name+ticker)
    class_id = df.get("Class-Contract ID", pd.Series("", index=df.index)).fillna("")
    series_id = df.get("Series ID", pd.Series("", index=df.index)).fillna("")
    name_col = df.get("Class Contract Name", pd.Series("", index=df.index)).fillna("")
    name_col = name_col.mask(name_col == "", df.get("Series Name", pd.Series("", index=df.index)).fillna(""))
    ticker_col = df.get("Class Symbol", pd.Series("", index=df.index)).fillna("").str.upper()

    # Create grouping key
    df["__gkey"] = class_id.mask(class_id == "", series_id)
    df.loc[df["__gkey"] == "", "__gkey"] = name_col + "|" + ticker_col

    results = []

    for gkey, group in df.groupby("__gkey", dropna=False):
        g = group.sort_values("_fdt", ascending=True)

        # Get latest record for each form type (40 Act + 33 Act)
        forms_up = g["Form"].fillna("").str.upper()
        g_bpos = g[forms_up.str.contains("485B", na=False)]
        g_posam = g[forms_up == "POS AM"]
        g_effect = g[forms_up == "EFFECT"]
        g_s3 = g[forms_up.str.startswith("S-3", na=False)]
        g_497 = g[forms_up.str.startswith("497", na=False)]
        g_apos = g[forms_up.str.startswith("485A", na=False)]
        g_s1 = g[forms_up.str.startswith("S-1", na=False)]

        # Pick the most authoritative latest filing
        # 40 Act: 485BPOS > 497 > 485APOS
        # 33 Act: EFFECT > POS AM > S-3 > S-1
        if not g_bpos.empty:
            latest = g_bpos.iloc[-1]
        elif not g_effect.empty:
            latest = g_effect.iloc[-1]
        elif not g_posam.empty:
            latest = g_posam.iloc[-1]
        elif not g_s3.empty:
            latest = g_s3.iloc[-1]
        elif not g_497.empty:
            latest = g_497.iloc[-1]
        elif not g_apos.empty:
            latest = g_apos.iloc[-1]
        elif not g_s1.empty:
            latest = g_s1.iloc[-1]
        else:
            latest = g.iloc[-1]

        # Determine status
        status, status_reason = _determine_status(latest)

        # Get best available values
        series_id_val = g["Series ID"].dropna().iloc[-1] if not g["Series ID"].dropna().empty else ""
        class_id_val = g["Class-Contract ID"].dropna().iloc[-1] if "Class-Contract ID" in g.columns and not g["Class-Contract ID"].dropna().empty else ""

        # Fund Name: Use SGML name (authoritative SEC-registered name)
        raw_name = g["Class Contract Name"].fillna("").iloc[-1]
        if not raw_name:
            raw_name = g["Series Name"].fillna("").iloc[-1]
        canonical_name = clean_fund_name_for_rollup(raw_name)

        # Keep prospectus name for reference only
        prospectus_name = ""
        if "Prospectus Name" in g.columns:
            pn = g["Prospectus Name"].dropna()
            pn = pn[pn != ""]
            if not pn.empty:
                prospectus_name = pn.iloc[-1]

        # Clean ticker: filter out placeholder values and single-char junk
        ticker = g["Class Symbol"].fillna("").str.upper().str.strip()
        ticker = ticker[~ticker.isin(_BAD_TICKERS)]
        ticker = ticker[ticker.str.len() >= 2]
        ticker = ticker.iloc[-1] if not ticker.empty else ""

        registrant = g["Registrant"].fillna("").iloc[-1] if "Registrant" in g.columns else trust_name
        cik = g["CIK"].fillna("").iloc[-1] if "CIK" in g.columns else ""

        eff_date = str(latest.get("Effective Date", "")).strip()
        eff_confidence = str(latest.get("Effective Date Confidence", "")).strip() if "Effective Date Confidence" in latest.index else ""

        # Prospectus Link: prefer most authoritative filing
        # 40 Act: 485BPOS > 485APOS | 33 Act: POS AM > S-3 > S-1
        prosp_link = ""
        forms_upper = g["Form"].fillna("").str.upper()
        # 485BPOS (not 485BXT)
        g_bpos_l = g[forms_upper.str.contains("485B", na=False) & ~forms_upper.str.contains("BXT", na=False)]
        if not g_bpos_l.empty:
            prosp_link = str(g_bpos_l.iloc[-1].get("Primary Link", ""))
        # POS AM (33 Act annual renewal)
        if not prosp_link:
            g_posam_l = g[forms_upper == "POS AM"]
            if not g_posam_l.empty:
                prosp_link = str(g_posam_l.iloc[-1].get("Primary Link", ""))
        # S-3 (33 Act shelf registration)
        if not prosp_link:
            g_s3_l = g[forms_upper.str.startswith("S-3")]
            if not g_s3_l.empty:
                prosp_link = str(g_s3_l.iloc[-1].get("Primary Link", ""))
        # 485APOS
        if not prosp_link:
            g_apos_l = g[forms_upper.str.startswith("485A")]
            if not g_apos_l.empty:
                prosp_link = str(g_apos_l.iloc[-1].get("Primary Link", ""))
        # S-1
        if not prosp_link:
            g_s1_l = g[forms_upper.str.startswith("S-1")]
            if not g_s1_l.empty:
                prosp_link = str(g_s1_l.iloc[-1].get("Primary Link", ""))
        # Final fallback: latest filing link
        if not prosp_link:
            prosp_link = str(latest.get("Primary Link", ""))

        results.append({
            "Series ID": series_id_val,
            "Class-Contract ID": class_id_val,
            "Fund Name": canonical_name,
            "SGML Name": raw_name,
            "Prospectus Name": prospectus_name,
            "Ticker": ticker,
            "Trust": registrant,
            "CIK": cik,
            "Status": status,
            "Status Reason": status_reason,
            "Effective Date": eff_date,
            "Effective Date Confidence": eff_confidence,
            "Latest Form": str(latest.get("Form", "")),
            "Latest Filing Date": str(latest.get("Filing Date", "")),
            "Prospectus Link": prosp_link,
        })

    if not results:
        return 0

    roll = pd.DataFrame(results)

    # Sort by trust, status, then name
    status_order = {"PENDING": 0, "DELAYED": 1, "EFFECTIVE": 2, "UNKNOWN": 3}
    roll["_status_sort"] = roll["Status"].map(status_order).fillna(3)
    roll = roll.sort_values(["Trust", "_status_sort", "Fund Name"], ascending=[True, True, True])
    roll = roll.drop(columns=["_status_sort"])

    # Deduplicate by Series ID + Ticker
    roll["_dedup_key"] = roll["Series ID"].fillna("") + "|" + roll["Ticker"].fillna("")
    roll = roll.drop_duplicates(subset=["_dedup_key"], keep="last")
    roll = roll.drop(columns=["_dedup_key"])

    roll.to_csv(p4, index=False)
    return len(roll)
