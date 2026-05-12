import json
from decimal import Decimal
from io import StringIO
from pathlib import Path

from ads_growth_agent.contracts import AdvertiserBrief
from ads_growth_agent.evaluation import load_eval_cases, run_local_eval_suite
from ads_growth_agent.logging_config import (
    configure_logging,
    log_strategy_run_completed,
)
from ads_growth_agent.strategy import generate_mock_growth_strategy


def test_strategy_run_log_is_structured_json() -> None:
    response = generate_mock_growth_strategy(_brief())
    stream = StringIO()
    logger = configure_logging(stream=stream, force=True)

    try:
        log_strategy_run_completed(response)
        record = json.loads(stream.getvalue().splitlines()[0])
    finally:
        logger.handlers.clear()

    assert record["event"] == "growth_strategy.run_completed"
    assert record["run_id"] == response.run_metadata.run_id
    assert record["execution_id"] == response.run_metadata.run_id
    assert record["trace_id"] == response.run_metadata.trace_id
    assert record["advertiser_id"] == "adv_logging"
    assert record["strategy_id"] == response.strategy.strategy_id
    assert record["tool_count"] == 5
    assert "api_key" not in record


def test_evaluation_suite_log_is_structured_json() -> None:
    stream = StringIO()
    logger = configure_logging(stream=stream, force=True)

    try:
        report = run_local_eval_suite(load_eval_cases(Path("examples/eval_cases.json"))[:1])
        records = [json.loads(line) for line in stream.getvalue().splitlines()]
    finally:
        logger.handlers.clear()

    suite_record = records[-1]
    assert suite_record["event"] == "evaluation.suite_completed"
    assert suite_record["suite_id"] == report.suite_id
    assert suite_record["total_cases"] == 1
    assert suite_record["passed_cases"] == 1
    assert suite_record["pass_rate"] == 1.0


def _brief() -> AdvertiserBrief:
    return AdvertiserBrief(
        advertiser_id="adv_logging",
        product_name="Loggable App",
        product_category="fitness app",
        objective="registrations",
        budget=Decimal("2000.00"),
        currency="USD",
        duration_days=14,
        target_market="United States",
        primary_kpi="trial registrations",
        target_cpa=Decimal("20.00"),
    )
