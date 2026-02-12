"""Tests for the ETF Launch Screener module."""
import pytest


# ---------------------------------------------------------------------------
# Data Loading Tests
# ---------------------------------------------------------------------------

def test_data_loading():
    """Test that both sheets load with correct row counts."""
    from screener.data_loader import load_all
    data = load_all()

    assert "stock_data" in data
    assert "etp_data" in data
    assert len(data) == 2  # Only 2 sheets now

    assert len(data["stock_data"]) > 2400
    assert len(data["etp_data"]) > 5000


def test_stock_data_has_required_columns():
    """Test stock_data has the columns needed for scoring."""
    from screener.data_loader import load_stock_data
    df = load_stock_data()

    required = ["Ticker", "Mkt Cap", "Total OI", "Turnover / Traded Value",
                "Twitter Positive Sentiment Count", "Short Interest Ratio",
                "Last Price", "GICS Sector", "ticker_clean"]
    for col in required:
        assert col in df.columns, f"Missing column: {col}"


def test_etp_data_has_category_attributes():
    """Test etp_data has all required category attribute columns."""
    from screener.data_loader import load_etp_data
    df = load_etp_data()

    required = [
        "q_category_attributes.map_li_category",
        "q_category_attributes.map_li_subcategory",
        "q_category_attributes.map_li_direction",
        "q_category_attributes.map_li_leverage_amount",
        "q_category_attributes.map_li_underlier",
        "underlier_clean",
        "t_w4.aum",
        "is_rex",
    ]
    for col in required:
        assert col in df.columns, f"Missing column: {col}"

    # REX funds should be derivable from is_rex
    rex = df[df["is_rex"] == True]
    assert len(rex) > 80


# ---------------------------------------------------------------------------
# Scoring Tests
# ---------------------------------------------------------------------------

def test_percentile_scoring():
    """Test that scoring produces valid composite scores (0-100)."""
    from screener.data_loader import load_stock_data
    from screener.scoring import compute_percentile_scores

    df = load_stock_data()
    scored = compute_percentile_scores(df)

    assert "composite_score" in scored.columns
    assert "rank" in scored.columns
    assert scored["composite_score"].min() >= 0
    assert scored["composite_score"].max() <= 100
    assert scored.iloc[0]["composite_score"] >= scored.iloc[-1]["composite_score"]  # sorted desc


def test_threshold_filters():
    """Test that threshold filters produce pass/fail column."""
    from screener.data_loader import load_stock_data, load_etp_data
    from screener.scoring import compute_percentile_scores, derive_rex_benchmarks, apply_threshold_filters

    stock = load_stock_data()
    etp = load_etp_data()

    benchmarks = derive_rex_benchmarks(etp, stock)
    scored = compute_percentile_scores(stock)
    filtered = apply_threshold_filters(scored, benchmarks)

    assert "passes_filters" in filtered.columns
    n_pass = filtered["passes_filters"].sum()
    assert 0 < n_pass < len(filtered)  # Some pass, some don't


# ---------------------------------------------------------------------------
# Regression Tests
# ---------------------------------------------------------------------------

def test_regression_training():
    """Test that regression model trains and produces predictions."""
    from screener.data_loader import load_stock_data, load_etp_data
    from screener.regression import build_training_set, train_model, predict_aum

    stock = load_stock_data()
    etp = load_etp_data()

    training = build_training_set(etp, stock)
    assert training is not None
    assert len(training) >= 10

    model = train_model(training)
    assert model is not None
    assert model.r_squared >= 0
    assert model.model_type in ("OLS", "GradientBoosting")

    predicted = predict_aum(model, stock.head(10))
    assert "predicted_aum" in predicted.columns
    assert all(predicted["predicted_aum"] >= 0)


# ---------------------------------------------------------------------------
# Competitive Analysis Tests
# ---------------------------------------------------------------------------

def test_competitive_density():
    """Test that known crowded underliers are categorized correctly."""
    from screener.data_loader import load_etp_data
    from screener.competitive import compute_competitive_density

    etp = load_etp_data()
    density = compute_competitive_density(etp)

    assert len(density) > 100  # At least 100 unique underliers

    # TSLA and NVDA should be "Crowded"
    tsla = density[density["underlier"] == "TSLA US"]
    assert len(tsla) == 1
    assert tsla.iloc[0]["density_category"] == "Crowded"
    assert tsla.iloc[0]["product_count"] >= 5

    nvda = density[density["underlier"] == "NVDA US"]
    assert len(nvda) == 1
    assert nvda.iloc[0]["density_category"] == "Crowded"


def test_fund_flows():
    """Test fund flow aggregation."""
    from screener.data_loader import load_etp_data
    from screener.competitive import compute_fund_flows

    etp = load_etp_data()
    flows = compute_fund_flows(etp)

    assert len(flows) > 0
    assert "underlier" in flows.columns
    assert "flow_1m" in flows.columns
    assert "flow_direction" in flows.columns


# ---------------------------------------------------------------------------
# Filing Match Tests
# ---------------------------------------------------------------------------

def test_filing_match():
    """Test that filing match uses etp_data and pipeline DB."""
    from screener.data_loader import load_stock_data, load_etp_data
    from screener.scoring import compute_percentile_scores
    from screener.filing_match import match_filings, get_rex_underlier_map

    stock = load_stock_data()
    etp = load_etp_data()
    scored = compute_percentile_scores(stock)

    # Verify underlier map works
    und_map = get_rex_underlier_map(etp)
    assert len(und_map) > 20  # REX has 30+ single-stock underliers

    matched = match_filings(scored, etp)
    assert "filing_status" in matched.columns

    # At least some should have REX filings
    rex_filed = matched[matched["filing_status"].str.startswith("REX Filed")]
    assert len(rex_filed) > 0


# ---------------------------------------------------------------------------
# PDF Report Tests
# ---------------------------------------------------------------------------

def test_pdf_generation():
    """Test that PDF report generates valid bytes."""
    from screener.report_generator import generate_executive_report

    results = [
        {"ticker": "NVDA US", "sector": "Technology", "composite_score": 89.8,
         "predicted_aum": 1340, "mkt_cap": 4491612, "call_oi_pctl": 99.5,
         "passes_filters": True, "filing_status": "REX Filed - Pending",
         "competitive_density": "Crowded", "competitor_count": 10, "total_competitor_aum": 5376},
        {"ticker": "AMD US", "sector": "Technology", "composite_score": 89.9,
         "predicted_aum": 716, "mkt_cap": 413083, "call_oi_pctl": 98.2,
         "passes_filters": True, "filing_status": "Not Filed",
         "competitive_density": "Crowded", "competitor_count": 5, "total_competitor_aum": 721},
    ]

    pdf = generate_executive_report(results)
    assert isinstance(pdf, bytes)
    assert len(pdf) > 1000
    assert pdf[:5] == b"%PDF-"  # Valid PDF header


# ---------------------------------------------------------------------------
# Web Route Tests (using TestClient)
# ---------------------------------------------------------------------------

def test_screener_page(client):
    """Test that /screener/ returns 200."""
    r = client.get("/screener/")
    assert r.status_code == 200
    assert "Launch Screener" in r.text


def test_screener_rex_funds(client):
    """Test that /screener/rex-funds returns 200."""
    r = client.get("/screener/rex-funds")
    assert r.status_code == 200
    assert "REX Fund Portfolio" in r.text


def test_screener_stock_detail(client):
    """Test that /screener/stock/{ticker} returns 200."""
    r = client.get("/screener/stock/NVDA US")
    assert r.status_code == 200
    assert "NVDA" in r.text
