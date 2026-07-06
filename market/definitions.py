"""REX ETP DEFINITION LIBRARY — the single source of truth.

Every fact about *how we define the ETP landscape* lives here and nowhere else.
Reports, the website, the pipeline, and QC all import these definitions so the
same question never gets two answers. The human-readable contract that mirrors
this module is docs/DEFINITIONS.md — `test_definitions.py` asserts the two agree.

Two views of the world (they can collide — that's intentional):

  INTERNAL  — REX's own products, grouped into our nine SUITES. Suite membership
              is a REX marketing/strategy concept, not a market taxonomy. e.g.
              "MicroSectors" = our index/basket L&I; "Growth & Income" = our
              income-on-single-stocks. Two suites can map to the same external
              class, and one external class can split across suites.

  EXTERNAL  — the whole ETP market classified by an objective taxonomy
              (Leveraged & Inverse, Income/Covered-Call, Crypto, Defined-Outcome,
              Thematic, ...) with a single-stock-vs-index axis. This is how we see
              competitors. Lives in the classification system (auto_classify +
              fund_master.csv); this library names the link, it doesn't restate
              the whole taxonomy.

The collision example, made concrete:
  - T-REX (internal suite) and MicroSectors (internal suite) are BOTH externally
    "Leveraged & Inverse". The axis that splits them is single-stock (T-REX) vs
    index/basket (MicroSectors).
  - "Growth & Income" (internal suite) is externally Income/Covered-Call, but
    specifically the single-stock kind.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ===========================================================================
# INTERNAL SUITES — REX's own product suites (the nine, in canonical order)
# ===========================================================================
@dataclass(frozen=True)
class Suite:
    key: str            # stable identifier (never displayed)
    display: str        # canonical name — THIS is the value stored in rex_suite
    abbr: str           # short label, or "" if none
    external_class: str # how this suite maps into the external taxonomy
    definition: str     # one-line human definition
    flagship: str       # an example/representative ticker (no " US")


INTERNAL_SUITES: list[Suite] = [
    Suite("microsectors", "MicroSectors", "",
          "Leveraged & Inverse / Index-Basket",
          "REX's index- and basket-based leverage & inverse suite (BMO-issued ETNs, REX-branded).",
          "FNGU"),
    Suite("t-rex", "T-REX", "",
          "Leveraged & Inverse / Single-Stock",
          "REX's single-stock leverage & inverse suite (2X Long and 2X Short on one underlier).",
          "TSLT"),
    Suite("epi", "Equity Premium Income", "EPI",
          "Income / Covered-Call (index/basket)",
          "REX equity premium income — covered-call income on indices and baskets.",
          "FEPI"),
    Suite("growth_income", "Growth & Income", "G&I",
          "Income / Covered-Call (single-stock)",
          "REX income products written on single stocks.",
          "AAPY"),
    Suite("incomemax", "IncomeMax", "",
          "Income / Option-Strategy",
          "REX option-strategy income (the IncomeMax / ULTI product line).",
          "ULTI"),
    Suite("structured", "Structured", "",
          "Structured / Autocallable",
          "REX structured products — autocallable income (ATCL).",
          "ATCL"),
    Suite("thematic", "Thematic", "",
          "Thematic Equity",
          "REX thematic equity. Currently only the Drone ETF (DRNZ).",
          "DRNZ"),
    Suite("crypto", "Crypto", "",
          "Crypto (spot / staking)",
          "REX-Osprey crypto spot and staking products.",
          "SSK"),
    Suite("moneymarket", "MoneyMarket", "",
          "Money Market / T-Bill",
          "REX laddered T-bill / money-market product (TLDR).",
          "TLDR"),
]

SUITE_BY_KEY: dict[str, Suite] = {s.key: s for s in INTERNAL_SUITES}
SUITE_BY_DISPLAY: dict[str, Suite] = {s.display: s for s in INTERNAL_SUITES}
SUITE_DISPLAYS: list[str] = [s.display for s in INTERNAL_SUITES]


# --- The classifier: fund -> internal suite (or None if not a REX suite fund) --
# Curated ticker overrides for OUR funds whose NAME does not encode the suite.
# Keep this tiny — it exists only for genuine name-rule gaps, not as a dumping
# ground. (TLDR is "THE LADDERED T-BILL ETF" — no "REX" prefix to key on.)
_SUITE_TICKER_OVERRIDES: dict[str, str] = {
    "TLDR": "MoneyMarket",
}


def _norm_ticker(ticker: str | None) -> str:
    if not ticker:
        return ""
    return str(ticker).upper().strip().split()[0]  # "TLDR US" -> "TLDR"


def suite_of(ticker: str | None, fund_name: str | None) -> str | None:
    """Canonical internal-suite classifier. Returns the suite *display* name
    (the value stored in mkt_master_data.rex_suite) or None.

    Order: curated ticker override (our edge funds) -> name rules
    (self-maintaining: a fund that launches tomorrow classifies with no edit).
    Patterns are REX-name-anchored so they can never tag a competitor."""
    t = _norm_ticker(ticker)
    if t in _SUITE_TICKER_OVERRIDES:
        return _SUITE_TICKER_OVERRIDES[t]
    return suite_for_name(fund_name)


def suite_for_name(fund_name: str | None) -> str | None:
    """Name-only suite classifier. Mirrors suite_of's name rules; kept as a
    separate entry for callers that only have the fund name."""
    if not fund_name:
        return None
    n = str(fund_name).upper()
    if "T-REX" in n:  # incl. co-brands e.g. ROUNDHILL T-REX (RAM); T-REX is REX-exclusive
        return "T-REX"
    if n.startswith("MICROSECTORS"):
        return "MicroSectors"
    if n.startswith("REX-OSPREY"):
        return "Crypto"
    if n.startswith("REX"):
        if "GROWTH & INCOME" in n:
            return "Growth & Income"
        if "PREMIUM INCOME" in n:
            return "Equity Premium Income"
        if "INCOMEMAX" in n:
            return "IncomeMax"
        if "AUTOCALL" in n:
            return "Structured"
        if "DRONE" in n:
            return "Thematic"
    return None


def attach_rex_suite(df) -> None:
    """Set df['rex_suite'] canonically, in place.

    Priority: suite_of(ticker, fund_name) — the authoritative, self-maintaining
    source — then any value already on the row, then the rex_suite_mapping.csv
    fallback (for funds the library can't classify). A library hit always wins,
    so a stale stored/CSV value can never undercount a suite again (the 40-vs-41
    T-REX drift).

    Deliberately import-light (pandas only, lazily; CSV fallback is best-effort)
    so report builders can call it in ANY environment — unlike the old
    data_engine._join_rex_suite, which was unreachable wherever the Bloomberg
    Graph file was absent because importing that module raised at module load."""
    import pandas as pd

    n = len(df)
    if "fund_name" in df.columns:
        tick = df["ticker"] if "ticker" in df.columns else [None] * n
        name_suite = pd.Series(
            [suite_of(t, nm) for t, nm in zip(tick, df["fund_name"])], index=df.index
        )
    else:
        name_suite = pd.Series([None] * n, index=df.index)

    prior = df["rex_suite"] if "rex_suite" in df.columns else pd.Series([None] * n, index=df.index)

    csv_suite = pd.Series([None] * n, index=df.index)
    try:
        from market.config import RULES_DIR
        csv_path = RULES_DIR / "rex_suite_mapping.csv"
        if csv_path.exists() and "ticker" in df.columns:
            m = pd.read_csv(csv_path, engine="python", on_bad_lines="skip")
            if "ticker" in m.columns and "rex_suite" in m.columns:
                lut = dict(zip(m["ticker"], m["rex_suite"]))
                csv_suite = df["ticker"].map(lut)
    except Exception:
        pass  # CSV is a fallback only; name derivation is the source of truth

    df.drop(columns=["rex_suite"], errors="ignore", inplace=True)
    resolved = name_suite.where(name_suite.notna(), prior)
    resolved = resolved.where(resolved.notna(), csv_suite)
    df["rex_suite"] = resolved


# ===========================================================================
# MARKET STATUS — what a status means for a KPI (the rule Ryu spelled out)
# ===========================================================================
# Daily Bloomberg files carry many statuses. For a PRESENT-DAY KPI:
#   - ACTV  -> live. Counted in counts, AUM, and flows at its real value.
#   - PEND  -> future product (may launch). EXCLUDED from every KPI — it is not
#              a product yet. (It belongs in the pipeline/launch views only.)
#   - all others (LIQU, DLST, ACQU, INAC, EXPD, TKCH, UNLS, PRNA, ...) -> closed.
#              The fund is gone. Present-day AUM and flow are $0 even if Bloomberg
#              still prints a stale value. Its history stays real (a time series
#              keeps the past values; only "today" is zero).
LIVE_STATUSES: frozenset[str] = frozenset({"ACTV"})
FUTURE_STATUSES: frozenset[str] = frozenset({"PEND"})
# Closed is defined by exclusion (anything not live and not future), so a new
# closure code we have never seen still behaves correctly (present-day = $0).
KNOWN_CLOSED_STATUSES: frozenset[str] = frozenset(
    {"LIQU", "DLST", "ACQU", "INAC", "EXPD", "TKCH", "UNLS", "PRNA"}
)


def status_role(status: str | None) -> str:
    """One of 'live' | 'future' | 'closed'."""
    s = (status or "").upper().strip()
    if s in LIVE_STATUSES:
        return "live"
    if s in FUTURE_STATUSES:
        return "future"
    return "closed"


def counts_in_live_kpi(status: str | None) -> bool:
    """True only for funds that belong in a present-day KPI count (ACTV)."""
    return status_role(status) == "live"


# Canonical DISPLAY status — the ONLY statuses allowed in ANY report (Ryu, repeated):
# Filed, Effective, Listed, Liquidated, Delisted, and Under Consideration (pipeline
# ideas only). NO 'Pending', NO 'Delayed', nothing else. Every status string shown to
# a user MUST route through canonical_status().
ALLOWED_DISPLAY_STATUSES: frozenset[str] = frozenset(
    {"Filed", "Effective", "Listed", "Liquidated", "Delisted", "Under Consideration"}
)
_STATUS_DISPLAY: dict[str, str] = {
    "PENDING": "Filed", "PEND": "Filed", "FILED": "Filed",
    "DELAYED": "Filed", "DELAY": "Filed", "DELAYING": "Filed", "DELAYING AMENDMENT": "Filed",
    "EFFECTIVE": "Effective", "EFF": "Effective",
    "LISTED": "Listed", "ACTV": "Listed", "ACTIVE": "Listed", "LIVE": "Listed", "TRADING": "Listed",
    "LIQU": "Liquidated", "LIQUIDATED": "Liquidated",
    "DLST": "Delisted", "DELISTED": "Delisted", "ACQU": "Delisted", "INAC": "Delisted",
    "EXPD": "Delisted", "TKCH": "Delisted", "UNLS": "Delisted", "PRNA": "Delisted",
    "UNDER CONSIDERATION": "Under Consideration", "UNDERCONSIDERATION": "Under Consideration",
    "TARGET LIST": "Under Consideration", "TARGET": "Under Consideration", "IDEA": "Under Consideration",
}


def canonical_status(status: str | None) -> str:
    """Map any raw status (fund_status PENDING/DELAYED, market_status ACTV/LIQU/DLST, or
    a rex_products status) to the ONE allowed display set above. Unknown -> 'Filed' (the
    safe pre-trading default). 'Pending'/'Delayed' must NEVER reach a report."""
    return _STATUS_DISPLAY.get((status or "").upper().strip(), "Filed")


def present_value(status: str | None, raw: float | None) -> float | None:
    """Present-day contribution of a fund to an AUM/flow KPI.
      live   -> its real value
      closed -> 0.0 (gone today, even if Bloomberg prints a stale figure)
      future -> None (excluded entirely; caller must drop it)"""
    role = status_role(status)
    if role == "future":
        return None
    if role == "closed":
        return 0.0
    return float(raw) if raw is not None else 0.0


# ===========================================================================
# TRUSTS — the legal wrappers our live products sit under
# ===========================================================================
LIVE_TRUSTS: list[str] = [
    "REX ETF Trust",
    "ETF Opportunities Trust",
    "World Funds Trust",
]
# Filed under this long ago; we will NOT launch from it. Pipeline/filings only.
PIPELINE_ONLY_TRUSTS: list[str] = [
    "Exchange Listed Funds Trust",
]


# ===========================================================================
# FILING FIELDS — what the filing scripts must capture (feeds INTERNAL pipeline)
# ===========================================================================
FILING_FIELDS: list[str] = [
    "fund_name",
    "ticker",            # if available
    "effective_date",
    "filing_type",
    "filing_trust",
    "filing_date",
]


# ===========================================================================
# Convenience for docs / assertions
# ===========================================================================
@dataclass
class _LibrarySummary:
    suites: list[str] = field(default_factory=lambda: list(SUITE_DISPLAYS))
    live_statuses: list[str] = field(default_factory=lambda: sorted(LIVE_STATUSES))
    future_statuses: list[str] = field(default_factory=lambda: sorted(FUTURE_STATUSES))
    live_trusts: list[str] = field(default_factory=lambda: list(LIVE_TRUSTS))


def summary() -> _LibrarySummary:
    return _LibrarySummary()
