"""Вкладка «Vol Analysis» — метрики волатильности по книге Коннолли.

Изолированный файл: не трогает board.py, alert_card.py, styles.py.
Читает st.session_state.positions (с вкладки «Позиция») и, где нужно,
fetch_optionboard (только чтение).
"""
import math
from datetime import date, datetime

import numpy as np
import pandas as pd
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
from core.options import fetch_optionboard
from core.strategies import _compute_r_q_for_leg


@st.fragment
def _render_vol_analysis_tab():
    st.header("Vol Analysis — теория Коннолли")
    st.caption(
        "К. Коннолли, «Покупка и продажа волатильности». "
        "Прибыль — от Γ-рехеджа, не от направления цены."
    )

    with st.expander("ℹ️ Что показывает вкладка", expanded=False):
        st.markdown(
            "**Снимок портфеля** — Γ, Θ, V, break-even daily move, "
            "длинная/короткая позиция по vol (гл. 4.3, 4.7, 5.2).\n\n"
            "**Карта дельты** (гл. 4.9): линии Δ во времени. "
            "Пересечение = сигнал к рехеджу.\n\n"
            "**Best / Worst case** (гл. 4.13, 5.6): предельные "
            "профили P&L для buy-and-hold.\n\n"
            "**Симулятор рехеджа** (гл. 4.10): Монте-Карло для "
            "дельта-нейтральной long-vol позиции. Прибыль зависит "
            "от реализованной волатильности.\n\n"
            "**Паритет пут-колл** (гл. 6.5): проверка на арбитраж."
        )

    _render_snapshot(st.session_state.get("positions", []))

    _asset = st.session_state.get("pos_asset", "")
    _atype = st.session_state.get("pos_category", "")

    _tabs = st.tabs([
        "Карта дельты",
        "Best / Worst case",
        "Симулятор рехеджа",
        "Паритет пут-колл",
        "σ-диапазон",
    ])
    with _tabs[0]:
        _render_delta_map(_asset, _atype)
    with _tabs[1]:
        _render_best_worst_case(_asset, _atype)
    with _tabs[2]:
        _render_rehedge_sim(_asset, _atype)
    with _tabs[3]:
        _render_put_call_parity(_asset, _atype)
    with _tabs[4]:
        _render_sigma_range(_asset, _atype)


# =====================================================================
# Снимок портфеля (гл. 4.3, 4.7, 5.2)
# =====================================================================
def _render_snapshot(positions: list[dict]) -> None:
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
def _render_delta_map(asset: str, atype: str) -> None:
    st.markdown("### Карта дельты (Fig 4.12)")
    st.caption(
        "Каждая линия — уровень Δ. Пересечение линии = сигнал к "
        "рехеджу, если торгуете с шагом Δ. Контуры сходятся к цене "
        "исполнения у экспирации — там рехедж максимально частый."
    )

    if not asset or not atype:
        st.info("Сначала выберите актив на вкладке «Позиция».")
        return

    spot = _get_current_market_price_for_asset(asset, atype)
    if spot is None:
        st.warning("Не удалось получить цену БА.")
        return

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        K = st.number_input("Страйк", value=float(spot), min_value=0.01,
                             step=1.0, key="va_K")
    with c2:
        sigma = st.number_input("IV, %", value=20.0, min_value=1.0,
                                 max_value=200.0, step=1.0, key="va_sigma")
    with c3:
        r_pct = st.number_input("Ставка, %", value=0.0, min_value=-10.0,
                                 max_value=100.0, step=0.5, key="va_r")
    with c4:
        opt_type = st.selectbox("Опцион", ["Call", "Put"], key="va_type")

    weeks = np.arange(0, 53)
    spots = np.linspace(K * 0.6, K * 1.4, 41)
    Z = np.zeros((len(weeks), len(spots)))
    for i, w in enumerate(weeks):
        T = max((52 - w) / 52.0, 1e-6)
        for j, S in enumerate(spots):
            Z[i, j] = bs_greeks(
                S, K, r_pct, 0.0, sigma, T,
                "C" if opt_type == "Call" else "P",
            ).delta

    lo, hi = (-1.0, 0.0) if opt_type == "Put" else (0.0, 1.0)
    fig = go.Figure(go.Contour(
        x=spots, y=weeks, z=Z,
        contours=dict(showlabels=True, start=lo, end=hi, size=0.1),
        colorscale="RdBu", reversescale=True,
        colorbar=dict(title="Δ"),
    ))
    fig.add_vline(x=spot, line_color="#1e88e5", line_dash="dash",
                  annotation_text=f"Спот {spot:.2f}")
    fig.add_vline(x=K, line_color="#333", line_dash="dot",
                  annotation_text=f"Страйк {K:.2f}")
    fig.update_layout(
        xaxis_title="Цена БА", yaxis_title="Недель до экспирации",
        height=500, margin=dict(l=20, r=20, t=40, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)


# =====================================================================
# 2. Best / Worst case (гл. 4.13, 5.6)
# =====================================================================
def _render_best_worst_case(asset: str, atype: str) -> None:
    st.markdown("### Best / Worst case (Fig 4.16, 5.6)")

    if not asset or not atype:
        st.info("Сначала выберите актив на вкладке «Позиция».")
        return

    spot = _get_current_market_price_for_asset(asset, atype)
    if spot is None:
        st.warning("Нет цены БА.")
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        K = st.number_input("Страйк", value=float(spot), min_value=0.01,
                             step=1.0, key="bw_K")
    with c2:
        iv = st.number_input("IV (заплаченная), %", value=20.0, min_value=1.0,
                              max_value=200.0, step=1.0, key="bw_iv")
    with c3:
        opt_type = st.selectbox("Опцион", ["Call", "Put"], key="bw_type")

    grid = np.linspace(K * 0.5, K * 1.5, 121)
    if opt_type == "Call":
        intrinsic = np.maximum(grid - K, 0)
        C0 = bs_greeks(spot, K, 0.0, 0.0, iv, 1.0, "C").price
    else:
        intrinsic = np.maximum(K - grid, 0)
        C0 = bs_greeks(spot, K, 0.0, 0.0, iv, 1.0, "P").price
    pnl = intrinsic - C0

    fig = go.Figure(go.Scatter(
        x=grid, y=pnl, mode="lines",
        line=dict(color="#1e5a7a", width=2),
        name=f"Long {opt_type} (купил-держи)",
    ))
    fig.add_hline(y=0, line_dash="dot", line_color="grey")
    fig.add_vline(x=K, line_dash="dot", line_color="grey",
                  annotation_text="страйк")
    fig.add_vline(x=spot, line_color="#1e88e5", line_dash="dash",
                  annotation_text=f"спот {spot:.2f}")
    fig.update_layout(
        xaxis_title="Цена БА на экспирации, ₽",
        yaxis_title="P&L, ₽ за 1 ед.",
        height=400, margin=dict(l=20, r=20, t=40, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "**Худший случай** (гл. 4.12): цена «прилипла» к страйку. "
        "**Лучший** (гл. 4.13): сильное движение в любую сторону. "
        "Для короткой позиции профиль зеркален."
    )


# =====================================================================
# 3. Симулятор рехеджа (гл. 4.10)
# =====================================================================
def _render_rehedge_sim(asset: str, atype: str) -> None:
    st.markdown("### Симулятор рехеджирования (Fig 4.10)")
    st.caption(
        "Дельта-нейтральная long-vol позиция: купили колл, продали "
        "Δ·100 акций. Прибыль = Γ-рехедж − Θ-распад. Если "
        "реализованная vol > заплаченной IV → прибыль."
    )

    if not asset or not atype:
        st.info("Сначала выберите актив на вкладке «Позиция».")
        return

    spot = _get_current_market_price_for_asset(asset, atype)
    if spot is None:
        st.warning("Нет цены БА.")
        return

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        K = st.number_input("Страйк", value=float(spot), min_value=0.01,
                             step=1.0, key="rs_K")
    with c2:
        sigma_impl = st.number_input("Заплаченная IV, %", value=20.0,
                                      min_value=1.0, max_value=200.0,
                                      step=1.0, key="rs_si")
    with c3:
        sigma_real = st.number_input("Реализованная vol, %", value=20.0,
                                      min_value=1.0, max_value=200.0,
                                      step=1.0, key="rs_sr")
    with c4:
        band = st.slider("Шаг рехеджа, Δ", 0.02, 0.30, 0.10, 0.02,
                          key="rs_band")
    n_paths = st.selectbox("Путей", [200, 500, 1000], index=1,
                            key="rs_np")

    if not st.button("Симулировать", type="primary", key="rs_run"):
        return

    with st.spinner("Симуляция…"):
        pnl = _mc_rehedge_pnl(spot, K, sigma_impl, sigma_real,
                              int(n_paths), band)

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
        st.success(f"Реализованная vol > заплаченной IV → в среднем прибыль.")
    elif sigma_real < sigma_impl:
        st.warning(f"Реализованная vol < заплаченной IV → в среднем убыток.")
    else:
        st.info("Реализованная vol = заплаченной IV → безубыток.")


def _mc_rehedge_pnl(S0, K, sigma_impl, sigma_real, n_paths, band):
    rng = np.random.default_rng(42)
    T, n_steps = 1.0, 252
    dt = T / n_steps
    C0 = bs_greeks(S0, K, 0.0, 0.0, sigma_impl, T, "C").price

    out = np.zeros(n_paths)
    for p in range(n_paths):
        S = S0
        d = bs_greeks(S, K, 0.0, 0.0, sigma_impl, T, "C").delta
        stock = -d
        cash = -C0 + d * S
        for step in range(1, n_steps + 1):
            S_new = S * math.exp(
                -0.5 * (sigma_real / 100.0) ** 2 * dt
                + (sigma_real / 100.0) * math.sqrt(dt) * rng.standard_normal()
            )
            tau = max(T - step * dt, 1e-9)
            d_new = bs_greeks(S_new, K, 0.0, 0.0, sigma_impl, tau, "C").delta
            if abs(d_new - d) >= band and step < n_steps:
                cash += (d_new - d) * S_new
                stock = -d_new
                d = d_new
            S = S_new
        out[p] = (cash + stock * S + max(S - K, 0.0)) * 100
    return out


# =====================================================================
# 4. Паритет пут-колл (гл. 6.5) — НЕ на доске, отдельной таблицей
# =====================================================================
def _render_put_call_parity(asset: str, atype: str) -> None:
    st.markdown("### Паритет пут-колл (гл. 6.5)")
    st.caption(
        "C − P = S·e^(−qT) − K·e^(−rT). Отклонение > 0 — либо арбитраж, "
        "либо устаревшие данные. Для MOEX — чаще второе."
    )

    if not asset or not atype:
        st.info("Сначала выберите актив на вкладке «Позиция».")
        return

    series_code = st.session_state.get("pos_series_code", "")
    expiry = st.session_state.get("pos_expiry", "")
    if not series_code or not expiry:
        st.info("Нет выбранной серии. Откройте «Позиция» и выберите серию.")
        return

    board = fetch_optionboard(asset, atype, series_code)
    if not board:
        st.warning("Не удалось загрузить доску.")
        return

    spot = _get_current_market_price_for_asset(asset, atype)
    if spot is None:
        st.warning("Нет цены БА.")
        return

    r_default, q_default = _compute_r_q_for_leg(asset, atype, expiry)
    c1, c2 = st.columns(2)
    with c1:
        r_pct = st.number_input("Ставка, %", value=float(r_default),
                                 step=0.5, key="pcp_r")
    with c2:
        q_pct = st.number_input("Дивдоходность, %", value=float(q_default),
                                 min_value=0.0, step=0.5, key="pcp_q")

    try:
        exp_date = datetime.strptime(expiry, "%Y-%m-%d").date()
        T = max((exp_date - date.today()).days / 365.0, 1e-6)
    except Exception:
        T = 1.0 / 12.0

    calls = board.get("call") or []
    puts = board.get("put") or []
    c_map = {float(c["strike"]): c for c in calls if c.get("strike") is not None}
    p_map = {float(p["strike"]): p for p in puts if p.get("strike") is not None}
    common = sorted(set(c_map) & set(p_map))

    rows = []
    for k in common:
        C = c_map[k].get("theorprice") or c_map[k].get("last") or 0
        P = p_map[k].get("theorprice") or p_map[k].get("last") or 0
        if not C or not P:
            continue
        dev = put_call_parity_deviation(float(C), float(P), float(spot),
                                         float(k), r_pct, q_pct, T)
        rows.append({
            "Страйк": k,
            "C (theor)": float(C),
            "P (theor)": float(P),
            "C − P": float(C) - float(P),
            "S·e^(−qT) − K·e^(−rT)": (float(spot) * math.exp(-q_pct/100 * T)
                                       - float(k) * math.exp(-r_pct/100 * T)),
            "Отклонение, ₽": dev,
            "Отклонение, %": dev / float(spot) * 100,
        })

    if not rows:
        st.info("Нет страйков с ценами и у call, и у put.")
        return

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


# =====================================================================
# 5. σ-диапазон (гл. 2.7)
# =====================================================================
def _render_sigma_range(asset: str, atype: str) -> None:
    st.markdown("### σ-диапазон цены (гл. 2.7)")
    st.caption(
        "1σ ≈ 66% вероятности, 2σ ≈ 95%, 3σ ≈ 99%. "
        "Если ваша проданная/купленная волатильность сильно "
        "расходится с реализованной — это и есть edge (или риск)."
    )

    if not asset or not atype:
        st.info("Сначала выберите актив на вкладке «Позиция».")
        return

    spot = _get_current_market_price_for_asset(asset, atype)
    if spot is None:
        st.warning("Нет цены БА.")
        return

    c1, c2 = st.columns(2)
    with c1:
        sigma = st.number_input("σ, %", value=20.0, min_value=1.0,
                                 max_value=200.0, step=1.0, key="sr_sigma")
    with c2:
        T_months = st.number_input("Горизонт, мес.", value=3, min_value=1,
                                    max_value=36, step=1, key="sr_months")

    ranges = sigma_range_forecast(spot, sigma, T_months / 12.0)
    st.markdown(
        f"**Текущая цена**: {spot:.2f} ₽ · **σ**: {sigma}% · "
        f"**T**: {T_months} мес."
    )
    for n, (lo, hi) in ranges.items():
        prob = {1: "66%", 2: "95%", 3: "99%"}[n]
        st.markdown(
            f"- **{n}σ ({prob})**: [{lo:.2f}, {hi:.2f}] ₽ "
            f"({(lo/spot-1)*100:+.1f}% … {(hi/spot-1)*100:+.1f}%)"
        )