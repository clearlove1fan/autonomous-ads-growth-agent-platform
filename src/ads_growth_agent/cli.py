import json
from pathlib import Path

import typer
from pydantic import ValidationError

from ads_growth_agent import __version__
from ads_growth_agent.config import get_settings
from ads_growth_agent.contracts import AdvertiserBrief, GrowthStrategyRequest
from ads_growth_agent.evaluation import load_eval_cases, run_local_eval_suite
from ads_growth_agent.logging_config import configure_logging
from ads_growth_agent.strategy import StrategyGenerationError, generate_mock_growth_strategy

app = typer.Typer(help="Autonomous Ads Growth Agent Platform CLI.")
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
        response = generate_mock_growth_strategy(request.brief)
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


@app.callback()
def main(version: bool = typer.Option(False, "--version", help="Show version and exit.")) -> None:
    configure_logging()
    if version:
        typer.echo(__version__)
        raise typer.Exit()
