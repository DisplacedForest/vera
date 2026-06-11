"""SearXNG query construction — news rides along with general so publishedDate reaches
the freshness gate whenever the instance has dated engines. Run: python3 -m pytest tests/test_websearch.py
"""
from routers.websearch import SearchRequest, _search_params


def test_search_params_include_news_category():
    p = _search_params(SearchRequest(query="x"))
    assert p["q"] == "x" and p["format"] == "json"
    assert p["categories"] == "general,news"


def test_search_params_carry_language():
    assert _search_params(SearchRequest(query="x", language="de"))["language"] == "de"
