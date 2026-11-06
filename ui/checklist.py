"""Справочный чек-лист перед входом в сделку."""
import streamlit as st


def _render_pretrade_checklist(items):
    if not items:
        return
    _icons = {"ok": "✅", "fail": "❌", "warn": "⚠️", "skip": "ℹ️"}
    _colors = {"ok": "#00a651", "fail": "#d32f2f",
               "warn": "#e6a500", "skip": "#7a7a7a"}
    lines = []
    for label, status, msg in items:
        icon = _icons.get(status, "ℹ️")
        color = _colors.get(status, "#7a7a7a")
        lines.append(
            f"<div style='padding:3px 0; font-size:.83rem; line-height:1.35;'>"
            f"<span style='color:{color}; font-weight:700;'>{icon}</span> "
            f"<b style='color:#1a3b4f;'>{label}</b>"
            f"<span style='color:#5a6b78;'> — {msg}</span></div>"
        )
    st.markdown(
        "<div style='background:#f6f9fc; border:1px solid #dce8f1; "
        "border-radius:8px; padding:10px 14px; margin:8px 0;'>"
        "<div style='font-size:.72rem; color:#2c506d; font-weight:700; "
        "text-transform:uppercase; letter-spacing:.05em; margin-bottom:6px;'>"
        "Чек-лист перед входом (справочно)</div>"
        + "".join(lines) +
        "</div>",
        unsafe_allow_html=True,
    )