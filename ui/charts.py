"""Биржевой график: OHLC + объём + уровни + профиль P&L.

Склейка сессий реализована через `rangebreaks` в Plotly —
данные НЕ фильтруются, ось X просто скрывает выходные и ночные часы,
поэтому бары идут подряд без визуальных «рваных» провалов.
"""
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from core.helpers import _fmt_strike
from core.pricing import compute_payoff, find_breakevens


def render_exchange_chart(df, positions, buy_level, sell_level, strikes, title, key,
                          current_price=None, comm_func=None, anchor_expiry=None,
                          interval=24):
    if df is None or df.empty:
        st.info(f"Нет данных для {title}")
        return

    # ==================================================================
    # СКЛЕЙКА СЕССИЙ через rangebreaks.
    #
    # D1 (interval=24): скрывает субботу и воскресенье — бары идут
    #                   непрерывно: пятница → понедельник.
    # H1 (interval=60): скрывает субботу/воскресенье И ночные часы
    #                   (23:00 → 07:00). Внутри дня остаются только
    #                   бары с 07:00 до 22:59, стыки дней без провалов.
    #
    # Если вечерняя сессия биржи доходит до 23:50 и нужно её показать —
    # замените `23` на `24` в bounds=[23, 7] (или на нужный час).
    # ==================================================================
    _rangebreaks = [
        dict(bounds=["sat", "mon"]),  # выходные
    ]
    if interval == 60:
        _rangebreaks.append(dict(bounds=[23, 7], pattern="hour"))

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.04, row_heights=[0.78, 0.22],
        specs=[[{"secondary_y": True}], [{"secondary_y": False}]],
    )
    _hover_dt_fmt = "%d.%m.%Y %H:%M" if interval == 60 else "%d.%m.%Y"

    fig.add_trace(
        go.Ohlc(
            x=df["begin"], open=df["open"], high=df["high"],
            low=df["low"], close=df["close"],
            increasing_line_color="black", decreasing_line_color="black",
            name="", showlegend=False,
            hovertemplate=(
                f"<b>%{{x|{_hover_dt_fmt}}}</b><br>"
                "O: %{open:.2f}<br>"
                "H: %{high:.2f}<br>"
                "L: %{low:.2f}<br>"
                "C: %{close:.2f}<extra></extra>"
            ),
        ),
        row=1, col=1, secondary_y=False,
    )
    fig.add_trace(
        go.Bar(
            x=df["begin"], y=df["volume"], marker_color="black",
            name="", showlegend=False,
            hovertemplate=(
                f"<b>%{{x|{_hover_dt_fmt}}}</b><br>"
                "V: %{y:,.0f}<extra></extra>"
            ),
        ),
        row=2, col=1,
    )
    price_min = float(df["low"].min()) * 0.97
    price_max = float(df["high"].max()) * 1.03
    if positions:
        be_points = find_breakevens(
            positions, price_min, price_max,
            n=500, comm_func=comm_func, anchor_expiry=anchor_expiry,
        )
        segments = [price_min] + sorted(be_points) + [price_max]
        for i in range(len(segments) - 1):
            seg_start = segments[i]
            seg_end = segments[i + 1]
            mid = (seg_start + seg_end) / 2
            mid_pnl = compute_payoff(
                positions, [mid], comm_func=comm_func,
                anchor_expiry=anchor_expiry,
            )[0]
            color = ("rgba(0,220,80,0.28)" if mid_pnl > 0
                     else "rgba(255,40,40,0.22)")
            fig.add_shape(
                type="rect", xref="x domain", x0=0, x1=1,
                yref="y", y0=seg_start, y1=seg_end,
                fillcolor=color, line_width=0, layer="below",
                row=1, col=1,
            )
    else:
        be_points = []

    visible_strikes = []
    if strikes:
        visible_strikes = sorted(
            float(s) for s in strikes if price_min <= float(s) <= price_max
        )
        for K in visible_strikes:
            fig.add_hline(
                y=K, line=dict(color="#9c00ff", width=1, dash="dot"),
                opacity=0.45, row=1, col=1,
            )

    buy_markers, sell_markers = [], []
    for p in (positions or []):
        if not p.get("visible", True):
            continue
        if p.get("Опцион") not in ("Call", "Put"):
            continue
        qty = int(p.get("Кол-во", 0))
        if qty == 0:
            continue
        K = p.get("Страйк")
        if K is None:
            continue
        code = "C" if p["Опцион"] == "Call" else "P"
        color = "#00a651" if p["Опцион"] == "Call" else "#d32f2f"
        sign = "+" if qty > 0 else "-"
        Ks = _fmt_strike(K, p.get("_atype"))
        exp_marker = ""
        exp_str = p.get("Эксп.", "—")
        if exp_str and exp_str != "—":
            try:
                exp_d = datetime.strptime(exp_str, "%Y-%m-%d").date()
                exp_marker = f" [{exp_d.strftime('%m/%y')}]"
            except Exception:
                pass
        entry = {
            "text": f"{sign}{abs(qty)}{code} {Ks}{exp_marker}",
            "color": color,
        }
        if qty > 0:
            buy_markers.append(entry)
        else:
            sell_markers.append(entry)

    def _add_level(level, label_txt, line_color, markers):
        fig.add_hline(
            y=level, line=dict(color=line_color, width=2.5),
            row=1, col=1,
        )
        fig.add_annotation(
            x=0.5, y=level, xref="paper", yref="y",
            text=label_txt, showarrow=False,
            font=dict(size=10, color=line_color, family="Arial Black"),
            bgcolor="rgba(255,255,255,0.85)", yshift=11, row=1, col=1,
        )
        for i, m in enumerate(markers[:6]):
            fig.add_annotation(
                x=0.5, y=level, xref="paper", yref="y",
                text=m["text"], showarrow=False,
                font=dict(size=10, color=m["color"],
                          family="Consolas, monospace"),
                bgcolor="rgba(255,255,255,0.88)",
                xshift=80 + i * 72, yshift=11, row=1, col=1,
            )

    if buy_level and buy_level > 0:
        _add_level(buy_level, f"Покупка {buy_level:.2f}", "#9c00ff", buy_markers)
    if sell_level and sell_level > 0:
        _add_level(sell_level, f"Продажа {sell_level:.2f}", "#fb92f0", sell_markers)
    if current_price is not None and current_price > 0:
        fig.add_hline(
            y=current_price,
            line=dict(color="#1e88e5", width=1.5, dash="dash"),
            row=1, col=1,
        )
        fig.add_annotation(
            x=1.0, y=current_price, xref="paper", yref="y",
            text=f"{current_price:.2f}", showarrow=False,
            xanchor="left", yanchor="middle", xshift=2,
            font=dict(size=9, color="#ffffff", family="Arial"),
            bgcolor="#1e88e5", borderpad=2, row=1, col=1,
        )
        _last_bar_date = df["begin"].iloc[-1]
        _last_bar_close = float(df["close"].iloc[-1])
        fig.add_annotation(
            x=_last_bar_date, y=_last_bar_close, xref="x", yref="y",
            text=f"{_last_bar_close:.2f}",
            showarrow=True, arrowhead=2, arrowsize=0.8,
            arrowwidth=1, arrowcolor="#000000",
            ax=32, ay=0, xanchor="left",
            font=dict(size=9, color="#ffffff", family="Arial"),
            bgcolor="#000000", bordercolor="#ffffff",
            borderwidth=1, borderpad=2, row=1, col=1,
        )
    for be in be_points:
        fig.add_hline(
            y=be, line=dict(color="#00a651", width=1.5, dash="dot"),
            row=1, col=1,
        )
        fig.add_annotation(
            x=0.5, y=be, xref="paper", yref="y",
            text=f"БУ {be:.2f}", showarrow=False,
            font=dict(size=10, color="#00a651", family="Arial Black"),
            bgcolor="rgba(255,255,255,0.90)",
            bordercolor="#00a651", borderwidth=1, yshift=10, row=1, col=1,
        )
    fig.add_trace(
        go.Scatter(
            x=[df["begin"].iloc[0], df["begin"].iloc[-1]],
            y=[price_min, price_max], mode="lines",
            line=dict(width=0), opacity=0, showlegend=False,
            hoverinfo="skip", name="_right_axis",
        ),
        row=1, col=1, secondary_y=True,
    )
    fig.update_layout(
        title=title, height=520,
        margin=dict(l=20, r=70, t=50, b=20),
        plot_bgcolor="white", paper_bgcolor="white",
        hovermode="closest", showlegend=False,
        hoverlabel=dict(
            bgcolor="rgba(255,255,255,0.95)",
            bordercolor="#1e5a7a",
            font=dict(size=11, color="#1a3b4f",
                      family="Consolas, monospace"),
            align="left", namelength=-1,
        ),
    )
    if visible_strikes:
        _strike_vals = [float(k) for k in visible_strikes]
        _strike_text = [_fmt_strike(k) for k in _strike_vals]
    else:
        _strike_vals = None
        _strike_text = None
    fig.update_yaxes(
        showgrid=True, gridcolor="rgba(0,0,0,0.05)",
        side="left", row=1, col=1,
        tickmode="array" if _strike_vals else "auto",
        tickvals=_strike_vals if _strike_vals else None,
        ticktext=_strike_text if _strike_text else None,
        tickfont=dict(size=10, color="#9c00ff"),
        showspikes=True, spikemode='across', spikesnap='cursor',
        spikecolor='#888888', spikethickness=1, spikedash='dot',
    )
    fig.update_yaxes(
        showgrid=True, gridcolor="rgba(0,0,0,0.05)",
        side="left", row=2, col=1,
        showspikes=True, spikemode='across', spikesnap='cursor',
        spikecolor='#888888', spikethickness=1, spikedash='dot',
    )
    fig.update_yaxes(
        side="right", row=1, col=1, secondary_y=True,
        showgrid=False, zeroline=False,
        tickfont=dict(size=10, color="#1a3b4f"),
        showspikes=True, spikemode='across', spikesnap='cursor',
        spikecolor='#1e88e5', spikethickness=1, spikedash='dot',
        title=None,
    )
    _x_min_val = df["begin"].min()
    _x_max_val = df["begin"].max()
    if pd.notna(_x_min_val) and pd.notna(_x_max_val):
        if len(df) >= 2:
            _diffs = df["begin"].diff().dropna()
            _step = (_diffs.median() if not _diffs.empty
                     else pd.Timedelta(days=1))
        else:
            _step = pd.Timedelta(days=1)
        if pd.isna(_step) or _step <= pd.Timedelta(0):
            _step = pd.Timedelta(days=1)
        _x_max_extended = _x_max_val + _step * 15
        _x_tickfmt = "%d.%m %H:%M" if interval == 60 else "%d.%m"

        fig.update_xaxes(
            type='date', range=[_x_min_val, _x_max_extended],
            showgrid=True, gridcolor="rgba(0,0,0,0.05)",
            rangeslider_visible=False, row=1, col=1,
            tickformat=_x_tickfmt,
            rangebreaks=_rangebreaks,
            showspikes=True, spikemode='across', spikesnap='cursor',
            spikecolor='#666666', spikethickness=1, spikedash='dot',
        )
        fig.update_xaxes(
            type='date', range=[_x_min_val, _x_max_extended],
            showgrid=True, gridcolor="rgba(0,0,0,0.05)",
            row=2, col=1, tickformat=_x_tickfmt,
            rangebreaks=_rangebreaks,
            showspikes=True, spikemode='across', spikesnap='cursor',
            spikecolor='#666666', spikethickness=1, spikedash='dot',
        )
    st.plotly_chart(fig, use_container_width=True, key=key)
