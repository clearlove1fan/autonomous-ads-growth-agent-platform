import typer

from ads_growth_agent import __version__
from ads_growth_agent.config import get_settings

app = typer.Typer(help="Autonomous Ads Growth Agent Platform CLI.")


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


@app.callback()
def main(version: bool = typer.Option(False, "--version", help="Show version and exit.")) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()
