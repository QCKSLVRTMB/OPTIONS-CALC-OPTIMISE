"""Вкладка «Доска опционов»: сводка серий, доска, улыбка волатильности."""
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.instruments import resolve_canonical_asset_code
from core.market_data import (
    format_series_label,
    classify_iv_vs_rvi,
    fetch_rvi_current,
)
from core.options import (
    fetch_optionseries,
    fetch_series_overview,
    fetch_optionboard,
    fetch_volatility_graph,
    find_alert_levels,
)
from core.alerts import (
    find_alert_full_info,
    get_alert_checks,
    build_alert_targets,
)
from core.strategies import (
    _calc_range_strikes,
    _suggest_strategy,
    _suggestion_legs,
)
from core.pricing import _get_current_market_price_for_asset
from core.helpers import _fmt_strike, push_strikes_to_calculator


@st.fragment
def _render_board_tab():
    st.header("Доска опционов и кривая волатильности")

    if "board_asset" not in st.session_state:
        st.session_state.board_asset = "SBER"
    if "board_category" not in st.session_state:
        st.session_state.board_category = "Акция"
    if "board_series_list" not in st.session_state:
        st.session_state.board_series_list = []
    if "board_series_code" not in st.session_state:
        st.session_state.board_series_code = ""
    if "board_expiry" not in st.session_state:
        st.session_state.board_expiry = ""
    if "board_autoloaded_for" not in st.session_state:
        st.session_state.board_autoloaded_for = (None, None)

    _bc1, _bc2, _bc3 = st.columns([3, 2, 2])
    with _bc1:
        _board_raw_asset = st.text_input(
            "Базовый актив", value=st.session_state.board_asset,
            key="board_asset_input", placeholder="SBER, GAZP, RTS…",
        ).strip()
    with _bc2:
        _cat_options = ["Акция", "Фьючерс", "Валюта", "Товар", "Индекс"]
        _cat_idx = (_cat_options.index(st.session_state.board_category)
                    if st.session_state.board_category in _cat_options else 0)
        _board_cat = st.selectbox(
            "Категория БА", _cat_options, index=_cat_idx,
            key="board_category_select",
        )
    with _bc3:
        st.markdown("<div style='height:1.5rem'></div>", unsafe_allow_html=True)
        _btn_reload = st.button(
            "Обновить серии", use_container_width=True,
            key="board_reload_btn",
        )

    _board_asset = resolve_canonical_asset_code(_board_raw_asset, _board_cat)

    if (_board_asset != st.session_state.board_asset
            or _board_cat != st.session_state.board_category):
        st.session_state.board_asset = _board_asset
        st.session_state.board_category = _board_cat
        st.session_state.board_series_list = []
        st.session_state.board_series_code = ""
        st.session_state.board_expiry = ""
        st.session_state.board_autoloaded_for = (None, None)

    _board_loaded_for = st.session_state.get(
        "board_autoloaded_for", (None, None)
    )
    _need_reload = (_board_asset
                    and (_board_asset, _board_cat) != _board_loaded_for) \
                   or _btn_reload

    if _need_reload and _board_asset:
        with st.spinner(f"Загрузка серий для {_board_asset}…"):
            try:
                _new_series = fetch_optionseries(_board_asset, _board_cat)
                st.session_state.board_series_list = _new_series or []
                st.session_state.board_autoloaded_for = (
                    _board_asset, _board_cat
                )
                if _new_series and not st.session_state.board_series_code:
                    _s_first = sorted(
                        _new_series, key=lambda x: x.get("expiry", "")
                    )[0]
                    st.session_state.board_series_code = _s_first["code"]
                    st.session_state.board_expiry = _s_first["expiry"]
                if not _new_series:
                    st.warning(f"ISS не вернул серий для {_board_asset}")
            except Exception as e:
                st.error(f"Ошибка загрузки серий: {e}")
                st.session_state.board_series_list = []

    st.markdown("---")

    if not st.session_state.board_series_list:
        st.info("Нет доступных серий.")
        return

    st.markdown(f"### Все серии опционов {_board_asset} ({_board_cat.lower()})")

    try:
        _df_overview = fetch_series_overview(_board_asset, _board_cat)
    except Exception as _e:
        _df_overview = pd.DataFrame()
        st.warning(f"Не удалось загрузить сводку серий: {_e}")

    if not _df_overview.empty:
        _cols_to_drop = [
            c for c in ("_series_code", "_expiry_raw", "_days_to",
                        "_series_type")
            if c in _df_overview.columns
        ]
        _df_show = _df_overview.drop(columns=_cols_to_drop)
        _df_styler = _df_show.style.format({
            "Объём, ₽": "{:,.0f}",
            "Об.контр.": "{:,.0f}",
            "Откр.поз.": "{:,.0f}",
            "Изменение, ₽": "{:+,.0f}",
        }, na_rep="—").apply(
            lambda row: [
                ("color: #00a651; font-weight: 700;"
                 if col == "Изменение, ₽"
                 and isinstance(row[col], (int, float)) and row[col] > 0
                 else ("color: #d32f2f; font-weight: 700;"
                       if col == "Изменение, ₽"
                       and isinstance(row[col], (int, float)) and row[col] < 0
                       else ""))
                for col in row.index
            ], axis=1,
        )

        _sel_event = st.dataframe(
            _df_styler, use_container_width=True, hide_index=True,
            height=min(120 + 35 * len(_df_show), 420),
            on_select="rerun", selection_mode="single-row",
            key="series_overview_table",
        )

        _sel_rows = []
        try:
            _sel_rows = list(getattr(_sel_event.selection, "rows", []) or [])
        except Exception:
            _sel_rows = []

        if _sel_rows:
            _row_idx = _sel_rows[0]
            if 0 <= _row_idx < len(_df_overview):
                _picked = _df_overview.iloc[_row_idx]
                _new_code = _picked["_series_code"]
                _new_expiry = _picked["_expiry_raw"]
                if _new_code != st.session_state.board_series_code:
                    st.session_state.board_series_code = _new_code
                    st.session_state.board_expiry = _new_expiry
                    st.rerun()

    st.markdown("---")

    _active_series_code = st.session_state.board_series_code
    _active_expiry = st.session_state.board_expiry

    if not _active_series_code:
        st.info("Выберите серию из таблицы выше.")
        return

    st.markdown(f"### Доска опционов — {_board_asset} "
                f"(дата исполнения: {format_series_label(_active_expiry)})")

    _sugg_board = None
    _alert_targets_board = None
    _board_for_sugg = None
    try:
        try:
            _board_for_sugg = fetch_optionboard(
                _board_asset, _board_cat, _active_series_code
            )
        except Exception:
            _board_for_sugg = None

        if _board_for_sugg:
            _strikes_sugg = sorted({
                float(x["strike"]) for side_key in ("call", "put")
                for x in (_board_for_sugg.get(side_key) or [])
                if x.get("strike") is not None
            })
            _lvl_sugg = find_alert_levels(_board_asset, _board_cat)
            _cp_sugg = _get_current_market_price_for_asset(
                _board_asset, _board_cat
            )
            _range_strikes_sugg = _calc_range_strikes(
                _lvl_sugg.get("buy"), _lvl_sugg.get("sell"), _strikes_sugg
            )
            _alert_info_sugg = find_alert_full_info(
                _board_asset, _board_cat
            )
            _checks_board = get_alert_checks(_board_asset, _board_cat)
            _sugg_board = _suggest_strategy(
                buy_check=_checks_board.get("buy"),
                sell_check=_checks_board.get("sell"),
                trend=(_alert_info_sugg or {}).get("trend", ""),
                wave=(_alert_info_sugg or {}).get("wave", ""),
                tendency=(_alert_info_sugg or {}).get("tendency", ""),
                range_strikes=_range_strikes_sugg,
                iv_zone=None, skew_type=None,
            )

            if _sugg_board.get("strategy"):
                _alert_targets_board = build_alert_targets(
                    _board_asset, _board_cat, _active_series_code,
                    _board_for_sugg, _lvl_sugg, current_price=_cp_sugg,
                )
    except Exception:
        _sugg_board = None

    _filter_by_sugg = False
    _sugg_strikes_set = set()
    if _sugg_board and _sugg_board.get("strategy") and _alert_targets_board:
        _legs_board = _suggestion_legs(
            _sugg_board["strategy"], _alert_targets_board
        )
        _sugg_strikes_set = {
            leg["strike"] for leg in _legs_board
            if leg.get("strike") is not None
        }

        _fc1, _fc2 = st.columns([3, 2])
        with _fc1:
            st.markdown(
                f"<div style='padding:10px 14px; border-radius:8px; "
                f"background:#eef6fb; border-left:4px solid #1e88e5; "
                f"font-size:.85rem;'>"
                f"<b>Рекомендация:</b> {_sugg_board['direction_ui']}<br>"
                f"<span style='color:#5a6b78; font-size:.8rem;'>"
                f"{_sugg_board['rationale']}</span></div>",
                unsafe_allow_html=True,
            )
        with _fc2:
            _filter_by_sugg = st.toggle(
                "Показать только страйки под конструкцию",
                value=False,
                key="board_filter_by_sugg",
            )

    _rvi_board_data = fetch_rvi_current()
    _rvi_board_val = (_rvi_board_data.get("value")
                      if _rvi_board_data else None)

    if _rvi_board_val is not None:
        _rvi_b_chg = _rvi_board_data.get("change") or 0.0
        st.markdown(
            f"<div style='background:#f0f4f8; border-radius:8px; "
            f"padding:8px 14px; margin-bottom:6px; margin-top:6px; "
            f"font-size:.9rem;'>"
            f"<b>RVI (рыночный фон):</b> "
            f"<b>{_rvi_board_val:.2f}%</b> "
            f"({_rvi_b_chg:+.2f} п.п.) — "
            f"IV на доске сравнивается с этим уровнем"
            f"</div>",
            unsafe_allow_html=True,
        )

        st.markdown(
            "<div style='background:#ffffff; border:1px solid #cfdfe9; "
            "border-radius:8px; padding:8px 14px; margin-bottom:12px; "
            "font-size:.82rem; display:flex; gap:18px; flex-wrap:wrap; "
            "align-items:center;'>"
            "<span style='color:#4a6f8a; font-weight:700;'>"
            "Легенда IV (IV / RVI):</span>"
            "<span><b style='color:#d32f2f;'>&gt; 2.0</b> "
            "<span style='color:#5a6b78;'>— продавать волатильность</span></span>"
            "<span><b style='color:#e6a500;'>1.5 … 2.0</b> "
            "<span style='color:#5a6b78;'>— осторожно</span></span>"
            "<span><b style='color:#7a7a7a;'>0.7 … 1.5</b> "
            "<span style='color:#5a6b78;'>— норма</span></span>"
            "<span><b style='color:#00a651;'>0.5 … 0.7</b> "
            "<span style='color:#5a6b78;'>— покупать волатильность</span></span>"
            "<span><b style='color:#1e88e5;'>&lt; 0.5</b> "
            "<span style='color:#5a6b78;'>— покупать (дёшево)</span></span>"
            "</div>",
            unsafe_allow_html=True,
        )

    _board_levels = find_alert_levels(_board_asset, category=_board_cat)
    _board_buy_level = (float(_board_levels["buy"] or 0)
                        if _board_levels["found"] else 0.0)
    _board_sell_level = (float(_board_levels["sell"] or 0)
                         if _board_levels["found"] else 0.0)

    try:
        _board_for_price = fetch_optionboard(
            _board_asset, _board_cat, _active_series_code
        )
        _calls_p = _board_for_price.get('call') or []
        _puts_p = _board_for_price.get('put') or []
        _c_map_p = {c['strike']: c for c in _calls_p
                    if c.get('theorprice') and c.get('strike') is not None}
        _p_map_p = {p2['strike']: p2 for p2 in _puts_p
                    if p2.get('theorprice') and p2.get('strike') is not None}
        _common_p = sorted(set(_c_map_p.keys()) & set(_p_map_p.keys()))
        _fs_est = []
        for k in _common_p:
            ct = _c_map_p[k]['theorprice']
            pt = _p_map_p[k]['theorprice']
            if ct and pt and ct > 0 and pt > 0:
                _fs_est.append(ct - pt + float(k))
        _f_current = None
        if _fs_est:
            _fs_est.sort()
            _f_current = _fs_est[len(_fs_est) // 2]
        _central_p = _board_for_price.get('central_strike')
        _strikes_p = sorted({
            c['strike'] for c in _calls_p if c.get('strike') is not None
        } | {
            p2['strike'] for p2 in _puts_p if p2.get('strike') is not None
        })
        _k_min = _strikes_p[0] if _strikes_p else None
        _k_max = _strikes_p[-1] if _strikes_p else None

        _info_parts = []
        if _f_current is not None:
            _info_parts.append(
                f"<span style='color:#1c5a7a; font-weight:700;'>"
                f"Текущая цена БА: {_f_current:,.2f} ₽</span>"
            )
        if _central_p is not None:
            _info_parts.append(
                f"<span style='color:#4a6f8a;'>"
                f"Центральный страйк: <b>{_fmt_strike(_central_p, _board_cat)}</b></span>"
            )
        if _k_min is not None and _k_max is not None:
            _info_parts.append(
                f"<span style='color:#4a6f8a;'>"
                f"Диапазон страйков: <b>{_fmt_strike(_k_min, _board_cat)} … "
                f"{_fmt_strike(_k_max, _board_cat)}</b> "
                f"({len(_strikes_p)} шт.)</span>"
            )
        if _info_parts:
            st.markdown(
                "<div style='background:#eef6fb; border-radius:12px; "
                "padding:10px 16px; margin-bottom:12px; font-size:.9rem; "
                "display:flex; gap:24px; flex-wrap:wrap; "
                "align-items:center;'>"
                + " · ".join(_info_parts) + "</div>",
                unsafe_allow_html=True,
            )
    except Exception:
        pass

    highlight_on = st.toggle(
        "Раскрасить Bid/Offer (выгодные цены) и греки", value=True
    )

    try:
        board = fetch_optionboard(_board_asset, _board_cat,
                                   _active_series_code)
    except Exception as e:
        st.error(f"Не удалось загрузить доску: {e}")
        board = None

    if board:
        calls = board.get('call') or []
        puts = board.get('put') or []
        central = board.get('central_strike')
        strikes = sorted({c['strike'] for c in calls}
                         | {p['strike'] for p in puts})
        c_map = {c['strike']: c for c in calls}
        p_map = {p['strike']: p for p in puts}

        _all_gammas = []
        _all_vegas = []
        for _k in strikes:
            for _src in (c_map.get(_k, {}), p_map.get(_k, {})):
                _g = _src.get("gamma")
                _v = _src.get("vega")
                if isinstance(_g, (int, float)) and _g > 0:
                    _all_gammas.append(abs(float(_g)))
                if isinstance(_v, (int, float)) and _v > 0:
                    _all_vegas.append(abs(float(_v)))
        _median_gamma = float(np.median(_all_gammas)) if _all_gammas else 0.0
        _median_vega = float(np.median(_all_vegas)) if _all_vegas else 0.0

        def nearest_strike(level, strikes_list):
            if level is None or level <= 0 or not strikes_list:
                return None
            return min(
                strikes_list, key=lambda s: abs(float(s) - float(level))
            )

        buy_strike_match = nearest_strike(_board_buy_level, strikes)
        sell_strike_match = nearest_strike(_board_sell_level, strikes)

        strikes_iv = []
        for k in strikes:
            c = c_map.get(k, {})
            p = p_map.get(k, {})
            iv = c.get('volatility') or p.get('volatility')
            strikes_iv.append({
                "strike": int(k) if float(k).is_integer() else k,
                "iv": float(iv) if iv is not None else None,
            })
        push_strikes_to_calculator(strikes_iv, central)

        rows = []
        for k in strikes:
            c = c_map.get(k, {})
            p = p_map.get(k, {})
            iv = c.get('volatility') or p.get('volatility')
            rows.append({
                "Call_Ticker": c.get('secid', '—'),
                "Call_Rho": c.get('rho'),
                "Call_Theta": c.get('theta'),
                "Call_Vega": c.get('vega'),
                "Call_Gamma": c.get('gamma'),
                "Call_Delta": c.get('delta'),
                "Call_Theor": c.get('theorprice'),
                "Call_Last": c.get('last'),
                "Call_Offer": c.get('offer'),
                "Call_Bid": c.get('bid'),
                "Strike": k,
                "IV_%": iv,
                "Put_Bid": p.get('bid'),
                "Put_Offer": p.get('offer'),
                "Put_Last": p.get('last'),
                "Put_Theor": p.get('theorprice'),
                "Put_Delta": p.get('delta'),
                "Put_Gamma": p.get('gamma'),
                "Put_Vega": p.get('vega'),
                "Put_Theta": p.get('theta'),
                "Put_Rho": p.get('rho'),
                "Put_Ticker": p.get('secid', '—'),
            })
        df = pd.DataFrame(rows)

        if _filter_by_sugg and _sugg_strikes_set:
            df = df[df["Strike"].isin(_sugg_strikes_set)].reset_index(drop=True)
            if df.empty:
                st.warning(
                    "Под рекомендованную конструкцию не найдено страйков"
                )
                return

        def _greek_signal_color(greek_name, row, side,
                                 buy_active, sell_active):
            _COL_BUY = "#9c00ff"
            _COL_SELL = "#fb92f0"
            _key_map = {"delta": "Delta", "gamma": "Gamma",
                        "vega": "Vega", "theta": "Theta"}
            _col_key = f"{side}_{_key_map.get(greek_name, '')}"
            _v = row.get(_col_key)
            if _v is None:
                return None
            try:
                _va = abs(float(_v))
            except (TypeError, ValueError):
                return None

            if greek_name == "delta":
                if buy_active and 0.20 <= _va <= 0.45:
                    return _COL_BUY
                if sell_active and 0.45 <= _va <= 0.70:
                    return _COL_SELL
                return None
            if greek_name == "gamma":
                if buy_active and _median_gamma > 0 \
                        and _va >= 1.5 * _median_gamma:
                    return _COL_BUY
                return None
            if greek_name == "vega":
                if _median_vega <= 0:
                    return None
                if buy_active and _va >= 1.2 * _median_vega:
                    return _COL_BUY
                if sell_active and _va <= 0.8 * _median_vega:
                    return _COL_SELL
                return None
            if greek_name == "theta":
                _vv = row.get(f"{side}_Vega")
                if _vv is None:
                    return None
                try:
                    _vva = abs(float(_vv))
                except (TypeError, ValueError):
                    return None
                if _vva < 1e-9:
                    return None
                _ratio = _va / _vva
                if buy_active and _ratio <= 0.7:
                    return _COL_BUY
                if sell_active and _ratio >= 1.2:
                    return _COL_SELL
                return None
            return None

        def _bid_offer_color(price, theor, is_bid):
            if price is None or theor is None:
                return None
            if not isinstance(price, (int, float)) \
                    or not isinstance(theor, (int, float)):
                return None
            if price <= 0 or theor <= 0:
                return None
            if is_bid:
                if price > theor:
                    return "#fb92f0"
            else:
                if price < theor:
                    return "#9c00ff"
            return None

        def style_row(row):
            strike = float(row["Strike"])
            is_central = (central is not None
                          and abs(strike - float(central)) < 0.01)
            is_buy_strike = (
                buy_strike_match is not None
                and abs(strike - float(buy_strike_match)) < 0.01
            )
            is_sell_strike = (
                sell_strike_match is not None
                and abs(strike - float(sell_strike_match)) < 0.01
            )
            call_bg = "#e1e3fb" if is_central else "#dbf3df"
            put_bg = "#fee5cd" if is_central else "#ffcdce"

            styles = []
            for col in row.index:
                style = ""
                if col.startswith("Call_"):
                    style = f"background-color: {call_bg};"
                elif col.startswith("Put_"):
                    style = f"background-color: {put_bg};"
                if col == "Strike":
                    if is_sell_strike:
                        style = ("background-color: #fb92f0; color: white; "
                                 "font-weight: bold")
                    elif is_buy_strike:
                        style = ("background-color: #9c00ff; color: white; "
                                 "font-weight: bold")
                    elif is_central:
                        style = "background-color: #e3e7ec; font-weight: bold"
                elif col == "IV_%":
                    if is_central:
                        style = "background-color: #e3e7ec; font-weight: bold"
                    else:
                        _iv_val = row.get("IV_%")
                        if _iv_val is not None and _rvi_board_val:
                            try:
                                _iv_f = float(_iv_val)
                                _zone = classify_iv_vs_rvi(
                                    _iv_f, _rvi_board_val
                                )
                            except (TypeError, ValueError):
                                _zone = None
                            if _zone and _zone.get("zone") != "normal":
                                _zcolor = _zone.get("zone_color", "")
                                if _zcolor:
                                    style = (style
                                             + f" color: {_zcolor};"
                                             + " font-weight: 700;")

                if highlight_on:
                    try:
                        _c_val = (float(central)
                                  if central is not None else None)
                    except (TypeError, ValueError):
                        _c_val = None
                    _is_atm = (_c_val is not None
                               and abs(strike - _c_val) < 0.01)

                    if col in ("Call_Bid", "Put_Bid"):
                        if _c_val is None or _is_atm:
                            _allow_bid = True
                        else:
                            _allow_bid = strike > _c_val
                        if _allow_bid:
                            theor_col = ("Call_Theor"
                                         if col.startswith("Call_")
                                         else "Put_Theor")
                            c = _bid_offer_color(row[col],
                                                  row.get(theor_col),
                                                  is_bid=True)
                            if c:
                                style = (f"background-color: {c}; "
                                         f"color: white; font-weight: 700")
                    elif col in ("Call_Offer", "Put_Offer"):
                        if _c_val is None or _is_atm:
                            _allow_offer = True
                        else:
                            _allow_offer = strike < _c_val
                        if _allow_offer:
                            theor_col = ("Call_Theor"
                                         if col.startswith("Call_")
                                         else "Put_Theor")
                            c = _bid_offer_color(row[col],
                                                  row.get(theor_col),
                                                  is_bid=False)
                            if c:
                                style = (f"background-color: {c}; "
                                         f"color: white; font-weight: 700")
                    elif col in ("Call_Delta", "Call_Gamma",
                                 "Call_Vega", "Call_Theta",
                                 "Put_Delta", "Put_Gamma",
                                 "Put_Vega", "Put_Theta"):
                        _g_side = "Call" if col.startswith("Call_") else "Put"
                        _g_name = col.split("_", 1)[1].lower()
                        if _g_side == "Call":
                            _theor = row.get("Call_Theor")
                            _bid_val = row.get("Call_Bid")
                            _off_val = row.get("Call_Offer")
                        else:
                            _theor = row.get("Put_Theor")
                            _bid_val = row.get("Put_Bid")
                            _off_val = row.get("Put_Offer")
                        _buy_a = (
                            _off_val is not None
                            and _bid_offer_color(_off_val, _theor,
                                                  is_bid=False) is not None
                            and (_c_val is None or _is_atm
                                 or strike < _c_val)
                        )
                        _sell_a = (
                            _bid_val is not None
                            and _bid_offer_color(_bid_val, _theor,
                                                  is_bid=True) is not None
                            and (_c_val is None or _is_atm
                                 or strike > _c_val)
                        )
                        if _buy_a or _sell_a:
                            _gc = _greek_signal_color(
                                _g_name, row, _g_side, _buy_a, _sell_a
                            )
                            if _gc:
                                style = (f"background-color: {_gc}; "
                                         f"color: white; font-weight: 700")

                styles.append(style)
            return styles

        column_display = {
            "Call_Ticker": "Тикер", "Call_Rho": "Ро",
            "Call_Theta": "Тета", "Call_Vega": "Вега",
            "Call_Gamma": "Гамма", "Call_Delta": "Дельта",
            "Call_Theor": "Теор.Ц", "Call_Last": "Посл.Ц",
            "Call_Offer": "Offer", "Call_Bid": "Bid",
            "Strike": "Страйк", "IV_%": "IV%",
            "Put_Bid": "Bid", "Put_Offer": "Offer",
            "Put_Last": "Посл.Ц", "Put_Theor": "Теор.Ц",
            "Put_Delta": "Дельта", "Put_Gamma": "Гамма",
            "Put_Vega": "Вега", "Put_Theta": "Тета",
            "Put_Rho": "Ро", "Put_Ticker": "Тикер",
        }

        caption_extra = ""
        if buy_strike_match is not None:
            caption_extra += (f" · страйк покупок ≈ "
                              f"{_fmt_strike(buy_strike_match, _board_cat)}")
        if sell_strike_match is not None:
            caption_extra += (f" · страйк продаж ≈ "
                              f"{_fmt_strike(sell_strike_match, _board_cat)}")
        st.caption(
            f"Центральный страйк: "
            f"{_fmt_strike(central, _board_cat) if central is not None else 'не определён'} · "
            f"всего страйков: {len(df)}{caption_extra}"
        )

        if "board_view_mode" not in st.session_state:
            st.session_state.board_view_mode = "Таблица"
        _view_mode_board = st.radio(
            "Вид доски",
            ["Таблица", "Карточки (мобильный)"],
            index=(0 if st.session_state.board_view_mode == "Таблица" else 1),
            horizontal=True,
            key="board_view_mode_radio",
            label_visibility="collapsed",
        )
        st.session_state.board_view_mode = _view_mode_board

        _format_map = {
            "Strike": "{:.2f}",
            "IV_%": "{:.2f}",
            "Call_Rho": "{:.2f}", "Call_Theta": "{:.2f}",
            "Call_Vega": "{:.2f}", "Call_Gamma": "{:.2f}",
            "Call_Delta": "{:.2f}", "Call_Theor": "{:.2f}",
            "Call_Last": "{:.2f}", "Call_Offer": "{:.2f}",
            "Call_Bid": "{:.2f}",
            "Put_Bid": "{:.2f}", "Put_Offer": "{:.2f}",
            "Put_Last": "{:.2f}", "Put_Theor": "{:.2f}",
            "Put_Delta": "{:.2f}", "Put_Gamma": "{:.2f}",
            "Put_Vega": "{:.2f}", "Put_Theta": "{:.2f}",
            "Put_Rho": "{:.2f}",
        }

        if st.session_state.board_view_mode == "Карточки (мобильный)":
            _is_atm = None
            if central is not None:
                try:
                    _is_atm = float(central)
                except (TypeError, ValueError):
                    _is_atm = None

            def _fmt_p(v):
                if v is None:
                    return "—"
                try:
                    return f"{float(v):.2f}"
                except (TypeError, ValueError):
                    return "—"

            def _fmt_g(v):
                if v is None:
                    return "—"
                try:
                    return f"{float(v):+.2f}"
                except (TypeError, ValueError):
                    return "—"

            def _greek_cls(gname, side, buy_active, sell_active, row_dict):
                _key_map = {"delta": "Delta", "gamma": "Gamma",
                            "vega": "Vega", "theta": "Theta"}
                _col = f"{side}_{_key_map.get(gname, '')}"
                _v = row_dict.get(_col)
                if _v is None:
                    return ""
                try:
                    _va = abs(float(_v))
                except (TypeError, ValueError):
                    return ""
                _BUY = "#9c00ff"
                _SELL = "#fb92f0"
                if gname == "delta":
                    if buy_active and 0.20 <= _va <= 0.45:
                        return _BUY
                    if sell_active and 0.45 <= _va <= 0.70:
                        return _SELL
                    return ""
                if gname == "gamma":
                    if buy_active and _median_gamma > 0 \
                            and _va >= 1.5 * _median_gamma:
                        return _BUY
                    return ""
                if gname == "vega":
                    if _median_vega <= 0:
                        return ""
                    if buy_active and _va >= 1.2 * _median_vega:
                        return _BUY
                    if sell_active and _va <= 0.8 * _median_vega:
                        return _SELL
                    return ""
                if gname == "theta":
                    _vv = row_dict.get(f"{side}_Vega")
                    if _vv is None:
                        return ""
                    try:
                        _vva = abs(float(_vv))
                    except (TypeError, ValueError):
                        return ""
                    if _vva < 1e-9:
                        return ""
                    _r = _va / _vva
                    if buy_active and _r <= 0.7:
                        return _BUY
                    if sell_active and _r >= 1.2:
                        return _SELL
                    return ""
                return ""

            for _idx_card, _row_card in df.iterrows():
                _rd = _row_card.to_dict()
                _k_val = _rd.get("Strike")
                _is_central = (_is_atm is not None
                               and _k_val is not None
                               and abs(float(_k_val) - _is_atm) < 0.01)
                _is_buy_strike = (
                    buy_strike_match is not None
                    and _k_val is not None
                    and abs(float(_k_val) - float(buy_strike_match)) < 0.01
                )
                _is_sell_strike = (
                    sell_strike_match is not None
                    and _k_val is not None
                    and abs(float(_k_val) - float(sell_strike_match)) < 0.01
                )

                _theor_c = _rd.get("Call_Theor")
                _bid_c = _rd.get("Call_Bid")
                _off_c = _rd.get("Call_Offer")
                _theor_p = _rd.get("Put_Theor")
                _bid_p = _rd.get("Put_Bid")
                _off_p = _rd.get("Put_Offer")

                _is_atm_val = (_is_atm is not None
                               and _k_val is not None
                               and abs(float(_k_val) - _is_atm) < 0.01)

                _buy_c = (
                    _off_c is not None
                    and _bid_offer_color(_off_c, _theor_c,
                                          is_bid=False) is not None
                    and (_is_atm is None or _is_atm_val
                         or float(_k_val) < _is_atm)
                )
                _sell_c = (
                    _bid_c is not None
                    and _bid_offer_color(_bid_c, _theor_c,
                                          is_bid=True) is not None
                    and (_is_atm is None or _is_atm_val
                         or float(_k_val) > _is_atm)
                )
                _buy_p = (
                    _off_p is not None
                    and _bid_offer_color(_off_p, _theor_p,
                                          is_bid=False) is not None
                    and (_is_atm is None or _is_atm_val
                         or float(_k_val) < _is_atm)
                )
                _sell_p = (
                    _bid_p is not None
                    and _bid_offer_color(_bid_p, _theor_p,
                                          is_bid=True) is not None
                    and (_is_atm is None or _is_atm_val
                         or float(_k_val) > _is_atm)
                )

                if _is_sell_strike:
                    _k_bg = "#fb92f0"
                    _k_fg = "white"
                elif _is_buy_strike:
                    _k_bg = "#9c00ff"
                    _k_fg = "white"
                elif _is_central:
                    _k_bg = "#e3e7ec"
                    _k_fg = "#111"
                else:
                    _k_bg = "#f6f9fc"
                    _k_fg = "#111"

                _k_disp = _fmt_strike(_k_val, _board_cat)
                _iv_val = _rd.get("IV_%")
                _iv_str = (f"{float(_iv_val):.2f}%"
                           if _iv_val is not None else "—")

                with st.container(border=True):
                    _hdr1, _hdr2 = st.columns([1, 2])
                    with _hdr1:
                        st.markdown(
                            f"<div style='background:{_k_bg}; color:{_k_fg}; "
                            f"padding:8px 12px; border-radius:6px; "
                            f"font-size:1.1rem; font-weight:800; "
                            f"text-align:center;'>"
                            f"{_k_disp}</div>",
                            unsafe_allow_html=True,
                        )
                    with _hdr2:
                        st.markdown(
                            f"<div style='font-size:.85rem; padding-top:4px;'>"
                            f"<b>IV:</b> {_iv_str}<br>"
                            f"<b>Call:</b> {_rd.get('Call_Ticker', '—')} · "
                            f"<b>Put:</b> {_rd.get('Put_Ticker', '—')}</div>",
                            unsafe_allow_html=True,
                        )

                    _cc, _cp = st.columns(2)
                    with _cc:
                        _c_bg = "#e1e3fb" if _is_central else "#dbf3df"
                        st.markdown(
                            f"<div style='background:{_c_bg}; "
                            f"padding:6px 10px; border-radius:6px 6px 0 0; "
                            f"font-weight:700; color:#00a651; "
                            f"font-size:.85rem;'>CALL</div>",
                            unsafe_allow_html=True,
                        )
                        _cbid_col = _bid_offer_color(_bid_c, _theor_c,
                                                      is_bid=True)
                        _coff_col = _bid_offer_color(_off_c, _theor_c,
                                                      is_bid=False)
                        _cbid_style = (
                            f"background-color:{_cbid_col}; color:white; "
                            f"font-weight:700;" if _cbid_col else ""
                        )
                        _coff_style = (
                            f"background-color:{_coff_col}; color:white; "
                            f"font-weight:700;" if _coff_col else ""
                        )
                        st.markdown(
                            f"<div style='font-size:.82rem; line-height:1.6;'>"
                            f"Bid: <b style='{_cbid_style}'>{_fmt_p(_bid_c)}</b> · "
                            f"Offer: <b style='{_coff_style}'>{_fmt_p(_off_c)}</b><br>"
                            f"Last: <b>{_fmt_p(_rd.get('Call_Last'))}</b> · "
                            f"Theor: <b>{_fmt_p(_theor_c)}</b></div>",
                            unsafe_allow_html=True,
                        )

                        def _g_cell(name, active_buy, active_sell):
                            _col = _greek_cls(name, "Call",
                                               active_buy, active_sell, _rd)
                            _val = _rd.get(f"Call_{name.capitalize()}")
                            _style = (
                                f"background-color:{_col}; color:white; "
                                f"font-weight:700;" if _col else ""
                            )
                            return f"<span style='{_style}'>{_fmt_g(_val)}</span>"

                        st.markdown(
                            f"<div style='font-size:.78rem; margin-top:4px; "
                            f"line-height:1.7;'>"
                            f"Δ: {_g_cell('delta', _buy_c, _sell_c)} "
                            f"Γ: {_g_cell('gamma', _buy_c, _sell_c)} "
                            f"ν: {_g_cell('vega', _buy_c, _sell_c)} "
                            f"Θ: {_g_cell('theta', _buy_c, _sell_c)}"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                    with _cp:
                        _p_bg = "#fee5cd" if _is_central else "#ffcdce"
                        st.markdown(
                            f"<div style='background:{_p_bg}; "
                            f"padding:6px 10px; border-radius:6px 6px 0 0; "
                            f"font-weight:700; color:#d32f2f; "
                            f"font-size:.85rem;'>PUT</div>",
                            unsafe_allow_html=True,
                        )
                        _pbid_col = _bid_offer_color(_bid_p, _theor_p,
                                                      is_bid=True)
                        _poff_col = _bid_offer_color(_off_p, _theor_p,
                                                      is_bid=False)
                        _pbid_style = (
                            f"background-color:{_pbid_col}; color:white; "
                            f"font-weight:700;" if _pbid_col else ""
                        )
                        _poff_style = (
                            f"background-color:{_poff_col}; color:white; "
                            f"font-weight:700;" if _poff_col else ""
                        )
                        st.markdown(
                            f"<div style='font-size:.82rem; line-height:1.6;'>"
                            f"Bid: <b style='{_pbid_style}'>{_fmt_p(_bid_p)}</b> · "
                            f"Offer: <b style='{_poff_style}'>{_fmt_p(_off_p)}</b><br>"
                            f"Last: <b>{_fmt_p(_rd.get('Put_Last'))}</b> · "
                            f"Theor: <b>{_fmt_p(_theor_p)}</b></div>",
                            unsafe_allow_html=True,
                        )

                        def _g_cell_p(name, active_buy, active_sell):
                            _col = _greek_cls(name, "Put",
                                               active_buy, active_sell, _rd)
                            _val = _rd.get(f"Put_{name.capitalize()}")
                            _style = (
                                f"background-color:{_col}; color:white; "
                                f"font-weight:700;" if _col else ""
                            )
                            return f"<span style='{_style}'>{_fmt_g(_val)}</span>"

                        st.markdown(
                            f"<div style='font-size:.78rem; margin-top:4px; "
                            f"line-height:1.7;'>"
                            f"Δ: {_g_cell_p('delta', _buy_p, _sell_p)} "
                            f"Γ: {_g_cell_p('gamma', _buy_p, _sell_p)} "
                            f"ν: {_g_cell_p('vega', _buy_p, _sell_p)} "
                            f"Θ: {_g_cell_p('theta', _buy_p, _sell_p)}"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
        else:
            st.dataframe(
                df.style.apply(style_row, axis=1).format(
                    _format_map, precision=2, na_rep="—"
                ),
                column_config=column_display,
                use_container_width=True, height=600,
            )

        st.markdown("### Улыбка волатильности")
        try:
            points = fetch_volatility_graph(
                _board_asset, _active_series_code, _board_cat
            )
        except Exception:
            points = []
        if points:
            strikes_g = [p['strike'] for p in points]
            vols_g = [p['volatility'] for p in points]
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=strikes_g, y=vols_g, mode='lines+markers',
                line=dict(color='#2c7da0', width=2),
                fill='tozeroy', fillcolor='rgba(44,125,160,0.1)',
                name='IV, %',
            ))
            if _rvi_board_val is not None:
                fig.add_hline(
                    y=_rvi_board_val,
                    line=dict(color="#d32f2f", width=2, dash="dash"),
                    annotation_text=f"RVI = {_rvi_board_val:.2f}%",
                    annotation_position="top right",
                    annotation_font_color="#d32f2f",
                )
            fig.update_layout(
                title="Улыбка волатильности",
                xaxis_title="Страйк", yaxis_title="IV, %",
                height=380, margin=dict(l=20, r=20, t=50, b=20),
                xaxis=dict(tickformat=".0f", hoverformat=".0f"),
                yaxis=dict(tickformat=".2f", hoverformat=".2f"),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Данные для улыбки волатильности недоступны.")