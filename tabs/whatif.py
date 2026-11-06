"""Вкладка «What-if симулятор»."""
from datetime import date, datetime, timedelta

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from core.market_data import fetch_rvi_current, _calc_comm_ui
from core.pricing import (
    _get_current_market_price_for_asset,
    _portfolio_value,
    _portfolio_value_with_rvi,
)


@st.fragment
def _render_whatif_tab():
    st.header("What-if симулятор")
    st.caption(
        "Симуляция P&L текущей позиции при изменении цены БА, "
        "волатильности и даты сценария. "
        "Позиция берётся с вкладки «Позиция»."
    )

    _asset = st.session_state.get("pos_asset", "")
    _atype = st.session_state.get("pos_category", "")

    if not _asset or not _atype:
        st.warning("Сначала выберите актив и категорию на вкладке «Позиция».")
        return

    positions = [
        p for p in st.session_state.get("positions", [])
        if p.get("visible", True)
        and (p.get("_asset") or _asset) == _asset
        and (p.get("_atype") or _atype) == _atype
    ]

    if not positions:
        st.info(f"Нет видимых позиций для {_asset} ({_atype}).")
        return

    current_price = _get_current_market_price_for_asset(_asset, _atype)
    if current_price is None:
        current_price = st.session_state.get("_current_market_price")
    if current_price is None:
        st.warning("Не удалось получить текущую цену БА — задайте вручную.")
        current_price = st.number_input(
            "Текущая цена БА (₽)", min_value=0.01, value=100.0,
            step=1.0, format="%.4f", key="whatif_current_price_manual",
        )
    else:
        st.success(f"Текущая цена {_asset}: {current_price:,.4f} ₽")

    _iv_list = [float(p.get("_vol", 0)) for p in positions
                if p.get("_vol") is not None
                and float(p.get("_vol", 0)) > 0]
    base_iv = float(np.mean(_iv_list)) if _iv_list else 20.0

    st.markdown("---")

    _wt_exp_dates = []
    for _p in positions:
        _e = _p.get("Эксп.", "—")
        if _e and _e != "—":
            try:
                _wt_exp_dates.append(
                    datetime.strptime(_e, "%Y-%m-%d").date()
                )
            except Exception:
                pass
    _wt_min_date = date.today()
    _wt_max_exp = (max(_wt_exp_dates) if _wt_exp_dates
                   else (_wt_min_date + timedelta(days=180)))
    _wt_max_date = _wt_max_exp + timedelta(days=30)

    _wt_asset_key = f"{_asset}|{_atype}"
    if st.session_state.get("_wt_last_asset") != _wt_asset_key:
        st.session_state.wt_price_abs = float(current_price)
        st.session_state.wt_price_pct = 0.0
        st.session_state.wt_iv_abs = float(base_iv)
        st.session_state.wt_iv_pp = 0.0
        st.session_state.wt_scenario_date = _wt_min_date
        st.session_state.wt_price_ver = st.session_state.get(
            "wt_price_ver", 0
        ) + 1
        st.session_state.wt_iv_ver = st.session_state.get(
            "wt_iv_ver", 0
        ) + 1
        st.session_state["_wt_last_asset"] = _wt_asset_key

    for _k, _def in [
        ("wt_price_abs", float(current_price)),
        ("wt_price_pct", 0.0),
        ("wt_iv_abs", float(base_iv)),
        ("wt_iv_pp", 0.0),
        ("wt_price_ver", 0),
        ("wt_iv_ver", 0),
    ]:
        if _k not in st.session_state:
            st.session_state[_k] = _def
    if "wt_scenario_date" not in st.session_state:
        st.session_state.wt_scenario_date = _wt_min_date

    _sd = st.session_state.wt_scenario_date
    if _sd < _wt_min_date:
        st.session_state.wt_scenario_date = _wt_min_date
    elif _sd > _wt_max_date:
        st.session_state.wt_scenario_date = _wt_max_date

    _p_ver = st.session_state.wt_price_ver
    _i_ver = st.session_state.wt_iv_ver
    _p_abs_key = f"wt_p_abs_{_p_ver}"
    _p_pct_key = f"wt_p_pct_{_p_ver}"
    _i_abs_key = f"wt_i_abs_{_i_ver}"
    _i_pp_key = f"wt_i_pp_{_i_ver}"

    def _wt_price_reset():
        st.session_state.wt_price_abs = float(current_price)
        st.session_state.wt_price_pct = 0.0
        st.session_state.wt_price_ver += 1

    def _wt_iv_reset():
        st.session_state.wt_iv_abs = float(base_iv)
        st.session_state.wt_iv_pp = 0.0
        st.session_state.wt_iv_ver += 1

    def _wt_date_reset():
        st.session_state.wt_scenario_date = _wt_min_date

    def _wt_reset_all():
        st.session_state.wt_price_abs = float(current_price)
        st.session_state.wt_price_pct = 0.0
        st.session_state.wt_price_ver += 1
        st.session_state.wt_iv_abs = float(base_iv)
        st.session_state.wt_iv_pp = 0.0
        st.session_state.wt_iv_ver += 1
        st.session_state.wt_scenario_date = _wt_min_date

    _hdr1, _hdr2 = st.columns([5, 1])
    with _hdr1:
        st.markdown("### Параметры сценария")
    with _hdr2:
        st.button(
            "Сбросить всё", key="wt_reset_all_btn",
            on_click=_wt_reset_all,
            use_container_width=True,
            help="Сбросить цену, IV и дату",
        )

    st.markdown(
        "<div style='font-size:.75rem; color:#2c506d; "
        "font-weight:700; text-transform:uppercase; "
        "letter-spacing:.03em; margin-bottom:2px;'>Цена БА</div>",
        unsafe_allow_html=True,
    )
    _pc1, _pc2, _pc3 = st.columns([1.6, 3.5, 0.6])
    with _pc1:
        _p_abs_val = st.number_input(
            "Цена, ₽",
            min_value=0.01, max_value=1e9,
            value=float(st.session_state.wt_price_abs),
            step=0.5, format="%.4f",
            key=_p_abs_key,
            label_visibility="collapsed",
        )
    with _pc2:
        _p_pct_val = st.slider(
            "Изменение цены, %",
            min_value=-50.0, max_value=50.0,
            value=float(st.session_state.wt_price_pct),
            step=0.5,
            key=_p_pct_key,
            label_visibility="collapsed",
        )
    with _pc3:
        st.button(
            "↺", key="wt_price_reset_btn",
            on_click=_wt_price_reset,
            use_container_width=True,
            help="Сбросить цену",
        )

    if abs(_p_abs_val - st.session_state.wt_price_abs) > 1e-9:
        st.session_state.wt_price_abs = _p_abs_val
        _pct_calc = ((_p_abs_val / current_price - 1) * 100.0
                     if current_price > 0 else 0.0)
        st.session_state.wt_price_pct = max(-50.0, min(50.0, _pct_calc))
        st.session_state.wt_price_ver += 1
        st.session_state.pop(f"wt_p_abs_{_p_ver - 1}", None)
        st.session_state.pop(f"wt_p_pct_{_p_ver - 1}", None)
        st.rerun()
    elif abs(_p_pct_val - st.session_state.wt_price_pct) > 1e-9:
        st.session_state.wt_price_pct = _p_pct_val
        st.session_state.wt_price_abs = (
            current_price * (1 + _p_pct_val / 100.0)
        )
        st.session_state.wt_price_ver += 1
        st.session_state.pop(f"wt_p_abs_{_p_ver - 1}", None)
        st.session_state.pop(f"wt_p_pct_{_p_ver - 1}", None)
        st.rerun()

    st.markdown(
        "<div style='font-size:.75rem; color:#2c506d; "
        "font-weight:700; text-transform:uppercase; "
        "letter-spacing:.03em; margin-top:6px; margin-bottom:2px;'>"
        "Волатильность (IV)</div>",
        unsafe_allow_html=True,
    )
    _ic1, _ic2, _ic3 = st.columns([1.6, 3.5, 0.6])
    with _ic1:
        _i_abs_val = st.number_input(
            "IV, %",
            min_value=0.1, max_value=500.0,
            value=float(st.session_state.wt_iv_abs),
            step=0.5, format="%.2f",
            key=_i_abs_key,
            label_visibility="collapsed",
        )
    with _ic2:
        _i_pp_val = st.slider(
            "Изменение IV, п.п.",
            min_value=-30.0, max_value=30.0,
            value=float(st.session_state.wt_iv_pp),
            step=0.5,
            key=_i_pp_key,
            label_visibility="collapsed",
        )
    with _ic3:
        st.button(
            "↺", key="wt_iv_reset_btn",
            on_click=_wt_iv_reset,
            use_container_width=True,
            help="Сбросить IV",
        )

    if abs(_i_abs_val - st.session_state.wt_iv_abs) > 1e-9:
        st.session_state.wt_iv_abs = max(0.1, _i_abs_val)
        st.session_state.wt_iv_pp = max(-30.0, min(30.0, _i_abs_val - base_iv))
        st.session_state.wt_iv_ver += 1
        st.session_state.pop(f"wt_i_abs_{_i_ver - 1}", None)
        st.session_state.pop(f"wt_i_pp_{_i_ver - 1}", None)
        st.rerun()
    elif abs(_i_pp_val - st.session_state.wt_iv_pp) > 1e-9:
        st.session_state.wt_iv_pp = _i_pp_val
        st.session_state.wt_iv_abs = max(0.1, base_iv + _i_pp_val)
        st.session_state.wt_iv_ver += 1
        st.session_state.pop(f"wt_i_abs_{_i_ver - 1}", None)
        st.session_state.pop(f"wt_i_pp_{_i_ver - 1}", None)
        st.rerun()

    st.markdown(
        "<div style='font-size:.75rem; color:#2c506d; "
        "font-weight:700; text-transform:uppercase; "
        "letter-spacing:.03em; margin-top:6px; margin-bottom:2px;'>"
        "Дата сценария</div>",
        unsafe_allow_html=True,
    )
    _dc1, _dc2, _dc3 = st.columns([1.6, 3.5, 0.6])
    with _dc1:
        st.date_input(
            "Дата",
            min_value=_wt_min_date,
            max_value=_wt_max_date,
            key="wt_scenario_date",
            label_visibility="collapsed",
        )
    with _dc2:
        _days_delta = (st.session_state.wt_scenario_date - _wt_min_date).days
        if _wt_exp_dates:
            _alive_cnt = sum(1 for _d in _wt_exp_dates
                             if _d > st.session_state.wt_scenario_date)
            _expired_cnt = len(_wt_exp_dates) - _alive_cnt
            st.markdown(
                f"<div style='padding:6px 12px; background:#eef6fb; "
                f"border-radius:8px; font-size:.85rem; color:#1c5a7a; "
                f"border:1px solid #cfdfe9;'>"
                f"<b>{st.session_state.wt_scenario_date.strftime('%d.%m.%Y')}</b> "
                f"(+{_days_delta} дн.) &nbsp;·&nbsp; "
                f"живых опционов: <b style='color:#00a651;'>{_alive_cnt}</b> "
                f"/ истекших: <b style='color:#d32f2f;'>{_expired_cnt}</b>"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<div style='padding:6px 12px; background:#eef6fb; "
                f"border-radius:8px; font-size:.85rem; color:#1c5a7a; "
                f"border:1px solid #cfdfe9;'>"
                f"<b>{st.session_state.wt_scenario_date.strftime('%d.%m.%Y')}</b> "
                f"(+{_days_delta} дн.)"
                f"</div>",
                unsafe_allow_html=True,
            )
    with _dc3:
        st.button(
            "↺", key="wt_date_reset_btn",
            on_click=_wt_date_reset,
            use_container_width=True,
            help="Сбросить дату на сегодня",
        )

    st.markdown("---")
    st.markdown("### Рыночный сценарий (RVI)")

    _rvi_wt_data = fetch_rvi_current()
    _rvi_base = (_rvi_wt_data.get("value")
                 if _rvi_wt_data and _rvi_wt_data.get("value") is not None
                 else 30.0)
    _rvi_base_is_fallback = not (
        _rvi_wt_data and _rvi_wt_data.get("value") is not None
    )

    _wt_rvi_c1, _wt_rvi_c2 = st.columns(2)
    with _wt_rvi_c1:
        _rvi_shift = st.slider(
            "Изменение RVI, п.п.",
            min_value=-30.0, max_value=30.0, value=0.0, step=1.0,
            key="whatif_rvi_shift",
            help="Имитация рыночного стресса: +10 п.п. = паника, "
                 "−10 п.п. = успокоение",
        )
    with _wt_rvi_c2:
        _rvi_correlation = st.slider(
            "Корреляция IV с RVI",
            min_value=0.0, max_value=1.0, value=0.7, step=0.05,
            key="whatif_rvi_correlation",
            help="Насколько сильно IV вашего инструмента следует за RVI. "
                 "0 = независимо, 1 = полностью коррелировано",
        )

    _rvi_scenario = _rvi_base + _rvi_shift

    _rvi_baseline_note = (
        " (fallback, ISS недоступен)" if _rvi_base_is_fallback else ""
    )
    st.caption(
        f"RVI: {_rvi_base:.2f}%{_rvi_baseline_note} → "
        f"сценарий: {_rvi_scenario:.2f}% "
        f"({_rvi_shift:+.1f} п.п.) · "
        f"корреляция IV-RVI: {_rvi_correlation:.2f}"
    )

    st.markdown("---")

    new_price = float(st.session_state.wt_price_abs)
    new_iv = float(st.session_state.wt_iv_abs)
    days_shift = (st.session_state.wt_scenario_date - date.today()).days
    price_shift_pct = ((new_price / current_price - 1) * 100
                       if current_price > 0 else 0.0)
    iv_shift_pp = new_iv - base_iv

    comm_func = _calc_comm_ui

    pnl_now = _portfolio_value(
        positions, np.array([current_price]), 0, base_iv, comm_func
    )[0]
    pnl_new = _portfolio_value(
        positions, np.array([new_price]), days_shift, new_iv, comm_func
    )[0]
    pnl_delta = pnl_new - pnl_now

    _pnl_now_rvi = _portfolio_value_with_rvi(
        positions, np.array([current_price]), 0,
        base_iv, 0.0, _rvi_correlation, comm_func,
    )[0]
    _pnl_new_rvi = _portfolio_value_with_rvi(
        positions, np.array([new_price]), days_shift,
        base_iv, _rvi_shift, _rvi_correlation, comm_func,
    )[0]
    _pnl_delta_rvi = _pnl_new_rvi - _pnl_now_rvi

    st.markdown("### Результат сценария")
    _m1, _m2, _m3, _m4 = st.columns(4)
    with _m1:
        st.metric("Цена БА", f"{new_price:,.4f} ₽",
                  delta=f"{new_price - current_price:+,.4f} ₽")
    with _m2:
        st.metric("IV, %", f"{new_iv:.2f}%",
                  delta=f"{iv_shift_pp:+.2f} п.п.")
    with _m3:
        st.metric("P&L сейчас", f"{pnl_now:+,.2f} ₽")
    with _m4:
        st.metric("P&L по сценарию", f"{pnl_new:+,.2f} ₽",
                  delta=f"{pnl_delta:+,.2f} ₽")

    _rvi_color_delta = "#00a651" if _pnl_delta_rvi >= 0 else "#d32f2f"
    st.markdown(
        f"<div style='background:#f0f4f8; border-radius:10px; "
        f"padding:10px 16px; margin-top:10px; font-size:.9rem; "
        f"display:flex; gap:24px; flex-wrap:wrap; align-items:center;'>"
        f"<span style='font-weight:700;'>С учётом RVI "
        f"({_rvi_shift:+.1f} п.п. × {_rvi_correlation:.2f}):</span>"
        f"<span>P&L по сценарию: <b>{_pnl_new_rvi:+,.2f} ₽</b></span>"
        f"<span style='color:{_rvi_color_delta};'>"
        f"Дельта: <b>{_pnl_delta_rvi:+,.2f} ₽</b></span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    _tot_delta = _tot_gamma = _tot_vega = _tot_theta = 0.0
    for p in positions:
        q = int(p.get("Кол-во", 0))
        _tot_delta += (p.get("Дельта") or 0) * q
        _tot_gamma += (p.get("Гамма") or 0) * q
        _tot_vega += (p.get("Вега") or 0) * q
        _tot_theta += (p.get("Тета") or 0) * q

    _dS = new_price - current_price
    contrib_delta = _tot_delta * _dS
    contrib_vega = _tot_vega * iv_shift_pp
    contrib_theta = _tot_theta * days_shift
    contrib_gamma = 0.5 * _tot_gamma * (_dS ** 2)

    st.markdown("#### Компоненты изменения P&L (первое приближение)")
    _c1, _c2, _c3, _c4 = st.columns(4)
    _c1.metric("От Δ-цены (delta)", f"{contrib_delta:+,.2f} ₽")
    _c2.metric("От Γ (gamma)", f"{contrib_gamma:+,.2f} ₽")
    _c3.metric("От IV (vega)", f"{contrib_vega:+,.2f} ₽")
    _c4.metric("От времени (theta)", f"{contrib_theta:+,.2f} ₽")

    st.markdown("---")

    st.markdown("### Профиль P&L на выбранный сценарий")
    _strikes_wa = sorted({float(p["Страйк"]) for p in positions
                           if p.get("Страйк") is not None})
    if not _strikes_wa:
        _strikes_wa = [current_price]
    _s_min_wa = min(_strikes_wa + [current_price]) * 0.75
    _s_max_wa = max(_strikes_wa + [current_price]) * 1.25
    S_wa = np.linspace(_s_min_wa, _s_max_wa, 400)

    pnl_base_curve = _portfolio_value(
        positions, S_wa, 0, base_iv, comm_func
    )
    pnl_scen_curve = _portfolio_value(
        positions, S_wa, days_shift, new_iv, comm_func
    )
    pnl_rvi_curve = _portfolio_value_with_rvi(
        positions, S_wa, days_shift, base_iv,
        _rvi_shift, _rvi_correlation, comm_func,
    )

    fig_wa = go.Figure()
    fig_wa.add_trace(go.Scatter(
        x=S_wa, y=pnl_base_curve, mode="lines",
        name="Текущий IV и время",
        line=dict(color="#9aa7b3", width=2, dash="dot"),
        hovertemplate="S: %{x:.2f}<br>P&L: %{y:+,.2f} ₽<extra></extra>",
    ))
    fig_wa.add_trace(go.Scatter(
        x=S_wa, y=pnl_scen_curve, mode="lines",
        name=(f"Сценарий (ΔS={price_shift_pct:+.1f}%, "
              f"ΔIV={iv_shift_pp:+.1f}пп, +{days_shift}д)"),
        line=dict(color="#1e5a7a", width=3),
        hovertemplate="S: %{x:.2f}<br>P&L: %{y:+,.2f} ₽<extra></extra>",
    ))
    if abs(_rvi_shift) > 0.01:
        fig_wa.add_trace(go.Scatter(
            x=S_wa, y=pnl_rvi_curve, mode="lines",
            name=(f"RVI-сценарий (ΔRVI={_rvi_shift:+.1f}пп, "
                  f"corr={_rvi_correlation:.2f})"),
            line=dict(color="#9c27b0", width=2, dash="dash"),
            hovertemplate="S: %{x:.2f}<br>P&L: %{y:+,.2f} ₽<extra></extra>",
        ))
    fig_wa.add_hline(y=0, line_dash="dot", line_color="#7f9bb3",
                     line_width=1)
    fig_wa.add_vline(x=current_price, line_dash="dash",
                     line_color="#1e88e5", line_width=1.5,
                     annotation_text=f"Тек. {current_price:.2f}",
                     annotation_position="top")
    fig_wa.add_vline(x=new_price, line_dash="dash",
                     line_color="#00a651", line_width=1.5,
                     annotation_text=f"Сцен. {new_price:.2f}",
                     annotation_position="bottom")
    fig_wa.add_trace(go.Scatter(
        x=[current_price], y=[pnl_now], mode="markers",
        marker=dict(size=14, color="#1e88e5", symbol="circle",
                    line=dict(color="white", width=2)),
        name="Сейчас",
        hovertemplate=("Сейчас<br>S: %{x:.2f}<br>"
                       "P&L: %{y:+,.2f} ₽<extra></extra>"),
    ))
    fig_wa.add_trace(go.Scatter(
        x=[new_price], y=[pnl_new], mode="markers",
        marker=dict(size=14, color="#00a651", symbol="diamond",
                    line=dict(color="white", width=2)),
        name="По сценарию",
        hovertemplate=("Сценарий<br>S: %{x:.2f}<br>"
                       "P&L: %{y:+,.2f} ₽<extra></extra>"),
    ))

    fig_wa.update_layout(
        title=f"P&L позиции — {_asset}",
        xaxis_title="Цена БА, ₽", yaxis_title="P&L, ₽",
        height=460, margin=dict(l=20, r=20, t=60, b=20),
        legend=dict(orientation="h", yanchor="bottom",
                    y=1.02, xanchor="left", x=0),
        hovermode="x unified",
    )
    st.plotly_chart(fig_wa, use_container_width=True)

    st.markdown("---")

    st.markdown("### Тепловая карта P&L (цена БА × IV)")
    _h_c1, _h_c2 = st.columns(2)
    with _h_c1:
        _grid_pct = st.slider("Диапазон цены ±, %", 5, 50, 20, 5,
                              key="whatif_grid_pct")
    with _h_c2:
        _grid_iv = st.slider("Диапазон IV ±, п.п.", 2, 30, 10, 1,
                              key="whatif_grid_iv")

    S_grid = np.linspace(
        current_price * (1 - _grid_pct / 100),
        current_price * (1 + _grid_pct / 100), 41,
    )
    IV_grid = np.linspace(max(0.5, base_iv - _grid_iv),
                          base_iv + _grid_iv, 41)

    Z = np.zeros((len(IV_grid), len(S_grid)))
    for _i, _ivv in enumerate(IV_grid):
        Z[_i, :] = _portfolio_value(
            positions, S_grid, days_shift, _ivv, comm_func
        )

    fig_hm = go.Figure(data=go.Heatmap(
        x=S_grid, y=IV_grid, z=Z,
        colorscale=[
            [0.0, "#7b0d0d"], [0.35, "#d32f2f"],
            [0.5, "#ffffff"],
            [0.65, "#00a651"], [1.0, "#064d23"],
        ],
        zmid=0,
        colorbar=dict(title="P&L, ₽"),
        hovertemplate=("Цена: %{x:.2f} ₽<br>IV: %{y:.2f}%<br>"
                       "P&L: %{z:+,.2f} ₽<extra></extra>"),
    ))
    fig_hm.add_trace(go.Scatter(
        x=[current_price], y=[base_iv], mode="markers",
        marker=dict(size=14, color="#1e88e5", symbol="x",
                    line=dict(color="white", width=2)),
        name="Сейчас",
        hovertemplate="Сейчас<br>Цена: %{x:.2f}<br>IV: %{y:.2f}%<extra></extra>",
    ))
    if price_shift_pct != 0 or iv_shift_pp != 0:
        fig_hm.add_trace(go.Scatter(
            x=[new_price], y=[new_iv], mode="markers",
            marker=dict(size=14, color="#00a651", symbol="diamond",
                        line=dict(color="white", width=2)),
            name="Сценарий",
            hovertemplate=("Сценарий<br>Цена: %{x:.2f}<br>"
                           "IV: %{y:.2f}%<extra></extra>"),
        ))
    fig_hm.update_layout(
        title=f"P&L позиции — {_asset} (+{days_shift} дней)",
        xaxis_title="Цена БА, ₽", yaxis_title="IV, %",
        height=520, margin=dict(l=20, r=20, t=60, b=20),
        legend=dict(orientation="h", yanchor="bottom",
                    y=1.02, xanchor="left", x=0),
    )
    st.plotly_chart(fig_hm, use_container_width=True)

    st.caption("Тепловая карта показывает P&L по всей комбинации "
               "«цена × IV» при фиксированном сдвиге времени.")