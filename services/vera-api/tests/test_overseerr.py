"""Overseerr normalizer unit tests. Standalone — run: python3 tests/test_overseerr.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import overseerr as ov  # noqa: E402


def test_availability_mapping():
    assert ov._availability(None) == "not_requested"          # not tracked
    assert ov._availability({}) == "not_requested"            # tracked, no status
    assert ov._availability({"status": 1}) == "not_requested"
    assert ov._availability({"status": 2}) == "requested"
    assert ov._availability({"status": 3}) == "processing"
    assert ov._availability({"status": 4}) == "partially_available"
    assert ov._availability({"status": 5}) == "available"
    assert ov._availability({"status": 99}) == "not_requested"  # unknown code


def test_year_parsing():
    assert ov._year("2021-09-15") == 2021
    assert ov._year("1979") == 1979
    assert ov._year("") is None
    assert ov._year(None) is None
    assert ov._year("TBA") is None


def test_normalize_movie():
    n = ov._normalize({
        "id": 438631, "mediaType": "movie", "title": "Dune", "name": None,
        "releaseDate": "2021-09-15", "overview": "Paul Atreides...",
        "posterPath": "/abc.jpg", "mediaInfo": {"status": 5},
    })
    assert n == {
        "id": 438631, "media_type": "movie", "title": "Dune", "year": 2021,
        "overview": "Paul Atreides...", "availability": "available",
        "poster": "https://image.tmdb.org/t/p/w185/abc.jpg",
    }


def test_normalize_tv_uses_name_and_firstair():
    n = ov._normalize({
        "id": 100, "mediaType": "tv", "title": None, "name": "The Traitors",
        "firstAirDate": "2023-01-12", "overview": "",
    })
    assert n["title"] == "The Traitors"
    assert n["year"] == 2023
    assert n["availability"] == "not_requested"  # no mediaInfo -> requestable
    assert n["poster"] is None  # no posterPath -> null


def test_normalize_drops_person():
    assert ov._normalize({"id": 1, "mediaType": "person", "name": "Denis Villeneuve"}) is None


def test_held_set():
    # the digest treats these as "already have / in flight" and skips them
    assert ov.HELD == {"requested", "processing", "partially_available", "available"}
    assert "not_requested" not in ov.HELD


if __name__ == "__main__":
    test_availability_mapping()
    test_year_parsing()
    test_normalize_movie()
    test_normalize_tv_uses_name_and_firstair()
    test_normalize_drops_person()
    test_held_set()
    print("OK")
