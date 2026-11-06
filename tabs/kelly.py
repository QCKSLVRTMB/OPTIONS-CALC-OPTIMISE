"""Вкладка «Калькулятор Келли»."""
import pandas as pd
import streamlit as st

from core.journal import _journal_get_deposit
from core.market_data import fetch_rvi_current


@st.fragment
def _render_kelly_tab():
    st.header("Калькулятор Келли")
    st.caption("Расчёт оптимального размера ставки (доли капитала) "
               "по критерию Келли.")

    with st.expander("Инструкция по использованию", expanded=False):
        st.markdown(r"""
**Что такое критерий Келли?**

Критерий Келли (Kelly Criterion) — математическая формула для определения
оптимальной доли капитала, которую следует рисковать в каждой сделке,
чтобы максимизировать долгосрочный рост капитала.

**Базовая формула (для бинарных исходов):**

$$f^* = \frac{(r + 1) \cdot p - 1}{r}$$

где:
- $f^*$ — доля капитала для ставки (в % от депозита);
- $p$ — вероятность выигрыша (0…1);
- $r$ — отношение среднего выигрыша к среднему убытку.

**Пример:** если $p = 0.44$ и $r = 2$, то
$f^* = \frac{(2+1) \cdot 0.44 - 1}{2} = 0.16$ → ставка **16%** капитала.

**Правила осторожности:**

1. Используйте дробный Келли (½ или ¼ от расчётной величины), потому
   что ваша оценка $p$ и $r$ может быть завышена, а сделки коррелированы.
2. Если $f^* \leq 0$ — система убыточна, торговать по ней нельзя.
3. Формула предполагает последовательные независимые сделки. При
   одновременных сделках используйте метод корректировки риска.
4. Критерий Келли агрессивен.
""")

    st.markdown("### 1) Источник статистики")
    _src = st.radio(
        "Откуда взять p, avg_win, avg_loss?",
        ["Из журнала", "Ввести вручную"],
        horizontal=True, key="kelly_source",
    )

    if _src == "Из журнала":
        _rows = st.session_state.get("journal_rows", [])
        _closed = [
            r for r in _rows
            if r.get("Статус") in ("Закрыта", "Сгорел")
        ]
        if not _closed:
            st.warning("В журнале нет закрытых сделок. "
                       "Переключитесь на ручной ввод или добавьте сделки.")
            return
        _wins = [r for r in _closed if (r.get("P&L") or 0) > 0]
        _losses = [r for r in _closed if (r.get("P&L") or 0) < 0]
        _p = len(_wins) / len(_closed) if _closed else 0.0
        _avg_w = (sum((r.get("P&L") or 0) for r in _wins) / len(_wins)
                  if _wins else 0.0)
        _avg_l = (abs(sum((r.get("P&L") or 0) for r in _losses)
                       / len(_losses)) if _losses else 0.0)
        if _avg_l <= 0:
            st.error("Средний убыток = 0. Невозможно вычислить r.")
            return
        _r_ratio = _avg_w / _avg_l
        st.success(
            f"Из журнала ({len(_closed)} закрытых сделок): "
            f"p = {_p:.3f}, avg_win = {_avg_w:,.2f} ₽, "
            f"avg_loss = {_avg_l:,.2f} ₽, r = {_r_ratio:.3f}"
        )

        _manual_p = _p
        _manual_avg_w = _avg_w
        _manual_avg_l = _avg_l
    else:
        _manual_p = st.number_input(
            "Вероятность выигрыша p (0…1)",
            min_value=0.0, max_value=1.0, value=0.50, step=0.01,
            format="%.3f", key="kelly_p",
        )
        _manual_avg_w = st.number_input(
            "Средний выигрыш, ₽",
            min_value=0.0, value=1000.0, step=50.0, format="%.2f",
            key="kelly_avg_w",
        )
        _manual_avg_l = st.number_input(
            "Средний убыток, ₽",
            min_value=0.01, value=500.0, step=50.0, format="%.2f",
            key="kelly_avg_l",
        )

    st.markdown("### 2) Депозит")
    _init_dep_k, _cur_dep_k, _dep_mode_k = _journal_get_deposit()
    _journal_has_rows = bool(st.session_state.get("journal_rows"))
    _use_journal_dep = _dep_mode_k == "manual" or _journal_has_rows

    if _use_journal_dep and _cur_dep_k > 0:
        _deposit = float(_cur_dep_k)
        _dep_src = ("текущий депозит журнала ("
                    + ("ручной" if _dep_mode_k == "manual"
                       else "авто по P&L")
                    + ")")
    else:
        _deposit = float(st.session_state.get("dep_inp", 100000.0))
        _dep_src = "вкладка «Позиция»"

    st.info(f"Депозит берётся из: {_dep_src} — **{_deposit:,.2f} ₽**")

    if _src == "Из журнала":
        _closed_count = len([
            r for r in st.session_state.get("journal_rows", [])
            if r.get("Статус") in ("Закрыта", "Сгорел")
        ])
        if _closed_count < 20:
            st.warning(
                f"Закрытых сделок в журнале: {_closed_count}. "
                f"Для достоверной оценки p и r рекомендуется ≥ 20 сделок. "
                f"Текущие результаты Kelly могут быть ненадёжны — "
                f"продолжайте собирать статистику."
            )
        elif _closed_count < 50:
            st.info(
                f"Закрытых сделок в журнале: {_closed_count}. "
                f"Статистика достаточная, но при 50+ сделках "
                f"точность будет выше."
            )
        else:
            st.success(
                f"Закрытых сделок в журнале: {_closed_count} — "
                f"статистика надёжна, Kelly-оценки устойчивы."
            )

    st.markdown("### 3) Результат")
    _f_full = None
    _f_half = _f_quarter = None

    if _manual_avg_l > 0 and _manual_avg_w > 0:
        _r_ratio = _manual_avg_w / _manual_avg_l
        _f_full = ((_r_ratio + 1.0) * _manual_p - 1.0) / _r_ratio
        _f_half = _f_full * 0.5
        _f_quarter = _f_full * 0.25

    if _f_full is None:
        st.error("Некорректные входные данные для расчёта.")
        return

    _color_f = ("#00a651" if _f_full > 0
                else "#d32f2f" if _f_full < 0 else "#333")

    _k1, _k2, _k3, _k4 = st.columns(4)
    with _k1:
        st.markdown(
            f"<div style='padding:12px; border-radius:10px; "
            f"background:#f0f4f8;'>"
            f"<div style='font-size:.72rem; color:#4a6f8a; "
            f"font-weight:700; text-transform:uppercase;'>"
            f"Полный Келли</div>"
            f"<div style='font-size:1.5rem; font-weight:800; "
            f"color:{_color_f}; margin-top:6px;'>"
            f"{_f_full*100:+.2f} %</div>"
            f"<div style='font-size:.8rem; color:#5a6b78; "
            f"margin-top:4px;'>{_f_full*_deposit:+,.2f} ₽</div>"
            f"</div>", unsafe_allow_html=True,
        )
    with _k2:
        st.markdown(
            f"<div style='padding:12px; border-radius:10px; "
            f"background:#f0f4f8;'>"
            f"<div style='font-size:.72rem; color:#4a6f8a; "
            f"font-weight:700; text-transform:uppercase;'>"
            f"½ Келли</div>"
            f"<div style='font-size:1.5rem; font-weight:800; "
            f"color:{_color_f}; margin-top:6px;'>"
            f"{_f_half*100:+.2f} %</div>"
            f"<div style='font-size:.8rem; color:#5a6b78; "
            f"margin-top:4px;'>{_f_half*_deposit:+,.2f} ₽</div>"
            f"</div>", unsafe_allow_html=True,
        )
    with _k3:
        st.markdown(
            f"<div style='padding:12px; border-radius:10px; "
            f"background:#f0f4f8;'>"
            f"<div style='font-size:.72rem; color:#4a6f8a; "
            f"font-weight:700; text-transform:uppercase;'>"
            f"¼ Келли</div>"
            f"<div style='font-size:1.5rem; font-weight:800; "
            f"color:{_color_f}; margin-top:6px;'>"
            f"{_f_quarter*100:+.2f} %</div>"
            f"<div style='font-size:.8rem; color:#5a6b78; "
            f"margin-top:4px;'>{_f_quarter*_deposit:+,.2f} ₽</div>"
            f"</div>", unsafe_allow_html=True,
        )
    with _k4:
        st.markdown(
            f"<div style='padding:12px; border-radius:10px; "
            f"background:#f0f4f8;'>"
            f"<div style='font-size:.72rem; color:#4a6f8a; "
            f"font-weight:700; text-transform:uppercase;'>"
            f"r (win/loss)</div>"
            f"<div style='font-size:1.5rem; font-weight:800; "
            f"color:#1a3b4f; margin-top:6px;'>{_r_ratio:.3f}</div>"
            f"<div style='font-size:.8rem; color:#5a6b78; "
            f"margin-top:4px;'>avg_win / avg_loss</div>"
            f"</div>", unsafe_allow_html=True,
        )

    _rvi_kelly_data = fetch_rvi_current()
    _rvi_kelly = (_rvi_kelly_data.get("value")
                  if _rvi_kelly_data else None)

    if _rvi_kelly is not None and _f_full is not None:
        _rvi_factor = max(0.3, 1.0 - _rvi_kelly / 100.0)

        _f_full_rvi = _f_full * _rvi_factor
        _f_half_rvi = _f_half * _rvi_factor
        _f_quarter_rvi = _f_quarter * _rvi_factor

        st.markdown("---")
        st.markdown("### RVI-поправка")
        st.markdown(
            f"<div style='padding:14px 18px; border-radius:12px; "
            f"background:#fff3e0; border-left:6px solid #e6a500; "
            f"margin:10px 0;'>"
            f"<div style='font-size:.9rem; font-weight:700; "
            f"color:#e6a500;'>RVI = {_rvi_kelly:.2f}%</div>"
            f"<div style='font-size:.82rem; color:#5a6b78; "
            f"margin-top:4px;'>Множитель: ×{_rvi_factor:.2f} "
            f"(снижение доли из-за корреляции сделок)</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        _rk1, _rk2, _rk3 = st.columns(3)
        with _rk1:
            st.metric("Полный Келли (RVI)",
                      f"{_f_full_rvi*100:+.2f} %",
                      delta=f"{_f_full_rvi*_deposit:+,.2f} ₽")
        with _rk2:
            st.metric("½ Келли (RVI)", f"{_f_half_rvi*100:+.2f} %",
                      delta=f"{_f_half_rvi*_deposit:+,.2f} ₽")
        with _rk3:
            st.metric("¼ Келли (RVI)", f"{_f_quarter_rvi*100:+.2f} %",
                      delta=f"{_f_quarter_rvi*_deposit:+,.2f} ₽")

        st.caption(
            "Формула: f_RVI = f_Келли × max(0.3, 1 − RVI/100). "
            "При высоком RVI сделки коррелированы — размер ставки снижается."
        )

    st.markdown("---")
    if _f_full <= 0:
        st.error("f* ≤ 0 — при текущих p и r система убыточна. "
                 "Критерий Келли рекомендует не торговать "
                 "по этой стратегии.")
    elif _f_full > 0.25:
        st.warning(f"Доля Келли {_f_full*100:.1f}% очень высокая. "
                   f"Рассмотрите ½ или ¼ Келли.")
    else:
        st.success(f"Доля Келли {_f_full*100:.1f}% укладывается в разумные "
                   f"рамки. Для консервативной торговли используйте "
                   f"½ или ¼ Келли.")

    with st.expander("Метод корректировки риска", expanded=False):
        _n_sim = st.number_input(
            "Число одновременных сделок",
            min_value=1, max_value=20, value=5,
            step=1, key="kelly_n_sim",
        )
        _f_used = st.selectbox(
            "Использовать долю Келли",
            ["Полный", "½", "¼"],
            index=1, key="kelly_frac_sel",
        )
        _f_map = {"Полный": _f_full, "½": _f_half, "¼": _f_quarter}
        _f_use = _f_map[_f_used]

        _rows_risk = []
        _capital = 1.0
        for i in range(int(_n_sim)):
            _invest = _capital * _f_use
            _rows_risk.append({
                "#": i + 1,
                "Доступный капитал, %": round(_capital * 100, 2),
                "Инвестировано, %": round(_invest * 100, 2),
                "Остаток, %": round((_capital - _invest) * 100, 2),
                "Сумма, ₽": round(_invest * _deposit, 2),
            })
            _capital -= _invest
        _df_risk = pd.DataFrame(_rows_risk)

        if "kelly_view_mode" not in st.session_state:
            st.session_state.kelly_view_mode = "Карточки (мобильный)"

        _view_mode_kelly = st.radio(
            "Вид таблицы риска",
            ["Таблица", "Карточки (мобильный)"],
            index=(0 if st.session_state.kelly_view_mode == "Таблица"
                   else 1),
            horizontal=True,
            key="kelly_view_mode_radio",
            label_visibility="collapsed",
        )
        st.session_state.kelly_view_mode = _view_mode_kelly

        if st.session_state.kelly_view_mode == "Карточки (мобильный)":
            for _, _row_r in _df_risk.iterrows():
                _num = int(_row_r["#"])
                _cap = float(_row_r["Доступный капитал, %"])
                _inv = float(_row_r["Инвестировано, %"])
                _rest = float(_row_r["Остаток, %"])
                _sum = float(_row_r["Сумма, ₽"])

                with st.container(border=True):
                    st.markdown(
                        f"<div style='font-size:.95rem; font-weight:700; "
                        f"color:#1a3b4f; margin-bottom:4px;'>"
                        f"Сделка #{_num}</div>"
                        f"<div style='font-size:.85rem; line-height:1.7;'>"
                        f"Доступно: <b>{_cap:.2f} %</b><br>"
                        f"Инвестировано: <b style='color:#1e88e5;'>"
                        f"{_inv:.2f} %</b><br>"
                        f"Остаток: <b>{_rest:.2f} %</b><br>"
                        f"Сумма ставки: <b style='color:#00a651;'>"
                        f"{_sum:,.2f} ₽</b>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
        else:
            st.dataframe(
                _df_risk, use_container_width=True, hide_index=True,
                height=min(120 + 35 * len(_df_risk), 400),
            )

        st.caption(f"Итоговая экспозиция при {_n_sim} сделках: "
                   f"{(1 - _capital) * 100:.2f}% "
                   f"= {(1 - _capital) * _deposit:,.2f} ₽")