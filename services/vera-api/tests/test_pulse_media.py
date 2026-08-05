import asyncio
import json
import time

import pytest
from fastapi.responses import FileResponse

from routers import pulse_media, pulse_store

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64
JPG = b"\xff\xd8\xff" + b"y" * 64


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("PULSE_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setattr(pulse_store, "DB_PATH", str(tmp_path / "pulse.db"))
    yield


def test_save_and_serve_round_trip(tmp_path):
    ref = pulse_media.save_image(PNG)
    assert ref.startswith("/pulse/media/") and ref.endswith(".png")
    name = ref.rsplit("/", 1)[1]
    resp = asyncio.run(pulse_media.serve(name))
    assert isinstance(resp, FileResponse)
    assert resp.media_type == "image/png"
    with open(resp.path, "rb") as f:
        assert f.read() == PNG


def test_save_derives_extension_from_mime():
    ref = pulse_media.save_image(JPG, "image/jpeg")
    assert ref.endswith(".jpg")


def test_save_failure_is_explicit(monkeypatch):
    monkeypatch.setenv("PULSE_MEDIA_DIR", "/dev/null/nope")
    assert pulse_media.save_image(PNG) is None


def test_serve_rejects_traversal_and_missing():
    assert asyncio.run(pulse_media.serve("../../etc/passwd")).status_code == 404
    assert asyncio.run(pulse_media.serve("..%2fpulse.db")).status_code == 404
    assert asyncio.run(pulse_media.serve("nope.png")).status_code == 404
    assert asyncio.run(pulse_media.serve("a" * 32 + ".png")).status_code == 404


def _insert(cid, image_url=None, inline=None, **extra):
    card = {"id": cid, "created_at": int(time.time()), "day": "2026-08-04",
            "status": "new", "title": f"t-{cid}", "summary": "s", "body": "b",
            "image_url": image_url, "tint": "#112233",
            "sources": [{"n": 1, "title": "src", "url": "https://example.com/a"}],
            "inline_images": inline or []}
    card.update(extra)
    pulse_store.insert_card(card)


def _fake_fetch(reachable):
    async def fetch(url, token):
        if url in reachable:
            return reachable[url], "image/png"
        return None
    return fetch


def test_migration_rehomes_reachable_and_marks_unreachable(monkeypatch):
    good = "http://legacy.example/api/v1/files/aa/content"
    dead = "http://legacy.example/api/v1/files/bb/content"
    inline_good = "http://legacy.example/api/v1/files/cc/content"
    _insert("c1", image_url=good,
            inline=[{"n": 1, "url": inline_good, "caption": "cap", "sourceN": 1},
                    {"n": 2, "url": dead, "caption": "gone", "sourceN": 0}])
    _insert("c2", image_url=dead)
    _insert("c3", image_url="/pulse/media/" + "0" * 32 + ".png")
    monkeypatch.setattr(pulse_media, "_fetch", _fake_fetch({good: PNG, inline_good: PNG}))

    out = asyncio.run(pulse_media.migrate(pulse_media.MigrateBody()))
    assert out["ok"] is True
    assert out["images_rehomed"] == 2 and out["images_missing"] == 2
    assert out["cards_rewritten"] == 2

    c1 = pulse_store.get_card("c1")
    assert c1["image_url"].startswith("/pulse/media/")
    assert c1["inline_images"][0]["url"].startswith("/pulse/media/")
    assert c1["inline_images"][1]["url"] == ""
    assert c1["inline_images"][1]["caption"] == "gone"
    assert c1["title"] == "t-c1" and c1["sources"][0]["url"] == "https://example.com/a"

    c2 = pulse_store.get_card("c2")
    assert c2["image_url"] is None
    assert c2["status"] == "new" and c2["body"] == "b"

    c3 = pulse_store.get_card("c3")
    assert c3["image_url"] == "/pulse/media/" + "0" * 32 + ".png"


def test_migration_rerun_is_a_noop(monkeypatch):
    good = "http://legacy.example/api/v1/files/aa/content"
    _insert("c1", image_url=good)
    monkeypatch.setattr(pulse_media, "_fetch", _fake_fetch({good: PNG}))
    first = asyncio.run(pulse_media.migrate(pulse_media.MigrateBody()))
    assert first["images_rehomed"] == 1
    ref = pulse_store.get_card("c1")["image_url"]
    second = asyncio.run(pulse_media.migrate(pulse_media.MigrateBody()))
    assert second["cards_rewritten"] == 0 and second["images_rehomed"] == 0
    assert pulse_store.get_card("c1")["image_url"] == ref


def test_migration_clears_legacy_chat_ids():
    pulse_store.init()
    with pulse_store._conn() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(cards)").fetchall()]
        if "promoted_chat_id" not in cols:
            c.execute("ALTER TABLE cards ADD COLUMN promoted_chat_id TEXT")
    _insert("c1")
    with pulse_store._conn() as c:
        c.execute("UPDATE cards SET promoted_chat_id='legacy' WHERE id='c1'")
    out = asyncio.run(pulse_media.migrate(pulse_media.MigrateBody()))
    assert out["chat_ids_cleared"] == 1
    with pulse_store._conn() as c:
        row = c.execute("SELECT promoted_chat_id FROM cards WHERE id='c1'").fetchone()
    assert row["promoted_chat_id"] is None


def test_migration_covers_expired_cards(monkeypatch):
    good = "http://legacy.example/api/v1/files/aa/content"
    _insert("c1", image_url=good, status="expired")
    monkeypatch.setattr(pulse_media, "_fetch", _fake_fetch({good: PNG}))
    out = asyncio.run(pulse_media.migrate(pulse_media.MigrateBody()))
    assert out["images_rehomed"] == 1
    assert pulse_store.get_card("c1")["image_url"].startswith("/pulse/media/")


def test_migration_sends_token(monkeypatch):
    seen = {}

    async def fetch(url, token):
        seen["token"] = token
        return PNG, "image/png"

    _insert("c1", image_url="http://legacy.example/x")
    monkeypatch.setattr(pulse_media, "_fetch", fetch)
    asyncio.run(pulse_media.migrate(pulse_media.MigrateBody(token="sekret")))
    assert seen["token"] == "sekret"


def test_saved_refs_are_json_clean():
    ref = pulse_media.save_image(PNG)
    assert json.loads(json.dumps(ref)) == ref
    assert "http" not in ref
