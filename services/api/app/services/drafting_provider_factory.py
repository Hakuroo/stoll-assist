from app.services.drafting_providers import (
    DeterministicDraftingProvider,
    DraftingProvider,
    OpenAIDraftingProvider,
)
from app.settings import Settings


def create_drafting_provider(settings: Settings) -> DraftingProvider:
    provider = settings.llm_drafting_provider.strip().lower()
    if provider == "deterministic":
        return DeterministicDraftingProvider()
    if provider == "openai":
        return OpenAIDraftingProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_generation_model,
            timeout_seconds=settings.llm_drafting_timeout_seconds,
        )
    raise ValueError(f"Unsupported drafting provider: {settings.llm_drafting_provider}")
