"""Вкладка «Калькулятор»: iframe с index.html и inject-данными."""
import hashlib
import json
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from config import HTML_PLACEHOLDER
from core.instruments import resolve_canonical_asset_code
from core.market_data import (
    format_series_label,
    get_last_close_price,
    get_risk_free_rate_for_expiry,
    get_dividend_yield_for_ticker,
    _get_engine_market,
    resolve_underlying_secid,
)
from core.options import (
    get_alert_assets_list,
    find_alert_levels,
    fetch_optionseries,
    fetch_series_info,
    fetch_optionboard,
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
from core.helpers import _fmt_strike
from core.positions_cache import _strike_eq


@st.fragment
def _render_calc_tab():
    st.header("Калькулятор опционов (Black-Scholes)")

    if "calc_asset_ticker" not in st.session_state:
        st.session_state.calc_asset_ticker = "SBER"
    if "calc_asset_category" not in st.session_state:
        st.session_state.calc_asset_category = "Акция"
    if "calc_series_list" not in st.session_state:
        st.session_state.calc_series_list = []
    if "calc_selected_series" not in st.session_state:
        st.session_state.calc_selected_series = ""
    if "calc_selected_expiry" not in st.session_state:
        st.session_state.calc_selected_expiry = ""
    if "calc_autoloaded_for" not in st.session_state:
        st.session_state.calc_autoloaded_for = (None, None)

    _alert_assets = get_alert_assets_list()

    with st.expander("Актив и опционная серия", expanded=True):
        if _alert_assets:
            _opts = ["— выберите актив из оповещений —"] + [
                f"{it['ticker']} · {it['category']}" for it in _alert_assets
            ]
            _sel_label = st.selectbox(
                "Актив из оповещений", _opts, index=0,
                key="calc_asset_select",
                help="Список формируется из листа оповещений.",
            )
            if _sel_label and not _sel_label.startswith("—"):
                _parts = _sel_label.split(" · ", 1)
                if len(_parts) == 2:
                    _tk_sel, _cat_sel = _parts
                    if (st.session_state.calc_asset_ticker != _tk_sel
                            or st.session_state.calc_asset_category != _cat_sel):
                        st.session_state["calc_asset_input"] = _tk_sel
                        st.session_state["calc_asset_type_select"] = _cat_sel
                        st.session_state.calc_asset_ticker = _tk_sel
                        st.session_state.calc_asset_category = _cat_sel
                        st.session_state.calc_series_list = []
                        st.session_state.calc_autoloaded_for = (None, None)
        else:
            st.caption("Лист оповещений не загружен.")

        _pc1, _pc2 = st.columns([3, 2])
        with _pc1:
            _raw_asset = st.text_input(
                "Базовый актив (тикер)",
                value=st.session_state.calc_asset_ticker,
                key="calc_asset_input",
                placeholder="SBER, GAZP, RTS…",
            ).strip()
        with _pc2:
            _cat_opts = ["Акция", "Фьючерс", "Валюта", "Товар", "Индекс"]
            _cat_idx = (_cat_opts.index(st.session_state.calc_asset_category)
                        if st.session_state.calc_asset_category in _cat_opts
                        else 0)
            _asset_type_ui = st.selectbox(
                "Категория БА", _cat_opts,
                index=_cat_idx,
                key="calc_asset_type_select",
            )

        _asset_canon = resolve_canonical_asset_code(_raw_asset, _asset_type_ui)

        if (_asset_canon != st.session_state.calc_asset_ticker
                or _asset_type_ui != st.session_state.calc_asset_category):
            st.session_state.calc_asset_ticker = _asset_canon
            st.session_state.calc_asset_category = _asset_type_ui
            st.session_state.calc_series_list = []
            st.session_state.calc_autoloaded_for = (None, None)

        _loaded_for = st.session_state.get("calc_autoloaded_for", (None, None))
        if _asset_canon and (_asset_canon, _asset_type_ui) != _loaded_for:
            with st.spinner("Загрузка опционных серий…"):
                try:
                    _series_loaded = fetch_optionseries(
                        _asset_canon, _asset_type_ui
                    )
                    st.session_state.calc_series_list = _series_loaded or []
                    st.session_state.calc_autoloaded_for = (
                        _asset_canon, _asset_type_ui
                    )
                    if not _series_loaded:
                        st.warning(f"ISS не вернул серий для {_asset_canon}")
                except Exception as e:
                    st.error(f"Ошибка загрузки серий: {e}")
                    st.session_state.calc_series_list = []
        elif not _asset_canon:
            st.session_state.calc_series_list = []
            st.session_state.calc_selected_series = ""
            st.session_state.calc_selected_expiry = ""
            st.session_state.calc_autoloaded_for = (None, None)

        if st.session_state.calc_series_list:
            _sorted_series = sorted(
                st.session_state.calc_series_list,
                key=lambda x: x.get("expiry", ""),
            )
            _option_labels = [format_series_label(s["expiry"])
                              for s in _sorted_series]
            _chosen = st.selectbox(
                "Дата исполнения", _option_labels, index=0,
                key="calc_series_select",
            )
            _chosen_idx = _option_labels.index(_chosen)
            _selected = _sorted_series[_chosen_idx]
            st.session_state.calc_selected_series = _selected["code"]
            st.session_state.calc_selected_expiry = _selected["expiry"]
            try:
                _info = fetch_series_info(
                    _asset_canon, _asset_type_ui, _selected["code"]
                )
                with st.expander("Об опционной серии", expanded=False):
                    st.json(_info, expanded=True)
            except Exception as _e:
                st.warning(f"Информация о серии недоступна: {_e}")

    _levels = find_alert_levels(_asset_canon, category=_asset_type_ui)
    if _levels["found"]:
        st.info(
            f"Уровни для {_asset_canon}: "
            f"покупка = {_levels['buy']:.2f} ₽ · "
            f"продажа = {_levels['sell']:.2f} ₽"
        )
    else:
        st.caption("Уровни из оповещений не найдены.")

    _board_calc = None
    if st.session_state.calc_selected_series:
        try:
            _board_calc = fetch_optionboard(
                _asset_canon, _asset_type_ui,
                st.session_state.calc_selected_series,
            )
        except Exception:
            _board_calc = None

    _current_price_for_targets = None
    try:
        if _asset_type_ui in ("Фьючерс", "Валюта", "Товар"):
            _eng_t, _mkt_t = "futures", "forts"
        elif _asset_type_ui == "Индекс":
            _eng_t, _mkt_t = "stock", "index"
        else:
            _eng_t, _mkt_t = "stock", "shares"
        _secid_t = resolve_underlying_secid(_asset_canon, _asset_type_ui)
        if _secid_t:
            _current_price_for_targets = get_last_close_price(
                _secid_t, _eng_t, _mkt_t
            )
    except Exception:
        _current_price_for_targets = None

    _alert_targets = None
    if _board_calc is not None:
        try:
            _alert_targets = build_alert_targets(
                _asset_canon, _asset_type_ui,
                st.session_state.calc_selected_series, _board_calc, _levels,
                current_price=_current_price_for_targets,
            )
        except Exception:
            _alert_targets = None

    try:
        if _board_calc and _levels.get("found"):
            _strikes_calc = sorted({
                float(x["strike"]) for side_key in ("call", "put")
                for x in (_board_calc.get(side_key) or [])
                if x.get("strike") is not None
            })
            _range_strikes_calc = _calc_range_strikes(
                _levels.get("buy"), _levels.get("sell"), _strikes_calc
            )

            _alert_info_calc = find_alert_full_info(
                _asset_canon, _asset_type_ui
            )
            _checks_calc = get_alert_checks(_asset_canon, _asset_type_ui)
            _sugg_calc = _suggest_strategy(
                buy_check=_checks_calc.get("buy"),
                sell_check=_checks_calc.get("sell"),
                trend=(_alert_info_calc or {}).get("trend", ""),
                wave=(_alert_info_calc or {}).get("wave", ""),
                tendency=(_alert_info_calc or {}).get("tendency", ""),
                range_strikes=_range_strikes_calc,
                iv_zone=None, skew_type=None,
            )

            if _sugg_calc.get("strategy"):
                _at_calc = build_alert_targets(
                    _asset_canon, _asset_type_ui,
                    st.session_state.calc_selected_series,
                    _board_calc, _levels,
                    current_price=_current_price_for_targets,
                )
                _legs_preview_calc = _suggestion_legs(
                    _sugg_calc["strategy"], _at_calc
                )
                _legs_html = " · ".join(
                    f"{leg['side']} {leg['option']} "
                    f"{_fmt_strike(leg['strike'], _asset_type_ui)}×{leg['qty']}"
                    for leg in _legs_preview_calc
                    if leg.get("strike") is not None
                )
                st.markdown(
                    f"<div style='padding:10px 14px; border-radius:8px; "
                    f"background:#eef6fb; border-left:4px solid #1e88e5; "
                    f"margin-top:6px; margin-bottom:6px; font-size:.85rem;'>"
                    f"<b>Рекомендация:</b> {_sugg_calc['direction_ui']}<br>"
                    f"<span style='color:#5a6b78; font-size:.8rem;'>"
                    f"{_sugg_calc['rationale']}</span><br>"
                    f"<span style='font-family:Consolas,monospace; "
                    f"font-size:.82rem; color:#1c5a7a;'>{_legs_html}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    except Exception:
        pass

    _inject = {
        "strikes": [],
        "central_strike": None,
        "expiry": st.session_state.calc_selected_expiry or "",
        "series_code": st.session_state.calc_selected_series or "",
        "rf_buy": None, "rf_sell": None,
        "div_buy": None, "div_sell": None,
        "vol_buy": 30.0, "vol_sell": 30.0,
        "alerts": {
            "ticker": _asset_canon,
            "buy": _levels.get("buy") if _levels.get("found") else None,
            "sell": _levels.get("sell") if _levels.get("found") else None,
        },
        "alert_targets": _alert_targets,
        "market_price": None,
    }

    if _board_calc is not None:
        try:
            _calls_inj = _board_calc.get('call') or []
            _puts_inj = _board_calc.get('put') or []
            _central_inj = _board_calc.get('central_strike')
            if _central_inj is not None:
                try:
                    _inject["central_strike"] = float(_central_inj)
                except (TypeError, ValueError):
                    _inject["central_strike"] = None

            _strikes_set = set()
            _strikes_iv_inj = []
            for _c in _calls_inj:
                if _c.get('strike') is not None:
                    _strikes_set.add(_c['strike'])
            for _p in _puts_inj:
                if _p.get('strike') is not None:
                    _strikes_set.add(_p['strike'])
            for _k in sorted(_strikes_set):
                _c_iv = next((c.get('volatility') for c in _calls_inj
                              if _strike_eq(c.get('strike'), _k)), None)
                _p_iv = next((p.get('volatility') for p in _puts_inj
                              if _strike_eq(p.get('strike'), _k)), None)
                _iv = _c_iv or _p_iv
                _k_val = float(_k)
                _strikes_iv_inj.append({
                    "strike": int(_k_val) if _k_val.is_integer() else _k_val,
                    "iv": float(_iv) if _iv is not None else None,
                })
            _inject["strikes"] = _strikes_iv_inj
        except Exception:
            pass

    if _asset_type_ui == "Акция" and st.session_state.calc_selected_expiry:
        try:
            _rf_inj = get_risk_free_rate_for_expiry(
                st.session_state.calc_selected_expiry
            )
            _inject["rf_buy"] = _rf_inj
            _inject["rf_sell"] = _rf_inj
        except Exception:
            pass
        try:
            _q_inj, _sp_inj, _rd_inj = get_dividend_yield_for_ticker(
                _asset_canon, st.session_state.calc_selected_expiry
            )
            _inject["div_buy"] = _q_inj
            _inject["div_sell"] = _q_inj
        except Exception:
            pass
    else:
        _inject["rf_buy"] = 0.0
        _inject["rf_sell"] = 0.0
        _inject["div_buy"] = 0.0
        _inject["div_sell"] = 0.0

    try:
        if _asset_type_ui in ("Фьючерс", "Валюта", "Товар"):
            _eng_mkt, _mkt_mkt = "futures", "forts"
        elif _asset_type_ui == "Индекс":
            _eng_mkt, _mkt_mkt = "stock", "index"
        else:
            _eng_mkt, _mkt_mkt = "stock", "shares"
        _mkt_secid = resolve_underlying_secid(_asset_canon, _asset_type_ui)
        if _mkt_secid:
            _close_price = get_last_close_price(
                _mkt_secid, _eng_mkt, _mkt_mkt
            )
            if _close_price is not None:
                _inject["market_price"] = _close_price
                st.session_state["quick_und_last_price"] = _close_price
                st.session_state["_current_market_price"] = _close_price
    except Exception:
        pass

    _calc_html_path = Path("index.html")
    calc_html = None
    if not _calc_html_path.exists():
        st.error("Файл `index.html` не найден рядом со скриптом.")
    else:
        try:
            calc_html = _calc_html_path.read_text(encoding="utf-8")
        except Exception as e:
            st.error(f"Не удалось прочитать index.html: {e}")
            calc_html = None

    if calc_html is not None:
        if HTML_PLACEHOLDER not in calc_html:
            st.warning("В index.html не найден плейсхолдер — калькулятор отключён.")
            calc_html = None
        else:
            _inject_json = json.dumps(_inject, ensure_ascii=False,
                                      default=str, sort_keys=True)
            _inject_hash = hashlib.md5(_inject_json.encode()).hexdigest()
            _cache_key = f"_calc_html_{_inject_hash}"
            if _cache_key not in st.session_state:
                for _old_k in list(st.session_state.keys()):
                    if (isinstance(_old_k, str)
                            and _old_k.startswith("_calc_html_")
                            and _old_k != _cache_key):
                        st.session_state.pop(_old_k, None)
                st.session_state[_cache_key] = calc_html.replace(
                    HTML_PLACEHOLDER, _inject_json.replace("</", "<\\/")
                )
            calc_html = st.session_state[_cache_key]

    if calc_html is None:
        st.info("Калькулятор недоступен.")
    else:
        components.html(calc_html, height=1100, scrolling=True)