from __future__ import annotations

import logging
import sys
from typing import IO, TYPE_CHECKING, Any

from pythonjsonlogger.json import JsonFormatter

from ads_growth_agent.config import Settings, get_settings

if TYPE_CHECKING:
    from ads_growth_agent.contracts import GrowthStrategyResponse, RunMetadata, ToolResult
    from ads_growth_agent.evaluation import EvaluationSuiteReport


LOGGER_NAME = "ads_growth_agent"
LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s %(message)s %(event)s %(service)s "
    "%(environment)s %(run_id)s %(execution_id)s %(trace_id)s %(advertiser_id)s "
    "%(strategy_id)s "
    "%(node_path)s %(tool_count)s %(failed_tool_count)s %(suite_id)s %(total_cases)s "
    "%(passed_cases)s %(failed_cases)s %(pass_rate)s %(error_code)s"
)


def configure_logging(
    settings: Settings | None = None,
    *,
    logger_name: str = LOGGER_NAME,
    stream: IO[str] | None = None,
    force: bool = False,
) -> logging.Logger:
    settings = settings or get_settings()
    logger = logging.getLogger(logger_name)
    if logger.handlers and not force:
        return logger

    if force:
        logger.handlers.clear()

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(
        JsonFormatter(
            LOG_FORMAT,
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    )
    logger.addHandler(handler)
    logger.setLevel(settings.ads_growth_log_level)
    logger.propagate = False
    return logger


def get_app_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def log_strategy_run_completed(response: GrowthStrategyResponse) -> None:
    metadata = response.run_metadata
    get_app_logger().info(
        "growth strategy run completed",
        extra={
            **_base_extra(),
            "event": "growth_strategy.run_completed",
            "run_id": metadata.run_id,
            "execution_id": metadata.execution_id,
            "trace_id": metadata.trace_id,
            "advertiser_id": response.strategy.advertiser_id,
            "strategy_id": metadata.strategy_id or response.strategy.strategy_id,
            "node_path": metadata.node_path,
            "tool_count": metadata.tool_count,
            "failed_tool_count": metadata.failed_tool_count,
        },
    )


def log_strategy_run_failed(
    *,
    advertiser_id: str,
    tool_result: ToolResult,
    run_metadata: RunMetadata | None,
) -> None:
    error_code = tool_result.error.code if tool_result.error else "TOOL_FAILURE"
    get_app_logger().error(
        "growth strategy run failed",
        extra={
            **_base_extra(),
            "event": "growth_strategy.run_failed",
            "run_id": run_metadata.run_id if run_metadata else None,
            "execution_id": run_metadata.execution_id if run_metadata else None,
            "trace_id": run_metadata.trace_id if run_metadata else None,
            "advertiser_id": advertiser_id,
            "strategy_id": run_metadata.strategy_id if run_metadata else None,
            "node_path": run_metadata.node_path if run_metadata else [],
            "tool_count": run_metadata.tool_count if run_metadata else 1,
            "failed_tool_count": run_metadata.failed_tool_count if run_metadata else 1,
            "error_code": error_code,
        },
    )


def log_evaluation_suite_completed(report: EvaluationSuiteReport) -> None:
    get_app_logger().info(
        "local evaluation suite completed",
        extra={
            **_base_extra(),
            "event": "evaluation.suite_completed",
            "suite_id": report.suite_id,
            "total_cases": report.total_cases,
            "passed_cases": report.passed_cases,
            "failed_cases": report.failed_cases,
            "pass_rate": report.pass_rate,
        },
    )


def _base_extra() -> dict[str, Any]:
    settings = get_settings()
    return {
        "service": "ads-growth-agent",
        "environment": settings.ads_growth_env,
    }
