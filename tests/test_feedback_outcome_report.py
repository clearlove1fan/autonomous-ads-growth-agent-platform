from ads_growth_agent.contracts import (
    CampaignPerformanceEventDetailResponse,
    CampaignPerformanceEventRequest,
    PerformanceEventType,
)
from ads_growth_agent.feedback import analyze_campaign_performance_event
from ads_growth_agent.feedback_outcome_report import build_campaign_feedback_outcome_report


def test_outcome_report_returns_no_followup_without_later_event() -> None:
    baseline = _event_detail(_event_request(event_id="evt_baseline"))
    store = FakePerformanceEventStore([baseline])

    report = build_campaign_feedback_outcome_report(baseline, store)

    assert report.outcome_status == "no_followup_event"
    assert report.followup_event_id is None
    assert report.comparison_event_count == 0
    assert report.metric_deltas == []
    assert "next performance snapshot" in report.recommendation


def test_outcome_report_classifies_improved_followup() -> None:
    baseline = _event_detail(_event_request(event_id="evt_baseline"))
    followup = _event_detail(
        _event_request(
            event_id="evt_followup",
            occurred_at="2026-05-13T12:00:00Z",
            impressions=12000,
            clicks=720,
            spend="900.00",
            conversions=90,
        )
    )
    store = FakePerformanceEventStore([followup, baseline])

    report = build_campaign_feedback_outcome_report(baseline, store)

    assert report.outcome_status == "improved"
    assert report.followup_event_id == "evt_followup"
    assert report.comparison_event_count == 1
    assert report.improved_metric_count > report.regressed_metric_count
    delta_by_name = {delta.metric_name: delta for delta in report.metric_deltas}
    assert delta_by_name["cpa"].delta_direction == "improved"
    assert delta_by_name["conversions"].delta_direction == "improved"
    assert delta_by_name["spend"].delta_direction == "informational"


class FakePerformanceEventStore:
    def __init__(self, events: list[CampaignPerformanceEventDetailResponse]) -> None:
        self.events = events
        self.list_requests: list[tuple[str | None, str | None, str | None, str | None, int]] = []

    def list_events(
        self,
        *,
        advertiser_id: str | None = None,
        run_id: str | None = None,
        campaign_id: str | None = None,
        draft_id: str | None = None,
        event_type: PerformanceEventType | None = None,
        limit: int = 50,
    ) -> list[CampaignPerformanceEventDetailResponse]:
        self.list_requests.append((advertiser_id, run_id, campaign_id, draft_id, limit))
        return [
            event
            for event in self.events
            if (advertiser_id is None or event.advertiser_id == advertiser_id)
            and (run_id is None or event.run_id == run_id)
            and (campaign_id is None or event.campaign_id == campaign_id)
            and (draft_id is None or event.draft_id == draft_id)
            and (event_type is None or event.event_type == event_type)
        ][:limit]


def _event_request(
    *,
    event_id: str,
    occurred_at: str = "2026-05-12T12:00:00Z",
    impressions: int = 10000,
    clicks: int = 500,
    spend: str = "1000.00",
    conversions: int = 20,
) -> CampaignPerformanceEventRequest:
    return CampaignPerformanceEventRequest.model_validate(
        {
            "event_id": event_id,
            "advertiser_id": "adv_fitness_001",
            "run_id": "run_001",
            "campaign_id": "cmp_fitness_001",
            "draft_id": "draft_fitness_001",
            "objective": "registrations",
            "event_type": "performance_snapshot",
            "occurred_at": occurred_at,
            "metrics": {
                "impressions": impressions,
                "clicks": clicks,
                "spend": spend,
                "conversions": conversions,
            },
            "target_cpa": "20.00",
            "attribution_window_days": 7,
        }
    )


def _event_detail(
    event: CampaignPerformanceEventRequest,
) -> CampaignPerformanceEventDetailResponse:
    analysis = analyze_campaign_performance_event(event)
    return CampaignPerformanceEventDetailResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        objective=event.objective,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        metrics=event.metrics,
        status="analyzed",
        metadata={"performance_event_persistence": "postgres"},
        analysis=analysis,
        created_at=analysis.created_at,
        updated_at=analysis.created_at,
    )
