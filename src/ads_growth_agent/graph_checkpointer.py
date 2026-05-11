from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.engine import make_url

from ads_growth_agent.config import Settings
from ads_growth_agent.observability import RunContext


@contextmanager
def open_configured_graph_checkpointer(settings: Settings) -> Iterator[Any | None]:
    if settings.graph_checkpointer_backend == "none":
        yield None
        return

    if settings.graph_checkpointer_backend == "memory":
        yield MemorySaver()
        return

    if settings.graph_checkpointer_backend == "postgres":
        from langgraph.checkpoint.postgres import PostgresSaver

        conn_string = psycopg_connection_string(settings.database_url)
        with PostgresSaver.from_conn_string(conn_string) as checkpointer:
            if settings.graph_checkpointer_setup:
                checkpointer.setup()
            yield checkpointer
        return

    raise ValueError(
        f"Unsupported graph checkpointer backend: {settings.graph_checkpointer_backend}"
    )


def graph_checkpoint_config(
    run_context: RunContext,
    *,
    enabled: bool,
    tenant_id: str | None = None,
) -> dict[str, Any] | None:
    if not enabled:
        return None
    thread_id = run_context.run_id if tenant_id is None else f"{tenant_id}:{run_context.run_id}"
    return {
        "configurable": {
            "thread_id": thread_id,
        }
    }


def psycopg_connection_string(database_url: str) -> str:
    url = make_url(database_url)
    if url.drivername.startswith("postgresql+"):
        url = url.set(drivername="postgresql")
    return url.render_as_string(hide_password=False)
