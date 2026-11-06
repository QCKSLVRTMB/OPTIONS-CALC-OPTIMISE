"""Вкладка «Журнал сделок»."""
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.helpers import _fmt_strike, _new_position_id
from core.journal import (
    _journal_closed_sorted,
    _journal_equity_series,
    _journal_get_deposit,
    _journal_max_drawdown,
    _journal_profit_factor,
    _journal_stats_by,
    _journal_streaks,
)


@st.fragment
def _render_journal_tab():
    st.header("Журнал сделок")
    st.caption("Учёт закрытых и открытых сделок. Экспорт/импорт CSV. "
               "Служит источником статистики для Калькулятора Келли.")

    if "journal_rows" not in st.session_state:
        st.session_state.journal_rows = []

    if "journal_view_mode" not in st.session_state:
        st.session_state.journal_view_mode = "Карточки (мобильный)"

    if "journal_initial_deposit" not in st.session_state:
        st.session_state.journal_initial_deposit = 100000.0
    if "journal_deposit_mode" not in st.session_state:
        st.session_state.journal_deposit_mode = "auto"
    if "journal_current_deposit_manual" not in st.session_state:
        st.session_state.journal_current_deposit_manual = None
    if "journal_period_filter" not in st.session_state:
        st.session_state.journal_period_filter = "Всё время"

    # ==========================================================
    # ШАПКА: депозит + метрики баланса
    # ==========================================================
    _init_dep, _cur_dep, _dep_mode = _journal_get_deposit()

    with st.container(border=True):
        st.markdown("#### Депозит")
        _dep_c1, _dep_c2, _dep_c3 = st.columns([2, 2, 2])
        with _dep_c1:
            _new_init = st.number_input(
                "Начальный депозит, ₽",
                min_value=0.0,
                value=float(_init_dep),
                step=1000.0,
                format="%.0f",
                key="journal_initial_deposit_inp",
            )
            if abs(_new_init - float(_init_dep)) > 0.01:
                st.session_state.journal_initial_deposit = float(_new_init)
                st.rerun()
        with _dep_c2:
            _mode_options = ["Авто (по P&L сделок)", "Вручную"]
            _mode_idx = 0 if _dep_mode == "auto" else 1
            _new_mode = st.selectbox(
                "Режим",
                _mode_options,
                index=_mode_idx,
                key="journal_deposit_mode_select",
            )
            _new_mode_key = ("auto" if _new_mode.startswith("Авто")
                             else "manual")
            if _new_mode_key != _dep_mode:
                st.session_state.journal_deposit_mode = _new_mode_key
                st.rerun()
        with _dep_c3:
            if _dep_mode == "manual":
                _cur_manual_val = (
                    st.session_state.get("journal_current_deposit_manual")
                    or _init_dep
                )
                _new_manual = st.number_input(
                    "Текущий депозит, ₽",
                    min_value=0.0,
                    value=float(_cur_manual_val),
                    step=1000.0,
                    format="%.0f",
                    key="journal_current_deposit_inp",
                )
                if abs(_new_manual - float(_cur_manual_val)) > 0.01:
                    st.session_state.journal_current_deposit_manual = (
                        float(_new_manual)
                    )
                    st.rerun()
            else:
                st.metric("Текущий баланс (авто)", f"{_cur_dep:,.0f} ₽")

        _growth_abs = _cur_dep - _init_dep
        _growth_pct = ((_growth_abs / _init_dep * 100.0)
                       if _init_dep > 0 else 0.0)
        _growth_color = ("#00a651" if _growth_abs > 0
                         else "#d32f2f" if _growth_abs < 0 else "#333")
        st.markdown(
            f"<div style='padding:8px 14px; background:#f6f9fc; "
            f"border-radius:8px; font-size:.9rem; margin-top:6px;'>"
            f"<b>Прирост:</b> "
            f"<span style='color:{_growth_color}; font-weight:800; "
            f"font-size:1.05rem;'>{_growth_abs:+,.2f} ₽</span> "
            f"<span style='color:{_growth_color};'>"
            f"({_growth_pct:+.2f}%)</span></div>",
            unsafe_allow_html=True,
        )

    # ==========================================================
    # ДОБАВИТЬ СДЕЛКУ
    # ==========================================================
    with st.expander("Добавить сделку", expanded=False):
        with st.form("journal_add_form", clear_on_submit=True):
            _jc1, _jc2, _jc3 = st.columns(3)
            with _jc1:
                j_date = st.date_input(
                    "Дата входа", value=date.today(), key="jf_date",
                )
                j_ticker = st.text_input(
                    "Тикер", value="SBER", key="jf_ticker",
                ).strip().upper()
            with _jc2:
                j_cat = st.selectbox(
                    "Категория",
                    ["Акция", "Фьючерс", "Валюта", "Товар", "Индекс"],
                    key="jf_cat",
                )
                j_instr = st.selectbox(
                    "Инструмент",
                    ["Опцион", "Фьючерс", "Акция", "Индекс"],
                    key="jf_instr",
                )
            with _jc3:
                j_opt = st.selectbox(
                    "Опцион", ["Call", "Put", "—"], key="jf_opt",
                )
                j_side = st.selectbox(
                    "Направление", ["Покупка", "Продажа"], key="jf_side",
                )

            _jd1, _jd2, _jd3 = st.columns(3)
            with _jd1:
                j_strike = st.number_input(
                    "Страйк", min_value=0.0, value=0.0,
                    step=1.0, format="%.4f", key="jf_strike",
                )
                j_qty = st.number_input(
                    "Кол-во", min_value=1, value=1, step=1, key="jf_qty",
                )
            with _jd2:
                j_expiry = st.date_input(
                    "Экспирация",
                    value=date.today() + timedelta(days=30),
                    key="jf_expiry",
                )
                j_entry = st.number_input(
                    "Цена входа", min_value=0.0, value=0.0,
                    step=0.01, format="%.4f", key="jf_entry",
                )
            with _jd3:
                j_exit = st.number_input(
                    "Цена выхода", min_value=0.0, value=0.0,
                    step=0.01, format="%.4f", key="jf_exit",
                    help="0 — сделка ещё открыта",
                )
                j_comm = st.number_input(
                    "Комиссия, ₽", min_value=0.0,
                    value=0.0, step=0.01, format="%.4f", key="jf_comm",
                )

            j_burned = st.checkbox(
                "Опцион сгорел (потеря премии)",
                value=False, key="jf_burned",
            )
            j_note = st.text_input("Заметка", value="", key="jf_note")

            _jsubmit = st.form_submit_button(
                "Добавить в журнал", type="primary",
                use_container_width=True,
            )
            if _jsubmit:
                _sign = 1 if j_side == "Покупка" else -1
                if j_burned:
                    _status = "Сгорел"
                    _exit_val = None
                    _pnl = -_sign * (j_entry * j_qty) - j_comm
                elif j_exit > 0:
                    _status = "Закрыта"
                    _exit_val = float(j_exit)
                    _pnl = (_sign * (j_exit - j_entry) * j_qty) - j_comm
                else:
                    _status = "Открыта"
                    _exit_val = None
                    _pnl = 0.0
                st.session_state.journal_rows.append({
                    "_id": _new_position_id(),
                    "Дата входа": j_date.isoformat(),
                    "Тикер": j_ticker,
                    "Категория": j_cat,
                    "Инструмент": j_instr,
                    "Опцион": j_opt,
                    "Направление": j_side,
                    "Страйк": j_strike if j_strike > 0 else None,
                    "Экспирация": j_expiry.isoformat(),
                    "Кол-во": int(j_qty),
                    "Цена входа": float(j_entry),
                    "Цена выхода": _exit_val,
                    "Комиссия": float(j_comm),
                    "P&L": float(_pnl),
                    "Статус": _status,
                    "Заметка": j_note,
                })
                _msg = (f"Сделка добавлена: {j_ticker} {j_opt} {j_strike} "
                        f"({j_side} × {j_qty})")
                if j_burned:
                    _msg += " (сгорел)"
                st.success(_msg)
                st.rerun()

    # ==========================================================
    # УПРАВЛЕНИЕ
    # ==========================================================
    st.markdown("### Управление")
    _e1, _e2, _e3 = st.columns(3)
    with _e1:
        if st.session_state.journal_rows:
            _df_j = pd.DataFrame(st.session_state.journal_rows)
            _df_j = _df_j.drop(columns=["_id"], errors="ignore")
            st.download_button(
                "Экспорт CSV",
                data=_df_j.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"journal_{date.today().isoformat()}.csv",
                mime="text/csv", use_container_width=True,
                key="journal_export_btn",
            )
        else:
            st.button(
                "Экспорт CSV", disabled=True,
                use_container_width=True, key="journal_export_disabled",
            )
    with _e2:
        _up = st.file_uploader(
            "Импорт CSV", type=["csv"], key="journal_import",
            label_visibility="collapsed",
        )
        if _up is not None:
            try:
                _df_imp = pd.read_csv(_up)
                _imp_rows = []
                for _, r in _df_imp.iterrows():
                    _row = {
                        "_id": _new_position_id(),
                        "Дата входа": str(r.get(
                            "Дата входа", date.today().isoformat()
                        )),
                        "Тикер": str(r.get("Тикер", "—")).upper(),
                        "Категория": str(r.get("Категория", "Акция")),
                        "Инструмент": str(r.get("Инструмент", "Опцион")),
                        "Опцион": str(r.get("Опцион", "—")),
                        "Направление": str(r.get(
                            "Направление", "Покупка"
                        )),
                        "Страйк": (
                            float(r["Страйк"])
                            if pd.notna(r.get("Страйк")) else None
                        ),
                        "Экспирация": str(r.get("Экспирация", "—")),
                        "Кол-во": int(r.get("Кол-во", 0) or 0),
                        "Цена входа": float(r.get("Цена входа", 0) or 0),
                        "Цена выхода": (
                            float(r["Цена выхода"])
                            if pd.notna(r.get("Цена выхода")) else None
                        ),
                        "Комиссия": float(r.get("Комиссия", 0) or 0),
                        "P&L": float(r.get("P&L", 0) or 0),
                        "Статус": str(r.get("Статус", "Открыта")),
                        "Заметка": str(r.get("Заметка", "")),
                    }
                    _imp_rows.append(_row)
                if st.button(
                    "Применить импорт", type="primary",
                    key="journal_apply_import",
                    use_container_width=True,
                ):
                    st.session_state.journal_rows = _imp_rows
                    st.success(f"Импортировано {len(_imp_rows)} сделок.")
                    st.rerun()
                st.caption(f"Файл: {len(_imp_rows)} строк")
            except Exception as e:
                st.error(f"Ошибка чтения CSV: {e}")
    with _e3:
        if st.button(
            "Очистить журнал", use_container_width=True,
            key="journal_clear_all",
        ):
            st.session_state.journal_rows = []
            st.rerun()

    st.markdown("---")

    # ==========================================================
    # ФИЛЬТР ПО ПЕРИОДУ
    # ==========================================================
    _period_options = ["Всё время", "Последний месяц",
                       "Последние 3 месяца", "Последние 6 месяцев",
                       "Последний год"]
    _period_chosen = st.radio(
        "Период",
        _period_options,
        index=(_period_options.index(
            st.session_state.journal_period_filter
        ) if st.session_state.journal_period_filter in _period_options else 0),
        horizontal=True,
        key="journal_period_radio",
        label_visibility="collapsed",
    )
    st.session_state.journal_period_filter = _period_chosen

    _period_days_map = {
        "Всё время": None,
        "Последний месяц": 30,
        "Последние 3 месяца": 90,
        "Последние 6 месяцев": 180,
        "Последний год": 365,
    }
    _period_days = _period_days_map.get(_period_chosen)

    _rows_all = st.session_state.journal_rows
    if _period_days is not None and _rows_all:
        _cutoff = date.today() - timedelta(days=_period_days)
        _rows_filtered = []
        for r in _rows_all:
            try:
                _rd = datetime.strptime(
                    str(r.get("Дата входа", "")), "%Y-%m-%d"
                ).date()
                if _rd >= _cutoff:
                    _rows_filtered.append(r)
            except Exception:
                _rows_filtered.append(r)
    else:
        _rows_filtered = list(_rows_all)

    # ==========================================================
    # СТАТИСТИКА
    # ==========================================================
    st.markdown("### Статистика")

    if not _rows_filtered:
        if _period_days is not None:
            st.info(f"Нет сделок за период «{_period_chosen}». "
                    f"Выберите «Всё время» или добавьте сделки.")
        else:
            st.info("Журнал пуст. Добавьте первую сделку.")
        return

    _closed = [
        r for r in _rows_filtered
        if r.get("Статус") in ("Закрыта", "Сгорел")
    ]
    _wins = [r for r in _closed if (r.get("P&L") or 0) > 0]
    _losses = [r for r in _closed if (r.get("P&L") or 0) < 0]
    _total_pnl = sum((r.get("P&L") or 0) for r in _closed)
    _gross_win = sum((r.get("P&L") or 0) for r in _wins)
    _gross_loss = abs(sum((r.get("P&L") or 0) for r in _losses))
    _avg_win = (_gross_win / len(_wins)) if _wins else 0.0
    _avg_loss = (_gross_loss / len(_losses)) if _losses else 0.0
    _win_rate = (len(_wins) / len(_closed) * 100) if _closed else 0.0

    _profit_factor = _journal_profit_factor(_closed)

    _equity_df = _journal_equity_series(_rows_all, _init_dep)
    _max_dd_abs, _max_dd_pct = _journal_max_drawdown(_equity_df)

    _streaks = _journal_streaks(_closed)

    _s1, _s2, _s3, _s4 = st.columns(4)
    _s1.metric("Всего сделок", len(_rows_filtered),
               delta=f"закрыто {len(_closed)}")
    _s2.metric("Win-rate", f"{_win_rate:.1f}%",
               delta=f"{len(_wins)}W / {len(_losses)}L")
    _s3.metric("Total P&L (фильтр)", f"{_total_pnl:+,.2f} ₽")
    if _profit_factor is None:
        _pf_str = "—"
    elif _profit_factor == float("inf"):
        _pf_str = "∞"
    else:
        _pf_str = f"{_profit_factor:.2f}"
    _s4.metric("Profit Factor", _pf_str)

    _s5, _s6, _s7, _s8 = st.columns(4)
    _s5.metric("Ср. выигрыш", f"{_avg_win:+,.2f} ₽")
    _s6.metric("Ср. убыток", f"{-_avg_loss:+,.2f} ₽")
    if _closed and _avg_loss > 0:
        _expectancy = ((_win_rate / 100.0) * _avg_win
                       - (1 - _win_rate / 100.0) * _avg_loss)
        _s7.metric("Expectancy", f"{_expectancy:+,.2f} ₽")
    else:
        _s7.metric("Expectancy", "—")
    _s8.metric("Max Drawdown",
               f"{_max_dd_abs:,.0f} ₽" if _max_dd_abs > 0 else "—",
               delta=(f"−{_max_dd_pct:.1f}%"
                      if _max_dd_pct > 0 else None),
               delta_color="inverse")

    _s9, _s10, _s11, _s12 = st.columns(4)
    _s9.metric("Max серия W", _streaks["max_wins"],
               delta=f"ср. {_streaks['avg_wins']:.1f}")
    _s10.metric("Max серия L", _streaks["max_losses"],
                delta=f"ср. {_streaks['avg_losses']:.1f}")
    _open_trades = [
        r for r in _rows_filtered if r.get("Статус") == "Открыта"
    ]
    _s11.metric("Открыто сейчас", len(_open_trades))
    _max_loss_single = 0.0
    if _losses:
        _max_loss_single = abs(min((r.get("P&L") or 0) for r in _losses))
    _risk_pct_per_trade = ((_max_loss_single / _init_dep * 100.0)
                           if _init_dep > 0 else 0.0)
    _s12.metric("Макс. убыток / сделка", f"{_max_loss_single:,.0f} ₽",
                delta=f"{_risk_pct_per_trade:.1f}% депо",
                delta_color="inverse")

    # ==========================================================
    # EQUITY CURVE
    # ==========================================================
    if not _equity_df.empty:
        st.markdown("---")
        st.markdown("### Кривая капитала")

        _eq_plot = _equity_df.copy()
        _eq_plot["idx"] = range(1, len(_eq_plot) + 1)
        _eq_plot["label"] = (_eq_plot["date"] + " #"
                             + _eq_plot["idx"].astype(str))

        fig_eq = go.Figure()
        fig_eq.add_trace(go.Scatter(
            x=_eq_plot["idx"],
            y=_eq_plot["balance"],
            mode="lines+markers",
            name="Баланс",
            line=dict(color="#1e5a7a", width=3),
            marker=dict(size=6, color="#1e88e5"),
            customdata=_eq_plot[["date", "pnl"]].values,
            hovertemplate=(
                "Сделка #%{x}<br>"
                "Дата: %{customdata[0]}<br>"
                "P&L: %{customdata[1]:+,.2f} ₽<br>"
                "Баланс: %{y:,.2f} ₽<extra></extra>"
            ),
        ))
        fig_eq.add_hline(
            y=float(_init_dep),
            line=dict(color="#7a7a7a", width=1.5, dash="dash"),
            annotation_text=f"Начальный депозит = {_init_dep:,.0f} ₽",
            annotation_position="top left",
            annotation_font_color="#7a7a7a",
        )
        fig_eq.update_layout(
            title="Кривая капитала",
            xaxis_title="№ сделки (закрытые)",
            yaxis_title="Баланс, ₽",
            height=380,
            margin=dict(l=20, r=20, t=50, b=20),
            plot_bgcolor="white", paper_bgcolor="white",
            hovermode="x unified",
            showlegend=False,
        )
        st.plotly_chart(fig_eq, use_container_width=True)

    # ==========================================================
    # СТАТИСТИКА ПО ТИКЕРАМ
    # ==========================================================
    with st.expander("Статистика по тикерам и стратегиям",
                     expanded=False):
        _stat_tab1, _stat_tab2 = st.tabs(["По тикерам", "По конструкциям"])

        with _stat_tab1:
            _df_by_ticker = _journal_stats_by(_closed, "Тикер")
            if not _df_by_ticker.empty:
                st.dataframe(
                    _df_by_ticker.style.format({
                        "Win-rate, %": "{:.1f}",
                        "P&L, ₽": "{:+,.2f}",
                    }, na_rep="—"),
                    use_container_width=True, hide_index=True,
                    height=min(120 + 35 * len(_df_by_ticker), 400),
                )
            else:
                st.caption("Нет закрытых сделок")

        with _stat_tab2:
            _df_by_constr = _journal_stats_by(_closed, "Опцион")
            if not _df_by_constr.empty:
                st.dataframe(
                    _df_by_constr.style.format({
                        "Win-rate, %": "{:.1f}",
                        "P&L, ₽": "{:+,.2f}",
                    }, na_rep="—"),
                    use_container_width=True, hide_index=True,
                    height=min(120 + 35 * len(_df_by_constr), 400),
                )
            else:
                st.caption("Нет закрытых сделок")

    # ==========================================================
    # ТАБЛИЦА / КАРТОЧКИ
    # ==========================================================
    st.markdown("---")
    st.markdown("### Список сделок")

    _balance_by_id = {}
    _pct_by_id = {}
    _running_balance = _init_dep
    for r in _journal_closed_sorted(_rows_all):
        _p = float(r.get("P&L") or 0.0)
        _bal_before = _running_balance
        _running_balance += _p
        _rid = r.get("_id")
        if _rid:
            _balance_by_id[_rid] = _running_balance
            _pct_by_id[_rid] = (
                (_p / _bal_before * 100.0) if _bal_before > 0 else 0.0
            )

    _rows_for_table = []
    for r in _rows_filtered:
        _rr = dict(r)
        _rid = r.get("_id")
        if r.get("Статус") in ("Закрыта", "Сгорел"):
            _rr["_balance"] = _balance_by_id.get(_rid)
            _rr["_pct_deposit"] = _pct_by_id.get(_rid)
        else:
            _rr["_balance"] = None
            _rr["_pct_deposit"] = None
        _rows_for_table.append(_rr)

    _df_journal = pd.DataFrame(_rows_for_table)
    _df_display = _df_journal.drop(
        columns=["_id", "_balance", "_pct_deposit"], errors="ignore"
    )

    def _style_journal_row(row):
        _styles = []
        for col in row.index:
            s = ""
            if col == "P&L":
                v = row[col]
                if isinstance(v, (int, float)):
                    if v > 0:
                        s = "color: #00a651; font-weight: 700;"
                    elif v < 0:
                        s = "color: #d32f2f; font-weight: 700;"
            elif col == "Направление":
                if row[col] == "Покупка":
                    s = "color: #00a651; font-weight: 600;"
                elif row[col] == "Продажа":
                    s = "color: #d32f2f; font-weight: 600;"
            elif col == "Статус":
                if row[col] == "Сгорел":
                    s = ("background-color: #ffe6e6; color: #8b1e1e; "
                         "font-weight: 700;")
                elif row[col] == "Закрыта":
                    s = "color: #5a6b78;"
                elif row[col] == "Открыта":
                    s = "color: #1e88e5; font-weight: 600;"
            _styles.append(s)
        return _styles

    _view_mode_journal = st.radio(
        "Вид журнала",
        ["Таблица", "Карточки (мобильный)"],
        index=(0 if st.session_state.journal_view_mode == "Таблица"
               else 1),
        horizontal=True,
        key="journal_view_mode_radio",
        label_visibility="collapsed",
    )
    st.session_state.journal_view_mode = _view_mode_journal

    if st.session_state.journal_view_mode == "Карточки (мобильный)":
        for _idx_j, _row_j in _df_journal.iterrows():
            _rd_j = _row_j.to_dict()
            _status = _rd_j.get("Статус", "")
            _pnl_j = _rd_j.get("P&L") or 0
            _side = _rd_j.get("Направление", "—")
            _opt = _rd_j.get("Опцион", "—")
            _ticker_j = _rd_j.get("Тикер", "—")
            _strike_j = _rd_j.get("Страйк")
            _qty_j = _rd_j.get("Кол-во", 0)
            _entry_j = _rd_j.get("Цена входа", 0)
            _exit_j = _rd_j.get("Цена выхода")
            _comm_j = _rd_j.get("Комиссия", 0)
            _date_j = _rd_j.get("Дата входа", "—")
            _exp_j = _rd_j.get("Экспирация", "—")
            _note_j = _rd_j.get("Заметка", "")
            _bal_j = _rd_j.get("_balance")
            _pct_j = _rd_j.get("_pct_deposit")

            _status_color = {
                "Сгорел": "#8b1e1e", "Закрыта": "#5a6b78",
                "Открыта": "#1e88e5",
            }.get(_status, "#333")
            _status_bg = {
                "Сгорел": "#ffe6e6", "Закрыта": "#eef2f5",
                "Открыта": "#e3f2fd",
            }.get(_status, "#f2f4f6")

            _opt_color = ("#00a651" if _opt == "Call"
                          else "#d32f2f" if _opt == "Put" else "#333")
            _side_color = ("#00a651" if _side == "Покупка"
                           else "#d32f2f" if _side == "Продажа"
                           else "#333")
            _pnl_color = ("#00a651" if _pnl_j > 0
                          else "#d32f2f" if _pnl_j < 0 else "#333")
            _strike_str = _fmt_strike(_strike_j, _rd_j.get("Категория"))
            _exit_str = f"{_exit_j:.4f}" if _exit_j is not None else "—"

            with st.container(border=True):
                st.markdown(
                    f"<div style='display:flex; "
                    f"justify-content:space-between; "
                    f"align-items:baseline;'>"
                    f"<div style='font-size:1.05rem; font-weight:700;'>"
                    f"{_ticker_j} "
                    f"<span style='color:{_opt_color};'>{_opt}</span> "
                    f"<span>{_strike_str}</span></div>"
                    f"<div style='background:{_status_bg}; "
                    f"color:{_status_color}; padding:2px 8px; "
                    f"border-radius:8px; font-size:.7rem; "
                    f"font-weight:700;'>{_status}</div></div>",
                    unsafe_allow_html=True,
                )

                st.markdown(
                    f"<div style='font-size:.78rem; color:#5a6b78; "
                    f"margin-top:2px;'>"
                    f"<span style='color:{_side_color}; font-weight:700;'>"
                    f"{_side}</span> · "
                    f"{_date_j} → {_exp_j}</div>",
                    unsafe_allow_html=True,
                )

                st.markdown(
                    f"<div style='margin-top:8px; padding:8px 10px; "
                    f"background:#f6f9fc; border-radius:6px; "
                    f"font-size:.88rem;'>"
                    f"<b>P&L:</b> <span style='color:{_pnl_color}; "
                    f"font-weight:800; font-size:1rem;'>"
                    f"{_pnl_j:+,.2f} ₽</span></div>",
                    unsafe_allow_html=True,
                )

                if _bal_j is not None:
                    st.markdown(
                        f"<div style='margin-top:4px; padding:6px 10px; "
                        f"background:#f0f4f8; border-radius:6px; "
                        f"font-size:.82rem;'>"
                        f"<b>Баланс после:</b> "
                        f"<span style='color:#1a3b4f; font-weight:700;'>"
                        f"{_bal_j:,.2f} ₽</span>"
                        f"{(' · ' + f'{_pct_j:+.2f}% депо') if _pct_j is not None else ''}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                _dc1, _dc2 = st.columns(2)
                with _dc1:
                    st.markdown(
                        f"<div style='font-size:.78rem;'>"
                        f"Кол-во: <b>{_qty_j}</b><br>"
                        f"Цена входа: <b>{_entry_j:.4f}</b></div>",
                        unsafe_allow_html=True,
                    )
                with _dc2:
                    st.markdown(
                        f"<div style='font-size:.78rem;'>"
                        f"Цена выхода: <b>{_exit_str}</b><br>"
                        f"Комиссия: <b>{_comm_j:.2f} ₽</b></div>",
                        unsafe_allow_html=True,
                    )

                if _note_j:
                    st.caption(f"Заметка: {_note_j}")

    else:
        _df_for_show = _df_display.copy()
        _df_for_show["Баланс, ₽"] = _df_journal["_balance"].values
        _df_for_show["% депо"] = _df_journal["_pct_deposit"].values

        st.dataframe(
            _df_for_show.style.apply(_style_journal_row, axis=1).format({
                "Страйк": lambda v: f"{v:.2f}" if pd.notna(v) else "—",
                "Цена входа": "{:.4f}",
                "Цена выхода": lambda v: (f"{v:.4f}"
                                          if pd.notna(v) else "—"),
                "Комиссия": "{:.2f}",
                "P&L": "{:+,.2f}",
                "Баланс, ₽": lambda v: (f"{v:,.2f}"
                                        if pd.notna(v) else "—"),
                "% депо": lambda v: (f"{v:+.2f}%"
                                     if pd.notna(v) else "—"),
            }, na_rep="—"),
            use_container_width=True, hide_index=True, height=400,
        )

    # ==========================================================
    # РЕДАКТИРОВАНИЕ
    # ==========================================================
    with st.expander("Редактировать / закрыть сделку", expanded=False):
        if not st.session_state.journal_rows:
            st.info("Журнал пуст.")
        else:
            _edit_opts = [
                f"{i}: {r.get('Дата входа','?')} · {r.get('Тикер','?')} · "
                f"{r.get('Опцион','?')} {r.get('Страйк','—')} · "
                f"{r.get('Статус','?')} · P&L {r.get('P&L',0):+.2f} ₽"
                for i, r in enumerate(st.session_state.journal_rows)
            ]
            _edit_sel = st.selectbox(
                "Выберите запись", _edit_opts, key="journal_edit_sel",
            )
            if _edit_sel:
                _edit_idx = int(_edit_sel.split(":", 1)[0])
                _edit_row = st.session_state.journal_rows[_edit_idx]
                _cur_status = _edit_row.get("Статус", "Открыта")
                _cur_exit = _edit_row.get("Цена выхода")
                _cur_comm = float(_edit_row.get("Комиссия", 0.0) or 0.0)

                st.markdown(
                    f"**Редактируется:** {_edit_row.get('Тикер','?')} · "
                    f"{_edit_row.get('Направление','?')} "
                    f"{_edit_row.get('Опцион','?')} "
                    f"{_edit_row.get('Страйк','—')} × "
                    f"{_edit_row.get('Кол-во', 0)} "
                    f"по {_edit_row.get('Цена входа', 0):.4f} ₽"
                )

                _ec1, _ec2, _ec3 = st.columns(3)
                with _ec1:
                    _e_exit = st.number_input(
                        "Цена выхода",
                        min_value=0.0,
                        value=(float(_cur_exit)
                               if _cur_exit is not None else 0.0),
                        step=0.01, format="%.4f",
                        key=f"journal_edit_exit_{_edit_idx}",
                    )
                    _e_burned = st.checkbox(
                        "Сгорел (потеря премии)",
                        value=(_cur_status == "Сгорел"),
                        key=f"journal_edit_burned_{_edit_idx}",
                    )
                with _ec2:
                    _e_comm = st.number_input(
                        "Комиссия, ₽",
                        min_value=0.0, value=_cur_comm,
                        step=0.01, format="%.4f",
                        key=f"journal_edit_comm_{_edit_idx}",
                    )
                with _ec3:
                    _e_note = st.text_input(
                        "Заметка",
                        value=str(_edit_row.get("Заметка", "")),
                        key=f"journal_edit_note_{_edit_idx}",
                    )

                _bc1, _bc2, _bc3 = st.columns([1, 1, 1])
                with _bc1:
                    if st.button(
                        "Сохранить", type="primary",
                        use_container_width=True,
                        key="journal_edit_save",
                    ):
                        _sign = (1 if _edit_row["Направление"] == "Покупка"
                                 else -1)
                        _entry = float(_edit_row["Цена входа"] or 0)
                        _qty = int(_edit_row["Кол-во"] or 0)

                        if _e_burned:
                            _new_status = "Сгорел"
                            _new_exit = None
                            _new_pnl = -_sign * _entry * _qty - _e_comm
                        elif _e_exit > 0:
                            _new_status = "Закрыта"
                            _new_exit = float(_e_exit)
                            _new_pnl = (_sign * (_e_exit - _entry) * _qty
                                        - _e_comm)
                        else:
                            _new_status = "Открыта"
                            _new_exit = None
                            _new_pnl = 0.0

                        _edit_row["Цена выхода"] = _new_exit
                        _edit_row["Комиссия"] = float(_e_comm)
                        _edit_row["P&L"] = float(_new_pnl)
                        _edit_row["Статус"] = _new_status
                        _edit_row["Заметка"] = _e_note
                        st.success(f"Запись обновлена: {_new_status}, "
                                   f"P&L = {_new_pnl:+.2f} ₽")
                        st.rerun()
                with _bc2:
                    if st.button(
                        "Пометить как «сгорел»",
                        use_container_width=True,
                        key="journal_burn_quick",
                    ):
                        _sign = (1 if _edit_row["Направление"] == "Покупка"
                                 else -1)
                        _entry = float(_edit_row["Цена входа"] or 0)
                        _qty = int(_edit_row["Кол-во"] or 0)
                        _edit_row["Цена выхода"] = None
                        _edit_row["P&L"] = (-_sign * _entry * _qty
                                            - _cur_comm)
                        _edit_row["Статус"] = "Сгорел"
                        st.success("Помечено как сгоревший")
                        st.rerun()
                with _bc3:
                    if st.button(
                        "Открыть снова",
                        use_container_width=True,
                        key="journal_reopen_quick",
                    ):
                        _edit_row["Цена выхода"] = None
                        _edit_row["P&L"] = 0.0
                        _edit_row["Статус"] = "Открыта"
                        st.success("Запись снова открыта")
                        st.rerun()

    with st.expander("Удалить сделки", expanded=False):
        _del_opts = [
            f"{i}: {r.get('Дата входа','?')} · {r.get('Тикер','?')} · "
            f"{r.get('Опцион','?')} {r.get('Страйк','—')} · "
            f"{r.get('P&L',0):+.2f} ₽"
            for i, r in enumerate(st.session_state.journal_rows)
        ]
        _del_sel = st.multiselect(
            "Выберите для удаления", _del_opts, key="journal_del_sel",
        )
        if st.button(
            "Удалить выбранные", type="secondary", key="journal_del_btn",
        ):
            _idx_to_del = set(int(s.split(":", 1)[0]) for s in _del_sel)
            st.session_state.journal_rows = [
                r for i, r in enumerate(st.session_state.journal_rows)
                if i not in _idx_to_del
            ]
            st.rerun()