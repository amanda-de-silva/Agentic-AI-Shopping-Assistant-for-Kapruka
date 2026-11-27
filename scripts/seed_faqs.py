"""
Loads config/faqs.yaml into wherever FAQ lookups are served from.

Currently a no-op beyond validation: the router/orchestrator reads config/faqs.yaml
directly at request time (see backend/agents/orchestrator.py:_match_faq), so there is
no separate FAQ table to seed today. This script exists as the seam for the day FAQs
move into Supabase (e.g. to support an admin UI) -- it already validates the YAML
shape so that migration is a small diff, not a rewrite.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_faqs_config  # noqa: E402


def main() -> None:
    faqs = get_faqs_config().get("faqs", [])
    if not faqs:
        print("No FAQs found in config/faqs.yaml.")
        return
    for faq in faqs:
        assert faq.get("id"), f"FAQ missing 'id': {faq}"
        assert faq.get("triggers"), f"FAQ '{faq.get('id')}' missing 'triggers'"
        assert faq.get("answer"), f"FAQ '{faq.get('id')}' missing 'answer'"
    print(f"Validated {len(faqs)} FAQs from config/faqs.yaml -- OK.")


if __name__ == "__main__":
    main()
