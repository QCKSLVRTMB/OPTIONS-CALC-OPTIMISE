"""Вкладка «Vol Analysis» — метрики волатильности по книге Коннолли.

Изолированный файл: не трогает board.py, alert_card.py, styles.py.
Работает с базовым активом и категорией из вкладки «Позиция»
(аналогично Heatmap IV / IV Rank / Skew).

Источники параметров:
  • актив/категория      — st.session_state['pos_asset'], ['pos_category']
  • серия по умолчанию   — st.session_state['pos_series_code']
  • IV                   — поле volatility на доске опционов (или ручное)
  • ставка, дивиденды    — core.strategies._compute_r_q_for_leg
                            (G-кривая ОФЗ + SmartLab; или ручное)
"""
from datetime import date, datetime

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from core.volatility import (
    bs_greeks,
    long_or_short_volatility,
    break_even_daily_move,
    sigma_range_forecast,
    put_call_parity_deviation,
    aggregate_portfolio_greeks,
)
from core.pricing import _get_current_market_price_for_asset
from core.options import fetch_optionboard, fetch_optionseries
from core.market_data import format_series_label
from core.strategies import _compute_r_q_for_leg


@st.fragment
def _render_vol_analysis_tab():
    st.header("Vol Analysis — теория Коннолли")
    st.caption(
        "К. Коннолли, «Покупка и продажа волатильности». "
        "Прибыль — от Γ-рехеджа, не от направления цены."
    )

    # =================================================================
    # 1. Привязка к активу (как в Heatmap IV / IV Rank / Skew)
    # =================================================================
    _va_asset = st.session_state.get("pos_asset", "")
    _va_cat = st.session_state.get("pos_category", "")

    if not _va_asset:
        st.warning("Сначала выберите актив и категорию на вкладке «Позиция».")
        return

    st.info(
        f"Актив: **{_va_asset}** · Категория: **{_va_cat}** "
        f"(из вкладки «Позиция»)"
    )

    # =================================================================
    # 2. Селектор опционной серии (дефолт — серия с вкладки «Позиция»)
    # =================================================================
    try:
        _series_all = fetch_optionseries(_va_asset, _va_cat) or []
    except Exception as _e:
        st.error(f"Не удалось получить серии: {_e}")
        return

    if not _series_all:
        st.warning(f"ISS не вернул серий для {_va_asset} ({_va_cat}).")
        return

    _series_sorted = sorted(_series_all, key=lambda x: x.get("expiry", ""))
    _series_labels = [format_series_label(s["expiry"]) for s in _series_sorted]
    _series_by_label = dict(zip(_series_labels, _series_sorted))

    _pos_code = st.session_state.get("pos_series_code", "")
    _default_idx = 0
    for _i, _s in enumerate(_series_sorted):
        if _s.get("code") == _pos_code:
            _default_idx = _i
            break

    _chosen_label = st.selectbox(
        "Опционная серия", _series_labels,
        index=_default_idx, key="va_series_select",
    )
    _chosen = _series_by_label[_chosen_label]
    _series_code = _chosen["code"]
    _expiry = _chosen["expiry"]

    # =================================================================
    # 3. Загрузка доски
    # =================================================================
    with st.spinner("Загрузка доски опционов…"):
        try:
            _board = fetch_optionboard(_va_asset, _va_cat, _series_code)
        except Exception as _e:
            st.error(f"Не удалось загрузить доску: {_e}")
            return

    if not _board:
        st.warning("Доска опционов пуста.")
        return

    _calls = _board.get("call") or []
    _puts = _board.get("put") or []
    _c_map = {
        float(c["strike"]): c for c in _calls
        if c.get("strike") is not None
    }
    _p_map = {
        float(p["strike"]): p for p in _puts
        if p.get("strike") is not None
    }
    _all_strikes = sorted(set(_c_map) | set(_p_map))
    _central = _board.get("central_strike")

    if not _all_strikes:
        st.warning("На доске нет страйков.")
        return

    _spot = _get_current_market_price_for_asset(_va_asset, _va_cat)

    # =================================================================
    # 4. Параметры расчёта (общие для под-вкладок)
    # =================================================================
    st.markdown("### Параметры расчёта")

    _pc1, _pc2, _pc3 = st.columns(3)

    with _pc1:
        _opt_type = st.selectbox(
            "Тип опциона", ["Call", "Put"], key="va_opt_type",
        )

    with _pc2:
        _strike_default_idx = 0
        if _central is not None:
            try:
                _strike_default_idx = _all_strikes.index(
                    min(_all_strikes, key=lambda s: abs(s - float(_central)))
                )
            except (ValueError, TypeError):
                _strike_default_idx = 0
        elif _spot is not None:
            try:
                _strike_default_idx = _all_strikes.index(
                    min(_all_strikes, key=lambda s: abs(s - float(_spot)))
                )
            except (ValueError, TypeError):
                _strike_default_idx = 0

        _strike = st.selectbox(
            "Страйк", _all_strikes,
            index=_strike_default_idx, key="va_strike",
            format_func=lambda x: f"{x:g}",
        )

    with _pc3:
        st.metric("Спот БА", f"{_spot:.2f} ₽" if _spot else "—")

    # --- IV из доски (auto) ---
    _iv_auto = None
    if _opt_type == "Call" and _strike in _c_map:
        _iv_auto = _c_map[_strike].get("volatility")
    elif _opt_type == "Put" and _strike in _p_map:
        _iv_auto = _p_map[_strike].get("volatility")

    if _iv_auto is None:
        _ivs = [
            _src.get("volatility") for _src in
            (_c_map.get(_strike, {}), _p_map.get(_strike, {}))
            if _src.get("volatility") is not None
        ]
        if _ivs:
            _iv_auto = float(np.mean([float(x) for x in _ivs]))
    if _iv_auto is None:
        _iv_auto = 20.0
    _iv_auto = float(_iv_auto)

    # --- Ставка и дивиденды из G-кривой / SmartLab ---
    _r_auto, _q_auto = _compute_r_q_for_leg(_va_asset, _va_cat, _expiry)

    # Контексты для автосброса полей
    _ctx_iv = f"{_series_code}|{_strike}|{_opt_type}"
    _ctx_rq = f"{_va_asset}|{_va_cat}|{_series_code}"

    if _ctx_iv != st.session_state.get("va_iv_ctx"):
        st.session_state["va_iv_input"] = _iv_auto
        st.session_state["va_iv_ctx"] = _ctx_iv

    if _ctx_rq != st.session_state.get("va_r_ctx"):
        st.session_state["va_r_input"] = float(_r_auto)
        st.session_state["va_r_ctx"] = _ctx_rq

    if _ctx_rq != st.session_state.get("va_q_ctx"):
        st.session_state["va_q_input"] = float(_q_auto)
        st.session_state["va_q_ctx"] = _ctx_rq

    _pc4, _pc5, _pc6 = st.columns(3)

    with _pc4:
        _iv_val = st.number_input(
            "IV, %", min_value=0.1, max_value=500.0,
            step=0.1, format="%.2f", key="va_iv_input",
        )
        st.caption(
            f"IV из доски: **{_iv_auto:.2f}%** "
            f"({_opt_type} {_strike:g})"
        )

    with _pc5:
        _r_val = st.number_input(
            "Ставка, %", min_value=-50.0, max_value=100.0,
            step=0.1, format="%.4f", key="va_r_input",
        )
        st.caption(f"G-кривая ОФЗ: **{float(_r_auto):.4f}%**")

    with _pc6:
        _q_val = st.number_input(
            "Дивдоходность, %", min_value=0.0, max_value=100.0,
            step=0.1, format="%.4f", key="va_q_input",
        )
        st.caption(f"SmartLab: **{float(_q_auto):.4f}%**")

    # =================================================================
    # 5. Snapshot портфеля (не зависит от параметров выше)
    # =================================================================
    _render_snapshot(st.session_state.get("positions", []))

    # =================================================================
    # 6. Под-вкладки
    # =================================================================
    _tabs = st.tabs([
        "Карта дельты",
        "Best / Worst case",
        "Симулятор рехеджа",
        "Паритет пут-колл",
        "σ-диапазон",
    ])

    with _tabs[0]:
        _render_delta_map(_strike, _iv_val, _r_val, _opt_type, _spot, _expiry)

    with _tabs[1]:
        _render_best_worst_case(_strike, _iv_val, _r_val, _q_val,
                                 _opt_type, _spot, _expiry)

    with _tabs[2]:
        _render_rehedge_sim(_strike, _iv_val, _r_val, _spot, _expiry)

    with _tabs[3]:
        _render_put_call_parity(_c_map, _p_map, _spot,
                                 _r_val, _q_val, _expiry, _strike)

    with _tabs[4]:
        _render_sigma_range(_iv_val, _spot, _expiry)


# =====================================================================
# Snapshot портфеля (гл. 4.3, 4.7, 5.2)
# =====================================================================
def _render_snapshot(positions):
    if not positions:
        return

    agg = aggregate_portfolio_greeks(positions)
    if not any(agg.values()):
        return

    kind = long_or_short_volatility(agg["gamma"])
    be = break_even_daily_move(agg["theta"], agg["gamma"])

    st.markdown("### Снимок портфеля по волатильности")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Δ (акций)", f"{agg['delta']:+.0f}")
    c2.metric("Γ", f"{agg['gamma']:+.4f}")
    c3.metric("Θ, ₽/день", f"{agg['theta']:+.2f}")
    c4.metric("Vega, ₽/+1%", f"{agg['vega']:+.2f}")
    c5.metric("Позиция по vol", kind)

    if be is not None:
        st.info(
            f"**Break-even daily move**: {be:.2f} ₽ за день. "
            f"Если БА ходит меньше — Θ съест Γ."
        )
    elif kind == "нейтральная":
        st.info("Позиция Γ-нейтральна — break-even не определён.")
    else:
        st.warning(
            "Θ и Γ имеют одинаковый знак — позиция «сложная» "
            "(возможно, у экспирации); break-even не определён."
        )


# =====================================================================
# 1. Карта дельты (гл. 4.9)
# =====================================================================
def _render_delta_map(K, sigma, r_pct, opt_type, spot, expiry_str):
    st.markdown("### Карта дельты (Fig 4.12)")
    st.caption(
        "Каждая линия — уровень Δ. Пересечение линии = сигнал к "
        "рехеджу, если торгуете с шагом Δ. Контуры сходятся к цене "
        "исполнения у экспирации — там рехедж максимально частый."
    )

    if spot is None:
        st.warning("Нет цены БА — карта не построена.")
        return
    if K is None or K <= 0 or sigma <= 0:
        st.warning("Некорректные параметры (страйк или IV).")
        return

    weeks = np.arange(0, 53)
    spots = np.linspace(float(K) * 0.6, float(K) * 1.4, 41)
    Z = np.zeros((len(weeks), len(spots)))
    for i, w in enumerate(weeks):
        T = max((52 - w) / 52.0, 1e-6)
        for j, S in enumerate(spots):
            Z[i, j] = bs_greeks(
                S, float(K), r_pct, 0.0, sigma, T,
                "C" if opt_type == "Call" else "P",
            ).delta

    lo, hi = (-1.0, 0.0) if opt_type == "Put" else (0.0, 1.0)
    fig = go.Figure(go.Contour(
        x=spots, y=weeks, z=Z,
        contours=dict(showlabels=True, start=lo, end=hi, size=0.1),
        colorscale="RdBu", reversescale=True,
        colorbar=dict(title="Δ"),
    ))
    fig.add_vline(x=float(spot), line_color="#1e88e5", line_dash="dash",
                  annotation_text=f"Спот {spot:.2f}")
    fig.add_vline(x=float(K), line_color="#333", line_dash="dot",
                  annotation_text=f"Страйк {K:g}")
    fig.update_layout(
        xaxis_title="Цена БА", yaxis_title="Недель до экспирации",
        height=500, margin=dict(l=20, r=20, t=40, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)


# =====================================================================
# 2. Best / Worst case (гл. 4.13, 5.6)
# =====================================================================
def _render_best_worst_case(K, sigma, r_pct, q_pct,
                              opt_type, spot, expiry_str):
    st.markdown("### Best / Worst case (Fig 4.16, 5.6)")
    st.caption(
        "Buy-and-hold профиль. Худший случай — цена «прилипла» к "
        "страйку. Лучший — сильное движение в любую сторону."
    )

    if spot is None:
        st.warning("Нет цены БА.")
        return
    if K is None or K <= 0 or sigma <= 0:
        st.warning("Некорректные параметры (страйк или IV).")
        return

    T = 1.0
    if expiry_str:
        try:
            _exp = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            T = max((_exp - date.today()).days / 365.0, 1e-6)
        except Exception:
            pass

    grid = np.linspace(float(K) * 0.5, float(K) * 1.5, 121)

    if opt_type == "Call":
        intrinsic = np.maximum(grid - float(K), 0)
        C0 = bs_greeks(float(spot), float(K), r_pct, q_pct,
                        sigma, T, "C").price
    else:
        intrinsic = np.maximum(float(K) - grid, 0)
        C0 = bs_greeks(float(spot), float(K), r_pct, q_pct,
                        sigma, T, "P").price

    pnl = intrinsic - C0

    fig = go.Figure(go.Scatter(
        x=grid, y=pnl, mode="lines",
        line=dict(color="#1e5a7a", width=2),
        name=f"Long {opt_type} (купил-держи)",
    ))
    fig.add_hline(y=0, line_dash="dot", line_color="grey")
    fig.add_vline(x=float(K), line_dash="dot", line_color="grey",
                  annotation_text=f"страйк {K:g}")
    fig.add_vline(x=float(spot), line_color="#1e88e5", line_dash="dash",
                  annotation_text=f"спот {spot:.2f}")
    fig.update_layout(
        xaxis_title="Цена БА на экспирации, ₽",
        yaxis_title="P&L, ₽ за 1 ед.",
        height=400, margin=dict(l=20, r=20, t=40, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        f"Заплаченная премия {opt_type}: **{C0:.4f}** ₽ "
        f"(σ={sigma:.2f}%, T={T*365:.0f} дн., r={r_pct:.2f}%, "
        f"q={q_pct:.2f}%)."
    )


# =====================================================================
# 3. Симулятор рехеджа (гл. 4.10)
# =====================================================================
def _render_rehedge_sim(K, sigma_impl, r_pct, spot, expiry_str):
    st.markdown("### Симулятор рехеджирования (Fig 4.10)")
    st.caption(
        "Дельта-нейтральная long-vol позиция: купили колл, продали "
        "Δ·100 акций. Прибыль = Γ-рехедж − Θ-распад. Если "
        "реализованная vol > заплаченной IV → прибыль."
    )

    if spot is None:
        st.warning("Нет цены БА.")
        return
    if K is None or K <= 0 or sigma_impl <= 0:
        st.warning("Некорректные параметры (страйк или IV).")
        return

    T_years = 1.0
    if expiry_str:
        try:
            _exp = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            T_years = max((_exp - date.today()).days / 365.0, 1e-6)
        except Exception:
            pass

    c1, c2, c3 = st.columns(3)
    with c1:
        sigma_real = st.number_input(
            "Реализованная vol, %", value=float(sigma_impl),
            min_value=1.0, max_value=200.0, step=1.0,
            key="va_rs_sr",
        )
    with c2:
        band = st.slider(
            "Шаг рехеджа, Δ", 0.02, 0.30, 0.10, 0.02, key="va_rs_band",
        )
    with c3:
        n_paths = st.selectbox(
            "Путей", [200, 500, 1000], index=1, key="va_rs_np",
        )

    st.caption(
        f"Горизонт: **{T_years*365:.0f} дн.** · "
        f"страйк {K:g} · заплаченная IV {sigma_impl:.2f}%"
    )

    if not st.button("Симулировать", type="primary", key="va_rs_run"):
        return

    with st.spinner("Симуляция…"):
        pnl = _mc_rehedge_pnl(
            float(spot), float(K), float(sigma_impl), float(sigma_real),
            int(n_paths), float(band), float(T_years), r_pct,
        )

    fig = go.Figure(go.Histogram(x=pnl, nbinsx=60,
                                  marker_color="steelblue"))
    fig.add_vline(x=0, line_color="red", line_dash="dash")
    fig.add_vline(x=float(np.mean(pnl)), line_color="green",
                  annotation_text=f"среднее {np.mean(pnl):.2f}")
    fig.update_layout(
        xaxis_title="P&L за 1 контракт (100 акций), ₽",
        yaxis_title="Путей", height=400,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Среднее", f"{np.mean(pnl):.2f}")
    m2.metric("Std", f"{np.std(pnl):.2f}")
    m3.metric("5% квантиль", f"{np.quantile(pnl, 0.05):.2f}")
    m4.metric("95% квантиль", f"{np.quantile(pnl, 0.95):.2f}")

    if sigma_real > sigma_impl:
        st.success("Реализованная vol > заплаченной IV → в среднем прибыль.")
    elif sigma_real < sigma_impl:
        st.warning("Реализованная vol < заплаченной IV → в среднем убыток.")
    else:
        st.info("Реализованная vol = заплаченной IV → безубыток.")


def _mc_rehedge_pnl(S0, K, sigma_impl, sigma_real,
                     n_paths, band, T_years, r_pct):
    rng = np.random.default_rng(42)
    n_steps = max(int(252 * T_years), 20)
    dt = T_years / n_steps
    C0 = bs_greeks(S0, K, r_pct, 0.0, sigma_impl, T_years, "C").price
    sig_r = sigma_real / 100.0

    out = np.zeros(n_paths)
    for p in range(n_paths):
        S = S0
        d = bs_greeks(S, K, r_pct, 0.0, sigma_impl, T_years, "C").delta
        stock = -d
        cash = -C0 + d * S
        for step in range(1, n_steps + 1):
            S_new = S * np.exp(
                -0.5 * sig_r ** 2 * dt
                + sig_r * np.sqrt(dt) * rng.standard_normal()
            )
            tau = max(T_years - step * dt, 1e-9)
            d_new = bs_greeks(S_new, K, r_pct, 0.0, sigma_impl,
                               tau, "C").delta
            if abs(d_new - d) >= band and step < n_steps:
                cash += (d_new - d) * S_new
                stock = -d_new
                d = d_new
            S = S_new
        out[p] = (cash + stock * S + max(S - K, 0.0)) * 100
    return out


# =====================================================================
# 4. Паритет пут-колл (гл. 6.5) — на данных доски, не на board.py
# =====================================================================
def _render_put_call_parity(c_map, p_map, spot, r_pct, q_pct,
                              expiry_str, current_strike):
    st.markdown("### Паритет пут-колл (гл. 6.5)")
    st.caption(
        "C − P = S·e^(−qT) − K·e^(−rT). Отклонение > 0 — либо арбитраж, "
        "либо устаревшие данные. Для MOEX — чаще второе."
    )

    if spot is None:
        st.warning("Нет цены БА.")
        return

    if expiry_str:
        try:
            _exp = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            T = max((_exp - date.today()).days / 365.0, 1e-6)
        except Exception:
            T = 1.0 / 12.0
    else:
        T = 1.0 / 12.0

    common = sorted(set(c_map) & set(p_map))
    rows = []
    for k in common:
        C = c_map[k].get("theorprice") or c_map[k].get("last") or 0
        P = p_map[k].get("theorprice") or p_map[k].get("last") or 0
        if not C or not P:
            continue
        dev = put_call_parity_deviation(
            float(C), float(P), float(spot), float(k),
            r_pct, q_pct, T,
        )
        rows.append({
            "Страйк": k,
            "C (theor)": float(C),
            "P (theor)": float(P),
            "C − P": float(C) - float(P),
            "S·e^(−qT) − K·e^(−rT)": (
                float(spot) * np.exp(-q_pct / 100 * T)
                - float(k) * np.exp(-r_pct / 100 * T)
            ),
            "Отклонение, ₽": dev,
            "Отклонение, %": dev / float(spot) * 100,
        })

    if not rows:
        st.info("Нет страйков с ценами и у call, и у put.")
        return

    import pandas as pd  # локально, чтобы не тащить в шапку
    df = pd.DataFrame(rows)
    st.dataframe(
        df.style.format({
            "Страйк": "{:.2f}",
            "C (theor)": "{:.4f}",
            "P (theor)": "{:.4f}",
            "C − P": "{:+.4f}",
            "S·e^(−qT) − K·e^(−rT)": "{:+.4f}",
            "Отклонение, ₽": "{:+.4f}",
            "Отклонение, %": "{:+.4f}",
        }, na_rep="—"),
        use_container_width=True, hide_index=True,
    )

    max_dev = float(df["Отклонение, %"].abs().max())
    if max_dev > 1.0:
        st.warning(
            f"Макс. отклонение {max_dev:.2f}% — проверьте ликвидность. "
            f"На MOEX это почти всегда артефакт данных, а не арбитраж."
        )
    else:
        st.success(f"Паритет соблюдён (макс. {max_dev:.2f}%).")

    # Подсветка выбранного страйка пользователя
    if current_strike in df["Страйк"].values:
        _sel = df[df["Страйк"] == current_strike].iloc[0]
        st.caption(
            f"Выбранный страйк **{current_strike:g}**: "
            f"C={_sel['C (theor)']:.4f}, P={_sel['P (theor)']:.4f}, "
            f"отклонение={_sel['Отклонение, ₽']:+.4f} ₽ "
            f"({_sel['Отклонение, %']:+.4f}%)"
        )


# =====================================================================
# 5. σ-диапазон (гл. 2.7)
# =====================================================================
def _render_sigma_range(sigma, spot, expiry_str):
    st.markdown("### σ-диапазон цены (гл. 2.7)")
    st.caption(
        "1σ ≈ 66% вероятности, 2σ ≈ 95%, 3σ ≈ 99%. "
        "Если ваша проданная/купленная волатильность сильно "
        "расходится с реализованной — это и есть edge (или риск)."
    )

    if spot is None:
        st.warning("Нет цены БА.")
        return
    if sigma <= 0:
        st.warning("Некорректная IV.")
        return

    _default_months = 3
    if expiry_str:
        try:
            _exp = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            _d = (_exp - date.today()).days
            if _d > 0:
                _default_months = max(1, min(36, round(_d / 30)))
        except Exception:
            pass

    c1, c2 = st.columns(2)
    with c1:
        horizon = st.number_input(
            "Горизонт, мес.", value=_default_months,
            min_value=1, max_value=36, step=1, key="va_sr_months",
        )
    with c2:
        st.metric("Спот БА", f"{spot:.2f} ₽")

    ranges = sigma_range_forecast(float(spot), float(sigma),
                                    horizon / 12.0)

    st.markdown(
        f"**Текущая цена**: {spot:.2f} ₽ · **σ**: {sigma:.2f}% · "
        f"**T**: {horizon} мес."
    )
    for n, (lo, hi) in ranges.items():
        prob = {1: "66%", 2: "95%", 3: "99%"}[n]
        st.markdown(
            f"- **{n}σ ({prob})**: [{lo:.2f}, {hi:.2f}] ₽ "
            f"({(lo / spot - 1) * 100:+.1f}% … "
            f"{(hi / spot - 1) * 100:+.1f}%)"
        )
