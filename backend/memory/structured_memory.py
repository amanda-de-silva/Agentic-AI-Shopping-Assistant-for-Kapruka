"""Supabase-backed authoritative recipient/profile memory."""

from __future__ import annotations

from backend.core.logging import get_logger
from backend.db.models import RecipientProfileRow
from backend.db.repositories.profile_repo import (
    append_field_value,
    delete_profiles_for_user,
    find_profile_by_name,
    list_profiles,
    upsert_profile,
)
from backend.memory.memory_rules import (
    is_temporary_budget_value,
    sanitize_memory_value,
)
from backend.memory.recipient_resolution import (
    is_invalid_recipient_name,
)
from backend.memory.semantic_memory import (
    index_recipient_note,
)


logger = get_logger(__name__)


SELF_PROFILE_NAME = "self"


_VALID_FIELDS = {
    "allergies",
    "likes",
    "dislikes",
}


def _normalize_name(
    value: str,
) -> str:
    return " ".join(
        value.strip().split()
    )


def _normalize_value(
    value: str,
) -> str:
    return " ".join(
        value.strip().split()
    )


def _is_valid_profile_name(
    name: str,
) -> bool:
    """
    'self' is a legitimate durable profile representing the authenticated
    user. All other validation continues to use the existing recipient
    validation logic.
    """

    clean_name = _normalize_name(
        name
    ).lower()

    if clean_name == SELF_PROFILE_NAME:
        return True

    return not is_invalid_recipient_name(
        name
    )


def _clean_array(
    values: list[str] | None,
) -> list[str]:

    result: list[
        str
    ] = []

    for value in (
        values
        or []
    ):

        cleaned = (
            sanitize_memory_value(
                value
            )
        )

        if not cleaned:
            continue

        if is_temporary_budget_value(
            cleaned
        ):
            continue

        if not any(
            existing.lower()
            == cleaned.lower()
            for existing in result
        ):
            result.append(
                cleaned
            )

    return result


def _clean_profile(
    profile: RecipientProfileRow,
) -> RecipientProfileRow | None:

    if not _is_valid_profile_name(
        profile.name
    ):
        return None

    profile.allergies = _clean_array(
        profile.allergies
    )

    profile.likes = _clean_array(
        profile.likes
    )

    profile.dislikes = _clean_array(
        profile.dislikes
    )

    if profile.notes:

        profile.notes = (
            sanitize_memory_value(
                profile.notes
            )
        )

    return profile


async def get_or_create_profile(
    *,
    user_id: str,
    session_id: str,
    name: str,
    relationship: str | None = None,
) -> RecipientProfileRow:

    clean_name = _normalize_name(
        name
    )

    if not _is_valid_profile_name(
        clean_name
    ):
        raise ValueError(
            "Invalid recipient profile name."
        )

    existing = (
        await find_profile_by_name(
            user_id=user_id,
            name=clean_name,
        )
    )

    if existing is not None:

        existing = _clean_profile(
            existing
        )

        if existing is None:
            raise ValueError(
                "Invalid recipient profile."
            )

        changed = False

        if existing.session_id != session_id:

            existing.session_id = session_id
            changed = True

        if (
            relationship
            and not existing.relationship
        ):

            existing.relationship = (
                relationship
            )

            changed = True

        if changed:

            existing = (
                await upsert_profile(
                    existing
                )
            )

        return existing

    return await upsert_profile(
        RecipientProfileRow(
            user_id=user_id,
            session_id=session_id,
            name=clean_name,
            relationship=relationship,
        )
    )


async def apply_update(
    *,
    user_id: str,
    session_id: str,
    recipient_name: str,
    field: str,
    value: str,
) -> RecipientProfileRow:

    clean_name = _normalize_name(
        recipient_name
    )

    clean_value = (
        sanitize_memory_value(
            _normalize_value(
                value
            )
        )
    )

    if not _is_valid_profile_name(
        clean_name
    ):
        raise ValueError(
            "Invalid recipient name."
        )

    if not clean_value:
        raise ValueError(
            "Temporary or invalid memory value "
            "cannot be stored."
        )

    field = (
        field.strip().lower()
    )

    profile = (
        await get_or_create_profile(
            user_id=user_id,
            session_id=session_id,
            name=clean_name,
        )
    )

    if field in _VALID_FIELDS:

        if not profile.id:
            raise ValueError(
                "Recipient profile ID is missing."
            )

        profile = (
            await append_field_value(
                user_id=user_id,
                session_id=session_id,
                profile_id=profile.id,
                field=field,
                value=clean_value,
            )
        )

    elif field == "notes":

        old_notes = (
            sanitize_memory_value(
                profile.notes
            )
            if profile.notes
            else None
        )

        if old_notes:

            profile.notes = (
                f"{old_notes}. "
                f"{clean_value}"
            )

        else:

            profile.notes = (
                clean_value
            )

        profile.session_id = (
            session_id
        )

        profile = (
            await upsert_profile(
                profile
            )
        )

    else:

        raise ValueError(
            f"Unsupported profile field: {field}"
        )

    # Supabase is the authoritative store.
    # Semantic indexing is best-effort.
    if profile.id:

        try:

            await index_recipient_note(
                session_id=session_id,
                profile_id=profile.id,
                name=profile.name,
                field=field,
                text=clean_value,
            )

        except Exception as exc:

            logger.warning(
                "Semantic indexing failed after profile write: %s",
                exc,
            )

    return profile


async def get_profile_for_prompt(
    *,
    user_id: str,
    name: str,
) -> str:

    clean_name = _normalize_name(
        name
    )

    # Normalize first-person references when this function is used
    # directly by another part of the system.
    if clean_name.lower() in {
        "i",
        "me",
        "myself",
        "my own",
        "mine",
        "my",
    }:

        clean_name = (
            SELF_PROFILE_NAME
        )

    profile = (
        await find_profile_by_name(
            user_id=user_id,
            name=clean_name,
        )
    )

    if profile is None:

        if clean_name == SELF_PROFILE_NAME:

            return (
                "I don't have any stored "
                "information about you yet."
            )

        return (
            f"I don't have any stored information "
            f"about {clean_name} yet."
        )

    profile = _clean_profile(
        profile
    )

    if profile is None:

        if clean_name == SELF_PROFILE_NAME:

            return (
                "I don't have any stored "
                "information about you yet."
            )

        return (
            f"I don't have any stored information "
            f"about {clean_name} yet."
        )

    # --------------------------------------------------------------
    # Self profile
    # --------------------------------------------------------------
    if profile.name.lower() == SELF_PROFILE_NAME:

        label = "you"

    else:

        relationship_names = {
            "father",
            "mother",
            "wife",
            "husband",
            "brother",
            "sister",
            "son",
            "daughter",
            "friend",
            "girlfriend",
            "boyfriend",
            "grandfather",
            "grandmother",
            "uncle",
            "aunt",
            "cousin",
        }

        if (
            profile.name.lower()
            in relationship_names
        ):

            label = (
                f"your {profile.name}"
            )

        else:

            label = profile.name

    sentences: list[
        str
    ] = []

    if profile.allergies:

        if label == "you":

            sentences.append(
                "You are allergic to "
                + ", ".join(
                    profile.allergies
                )
                + "."
            )

        else:

            sentences.append(
                f"{label.capitalize()} is allergic to "
                + ", ".join(
                    profile.allergies
                )
                + "."
            )

    if profile.dislikes:

        if label == "you":

            sentences.append(
                "You dislike "
                + ", ".join(
                    profile.dislikes
                )
                + "."
            )

        else:

            sentences.append(
                f"{label.capitalize()} dislikes "
                + ", ".join(
                    profile.dislikes
                )
                + "."
            )

    if profile.likes:

        if label == "you":

            sentences.append(
                "You like "
                + ", ".join(
                    profile.likes
                )
                + "."
            )

        else:

            sentences.append(
                f"{label.capitalize()} likes "
                + ", ".join(
                    profile.likes
                )
                + "."
            )

    if profile.notes:

        sentences.append(
            f"Note: {profile.notes}"
        )

    if not sentences:

        if label == "you":

            return (
                "I have a profile for you, "
                "but no detailed preferences are stored yet."
            )

        return (
            f"I have a profile for {label}, "
            "but no detailed preferences are stored yet."
        )

    return " ".join(
        sentences
    )


async def list_all_profiles(
    user_id: str,
) -> list[RecipientProfileRow]:

    raw_profiles = (
        await list_profiles(
            user_id
        )
    )

    result: list[
        RecipientProfileRow
    ] = []

    for profile in raw_profiles:

        cleaned = _clean_profile(
            profile
        )

        if cleaned is not None:

            result.append(
                cleaned
            )

    return result


async def clear_user_memory(
    user_id: str,
) -> None:

    await delete_profiles_for_user(
        user_id
    )