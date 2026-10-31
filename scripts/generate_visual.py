"""Test Muse Image fallback on a previously verified draft, without UI."""
import argparse
import json
from pathlib import Path

from backend.documents import Document
from backend.retrieval import SourceError
from backend.visuals import choose_visual


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("draft", type=Path, help="JSON from scripts.generate_source")
    parser.add_argument("--output", type=Path, default=Path("data/muse-image-test.png"))
    args = parser.parse_args()
    try:
        draft = json.loads(args.draft.read_text())
        if draft.get("verification") != "machine_checked":
            raise SourceError("Use a machine-checked source-engine draft for this test")
        document = Document(draft["original_url"], draft["title"], [])
        # This command explicitly tests fallback; it does not fetch/select source art.
        visual, image = choose_visual(document, draft, fetcher=None,
                                      stage=lambda stage: print(stage, flush=True))
        if image is None:
            raise SourceError(visual["detail"])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(image)
        args.output.with_suffix(".json").write_text(json.dumps(visual, indent=2))
        print(f"Saved Muse Image fallback test to {args.output}")
    except (SourceError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
