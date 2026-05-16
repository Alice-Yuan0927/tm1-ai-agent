import json
from pathlib import Path

from backend.ai.mdx_planner import try_plan_mdx
from backend.ai.result_validators import static_mdx_schema_issue


def _cases():
    path = Path(__file__).parent / "fixtures" / "eval_cases.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def test_deterministic_eval_cases():
    for case in _cases():
        plan = try_plan_mdx(case["question"], case["schema"])
        assert plan is not None, case["id"]
        mdx = plan.mdx
        assert static_mdx_schema_issue(mdx, case["schema"]) is None, case["id"]
        for expected in case["expect"].get("contains", []):
            assert expected in mdx, case["id"]
        for forbidden in case["expect"].get("forbidden", []):
            assert forbidden not in mdx, case["id"]
