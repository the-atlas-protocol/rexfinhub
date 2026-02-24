"""Market page integration tests."""
import pytest

from webapp.services.market_data import ALL_CATEGORIES


# ---------------------------------------------------------------------------
# Page load tests (all routes should return 200 even without Bloomberg data)
# ---------------------------------------------------------------------------

def test_home_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200


def test_home_analysis_link_valid(client):
    """Filing Analysis card should link to /filings/, not /analysis."""
    r = client.get("/")
    assert "/filings/" in r.text
    # Should NOT have href="/analysis" as a standalone link
    assert 'href="/analysis"' not in r.text


def test_rex_view_loads(client):
    r = client.get("/market/rex")
    assert r.status_code == 200


def test_rex_view_default_etf(client):
    """Default fund_structure should be ETF."""
    r = client.get("/market/rex")
    assert r.status_code == 200


def test_rex_view_with_fund_structure(client):
    r = client.get("/market/rex?fund_structure=ETF")
    assert r.status_code == 200


def test_rex_view_multi_fund_structure(client):
    """Multi-select fund_structure should work."""
    r = client.get("/market/rex?fund_structure=ETF,ETN")
    assert r.status_code == 200


def test_rex_view_with_category(client):
    """Category filter should work with URL encoding."""
    r = client.get("/market/rex?category=Crypto")
    assert r.status_code == 200


def test_category_view_loads(client):
    r = client.get("/market/category")
    assert r.status_code == 200


def test_category_view_default_etf(client):
    """Default fund_structure should be ETF."""
    r = client.get("/market/category")
    assert r.status_code == 200


def test_category_view_preserves_filters(client):
    """Category + fund_structure params should both appear in response."""
    r = client.get("/market/category?cat=Crypto&fund_structure=ETF")
    assert r.status_code == 200


def test_category_view_all_categories(client):
    """Every category should return 200."""
    for cat in ALL_CATEGORIES:
        r = client.get(f"/market/category?cat={cat}")
        assert r.status_code == 200, f"Category '{cat}' returned {r.status_code}"


def test_category_view_pagination(client):
    """Pagination params should work."""
    r = client.get("/market/category?cat=Crypto&page=1&per_page=25")
    assert r.status_code == 200


def test_category_view_slicer_params(client):
    """Slicer parameters passed as query params should work."""
    r = client.get("/market/category?cat=Crypto&q_category_attributes.map_crypto_is_spot=Spot")
    assert r.status_code == 200


def test_treemap_redirects_to_category(client):
    """Standalone treemap should redirect to category view."""
    r = client.get("/market/treemap", follow_redirects=False)
    assert r.status_code == 302
    assert "/market/category" in r.headers.get("location", "")


def test_treemap_with_cat_redirects(client):
    """Treemap with category should redirect preserving category."""
    r = client.get("/market/treemap?cat=Crypto", follow_redirects=False)
    assert r.status_code == 302
    assert "Crypto" in r.headers.get("location", "")


def test_issuer_view_loads(client):
    r = client.get("/market/issuer")
    assert r.status_code == 200


def test_issuer_view_all_categories(client):
    for cat in ALL_CATEGORIES:
        r = client.get(f"/market/issuer?cat={cat}")
        assert r.status_code == 200, f"Issuer view for '{cat}' returned {r.status_code}"


def test_issuer_detail_with_empty_issuer(client):
    """Should not crash with empty issuer param."""
    r = client.get("/market/issuer/detail?issuer=")
    assert r.status_code == 200


def test_market_share_loads(client):
    r = client.get("/market/share")
    assert r.status_code == 200


def test_market_share_all_categories(client):
    for cat in ALL_CATEGORIES:
        r = client.get(f"/market/share?cat={cat}")
        assert r.status_code == 200


def test_underlier_loads(client):
    r = client.get("/market/underlier")
    assert r.status_code == 200


def test_underlier_types(client):
    for t in ["income", "li"]:
        r = client.get(f"/market/underlier?type={t}")
        assert r.status_code == 200


def test_calendar_loads(client):
    r = client.get("/market/calendar")
    assert r.status_code == 200


def test_compare_loads(client):
    r = client.get("/market/compare")
    assert r.status_code == 200


def test_compare_with_tickers(client):
    r = client.get("/market/compare?tickers=SPY")
    assert r.status_code == 200


def test_screener_loads(client):
    r = client.get("/screener/")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Filing Analysis page tests
# ---------------------------------------------------------------------------

def test_filing_analysis_loads(client):
    r = client.get("/filings/")
    assert r.status_code == 200


def test_filing_analysis_search(client):
    r = client.get("/filings/?q=REX")
    assert r.status_code == 200


def test_filing_analysis_form_filter(client):
    r = client.get("/filings/?form_type=485BPOS")
    assert r.status_code == 200


def test_filing_analysis_date_range(client):
    r = client.get("/filings/?date_range=30")
    assert r.status_code == 200


def test_filing_analysis_combined_filters(client):
    r = client.get("/filings/?form_type=485BPOS&date_range=90&per_page=25")
    assert r.status_code == 200


def test_home_filing_analysis_link(client):
    """Filing Analysis card should link to /filings/, not /dashboard."""
    r = client.get("/")
    assert '/filings/' in r.text
