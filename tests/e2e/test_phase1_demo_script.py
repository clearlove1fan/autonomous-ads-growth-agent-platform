import subprocess
import sys


def test_phase1_demo_verification_script() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/verify_phase1_demo.py"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Phase 1 MVP demo verification passed" in result.stdout
    assert (
        "Graph path: planner -> retriever -> tool_executor -> critic -> finalizer"
        in result.stdout
    )
    assert "Feedback: underperforming" in result.stdout
    assert "Recommendations: adjust_budget, refresh_creative" in result.stdout
