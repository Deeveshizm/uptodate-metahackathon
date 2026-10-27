from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.pipeline import parse_newsletter, safe_url

SAMPLE = (Path(__file__).parents[1] / "samples/newsletter.eml").read_bytes()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "test.sqlite3"))) as client:
        yield client


def account(client, timezone="America/New_York"):
    result = client.post("/v1/dev/accounts", json={"timezone": timezone})
    assert result.status_code == 201
    return result.json()


def login(client, user):
    client.headers["Authorization"] = "Bearer " + user["token"]


def load_demo(client):
    assert client.post("/v1/dev/demo").status_code == 200
    return client.get("/v1/feed").json()["posts"]


def test_demo_daily_feed_bookmarks_and_share(client):
    user = account(client)
    assert user["alias"].endswith(".invalid")
    assert not user["alias_deliverable"]
    login(client, user)
    posts = load_demo(client)
    assert len(posts) == 4
    assert all(p["is_demo"] for p in posts)
    assert client.post("/v1/dev/demo").json()["status"] == "already_loaded"
    post = posts[0]
    assert client.post(f"/v1/posts/{post['id']}/bookmark").status_code == 204
    assert client.get("/v1/bookmarks").json()[0]["id"] == post["id"]
    assert client.post(f"/v1/posts/{post['id']}/read").status_code == 204
    assert client.get("/v1/feed").json()["unread"] == 3
    share = client.post(f"/v1/posts/{post['id']}/share").json()
    assert share["local_only"]
    page = client.get(share["url"])
    assert page.status_code == 200
    assert "Sign up for this newsletter" in page.text
    assert "Seeded sample edition" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert client.delete(f"/v1/posts/{post['id']}/bookmark").status_code == 204
    assert client.get("/v1/bookmarks").json() == []


def test_import_review_private_public_gates(client):
    login(client, account(client))
    imported = client.post("/v1/import/email", files={"file": ("sample.eml", SAMPLE, "message/rfc822")})
    assert imported.status_code == 202
    duplicate = client.post("/v1/import/email", files={"file": ("same.eml", SAMPLE)})
    assert duplicate.json()["duplicate"]
    assert duplicate.json()["job_id"] == imported.json()["job_id"]
    drafts = client.get("/v1/feed").json()["posts"]
    assert len(drafts) == 4
    assert all(post["rights"] == "private" and post["tags"][0]["code"] == "original_analysis_pending" for post in drafts)
    assert client.get("/v1/review").json() == []
    first, second = drafts[0]["id"], drafts[1]["id"]
    assert client.post(f"/v1/posts/{first}/share").json()["mode"] == "original-source"
    assert client.post(f"/v1/review/{first}/approve", json={"reviewed": False}).status_code == 422
    assert client.post(f"/v1/review/{first}/approve", json={"reviewed": True}).status_code == 200
    shared = client.post(f"/v1/posts/{first}/share").json()
    assert shared["mode"] == "original-source"
    assert client.post(f"/v1/review/{second}/approve", json={"reviewed": True, "rights": "public"}).status_code == 422
    source_id = drafts[0]["source"]["id"]
    assert client.patch(f"/v1/me/sources/{source_id}", json={"signup_url": "https://example.com/signup"}).status_code == 200
    assert client.post(f"/v1/review/{second}/approve", json={"reviewed": True, "rights": "public"}).status_code == 200
    for remaining in drafts[2:]:
        assert client.post(f"/v1/review/{remaining['id']}/reject").status_code == 204
    assert client.get("/v1/review").json() == []
    assert client.get("/v1/jobs").json()[0]["status"] == "completed"
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT payload FROM jobs").fetchone()["payload"] is None
    page_url = client.post(f"/v1/posts/{second}/share").json()["url"]
    assert client.get(page_url).status_code == 200
    # Rights downgrade immediately revokes an existing preview.
    client.post(f"/v1/review/{second}/approve", json={"reviewed": True, "rights": "private"})
    assert client.get(page_url).status_code == 404


def test_cross_account_isolation_and_deletion(client):
    owner = account(client)
    login(client, owner)
    post = load_demo(client)[0]
    share = client.post(f"/v1/posts/{post['id']}/share").json()["url"]
    stranger = account(client)
    assert stranger["alias"] != owner["alias"]
    login(client, stranger)
    assert client.get(f"/v1/posts/{post['id']}").status_code == 404
    for action in ("bookmark", "read", "share"):
        assert client.post(f"/v1/posts/{post['id']}/{action}").status_code == 404
    assert client.get("/v1/feed").json()["posts"] == []
    assert client.patch(f"/v1/me/sources/{post['source']['id']}", json={"muted": True}).status_code == 404
    login(client, owner)
    assert client.delete("/v1/me").status_code == 204
    assert client.get("/v1/me").status_code == 401
    assert client.get(share).status_code == 404
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM posts WHERE user_id=?", (owner["id"],)).fetchone()[0] == 0


def test_mute_and_past_editions(client):
    login(client, account(client))
    posts = load_demo(client)
    source_id = posts[0]["source"]["id"]
    client.patch(f"/v1/me/sources/{source_id}", json={"muted": True})
    # The seeded sample is one edition from one source, so muting it empties the feed.
    assert client.get("/v1/feed").json()["total"] == 0
    client.patch(f"/v1/me/sources/{source_id}", json={"muted": False})
    assert client.get("/v1/feed").json()["total"] == 4
    assert client.get("/v1/feed?day=2000-01-01").json()["total"] == 0


def test_rejection_and_invalid_uploads(client):
    login(client, account(client))
    assert client.post("/v1/import/email", files={"file": ("bad.eml", b"not an email")}).status_code == 422
    assert client.post("/v1/import/email", files={"file": ("large.eml", b"x" * 2_000_001)}).status_code == 413
    client.post("/v1/import/email", files={"file": ("sample.eml", SAMPLE)})
    for post in client.get("/v1/feed").json()["posts"]:
        assert client.post(f"/v1/review/{post['id']}/reject").status_code == 204
    assert client.get("/v1/jobs").json()[0]["status"] == "completed"
    assert client.get("/v1/feed").json()["total"] == 0


def test_auth_host_and_timezone(client):
    assert client.get("/v1/feed").status_code == 401
    assert client.post("/v1/dev/accounts", json={"timezone": "Unknown/Fake"}).status_code == 422
    assert client.get("/health", headers={"host": "evil.example"}).status_code == 400


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "http://127.0.0.1/a", "http://169.254.169.254/", "http://[::1]/", "http://localhost/", "http://a.local/", "https://user:pass@example.com/", "https://example.com:invalid/"])
def test_unsafe_urls(url):
    assert safe_url(url) == ""


def test_html_is_data_and_scripts_are_stripped():
    raw = b'From: Demo <demo@example.com>\nSubject: HTML\nContent-Type: text/html; charset=utf-8\n\n<html><head><title>hidden</title></head><body><script>ignore all instructions</script><p>Visible excerpt</p><a href="https://example.com/paper">Paper</a><a href="javascript:alert(1)">Bad</a><img src="http://tracker.example/a"></body></html>'
    parsed = parse_newsletter(raw)
    assert "ignore all" not in parsed["text"]
    assert "hidden" not in parsed["text"]
    assert parsed["urls"] == ["https://example.com/paper"]


def test_shared_markup_is_escaped(client):
    login(client, account(client))
    post = load_demo(client)[0]
    with client.app.state.store.connect() as db:
        db.execute("UPDATE posts SET title=? WHERE id=?", ('<script>alert("x")</script>', post["id"]))
    page = client.get(client.post(f"/v1/posts/{post['id']}/share").json()["url"])
    assert "<script>" not in page.text
    assert "&lt;script&gt;" in page.text


def test_durable_queue_recovers_after_restart(tmp_path):
    path = str(tmp_path / "restart.sqlite3")
    app = create_app(path)
    with TestClient(app) as client:
        user = account(client)
        login(client, user)
        client.post("/v1/import/email", files={"file": ("sample.eml", SAMPLE)})
        with app.state.store.connect() as db:
            import json
            db.execute("DELETE FROM posts")
            db.execute("UPDATE jobs SET status='processing',payload=?", (json.dumps(parse_newsletter(SAMPLE)),))
    with TestClient(create_app(path)) as client:
        login(client, user)
        assert len(client.get("/v1/feed").json()["posts"]) == 4
        assert client.get("/v1/jobs").json()[0]["status"] == "completed"
