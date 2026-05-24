from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal, Protocol

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from ads_growth_agent.contracts import (
    AgentRunDetailResponse,
    AgentRunStepRecord,
    FinalGrowthStrategy,
)
from ads_growth_agent.persistence.run_store import DEFAULT_TENANT_ID
from ads_growth_agent.persistence.schema import agent_run_steps, agent_runs


class AgentRunReadStore(Protocol):
    def get_run(self, run_id: str) -> AgentRunDetailResponse | None:
        """Return one persisted run detail for the configured tenant."""

    def list_runs(
        self,
        *,
        status: Literal["running", "completed", "failed"] | None = None,
        limit: int = 50,
    ) -> list[AgentRunDetailResponse]:
        """Return recent persisted runs for operator inspection."""


class NoopAgentRunReadStore:
    def get_run(self, run_id: str) -> AgentRunDetailResponse | None:
        return None

    def list_runs(
        self,
        *,
        status: Literal["running", "completed", "failed"] | None = None,
        limit: int = 50,
    ) -> list[AgentRunDetailResponse]:
        return []


class PostgresAgentRunReadStore:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._bind = bind
        self._tenant_id = tenant_id

    def get_run(self, run_id: str) -> AgentRunDetailResponse | None:
        with _connection(self._bind) as connection:
            run = connection.execute(
                sa.select(agent_runs)
                .where(agent_runs.c.tenant_id == self._tenant_id)
                .where(agent_runs.c.run_id == run_id)
            ).mappings().one_or_none()
            if run is None:
                return None

            steps = connection.execute(
                sa.select(agent_run_steps)
                .where(agent_run_steps.c.tenant_id == self._tenant_id)
                .where(agent_run_steps.c.run_id == run_id)
                .order_by(agent_run_steps.c.step_index.asc())
            ).mappings().all()

        return _row_to_run_detail(run, steps=steps)

    def list_runs(
        self,
        *,
        status: Literal["running", "completed", "failed"] | None = None,
        limit: int = 50,
    ) -> list[AgentRunDetailResponse]:
        statement = sa.select(agent_runs).where(agent_runs.c.tenant_id == self._tenant_id)
        if status is not None:
            statement = statement.where(agent_runs.c.status == status)

        statement = statement.order_by(
            agent_runs.c.created_at.desc(),
            agent_runs.c.run_id.desc(),
        ).limit(limit)
        with _connection(self._bind) as connection:
            rows = connection.execute(statement).mappings().all()
        return [_row_to_run_detail(row, steps=[]) for row in rows]


@contextmanager
def _connection(bind: Engine | Connection) -> Iterator[Connection]:
    if isinstance(bind, Engine):
        with bind.connect() as connection:
            yield connection
    else:
        yield bind


def _row_to_run_detail(row, *, steps) -> AgentRunDetailResponse:
    metadata = dict(row["metadata"] or {})
    final_strategy = (
        FinalGrowthStrategy.model_validate(row["final_strategy_json"])
        if row["final_strategy_json"] is not None
        else None
    )
    return AgentRunDetailResponse(
        run_id=row["run_id"],
        execution_id=str(metadata.get("execution_id") or row["run_id"]),
        strategy_id=row["strategy_id"],
        advertiser_id=row["advertiser_id"],
        objective=row["objective"],
        status=row["status"],
        trace_id=row["trace_id"],
        node_path=list(row["node_path"] or []),
        final_strategy=final_strategy,
        error_summary=list(row["error_summary"] or []),
        metadata=metadata,
        steps=[
            AgentRunStepRecord(
                step_index=step["step_index"],
                node_name=step["node_name"],
                status=step["status"],
                input_json=dict(step["input_json"] or {}),
                output_json=dict(step["output_json"] or {}),
                error_json=dict(step["error_json"]) if step["error_json"] else None,
                latency_ms=step["latency_ms"],
                created_at=step["created_at"],
            )
            for step in steps
        ],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )
