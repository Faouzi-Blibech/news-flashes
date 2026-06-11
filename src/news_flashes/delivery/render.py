"""HTML email rendering for approved FX news-flash notes.

Entry point
-----------
>>> html = render_email(flash)

The function auto-generates a price chart from the flash's embedded
``MarketContext`` unless overridden.

Disclaimer handling
-------------------
The disclaimer source file (``generation/templates/disclaimer.txt``) begins
with an internal compliance note on a line prefixed with ``#``.  That line
is stripped before any client-facing output is produced.  ``load_disclaimer``
is exposed as a reusable helper.
"""

from __future__ import annotations

from pathlib import Path

import markdown as md
from jinja2 import Environment, FileSystemLoader, select_autoescape

from news_flashes.models.schema import Flash

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_DISCLAIMER_PATH = (
    Path(__file__).parent.parent / "generation" / "templates" / "disclaimer.txt"
)

# ---------------------------------------------------------------------------
# Jinja2 environment
# ---------------------------------------------------------------------------

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def load_disclaimer() -> str:
    """Load and clean the legal disclaimer text.

    Strips any leading lines that start with ``#`` (internal compliance notes)
    and returns the remaining body as a plain string.
    """
    raw = _DISCLAIMER_PATH.read_text(encoding="utf-8")
    lines = raw.splitlines()
    # Drop all leading lines that are blank or start with '#'
    body_lines: list[str] = []
    found_body = False
    for line in lines:
        stripped = line.strip()
        if not found_body:
            if stripped.startswith("#") or stripped == "":
                continue
            found_body = True
        body_lines.append(line)
    return "\n".join(body_lines).strip()


def render_email(
    flash: Flash,
    *,
    chart_png: bytes | None = None,
    include_chart: bool = True,
) -> str:
    """Render an approved flash as a branded HTML email string.

    Parameters
    ----------
    flash:
        The approved :class:`~news_flashes.models.schema.Flash` to render.
        The body text is taken from ``flash.edited_text`` if set, otherwise
        ``flash.draft_text``.
    chart_png:
        Optional explicit PNG bytes to embed.  When ``None`` (the default)
        **and** ``include_chart=True``, the chart is auto-generated from the
        flash's embedded ``MarketContext``.  Pass ``chart_png=b""`` (or set
        ``include_chart=False``) to suppress the chart entirely.
    include_chart:
        Set to ``False`` to render the email without a chart, regardless of
        whether ``chart_png`` was supplied or history is available.

    Returns
    -------
    str
        Full HTML document as a string, ready to be sent as an HTML email
        body.
    """
    # ---- Body text ---------------------------------------------------------
    body_text: str = flash.edited_text or flash.draft_text or ""

    # Convert Markdown to HTML (nl2br converts bare newlines inside paragraphs)
    body_html = md.markdown(
        body_text,
        extensions=["nl2br", "tables"],
    )

    # ---- Chart -------------------------------------------------------------
    chart_uri: str | None = None

    if include_chart:
        if chart_png is None:
            # Auto-generate from the flash's embedded market context
            from news_flashes.delivery.charts import (
                chart_data_uri,
                render_history_chart,
            )
            mc = flash.get_market_context()
            png = render_history_chart(mc)
            if png is not None:
                chart_uri = chart_data_uri(png)
        elif chart_png:  # non-empty bytes were explicitly passed
            from news_flashes.delivery.charts import chart_data_uri
            chart_uri = chart_data_uri(chart_png)
        # else: chart_png == b"" or falsy → no chart

    # ---- Disclaimer --------------------------------------------------------
    disclaimer_text = load_disclaimer()

    # ---- Render template ---------------------------------------------------
    template = _env.get_template("email.html.j2")
    return template.render(
        subject=flash.subject or "Flash FX",
        body_html=body_html,
        chart_uri=chart_uri,
        disclaimer_text=disclaimer_text,
    )
