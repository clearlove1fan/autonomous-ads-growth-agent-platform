import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field

from ads_growth_agent.config import Settings, get_settings
from ads_growth_agent.contracts import (
    AdvertiserBrief,
    AdvertiserBriefIntakeRequest,
    AdvertiserBriefIntakeResponse,
    CampaignObjective,
)
from ads_growth_agent.llm import (
    LiteLLMGatewayClient,
    LLMMessage,
    StructuredOutputResult,
    generate_structured_output,
)


class BriefIntakeLLMOutput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    brief: AdvertiserBrief
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


def parse_advertiser_brief(
    request: AdvertiserBriefIntakeRequest,
    *,
    settings: Settings | None = None,
    llm_client: LiteLLMGatewayClient | None = None,
) -> AdvertiserBriefIntakeResponse:
    settings = settings or get_settings()
    if settings.use_llm_brief_intake:
        llm_response = _try_llm_brief_intake(request, settings=settings, llm_client=llm_client)
        if llm_response is not None:
            return llm_response

    return _heuristic_brief_intake(request, mode="heuristic")


def _try_llm_brief_intake(
    request: AdvertiserBriefIntakeRequest,
    *,
    settings: Settings,
    llm_client: LiteLLMGatewayClient | None,
) -> AdvertiserBriefIntakeResponse | None:
    client = llm_client or LiteLLMGatewayClient(settings=settings)
    output, structured_result = generate_structured_output(
        client,
        _brief_intake_messages(request),
        output_model=BriefIntakeLLMOutput,
        model=settings.default_chat_model,
        max_repair_attempts=settings.llm_structured_output_max_repair_attempts,
    )
    if output is None:
        fallback = _heuristic_brief_intake(
            request,
            mode="llm_fallback",
            extraction_errors=_structured_output_errors(structured_result),
        )
        return fallback

    brief = _apply_request_overrides(output.brief, request)
    return AdvertiserBriefIntakeResponse(
        source_text=request.text,
        brief=brief,
        mode="llm",
        confidence=output.confidence,
        assumptions=output.assumptions,
    )


def _heuristic_brief_intake(
    request: AdvertiserBriefIntakeRequest,
    *,
    mode: str,
    extraction_errors: list[str] | None = None,
) -> AdvertiserBriefIntakeResponse:
    text = request.text.strip()
    assumptions: list[str] = []
    objective = _extract_objective(text)
    budget = _extract_budget(text, assumptions)
    currency = _extract_currency(text, request.default_currency)
    duration_days = _extract_duration_days(text, request.default_duration_days)
    target_market = _extract_target_market(text, request.default_target_market)
    product_category = _extract_product_category(text, assumptions)
    product_name = _extract_product_name(text, product_category)
    advertiser_id = request.advertiser_id or _advertiser_id_for_product(product_name)
    target_cpa = _extract_target_cpa(text)
    constraints = _extract_constraints(text)
    known_audiences = _known_audiences_for_category(product_category)

    if target_cpa is None:
        assumptions.append("No target CPA was provided; strategy will infer efficiency targets.")

    brief = AdvertiserBrief(
        advertiser_id=advertiser_id,
        product_name=product_name,
        product_category=product_category,
        objective=objective,
        budget=budget,
        currency=currency,
        duration_days=duration_days,
        target_market=target_market,
        primary_kpi=_primary_kpi_for_objective(objective),
        target_cpa=target_cpa,
        landing_page_url=_extract_url(text),
        brand_voice=_brand_voice_for_category(product_category),
        constraints=constraints,
        known_audiences=known_audiences,
        historical_context=text,
    )
    return AdvertiserBriefIntakeResponse(
        source_text=text,
        brief=brief,
        mode=mode,  # type: ignore[arg-type]
        confidence=_heuristic_confidence(text, assumptions),
        assumptions=assumptions,
        extraction_errors=extraction_errors or [],
    )


def _brief_intake_messages(request: AdvertiserBriefIntakeRequest) -> list[LLMMessage]:
    defaults = request.model_dump(mode="json", exclude={"text"})
    return [
        LLMMessage(
            role="system",
            content=(
                "You extract advertiser campaign briefs for an autonomous ads growth "
                "platform. Return a strict AdvertiserBrief plus assumptions. Infer only "
                "reasonable fields from the user's text and provided defaults. Do not "
                "invent claims, medical outcomes, or unavailable landing pages."
            ),
        ),
        LLMMessage(
            role="user",
            content=(
                "Extract a structured advertiser brief from this plain-language request.\n"
                f"Defaults JSON: {json.dumps(defaults, sort_keys=True)}\n"
                f"Request text: {request.text}"
            ),
        ),
    ]


def _apply_request_overrides(
    brief: AdvertiserBrief,
    request: AdvertiserBriefIntakeRequest,
) -> AdvertiserBrief:
    updates = {}
    if request.advertiser_id is not None:
        updates["advertiser_id"] = request.advertiser_id
    return brief.model_copy(update=updates) if updates else brief


def _structured_output_errors(result: StructuredOutputResult) -> list[str]:
    errors = [
        f"{attempt.mode}: {attempt.error_code or 'ERROR'}: {attempt.message}"
        for attempt in result.attempts
        if not attempt.success
    ]
    if result.error_message and not errors:
        errors.append(result.error_message)
    return errors


def _extract_objective(text: str) -> CampaignObjective:
    normalized = text.lower()
    if _contains_any(normalized, ["注册", "signup", "sign up", "registration", "trial"]):
        return CampaignObjective.REGISTRATIONS
    if _contains_any(normalized, ["install", "安装", "下载", "app install"]):
        return CampaignObjective.APP_INSTALLS
    if _contains_any(normalized, ["purchase", "sales", "revenue", "购买", "销售", "转化"]):
        return CampaignObjective.PURCHASES
    if _contains_any(normalized, ["lead", "leads", "线索", "留资"]):
        return CampaignObjective.LEADS
    if _contains_any(normalized, ["traffic", "click", "visits", "访问", "点击"]):
        return CampaignObjective.TRAFFIC
    if _contains_any(normalized, ["awareness", "brand", "曝光", "认知", "品牌"]):
        return CampaignObjective.AWARENESS
    return CampaignObjective.REGISTRATIONS


def _extract_budget(text: str, assumptions: list[str]) -> Decimal:
    patterns = [
        r"(?:\$|usd\s*)([\d,]+(?:\.\d{1,2})?)",
        r"([\d,]+(?:\.\d{1,2})?)\s*(?:usd|dollars?|美元|美金)",
        r"(?:budget|预算)\s*(?:of|is|:|为|是)?\s*(?:\$|usd\s*)?([\d,]+(?:\.\d{1,2})?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _money(match.group(1))
    assumptions.append("No explicit budget was found; using a default budget of 1000.00.")
    return Decimal("1000.00")


def _extract_currency(text: str, default_currency: str) -> str:
    normalized = text.lower()
    if _contains_any(normalized, ["rmb", "cny", "人民币", "¥", "￥"]):
        return "CNY"
    if _contains_any(normalized, ["eur", "€"]):
        return "EUR"
    if _contains_any(normalized, ["gbp", "£"]):
        return "GBP"
    if _contains_any(normalized, ["usd", "$", "美元", "美金", "dollar"]):
        return "USD"
    return default_currency.upper()


def _extract_duration_days(text: str, default_duration_days: int) -> int:
    match = re.search(
        r"(\d+)\s*(day|days|week|weeks|month|months|天|周|星期|个月)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return default_duration_days
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit in {"week", "weeks", "周", "星期"}:
        return min(amount * 7, 365)
    if unit in {"month", "months", "个月"}:
        return min(amount * 30, 365)
    return min(amount, 365)


def _extract_target_market(text: str, default_target_market: str) -> str:
    normalized = text.lower()
    market_signals = [
        ("United States", ["united states", "u.s.", " us ", "usa", "美国"]),
        ("China", ["china", "中国", "国内"]),
        ("United Kingdom", ["united kingdom", "uk", "英国"]),
        ("Canada", ["canada", "加拿大"]),
        ("Japan", ["japan", "日本"]),
        ("Southeast Asia", ["southeast asia", "sea", "东南亚"]),
    ]
    padded = f" {normalized} "
    for market, signals in market_signals:
        if any(signal in padded for signal in signals):
            return market
    return default_target_market


def _extract_product_category(text: str, assumptions: list[str]) -> str:
    normalized = text.lower()
    categories = [
        ("fitness app", ["fitness", "workout", "健身", "运动 app", "运动应用"]),
        ("mobile game", ["game", "gaming", "手游", "游戏"]),
        ("ecommerce store", ["ecommerce", "e-commerce", "shop", "store", "电商"]),
        ("education app", ["education", "learning", "course", "教育", "学习"]),
        ("finance app", ["finance", "fintech", "banking", "理财", "金融"]),
        ("productivity app", ["productivity", "workflow", "效率", "办公"]),
    ]
    for category, signals in categories:
        if _contains_any(normalized, signals):
            return category
    assumptions.append("Product category was not explicit; using mobile app as a generic category.")
    return "mobile app"


def _extract_product_name(text: str, product_category: str) -> str:
    match = re.search(
        r"(?:called|named|for|推广|推广一个|推广一款)\s+([A-Za-z][A-Za-z0-9 -]{1,80})",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        candidate = re.split(r"\s+(?:to|for|with|in|on)\s+", match.group(1), maxsplit=1)[0]
        return candidate.strip(" ,.;:")[:160]
    return product_category.title()


def _extract_target_cpa(text: str) -> Decimal | None:
    match = re.search(
        (
            r"(?:target\s*)?(?:cpa|cost per acquisition|每.*转化)"
            r"[^\d$￥¥]*(?:\$|usd\s*)?([\d,]+(?:\.\d{1,2})?)"
        ),
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return _money(match.group(1))
    return None


def _extract_constraints(text: str) -> list[str]:
    constraints: list[str] = []
    for pattern in [
        r"(?:avoid|do not|don't)\s+([^.;\n]+)",
        r"(?:避免|不要|不能)([^。；\n]+)",
    ]:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = match.group(1).strip(" ,.;。；")
            if value:
                constraints.append(value[:240])
    return constraints[:5]


def _extract_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s,，。]+", text)
    return match.group(0)[:512] if match else None


def _known_audiences_for_category(product_category: str) -> list[str]:
    if product_category == "fitness app":
        return ["Home workout beginners", "Wearable fitness tracker users"]
    if product_category == "mobile game":
        return ["Casual mobile gamers", "Rewarded video ad engagers"]
    if product_category == "ecommerce store":
        return ["Recent category shoppers", "Lookalikes of purchasers"]
    if product_category == "education app":
        return ["Self-improvement learners", "Career switchers"]
    return ["Broad interest audience", "Lookalikes of high-intent visitors"]


def _brand_voice_for_category(product_category: str) -> str:
    if product_category == "fitness app":
        return "motivational and practical"
    if product_category == "finance app":
        return "clear, trustworthy, and action-oriented"
    if product_category == "mobile game":
        return "energetic and playful"
    return "clear and benefit-focused"


def _primary_kpi_for_objective(objective: CampaignObjective) -> str:
    return {
        CampaignObjective.APP_INSTALLS: "app installs",
        CampaignObjective.REGISTRATIONS: "registrations",
        CampaignObjective.PURCHASES: "purchases",
        CampaignObjective.LEADS: "qualified leads",
        CampaignObjective.TRAFFIC: "landing page visits",
        CampaignObjective.AWARENESS: "qualified reach",
    }[objective]


def _heuristic_confidence(text: str, assumptions: list[str]) -> float:
    score = Decimal("0.72")
    if re.search(r"(?:\$|usd|美元|美金|budget|预算)", text, flags=re.IGNORECASE):
        score += Decimal("0.08")
    if re.search(r"(registration|注册|install|安装|purchase|购买|lead|线索)", text, re.I):
        score += Decimal("0.08")
    score -= Decimal("0.05") * len(assumptions)
    return float(max(Decimal("0.30"), min(Decimal("0.90"), score)))


def _advertiser_id_for_product(product_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", product_name.lower()).strip("_")
    return f"adv_{slug or 'advertiser'}"[:128]


def _money(value: str) -> Decimal:
    try:
        amount = Decimal(value.replace(",", ""))
    except InvalidOperation:
        amount = Decimal("1000.00")
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _contains_any(text: str, signals: list[str]) -> bool:
    return any(signal in text for signal in signals)
