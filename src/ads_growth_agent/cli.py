import json
from pathlib import Path

import sqlalchemy as sa
import typer
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from ads_growth_agent import __version__
from ads_growth_agent.brief_intake import parse_advertiser_brief
from ads_growth_agent.config import get_settings
from ads_growth_agent.contracts import (
    AdvertiserBrief,
    AdvertiserBriefIntakeRequest,
    GrowthStrategyRequest,
    StrategyJobListResponse,
    StrategyJobStatus,
)
from ads_growth_agent.evaluation import load_eval_cases, run_local_eval_suite
from ads_growth_agent.logging_config import configure_logging
from ads_growth_agent.outbox import process_configured_outbox
from ads_growth_agent.persistence.knowledge_seed import seed_default_knowledge
from ads_growth_agent.strategy import StrategyGenerationError, generate_growth_strategy
from ads_growth_agent.strategy_job_store_factory import build_configured_strategy_job_store
from ads_growth_agent.strategy_job_worker import process_configured_strategy_jobs

app = typer.Typer(
    help="Autonomous Ads Growth Agent Platform CLI.",
    no_args_is_help=True,
)
BRIEF_FILE_ARGUMENT = typer.Argument(
    ...,
    exists=True,
    dir_okay=False,
    readable=True,
    help="Path to an advertiser brief JSON file.",
)
EVAL_FILE_ARGUMENT = typer.Argument(
    ...,
    exists=True,
    dir_okay=False,
    readable=True,
    help="Path to a local evaluation cases JSON file.",
)
STRATEGY_JOB_STATUS_OPTION = typer.Option(None, "--status")
STRATEGY_JOB_ADVERTISER_ID_OPTION = typer.Option(None, "--advertiser-id")
STRATEGY_JOB_LIST_LIMIT_OPTION = typer.Option(50, "--limit", min=1, max=100)
STRATEGY_JOB_ID_ARGUMENT = typer.Argument(..., help="Strategy job ID.")
STRATEGY_JOB_REQUESTED_BY_OPTION = typer.Option(
    "cli",
    "--requested-by",
    help="Operator or automation identifier recorded in retry metadata.",
)
STRATEGY_JOB_CANCEL_REASON_OPTION = typer.Option(
    None,
    "--reason",
    help="Human-readable cancellation reason.",
)
BRIEF_TEXT_ARGUMENT = typer.Argument(
    ...,
    help="Plain-language advertiser goal or campaign brief.",
)
BRIEF_TEXT_ADVERTISER_ID_OPTION = typer.Option(None, "--advertiser-id")
BRIEF_TEXT_TARGET_MARKET_OPTION = typer.Option("United States", "--target-market")
BRIEF_TEXT_CURRENCY_OPTION = typer.Option("USD", "--currency")
BRIEF_TEXT_DURATION_OPTION = typer.Option(14, "--duration-days", min=1, max=365)


@app.command()
def health() -> None:
    """Print local service health information."""
    settings = get_settings()
    typer.echo(
        {
            "status": "ok",
            "service": "ads-growth-agent",
            "version": __version__,
            "environment": settings.ads_growth_env,
        }
    )


@app.command()
def plan(brief_file: Path = BRIEF_FILE_ARGUMENT) -> None:
    """Generate a deterministic draft growth strategy from an advertiser brief."""
    try:
        payload = json.loads(brief_file.read_text())
        request = _parse_strategy_request(payload)
        response = generate_growth_strategy(request.brief)
    except json.JSONDecodeError as exc:
        typer.echo(f"Invalid JSON: {exc}", err=True)
        raise typer.Exit(2) from exc
    except ValidationError as exc:
        typer.echo(json.dumps(exc.errors(include_url=False), indent=2), err=True)
        raise typer.Exit(2) from exc
    except StrategyGenerationError as exc:
        typer.echo(f"Strategy generation failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(response.model_dump_json(indent=2))


@app.command("parse-brief-text")
def parse_brief_text(
    text: str = BRIEF_TEXT_ARGUMENT,
    advertiser_id: str | None = BRIEF_TEXT_ADVERTISER_ID_OPTION,
    target_market: str = BRIEF_TEXT_TARGET_MARKET_OPTION,
    currency: str = BRIEF_TEXT_CURRENCY_OPTION,
    duration_days: int = BRIEF_TEXT_DURATION_OPTION,
) -> None:
    """Parse a plain-language advertiser request into a structured brief."""
    try:
        settings = get_settings()
        response = parse_advertiser_brief(
            AdvertiserBriefIntakeRequest(
                text=text,
                advertiser_id=advertiser_id,
                default_target_market=target_market,
                default_currency=currency,
                default_duration_days=duration_days,
            ),
            settings=settings,
        )
    except ValidationError as exc:
        typer.echo(json.dumps(exc.errors(include_url=False), indent=2), err=True)
        raise typer.Exit(2) from exc

    typer.echo(response.model_dump_json(indent=2))


@app.command("plan-text")
def plan_text(
    text: str = BRIEF_TEXT_ARGUMENT,
    advertiser_id: str | None = BRIEF_TEXT_ADVERTISER_ID_OPTION,
    target_market: str = BRIEF_TEXT_TARGET_MARKET_OPTION,
    currency: str = BRIEF_TEXT_CURRENCY_OPTION,
    duration_days: int = BRIEF_TEXT_DURATION_OPTION,
) -> None:
    """Generate a growth strategy directly from a plain-language advertiser request."""
    try:
        settings = get_settings()
        intake = parse_advertiser_brief(
            AdvertiserBriefIntakeRequest(
                text=text,
                advertiser_id=advertiser_id,
                default_target_market=target_market,
                default_currency=currency,
                default_duration_days=duration_days,
            ),
            settings=settings,
        )
        response = generate_growth_strategy(intake.brief, settings=settings)
    except ValidationError as exc:
        typer.echo(json.dumps(exc.errors(include_url=False), indent=2), err=True)
        raise typer.Exit(2) from exc
    except StrategyGenerationError as exc:
        typer.echo(f"Strategy generation failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(
        json.dumps(
            {
                "intake": intake.model_dump(mode="json"),
                "growth_strategy": response.model_dump(mode="json"),
            },
            indent=2,
        )
    )


@app.command("seed-knowledge")
def seed_knowledge() -> None:
    """Seed the default RAG and advertiser-memory corpus into PostgreSQL."""
    settings = get_settings()
    engine = sa.create_engine(settings.database_url, pool_pre_ping=True)
    try:
        seed_default_knowledge(engine, tenant_id=settings.tenant_id)
    finally:
        engine.dispose()

    typer.echo(
        json.dumps(
            {
                "status": "ok",
                "tenant_id": settings.tenant_id,
                "database_url": _safe_database_url(settings.database_url),
                "seeded_corpus": "default_knowledge_documents",
            },
            indent=2,
        )
    )


@app.command("process-outbox")
def process_outbox(
    limit: int = typer.Option(100, "--limit", min=1, max=1_000),
    worker_id: str | None = typer.Option(None, "--worker-id"),
) -> None:
    """Process a bounded batch of durable outbox events."""
    settings = get_settings()
    report = process_configured_outbox(settings, limit=limit, worker_id=worker_id)
    typer.echo(report.model_dump_json(indent=2))


@app.command("process-strategy-jobs")
def process_strategy_jobs(
    limit: int = typer.Option(10, "--limit", min=1, max=100),
    worker_id: str | None = typer.Option(None, "--worker-id"),
    lock_seconds: int = typer.Option(1_800, "--lock-seconds", min=30, max=86_400),
) -> None:
    """Process a bounded batch of queued strategy-generation jobs."""
    settings = get_settings()
    report = process_configured_strategy_jobs(
        settings,
        limit=limit,
        worker_id=worker_id,
        lock_seconds=lock_seconds,
    )
    typer.echo(report.model_dump_json(indent=2))


@app.command("list-strategy-jobs")
def list_strategy_jobs(
    status: StrategyJobStatus | None = STRATEGY_JOB_STATUS_OPTION,
    advertiser_id: str | None = STRATEGY_JOB_ADVERTISER_ID_OPTION,
    limit: int = STRATEGY_JOB_LIST_LIMIT_OPTION,
) -> None:
    """List recent strategy-generation jobs for queue inspection."""
    settings = get_settings()
    store = build_configured_strategy_job_store(settings)
    jobs = store.list_jobs(status=status, advertiser_id=advertiser_id, limit=limit)
    response = StrategyJobListResponse(
        items=jobs,
        count=len(jobs),
        limit=limit,
        status=status,
        advertiser_id=advertiser_id,
    )
    typer.echo(response.model_dump_json(indent=2))


@app.command("retry-strategy-job")
def retry_strategy_job(
    job_id: str = STRATEGY_JOB_ID_ARGUMENT,
    requested_by: str = STRATEGY_JOB_REQUESTED_BY_OPTION,
) -> None:
    """Manually requeue a failed strategy-generation job."""
    settings = get_settings()
    store = build_configured_strategy_job_store(settings)
    job = store.get_job(job_id)
    if job is None:
        typer.echo(f"Strategy job not found: {job_id}", err=True)
        raise typer.Exit(1)
    if job.status != StrategyJobStatus.FAILED:
        typer.echo(
            f"Strategy job is not retryable: {job_id} status={job.status.value}",
            err=True,
        )
        raise typer.Exit(1)
    retried = store.retry_failed(
        job_id,
        max_attempts=settings.strategy_job_max_attempts,
        requested_by=requested_by.strip() or "cli",
    )
    if retried is None:
        typer.echo(f"Strategy job could not be retried: {job_id}", err=True)
        raise typer.Exit(1)
    typer.echo(retried.model_dump_json(indent=2))


@app.command("cancel-strategy-job")
def cancel_strategy_job(
    job_id: str = STRATEGY_JOB_ID_ARGUMENT,
    requested_by: str = STRATEGY_JOB_REQUESTED_BY_OPTION,
    reason: str | None = STRATEGY_JOB_CANCEL_REASON_OPTION,
) -> None:
    """Manually cancel a queued or running strategy-generation job."""
    settings = get_settings()
    store = build_configured_strategy_job_store(settings)
    job = store.get_job(job_id)
    if job is None:
        typer.echo(f"Strategy job not found: {job_id}", err=True)
        raise typer.Exit(1)
    if job.status not in {StrategyJobStatus.QUEUED, StrategyJobStatus.RUNNING}:
        typer.echo(
            f"Strategy job is not cancellable: {job_id} status={job.status.value}",
            err=True,
        )
        raise typer.Exit(1)
    cancelled = store.cancel(
        job_id,
        requested_by=requested_by.strip() or "cli",
        reason=reason.strip() if reason else None,
    )
    if cancelled is None:
        typer.echo(f"Strategy job could not be cancelled: {job_id}", err=True)
        raise typer.Exit(1)
    typer.echo(cancelled.model_dump_json(indent=2))


@app.command("eval")
def run_eval(eval_file: Path = EVAL_FILE_ARGUMENT) -> None:
    """Run deterministic local evaluators against curated advertiser briefs."""
    try:
        cases = load_eval_cases(eval_file)
        report = run_local_eval_suite(cases)
    except json.JSONDecodeError as exc:
        typer.echo(f"Invalid JSON: {exc}", err=True)
        raise typer.Exit(2) from exc
    except ValidationError as exc:
        typer.echo(json.dumps(exc.errors(include_url=False), indent=2), err=True)
        raise typer.Exit(2) from exc

    typer.echo(report.model_dump_json(indent=2))


def _parse_strategy_request(payload: object) -> GrowthStrategyRequest:
    if isinstance(payload, dict) and "brief" in payload:
        return GrowthStrategyRequest.model_validate(payload)
    return GrowthStrategyRequest(brief=AdvertiserBrief.model_validate(payload))


def _safe_database_url(database_url: str) -> str:
    return make_url(database_url).render_as_string(hide_password=True)


@app.callback(invoke_without_command=True)
def main(version: bool = typer.Option(False, "--version", help="Show version and exit.")) -> None:
    configure_logging()
    if version:
        typer.echo(__version__)
        raise typer.Exit()
