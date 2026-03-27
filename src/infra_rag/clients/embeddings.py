from typing import Any

from infra_rag.config import settings


def embed_text(text: str) -> list[float] | None:
    if not text:
        return None
    if settings.llm.provider != "openai":
        return None
    try:
        from langchain_openai import OpenAIEmbeddings
        emb = OpenAIEmbeddings(api_key=settings.llm.openai_api_key or None)
        vec = emb.embed_query(text)
        return vec
    except Exception:
        return None
