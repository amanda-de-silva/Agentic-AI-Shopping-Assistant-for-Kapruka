"""
Loads and renders the Jinja system prompt templates in backend/prompts/.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(PROMPTS_DIR)),
        autoescape=select_autoescape(disabled_extensions=("jinja",)),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_prompt(template_name: str, **context) -> str:
    """`template_name` is the filename inside backend/prompts/, e.g. 'router_system.jinja'."""
    template = _env().get_template(template_name)
    return template.render(**context)
