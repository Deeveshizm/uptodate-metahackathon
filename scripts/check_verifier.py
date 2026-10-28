"""Small explicit entailment regression check using original fictional source text."""
import json
from pathlib import Path

from backend.generation import generation_model, Verification, VERIFY_SYSTEM
from backend.retrieval import SourceError


def main():
    source = "Lantern Cafe serves espresso, flat whites, and cold brew in Berkeley. It also offers pastries and outdoor seating. No health benefits have been measured."
    cases = [
        ("faithful_selective_summary", {
            "title": ("Lantern Cafe in Berkeley", True),
            "hook": ("Lantern Cafe offers coffee in Berkeley.", True),
            "summary": ("The Berkeley cafe serves espresso and cold brew.", True),
        }),
        ("unsupported_claims", {
            "title": ("Lantern Cafe in Berkeley", True),
            "hook": ("Lantern Cafe is in Oakland.", False),
            "summary": ("Its coffee has been proven to improve heart health.", False),
        }),
        ("negation_and_numbers", {
            "title": ("Lantern Cafe in Berkeley", True),
            "hook": ("Lantern Cafe does not serve cold brew.", False),
            "summary": ("The cafe has measured a 50% increase in sales.", False),
        }),
    ]
    model = generation_model()
    results = []
    for name, fields in cases:
        payload = {"fields_to_check": list(fields), "evidence_by_field": {
            field: {"text": text, "source_excerpts": [source]} for field, (text, _) in fields.items()
        }}
        verdict = model.complete(Verification, VERIFY_SYSTEM, payload)
        actual = {item.field: item.supported for item in verdict.verdicts}
        expected = {field: supported for field, (_, supported) in fields.items()}
        passed = actual == expected and len(verdict.verdicts) == len(fields)
        results.append({"case": name, "passed": passed, "expected": expected, "actual": verdict.model_dump()})
        print(name + ": " + ("PASS" if passed else "FAIL"), flush=True)
    output = Path("data/verifier-calibration.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"model": model.last_usage.get("model", model.model), "cases": results}, indent=2))
    if not all(item["passed"] for item in results):
        raise SystemExit("Verifier calibration failed; inspect data/verifier-calibration.json")
    print("Verifier accepted supported paraphrases and rejected invented, negated, and numerical claims.")


if __name__ == "__main__":
    try:
        main()
    except SourceError as exc:
        raise SystemExit(str(exc)) from None
