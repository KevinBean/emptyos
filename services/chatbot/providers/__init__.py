from .base import Provider, CompletionResult
from .openai_provider import OpenAIProvider

__all__ = ["Provider", "CompletionResult", "OpenAIProvider"]


def get_provider(name: str) -> Provider:
    if name == "openai":
        return OpenAIProvider()
    raise ValueError(f"unknown provider: {name}")
