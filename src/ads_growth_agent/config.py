from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    ads_growth_env: str = "local"
    ads_growth_log_level: str = "INFO"
    tenant_id: str = "default"
    database_url: str = "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth"
    knowledge_store_backend: Literal["memory", "postgres"] = "memory"
    run_persistence_backend: Literal["none", "postgres"] = "none"
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = "sk-local-dev-key"
    default_chat_model: str = "openai/gpt-4o-mini"
    default_embedding_model: str = "openai/text-embedding-3-small"
    use_llm_planner: bool = False
    use_llm_critic: bool = False
    llm_structured_output_max_repair_attempts: int = 1
    llm_critic_min_score: float = 7.0
    max_revision_attempts: int = 1
    knowledge_top_k: int = 3
    langsmith_tracing: bool = False
    langsmith_project: str = "ads-growth-agent-local"


def get_settings() -> Settings:
    return Settings()
