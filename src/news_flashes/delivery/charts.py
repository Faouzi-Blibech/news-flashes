"""Chart generation for FX news-flash emails.

Uses the non-interactive Agg backend so rendering works headlessly on the
server (no display required).
"""

from __future__ import annotations

import base64
import io

import matplotlib
matplotlib.use("Agg")  # must be set before importing pyplot
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from news_flashes.models.schema import MarketContext

# Instruments the desk cares about, in preference order.
_DESK_SYMBOLS = ["DXY", "USDJPY", "EURUSD"]


def render_history_chart(
    market_context: MarketContext,
    symbols: list[str] | None = None,
) -> bytes | None:
    """Render a price-history chart and return it as PNG bytes.

    Parameters
    ----------
    market_context:
        The ``MarketContext`` whose ``history`` dict provides the series.
    symbols:
        Instruments to plot (e.g. ``["DXY", "USDJPY"]``).  When ``None``,
        the function tries ``["DXY", "USDJPY"]`` first, then falls back to
        whatever instruments are present in ``market_context.history``.

    Returns
    -------
    bytes or None
        Raw PNG bytes, or ``None`` when there is no usable history data.
    """
    available = market_context.history

    if not available:
        return None

    # Determine which symbols to plot
    if symbols is not None:
        to_plot = [s for s in symbols if s in available and available[s]]
    else:
        preferred = [s for s in _DESK_SYMBOLS if s in available and available[s]]
        if preferred:
            to_plot = preferred[:2]  # DXY + USDJPY by default
        else:
            to_plot = [s for s in available if available[s]]

    if not to_plot:
        return None

    n = len(to_plot)
    fig, axes = plt.subplots(
        n, 1,
        figsize=(9, 3.5 * n),
        sharex=True,
        constrained_layout=True,
    )

    # Ensure axes is always a list for uniform handling
    if n == 1:
        axes = [axes]

    for ax, symbol in zip(axes, to_plot):
        points = available[symbol]
        # Sort by time to be safe
        points_sorted = sorted(points, key=lambda p: p.t)
        dates = [p.t for p in points_sorted]
        values = [p.value for p in points_sorted]

        ax.plot(dates, values, linewidth=1.8, color="#1f77b4")
        ax.fill_between(dates, values, alpha=0.12, color="#1f77b4")

        ax.set_title(symbol, fontsize=12, fontweight="bold", pad=6)
        ax.set_ylabel("Valeur", fontsize=9)
        ax.grid(True, linestyle="--", alpha=0.4)
        ax.tick_params(axis="both", labelsize=8)

        # Format x-axis dates on the bottom-most subplot only
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))

    # Rotate date labels only on the last (bottom) axis
    plt.setp(axes[-1].xaxis.get_majorticklabels(), rotation=30, ha="right")
    fig.suptitle("Évolution des cours — 30 jours", fontsize=11, y=1.01 if n > 1 else 1.02)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def chart_data_uri(png_bytes: bytes) -> str:
    """Convert raw PNG bytes to a ``data:image/png;base64,...`` URI string.

    Suitable for use as the ``src`` attribute of an HTML ``<img>`` element.
    """
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"
