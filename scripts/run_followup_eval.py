import json
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.followup_resolver import FollowupResolver


def main() -> int:
    dataset_path = Path("config/followup_eval.json")
    if not dataset_path.exists():
        print("Missing config/followup_eval.json")
        return 1

    if load_dotenv:
        load_dotenv()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY is not set. Skipping follow-up eval.")
        return 2

    cases = json.loads(dataset_path.read_text())
    resolver = FollowupResolver()

    total = 0
    passed = 0
    failures = []

    for case in cases:
        total += 1
        decision = resolver.resolve(
            case["question"],
            case.get("last_summary", ""),
            case.get("last_sql", ""),
        )
        expected = case["expected_action"]
        if decision.action == expected:
            passed += 1
        else:
            failures.append(
                {
                    "id": case.get("id"),
                    "expected": expected,
                    "actual": decision.action,
                    "reason": decision.reason,
                    "question": case.get("question"),
                }
            )

    print(f"Follow-up resolver accuracy: {passed}/{total}")
    if failures:
        print("Failures:")
        for item in failures:
            print(json.dumps(item, ensure_ascii=True))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
