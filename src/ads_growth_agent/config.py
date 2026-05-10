from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    ads_growth_env: str = "local"
    ads_growth_log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth"
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = "local-dev-key"
    default_chat_model: str = "openai/gpt-4o-mini"
    default_embedding_model: str = "openai/text-embedding-3-small"
    langsmith_tracing: bool = False
    langsmith_project: str = "ads-growth-agent-local"


def get_settings() -> Settings:
    return Settings()
