"""Мелкие helper-ы: форматирование, генерация id, postMessage-мост."""
import json
import uuid

import streamlit.components.v1 as components


# =====================================================================
# Форматирование / идентификаторы
# =====================================================================
def _color_call_put(option):
    if option == "Call":
        return f"<span style='color:#00a651; font-weight:700;'>{option}</span>"
    if option == "Put":
        return f"<span style='color:#d32f2f; font-weight:700;'>{option}</span>"
    return option


def _side_from_qty(qty):
    if qty > 0:
        return "Buy"
    if qty < 0:
        return "Sell"
    return "—"


def _side_ui(side):
    return {"Buy": "Покупка", "Sell": "Продажа"}.get(side, side)


def _side_internal(side_ui):
    return {"Покупка": "Buy", "Продажа": "Sell"}.get(side_ui, side_ui)


def _new_position_id():
    return uuid.uuid4().hex[:8]


def _fmt_strike(k, asset_type=None):
    if k is None:
        return "—"
    try:
        kf = float(k)
    except (TypeError, ValueError):
        return str(k)
    if asset_type == "Валюта":
        return f"{kf:.1f}"
    if kf.is_integer():
        return f"{int(kf)}"
    return f"{kf:.2f}"


def _greek_delta_color(delta):
    if delta is None or not isinstance(delta, (int, float)):
        return ""
    d = abs(delta)
    if 0.25 <= d <= 0.45:
        return "#00a651"
    if (0.15 <= d < 0.25) or (0.45 < d <= 0.55):
        return "#e6a500"
    return "#d32f2f"


def _greek_theta_color(theta, vega):
    if theta is None or vega is None:
        return ""
    if not isinstance(theta, (int, float)) or not isinstance(vega, (int, float)):
        return ""
    if abs(vega) < 1e-9:
        return ""
    ratio = abs(theta) / abs(vega)
    if ratio > 1.0:
        return "#00a651"
    if ratio > 0.5:
        return "#e6a500"
    return "#d32f2f"


def _safe_float_qp(key, default=0.0):
    """Безопасное чтение query-параметра. Используется в app.py."""
    import streamlit as st
    try:
        return float(st.query_params.get(key, default) or default)
    except (TypeError, ValueError):
        return default


# =====================================================================
# postMessage-мост в iframe с калькулятором
# =====================================================================
def _send_to_iframes(payload, delays=(300, 1500)):
    delays_js = "\n".join([f"setTimeout(send, {d});" for d in delays])
    js = f"""
    <script>
    (function() {{
      const payload = {json.dumps(payload, ensure_ascii=False)};
      function send() {{
        try {{
          const frames = window.parent.document.querySelectorAll('iframe');
          frames.forEach(f => {{
            try {{ f.contentWindow.postMessage(payload, '*'); }} catch (e) {{}}
          }});
        }} catch (e) {{}}
      }}
      send();
      {delays_js}
    }})();
    </script>
    """
    components.html(js, height=0)


def push_expiry_to_calculator(expiry_str, series_code=""):
    _send_to_iframes({
        "type": "setExpiry",
        "value": expiry_str,
        "series_code": series_code,
    })


def push_strikes_to_calculator(strikes_iv, central_strike):
    _send_to_iframes({
        "type": "setStrikes",
        "strikes": strikes_iv,
        "central": central_strike,
    })


def push_alert_levels(ticker, buy_lvl, sell_lvl):
    _send_to_iframes({
        "type": "setAlertLevels",
        "ticker": ticker or "",
        "buy": float(buy_lvl) if buy_lvl is not None else None,
        "sell": float(sell_lvl) if sell_lvl is not None else None,
    })