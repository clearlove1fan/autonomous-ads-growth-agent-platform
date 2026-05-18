import subprocess
import sys


def test_negative_demo_verification_script() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/verify_negative_demos.py"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Negative demo verification passed" in result.stdout
    assert "LLM_PLANNER_INVALID_TOOL_PLAN" in result.stdout
    assert "HTTP 409 IDEMPOTENCY_KEY_REUSED" in result.stdout
    assert "HTTP 409 PERFORMANCE_EVENT_ID_CONFLICT" in result.stdout
