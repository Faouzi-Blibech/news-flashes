"""Flash generation: CANDIDATE → DRAFT via Claude.

Calling convention
------------------
>>> flash = generate_draft(flash)          # uses settings.anthropic_api_key
>>> flash = generate_draft(flash, client=fake_client)  # injectable for tests

The function does NOT commit to the database.  The caller owns the
SQLModel session and the commit.
"""

from __future__ import annotations

from typing import Any

from news_flashes.config import settings
from news_flashes.models.schema import Flash, FlashStatus
from news_flashes.generation.prompt import (
    SYSTEM_PROMPT,
    build_user_message,
    load_example,
)

# ---------------------------------------------------------------------------
# Subject-line strategy
# ---------------------------------------------------------------------------
# We use a deterministic approach: derive the subject directly from the event
# data.  This is simpler, faster, and 100 % reliable — no extra LLM call, no
# parsing risk.  The format is:
#
#   Flash FX — {event.title}  [ | {country}]
#
# e.g. "Flash FX — US CPI (YoY) | US"
#
# The human editor can refine the subject during the approval step.

def _build_subject(event) -> str:
    parts = [f"Flash FX — {event.title}"]
    if event.country:
        parts.append(event.country)
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_draft(
    flash: Flash,
    *,
    model: str | None = None,
    client: Any | None = None,
) -> Flash:
    """Generate a French news-flash draft and advance the Flash to DRAFT.

    Parameters
    ----------
    flash:
        A Flash whose ``status`` is CANDIDATE.  The function reads the
        embedded ``Event`` and ``MarketContext`` from the JSON columns.
    model:
        Optional model override.  Defaults to ``settings.model_default``.
    client:
        Optional pre-built ``anthropic.Anthropic`` client.  If ``None``, one
        is constructed from ``settings.anthropic_api_key``.  Pass a stub here
        in tests to avoid network calls.

    Returns
    -------
    Flash
        The same flash object with ``draft_text``, ``subject``, and
        ``status == FlashStatus.DRAFT`` set.  The caller must commit.

    Raises
    ------
    InvalidTransition
        If ``flash.status`` is not CANDIDATE (enforced by ``advance_to``).
    """
    # --- Resolve the Anthropic client -------------------------------------
    if client is None:
        from anthropic import Anthropic  # deferred import for testability
        client = Anthropic(api_key=settings.anthropic_api_key)

    resolved_model = model or settings.model_default

    # --- Load structured data from the flash ------------------------------
    event = flash.get_event()
    mc = flash.get_market_context()

    # --- Build the few-shot + user message --------------------------------
    example_text = load_example()

    few_shot_prefix = (
        "Voici un exemple de news flash rédigé dans le style attendu :\n\n"
        "---\n"
        f"{example_text}\n"
        "---\n\n"
        "Maintenant, rédige un nouveau news flash pour l'événement suivant :\n\n"
    )

    user_message = few_shot_prefix + build_user_message(event, mc)

    # --- Call the model ---------------------------------------------------
    response = client.messages.create(
        model=resolved_model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_message},
        ],
    )

    # --- Parse the response -----------------------------------------------
    # The SDK returns a Message with .content: list of content blocks.
    # Text blocks have a .text attribute.
    draft_text: str = ""
    for block in response.content:
        if hasattr(block, "text"):
            draft_text += block.text

    draft_text = draft_text.strip()

    # --- Populate the flash -----------------------------------------------
    flash.draft_text = draft_text
    flash.subject = _build_subject(event)

    # advance_to enforces CANDIDATE → DRAFT and raises InvalidTransition otherwise
    flash.advance_to(FlashStatus.DRAFT)

    return flash
