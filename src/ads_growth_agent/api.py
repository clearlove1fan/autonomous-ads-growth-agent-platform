from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ads_growth_agent import __version__
from ads_growth_agent.config import get_settings
from ads_growth_agent.contracts import GrowthStrategyRequest, GrowthStrategyResponse
from ads_growth_agent.logging_config import configure_logging
from ads_growth_agent.strategy import StrategyGenerationError, generate_growth_strategy


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str


app = FastAPI(
    title="Autonomous Ads Growth Agent Platform",
    version=__version__,
    description="AI agent platform for advertiser growth automation.",
)
configure_logging()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service="ads-growth-agent",
        version=__version__,
        environment=settings.ads_growth_env,
    )


@app.post("/growth-strategies", response_model=GrowthStrategyResponse)
def create_growth_strategy(request: GrowthStrategyRequest) -> GrowthStrategyResponse:
    try:
        return generate_growth_strategy(request.brief)
    except StrategyGenerationError as exc:
        error = exc.tool_result.error
        raise HTTPException(
            status_code=502,
            detail={
                "message": str(exc),
                "tool_name": exc.tool_result.tool_name,
                "error_code": error.code if error else "TOOL_FAILURE",
                "run_metadata": (
                    exc.run_metadata.model_dump(mode="json") if exc.run_metadata else None
                ),
            },
        ) from exc
