"""
FastAPI application entrypoint.

Creates the FastAPI app, wires up CORS, mounts API routers and the chat
streaming endpoint, and runs startup hooks: Kapruka MCP handshake, Qdrant
collection bootstrap, Langfuse init check.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import (
    auth,
    cart,
    chat,
    checkout,
    delivery,
    gift_composer,
    health,
    memory,
    orders,
    products,
    shopping_list,
)
from backend.api.websocket import chat_stream
from backend.core.exceptions import AppError
from backend.core.logging import (
    configure_logging,
    get_logger,
)
from backend.db.qdrant_client import ensure_collections
from backend.mcp.client import get_mcp_client
from backend.observability.langfuse_client import get_langfuse
from config.settings import get_settings


configure_logging()

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    mcp_client = get_mcp_client()

    try:
        await mcp_client.connect()

        tools = await mcp_client.list_tools()

        logger.info(
            f"Kapruka MCP connected, {len(tools)} tools available",
            extra={"tool": "startup"},
        )

    except Exception as exc:  # noqa: BLE001
        logger.error(
            f"Kapruka MCP connection failed at startup: {exc}"
        )

    if settings.qdrant_url:
        try:
            await ensure_collections()
            logger.info("Qdrant collections ready")

        except Exception as exc:  # noqa: BLE001
            logger.error(
                f"Qdrant collection bootstrap failed: {exc}"
            )

    if get_langfuse() is not None:
        logger.info(
            "Langfuse observability enabled"
        )
    else:
        logger.info(
            "Langfuse observability disabled (no keys configured)"
        )

    yield

    try:
        await mcp_client.close()

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"Error during MCP client shutdown: {exc}"
        )


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Kapruka AI Shopping Concierge",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(
        request,
        exc: AppError,
    ):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
            },
        )

    app.include_router(
        auth.router,
        prefix="/api",
    )

    app.include_router(
        health.router,
        prefix="/api",
    )

    app.include_router(
        chat.router,
        prefix="/api",
    )

    app.include_router(
        chat_stream.router,
        prefix="/api",
    )

    app.include_router(
        cart.router,
        prefix="/api",
    )

    app.include_router(
        checkout.router,
        prefix="/api",
    )

    app.include_router(
        orders.router,
        prefix="/api",
    )

    app.include_router(
        delivery.router,
        prefix="/api",
    )

    app.include_router(
        gift_composer.router,
        prefix="/api",
    )

    # Isolated item-by-item shopping-list search. Existing chat/cart routes are
    # registered exactly as before; this router only performs product searches.
    app.include_router(
        shopping_list.router,
        prefix="/api",
    )

    app.include_router(
        memory.router,
        prefix="/api",
    )

    app.include_router(
        products.router,
        prefix="/api",
    )

    return app


app = create_app()
