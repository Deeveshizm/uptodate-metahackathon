"""Live localhost integration check. Deletes only the account it creates."""
from pathlib import Path
import argparse
import json
import time

import httpx


def wait_for_job(client, job_id):
    previous = ""
    for _ in range(900):
        job = next(j for j in client.get("/v1/jobs").json() if j["id"] == job_id)
        stage = job["stage"] or job["status"]
        if stage != previous:
            print(job["kind"] + ":", stage, flush=True)
            previous = stage
        if job["status"] not in ("queued", "processing"):
            return job
        time.sleep(1)
    raise AssertionError("Local test exceeded 15 minutes; check backend jobs and ComfyUI queue")


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source", help="Also run live local-model generation on this public original URL")
    source.add_argument("--newsletter", help="Test automatic discovery/generation through Add newsletter; does not submit signup")
    parser.add_argument("--output", type=Path, help="Save generated text and available image locally before deleting the test account")
    parser.add_argument("--require-image", action="store_true", help="Fail unless the source test produces a ready, retrievable image")
    args = parser.parse_args()
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=15) as client:
        assert client.get("/health").json()["status"] == "ok"
        result = client.post("/v1/dev/accounts", json={"timezone": "America/New_York"})
        result.raise_for_status()
        client.headers["Authorization"] = "Bearer " + result.json()["token"]
        try:
            sample = (Path(__file__).parents[1] / "samples/newsletter.eml").read_bytes()
            imported = client.post("/v1/import/email", files={"file": ("sample.eml", sample)})
            imported.raise_for_status()
            # Processing is queued; poll promptly without hiding a failure.
            drafts = []
            for _ in range(30):
                drafts = client.get("/v1/feed").json()["posts"]
                if drafts:
                    break
                time.sleep(0.1)
            assert len(drafts) == 4, "Expected four excerpt drafts"
            post_id = drafts[0]["id"]
            assert client.get("/v1/feed").json()["total"] == 4
            assert client.post(f"/v1/posts/{post_id}/bookmark").status_code == 204
            assert len(client.get("/v1/bookmarks").json()) == 1
            assert client.post(f"/v1/posts/{post_id}/share").json()["mode"] == "original-source"
            assert client.post("/v1/dev/demo").status_code == 200
            demo = next(p for p in client.get("/v1/feed").json()["posts"] if p["is_demo"])
            share = client.post(f"/v1/posts/{demo['id']}/share").json()["url"]
            assert "Sign up for this newsletter" in client.get(share).text
            print("Live API passed: import → automatic private feed → bookmark → safe share.")
            if args.source or args.newsletter:
                response = (client.post("/v1/newsletters", json={"url": args.newsletter, "auto_subscribe": False}) if args.newsletter
                            else client.post("/v1/generation", json={"url": args.source}))
                response.raise_for_status()
                job_id = response.json()["job_id"]
                job = wait_for_job(client, job_id)
                if args.newsletter:
                    assert job["status"] == "completed", job.get("error")
                    job_id = job["result"].get("generation_job_id")
                    assert job_id, job["result"]["content_discovery"]
                    job = wait_for_job(client, job_id)
                assert job["status"] == "completed", job.get("error")
                post_id = next(item["post_id"] for item in job["result"]["items"] if item["status"] == "published")
                generated = client.get(f"/v1/posts/{post_id}").json()
                assert generated["verification"] == "machine_checked"
                assert generated["finding"] != generated["hook"]
                assert generated["visual"]["kind"] in ("source", "generated", "pending")
                assert generated["rights"] == "private" and generated["publication"] == {"mode": "automatic_private", "human_reviewed": False}
                assert any(post["id"] == post_id for post in client.get("/v1/feed").json()["posts"]), "Generated post was not automatically placed in the private feed"
                if args.require_image:
                    assert generated["visual"]["status"] == "ready", generated["visual"].get("detail")
                    media = client.get(f"/v1/posts/{post_id}/image")
                    media.raise_for_status()
                    assert media.headers["content-type"] == "image/png" and media.content.startswith(b"\x89PNG")
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    fields = ("title", "hook", "finding", "limitations", "original_url", "analysis", "evidence", "visual", "verification")
                    args.output.write_text(json.dumps({key: generated[key] for key in fields}, indent=2))
                    if generated["visual"]["status"] == "ready":
                        media = client.get(f"/v1/posts/{post_id}/image")
                        media.raise_for_status()
                        args.output.with_suffix(".png").write_bytes(media.content)
                    print("Saved local test artifact:", args.output, flush=True)
                assert any(post["id"] == post_id for post in client.get("/v1/feed").json()["posts"]), "Approved generated post is missing from the feed"
                print("Live source engine passed: original retrieval → local drafting → cited verification → source-first visual → automatic private feed.", flush=True)
        finally:
            response = client.delete("/v1/me")
            response.raise_for_status()
            print("Temporary smoke-test account and its records removed.")


if __name__ == "__main__":
    main()
