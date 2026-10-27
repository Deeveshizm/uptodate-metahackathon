"""Run a real local-model generation independently of the app UI."""
import argparse
import json
from pathlib import Path

from backend.documents import parse_document
from backend.generation import Ollama, generate
from backend.retrieval import PublicFetcher, Resource, SourceError


class TraceOllama(Ollama):
    """Explicit CLI diagnostics only; never persist production jobs' source text."""
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.calls = []

    def complete(self, schema, system, payload):
        call = {"schema": schema.__name__, "input": payload}
        self.calls.append(call)
        try:
            result = super().complete(schema, system, payload)
            call["output"] = result.model_dump()
            call["usage"] = self.last_usage
            return result
        except SourceError as exc:
            call["error"] = str(exc)
            raise
        finally:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.calls, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url", nargs="?", help="Public original URL; omit to test the original local fixture")
    parser.add_argument("--output", type=Path, help="Save a private local result artifact")
    parser.add_argument("--trace", type=Path, help="Save private intermediate drafts, citations, and verifier responses; contains source text")
    args = parser.parse_args()
    try:
        model = TraceOllama(args.trace) if args.trace else Ollama()
        status = model.status()
        if not status["ready"]:
            raise SourceError(f"Start Ollama and install the configured model: {model.model}")
        if args.url:
            resource = PublicFetcher().fetch(args.url)
        else:
            fixture = Path(__file__).parents[1] / "samples/source.html"
            resource = Resource("https://example.com/original-prototype-fixture", "text/html", fixture.read_bytes())
        document = parse_document(resource)
        print(f"Extracted {len(document.spans)} source spans; using {model.model}", flush=True)
        result = generate(document, model, stage=lambda stage: print(stage, flush=True))
        result["fixture"] = not bool(args.url)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False))
            print(f"Saved generated draft to {args.output}")
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
    except SourceError as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
