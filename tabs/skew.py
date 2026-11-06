"""Вкладка «Детектор Skew»."""
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import CHECKLIST_RULES
from core.market_data import format_series_label
from core.options import fetch_optionseries
from core.pricing import _get_current_market_price_for_asset
from core.helpers import _fmt_strike
from core.positions_cache import _get_board_for_analytics


@st.fragment
def _render_skew_tab():
    st.header("Детектор Skew и рекомендации")
    st.caption("Анализ наклона волатильности (volatility skew). "
               "Критерий наличия наклона: σ/μ ≥ 15%.")

    _sk_asset = st.session_state.get("pos_asset", "")
    _sk_cat = st.session_state.get("pos_category", "")

    if not _sk_asset:
        st.warning("Сначала выберите актив и категорию на вкладке «Позиция».")
        return

    st.info(f"Актив: {_sk_asset} · Категория: {_sk_cat} "
            f"(из вкладки «Позиция»)")

    try:
        _sk_series_all = fetch_optionseries(_sk_asset, _sk_cat) or []
    except Exception as e:
        st.error(f"Не удалось получить серии: {e}")
        return

    if not _sk_series_all:
        st.warning(f"ISS не вернул серий для {_sk_asset}")
        return

    _sk_series_sorted = sorted(
        _sk_series_all, key=lambda x: x.get("expiry", "")
    )
    _sk_series_labels = [format_series_label(s["expiry"])
                          for s in _sk_series_sorted]
    _sk_chosen_label = st.selectbox(
        "Опционная серия", _sk_series_labels, index=0,
        key="sk_series_select",
    )
    _sk_chosen_idx = _sk_series_labels.index(_sk_chosen_label)
    _sk_chosen = _sk_series_sorted[_sk_chosen_idx]
    _sk_chosen_code = _sk_chosen["code"]

    st.markdown("---")

    with st.spinner("Загрузка доски опционов…"):
        _board_sk = _get_board_for_analytics(
            _sk_asset, _sk_cat, _sk_chosen_code
        )

    if not _board_sk:
        st.warning("Не удалось загрузить доску опционов.")
        return

    _sk_rows = []
    _calls_sk = _board_sk.get("call") or []
    _puts_sk = _board_sk.get("put") or []

    _calls_sk_map = {c.get("strike"): c for c in _calls_sk
                     if c.get("strike") is not None}
    _puts_sk_map = {p.get("strike"): p for p in _puts_sk
                    if p.get("strike") is not None}
    _all_strikes_sk = sorted(
        set(_calls_sk_map.keys()) | set(_puts_sk_map.keys())
    )

    for _k in _all_strikes_sk:
        _c = _calls_sk_map.get(_k, {})
        _p = _puts_sk_map.get(_k, {})
        _iv_c = _c.get("volatility")
        _iv_p = _p.get("volatility")
        _iv_avg = None
        _ivs = [x for x in (_iv_c, _iv_p) if x is not None]
        if _ivs:
            _iv_avg = float(np.mean([float(x) for x in _ivs]))
        _sk_rows.append({
            "strike": float(_k),
            "iv_call": float(_iv_c) if _iv_c is not None else None,
            "iv_put": float(_iv_p) if _iv_p is not None else None,
            "iv_avg": _iv_avg,
        })

    _df_sk = pd.DataFrame(_sk_rows).dropna(subset=["iv_avg"])
    if _df_sk.empty:
        st.warning("Не удалось получить IV ни по одному страйку.")
        return

    _df_sk = _df_sk.sort_values("strike").reset_index(drop=True)
    _central_sk = _board_sk.get("central_strike")
    _current_price_sk = _get_current_market_price_for_asset(
        _sk_asset, _sk_cat
    )

    _iv_vals = _df_sk["iv_avg"].values
    _iv_mean = float(np.mean(_iv_vals))
    _iv_std = float(np.std(_iv_vals, ddof=1)) if len(_iv_vals) > 1 else 0.0
    _skew_ratio = (_iv_std / _iv_mean * 100) if _iv_mean > 0 else 0.0

    st.markdown("### 1) Метрики наклона волатильности")
    _sm1, _sm2, _sm3, _sm4 = st.columns(4)
    _sm1.metric("Среднее IV", f"{_iv_mean:.2f}%")
    _sm2.metric("σ IV", f"{_iv_std:.2f}%")
    _sm3.metric("σ/μ", f"{_skew_ratio:.2f}%")
    _sm4.metric("Страйков в анализе", len(_df_sk))

    if _skew_ratio < CHECKLIST_RULES["skew_threshold_pct"]:
        st.success(f"σ/μ = {_skew_ratio:.2f}% < 15% — "
                   f"наклон волатильности отсутствует. "
                   f"Стратегии на skew не имеют статистического преимущества.")
    else:
        st.warning(f"σ/μ = {_skew_ratio:.2f}% ≥ 15% — "
                   f"обнаружен наклон волатильности. "
                   f"Есть статистический эдж.")

    _slope = 0.0
    _skew_type = "—"
    _skew_type_label = "—"
    _skew_color = "#7a7a7a"

    if len(_df_sk) >= 3:
        _x = _df_sk["strike"].values
        _y = _df_sk["iv_avg"].values
        try:
            _slope = float(np.polyfit(_x, _y, 1)[0])
        except Exception:
            _slope = 0.0

        _central_ref = (float(_central_sk) if _central_sk is not None
                        else float(_current_price_sk)
                        if _current_price_sk is not None
                        else float(np.median(_x)))

        _left_mask = _df_sk["strike"] < _central_ref
        _right_mask = _df_sk["strike"] > _central_ref

        _iv_left_avg = (_df_sk.loc[_left_mask, "iv_avg"].mean()
                        if _left_mask.any() else np.nan)
        _iv_right_avg = (_df_sk.loc[_right_mask, "iv_avg"].mean()
                         if _right_mask.any() else np.nan)

        if not np.isnan(_iv_left_avg) and not np.isnan(_iv_right_avg):
            _delta_lr = _iv_left_avg - _iv_right_avg
            if _delta_lr > 2:
                _skew_type = "reverse"
                _skew_type_label = "ОБРАТНЫЙ (Reverse) — Put-skew"
                _skew_color = "#1e88e5"
            elif _delta_lr < -2:
                _skew_type = "forward"
                _skew_type_label = "ПРЯМОЙ (Forward) — Call-skew"
                _skew_color = "#e6a500"
            else:
                if (not np.isnan(_iv_left_avg)
                        and not np.isnan(_iv_right_avg)
                        and _iv_left_avg > _iv_mean + 1
                        and _iv_right_avg > _iv_mean + 1):
                    _skew_type = "dual"
                    _skew_type_label = "ДВОЙНОЙ (Dual)"
                    _skew_color = "#9c27b0"
                else:
                    _skew_type = "flat"
                    _skew_type_label = "Плоский / симметричный"
                    _skew_color = "#7a7a7a"

    st.markdown("### 2) Тип наклона")
    st.markdown(
        f"<div style='padding:14px 18px; border-radius:12px; "
        f"background:{_skew_color}20; border-left:6px solid {_skew_color}; "
        f"margin-bottom:14px;'>"
        f"<div style='font-size:1.05rem; font-weight:800; color:{_skew_color};'>"
        f"{_skew_type_label}</div>"
        f"<div style='font-size:.82rem; color:#5a6b78; margin-top:6px;'>"
        f"Наклон регрессии (IV по страйку): "
        f"<b>{_slope:+.4f} %/пункт</b></div></div>",
        unsafe_allow_html=True,
    )

    st.markdown("### 3) Улыбка волатильности и линия регрессии")
    _fig_sk = go.Figure()

    _df_c = _df_sk.dropna(subset=["iv_call"])
    if not _df_c.empty:
        _fig_sk.add_trace(go.Scatter(
            x=_df_c["strike"], y=_df_c["iv_call"],
            mode="markers+lines", name="IV Call",
            line=dict(color="#00a651", width=1.5, dash="dot"),
            marker=dict(size=8, color="#00a651"),
            hovertemplate=("Страйк: %{x}<br>"
                           "IV Call: %{y:.2f}%<extra></extra>"),
        ))

    _df_p = _df_sk.dropna(subset=["iv_put"])
    if not _df_p.empty:
        _fig_sk.add_trace(go.Scatter(
            x=_df_p["strike"], y=_df_p["iv_put"],
            mode="markers+lines", name="IV Put",
            line=dict(color="#d32f2f", width=1.5, dash="dot"),
            marker=dict(size=8, color="#d32f2f"),
            hovertemplate=("Страйк: %{x}<br>"
                           "IV Put: %{y:.2f}%<extra></extra>"),
        ))

    _fig_sk.add_trace(go.Scatter(
        x=_df_sk["strike"], y=_df_sk["iv_avg"],
        mode="lines", name="IV сред.",
        line=dict(color="#1e5a7a", width=3),
        hovertemplate="Страйк: %{x}<br>IV: %{y:.2f}%<extra></extra>",
    ))

    if len(_df_sk) >= 3:
        _xr = np.array([_df_sk["strike"].min(), _df_sk["strike"].max()])
        try:
            _coef = np.polyfit(_df_sk["strike"], _df_sk["iv_avg"], 1)
            _yr = np.polyval(_coef, _xr)
            _fig_sk.add_trace(go.Scatter(
                x=_xr, y=_yr, mode="lines", name="Регрессия",
                line=dict(color="#9c27b0", width=2, dash="dash"),
                hovertemplate="Регрессия<br>%{x:.0f}: %{y:.2f}%<extra></extra>",
            ))
        except Exception:
            pass

    if _central_sk is not None:
        _fig_sk.add_vline(
            x=float(_central_sk),
            line=dict(color="#4a6f8a", width=1.5, dash="dot"),
            annotation_text=f"Центр {_fmt_strike(_central_sk, _sk_cat)}",
            annotation_position="top",
        )
    if _current_price_sk is not None:
        _fig_sk.add_vline(
            x=float(_current_price_sk),
            line=dict(color="#1e88e5", width=2, dash="dash"),
            annotation_text=f"БА {_current_price_sk:.2f}",
            annotation_position="bottom",
        )

    _fig_sk.update_layout(
        title=f"Улыбка волатильности — {_sk_asset} "
              f"({format_series_label(_sk_chosen['expiry'])})",
        xaxis_title="Страйк", yaxis_title="IV, %",
        height=460, margin=dict(l=20, r=20, t=60, b=20),
        legend=dict(orientation="h", yanchor="bottom",
                    y=1.02, xanchor="left", x=0),
        hovermode="x unified",
    )
    st.plotly_chart(_fig_sk, use_container_width=True)

    st.markdown("---")
    st.markdown("### 4) Рекомендации по стратегиям")

    if _skew_ratio < CHECKLIST_RULES["skew_threshold_pct"]:
        st.info("Наклон волатильности отсутствует (σ/μ < 15%). "
                "Стратегии, основанные на skew, не имеют статистического "
                "преимущества. Ориентируйтесь на направленные стратегии "
                "или на общий уровень IV (вкладка «IV Rank / Децили»).")
    elif _skew_type == "forward":
        st.success(
            "Прямой наклон — IV на высоких страйках выше, "
            "чем на низких. Опционы Call 'вне денег' относительно дорогие, "
            "Put 'вне денег' — дешёвые."
        )
        st.markdown("""
**Рекомендуемые стратегии:**

| Стратегия | Комментарий |
|---|---|
| **Пропорциональный Call-спрэд** (1-к-2) | Купить 1 OTM Call + продать 2 дальних OTM Call. |
| **Обратный Put-спрэд** | Купить 2 дешёвых OTM Put + продать 1 дорогой OTM Put. |

**Правила:**
- На длинной стороне — «около денег», на короткой — «вне денег»;
- IV низкая → **обратный Put-спрэд**;
- IV высокая → **пропорциональный Call-спрэд**;
- Обязательно — ментальные стопы.
""")
    elif _skew_type == "reverse":
        st.success(
            "Обратный наклон — IV на низких страйках выше, "
            "чем на высоких. Опционы Put 'вне денег' относительно дорогие, "
            "Call 'вне денег' — дешёвые. Характерен для широких индексов."
        )
        st.markdown("""
**Рекомендуемые стратегии:**

| Стратегия | Комментарий |
|---|---|
| **Обратный Call-спрэд** | Продать 1 Call «в деньгах» + купить 2 OTM Call. |
| **Пропорциональный Put-спрэд** (1-к-2) | Купить 1 ATM Put + продать 2 дальних OTM Put. |

**Особенности для индексов:**
- Наклон устойчив годами;
- При росте БА — роллируйте длинные Call вверх;
- Только дельта-хеджированные позиции.
""")
    elif _skew_type == "dual":
        st.info("Двойной наклон — IV повышена и на нижних, и на "
                "верхних страйках. Обычно возникает на рынках металлов.")
        st.markdown("""
**Рекомендуемые стратегии:**
- **Короткий стрэддл** на центральном страйке;
- **Продажа Iron Condor** — с защитными крыльями;
- Покупка дешёвых ATM + продажа дорогих OTM на обоих крыльях.
""")
    else:
        st.info("Плоский наклон — статистического преимущества нет. "
                "Работайте по направленным стратегиям или по IV Rank.")

    st.markdown("---")

    with st.expander("Детальная таблица IV по страйкам", expanded=False):
        _df_show_sk = _df_sk.copy()
        _df_show_sk = _df_show_sk.rename(columns={
            "strike": "Страйк",
            "iv_call": "IV Call, %",
            "iv_put": "IV Put, %",
            "iv_avg": "IV сред., %",
        })
        st.dataframe(
            _df_show_sk.style.format({
                "Страйк": "{:.2f}",
                "IV Call, %": "{:.2f}",
                "IV Put, %": "{:.2f}",
                "IV сред., %": "{:.2f}",
            }, na_rep="—"),
            use_container_width=True, hide_index=True,
            height=min(120 + 35 * len(_df_show_sk), 500),
        )

    st.caption("Критерий наличия наклона: σ/μ ≥ 15%.")