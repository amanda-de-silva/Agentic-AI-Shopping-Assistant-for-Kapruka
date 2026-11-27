"""
Async MCP client (streamable HTTP) wrapping the 7 Kapruka tools:
search_products, get_product, list_categories, list_delivery_cities,
check_delivery, create_order, track_order. Handles retries via tenacity
and stays under Kapruka's published rate limits via KaprukaRateLimiter.

One client instance is opened at app startup (see backend/main.py) and reused for
the process lifetime -- streamable HTTP sessions are cheap to hold open and this
avoids paying a handshake on every chat turn.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession

try:  # mcp SDK renamed this export across versions -- support both.
    from mcp.client.streamable_http import streamablehttp_client
except ImportError:
    from mcp.client.streamable_http import streamable_http_client as streamablehttp_client

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from backend.core.exceptions import MCPToolError
from backend.core.logging import get_logger
from backend.mcp.rate_limiter import get_rate_limiter
from backend.observability.tracing import traced

from backend.mcp.tool_schemas import (
    CheckDeliveryInput,
    CreateOrderInput,
    GetProductInput,
    ListCategoriesInput,
    ListDeliveryCitiesInput,
    MCPToolResult,
    SearchProductsInput,
    TrackOrderInput,
)
from config.settings import get_params_config, get_settings

logger = get_logger(__name__)


class _TransientMCPError(Exception):
    """Internal marker used to decide what tenacity should retry on."""


class KaprukaMCPClient:
    def __init__(self, url: str | None = None):
        settings = get_settings()
        self.url = url or settings.kapruka_mcp_url
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._rate_limiter = get_rate_limiter()

        params = get_params_config()["mcp"]
        self._timeout_seconds = params["timeout_seconds"]
        self._retry_cfg = params["retry"]

    async def connect(self) -> None:
        if self._session is not None:
            return

        stack = AsyncExitStack()

        try:
            # The mcp SDK's streamable_http_client has yielded either a
            # (read, write) pair or a (read, write, get_session_id) triple
            # across versions -- handle both.
            streams = await stack.enter_async_context(
                streamablehttp_client(self.url)
            )

            read, write = streams[0], streams[1]

            session = await stack.enter_async_context(
                ClientSession(read, write)
            )

            await session.initialize()

        except Exception:
            # Never leave a half-opened stack lying around on failure.
            await stack.aclose()
            raise

        self._stack = stack
        self._session = session

        logger.info(
            "Connected to Kapruka MCP server",
            extra={"tool": "connect"},
        )

    async def close(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"Error closing Kapruka MCP connection: {exc}"
                )

        self._session = None
        self._stack = None

        await self._rate_limiter.aclose()

    async def list_tools(self) -> list[str]:
        await self.connect()

        assert self._session is not None

        result = await self._session.list_tools()

        return [
            tool.name
            for tool in result.tools
        ]

    async def _call_raw(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        await self.connect()

        assert self._session is not None

        try:
            # Kapruka's MCP tools require their actual arguments to be wrapped
            # inside a required "params" object.
            result = await asyncio.wait_for(
                self._session.call_tool(
                    tool_name,
                    {"params": arguments},
                ),
                timeout=self._timeout_seconds,
            )

        except asyncio.TimeoutError as exc:
            raise _TransientMCPError(
                f"timed out after {self._timeout_seconds}s"
            ) from exc

        except Exception as exc:
            # Connection drop / transport failure.
            raise _TransientMCPError(
                str(exc)
            ) from exc

        # Kapruka/MCP can report a tool-level failure in two ways:
        #
        # 1. MCP protocol result.is_error == True
        #
        # 2. A normal result whose text or structured content contains:
        #
        #       Error (<code>): <message>
        #
        # Normalize both forms here.
        remote_error = _extract_mcp_error(result)

        if remote_error is not None:
            code, message, raw = remote_error

            raise MCPToolError(
                tool_name,
                message,
                remote_code=code,
                raw_message=raw,
            )

        # Prefer structured content when available.
        if result.structured_content is not None:
            return result.structured_content

        text = _extract_text(result)

        if text is None:
            return None

        try:
            return json.loads(text)

        except json.JSONDecodeError:
            # Preserve raw markdown/text for the caller/parser.
            return {
                "raw_markdown": text
            }

    @traced("mcp.call_tool")
    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        is_order: bool = False,
    ) -> MCPToolResult:

        if is_order:
            await self._rate_limiter.acquire_order()
        else:
            await self._rate_limiter.acquire_request()

        start = time.monotonic()

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(
                    self._retry_cfg["max_attempts"]
                ),
                wait=wait_exponential(
                    multiplier=self._retry_cfg["backoff_seconds"],
                    exp_base=self._retry_cfg["backoff_multiplier"],
                ),
                retry=retry_if_exception_type(
                    _TransientMCPError
                ),
                reraise=True,
            ):
                with attempt:
                    data = await self._call_raw(
                        tool_name,
                        arguments,
                    )

            latency_ms = (
                time.monotonic() - start
            ) * 1000

            logger.info(
                "MCP tool call succeeded",
                extra={
                    "tool": tool_name,
                    "duration_ms": round(
                        latency_ms,
                        1,
                    ),
                },
            )

            return MCPToolResult(
                tool_name=tool_name,
                ok=True,
                data=data,
                latency_ms=latency_ms,
            )

        except MCPToolError:
            # IMPORTANT:
            # Do not retry an explicit tool-level rejection.
            raise

        except _TransientMCPError as exc:
            raise MCPToolError(
                tool_name,
                f"transport error after retries: {exc}",
            ) from exc

    # ------------------------------------------------------------------
    # Typed wrappers
    # ------------------------------------------------------------------

    async def search_products(
        self,
        params: SearchProductsInput,
    ) -> MCPToolResult:
        return await self.call_tool(
            "kapruka_search_products",
            params.model_dump(
                exclude_none=True
            ),
        )

    async def get_product(
        self,
        params: GetProductInput,
    ) -> MCPToolResult:
        return await self.call_tool(
            "kapruka_get_product",
            params.model_dump(
                exclude_none=True
            ),
        )

    async def list_categories(
        self,
        params: ListCategoriesInput,
    ) -> MCPToolResult:
        return await self.call_tool(
            "kapruka_list_categories",
            params.model_dump(
                exclude_none=True
            ),
        )

    async def list_delivery_cities(
        self,
        params: ListDeliveryCitiesInput,
    ) -> MCPToolResult:
        return await self.call_tool(
            "kapruka_list_delivery_cities",
            params.model_dump(
                exclude_none=True
            ),
        )

    async def check_delivery(
        self,
        params: CheckDeliveryInput,
    ) -> MCPToolResult:
        return await self.call_tool(
            "kapruka_check_delivery",
            params.model_dump(
                exclude_none=True
            ),
        )

    async def create_order(
        self,
        params: CreateOrderInput,
    ) -> MCPToolResult:
        return await self.call_tool(
            "kapruka_create_order",
            params.model_dump(
                exclude_none=True
            ),
            is_order=True,
        )

    async def track_order(
        self,
        params: TrackOrderInput,
    ) -> MCPToolResult:
        return await self.call_tool(
            "kapruka_track_order",
            params.model_dump(
                exclude_none=True
            ),
        )


_MCP_ERROR_RE = re.compile(
    r"^\s*Error\s*\((?P<code>[^)]+)\)"
    r"\s*:\s*(?P<message>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _error_from_text(
    value: Any,
) -> tuple[str | None, str, str] | None:

    if not isinstance(
        value,
        str,
    ):
        return None

    text = value.strip()

    if not text:
        return None

    match = _MCP_ERROR_RE.match(text)

    if match:
        return (
            match.group("code").strip() or None,
            match.group("message").strip(),
            text,
        )

    return None


def _extract_mcp_error(
    result: Any,
) -> tuple[str | None, str, str] | None:
    """Extract a normalized Kapruka tool error from an MCP result.

    Kapruka can return tool failures as protocol errors or as ordinary text such
    as ``Error (validation_error): ...``. A structured response may also wrap that
    same text under a ``result`` key.

    This helper deliberately does not treat an arbitrary occurrence of the word
    "error" as a failure. It only recognizes the confirmed
    ``Error (<code>): <message>`` format or explicit error fields.
    """

    content_text = _extract_text(
        result
    )

    # --------------------------------------------------------------
    # Protocol-level MCP error
    # --------------------------------------------------------------
    if getattr(
        result,
        "is_error",
        False,
    ):
        parsed = _error_from_text(
            content_text
        )

        if parsed is not None:
            return parsed

        raw = (
            content_text
            or "unknown MCP error"
        ).strip()

        return (
            None,
            raw,
            raw,
        )

    # --------------------------------------------------------------
    # Structured content
    # --------------------------------------------------------------
    structured = getattr(
        result,
        "structured_content",
        None,
    )

    if isinstance(
        structured,
        dict,
    ):

        # Common FastMCP/Kapruka wrapper:
        #
        # {
        #     "result": "Error (some_code): ..."
        # }
        for key in (
            "result",
            "error",
            "message",
        ):

            value = structured.get(
                key
            )

            parsed = _error_from_text(
                value
            )

            if parsed is not None:
                return parsed

        error_value = structured.get(
            "error"
        )

        if isinstance(
            error_value,
            dict,
        ):
            code = error_value.get(
                "code"
            )

            message = error_value.get(
                "message"
            )

            if message:
                raw = str(
                    message
                ).strip()

                return (
                    str(code).strip()
                    if code
                    else None,
                    raw,
                    raw,
                )

    # --------------------------------------------------------------
    # Normal text response carrying an Error(...) payload
    # --------------------------------------------------------------
    return _error_from_text(
        content_text
    )


def _extract_text(
    result: Any,
) -> str | None:
    """Extract the first text content block from an MCP result."""

    for block in (
        getattr(
            result,
            "content",
            [],
        )
        or []
    ):

        if getattr(
            block,
            "type",
            None,
        ) == "text":

            return block.text

    return None


# ------------------------------------------------------------------
# Process-wide singleton.
# ------------------------------------------------------------------

_client: KaprukaMCPClient | None = None


def get_mcp_client() -> KaprukaMCPClient:
    global _client

    if _client is None:
        _client = KaprukaMCPClient()

    return _client