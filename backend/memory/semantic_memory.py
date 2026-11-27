"""Hybrid Qdrant semantic memory for recipient notes and conversation summaries."""
from __future__ import annotations

import asyncio
import uuid

from qdrant_client import models as qmodels

from backend.db.qdrant_client import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    get_qdrant,
)
from backend.llm.embeddings_client import (
    get_embeddings_client,
    get_sparse_embeddings_client,
)
from config.settings import (
    get_params_config,
    get_settings,
)


async def _embed_hybrid(
    text: str,
) -> tuple[
    list[float],
    qmodels.SparseVector,
]:
    dense_client = get_embeddings_client()
    sparse_client = get_sparse_embeddings_client()

    dense_vec, sparse_raw = await asyncio.gather(
        dense_client.embed_one(text),
        sparse_client.embed_one(text),
    )

    sparse_vec = qmodels.SparseVector(
        indices=sparse_raw["indices"],
        values=sparse_raw["values"],
    )

    return dense_vec, sparse_vec


async def index_recipient_note(
    *,
    session_id: str,
    profile_id: str,
    name: str,
    field: str,
    text: str,
) -> None:
    settings = get_settings()
    client = get_qdrant()

    content = f"{name} ({field}): {text}"

    dense_vec, sparse_vec = await _embed_hybrid(
        content
    )

    if not dense_vec:
        return

    await client.upsert(
        collection_name=settings.qdrant_collection_semantic_memory,
        points=[
            qmodels.PointStruct(
                id=str(uuid.uuid4()),
                vector={
                    DENSE_VECTOR_NAME: dense_vec,
                    SPARSE_VECTOR_NAME: sparse_vec,
                },
                payload={
                    "session_id": session_id,
                    "profile_id": profile_id,
                    "name": name,
                    "field": field,
                    "text": text,
                },
            )
        ],
    )


async def recall_relevant_notes(
    session_id: str,
    query_text: str,
) -> list[dict]:
    settings = get_settings()

    top_k = get_params_config()["memory"]["semantic_top_k"]

    client = get_qdrant()

    dense_vec, sparse_vec = await _embed_hybrid(
        query_text
    )

    if not dense_vec:
        return []

    tenant_filter = qmodels.Filter(
        must=[
            qmodels.FieldCondition(
                key="session_id",
                match=qmodels.MatchValue(
                    value=session_id
                ),
            )
        ]
    )

    result = await client.query_points(
        collection_name=settings.qdrant_collection_semantic_memory,
        prefetch=[
            qmodels.Prefetch(
                query=dense_vec,
                using=DENSE_VECTOR_NAME,
                filter=tenant_filter,
                limit=top_k * 4,
            ),
            qmodels.Prefetch(
                query=sparse_vec,
                using=SPARSE_VECTOR_NAME,
                filter=tenant_filter,
                limit=top_k * 4,
            ),
        ],
        query=qmodels.FusionQuery(
            fusion=qmodels.Fusion.RRF
        ),
        query_filter=tenant_filter,
        limit=top_k,
    )

    return [
        {
            "score": p.score,
            **(p.payload or {}),
        }
        for p in result.points
    ]


async def index_conversation_summary(
    session_id: str,
    summary: str,
) -> None:
    settings = get_settings()
    client = get_qdrant()

    dense_vec, sparse_vec = await _embed_hybrid(
        summary
    )

    if not dense_vec:
        return

    await client.upsert(
        collection_name=settings.qdrant_collection_convo_summaries,
        points=[
            qmodels.PointStruct(
                id=str(uuid.uuid4()),
                vector={
                    DENSE_VECTOR_NAME: dense_vec,
                    SPARSE_VECTOR_NAME: sparse_vec,
                },
                payload={
                    "session_id": session_id,
                    "summary": summary,
                },
            )
        ],
    )


async def clear_session_memory(
    session_id: str,
) -> None:
    """
    Delete all Qdrant memory points belonging to one
    authenticated user session.
    """
    settings = get_settings()

    if not settings.qdrant_url:
        return

    client = get_qdrant()

    tenant_filter = qmodels.Filter(
        must=[
            qmodels.FieldCondition(
                key="session_id",
                match=qmodels.MatchValue(
                    value=session_id
                ),
            )
        ]
    )

    selector = qmodels.FilterSelector(
        filter=tenant_filter
    )

    for collection_name in (
        settings.qdrant_collection_semantic_memory,
        settings.qdrant_collection_convo_summaries,
    ):
        await client.delete(
            collection_name=collection_name,
            points_selector=selector,
            wait=True,
        )