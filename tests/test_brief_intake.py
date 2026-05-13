import json
from decimal import Decimal

from ads_growth_agent.brief_intake import parse_advertiser_brief
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import AdvertiserBriefIntakeRequest
from ads_growth_agent.llm import LLMCompletion, ModelGatewayError


def test_heuristic_brief_intake_parses_plain_language_advertiser_goal() -> None:
    response = parse_advertiser_brief(
        AdvertiserBriefIntakeRequest(
            text=(
                "I want to use a $2000 budget to promote a fitness app in the "
                "United States and increase trial registrations over 14 days. "
                "Target CPA is $20. Avoid unrealistic body transformation claims."
            ),
            advertiser_id="adv_text_001",
        ),
        settings=Settings(use_llm_brief_intake=False),
    )

    brief = response.brief
    assert response.mode == "heuristic"
    assert response.confidence >= 0.75
    assert brief.advertiser_id == "adv_text_001"
    assert brief.product_name == "Fitness App"
    assert brief.product_category == "fitness app"
    assert brief.objective == "registrations"
    assert brief.budget == Decimal("2000.00")
    assert brief.currency == "USD"
    assert brief.duration_days == 14
    assert brief.target_market == "United States"
    assert brief.primary_kpi == "registrations"
    assert brief.target_cpa == Decimal("20.00")
    assert brief.constraints == ["unrealistic body transformation claims"]


def test_heuristic_brief_intake_handles_chinese_goal_text() -> None:
    response = parse_advertiser_brief(
        AdvertiserBriefIntakeRequest(
            text="我想用 2000 美元预算推广一个健身 App，提高注册转化，投放美国市场。",
            advertiser_id="adv_cn_001",
        ),
        settings=Settings(use_llm_brief_intake=False),
    )

    brief = response.brief
    assert brief.advertiser_id == "adv_cn_001"
    assert brief.product_category == "fitness app"
    assert brief.objective == "registrations"
    assert brief.budget == Decimal("2000.00")
    assert brief.currency == "USD"
    assert brief.target_market == "United States"


def test_llm_brief_intake_uses_structured_output_when_enabled() -> None:
    client = FakeLLMClient(
        {
            "brief": {
                "advertiser_id": "adv_llm",
                "product_name": "FitTrack Pro",
                "product_category": "fitness app",
                "objective": "registrations",
                "budget": "2000.00",
                "currency": "USD",
                "duration_days": 21,
                "target_market": "Canada",
                "primary_kpi": "trial registrations",
                "target_cpa": "25.00",
                "landing_page_url": "https://example.com",
                "brand_voice": "motivational",
                "constraints": ["Avoid medical claims"],
                "known_audiences": ["Home workout beginners"],
                "historical_context": "User wants registration growth.",
            },
            "assumptions": ["Target market inferred from text."],
            "confidence": 0.91,
        }
    )

    response = parse_advertiser_brief(
        AdvertiserBriefIntakeRequest(
            text="Promote FitTrack Pro in Canada with $2000 for trial registrations.",
            advertiser_id="adv_override",
        ),
        settings=Settings(use_llm_brief_intake=True),
        llm_client=client,
    )

    assert response.mode == "llm"
    assert response.confidence == 0.91
    assert response.brief.advertiser_id == "adv_override"
    assert response.brief.target_market == "Canada"
    assert response.assumptions == ["Target market inferred from text."]
    assert client.call_count == 1


def test_llm_brief_intake_falls_back_to_heuristic_on_gateway_failure() -> None:
    response = parse_advertiser_brief(
        AdvertiserBriefIntakeRequest(
            text="Use $2000 to promote a fitness app and improve registrations.",
            advertiser_id="adv_fallback",
        ),
        settings=Settings(use_llm_brief_intake=True),
        llm_client=FakeLLMClient(error=ModelGatewayError("MODEL_TIMEOUT", "timeout")),
    )

    assert response.mode == "llm_fallback"
    assert response.brief.advertiser_id == "adv_fallback"
    assert response.brief.product_category == "fitness app"
    assert response.extraction_errors


class FakeLLMClient:
    def __init__(
        self,
        payload: dict | None = None,
        *,
        error: ModelGatewayError | None = None,
    ) -> None:
        self._payload = payload
        self._error = error
        self.call_count = 0

    def complete(self, *args, **kwargs) -> LLMCompletion:
        self.call_count += 1
        if self._error is not None:
            raise self._error
        return LLMCompletion(
            content=json.dumps(self._payload),
            model="test-model",
            finish_reason="stop",
        )
