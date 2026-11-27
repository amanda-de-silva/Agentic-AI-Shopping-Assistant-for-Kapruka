"""
Async clients for the two halves of hybrid search:
  - EmbeddingsClient: dense embeddings via openai/text-embedding-3-small (OpenRouter)
  - SparseEmbeddingsClient: sparse/BM25 ("keyword") embeddings via fastembed, local

Used together by memory/semantic_memory.py to embed recipient notes and conversation
summaries for Qdrant's hybrid (dense + sparse, RRF-fused) search.
"""
from __future__ import annotations

import asyncio

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.settings import get_models_config, get_settings


class EmbeddingsClient:
    def __init__(self) -> None:
        settings = get_settings()
        models_cfg = get_models_config()["llm"]
        self._model = models_cfg["embedding_model"]
        self.dimensions = models_cfg.get("embedding_dimensions", 1536)
        self._client = httpx.AsyncClient(
            base_url=settings.openrouter_base_url.rstrip("/"),
            timeout=models_cfg.get("timeout_seconds", 30),
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": settings.openrouter_app_url,
                "X-Title": settings.openrouter_app_name,
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=10),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    )
    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = await self._client.post(
            "/embeddings", json={"model": self._model, "input": texts}
        )
        resp.raise_for_status()
        data = resp.json()
        # OpenRouter preserves input order in `data`
        return [item["embedding"] for item in data["data"]]

    async def embed_one(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result[0] if result else []


class SparseEmbeddingsClient:
    """BM25 sparse ('keyword') embeddings via fastembed, computed locally on CPU
    (not an OpenRouter call -- BM25 is a statistical model, not something an LLM
    provider serves).

    Note: fastembed still needs a one-time download of the model's small vocab/
    stopwords files from Hugging Face on first use (a few hundred KB, not a neural
    net) -- after that it's cached locally and runs fully offline. If your deployment
    environment has restricted egress, make sure huggingface.co is reachable at least
    once (or pre-warm the cache during your Docker build), or swap this for a vendored
    tokenizer if you need fully air-gapped startup.
    """

    def __init__(self) -> None:
        models_cfg = get_models_config()["llm"]
        self._model_name = models_cfg.get("sparse_embedding_model", "Qdrant/bm25")
        self._timeout_seconds = models_cfg.get("sparse_embedding_timeout_seconds", 60)
        self._model = None  # lazy-loaded on first use, off the event loop

    def _get_model(self):
        if self._model is None:
            from fastembed import SparseTextEmbedding

            self._model = SparseTextEmbedding(model_name=self._model_name)
        return self._model

    async def embed(self, texts: list[str]) -> list[dict[str, list]]:
        """Returns a list of {"indices": [...], "values": [...]} dicts, one per text,
        ready to hand to qdrant_client.http.models.SparseVector(**result)."""
        if not texts:
            return []

        def _run() -> list[dict[str, list]]:
            model = self._get_model()
            embeddings = list(model.embed(texts))
            return [
                {"indices": e.indices.tolist(), "values": e.values.tolist()} for e in embeddings
            ]

        # asyncio.to_thread() alone does NOT time out -- a stalled model download or
        # flaky connection to huggingface.co (the one-time download this triggers on
        # first use) hangs this thread, and therefore the whole request, indefinitely
        # with no error at all. This was a real production hang, not a hypothetical
        # -- wait_for is what actually bounds it.
        try:
            return await asyncio.wait_for(asyncio.to_thread(_run), timeout=self._timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"Sparse (BM25) embedding timed out after {self._timeout_seconds}s. "
                "This is almost always the one-time model download from "
                "huggingface.co stalling -- check network connectivity to "
                "huggingface.co, or that the fastembed cache wasn't cleared."
            ) from exc

    async def embed_one(self, text: str) -> dict[str, list]:
        result = await self.embed([text])
        return result[0] if result else {"indices": [], "values": []}


_client: EmbeddingsClient | None = None
_sparse_client: SparseEmbeddingsClient | None = None


def get_embeddings_client() -> EmbeddingsClient:
    global _client
    if _client is None:
        _client = EmbeddingsClient()
    return _client


def get_sparse_embeddings_client() -> SparseEmbeddingsClient:
    global _sparse_client
    if _sparse_client is None:
        _sparse_client = SparseEmbeddingsClient()
    return _sparse_client