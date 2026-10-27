"""Generate in an existing simulator account; never delete or auto-approve it."""
import argparse
import json
from pathlib import Path

import httpx

from scripts.smoke_api import wait_for_job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-file", required=True, type=Path, help="The app sandbox's simulator-session file; its token is never printed")
    parser.add_argument("--newsletter", default="https://newsletter.alphasignal.ai/")
    parser.add_argument("--output", type=Path, default=Path("data/simulator-alphasignal-test.json"))
    parser.add_argument("--retry-visual", action="store_true", help="Retry a pending visual on the existing post; do not regenerate or approve its text")
    parser.add_argument("--regenerate", action="store_true", help="Regenerate the existing post from its original source, preserving its ID and bookmarks")
    args = parser.parse_args()
    try:
        token = args.session_file.read_text().strip()
        if not token:
            raise ValueError("Start a local account in the simulator first")
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=15,
                          headers={"Authorization": "Bearer " + token}, trust_env=False) as client:
            identity = client.get("/v1/me")
            identity.raise_for_status()
            response = client.post("/v1/newsletters", json={"url": args.newsletter, "auto_subscribe": False})
            response.raise_for_status()
            print("Newsletter queued in the existing simulator account; no signup submitted.", flush=True)
            parent = wait_for_job(client, response.json()["job_id"])
            if parent["status"] != "completed":
                raise ValueError(parent.get("error") or "Newsletter discovery failed")
            generation_id = parent["result"].get("generation_job_id")
            if not generation_id:
                raise ValueError(str(parent["result"].get("content_discovery")))
            job = wait_for_job(client, generation_id)
            if job["status"] not in ("review", "completed"):
                raise ValueError(job.get("error") or "Generation failed")
            item = next((item for item in job["result"]["items"] if item.get("post_id")), None)
            if not item:
                raise ValueError("Generation completed without a post")
            response = client.get("/v1/posts/" + item["post_id"])
            response.raise_for_status()
            post = response.json()
            if args.regenerate:
                response = client.post("/v1/posts/" + post["id"] + "/regenerate")
                response.raise_for_status()
                replacement = wait_for_job(client, response.json()["job_id"])
                if replacement["status"] != "completed":
                    raise ValueError(replacement.get("error") or "Original-source regeneration failed; previous post was retained")
                response = client.get("/v1/posts/" + post["id"])
                response.raise_for_status()
                post = response.json()
            if post["verification"] != "machine_checked":
                raise ValueError("Generated text has not passed evidence checks")
            if args.retry_visual and post["visual"]["status"] != "ready":
                response = client.post("/v1/posts/" + post["id"] + "/image/retry")
                response.raise_for_status()
                visual_job_id = response.json().get("job_id")
                if visual_job_id:
                    visual_job = wait_for_job(client, visual_job_id)
                    if visual_job["status"] != "completed":
                        raise ValueError(visual_job.get("error") or "Source visual retry failed")
                response = client.get("/v1/posts/" + post["id"])
                response.raise_for_status()
                post = response.json()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            fields = ("id", "title", "hook", "finding", "limitations", "original_url", "analysis", "evidence", "visual", "verification", "status")
            args.output.write_text(json.dumps({key: post[key] for key in fields}, indent=2))
            if post["visual"]["status"] == "ready":
                media = client.get("/v1/posts/" + post["id"] + "/image")
                media.raise_for_status()
                if media.headers["content-type"] != "image/png" or not media.content.startswith(b"\x89PNG"):
                    raise ValueError("Generated media is not a retrievable PNG")
                args.output.with_suffix(".png").write_bytes(media.content)
            else:
                raise ValueError(post["visual"].get("detail") or "Image remains pending; the text draft is retained in Review")
            feed = client.get("/v1/feed")
            feed.raise_for_status()
            if not any(candidate["id"] == post["id"] for candidate in feed.json()["posts"]):
                raise ValueError("Generated post is missing from the automatic private feed")
            if post["rights"] != "private" or post["publication"].get("human_reviewed"):
                raise ValueError("Automatic feed must stay private and must not claim human approval")
            print("Ready in simulator " + ("Review" if post["status"] == "review" else "Home") + ": " + post["title"], flush=True)
            print("The account and post are retained in the private feed. No human approval, public publishing, or account deletion was performed.", flush=True)
            print("Saved local artifact:", args.output, flush=True)
    except (OSError, ValueError, httpx.HTTPError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
