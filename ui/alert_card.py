"""Карточка тикера в разделе «Оповещения»."""
import streamlit as st

from core.alerts import _level_proximity, _trend_emoji_triplet
from core.options import fetch_optionboard
from core.strategies import _calc_range_strikes, _suggest_strategy


def _render_alert_card(r):
    _ticker = r["ticker"]
    _category = r["category"]
    _last = r["market_price"]
    _lvl_buy = r["lvl_buy"]
    _lvl_sell = r["lvl_sell"]
    _buy_dev = r["buy_dev_pct"]
    _sell_dev = r["sell_dev_pct"]
    _buy_active = r["buy_active"]
    _sell_active = r["sell_active"]
    _at_buy = r.get("at_buy_today", False)
    _at_sell = r.get("at_sell_today", False)
    _high_vol = r.get("high_volume", False)
    _spike = r.get("intraday_spike", False)

    _checks_key = f"{_ticker.upper().strip()}|{_category.strip()}"
    _all_checks_card = st.session_state.get("_alert_checks", {})
    _stored_card = _all_checks_card.get(_checks_key) or {}

    _stored_buy_card = _stored_card.get("_lvl_buy")
    _stored_sell_card = _stored_card.get("_lvl_sell")
    _levels_changed_card = False
    if _stored_buy_card is not None and _stored_sell_card is not None:
        if (abs(float(_stored_buy_card) - float(_lvl_buy)) > 0.01
                or abs(float(_stored_sell_card) - float(_lvl_sell)) > 0.01):
            _levels_changed_card = True

    if _levels_changed_card:
        _all_checks_card[_checks_key] = {
            "buy": None, "sell": None,
            "_lvl_buy": _lvl_buy, "_lvl_sell": _lvl_sell,
        }
        st.session_state["_alert_checks"] = _all_checks_card
        st.session_state.pop(f"_alert_buy_check_{_checks_key}", None)
        st.session_state.pop(f"_alert_sell_check_{_checks_key}", None)
        _cur_checks = {"buy": None, "sell": None}
    else:
        _cur_checks = _stored_card or {"buy": None, "sell": None}

    _price_str = f"{_last:,.2f} ₽" if _last else "— ₽"
    _buy_dev_str = f"{_buy_dev:+.2f} %" if _buy_dev is not None else "—"
    _sell_dev_str = f"{_sell_dev:+.2f} %" if _sell_dev is not None else "—"

    _buy_bg = "#0a8f3c" if _buy_active else "#e6e9ec"
    _buy_fg = "#ffffff" if _buy_active else "#5a6b78"
    _sell_bg = "#d32f2f" if _sell_active else "#e6e9ec"
    _sell_fg = "#ffffff" if _sell_active else "#5a6b78"
    _buy_mark = "✓" if _buy_active else "·"
    _sell_mark = "✓" if _sell_active else "·"

    _lvl_buy_bg = "rgba(156, 0, 255, 0.15)"
    _lvl_buy_border = "rgba(156, 0, 255, 0.45)"
    _lvl_sell_bg = "rgba(251, 146, 240, 0.30)"
    _lvl_sell_border = "rgba(251, 146, 240, 0.65)"

    _prox_buy = _level_proximity(_last, _lvl_buy, _lvl_sell)
    _prox_sell = _level_proximity(_last, _lvl_sell, _lvl_buy)

    _at_buy_badge = (
        "<div style='margin-top:4px; padding:2px 8px; "
        "display:inline-block; border-radius:10px; "
        "background:#9c00ff; color:#fff; font-size:.62rem; "
        "font-weight:700; letter-spacing:.04em;'>"
        "ЦЕНА НА УРОВНЕ</div>"
    ) if _at_buy else ""
    _at_sell_badge = (
        "<div style='margin-top:4px; padding:2px 8px; "
        "display:inline-block; border-radius:10px; "
        "background:#d81b60; color:#fff; font-size:.62rem; "
        "font-weight:700; letter-spacing:.04em;'>"
        "ЦЕНА НА УРОВНЕ</div>"
    ) if _at_sell else ""

    def _prox_bar_html(prox, color):
        if prox is None:
            return ""
        _p = max(0.0, min(100.0, float(prox)))
        return (
            f"<div style='margin-top:5px; height:6px; "
            f"background:#e6e9ec; border-radius:3px; overflow:hidden;'>"
            f"<div style='height:100%; width:{_p:.0f}%; "
            f"background:{color}; transition:width .3s;'></div></div>"
            f"<div style='font-size:.6rem; color:#5a6b78; "
            f"margin-top:1px;'>Близость: {_p:.0f}%</div>"
        )

    with st.container(border=True):
        st.markdown(
            f"<div style='display:flex; justify-content:space-between; "
            f"align-items:baseline;'>"
            f"<div style='font-size:1.15rem; font-weight:700; color:#111; "
            f"line-height:1.1;'>{_ticker} "
            f"<span style='font-size:.85rem; margin-left:6px;'>"
            f"{_trend_emoji_triplet(r.get('trend', ''), r.get('wave', ''), r.get('tendency', ''))}"
            f"</span></div>"
            f"<div style='font-size:1.05rem; font-weight:700; color:#111;'>"
            f"{_price_str}</div></div>"
            f"<div style='font-size:.68rem; color:#5a6b78; margin-top:2px; "
            f"text-transform:uppercase; letter-spacing:.06em;'>"
            f"{_category}</div>",
            unsafe_allow_html=True,
        )

        try:
            _strikes_card = []
            try:
                _card_series = r.get("_series_code")
                _b_chk = (
                    fetch_optionboard(r["ticker"], _category, _card_series)
                    if _card_series
                    else None
                )
                if _b_chk:
                    _strikes_card = sorted({
                        float(x["strike"]) for side_key in ("call", "put")
                        for x in (_b_chk.get(side_key) or [])
                        if x.get("strike") is not None
                    })
            except Exception:
                _strikes_card = []

            _range_strikes_card = _calc_range_strikes(
                _lvl_buy, _lvl_sell, _strikes_card
            )

            _sugg_card = _suggest_strategy(
                buy_check=_cur_checks.get("buy"),
                sell_check=_cur_checks.get("sell"),
                trend=r.get("trend", ""),
                wave=r.get("wave", ""),
                tendency=r.get("tendency", ""),
                range_strikes=_range_strikes_card,
                iv_zone=None,
                skew_type=None,
            )

            if _sugg_card.get("strategy"):
                _conf_color = {
                    "high": "#00a651",
                    "medium": "#e6a500",
                    "low": "#7a7a7a",
                }.get(_sugg_card["confidence"], "#7a7a7a")
                st.markdown(
                    f"<div style='margin-top:6px; padding:4px 8px; "
                    f"border-radius:6px; background:#f0f4f8; "
                    f"border-left:3px solid {_conf_color}; "
                    f"font-size:.7rem; color:#2c506d;'>"
                    f"<b>Рекомендация:</b> {_sugg_card['direction_ui']}"
                    f"<span style='color:#5a6b78;'> — "
                    f"{_sugg_card['rationale']}</span></div>",
                    unsafe_allow_html=True,
                )
        except Exception:
            pass

        _vol_badges = []
        if _high_vol:
            _vol_badges.append(
                "<div style='padding:3px 8px; border-radius:10px; "
                "background:#d32f2f; color:#fff; font-size:.68rem; "
                "font-weight:700; letter-spacing:.03em; display:inline-block; "
                "margin-right:6px;'>ВЫСОКИЙ ОБЪЁМ</div>"
            )
        if _spike:
            _vol_badges.append(
                "<div style='padding:3px 8px; border-radius:10px; "
                "background:#ff6f00; color:#fff; font-size:.68rem; "
                "font-weight:700; letter-spacing:.03em; display:inline-block;'>"
                "ВСПЛЕСК ОБЪЁМА</div>"
            )
        if _vol_badges:
            st.markdown(
                f"<div style='margin-top:6px;'>{''.join(_vol_badges)}</div>",
                unsafe_allow_html=True,
            )

        st.markdown(
            "<div style='margin-top:8px; font-size:.66rem; color:#2c506d; "
            "font-weight:700; text-transform:uppercase; "
            "letter-spacing:.05em;'>Ожидание движения стрелой</div>",
            unsafe_allow_html=True,
        )

        _cur_buy = _cur_checks.get("buy")
        _cur_sell = _cur_checks.get("sell")

        _buy_opts = ["—", "1", "2"]
        _buy_idx = 0
        if _cur_buy == "1":
            _buy_idx = 1
        elif _cur_buy == "2":
            _buy_idx = 2

        _sell_opts = ["—", "3", "4"]
        _sell_idx = 0
        if _cur_sell == "3":
            _sell_idx = 1
        elif _cur_sell == "4":
            _sell_idx = 2

        _opt_labels = {
            "—": "—",
            "1": "**1** покупка :green-background[**CALL**]",
            "2": "**2** продажа :red-background[**PUT**]",
            "3": "**3** продажа :green-background[**CALL**]",
            "4": "**4** покупка :red-background[**PUT**]",
        }

        def _fmt_opt(v):
            return _opt_labels.get(v, v)

        _lc1, _lc2 = st.columns(2)
        with _lc1:
            st.markdown(
                f"<div style='background:{_lvl_buy_bg}; "
                f"border:1px solid {_lvl_buy_border}; border-radius:6px; "
                f"padding:7px 9px;'>"
                f"<div style='display:flex; justify-content:space-between; align-items:center;'>"
                f"<div style='font-size:.6rem; color:#333; font-weight:700; "
                f"text-transform:uppercase; letter-spacing:.06em;'>Покупка</div>"
                f"<div style='font-size:.65rem; font-weight:700; "
                f"color:{_buy_fg}; background:{_buy_bg}; "
                f"padding:2px 6px; border-radius:10px;'>{_buy_mark}</div>"
                f"</div>"
                f"<div style='font-size:1.02rem; font-weight:700; color:#111; "
                f"line-height:1.2; margin-top:2px;'>{_lvl_buy:.2f}</div>"
                f"<div style='font-size:.76rem; color:#333; margin-top:1px;'>"
                f"{_buy_dev_str}</div>"
                f"{_at_buy_badge}"
                f"{_prox_bar_html(_prox_buy, '#9c00ff')}"
                f"</div>",
                unsafe_allow_html=True,
            )

            _buy_choice = st.radio(
                "buy_group",
                _buy_opts,
                index=_buy_idx,
                key=f"_alert_buy_check_{_checks_key}",
                horizontal=True,
                format_func=_fmt_opt,
                label_visibility="collapsed",
            )

        with _lc2:
            st.markdown(
                f"<div style='background:{_lvl_sell_bg}; "
                f"border:1px solid {_lvl_sell_border}; border-radius:6px; "
                f"padding:7px 9px;'>"
                f"<div style='display:flex; justify-content:space-between; align-items:center;'>"
                f"<div style='font-size:.6rem; color:#333; font-weight:700; "
                f"text-transform:uppercase; letter-spacing:.06em;'>Продажа</div>"
                f"<div style='font-size:.65rem; font-weight:700; "
                f"color:{_sell_fg}; background:{_sell_bg}; "
                f"padding:2px 6px; border-radius:10px;'>{_sell_mark}</div>"
                f"</div>"
                f"<div style='font-size:1.02rem; font-weight:700; color:#111; "
                f"line-height:1.2; margin-top:2px;'>{_lvl_sell:.2f}</div>"
                f"<div style='font-size:.76rem; color:#333; margin-top:1px;'>"
                f"{_sell_dev_str}</div>"
                f"{_at_sell_badge}"
                f"{_prox_bar_html(_prox_sell, '#d81b60')}"
                f"</div>",
                unsafe_allow_html=True,
            )

            _sell_choice = st.radio(
                "sell_group",
                _sell_opts,
                index=_sell_idx,
                key=f"_alert_sell_check_{_checks_key}",
                horizontal=True,
                format_func=_fmt_opt,
                label_visibility="collapsed",
            )

        _new_checks = st.session_state.get("_alert_checks", {})
        if _checks_key not in _new_checks:
            _new_checks[_checks_key] = {}
        _new_checks[_checks_key]["buy"] = (
            None if _buy_choice == "—" else _buy_choice
        )
        _new_checks[_checks_key]["sell"] = (
            None if _sell_choice == "—" else _sell_choice
        )
        _new_checks[_checks_key]["_lvl_buy"] = _lvl_buy
        _new_checks[_checks_key]["_lvl_sell"] = _lvl_sell
        st.session_state["_alert_checks"] = _new_checks