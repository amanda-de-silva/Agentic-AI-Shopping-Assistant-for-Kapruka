"""
Qdrant Cloud client singleton + collection bootstrap (semantic_memory, conversation_summaries).

Both collections are configured for hybrid search: a named "dense" vector
(openai/text-embedding-3-small, cosine) plus a named "keyword" sparse vector (BM25 via
fastembed), fused with Reciprocal Rank Fusion at query time. See memory/semantic_memory.py.

Both collections are also multitenant: many sessions/users share one collection,
isolated by a `session_id` payload filter on every query, not by separate collections
per tenant. A keyword payload index on `session_id` keeps those filtered queries fast
as the collection grows -- see ensure_collections() below.
"""
from __future__ import annotations

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from config.settings import get_models_config, get_settings

_client: AsyncQdrantClient | None = None

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "keyword"


def get_qdrant() -> AsyncQdrantClient:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.qdrant_url:
            raise RuntimeError("QDRANT_URL is not set in .env")
        _client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            # The SDK's own default is None -- no timeout at all -- meaning a slow or
            # unreachable cluster could hang a request indefinitely instead of failing
            # fast like every other network client in this codebase does. Matches
            # models.yaml's llm.timeout_seconds for consistency, not because Qdrant
            # calls are expected to be that slow.
            timeout=30,
        )
    return _client


async def ensure_collections() -> None:
    """Idempotently creates the two Qdrant collections this app needs, with hybrid
    (dense + sparse) vector config and a session_id payload index for multitenancy.
    Safe to call on every startup -- checks existence first."""
    client = get_qdrant()
    settings = get_settings()
    dims = get_models_config()["llm"].get("embedding_dimensions", 1536)

    for collection_name in (
        settings.qdrant_collection_semantic_memory,
        settings.qdrant_collection_convo_summaries,
    ):
        exists = await client.collection_exists(collection_name)
        if not exists:
            await client.create_collection(
                collection_name=collection_name,
                vectors_config={
                    DENSE_VECTOR_NAME: qmodels.VectorParams(
                        size=dims, distance=qmodels.Distance.COSINE
                    ),
                },
                sparse_vectors_config={
                    SPARSE_VECTOR_NAME: qmodels.SparseVectorParams(
                        modifier=qmodels.Modifier.IDF,
                    ),
                },
            )

        await client.create_payload_index(
            collection_name=collection_name,
            field_name="session_id",
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
        )