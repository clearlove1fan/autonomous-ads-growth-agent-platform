#!/usr/bin/env python3
"""Run and validate the curated Phase 1 MVP demo."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from typer.testing import CliRunner  # noqa: E402

from ads_growth_agent.cli import app  # noqa: E402

EXPECTED_DEMO_CASE = "phase1_fitness_app_underperforming_feedback"
EXPECTED_NODE_PATH = ["planner", "retriever", "tool_executor", "critic", "finalizer"]
EXPECTED_TOOLS = {
    "recommend_audience",
    "generate_creative_brief",
    "optimize_budget",
    "estimate_performance",
    "create_campaign_draft",
}


class DemoVerificationError(Exception):
    """Raised when the curated demo output violates the expected MVP contract."""

    def __init__(self, issues: list[str]) -> None:
        super().__init__("\n".join(issues))
        self.issues = issues


def run_demo_payload() -> dict[str, Any]:
    result = CliRunner().invoke(app, ["demo"])
    if result.exit_code != 0:
        stderr = getattr(result, "stderr", "")
        raise DemoVerificationError(
            [
                "ads-growth-agent demo failed",
                f"exit_code={result.exit_code}",
                f"stdout={result.stdout.strip()}",
                f"stderr={stderr.strip()}",
            ]
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DemoVerificationError(
            [f"ads-growth-agent demo returned non-JSON stdout: {exc}"]
        ) from exc


def validate_demo_payload(payload: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []

    intake = payload.get("intake", {})
    growth_strategy = payload.get("growth_strategy", {})
    strategy = growth_strategy.get("strategy", {})
    run_metadata = growth_strategy.get("run_metadata", {})
    tool_results = growth_strategy.get("tool_results", [])
    feedback = payload.get("feedback_analysis", {})

    _expect(payload.get("demo_case") == EXPECTED_DEMO_CASE, issues, "unexpected demo_case")
    _expect(intake.get("mode") == "heuristic", issues, "intake should use heuristic mode")
    _expect(strategy.get("advertiser_id") == "adv_fitness_001", issues, "unexpected advertiser")
    _expect(strategy.get("objective") == "registrations", issues, "unexpected objective")
    _expect(
        run_metadata.get("node_path") == EXPECTED_NODE_PATH,
        issues,
        "graph node path changed",
    )
    _expect(run_metadata.get("tool_count") == 5, issues, "unexpected tool count")
    _expect(run_metadata.get("failed_tool_count") == 0, issues, "tool failure detected")
    _expect(
        {result.get("tool_name") for result in tool_results} == EXPECTED_TOOLS,
        issues,
        "tool set changed",
    )
    _expect(
        strategy.get("campaign_draft", {}).get("status") == "draft",
        issues,
        "campaign draft should remain draft-only",
    )
    _expect(
        strategy.get("feedback_context", {}).get("strategy_id") == strategy.get("strategy_id"),
        issues,
        "feedback_context should link back to strategy_id",
    )
    _expect(
        feedback.get("health_status") == "underperforming",
        issues,
        "feedback analysis should classify demo event as underperforming",
    )
    _expect(
        feedback.get("strategy_id") == strategy.get("strategy_id"),
        issues,
        "feedback analysis should link back to strategy_id",
    )
    _expect(
        all(
            item.get("requires_human_approval") is True
            for item in feedback.get("recommendations", [])
        ),
        issues,
        "all feedback recommendations should require human approval",
    )

    matched_rules = feedback.get("matched_strategy_rules", [])
    matched_rule_ids = [rule.get("rule_id", "") for rule in matched_rules]
    _expect(
        any(rule_id.endswith(":rule:cpa_guardrail") for rule_id in matched_rule_ids),
        issues,
        "feedback should match the CPA guardrail optimization rule",
    )

    source_types = {source.get("source_type") for source in strategy.get("sources", [])}
    _expect("advertiser_memory" in source_types, issues, "advertiser memory source missing")
    _expect("rag_document" in source_types, issues, "RAG document source missing")

    if issues:
        raise DemoVerificationError(issues)

    return {
        "demo_case": payload["demo_case"],
        "input_text": payload["input_text"],
        "intake_mode": intake["mode"],
        "advertiser_id": strategy["advertiser_id"],
        "objective": strategy["objective"],
        "budget": strategy["campaign_objective"]["budget"],
        "currency": strategy["campaign_objective"]["currency"],
        "node_path": run_metadata["node_path"],
        "strategy_id": strategy["strategy_id"],
        "draft_id": strategy["campaign_draft"]["draft_id"],
        "draft_status": strategy["campaign_draft"]["status"],
        "estimated_conversions": strategy["performance_forecast"]["estimated_conversions"],
        "estimated_cpa": strategy["performance_forecast"]["estimated_cpa"],
        "feedback_status": feedback["health_status"],
        "matched_rules": matched_rule_ids,
        "recommendation_types": [
            item["action_type"] for item in feedback.get("recommendations", [])
        ],
        "source_types": sorted(source_type for source_type in source_types if source_type),
    }


def render_summary(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Phase 1 MVP demo verification passed",
            f"Demo case: {summary['demo_case']}",
            f"Input: {summary['input_text']}",
            (
                "Intake: "
                f"{summary['intake_mode']} -> advertiser={summary['advertiser_id']}, "
                f"objective={summary['objective']}, "
                f"budget={summary['currency']} {summary['budget']}"
            ),
            f"Graph path: {' -> '.join(summary['node_path'])}",
            (
                "Strategy: "
                f"{summary['strategy_id']} -> draft={summary['draft_id']} "
                f"({summary['draft_status']})"
            ),
            (
                "Forecast: "
                f"{summary['estimated_conversions']} conversions at CPA "
                f"{summary['currency']} {summary['estimated_cpa']}"
            ),
            (
                "Feedback: "
                f"{summary['feedback_status']} -> matched_rules="
                f"{', '.join(summary['matched_rules'])}"
            ),
            (
                "Recommendations: "
                f"{', '.join(summary['recommendation_types'])} "
                "(draft-only, human approval required)"
            ),
            f"Sources: {', '.join(summary['source_types'])}",
        ]
    )


def _expect(condition: bool, issues: list[str], message: str) -> None:
    if not condition:
        issues.append(message)


def main() -> int:
    try:
        payload = run_demo_payload()
        summary = validate_demo_payload(payload)
    except DemoVerificationError as exc:
        print("Phase 1 MVP demo verification failed", file=sys.stderr)
        for issue in exc.issues:
            print(f"- {issue}", file=sys.stderr)
        return 1

    print(render_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
