"""Streamlit review dashboard — human-in-the-loop control panel.

Run with:
    streamlit run src/news_flashes/review/app.py

Or with PYTHONPATH set:
    PYTHONPATH=src streamlit run src/news_flashes/review/app.py
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components
from sqlmodel import select

from news_flashes.models.db import get_session, init_db
from news_flashes.models.schema import Flash, FlashStatus
from news_flashes.config import settings
from news_flashes.delivery.charts import render_history_chart
from news_flashes.delivery.clients import load_clients
from news_flashes.delivery.render import render_email
from news_flashes.delivery.sender import StubSender, send_flash
from news_flashes.generation.generator import generate_draft
from news_flashes.review.logic import actionable_statuses, apply_approval, can_approve

# ---------------------------------------------------------------------------
# Page config (must be the first Streamlit call)
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Desk FX — Revue des News Flashes",
    page_icon="📊",
    layout="wide",
)

# ---------------------------------------------------------------------------
# DB init (once per process, idempotent)
# ---------------------------------------------------------------------------

init_db()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STATUS_LABELS: dict[FlashStatus, str] = {
    FlashStatus.CANDIDATE: "Candidat",
    FlashStatus.DRAFT:     "Brouillon",
    FlashStatus.APPROVED:  "Approuvé",
    FlashStatus.SENT:      "Envoyé",
    FlashStatus.REJECTED:  "Rejeté",
}

_STATUS_COLORS: dict[FlashStatus, str] = {
    FlashStatus.CANDIDATE: "#888888",
    FlashStatus.DRAFT:     "#1f77b4",
    FlashStatus.APPROVED:  "#2ca02c",
    FlashStatus.SENT:      "#9467bd",
    FlashStatus.REJECTED:  "#d62728",
}


def _badge(status: FlashStatus) -> str:
    color = _STATUS_COLORS.get(status, "#888888")
    label = _STATUS_LABELS.get(status, status.value)
    return f'<span style="background:{color};color:white;padding:2px 8px;border-radius:4px;font-size:0.85em;">{label}</span>'


# ---------------------------------------------------------------------------
# Sidebar — status filter
# ---------------------------------------------------------------------------

st.title("📰 Desk FX — Revue des News Flashes")

st.sidebar.header("Filtre statut")

all_statuses = list(FlashStatus)
defaults = sorted(actionable_statuses(), key=lambda s: s.value)

selected_statuses = st.sidebar.multiselect(
    "Afficher les statuts",
    options=[s.value for s in all_statuses],
    default=[s.value for s in defaults],
    format_func=lambda v: _STATUS_LABELS.get(FlashStatus(v), v),
)

# ---------------------------------------------------------------------------
# Query flashes
# ---------------------------------------------------------------------------

with get_session() as session:
    stmt = select(Flash)
    if selected_statuses:
        stmt = stmt.where(Flash.status.in_(selected_statuses))  # type: ignore[attr-defined]
    stmt = stmt.order_by(Flash.created_at.desc())  # type: ignore[union-attr]
    flashes: list[Flash] = list(session.exec(stmt).all())

# ---------------------------------------------------------------------------
# Flash list / select
# ---------------------------------------------------------------------------

if not flashes:
    st.info("Aucun flash ne correspond au filtre sélectionné.")
    st.stop()

# Build display labels for the selectbox
def _flash_label(f: Flash) -> str:
    status_label = _STATUS_LABELS.get(f.status, f.status.value)
    subject = f.subject or (f.get_event().title if f.event else f"Flash #{f.id}")
    ts = f.created_at.strftime("%d/%m/%Y %H:%M") if f.created_at else ""
    return f"[{status_label}] {subject}  ({ts})"


flash_by_id = {f.id: f for f in flashes}
flash_ids = [f.id for f in flashes]

# Persist selected flash id across reruns
if "selected_flash_id" not in st.session_state:
    st.session_state.selected_flash_id = flash_ids[0] if flash_ids else None

# If previously selected flash is no longer in the filtered list, reset
if st.session_state.selected_flash_id not in flash_by_id:
    st.session_state.selected_flash_id = flash_ids[0] if flash_ids else None

selected_id = st.selectbox(
    "Sélectionner un flash",
    options=flash_ids,
    index=flash_ids.index(st.session_state.selected_flash_id) if st.session_state.selected_flash_id in flash_ids else 0,
    format_func=lambda fid: _flash_label(flash_by_id[fid]),
    key="flash_selectbox",
)

st.session_state.selected_flash_id = selected_id

if selected_id is None:
    st.stop()

flash = flash_by_id[selected_id]

# ---------------------------------------------------------------------------
# Detail view — two columns
# ---------------------------------------------------------------------------

left_col, right_col = st.columns([1, 1])

# ---- LEFT: Source context (read-only) ----------------------------------------

with left_col:
    st.subheader("Contexte source")

    # Status badge
    st.markdown(
        f"**Statut :** {_badge(flash.status)}",
        unsafe_allow_html=True,
    )

    # Audit trail for advanced states
    if flash.approved_by:
        st.markdown(f"**Approuvé par :** {flash.approved_by}")
    if flash.approved_at:
        st.markdown(f"**Approuvé le :** {flash.approved_at.strftime('%d/%m/%Y %H:%M UTC')}")
    if flash.sent_at:
        st.markdown(f"**Envoyé le :** {flash.sent_at.strftime('%d/%m/%Y %H:%M UTC')}")

    st.divider()

    # Event details
    if flash.event:
        event = flash.get_event()
        st.markdown("#### Événement")
        st.markdown(f"**Titre :** {event.title}")
        if event.source:
            st.markdown(f"**Source :** {event.source}")
        if event.currency:
            st.markdown(f"**Devise :** {event.currency}")
        if event.country:
            st.markdown(f"**Pays :** {event.country}")
        if event.impact:
            st.markdown(f"**Impact :** {event.impact}")
        if event.event_time:
            st.markdown(f"**Date/heure :** {event.event_time.strftime('%d/%m/%Y %H:%M UTC')}")
        if event.actual:
            st.markdown(f"**Réel :** {event.actual}")
        if event.forecast:
            st.markdown(f"**Prévision :** {event.forecast}")
        if event.previous:
            st.markdown(f"**Précédent :** {event.previous}")
        if event.headline:
            st.markdown(f"**Titre :** {event.headline}")
        if event.url:
            st.markdown(f"**URL :** [{event.url}]({event.url})")
        if event.summary:
            st.markdown(f"**Résumé :** {event.summary}")
    else:
        st.info("Aucune donnée d'événement disponible.")

    st.divider()

    # Market context — quotes table
    if flash.market_context:
        mc = flash.get_market_context()
        if mc.quotes:
            st.markdown("#### Cotations de marché")
            quote_data = [
                {
                    "Symbole": sym,
                    "Niveau": f"{q.level:.4f}",
                    "Variation": f"{q.change:+.4f}",
                    "Heure": q.asof.strftime("%d/%m %H:%M"),
                }
                for sym, q in mc.quotes.items()
            ]
            st.table(quote_data)

        # Chart
        if mc.history:
            st.markdown("#### Graphique historique")
            chart_png = render_history_chart(mc)
            if chart_png:
                st.image(chart_png, use_container_width=True)

# ---- RIGHT: Draft + actions --------------------------------------------------

with right_col:
    st.subheader("Brouillon et actions")

    # Subject field
    current_subject = flash.subject or ""
    edited_subject = st.text_input(
        "Sujet de l'email",
        value=current_subject,
        key=f"subject_{flash.id}",
    )

    # Body text area
    body_placeholder = flash.edited_text or flash.draft_text or ""
    edited_text = st.text_area(
        "Corps du message (éditez avant d'approuver)",
        value=body_placeholder,
        height=300,
        key=f"body_{flash.id}",
    )

    st.divider()

    # ---- CANDIDATE: Generate draft button -----------------------------------
    if flash.status == FlashStatus.CANDIDATE:
        st.markdown("##### Générer un brouillon")
        api_key_ok = bool(settings.anthropic_api_key)
        if not api_key_ok:
            st.info(
                "Clé API Anthropic non configurée. "
                "Définissez `ANTHROPIC_API_KEY` dans `.env` pour activer la génération."
            )
        gen_btn = st.button(
            "📝 Générer le brouillon",
            disabled=not api_key_ok,
            key=f"gen_{flash.id}",
        )
        if gen_btn and api_key_ok:
            try:
                with st.spinner("Génération en cours…"):
                    with get_session() as session:
                        fresh_flash = session.get(Flash, flash.id)
                        if fresh_flash is None:
                            st.error("Flash introuvable en base de données.")
                        else:
                            generate_draft(fresh_flash)
                            session.add(fresh_flash)
                            session.commit()
                    st.success("Brouillon généré avec succès.")
                    st.rerun()
            except Exception as exc:
                st.error(f"Erreur lors de la génération : {exc}")

    # ---- DRAFT: Approve / Reject -------------------------------------------
    if flash.status == FlashStatus.DRAFT:
        st.markdown("##### Approbation")
        approver_name = st.text_input(
            "Approuvé par (nom / initiales)*",
            key=f"approver_{flash.id}",
        )

        ok, reason = can_approve(flash, edited_text, approver_name)
        if not ok and reason:
            st.warning(reason)

        approve_btn = st.button(
            "✅ Approuver",
            key=f"approve_{flash.id}",
            type="primary",
        )
        if approve_btn:
            ok2, reason2 = can_approve(flash, edited_text, approver_name)
            if not ok2:
                st.error(reason2)
            else:
                try:
                    with get_session() as session:
                        fresh_flash = session.get(Flash, flash.id)
                        if fresh_flash is None:
                            st.error("Flash introuvable en base de données.")
                        else:
                            apply_approval(
                                fresh_flash,
                                edited_text,
                                edited_subject,
                                approver_name,
                            )
                            session.add(fresh_flash)
                            session.commit()
                    st.success("Flash approuvé avec succès.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Erreur lors de l'approbation : {exc}")

        st.divider()

    # ---- APPROVED: Send button -------------------------------------------
    if flash.status == FlashStatus.APPROVED:
        st.markdown("##### Envoi")

        # Preview
        with st.expander("Aperçu de l'email HTML", expanded=False):
            html_preview = render_email(flash)
            components.html(html_preview, height=500, scrolling=True)

        with get_session() as session:
            clients = load_clients(session)

        if not clients:
            st.warning(
                "Aucun client actif trouvé en base de données. "
                "Importez des clients avant d'envoyer."
            )

        send_btn = st.button(
            "📤 Envoyer",
            key=f"send_{flash.id}",
            type="primary",
            disabled=not clients,
        )
        if send_btn and clients:
            try:
                with get_session() as session:
                    fresh_flash = session.get(Flash, flash.id)
                    if fresh_flash is None:
                        st.error("Flash introuvable en base de données.")
                    else:
                        fresh_clients = load_clients(session)
                        sender = StubSender(outdir="outbox")
                        count = send_flash(fresh_flash, fresh_clients, sender, session=session)
                st.success(
                    f"{count} email(s) envoyé(s). "
                    "Les fichiers HTML sont disponibles dans le dossier `outbox/`."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Erreur lors de l'envoi : {exc}")

    # ---- REJECT button (available from CANDIDATE, DRAFT, APPROVED) ----------
    rejectable = flash.status in (
        FlashStatus.CANDIDATE,
        FlashStatus.DRAFT,
        FlashStatus.APPROVED,
    )
    if rejectable:
        st.divider()
        reject_btn = st.button(
            "🚫 Rejeter",
            key=f"reject_{flash.id}",
        )
        if reject_btn:
            try:
                with get_session() as session:
                    fresh_flash = session.get(Flash, flash.id)
                    if fresh_flash is None:
                        st.error("Flash introuvable en base de données.")
                    else:
                        fresh_flash.advance_to(FlashStatus.REJECTED)
                        session.add(fresh_flash)
                        session.commit()
                st.success("Flash rejeté.")
                st.rerun()
            except Exception as exc:
                st.error(f"Erreur lors du rejet : {exc}")

    # ---- SENT / REJECTED: read-only info -----------------------------------
    if flash.status in (FlashStatus.SENT, FlashStatus.REJECTED):
        label = "Envoyé" if flash.status == FlashStatus.SENT else "Rejeté"
        st.info(f"Ce flash est en statut final : **{label}**. Aucune action possible.")
