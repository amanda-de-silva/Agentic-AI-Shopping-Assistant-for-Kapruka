"""SSE chat endpoint with bilingual normalization and performance measurement."""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from backend.agents.language_agent import (
    localize_response,
    normalize_user_message,
)
from backend.agents.orchestrator import prepare_turn
from backend.agents.response_composer import (
    build_ui_actions,
    compose_stream,
)
from backend.api.deps import get_session
from backend.core.logging import get_logger
from backend.db.models import SessionRow
from backend.memory import short_term
from backend.schemas.chat import ChatRequest

router = APIRouter(tags=["chat"])
logger = get_logger(__name__)


def _print_perf(
    stage: str,
    duration_seconds: float,
) -> None:
    """Print a human-readable Stage-1 timing entry to backend console."""
    duration_ms = duration_seconds * 1000.0

    print(
        f"[PERF][CHAT] {stage:<35} "
        f"{duration_ms:>10.1f} ms "
        f"({duration_seconds:>7.3f} s)",
        flush=True,
    )


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    session: SessionRow = Depends(get_session),
):
    async def event_generator():
        turn_started = time.perf_counter()

        try:
            print(
                "\n"
                + "=" * 88
                + "\n"
                f"[PERF][CHAT] NEW TURN "
                f"| session={session.id}"
                + "\n"
                + "=" * 88,
                flush=True,
            )

            # ------------------------------------------------------------------
            # 1. LANGUAGE NORMALIZATION
            # ------------------------------------------------------------------
            language_started = time.perf_counter()

            language = await normalize_user_message(
                payload.message
            )

            language_duration = (
                time.perf_counter() - language_started
            )

            working_message = (
                language.normalized_english
                or payload.message
            )

            response_language = (
                language.response_language
            )

            _print_perf(
                "language normalization",
                language_duration,
            )

            print(
                "[PERF][CHAT] "
                f"source_language={language.source_language} "
                f"| response_language={response_language} "
                f"| normalized={'yes' if language.normalized_english else 'no'}",
                flush=True,
            )

            # ------------------------------------------------------------------
            # 2. ORCHESTRATOR / MAIN TURN
            # ------------------------------------------------------------------
            prepare_started = time.perf_counter()

            specialist_outputs, intents, faq_reply = (
                await prepare_turn(
                    session_id=session.id,
                    user_id=session.user_id,
                    user_message=working_message,
                )
            )

            prepare_duration = (
                time.perf_counter() - prepare_started
            )

            specialist_outputs["_language"] = (
                language.model_dump()
            )

            _print_perf(
                "prepare_turn / orchestrator",
                prepare_duration,
            )

            print(
                "[PERF][CHAT] "
                f"intents={intents}",
                flush=True,
            )

            # ------------------------------------------------------------------
            # 3. FAQ OR RESPONSE COMPOSER
            # ------------------------------------------------------------------
            if faq_reply is not None:
                faq_started = time.perf_counter()

                localized_faq = await localize_response(
                    faq_reply,
                    response_language,
                )

                faq_duration = (
                    time.perf_counter() - faq_started
                )

                _print_perf(
                    "FAQ localization",
                    faq_duration,
                )

                reply = localized_faq
                actions = []

                # This is not model-token streaming.
                yield {
                    "event": "token",
                    "data": reply,
                }

            else:
                composer_started = time.perf_counter()
                first_token_time: float | None = None
                token_count = 0

                parts: list[str] = []

                async for token in compose_stream(
                    specialist_outputs=specialist_outputs,
                    user_message=working_message,
                    original_user_message=payload.message,
                    response_language=response_language,
                ):
                    if first_token_time is None:
                        first_token_time = (
                            time.perf_counter()
                            - composer_started
                        )

                        _print_perf(
                            "composer FIRST TOKEN",
                            first_token_time,
                        )

                    token_count += 1
                    parts.append(token)

                    yield {
                        "event": "token",
                        "data": token,
                    }

                composer_duration = (
                    time.perf_counter()
                    - composer_started
                )

                _print_perf(
                    "response composer stream",
                    composer_duration,
                )

                print(
                    "[PERF][CHAT] "
                    f"composer tokens={token_count}",
                    flush=True,
                )

                reply = (
                    "".join(parts).strip()
                    or "Sorry, I hit a snag putting that together."
                )

                actions = build_ui_actions(
                    specialist_outputs
                )

            # ------------------------------------------------------------------
            # 4. ASSISTANT MESSAGE PERSISTENCE
            # ------------------------------------------------------------------
            persist_started = time.perf_counter()

            await short_term.record_turn(
                session.id,
                "assistant",
                reply,
                intent=",".join(intents),
            )

            persist_duration = (
                time.perf_counter() - persist_started
            )

            _print_perf(
                "assistant message persistence",
                persist_duration,
            )

            # Keep existing background summary behavior unchanged.
            asyncio.create_task(
                _schedule_summary(session.id)
            )

            # ------------------------------------------------------------------
            # 5. TOTAL TURN
            # ------------------------------------------------------------------
            total_duration = (
                time.perf_counter() - turn_started
            )

            _print_perf(
                "TOTAL CHAT TURN",
                total_duration,
            )

            print(
                "[PERF][CHAT] "
                f"total_seconds={total_duration:.3f} "
                f"| total_ms={total_duration * 1000.0:.1f}",
                flush=True,
            )

            print(
                "=" * 88 + "\n",
                flush=True,
            )

            yield {
                "event": "done",
                "data": json.dumps(
                    {
                        "session_id": session.id,
                        "reply": reply,
                        "ui_actions": [
                            action.model_dump()
                            for action in actions
                        ],
                    }
                ),
            }

        except Exception as exc:  # noqa: BLE001
            total_duration = (
                time.perf_counter()
                - turn_started
            )

            _print_perf(
                "FAILED CHAT TURN",
                total_duration,
            )

            logger.exception(
                "chat_stream turn failed",
                extra={
                    "session_id": session.id,
                },
            )

            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "message": (
                            f"{type(exc).__name__}: {exc}"
                        )
                    }
                ),
            }

    return EventSourceResponse(
        event_generator()
    )


async def _schedule_summary(
    session_id: str,
) -> None:
    """Reuse the same summary worker used by normal chat."""
    from backend.agents.orchestrator import (
        _maybe_index_summary,
    )

    await _maybe_index_summary(
        session_id
    )