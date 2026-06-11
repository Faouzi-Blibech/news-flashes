"""Prompt construction for the Flash generation stage.

All functions are pure (no network calls) and return plain strings.
The only I/O is ``load_example()`` reading the bundled template file.
"""

from __future__ import annotations

import re
from pathlib import Path

from news_flashes.models.schema import Event, MarketContext

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_EXAMPLE_PATH = _TEMPLATES_DIR / "example_flash_fr.md"

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
Tu es l'analyste principal du Desk FX d'une banque tunisienne spécialisée dans \
le conseil aux entreprises sur les risques de change. Ta mission est de rédiger \
des "news flash" destinées aux clients professionnels, en français, dans un style \
professionnel, précis et concis.

### Format obligatoire

Chaque news flash DOIT respecter EXACTEMENT la structure suivante :

1. **Salutation** : commence toujours par « Bonsoir chers clients, »
2. **Introduction** : une phrase courte identifiant l'événement déclencheur et sa \
surprise éventuelle par rapport aux attentes.
3. **Sections numérotées** :
   - Section 1 — Niveaux techniques à surveiller : cite des niveaux de prix \
spécifiques et chiffrés (supports, résistances, moyennes mobiles) pour les \
instruments pertinents, notamment DXY et USD/JPY lorsqu'ils sont présents dans \
les données fournies.
   - Section 2 — Contexte de marché : explique l'importance de l'événement, \
les implications pour la politique monétaire, les flux de capitaux et le sentiment \
global.
   - Section 3 — Impact sur les cotations TND : analyse concrètement les \
répercussions sur le Dinar Tunisien (USD/TND, EUR/TND) et les recommandations \
pratiques pour les entreprises clientes.
4. **Synthèse** : un paragraphe de synthèse présentant la lecture du desk, les \
risques résiduels et les prochains catalyseurs à surveiller.
5. **Clôture** : une formule de politesse, suivie de « La Desk FX ».
6. **Graphique** : inclure la mention « Voir graphique ci-dessous. »

### Règles impératives

- Rédige EXCLUSIVEMENT en français.
- N'invente AUCUN chiffre : utilise uniquement les niveaux et données fournis \
dans le message utilisateur. Tu peux arrondir légèrement pour fluidifier la \
lecture, mais ne fabrique pas de niveaux qui n'existent pas dans les données.
- N'inclus PAS de disclaimer légal (il sera ajouté automatiquement).
- Ne mentionne PAS de graphique externe et ne décris PAS un graphique hypothétique ; \
la simple mention « Voir graphique ci-dessous » suffit.
- Adopte un ton professionnel et direct, adapté à des trésoriers d'entreprise \
et des directeurs financiers francophones.
- Longueur cible : 300–500 mots (corps du message, hors salutation et clôture).
"""

# ---------------------------------------------------------------------------
# Few-shot example loader
# ---------------------------------------------------------------------------

_PLACEHOLDER_COMMENT_RE = re.compile(
    r"<!--.*?PLACEHOLDER.*?-->", re.DOTALL | re.IGNORECASE
)


def load_example() -> str:
    """Return the house-style example flash, with the placeholder comment removed.

    Reads the bundled ``templates/example_flash_fr.md`` relative to this module.
    """
    raw = _EXAMPLE_PATH.read_text(encoding="utf-8")
    # Strip the HTML placeholder comment so it never reaches the model.
    cleaned = _PLACEHOLDER_COMMENT_RE.sub("", raw).lstrip()
    return cleaned


# ---------------------------------------------------------------------------
# User-message builder
# ---------------------------------------------------------------------------

def _trend_summary(history_values: list[float]) -> str:
    """Return a short French trend description from a price series."""
    if len(history_values) < 2:
        return "données insuffisantes"
    first = history_values[0]
    last = history_values[-1]
    change_pct = (last - first) / abs(first) * 100 if first else 0.0
    if change_pct > 0.5:
        return f"tendance haussière sur la période ({change_pct:+.1f} %)"
    elif change_pct < -0.5:
        return f"tendance baissière sur la période ({change_pct:+.1f} %)"
    else:
        return f"évolution quasi stable sur la période ({change_pct:+.1f} %)"


def build_user_message(event: Event, market_context: MarketContext) -> str:
    """Render the triggering event and market snapshot into a French briefing.

    This string forms the user turn sent to the model.  It is a pure function:
    no network calls, no side-effects.
    """
    lines: list[str] = []

    # ---- Event block -------------------------------------------------------
    lines.append("## Événement déclencheur\n")
    lines.append(f"- **Titre** : {event.title}")
    if event.country:
        lines.append(f"- **Pays** : {event.country}")
    if event.currency:
        lines.append(f"- **Devise concernée** : {event.currency}")
    if event.impact:
        lines.append(f"- **Impact attendu** : {event.impact}")
    if event.event_time:
        lines.append(
            f"- **Heure de publication** : "
            f"{event.event_time.strftime('%Y-%m-%d %H:%M UTC')}"
        )

    # Calendar fields
    if event.actual is not None:
        lines.append(f"- **Résultat réel** : {event.actual}")
    if event.forecast is not None:
        lines.append(f"- **Prévision consensus** : {event.forecast}")
    if event.previous is not None:
        lines.append(f"- **Chiffre précédent** : {event.previous}")

    # News fields
    if event.headline:
        lines.append(f"- **Titre de l'article** : {event.headline}")
    if event.summary:
        lines.append(f"\n**Résumé** : {event.summary}")

    lines.append("")

    # ---- Market quotes block -----------------------------------------------
    if market_context.quotes:
        lines.append("## Cotations de marché actuelles\n")
        for symbol, q in market_context.quotes.items():
            sign = "+" if q.change >= 0 else ""
            lines.append(
                f"- **{symbol}** : {q.level:.4f} "
                f"({sign}{q.change:.4f} sur la séance, "
                f"relevé à {q.asof.strftime('%H:%M UTC')})"
            )
        lines.append("")

    # ---- History / trend block ---------------------------------------------
    if market_context.history:
        lines.append("## Tendances récentes (30 dernières séances)\n")
        for symbol, pts in market_context.history.items():
            if pts:
                values = [p.value for p in pts]
                trend = _trend_summary(values)
                start_val = values[0]
                end_val = values[-1]
                lines.append(
                    f"- **{symbol}** : de {start_val:.4f} à {end_val:.4f} — {trend}"
                )
        lines.append("")

    # ---- Instruction -------------------------------------------------------
    lines.append(
        "Sur la base de ces données, rédige le news flash FX pour nos clients, "
        "en respectant scrupuleusement le format et les règles décrits dans tes instructions."
    )

    return "\n".join(lines)
