"""
ETP Filing Tracker - Streamlit Dashboard

Run: streamlit run dashboard.py
"""
import streamlit as st
import pandas as pd
from pathlib import Path
from io import BytesIO
from etp_tracker.trusts import TRUST_CIKS

OUTPUT_DIR = Path("outputs")

st.set_page_config(page_title="ETP Filing Tracker", page_icon="📊", layout="wide")


@st.cache_data(ttl=300)
def load_all_fund_status() -> pd.DataFrame:
    """Load and combine _4_Fund_Status.csv from all trusts."""
    frames = []
    for folder in OUTPUT_DIR.iterdir():
        if not folder.is_dir():
            continue
        f4 = list(folder.glob("*_4_Fund_Status.csv"))
        if f4:
            df = pd.read_csv(f4[0], dtype=str)
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    return combined


@st.cache_data(ttl=300)
def load_all_name_history() -> pd.DataFrame:
    """Load and combine _5_Name_History.csv from all trusts."""
    frames = []
    for folder in OUTPUT_DIR.iterdir():
        if not folder.is_dir():
            continue
        f5 = list(folder.glob("*_5_Name_History.csv"))
        if f5:
            df = pd.read_csv(f5[0], dtype=str)
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    """Convert DataFrame to Excel bytes for download."""
    buf = BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    return buf.getvalue()


# --- Header ---
st.title("ETP Filing Tracker")
st.caption("SEC EDGAR prospectus filings across 14 ETP trusts")

# --- Load data ---
df = load_all_fund_status()
df_names = load_all_name_history()

if df.empty:
    st.warning("No data found. Run the pipeline first.")
    st.code(
        'python -c "\n'
        "from etp_tracker.run_pipeline import run_pipeline\n"
        "from etp_tracker.trusts import get_all_ciks, get_overrides\n"
        "run_pipeline(ciks=get_all_ciks(), overrides=get_overrides(), "
        "user_agent='YourName/1.0 (email@example.com)')\n"
        '"',
        language="bash",
    )
    st.stop()

# --- Sidebar Filters ---
st.sidebar.header("Filters")

trusts = sorted(df["Trust"].dropna().unique())
selected_trusts = st.sidebar.multiselect("Trust", trusts, default=trusts)

statuses = sorted(df["Status"].dropna().unique())
selected_statuses = st.sidebar.multiselect("Status", statuses, default=statuses)

search = st.sidebar.text_input("Search (name or ticker)")

# Apply filters
mask = df["Trust"].isin(selected_trusts) & df["Status"].isin(selected_statuses)
if search:
    search_lower = search.lower()
    mask = mask & (
        df["Fund Name"].fillna("").str.lower().str.contains(search_lower, regex=False)
        | df["Ticker"].fillna("").str.lower().str.contains(search_lower, regex=False)
    )
filtered = df[mask].copy()

# --- KPI Row ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Funds", len(filtered))
col2.metric("EFFECTIVE", len(filtered[filtered["Status"] == "EFFECTIVE"]))
col3.metric("PENDING", len(filtered[filtered["Status"] == "PENDING"]))
col4.metric("DELAYED", len(filtered[filtered["Status"] == "DELAYED"]))

# --- Main Table ---
st.subheader("Fund Status")

display_cols = [
    "Fund Name",
    "Ticker",
    "Trust",
    "Status",
    "Effective Date",
    "Latest Form",
    "Latest Filing Date",
    "Prospectus Link",
    "Series ID",
]
available_cols = [c for c in display_cols if c in filtered.columns]
display_df = filtered[available_cols].reset_index(drop=True)

st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Prospectus Link": st.column_config.LinkColumn("Filing Link", display_text="View"),
    },
)

# --- Downloads ---
st.subheader("Downloads")
dl1, dl2 = st.columns(2)
with dl1:
    st.download_button(
        "Download Fund Status (Excel)",
        data=to_excel_bytes(filtered[available_cols]),
        file_name="etp_fund_status.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
with dl2:
    if not df_names.empty:
        st.download_button(
            "Download Name History (Excel)",
            data=to_excel_bytes(df_names),
            file_name="etp_name_history.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

# --- Name Changes Section ---
if not df_names.empty:
    st.subheader("Name Changes")
    multi = df_names.groupby("Series ID").size()
    multi = multi[multi > 1]
    if len(multi):
        st.write(f"{len(multi)} funds with name changes detected")
        for sid in multi.index[:20]:
            rows = df_names[df_names["Series ID"] == sid].sort_values("First Seen Date")
            names = rows["Name"].tolist()
            current = rows[rows["Is Current"] == "Y"]["Name"].values
            label = current[0] if len(current) else names[-1]
            with st.expander(f"{label} ({sid})"):
                st.dataframe(rows[["Name", "First Seen Date", "Last Seen Date", "Is Current"]], hide_index=True)
    else:
        st.info("No name changes detected.")
