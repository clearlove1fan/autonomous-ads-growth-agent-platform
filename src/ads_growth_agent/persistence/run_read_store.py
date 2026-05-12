from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

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


class NoopAgentRunReadStore:
    def get_run(self, run_id: str) -> AgentRunDetailResponse | None:
        return None


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

        metadata = dict(run["metadata"] or {})
        final_strategy = (
            FinalGrowthStrategy.model_validate(run["final_strategy_json"])
            if run["final_strategy_json"] is not None
            else None
        )
        return AgentRunDetailResponse(
            run_id=run["run_id"],
            execution_id=str(metadata.get("execution_id") or run["run_id"]),
            strategy_id=run["strategy_id"],
            advertiser_id=run["advertiser_id"],
            objective=run["objective"],
            status=run["status"],
            trace_id=run["trace_id"],
            node_path=list(run["node_path"] or []),
            final_strategy=final_strategy,
            error_summary=list(run["error_summary"] or []),
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
            created_at=run["created_at"],
            completed_at=run["completed_at"],
        )


@contextmanager
def _connection(bind: Engine | Connection) -> Iterator[Connection]:
    if isinstance(bind, Engine):
        with bind.connect() as connection:
            yield connection
    else:
        yield bind
