from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_name: str = "stoll-assist"
    database_url: str
    redis_url: str
    webhook_queue_name: str = "stoll:webhooks"
    worker_block_timeout_seconds: int = 5

    meta_verify_token: str
    meta_app_secret: str
    meta_access_token: str
    meta_phone_number_id: str
    meta_api_version: str

    openai_api_key: str
    openai_classifier_model: str = "gpt-5.4-nano"
    openai_generation_model: str = "gpt-5.4-mini"
    openai_verifier_model: str = "gpt-5.4-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    default_tenant_slug: str = "grupo-stoll"
    agent_name: str = "Agustina"
    agent_disclosure: str = (
        "Soy Agustina, asistente digital del equipo de Grupo Stöll."
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
