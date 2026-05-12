from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from ads_growth_agent.contracts import (
    AdvertiserBrief,
    GrowthStrategyResponse,
    RunMetadata,
    ToolResult,
)
from ads_growth_agent.persistence.identity import upsert_tenant_and_advertiser
from ads_growth_agent.persistence.partitioning import partition_bucket
from ads_growth_agent.persistence.schema import (
    agent_run_steps,
    agent_runs,
)

DEFAULT_TENANT_ID = "default"


class AgentRunStore(Protocol):
    def record_started(self, brief: AdvertiserBrief, run_metadata: RunMetadata) -> None:
        """Persist a running strategy execution."""

    def record_completed(self, brief: AdvertiserBrief, response: GrowthStrategyResponse) -> None:
        """Persist a completed strategy run."""

    def record_failed(
        self,
        brief: AdvertiserBrief,
        run_metadata: RunMetadata,
        *,
        tool_results: list[ToolResult],
        error_message: str,
    ) -> None:
        """Persist a failed strategy run."""


class NoopAgentRunStore:
    def record_started(self, brief: AdvertiserBrief, run_metadata: RunMetadata) -> None:
        return None

    def record_completed(self, brief: AdvertiserBrief, response: GrowthStrategyResponse) -> None:
        return None

    def record_failed(
        self,
        brief: AdvertiserBrief,
        run_metadata: RunMetadata,
        *,
        tool_results: list[ToolResult],
        error_message: str,
    ) -> None:
        return None


class PostgresAgentRunStore:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._bind = bind
        self._tenant_id = tenant_id

    def record_started(self, brief: AdvertiserBrief, run_metadata: RunMetadata) -> None:
        with _transaction(self._bind) as connection:
            upsert_tenant_and_advertiser(
                connection,
                brief,
                tenant_id=self._tenant_id,
                upserted_by="agent_run_store",
            )
            _upsert_agent_run(
                connection,
                brief,
                run_metadata,
                tenant_id=self._tenant_id,
                status="running",
                final_strategy_json=None,
                error_summary=[],
            )

    def record_completed(self, brief: AdvertiserBrief, response: GrowthStrategyResponse) -> None:
        with _transaction(self._bind) as connection:
            upsert_tenant_and_advertiser(
                connection,
                brief,
                tenant_id=self._tenant_id,
                upserted_by="agent_run_store",
            )
            _upsert_agent_run(
                connection,
                brief,
                response.run_metadata,
                tenant_id=self._tenant_id,
                status="completed",
                final_strategy_json=response.strategy.model_dump(mode="json"),
                error_summary=[],
            )
            _replace_run_steps(
                connection,
                response.run_metadata,
                tenant_id=self._tenant_id,
                steps=_completed_steps(response),
            )

    def record_failed(
        self,
        brief: AdvertiserBrief,
        run_metadata: RunMetadata,
        *,
        tool_results: list[ToolResult],
        error_message: str,
    ) -> None:
        with _transaction(self._bind) as connection:
            upsert_tenant_and_advertiser(
                connection,
                brief,
                tenant_id=self._tenant_id,
                upserted_by="agent_run_store",
            )
            error_summary = run_metadata.error_summary or [error_message]
            _upsert_agent_run(
                connection,
                brief,
                run_metadata,
                tenant_id=self._tenant_id,
                status="failed",
                final_strategy_json=None,
                error_summary=error_summary,
            )
            _replace_run_steps(
                connection,
                run_metadata,
                tenant_id=self._tenant_id,
                steps=_failed_steps(run_metadata, tool_results, error_message),
            )


@contextmanager
def _transaction(bind: Engine | Connection) -> Iterator[Connection]:
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            yield connection
    else:
        yield bind


def _upsert_agent_run(
    connection: Connection,
    brief: AdvertiserBrief,
    run_metadata: RunMetadata,
    *,
    tenant_id: str,
    status: str,
    final_strategy_json: dict[str, Any] | None,
    error_summary: list[str],
) -> None:
    strategy_id = _strategy_id(run_metadata)
    completed_at = sa.func.now() if status in {"completed", "failed"} else None
    metadata = {
        "strategy_id": strategy_id,
        "execution_id": run_metadata.execution_id,
        "langsmith_project": run_metadata.langsmith_project,
        "tracing_enabled": run_metadata.tracing_enabled,
        "tool_count": run_metadata.tool_count,
        "failed_tool_count": run_metadata.failed_tool_count,
        "product_name": brief.product_name,
        "product_category": brief.product_category,
        "target_market": brief.target_market,
        "run_persistence": "postgres",
    }
    values = {
        "tenant_id": tenant_id,
        "run_id": run_metadata.run_id,
        "strategy_id": strategy_id,
        "advertiser_id": brief.advertiser_id,
        "objective": brief.objective.value,
        "status": status,
        "trace_id": run_metadata.trace_id,
        "node_path": run_metadata.node_path,
        "final_strategy_json": final_strategy_json,
        "error_summary": error_summary,
        "metadata": metadata,
        "partition_key": run_metadata.run_id,
        "partition_bucket": partition_bucket(run_metadata.run_id),
        "completed_at": completed_at,
    }
    stmt = (
        pg_insert(agent_runs)
        .values(values)
        .on_conflict_do_update(
            index_elements=[agent_runs.c.tenant_id, agent_runs.c.run_id],
            set_={
                "advertiser_id": values["advertiser_id"],
                "objective": values["objective"],
                "status": values["status"],
                "strategy_id": values["strategy_id"],
                "trace_id": values["trace_id"],
                "node_path": values["node_path"],
                "final_strategy_json": values["final_strategy_json"],
                "error_summary": values["error_summary"],
                "metadata": values["metadata"],
                "partition_key": values["partition_key"],
                "partition_bucket": values["partition_bucket"],
                "completed_at": values["completed_at"],
            },
        )
    )
    connection.execute(stmt)


def _replace_run_steps(
    connection: Connection,
    run_metadata: RunMetadata,
    *,
    tenant_id: str,
    steps: list[dict[str, Any]],
) -> None:
    connection.execute(
        agent_run_steps.delete()
        .where(agent_run_steps.c.tenant_id == tenant_id)
        .where(agent_run_steps.c.run_id == run_metadata.run_id)
    )
    for index, step in enumerate(steps):
        connection.execute(
            agent_run_steps.insert().values(
                tenant_id=tenant_id,
                run_id=run_metadata.run_id,
                strategy_id=_strategy_id(run_metadata),
                step_index=index,
                node_name=step["node_name"],
                status=step["status"],
                input_json=step.get("input_json", {}),
                output_json=step.get("output_json", {}),
                error_json=step.get("error_json"),
                latency_ms=step.get("latency_ms", 0),
                partition_key=run_metadata.run_id,
                partition_bucket=partition_bucket(run_metadata.run_id),
            )
        )


def _completed_steps(response: GrowthStrategyResponse) -> list[dict[str, Any]]:
    tool_summaries = [
        summary.model_dump(mode="json") for summary in response.run_metadata.tool_summaries
    ]
    steps: list[dict[str, Any]] = []
    for node_name in response.node_path:
        output_json: dict[str, Any] = {"node_name": node_name}
        latency_ms = 0
        if node_name == "tool_executor":
            output_json["tool_summaries"] = tool_summaries
            latency_ms = sum(summary.latency_ms for summary in response.run_metadata.tool_summaries)
        elif node_name == "critic":
            output_json["critique"] = response.strategy.critique.model_dump(mode="json")
        elif node_name == "finalizer":
            output_json["strategy_id"] = response.strategy.strategy_id
            output_json["source_count"] = len(response.strategy.sources)

        steps.append(
            {
                "node_name": node_name,
                "status": "completed",
                "input_json": _step_input_json(response.run_metadata),
                "output_json": output_json,
                "latency_ms": latency_ms,
            }
        )
    return steps


def _failed_steps(
    run_metadata: RunMetadata,
    tool_results: list[ToolResult],
    error_message: str,
) -> list[dict[str, Any]]:
    node_path = run_metadata.node_path or ["unknown"]
    steps: list[dict[str, Any]] = []
    for index, node_name in enumerate(node_path):
        is_failed_step = index == len(node_path) - 1
        steps.append(
            {
                "node_name": node_name,
                "status": "failed" if is_failed_step else "completed",
                "input_json": _step_input_json(run_metadata),
                "output_json": {
                    "tool_summaries": [
                        summary.model_dump(mode="json")
                        for summary in run_metadata.tool_summaries
                    ],
                }
                if node_name == "tool_executor"
                else {"node_name": node_name},
                "error_json": _failure_error_json(tool_results, error_message)
                if is_failed_step
                else None,
                "latency_ms": sum(result.latency_ms for result in tool_results)
                if node_name == "tool_executor"
                else 0,
            }
        )
    return steps


def _failure_error_json(tool_results: list[ToolResult], error_message: str) -> dict[str, Any]:
    failed_results = [result for result in tool_results if not result.success]
    latest_failure = failed_results[-1] if failed_results else None
    return {
        "message": error_message,
        "tool_name": latest_failure.tool_name if latest_failure else None,
        "error": latest_failure.error.model_dump(mode="json")
        if latest_failure and latest_failure.error
        else None,
    }


def _strategy_id(run_metadata: RunMetadata) -> str:
    return run_metadata.strategy_id or run_metadata.run_id


def _step_input_json(run_metadata: RunMetadata) -> dict[str, Any]:
    return {
        "run_id": run_metadata.run_id,
        "execution_id": run_metadata.execution_id or run_metadata.run_id,
        "strategy_id": run_metadata.strategy_id,
    }
