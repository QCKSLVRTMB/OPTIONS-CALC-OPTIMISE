"""Вкладка «Heatmap IV»."""
from datetime import date, datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.market_data import classify_iv_vs_rvi, fetch_rvi_current
from core.options import fetch_optionseries, fetch_optionboard
from core.pricing import _get_current_market_price_for_asset
from core.helpers import _fmt_strike


@st.fragment
def _render_heatmap_tab():
    st.header("Heatmap IV по сериям и страйкам")
    st.caption(
        "Тепловая карта подразумеваемой волатильности по всем "
        "доступным сериям и страйкам выбранного БА. "
        "Помогает увидеть структуру срочной волатильности "
        "(term structure) и улыбку."
    )

    _hm_asset = st.session_state.get("pos_asset", "")
    _hm_cat = st.session_state.get("pos_category", "")

    if not _hm_asset:
        st.warning("Сначала выберите актив и категорию на вкладке «Позиция».")
        return

    _hm1, _hm2 = st.columns([4, 2])
    with _hm1:
        st.info(f"Актив: {_hm_asset} · Категория: {_hm_cat} "
                f"(из вкладки «Позиция»)")
    with _hm2:
        _hm_build = st.button(
            "Построить Heatmap",
            type="primary",
            use_container_width=True,
            key="hm_build_btn",
        )

    _hm_cache_key_base = f"_hm_cache_{_hm_asset}_{_hm_cat}"

    if not _hm_build and _hm_cache_key_base not in st.session_state:
        st.info("Нажмите «Построить Heatmap» для загрузки данных "
                "(может занять 10–30 секунд).")
        return

    _cache_key = _hm_cache_key_base

    if _hm_build or _cache_key not in st.session_state:
        with st.spinner(f"Загрузка серий и досок для {_hm_asset}…"):
            try:
                _series_all = fetch_optionseries(_hm_asset, _hm_cat) or []
            except Exception as e:
                st.error(f"Не удалось получить серии: {e}")
                return
            if not _series_all:
                st.warning(f"ISS не вернул серий для {_hm_asset}")
                return

            _series_sorted = sorted(
                _series_all, key=lambda x: x.get("expiry", "")
            )[:12]

            _hm_rows = []
            _progress = st.progress(0.0, text="Загрузка досок…")
            _n = len(_series_sorted)
            _today = date.today()
            for _i, _s in enumerate(_series_sorted):
                try:
                    _b = fetch_optionboard(_hm_asset, _hm_cat, _s["code"])
                except Exception:
                    _b = None
                if _b:
                    try:
                        _d = datetime.strptime(
                            _s["expiry"], "%Y-%m-%d"
                        ).date()
                        _days = (_d - _today).days
                    except Exception:
                        _days = None
                    _seen_strikes = {}
                    for _side_key in ("call", "put"):
                        for _row in (_b.get(_side_key) or []):
                            _k = _row.get("strike")
                            _iv = _row.get("volatility")
                            if _k is None or _iv is None:
                                continue
                            try:
                                _kf = float(_k)
                                _ivf = float(_iv)
                            except (TypeError, ValueError):
                                continue
                            if not (0 < _ivf < 500):
                                continue
                            if _kf in _seen_strikes:
                                _seen_strikes[_kf] = (
                                    _seen_strikes[_kf] + _ivf
                                ) / 2.0
                            else:
                                _seen_strikes[_kf] = _ivf
                    for _kf, _ivf in _seen_strikes.items():
                        _hm_rows.append({
                            "expiry": _s["expiry"],
                            "days_to": _days,
                            "strike": _kf,
                            "iv": _ivf,
                        })
                _progress.progress((_i + 1) / _n,
                                    text=f"Серия {_i+1}/{_n}…")
            _progress.empty()

            st.session_state[_cache_key] = _hm_rows
            st.session_state[f"{_cache_key}_series"] = _series_sorted

    _hm_rows = st.session_state.get(_cache_key, [])
    _series_sorted = st.session_state.get(f"{_cache_key}_series", [])

    if not _hm_rows:
        st.warning("Не удалось получить данные IV ни по одной серии.")
        return

    _df_hm = pd.DataFrame(_hm_rows)

    _current_price_hm = _get_current_market_price_for_asset(
        _hm_asset, _hm_cat
    )

    st.markdown("---")
    _oc1, _oc2, _oc3 = st.columns(3)
    with _oc1:
        _limit_strikes_around = st.slider(
            "Страйков вокруг центрального (0 = все)",
            min_value=0, max_value=30, value=12, step=1,
            key="hm_limit_strikes",
            help="Ограничить диапазон страйков вокруг текущей цены БА.",
        )
    with _oc2:
        _show_values = st.toggle(
            "Показывать значения IV в ячейках", value=True,
            key="hm_show_values",
        )
    with _oc3:
        _hm_color_scale = st.selectbox(
            "Цветовая шкала",
            ["RdYlGn_r", "Viridis", "Cividis", "Turbo", "Plasma"],
            index=0, key="hm_colorscale",
        )

    if _limit_strikes_around > 0 and _current_price_hm is not None:
        _all_strikes_hm = sorted(_df_hm["strike"].unique())
        _nearest_idx = min(
            range(len(_all_strikes_hm)),
            key=lambda i: abs(_all_strikes_hm[i] - _current_price_hm),
        )
        _lo = max(0, _nearest_idx - _limit_strikes_around)
        _hi = min(len(_all_strikes_hm),
                  _nearest_idx + _limit_strikes_around + 1)
        _strikes_keep = _all_strikes_hm[_lo:_hi]
        _df_hm = _df_hm[_df_hm["strike"].isin(_strikes_keep)]

    if _df_hm.empty:
        st.warning("После фильтрации не осталось данных.")
        return

    _expiries_sorted = sorted(_df_hm["expiry"].unique())
    _strikes_sorted = sorted(_df_hm["strike"].unique())

    _Z = np.full((len(_expiries_sorted), len(_strikes_sorted)), np.nan)
    for _i, _exp in enumerate(_expiries_sorted):
        _sub = _df_hm[_df_hm["expiry"] == _exp]
        for _j, _k in enumerate(_strikes_sorted):
            _v = _sub.loc[_sub["strike"] == _k, "iv"]
            if not _v.empty:
                _Z[_i, _j] = float(_v.iloc[0])

    _exp_labels = []
    _today = date.today()
    for _e in _expiries_sorted:
        try:
            _d = datetime.strptime(_e, "%Y-%m-%d").date()
            _dd = (_d - _today).days
            _exp_labels.append(f"{_d.strftime('%d.%m.%y')} ({_dd})")
        except Exception:
            _exp_labels.append(str(_e))

    _strike_labels = [_fmt_strike(k, _hm_cat) for k in _strikes_sorted]

    _hover_text = []
    for _i, _exp in enumerate(_expiries_sorted):
        _row_hover = []
        for _j, _k in enumerate(_strikes_sorted):
            _v = _Z[_i, _j]
            _v_str = f"{_v:.2f}%" if not np.isnan(_v) else "—"
            _row_hover.append(
                f"Серия: {_exp_labels[_i]}<br>"
                f"Страйк: {_strike_labels[_j]}<br>"
                f"IV: {_v_str}"
            )
        _hover_text.append(_row_hover)

    fig_hm = go.Figure(data=go.Heatmap(
        z=_Z,
        x=_strike_labels,
        y=_exp_labels,
        colorscale=_hm_color_scale,
        hovertemplate="%{text}<extra></extra>",
        text=_hover_text,
        colorbar=dict(title="IV, %"),
        zmin=float(np.nanmin(_Z)) if not np.all(np.isnan(_Z)) else None,
        zmax=float(np.nanmax(_Z)) if not np.all(np.isnan(_Z)) else None,
    ))

    if _show_values:
        _annotations = []
        for _i in range(len(_expiries_sorted)):
            for _j in range(len(_strikes_sorted)):
                _v = _Z[_i, _j]
                if np.isnan(_v):
                    _txt = "—"
                else:
                    _txt = f"{_v:.1f}"
                _annotations.append(dict(
                    x=_strike_labels[_j], y=_exp_labels[_i],
                    text=_txt, showarrow=False,
                    font=dict(size=10, color="#111"),
                ))
        fig_hm.update_layout(annotations=_annotations)

    if _current_price_hm is not None:
        try:
            _nearest_strike_label = min(
                _strike_labels,
                key=lambda s: abs(float(s) - _current_price_hm),
            )
        except Exception:
            _nearest_strike_label = None

        if _nearest_strike_label is not None:
            fig_hm.add_shape(
                type="line",
                xref="x", yref="paper",
                x0=_nearest_strike_label, x1=_nearest_strike_label,
                y0=0, y1=1,
                line=dict(color="#1e88e5", width=2, dash="dash"),
                layer="above",
            )
            fig_hm.add_annotation(
                x=_nearest_strike_label, y=1,
                xref="x", yref="paper",
                text=f"БА≈{_current_price_hm:.2f}",
                showarrow=False,
                yshift=12,
                font=dict(color="#1e88e5", size=11,
                          family="Arial Black"),
                bgcolor="rgba(255,255,255,0.85)",
                bordercolor="#1e88e5", borderwidth=1,
            )
    _fig_h = max(400, 40 + 42 * len(_expiries_sorted))
    fig_hm.update_layout(
        title=f"Heatmap IV — {_hm_asset} ({_hm_cat})",
        xaxis_title="Страйк",
        yaxis_title="Серия (дата исп.)",
        height=_fig_h,
        margin=dict(l=20, r=20, t=60, b=20),
    )
    st.plotly_chart(fig_hm, use_container_width=True)

    _rvi_hm_data = fetch_rvi_current()
    _rvi_hm = _rvi_hm_data.get("value") if _rvi_hm_data else None

    if _rvi_hm is not None and not _df_hm.empty:
        _hm_avg_iv = float(_df_hm["iv"].mean())
        _hm_ratio = _hm_avg_iv / _rvi_hm if _rvi_hm > 0 else None
        _hm_zone = classify_iv_vs_rvi(_hm_avg_iv, _rvi_hm)

        _hm_zone_label = _hm_zone["zone_label"] if _hm_zone else "—"
        _hm_zone_color = _hm_zone["zone_color"] if _hm_zone else "#7a7a7a"

        _ratio_str = f"{_hm_ratio:.2f}×" if _hm_ratio is not None else "—"
        st.markdown(
            f"<div style='background:#f0f4f8; border-radius:8px; "
            f"padding:10px 14px; margin:10px 0; font-size:.9rem; "
            f"display:flex; gap:24px; flex-wrap:wrap; align-items:center;'>"
            f"<span><b>RVI:</b> {_rvi_hm:.2f}%</span>"
            f"<span><b>Средняя IV на доске:</b> {_hm_avg_iv:.2f}%</span>"
            f"<span><b>Ratio:</b> {_ratio_str}</span>"
            f"<span style='color:{_hm_zone_color}; font-weight:700;'>"
            f"{_hm_zone_label}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.markdown("### Сводные метрики по сериям")

    _summary_rows = []
    for _exp in _expiries_sorted:
        _sub = _df_hm[_df_hm["expiry"] == _exp]
        _iv_min = _sub["iv"].min() if not _sub.empty else np.nan
        _iv_max = _sub["iv"].max() if not _sub.empty else np.nan
        _iv_mean = _sub["iv"].mean() if not _sub.empty else np.nan
        _iv_std = _sub["iv"].std() if len(_sub) > 1 else 0.0
        try:
            _d = datetime.strptime(_exp, "%Y-%m-%d").date()
            _dd = (_d - _today).days
        except Exception:
            _dd = None
        _summary_rows.append({
            "Серия": _exp,
            "До эксп., дн.": _dd,
            "IV min, %": (round(_iv_min, 2)
                          if not np.isnan(_iv_min) else None),
            "IV max, %": (round(_iv_max, 2)
                          if not np.isnan(_iv_max) else None),
            "IV сред., %": (round(_iv_mean, 2)
                            if not np.isnan(_iv_mean) else None),
            "σ IV": round(_iv_std, 2),
            "Страйков": len(_sub),
        })

    _df_summary = pd.DataFrame(_summary_rows)
    st.dataframe(
        _df_summary, use_container_width=True, hide_index=True,
        height=min(120 + 35 * len(_df_summary), 400),
    )

    st.caption("Как читать: строки — серии (чем ниже, тем дальше "
               "экспирация), столбцы — страйки. Вертикальный градиент "
               "показывает term structure (срочную структуру) IV; "
               "горизонтальный — улыбку волатильности внутри серии.")