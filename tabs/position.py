"""Вкладка «Позиция»: управление текущим портфелем."""
import json
import math
from datetime import date, datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import CHECKLIST_RULES, _IS_CLOUD
from core.instruments import resolve_canonical_asset_code
from core.market_data import (
    calc_commission,
    format_series_label,
    get_last_close_price,
    _get_engine_market,
    resolve_underlying_secid,
    fetch_ba_iss_info,
    fetch_futures_info_iss,
    fetch_futures_contracts_list,
    apply_parity_delta,
    _calc_comm_ui,
    classify_strategy,
    fetch_rvi_current,
)
from core.options import (
    fetch_optionseries,
    fetch_all_series_for_asset,
    fetch_optionboard,
    _fetch_board_cached_30s,
)
from core.alerts import (
    find_alert_levels,
    find_alert_full_info,
    get_alert_checks,
    get_alert_recommendation_text,
    _trend_emoji_triplet,
    _get_recommendation,
    build_alert_targets,
)
from core.strategies import (
    _calc_range_strikes,
    _suggest_strategy,
    _suggestion_legs,
    _apply_suggested_strategy,
    _compute_r_q_for_leg,
    _migrate_positions_r_q,
    _pretrade_checklist_state,
    _validate_options_leg,
    _planned_close_date_str,
    _planned_close_badge,
)
from core.pricing import (
    compute_payoff,
    _get_current_market_price_for_asset,
)
from core.helpers import (
    _color_call_put,
    _side_from_qty,
    _side_ui,
    _side_internal,
    _new_position_id,
    _fmt_strike,
    _greek_delta_color,
    _greek_theta_color,
)
from core.positions_cache import (
    _cache_file,
    _load_cache,
    _save_cache,
    _positions_signature,
    _find_board_row,
    _find_board_row_by_secid,
    _strike_eq,
)
from core.backup import _backup_clean
from ui.checklist import _render_pretrade_checklist
from ui.charts import render_exchange_chart


@st.fragment
def _render_position_tab():
    st.header("Управление позицией")

    # [PATCH 4] Сохраняем состояние предыдущего рендера до переключения
    # актива. Это также фиксирует удаление последней позиции.
    _active_key_to_sync = st.session_state.get("_pos_active_key")

    if _active_key_to_sync and "positions" in st.session_state:
        _current_positions = st.session_state["positions"]
        _signature = _positions_signature(_current_positions)

        if _signature != st.session_state.get("_pos_signature"):
            _cache = dict(st.session_state.get("positions_cache", {}))
            _cache[_active_key_to_sync] = _backup_clean(_current_positions)

            st.session_state["positions_cache"] = _cache
            _save_cache(_cache)
            st.session_state["_pos_signature"] = _signature

    if _IS_CLOUD:
        st.info(
            "☁️ **Streamlit Cloud:** файловый кэш отключён. Позиции "
            "живут только в текущей сессии. Для сохранения между "
            "сессиями — используйте **«Резервная копия · JSON»** "
            "вверху страницы."
        )

    # [PATCH 5] Начальный вид карточек на мобильных.
    if "pos_view_mode" not in st.session_state:
        st.session_state.pos_view_mode = "Карточки (мобильный)"

    _view_mode_pos = st.radio(
        "Вид позиций",
        ["Таблица", "Карточки (мобильный)"],
        index=0 if st.session_state.pos_view_mode == "Таблица" else 1,
        horizontal=True,
        key="pos_view_mode_radio",
        label_visibility="collapsed",
    )
    st.session_state.pos_view_mode = _view_mode_pos

    if "pos_asset" not in st.session_state:
        st.session_state.pos_asset = st.session_state.get("board_asset", "SBER")
    if "pos_category" not in st.session_state:
        st.session_state.pos_category = st.session_state.get(
            "board_category", "Акция"
        )
    if "pos_series_list" not in st.session_state:
        st.session_state.pos_series_list = []
    if "pos_series_code" not in st.session_state:
        st.session_state.pos_series_code = ""
    if "pos_expiry" not in st.session_state:
        st.session_state.pos_expiry = ""
    if "pos_autoloaded_for" not in st.session_state:
        st.session_state.pos_autoloaded_for = (None, None)

    _pc1, _pc2 = st.columns([3, 2])
    with _pc1:
        _pos_raw_asset = st.text_input(
            "Базовый актив (для позиции)",
            value=st.session_state.pos_asset,
            key="pos_asset_input",
            placeholder="SBER, GAZP, RTS…",
        ).strip()
    with _pc2:
        _pos_cat_opts = ["Акция", "Фьючерс", "Валюта", "Товар", "Индекс"]
        _pos_cat_idx = (
            _pos_cat_opts.index(st.session_state.pos_category)
            if st.session_state.pos_category in _pos_cat_opts else 0
        )
        _pos_cat = st.selectbox(
            "Категория БА",
            _pos_cat_opts,
            index=_pos_cat_idx,
            key="pos_category_select",
        )

    _pos_asset = resolve_canonical_asset_code(_pos_raw_asset, _pos_cat)

    # Тренд / Волна / Тенденция + расшифровка
    _alert_info_pos = find_alert_full_info(_pos_asset, _pos_cat)
    if _alert_info_pos:
        _pos_triplet = _trend_emoji_triplet(
            _alert_info_pos["trend"],
            _alert_info_pos["wave"],
            _alert_info_pos["tendency"],
        )
        _pos_rec_side, _pos_rec_desc = _get_recommendation(
            _alert_info_pos["trend"],
            _alert_info_pos["wave"],
            _alert_info_pos["tendency"],
        )

        if _pos_triplet:
            _pos_rec_color = (
                "#00a651" if _pos_rec_side == "CALL"
                else "#d32f2f" if _pos_rec_side == "PUT"
                else "#333"
            )
            _pos_rec_badge = ""
            if _pos_rec_side:
                _pos_rec_badge = (
                    f"<span style='padding:2px 12px; border-radius:6px; "
                    f"background:{_pos_rec_color}; color:white; "
                    f"font-weight:700; font-size:.85rem; margin-left:12px; "
                    f"letter-spacing:.05em;'>{_pos_rec_side}</span>"
                )
            _pos_desc_html = ""
            if _pos_rec_desc:
                _pos_desc_html = (
                    f"<div style='margin-top:8px; padding:10px 14px; "
                    f"background:#ffffff; border-radius:8px; "
                    f"border-left:4px solid {_pos_rec_color}; "
                    f"font-size:.85rem; color:#333; line-height:1.45;'>"
                    f"{_pos_rec_desc}</div>"
                )

            st.markdown(
                f"<div style='padding:12px 16px; background:#eef6fb; "
                f"border-radius:10px; border:1px solid #cfdfe9; "
                f"margin-top:8px; margin-bottom:6px;'>"
                f"<div style='display:flex; align-items:center;'>"
                f"<span style='font-size:1.3rem; letter-spacing:2px;'>"
                f"{_pos_triplet}</span>"
                f"{_pos_rec_badge}</div>"
                f"{_pos_desc_html}"
                f"</div>",
                unsafe_allow_html=True,
            )

    # Рекомендация по чек-боксам
    _rec_text = get_alert_recommendation_text(_pos_asset, _pos_cat)
    if _rec_text:
        st.markdown(
            f"<div style='padding:12px 16px; border-radius:10px; "
            f"background:#e8f5e9; border-left:6px solid #00a651; "
            f"margin-top:8px; margin-bottom:6px; font-size:.9rem; color:#0a4d20;'>"
            f"<b>Рекомендация по выбранным чек-боксам:</b><br>"
            f"{_rec_text}</div>",
            unsafe_allow_html=True,
        )

    # G4: баннер рекомендованной конструкции с кнопкой «Применить»
    _sugg_pos = None
    _alert_targets_pos = None
    _board_pos = None
    try:
        if (_pos_asset and _pos_cat
                and st.session_state.get("pos_series_code")):
            try:
                _board_pos = fetch_optionboard(
                    _pos_asset, _pos_cat,
                    st.session_state.pos_series_code,
                )
            except Exception:
                _board_pos = None

            if _board_pos:
                _strikes_pos = sorted({
                    float(x["strike"]) for side_key in ("call", "put")
                    for x in (_board_pos.get(side_key) or [])
                    if x.get("strike") is not None
                })
                _lvl_pos = find_alert_levels(_pos_asset, _pos_cat)
                _cp_pos = _get_current_market_price_for_asset(
                    _pos_asset, _pos_cat
                )
                _range_strikes_pos = _calc_range_strikes(
                    _lvl_pos.get("buy"), _lvl_pos.get("sell"), _strikes_pos
                )

                _checks_pos = get_alert_checks(_pos_asset, _pos_cat)
                _sugg_pos = _suggest_strategy(
                    buy_check=_checks_pos.get("buy"),
                    sell_check=_checks_pos.get("sell"),
                    trend=(_alert_info_pos or {}).get("trend", ""),
                    wave=(_alert_info_pos or {}).get("wave", ""),
                    tendency=(_alert_info_pos or {}).get("tendency", ""),
                    range_strikes=_range_strikes_pos,
                    iv_zone=None, skew_type=None,
                )

                if _sugg_pos.get("strategy"):
                    _alert_targets_pos = build_alert_targets(
                        _pos_asset, _pos_cat,
                        st.session_state.pos_series_code,
                        _board_pos, _lvl_pos, current_price=_cp_pos,
                    )

                if _sugg_pos.get("strategy") and _alert_targets_pos:
                    _sugg_color = {
                        "high": "#00a651",
                        "medium": "#e6a500",
                        "low": "#7a7a7a",
                    }.get(_sugg_pos["confidence"], "#7a7a7a")
                    _legs_preview = _suggestion_legs(
                        _sugg_pos["strategy"], _alert_targets_pos
                    )
                    _legs_lines = "".join(
                        f"<div style='font-size:.82rem; color:#333;'>"
                        f"• {leg['label']}: {leg['side']} {leg['option']} "
                        f"{_fmt_strike(leg['strike'], _pos_cat)}"
                        f" × {leg['qty']}</div>"
                        for leg in _legs_preview
                        if leg.get("strike") is not None
                    )
                    st.markdown(
                        f"<div style='padding:12px 16px; border-radius:10px; "
                        f"background:{_sugg_color}15; "
                        f"border-left:6px solid {_sugg_color}; "
                        f"margin-top:8px; margin-bottom:8px;'>"
                        f"<div style='font-size:1rem; font-weight:800; "
                        f"color:{_sugg_color};'>"
                        f"Рекомендация: {_sugg_pos['direction_ui']}</div>"
                        f"<div style='font-size:.82rem; color:#5a6b78; "
                        f"margin-top:4px;'>{_sugg_pos['rationale']}</div>"
                        f"<div style='margin-top:6px;'>{_legs_lines}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                    if st.button(
                        f"Применить конструкцию: {_sugg_pos['strategy']}",
                        type="primary",
                        use_container_width=True,
                        key="apply_sugg_strategy_btn",
                    ):
                        _created = _apply_suggested_strategy(
                            _sugg_pos["strategy"],
                            _pos_asset, _pos_cat,
                            st.session_state.pos_series_code,
                            st.session_state.pos_expiry,
                            _alert_targets_pos, _board_pos,
                        )
                        if _created:
                            for _np in _created:
                                st.session_state.positions.append(_np)
                            _ck_pos = st.session_state.get("_pos_active_key")
                            if _ck_pos:
                                st.session_state.positions_cache[_ck_pos] = list(
                                    st.session_state.positions
                                )
                                _save_cache(
                                    st.session_state.positions_cache
                                )
                                st.session_state["_pos_signature"] = (
                                    _positions_signature(
                                        st.session_state.positions
                                    )
                                )
                            st.success(
                                f"Добавлено ног: {len(_created)} "
                                f"({_sugg_pos['strategy']})"
                            )
                            st.rerun()
                        else:
                            st.warning(
                                "Не удалось создать позиции — "
                                "проверьте доску/страйки"
                            )
    except Exception:
        _sugg_pos = None

    # Кэш позиций
    if "positions_cache" not in st.session_state:
        st.session_state.positions_cache = _load_cache()
    if "positions" not in st.session_state:
        st.session_state.positions = []

    _new_key = f"{_pos_asset}|{_pos_cat}"
    _old_key = st.session_state.get("_pos_active_key")

    if _old_key is None:
        st.session_state["_pos_active_key"] = _new_key
        _cached = st.session_state.positions_cache.get(_new_key, [])
        if _cached and not st.session_state.positions:
            st.session_state.positions = list(_cached)
        else:
            st.session_state.positions_cache.setdefault(
                _new_key, list(st.session_state.positions)
            )
        _save_cache(st.session_state.positions_cache)
        st.session_state["_pos_signature"] = _positions_signature(
            st.session_state.positions
        )

    elif _new_key != _old_key:
        st.session_state.positions_cache[_old_key] = list(
            st.session_state.positions
        )
        st.session_state.positions = list(
            st.session_state.positions_cache.get(_new_key, [])
        )
        st.session_state["_pos_active_key"] = _new_key
        for _k in list(st.session_state.keys()):
            if _k.startswith(("qty_", "price_", "qp_", "qm_",
                              "pp_", "pm_")):
                del st.session_state[_k]
        for _fk in ("form_series", "form_strike", "form_opt_type",
                    "form_side", "form_qty", "form_price",
                    "form_instrument_type", "form_futures_contract",
                    "form_use_auto_price", "fallback_series_selector"):
            st.session_state.pop(_fk, None)
        _save_cache(st.session_state.positions_cache)
        st.session_state["_pos_signature"] = _positions_signature(
            st.session_state.positions
        )

    if (_pos_asset != st.session_state.pos_asset
            or _pos_cat != st.session_state.pos_category):
        st.session_state.pos_asset = _pos_asset
        st.session_state.pos_category = _pos_cat
        for _fk in ("form_series", "form_strike", "form_opt_type",
                    "form_side", "form_qty", "form_price",
                    "form_instrument_type", "form_futures_contract",
                    "form_use_auto_price", "fallback_series_selector"):
            st.session_state.pop(_fk, None)

    # G3: миграция r/q + гигиена полей у существующих позиций
    if st.session_state.get("_g3_migrated") is not True:
        try:
            if st.session_state.get("positions"):
                _ck_mig = st.session_state.get("_pos_active_key")
                _parts_mig = (_ck_mig.split("|", 1)
                              if _ck_mig else [None, None])
                _asset_mig = _parts_mig[0] if len(_parts_mig) > 0 else None
                _atype_mig = _parts_mig[1] if len(_parts_mig) > 1 else None
                for _p_mig in st.session_state.positions:
                    if not _p_mig.get("_asset") and _asset_mig:
                        _p_mig["_asset"] = _asset_mig
                    if not _p_mig.get("_atype") and _atype_mig:
                        _p_mig["_atype"] = _atype_mig
                    if _p_mig.get("Тип инструмента") == "Опцион":
                        if ("_vol" not in _p_mig
                                or _p_mig.get("_vol") is None):
                            _p_mig["_vol"] = 20.0
                        if "_r" not in _p_mig:
                            _p_mig["_r"] = 0.0
                        if "_q" not in _p_mig:
                            _p_mig["_q"] = 0.0
                    if "_id" not in _p_mig:
                        _p_mig["_id"] = _new_position_id()

                _migrate_positions_r_q(st.session_state.positions)

                if _ck_mig:
                    st.session_state.positions_cache[_ck_mig] = list(
                        st.session_state.positions
                    )
                    _save_cache(st.session_state.positions_cache)
                    st.session_state["_pos_signature"] = _positions_signature(
                        st.session_state.positions
                    )
            st.session_state["_g3_migrated"] = True
        except Exception:
            pass

    try:
        from core.instruments import get_asset_code_and_type
        _iss_code_disp, _iss_type_disp = get_asset_code_and_type(
            _pos_asset, _pos_cat
        )
        if _iss_code_disp and _iss_code_disp.upper() != _pos_asset.upper():
            st.caption(
                f"Для категории {_pos_cat} запрашивается код "
                f"{_iss_code_disp} (тип: {_iss_type_disp})"
            )
        else:
            st.caption(
                f"Запрос: код {_iss_code_disp} · тип: {_iss_type_disp}"
            )
    except Exception:
        pass

    _cache_path = _cache_file()
    _cache_info = {
        k: len(v) for k, v in st.session_state.positions_cache.items()
        if isinstance(v, list) and len(v) > 0
    }

    _cache_label = (f"Кэш позиций — активов: {len(_cache_info)} · "
                    f"текущий: {_new_key} "
                    f"({_cache_info.get(_new_key, 0)} шт.)")

    with st.expander(_cache_label, expanded=False):
        if _cache_path is not None:
            st.caption(f"Файл: {_cache_path}")
        else:
            st.caption("Файл недоступен — кэш работает только в памяти.")

        if not _cache_info:
            st.info("Кэш пуст. Собери позицию — она автоматически сохранится.")
        else:
            st.markdown("**Сохранённые активы:**")
            for _ck in sorted(_cache_info.keys()):
                _cn = _cache_info[_ck]
                _is_current = (_ck == _new_key)
                _cc1, _cc2, _cc3 = st.columns([4, 1, 1])
                with _cc1:
                    _mark = "🟢" if _is_current else "⚪"
                    st.markdown(f"{_mark} {_ck} — {_cn} шт.")
                with _cc2:
                    if not _is_current:
                        if st.button(
                            "Загрузить",
                            key=f"pos_cache_load_{_ck}",
                            use_container_width=True,
                        ):
                            _cur_active = st.session_state.get(
                                "_pos_active_key"
                            )
                            if _cur_active:
                                st.session_state.positions_cache[
                                    _cur_active
                                ] = list(st.session_state.positions)
                            _parts = _ck.split("|", 1)
                            if len(_parts) == 2:
                                st.session_state.pos_asset = _parts[0]
                                st.session_state.pos_category = _parts[1]
                                st.session_state["_pos_active_key"] = _ck
                                st.session_state.positions = list(
                                    st.session_state.positions_cache.get(
                                        _ck, []
                                    )
                                )
                                for _k in list(st.session_state.keys()):
                                    if _k.startswith(("qty_", "price_",
                                                      "qp_", "qm_",
                                                      "pp_", "pm_")):
                                        del st.session_state[_k]
                                for _fk in ("form_series", "form_strike",
                                            "form_opt_type", "form_side",
                                            "form_qty", "form_price",
                                            "form_instrument_type",
                                            "form_futures_contract",
                                            "form_use_auto_price",
                                            "fallback_series_selector"):
                                    st.session_state.pop(_fk, None)
                                st.session_state.pos_series_list = []
                                st.session_state.pos_series_code = ""
                                st.session_state.pos_expiry = ""
                                st.session_state.pos_autoloaded_for = (
                                    None, None
                                )
                                for _wk in ("pos_asset_input",
                                            "pos_category_select"):
                                    if _wk in st.session_state:
                                        del st.session_state[_wk]
                                st.session_state["_wt_last_asset"] = None
                                st.session_state["_current_market_price"] = None
                                _save_cache(
                                    st.session_state.positions_cache
                                )
                                st.session_state["_pos_signature"] = (
                                    _positions_signature(
                                        st.session_state.positions
                                    )
                                )
                                st.rerun()
                with _cc3:
                    if not _is_current:
                        if st.button(
                            "Удалить",
                            key=f"pos_cache_del_{_ck}",
                            use_container_width=True,
                        ):
                            st.session_state.positions_cache.pop(_ck, None)
                            _save_cache(st.session_state.positions_cache)
                            st.rerun()

            st.markdown("---")
            _cb1, _cb2 = st.columns(2)
            with _cb1:
                if st.button(
                    "Сохранить сейчас",
                    key="pos_cache_save_now",
                    use_container_width=True,
                ):
                    st.session_state.positions_cache[_new_key] = list(
                        st.session_state.positions
                    )
                    _ok = _save_cache(st.session_state.positions_cache)
                    st.session_state["_pos_signature"] = _positions_signature(
                        st.session_state.positions
                    )
                    if _ok:
                        st.success("Кэш записан на диск")
                    else:
                        st.warning(
                            "Не удалось записать файл — кэш только в памяти"
                        )
                    st.rerun()
            with _cb2:
                if st.button(
                    "Очистить весь кэш",
                    key="pos_cache_clear_all",
                    use_container_width=True,
                    help="Удалить все сохранённые позиции (включая текущую)",
                ):
                    st.session_state.positions_cache = {}
                    st.session_state.positions = []
                    _save_cache({})
                    st.session_state["_pos_signature"] = ""
                    st.success("Кэш очищен")
                    st.rerun()

    _pos_loaded_for = st.session_state.get("pos_autoloaded_for", (None, None))
    if _pos_asset and (_pos_asset, _pos_cat) != _pos_loaded_for:
        with st.spinner(f"Загрузка серий для {_pos_asset}…"):
            try:
                _pos_series_new = fetch_optionseries(_pos_asset, _pos_cat)
                st.session_state.pos_series_list = _pos_series_new or []
                st.session_state.pos_autoloaded_for = (_pos_asset, _pos_cat)
                if _pos_series_new and not st.session_state.pos_series_code:
                    _s_first = sorted(
                        _pos_series_new, key=lambda x: x.get("expiry", "")
                    )[0]
                    st.session_state.pos_series_code = _s_first["code"]
                    st.session_state.pos_expiry = _s_first["expiry"]
                if not _pos_series_new:
                    st.warning(f"ISS не вернул ни одной серии "
                               f"для {_pos_asset} ({_pos_cat}).")
            except Exception as e:
                st.error(f"Ошибка загрузки серий: {e}")
                st.session_state.pos_series_list = []

    st.markdown("---")

    col_ctrl, col_add = st.columns([1, 1.7], gap="medium")

    with col_ctrl:
        st.markdown("#### Параметры")
        with st.container(border=True):
            _r1c1, _r1c2 = st.columns(2)
            with _r1c1:
                deposit = st.number_input(
                    "Депозит, ₽", min_value=0.0,
                    value=100000.0, step=1000.0,
                    format="%.0f", key="dep_inp",
                )
            with _r1c2:
                risk_pct = st.number_input(
                    "Риск, %", min_value=0.1,
                    max_value=100.0, value=1.0,
                    step=0.1, format="%.1f", key="rp_inp",
                )

            st.markdown("**Комиссии**")
            _r2c1, _r2c2 = st.columns(2)
            with _r2c1:
                comm_options_pct = st.number_input(
                    "Опционы, %", min_value=0.0, max_value=20.0, value=3.0,
                    step=0.1, format="%.2f", key="cop_inp",
                )
            with _r2c2:
                min_comm_options = st.number_input(
                    "Мин. опц., ₽", min_value=0.0, max_value=100.0,
                    value=0.02, step=0.01, format="%.4f", key="cmo_inp",
                )
            _r3c1, _r3c2 = st.columns(2)
            with _r3c1:
                comm_futures_pct = st.number_input(
                    "Фьючерсы, %", min_value=0.0, max_value=5.0, value=0.1,
                    step=0.01, format="%.3f", key="cfp_inp",
                )
            with _r3c2:
                comm_stocks_pct = st.number_input(
                    "Акции, %", min_value=0.0, max_value=5.0, value=0.3,
                    step=0.01, format="%.3f", key="csp_inp",
                )

            st.markdown("---")
            with st.popover("Импорт портфеля (CSV)",
                             use_container_width=True):
                st.markdown("**Загрузить CSV**")
                _pf_file = st.file_uploader(
                    "CSV файл", type=["csv"],
                    key="pf_csv_inp",
                    label_visibility="collapsed",
                )
                if _pf_file is not None:
                    try:
                        _pf_df = pd.read_csv(_pf_file)

                        _csv_asset = None
                        _csv_category = None
                        for _colname in ("Актив", "Тикер БА",
                                          "База", "Asset"):
                            if _colname in _pf_df.columns:
                                _vals = (_pf_df[_colname].dropna()
                                         .astype(str).unique())
                                if len(_vals) > 0:
                                    _csv_asset = _vals[0].strip()
                                    break
                        for _colname in ("Категория", "Категория БА",
                                          "Category"):
                            if _colname in _pf_df.columns:
                                _vals = (_pf_df[_colname].dropna()
                                         .astype(str).unique())
                                if len(_vals) > 0:
                                    _csv_category = _vals[0].strip()
                                    break

                        if _csv_asset:
                            st.info(
                                f"В файле указан актив: {_csv_asset}"
                                + (f" · Категория: {_csv_category}"
                                   if _csv_category else "")
                            )

                        _imported = []
                        for _, row in _pf_df.iterrows():
                            _imported.append({
                                "_id": _new_position_id(),
                                "Конструкция": str(row.get(
                                    "Конструкция", "Без названия"
                                )),
                                "Тип инструмента": str(row.get(
                                    "Тип", "Опцион"
                                )),
                                "Опцион": str(row.get("Опцион", "—")),
                                "Направление": str(row.get(
                                    "Направление", "Buy"
                                )),
                                "Страйк": (
                                    float(row["Страйк"])
                                    if pd.notna(row.get("Страйк"))
                                    and str(row.get("Страйк")) != "—"
                                    else None
                                ),
                                "Эксп.": str(row.get("Эксп.", "—")),
                                "Тикер": str(row.get("Тикер", "—")),
                                "Кол-во": int(row.get("Кол-во", 0)),
                                "Цена": float(row.get("Цена", 0)),
                                "visible": True,
                                "_asset": (
                                    _csv_asset
                                    or st.session_state.get("pos_asset", "")
                                ),
                                "_atype": (
                                    _csv_category
                                    or st.session_state.get(
                                        "pos_category", ""
                                    )
                                ),
                            })

                        if st.button("Применить", type="primary",
                                      key="pf_apply"):
                            for k in list(st.session_state.keys()):
                                if k.startswith(("qty_", "price_",
                                                 "qp_", "qm_",
                                                 "pp_", "pm_")):
                                    del st.session_state[k]
                            st.session_state.positions = _imported

                            if _csv_asset:
                                _canon = resolve_canonical_asset_code(
                                    _csv_asset, _csv_category or None
                                )
                                st.session_state.pos_asset = _canon
                            if _csv_category and _csv_category in (
                                "Акция", "Фьючерс", "Валюта",
                                "Товар", "Индекс"
                            ):
                                st.session_state.pos_category = _csv_category

                            _new_key_csv = (
                                f"{st.session_state.pos_asset}|"
                                f"{st.session_state.pos_category}"
                            )
                            st.session_state["_pos_active_key"] = _new_key_csv
                            st.session_state.positions_cache[
                                _new_key_csv
                            ] = list(_imported)
                            _save_cache(st.session_state.positions_cache)

                            st.session_state.pos_series_list = []
                            st.session_state.pos_series_code = ""
                            st.session_state.pos_expiry = ""
                            st.session_state.pos_autoloaded_for = (None, None)

                            for _wk in ("pos_asset_input",
                                        "pos_category_select",
                                        "form_series", "form_strike",
                                        "form_opt_type", "form_side",
                                        "form_qty", "form_price",
                                        "form_instrument_type",
                                        "form_futures_contract",
                                        "form_use_auto_price",
                                        "fallback_series_selector"):
                                if _wk in st.session_state:
                                    del st.session_state[_wk]

                            st.session_state["_wt_last_asset"] = None
                            st.session_state["_current_market_price"] = None
                            st.session_state["_g3_migrated"] = False
                            st.session_state["_pos_signature"] = (
                                _positions_signature(
                                    st.session_state.positions
                                )
                            )

                            st.rerun()
                        st.caption(f"Строк: {len(_imported)}")
                    except Exception as e:
                        st.error(f"Ошибка чтения CSV: {e}")

        risk_amount = deposit * risk_pct / 100.0
        st.caption(
            f"Доступно для сделки: {risk_amount:,.2f} ₽ "
            f"({risk_pct}% от {deposit:,.0f} ₽)"
        )

    def _calc_comm(premium, instr_type="Опцион"):
        return calc_commission(
            premium, instrument_type=instr_type,
            min_comm_options=min_comm_options,
            comm_options_pct=comm_options_pct,
            comm_futures_pct=comm_futures_pct,
            comm_stocks_pct=comm_stocks_pct,
        )

    def _norm_cdf_py(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def black_scholes_py(S, K, T, r_pct, vol_pct, div_pct, opt_type):
        if T <= 0 or S <= 0 or K <= 0 or vol_pct <= 0:
            return max(0.0, S - K) if opt_type == "call" else max(0.0, K - S)
        r = r_pct / 100.0
        q = div_pct / 100.0
        sigma = vol_pct / 100.0
        d1 = (math.log(S / K) + (r - q + sigma * sigma / 2) * T) \
             / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        if opt_type == "call":
            return S * math.exp(-q * T) * _norm_cdf_py(d1) \
                   - K * math.exp(-r * T) * _norm_cdf_py(d2)
        return K * math.exp(-r * T) * _norm_cdf_py(-d2) \
               - S * math.exp(-q * T) * _norm_cdf_py(-d1)

    def _sync_qty_to_position(key, pos_id):
        try:
            _new_val = int(st.session_state.get(key, 0))
        except (TypeError, ValueError):
            return
        for _p in st.session_state.get("positions", []):
            if _p.get("_id") == pos_id:
                _p["Кол-во"] = _new_val
                return

    def _sync_price_to_position(key, pos_id):
        try:
            _new_val = float(st.session_state.get(key, 0.0))
        except (TypeError, ValueError):
            return
        if _new_val < 0:
            _new_val = 0.0
        for _p in st.session_state.get("positions", []):
            if _p.get("_id") == pos_id:
                _p["Цена"] = _new_val
                return

    def _cb_qty_dec(key, pos_id):
        st.session_state[key] = int(st.session_state.get(key, 1)) - 1
        _sync_qty_to_position(key, pos_id)

    def _cb_qty_inc(key, pos_id):
        st.session_state[key] = int(st.session_state.get(key, 1)) + 1
        _sync_qty_to_position(key, pos_id)

    def _cb_price_dec(key, pos_id):
        st.session_state[key] = round(
            max(0.0, float(st.session_state.get(key, 0.0)) - 0.01), 4
        )
        _sync_price_to_position(key, pos_id)

    def _cb_price_inc(key, pos_id):
        st.session_state[key] = round(
            float(st.session_state.get(key, 0.0)) + 0.01, 4
        )
        _sync_price_to_position(key, pos_id)

    def _cb_qty_sync(key, pos_id):
        _sync_qty_to_position(key, pos_id)

    def _cb_price_sync(key, pos_id):
        _sync_price_to_position(key, pos_id)

    def _get_board_price(_strike, _opt_type, _side_internal, _c_map, _p_map):
        if _strike is None:
            return 0.0
        src = _c_map if _opt_type == "Call" else _p_map
        row = src.get(_strike, {})
        if not row:
            return 0.0
        v = (row.get('offer') if _side_internal == "Buy"
             else row.get('bid'))
        if v is None or v <= 0:
            v = row.get('theorprice')
        if v is None or v <= 0:
            v = row.get('last')
        try:
            return float(v) if v and v > 0 else 0.0
        except Exception:
            return 0.0

    def _fmt_board_label(_opt_type, _side_internal, _strike, _price,
                         _asset_type=None):
        if _price is None or _price <= 0 or _strike is None:
            return "—"
        _sign = "+" if _side_internal == "Buy" else "-"
        _letter = "C" if _opt_type == "Call" else "P"
        _k_str = _fmt_strike(_strike, _asset_type)
        _p_str = f"{_price:.4f}".replace('.', ',')
        return f"{_sign}{_letter}{_k_str} {_p_str}"

    # ⬇️ Продолжение (форма добавления, живое обновление, таблица,
    #    анализ, графики) — в части 7b.




    with col_add:
        st.markdown("#### Добавить позицию")

        _flash = st.session_state.pop("_position_add_flash", None)
        if _flash:
            if _flash.get("errors"):
                st.markdown(
                    "<div style='padding:10px 14px; border-radius:8px; "
                    "background:#ffebee; border-left:4px solid #d32f2f; "
                    "font-size:.85rem; color:#8b1e1e; margin-bottom:8px;'>"
                    "<b>Позиция добавлена с замечаниями:</b><br>"
                    + "<br>".join(f"• {_e}" for _e in _flash["errors"])
                    + "</div>",
                    unsafe_allow_html=True,
                )
            if _flash.get("warnings"):
                st.markdown(
                    "<div style='padding:8px 12px; border-radius:8px; "
                    "background:#fff3e0; border-left:4px solid #e6a500; "
                    "font-size:.82rem; color:#8a6500; margin-bottom:8px;'>"
                    + "<br>".join(f"• {_w}" for _w in _flash["warnings"])
                    + "</div>",
                    unsafe_allow_html=True,
                )
            if _flash.get("message"):
                st.success(_flash["message"])

        if "positions" not in st.session_state:
            st.session_state.positions = []

        _asset_now_all = st.session_state.get("pos_asset", "")
        _atype_now_all = st.session_state.get("pos_category", "")
        _series_now = st.session_state.get("pos_series_code", "")
        _expiry_now = st.session_state.get("pos_expiry", "")

        if not (_asset_now_all and _atype_now_all):
            st.warning(
                "Сначала выберите базовый актив и категорию в блоке выше."
            )
        else:
            _all_series = []
            try:
                _all_series = fetch_all_series_for_asset(
                    _asset_now_all, _atype_now_all
                ) or []
            except Exception:
                _all_series = []
            if not _all_series:
                _all_series = st.session_state.get("pos_series_list", []) or []

            if not _all_series:
                st.error(
                    f"Не удалось получить опционные серии "
                    f"для {_asset_now_all} ({_atype_now_all})."
                )
                try:
                    from core.instruments import get_asset_code_and_type
                    _iss_code_dbg, _iss_type_dbg = get_asset_code_and_type(
                        _asset_now_all, _atype_now_all
                    )
                    st.caption(
                        f"В ISS отправлялся запрос: код {_iss_code_dbg}, "
                        f"тип {_iss_type_dbg}"
                    )
                    if (_atype_now_all == "Фьючерс"
                            and _iss_code_dbg == _asset_now_all.upper()):
                        st.info(
                            "Похоже, для этого тикера alias не задан. "
                            "Попробуйте ввести код фьючерса напрямую: "
                            "GAZR (для Газпрома), SBRF (Сбер), "
                            "SBPR (Сбер-п), MTSI (МТС), "
                            "SNGR / SNGP (Сургут), TRNF (Транснефть)."
                        )
                except Exception:
                    pass
            else:
                _all_series_codes = {s["code"] for s in _all_series}
                if not _series_now or _series_now not in _all_series_codes:
                    _first = sorted(
                        _all_series, key=lambda x: x.get("expiry", "")
                    )[0]
                    st.session_state.pos_series_code = _first["code"]
                    st.session_state.pos_expiry = _first["expiry"]
                    _series_now = _first["code"]
                    _expiry_now = _first["expiry"]

                _series_labels = [format_series_label(s["expiry"])
                                  for s in _all_series]
                _series_by_label = {
                    lab: s for lab, s in zip(_series_labels, _all_series)
                }

                _default_series_idx = 0
                for i, s in enumerate(_all_series):
                    if s.get("code") == _series_now:
                        _default_series_idx = i
                        break

                _board_load_err = None
                try:
                    board = fetch_optionboard(
                        _asset_now_all, _atype_now_all, _series_now
                    )
                except Exception as e:
                    _board_load_err = str(e)
                    board = None

                if _board_load_err:
                    st.error(
                        f"Не удалось загрузить доску для серии "
                        f"{_series_now} (эксп. {_expiry_now})."
                    )
                    with st.expander("Подробнее", expanded=False):
                        st.code(_board_load_err)
                        st.caption(
                            f"Актив: {_asset_now_all} · "
                            f"Категория: {_atype_now_all} · "
                            f"Серия: {_series_now}"
                        )

                    st.markdown("**Попробуйте выбрать другую серию:**")
                    _fallback_label = st.selectbox(
                        "Серия опционов",
                        _series_labels,
                        index=_default_series_idx,
                        key="fallback_series_selector",
                    )
                    if st.button(
                        "Переключиться на эту серию",
                        key="fallback_series_btn",
                        type="primary",
                    ):
                        _fb = _series_by_label[_fallback_label]
                        st.session_state.pos_series_code = _fb["code"]
                        st.session_state.pos_expiry = _fb["expiry"]
                        st.session_state.pop("form_series", None)
                        st.session_state.pop("form_strike", None)
                        st.rerun()

                if not board:
                    st.error(
                        f"⚠️ Форма добавления недоступна: доска опционов "
                        f"не загружена для {_asset_now_all} "
                        f"({_atype_now_all}), серия {_series_now}."
                    )
                    with st.expander("Диагностика", expanded=False):
                        st.json({
                            "pos_asset": _asset_now_all,
                            "pos_category": _atype_now_all,
                            "pos_series_code": _series_now,
                            "pos_expiry": _expiry_now,
                            "all_series_count": len(_all_series),
                            "all_series_codes_first5":
                                [s["code"] for s in _all_series[:5]],
                            "board_load_err": _board_load_err,
                            "is_cloud": _IS_CLOUD,
                        })
                        if st.button(
                            "Сбросить серию и попробовать снова",
                            key=f"reset_series_{_asset_now_all}",
                        ):
                            st.session_state.pos_series_code = ""
                            st.session_state.pos_autoloaded_for = (
                                None, None
                            )
                            try:
                                fetch_optionboard.clear()
                            except Exception:
                                pass
                            st.rerun()

                if board:
                    calls = board.get('call') or []
                    puts = board.get('put') or []
                    central = board.get('central_strike')
                    c_map = {c['strike']: c for c in calls
                             if c.get('strike') is not None}
                    p_map = {p['strike']: p for p in puts
                             if p.get('strike') is not None}
                    all_strikes = sorted(set(c_map.keys())
                                          | set(p_map.keys()))
                    asset_type_ui_now = _atype_now_all
                    asset_now = _asset_now_all

                    _instr_options = ["Опцион"]
                    if asset_type_ui_now in ("Фьючерс", "Валюта", "Товар"):
                        _instr_options.append("Фьючерс")
                    elif asset_type_ui_now == "Акция":
                        _instr_options.append("Акция")
                    else:
                        _instr_options.append("Индекс")

                    with st.container(border=True):
                        instrument_type = st.selectbox(
                            "Тип инструмента", _instr_options,
                            key="form_instrument_type",
                        )

                        chosen_series_code = None
                        expiry_now_form = _expiry_now
                        _futures_secid_form = None

                        if instrument_type == "Опцион":
                            chosen_series_label = st.selectbox(
                                "Дата исполнения", _series_labels,
                                index=_default_series_idx, key="form_series",
                            )
                            chosen_series = _series_by_label[
                                chosen_series_label
                            ]
                            expiry_now_form = chosen_series["expiry"]
                            chosen_series_code = chosen_series["code"]
                        elif instrument_type == "Фьючерс":
                            _contracts = fetch_futures_contracts_list(
                                asset_now
                            )
                            if _contracts:
                                _contract_labels = [
                                    f"{c['expiration']} — {c['secid']}"
                                    for c in _contracts
                                ]
                                _chosen = st.selectbox(
                                    "Фьючерсный контракт",
                                    _contract_labels, index=0,
                                    key="form_futures_contract",
                                )
                                _chosen_contract = _contracts[
                                    _contract_labels.index(_chosen)
                                ]
                                expiry_now_form = _chosen_contract[
                                    "expiration"
                                ]
                                _futures_secid_form = _chosen_contract[
                                    "secid"
                                ]
                            else:
                                expiry_now_form = _expiry_now
                                _futures_secid_form = asset_now
                                st.text_input(
                                    "Дата исполнения",
                                    value=_expiry_now,
                                    disabled=True,
                                    key="form_fut_exp_disp",
                                )
                        else:
                            expiry_now_form = "—"
                            st.text_input(
                                "Дата исполнения",
                                value="—", disabled=True,
                                key="form_no_exp_disp",
                            )

                        chosen_strike = None
                        opt_type = "—"

                        if instrument_type == "Опцион" and all_strikes:
                            default_idx = 0
                            if central is not None:
                                try:
                                    default_idx = all_strikes.index(
                                        min(
                                            all_strikes,
                                            key=lambda s: abs(
                                                float(s) - float(central)
                                            ),
                                        )
                                    )
                                except ValueError:
                                    default_idx = 0
                            chosen_strike = st.selectbox(
                                "Страйк", all_strikes,
                                index=default_idx, key="form_strike",
                                format_func=lambda x: _fmt_strike(
                                    x, asset_type_ui_now
                                ),
                            )
                            opt_type = st.selectbox(
                                "Опцион", ["Call", "Put"],
                                key="form_opt_type",
                            )

                        _f3c1, _f3c2 = st.columns(2)
                        with _f3c1:
                            side_ui = st.selectbox(
                                "Направление", ["Покупка", "Продажа"],
                                key="form_side",
                            )
                            side = _side_internal(side_ui)
                        with _f3c2:
                            qty_input = st.number_input(
                                "Кол-во", min_value=1, value=1,
                                step=1, key="form_qty",
                            )

                        if instrument_type == "Опцион" and opt_type == "Put":
                            st.caption(
                                "Справка: путы обычно открываются вдвое "
                                "меньшим объёмом, чем коллы"
                            )

                    if instrument_type == "Опцион":
                        _cur_price_for_check = (
                            _get_current_market_price_for_asset(
                                asset_now, asset_type_ui_now
                            )
                        )

                        _tf_level = ""
                        try:
                            _df_al_chk = st.session_state.get("alerts_df")
                            if (_df_al_chk is not None
                                    and "ТФ уровня" in _df_al_chk.columns):
                                _mask_chk = (
                                    _df_al_chk["Тикер БА"].astype(str)
                                    .str.upper().str.strip()
                                    == asset_now.upper()
                                )
                                _row_chk = _df_al_chk[_mask_chk].head(1)
                                if not _row_chk.empty:
                                    _tf_level = str(
                                        _row_chk.iloc[0].get(
                                            "ТФ уровня", ""
                                        ) or ""
                                    )
                        except Exception:
                            pass

                        _trend_v = (_alert_info_pos.get("trend", "")
                                    if _alert_info_pos else "")
                        _wave_v = (_alert_info_pos.get("wave", "")
                                   if _alert_info_pos else "")
                        _tend_v = (_alert_info_pos.get("tendency", "")
                                   if _alert_info_pos else "")

                        _checklist_items = _pretrade_checklist_state(
                            trend=_trend_v, wave=_wave_v, tendency=_tend_v,
                            tf_level=_tf_level,
                            side=side, opt_type=opt_type,
                            strike=chosen_strike,
                            current_price=_cur_price_for_check,
                            expiry_str=expiry_now_form,
                            qty=int(qty_input),
                        )
                        _render_pretrade_checklist(_checklist_items)

                    with st.form("add_position_form",
                                  clear_on_submit=False):
                        ref_opt = {}
                        _auto_price = 0.0
                        if (instrument_type == "Опцион"
                                and chosen_strike is not None):
                            ref_opt = (
                                c_map.get(chosen_strike, {})
                                if opt_type == "Call"
                                else p_map.get(chosen_strike, {})
                            )
                            _auto_price = _get_board_price(
                                chosen_strike, opt_type, side, c_map, p_map
                            )

                        price_input = st.number_input(
                            "Цена, ₽ (0 = взять из доски)",
                            min_value=0.0,
                            value=0.0, step=0.01, format="%.4f",
                            key="form_price",
                        )

                        if (instrument_type == "Опцион"
                                and chosen_strike is not None):
                            ticker_val = ref_opt.get('secid', '—')
                        elif (instrument_type == "Фьючерс"
                              and _futures_secid_form):
                            ticker_val = _futures_secid_form
                        else:
                            ticker_val = (
                                resolve_underlying_secid(
                                    asset_now, asset_type_ui_now
                                ) or '—'
                            )
                        st.text_input(
                            "Тикер (авто)", value=ticker_val,
                            disabled=True, key="form_ticker_disp",
                        )

                        use_auto_price = True
                        if (instrument_type == "Опцион"
                                and chosen_strike is not None
                                and _auto_price > 0):
                            _label = _fmt_board_label(
                                opt_type, side, chosen_strike,
                                _auto_price, asset_type_ui_now,
                            )
                            st.markdown(
                                f"<div style='background:#eef6fb; "
                                f"border-radius:10px; padding:8px 14px; "
                                f"font-size:.9rem; color:#1c5a7a; margin:6px 0;'>"
                                f"Цена из доски: "
                                f"<b style='font-family:Consolas,"
                                f"monospace; font-size:1rem;'>{_label}</b></div>",
                                unsafe_allow_html=True,
                            )
                            use_auto_price = st.checkbox(
                                "Использовать авто-цену из доски",
                                value=False, key="form_use_auto_price",
                            )
                        elif (instrument_type == "Опцион"
                              and chosen_strike is not None):
                            st.caption(
                                "Авто-цена недоступна для выбранной "
                                "комбинации"
                            )

                        submitted = st.form_submit_button(
                            "Добавить позицию", type="primary",
                            use_container_width=True,
                        )

                    if submitted:
                        if instrument_type == "Фьючерс":
                            _und_secid = _futures_secid_form or asset_now
                            _fut_info = fetch_futures_info_iss(
                                _und_secid
                            ) or {}
                            _last_price = _fut_info.get("last")
                            _fut_exp = (_fut_info.get("expiration")
                                        or expiry_now_form)
                            final_price = (
                                float(price_input) if price_input > 0
                                else (float(_last_price)
                                      if _last_price else 0.0)
                            )
                            if final_price > 0:
                                signed_qty = (int(qty_input)
                                              if side == "Buy"
                                              else -int(qty_input))
                                _pos = {
                                    "_id": _new_position_id(),
                                    "Конструкция": "Без названия",
                                    "Тип инструмента": "Фьючерс",
                                    "Опцион": "БА",
                                    "Направление": side,
                                    "Страйк": None,
                                    "Эксп.": _fut_exp,
                                    "Тикер": _und_secid,
                                    "Кол-во": signed_qty,
                                    "Цена": float(final_price),
                                    "Теор.цена": (
                                        float(_last_price)
                                        if _last_price
                                        else float(final_price)
                                    ),
                                    "Дельта": None, "Гамма": None,
                                    "Вега": None,
                                    "Тета": None, "Ро": None,
                                    "visible": True,
                                }
                                _pos = apply_parity_delta(_pos)
                                st.session_state.positions.append(_pos)
                                st.session_state["_position_add_flash"] = {
                                    "errors": [], "warnings": [],
                                    "message": (
                                        f"Добавлено: {side_ui} фьючерс "
                                        f"{_und_secid} × {qty_input} "
                                        f"по {final_price:.4f} ₽"
                                    ),
                                }
                                st.rerun()
                            else:
                                st.error(
                                    "Не удалось определить цену фьючерса."
                                )

                        elif instrument_type in ("Акция", "Индекс"):
                            _und_secid = resolve_underlying_secid(
                                asset_now, asset_type_ui_now
                            )
                            _ba_info = fetch_ba_iss_info(
                                _und_secid, asset_type_ui_now
                            ) or {}
                            _last_price = _ba_info.get("last")
                            final_price = (
                                float(price_input) if price_input > 0
                                else (float(_last_price)
                                      if _last_price else 0.0)
                            )
                            if final_price > 0:
                                signed_qty = (int(qty_input)
                                              if side == "Buy"
                                              else -int(qty_input))
                                _pos = {
                                    "_id": _new_position_id(),
                                    "Конструкция": "Без названия",
                                    "Тип инструмента": instrument_type,
                                    "Опцион": "БА",
                                    "Направление": side,
                                    "Страйк": None,
                                    "Эксп.": "—",
                                    "Тикер": _und_secid or '—',
                                    "Кол-во": signed_qty,
                                    "Цена": float(final_price),
                                    "Теор.цена": (
                                        float(_last_price)
                                        if _last_price
                                        else float(final_price)
                                    ),
                                    "Дельта": None, "Гамма": None,
                                    "Вега": None,
                                    "Тета": None, "Ро": None,
                                    "visible": True,
                                }
                                _pos = apply_parity_delta(_pos)
                                st.session_state.positions.append(_pos)
                                st.session_state["_position_add_flash"] = {
                                    "errors": [], "warnings": [],
                                    "message": (
                                        f"Добавлено: {side_ui} "
                                        f"{instrument_type} "
                                        f"{_und_secid} × {qty_input} "
                                        f"по {final_price:.4f} ₽"
                                    ),
                                }
                                st.rerun()
                            else:
                                st.error(
                                    "Не удалось определить цену БА."
                                )

                        else:  # Опцион
                            if chosen_strike is None:
                                st.error("Укажите страйк.")
                            else:
                                _sel_series_code = (
                                    chosen_series_code or _series_now
                                )
                                if _sel_series_code != _series_now:
                                    try:
                                        _board_sel = fetch_optionboard(
                                            asset_now, asset_type_ui_now,
                                            _sel_series_code,
                                        )
                                        _calls_sel = (
                                            _board_sel.get('call') or []
                                        )
                                        _puts_sel = (
                                            _board_sel.get('put') or []
                                        )
                                        _c_map_sel = {
                                            c['strike']: c
                                            for c in _calls_sel
                                            if c.get('strike') is not None
                                        }
                                        _p_map_sel = {
                                            p['strike']: p
                                            for p in _puts_sel
                                            if p.get('strike') is not None
                                        }
                                    except Exception:
                                        _c_map_sel = c_map
                                        _p_map_sel = p_map
                                else:
                                    _c_map_sel = c_map
                                    _p_map_sel = p_map

                                ref = (
                                    _c_map_sel.get(chosen_strike, {})
                                    if opt_type == "Call"
                                    else _p_map_sel.get(chosen_strike, {})
                                )
                                final_pos_price = None
                                _src_label = ""

                                if use_auto_price:
                                    _auto_p_sel = _get_board_price(
                                        chosen_strike, opt_type, side,
                                        _c_map_sel, _p_map_sel,
                                    )
                                    if _auto_p_sel > 0:
                                        final_pos_price = float(_auto_p_sel)
                                        _src_label = " (из доски)"

                                if (final_pos_price is None
                                        and price_input > 0):
                                    final_pos_price = float(price_input)
                                    _src_label = " (ручная)"

                                if (final_pos_price is None
                                        or final_pos_price <= 0):
                                    st.error(
                                        "Укажите цену > 0 или включите "
                                        "авто-цену."
                                    )
                                else:
                                    _cp_leg = (
                                        _get_current_market_price_for_asset(
                                            asset_now, asset_type_ui_now
                                        )
                                    )
                                    _errs, _warns = _validate_options_leg(
                                        side=side, opt_type=opt_type,
                                        strike=chosen_strike,
                                        expiry_str=expiry_now_form,
                                        qty=int(qty_input),
                                        current_price=_cp_leg,
                                    )

                                    _r_leg_add, _q_leg_add = (
                                        _compute_r_q_for_leg(
                                            asset_now, asset_type_ui_now,
                                            expiry_now_form,
                                        )
                                    )

                                    signed_qty = (int(qty_input)
                                                  if side == "Buy"
                                                  else -int(qty_input))
                                    st.session_state.positions.append({
                                        "_id": _new_position_id(),
                                        "Конструкция": "Без названия",
                                        "Тип инструмента": "Опцион",
                                        "Опцион": opt_type,
                                        "Направление": side,
                                        "Страйк": chosen_strike,
                                        "Эксп.": expiry_now_form,
                                        "Тикер": ref.get('secid', '—'),
                                        "Кол-во": signed_qty,
                                        "Цена": float(final_pos_price),
                                        "Теор.цена": None,
                                        "Дельта": ref.get('delta'),
                                        "Гамма": ref.get('gamma'),
                                        "Вега": ref.get('vega'),
                                        "Тета": ref.get('theta'),
                                        "Ро": ref.get('rho'),
                                        "visible": True,
                                        "_vol": float(
                                            ref.get('volatility') or 20.0
                                        ),
                                        "_r": float(_r_leg_add),
                                        "_q": float(_q_leg_add),
                                        "_series_code": _sel_series_code,
                                        "_asset": asset_now,
                                        "_atype": asset_type_ui_now,
                                    })

                                    _success_msg = (
                                        f"Добавлено: {side_ui} "
                                        f"{opt_type} "
                                        f"{chosen_strike} × {qty_input} "
                                        f"[{expiry_now_form}]"
                                        f"{_src_label} "
                                        f"по {final_pos_price:.4f} ₽"
                                    )
                                    st.session_state[
                                        "_position_add_flash"
                                    ] = {
                                        "errors": _errs,
                                        "warnings": _warns,
                                        "message": _success_msg,
                                    }
                                    st.rerun()

    # ==================================================================
    # ЖИВОЕ ОБНОВЛЕНИЕ ТЕОР. ЦЕНЫ И ГРЕКОВ
    # ==================================================================
    _pos_asset_r = st.session_state.get("pos_asset", "")
    _pos_atype_r = st.session_state.get("pos_category", "")
    _pos_series_r = st.session_state.get("pos_series_code", "")

    if _pos_asset_r and _pos_atype_r and st.session_state.positions:
        _series_to_fetch = set()
        for _lp in st.session_state.positions:
            if _lp.get("Тип инструмента") != "Опцион":
                continue
            _s = _lp.get("_series_code") or _pos_series_r
            if _s:
                _series_to_fetch.add(_s)

        _boards_live = {}
        for _s in _series_to_fetch:
            _boards_live[_s] = _fetch_board_cached_30s(
                _pos_asset_r, _pos_atype_r, _s
            )

        def _estimate_F(board):
            if not board:
                return None
            _calls = board.get("call") or []
            _puts = board.get("put") or []
            _c_map = {
                c["strike"]: c for c in _calls
                if c.get("theorprice") and c.get("strike") is not None
            }
            _p_map = {
                p["strike"]: p for p in _puts
                if p.get("theorprice") and p.get("strike") is not None
            }
            _commons = sorted(set(_c_map) & set(_p_map))
            _fs_est = []
            for _k in _commons:
                _ct = _c_map[_k]["theorprice"]
                _pt = _p_map[_k]["theorprice"]
                if _ct and _pt and _ct > 0 and _pt > 0:
                    _fs_est.append(_ct - _pt + float(_k))
            if _fs_est:
                _fs_est.sort()
                return _fs_est[len(_fs_est) // 2]
            return None

        _updated = 0
        for _lp in st.session_state.positions:
            if _lp.get("Тип инструмента") != "Опцион":
                continue
            _s = _lp.get("_series_code") or _pos_series_r
            _b = _boards_live.get(_s)
            if not _b:
                _lp["Теор.цена"] = None
                continue

            _row = _find_board_row_by_secid(_b, _lp.get("Тикер"))
            if _row is None:
                _side = ("call" if _lp.get("Опцион") == "Call" else "put")
                _row = _find_board_row(_b, _side, _lp.get("Страйк"))
            if _row is None:
                _lp["Теор.цена"] = None
                continue

            _lp["Дельта"] = _row.get("delta")
            _lp["Гамма"] = _row.get("gamma")
            _lp["Вега"] = _row.get("vega")
            _lp["Тета"] = _row.get("theta")
            _lp["Ро"] = _row.get("rho")
            _iv = _row.get("volatility")
            if _iv is not None:
                try:
                    _lp["_vol"] = float(_iv)
                except (TypeError, ValueError):
                    pass

            _th = _row.get("theorprice")
            _th_ok = False
            if _th is not None:
                try:
                    _th_f = float(_th)
                    if _th_f > 0:
                        _lp["Теор.цена"] = _th_f
                        _th_ok = True
                        _updated += 1
                except (TypeError, ValueError):
                    pass

            if not _th_ok:
                _opp_side = ("put" if _lp.get("Опцион") == "Call"
                             else "call")
                _opp_list = _b.get(_opp_side) or []
                _opp = next(
                    (
                        x for x in _opp_list
                        if _strike_eq(x.get("strike"), _lp.get("Страйк"))
                    ),
                    None,
                )
                _th_opp = (_opp or {}).get("theorprice")
                _k_f = float(_lp.get("Страйк") or 0)
                _F = _estimate_F(_b)
                if _th_opp is not None and _F is not None:
                    try:
                        _th_opp_f = float(_th_opp)
                        if _th_opp_f > 0:
                            if _lp.get("Опцион") == "Call":
                                _th_calc = _th_opp_f + (_F - _k_f)
                            else:
                                _th_calc = _th_opp_f + (_k_f - _F)
                            if _th_calc > 0:
                                _lp["Теор.цена"] = float(_th_calc)
                                _updated += 1
                            else:
                                _lp["Теор.цена"] = None
                        else:
                            _lp["Теор.цена"] = None
                    except (TypeError, ValueError):
                        _lp["Теор.цена"] = None
                else:
                    _lp["Теор.цена"] = None

        _ck = st.session_state.get("_pos_active_key")
        if _ck:
            _new_sig = _positions_signature(st.session_state.positions)
            if _new_sig != st.session_state.get("_pos_signature"):
                st.session_state.positions_cache[_ck] = list(
                    st.session_state.positions
                )
                _ok_save = _save_cache(st.session_state.positions_cache)
                if _ok_save:
                    st.session_state["_last_save_ts"] = datetime.now().strftime(
                        "%H:%M:%S"
                    )
                    st.session_state["_last_save_count"] = _updated
                st.session_state["_pos_signature"] = _new_sig

    # ==================================================================
    # ТЕКУЩИЕ ПОЗИЦИИ
    # ==================================================================
    st.markdown("---")
    st.markdown("### Текущие позиции")

    _pos_cat_for_fmt = st.session_state.get("pos_category", "")

    if not st.session_state.positions:
        st.caption("Портфель пуст.")
    elif st.session_state.get("pos_view_mode") == "Карточки (мобильный)":
        for idx, p in enumerate(st.session_state.positions):
            _id = p.get("_id", f"legacy_{idx}")
            _visible = p.get("visible", True)
            _gray = "opacity:0.45;" if not _visible else ""

            _instr = p.get("Тип инструмента", "Опцион")
            _isBA = (_instr == "БА" or p.get("Опцион") == "БА")
            _qty = int(p.get("Кол-во", 0))
            _price = float(p.get("Цена", 0))
            _theor = float(p.get("Теор.цена") or 0)
            _com = 0.0 if _isBA else _calc_comm(_price, _instr)
            _pnl = ((_theor - _price) * _qty if _isBA
                    else (_theor - _price - _com) * _qty)
            _color_pnl = ("#00a651" if _pnl > 0
                          else "#d32f2f" if _pnl < 0 else "#333")

            _exp_str = p.get("Эксп.", "—")
            _dte = "—"
            if _exp_str and _exp_str != "—":
                try:
                    _d = datetime.strptime(_exp_str, "%Y-%m-%d").date()
                    _dte = f"{(_d - date.today()).days}"
                except Exception:
                    pass
            _plan_badge = _planned_close_badge(_exp_str)
            _plan_date = _planned_close_date_str(_exp_str)

            _opt = p.get("Опцион", "—")
            _opt_color = ("#00a651" if _opt == "Call"
                          else "#d32f2f" if _opt == "Put" else "#333")
            _atype_row = p.get("_atype", _pos_cat_for_fmt)
            _k_str = _fmt_strike(p.get("Страйк"), _atype_row)

            with st.container(border=True):
                _h1c1, _h1c2, _h1c3 = st.columns([3, 2, 1])
                with _h1c1:
                    st.markdown(
                        f"<div style='{_gray}'>"
                        f"<span style='font-size:1.1rem; font-weight:700;'>"
                        f"{p.get('Тикер', '—')}</span> "
                        f"<span style='color:{_opt_color}; font-weight:700;'>"
                        f"{_opt}</span> "
                        f"<span style='font-weight:700;'>"
                        f"{_k_str}</span></div>",
                        unsafe_allow_html=True,
                    )
                with _h1c2:
                    st.markdown(
                        f"<div style='{_gray}; font-size:.85rem; "
                        f"text-align:right;'>"
                        f"<b>{_instr}</b><br>"
                        f"до эксп.: {_dte} дн.</div>",
                        unsafe_allow_html=True,
                    )
                with _h1c3:
                    if st.button(
                        "✖", key=f"m_del_{_id}",
                        use_container_width=True,
                    ):
                        st.session_state.positions.pop(idx)
                        for k in list(st.session_state.keys()):
                            if k.endswith(f"_{_id}"):
                                del st.session_state[k]
                        st.rerun()

                _m_row1, _m_row2 = st.columns(2)
                with _m_row1:
                    st.markdown(
                        f"<div style='{_gray}; font-size:.82rem;'>"
                        f"Экспирация: <b>{_exp_str}</b><br>"
                        f"План. закрытие: <b>{_plan_date}</b>"
                        f"{_plan_badge}</div>",
                        unsafe_allow_html=True,
                    )
                with _m_row2:
                    st.markdown(
                        f"<div style='{_gray}; font-size:.82rem;'>"
                        f"Кол-во: <b>{_qty}</b><br>"
                        f"Цена: <b>{_price:.4f}</b><br>"
                        f"Теор.: <b>{_theor:.4f}</b></div>",
                        unsafe_allow_html=True,
                    )

                _d_val = p.get('Дельта')
                _g_val = p.get('Гамма')
                _v_val = p.get('Вега')
                _t_val = p.get('Тета')
                st.markdown(
                    f"<div style='margin-top:6px; padding:8px 10px; "
                    f"background:#f6f9fc; border-radius:6px; "
                    f"font-size:.85rem;'>"
                    f"P&L: <b style='color:{_color_pnl};'>"
                    f"{_pnl:+,.2f} ₽</b> &nbsp;·&nbsp; "
                    f"Δ={_d_val if isinstance(_d_val, (int, float)) else '—'} "
                    f"Γ={_g_val if isinstance(_g_val, (int, float)) else '—'} "
                    f"ν={_v_val if isinstance(_v_val, (int, float)) else '—'} "
                    f"Θ={_t_val if isinstance(_t_val, (int, float)) else '—'}"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                _m_qty_col, _m_price_col, _m_vis_col = st.columns([2, 2, 1])
                with _m_qty_col:
                    kq = f"qty_{_id}"
                    if kq not in st.session_state:
                        st.session_state[kq] = int(p.get("Кол-во", 1))
                    st.number_input(
                        "Кол-во", min_value=-10000, max_value=10000,
                        step=1, key=kq,
                        on_change=_cb_qty_sync, args=(kq, _id),
                    )
                with _m_price_col:
                    kp = f"price_{_id}"
                    if kp not in st.session_state:
                        st.session_state[kp] = float(p.get("Цена", 0.0))
                    st.number_input(
                        "Цена", min_value=0.0, step=0.01,
                        format="%.4f", key=kp,
                        on_change=_cb_price_sync, args=(kp, _id),
                    )
                with _m_vis_col:
                    st.markdown(
                        "<div style='height:1.5rem;'></div>",
                        unsafe_allow_html=True,
                    )
                    _icon = "👁" if _visible else "🚫"
                    if st.button(
                        _icon, key=f"m_vis_{_id}",
                        use_container_width=True,
                    ):
                        p["visible"] = not _visible
                        st.rerun()

        st.markdown("---")
        _m_sum1, _m_sum2 = st.columns(2)
        with _m_sum1:
            _tot_pnl_mobile = 0.0
            for p in st.session_state.positions:
                if not p.get("visible", True):
                    continue
                _q = int(p.get("Кол-во", 0))
                _pr = float(p.get("Цена", 0))
                _th = float(p.get("Теор.цена") or 0)
                _instr = p.get("Тип инструмента", "Опцион")
                _isBA = (_instr == "БА" or p.get("Опцион") == "БА")
                if _isBA:
                    _tot_pnl_mobile += (_th - _pr) * _q
                else:
                    _c = _calc_comm(_pr, _instr)
                    _tot_pnl_mobile += (_th - _pr - _c) * _q
            _c_pnl_m = ("#00a651" if _tot_pnl_mobile > 0
                        else "#d32f2f" if _tot_pnl_mobile < 0 else "#333")
            st.markdown(
                f"<div style='font-size:1rem;'>"
                f"<b>Итого P&L:</b> "
                f"<span style='color:{_c_pnl_m}; font-weight:800;'>"
                f"{_tot_pnl_mobile:+,.2f} ₽</span></div>",
                unsafe_allow_html=True,
            )
        with _m_sum2:
            if st.button(
                "Обновить позиции",
                type="primary", use_container_width=True,
                key="m_refresh_positions",
            ):
                st.rerun()
    else:
        _pos_container = st.container(border=True)
        with _pos_container:
            st.markdown("<div class='pos-row-wrap'>", unsafe_allow_html=True)
            COL_W = [0.28, 0.32, 0.85, 0.75, 0.85, 0.95, 0.65, 1.10, 1.30,
                     1.05, 1.20, 1.15, 1.15, 1.15, 0.95, 0.85, 0.85, 0.85,
                     0.75, 0.95]
            HEADERS = ["", "", "Тип", "Опцион", "Страйк", "Дата исп.",
                       "До эксп.", "План. закрытие", "Тикер", "Кол-во",
                       "Цена", "Ср. цена", "Теор. цена", "P&L", "Дельта",
                       "Гамма", "Вега", "Тета", "Ро", "Комисс."]

            _hdr = st.columns(COL_W)
            for c, h in zip(_hdr, HEADERS):
                with c:
                    st.markdown(
                        f"<div style='font-size:.68rem; color:#2c506d; "
                        f"font-weight:700; text-transform:uppercase; "
                        f"letter-spacing:.02em; padding-top:4px;'>{h}</div>",
                        unsafe_allow_html=True,
                    )

            st.markdown(
                "<hr style='margin:2px 0 4px 0; border:none; "
                "border-top:1px solid #e6edf4;'>",
                unsafe_allow_html=True,
            )

            _today = date.today()
            for idx, p in enumerate(st.session_state.positions):
                _id = p.get("_id", f"legacy_{idx}")
                visible = p.get("visible", True)
                gray = "opacity:0.45;" if not visible else ""

                row = st.columns(COL_W)

                with row[0]:
                    if st.button(
                        "✖", key=f"del_{_id}", help="Удалить строку",
                        use_container_width=True,
                    ):
                        st.session_state.positions.pop(idx)
                        for k in list(st.session_state.keys()):
                            if k.endswith(f"_{_id}"):
                                del st.session_state[k]
                        st.rerun()

                with row[1]:
                    icon = "👁" if visible else "🚫"
                    if st.button(
                        icon, key=f"vis_{_id}",
                        help="Скрыть/показать в профиле и расчётах",
                        use_container_width=True,
                    ):
                        p["visible"] = not visible
                        st.rerun()

                with row[2]:
                    _t = p.get("Тип инструмента", "Опцион")
                    _short_t = {"Опцион": "Опцион", "Фьючерс": "Фьюч.",
                                "Акция": "Акция", "Индекс": "Индекс"}.get(
                        _t, _t
                    )
                    st.markdown(
                        f"<div style='padding-top:6px; {gray}; "
                        f"font-size:.82rem;'>{_short_t}</div>",
                        unsafe_allow_html=True,
                    )

                with row[3]:
                    _opt = p.get("Опцион", "—")
                    _opt_html = (
                        _color_call_put(_opt)
                        if _opt in ("Call", "Put") else _opt
                    )
                    st.markdown(
                        f"<div style='padding-top:6px; {gray};'>"
                        f"{_opt_html}</div>",
                        unsafe_allow_html=True,
                    )

                with row[4]:
                    _k = p.get("Страйк")
                    _atype_row = p.get("_atype", _pos_cat_for_fmt)
                    _k_txt = (
                        f"<b>{_fmt_strike(_k, _atype_row)}</b>"
                        if _k is not None else "—"
                    )
                    st.markdown(
                        f"<div style='padding-top:6px; {gray};'>"
                        f"{_k_txt}</div>",
                        unsafe_allow_html=True,
                    )

                with row[5]:
                    _exp_disp = p.get("Эксп.", "—")
                    try:
                        _exp_disp_fmt = datetime.strptime(
                            _exp_disp, "%Y-%m-%d"
                        ).strftime("%d.%m.%Y")
                    except Exception:
                        _exp_disp_fmt = _exp_disp
                    st.markdown(
                        f"<div style='padding-top:6px; {gray}; "
                        f"font-size:.78rem;'>{_exp_disp_fmt}</div>",
                        unsafe_allow_html=True,
                    )

                with row[6]:
                    _dte = "—"
                    _exp_str = p.get("Эксп.", "—")
                    if _exp_str and _exp_str != "—":
                        try:
                            _d = datetime.strptime(
                                _exp_str, "%Y-%m-%d"
                            ).date()
                            _dte = f"{(_d - _today).days}"
                        except Exception:
                            pass
                    st.markdown(
                        f"<div style='padding-top:6px; {gray}; "
                        f"font-size:.82rem;'>{_dte}</div>",
                        unsafe_allow_html=True,
                    )

                with row[7]:
                    _exp_str = p.get("Эксп.", "—")
                    _plan_date_str = _planned_close_date_str(_exp_str)
                    _plan_badge = _planned_close_badge(_exp_str)
                    st.markdown(
                        f"<div style='padding-top:6px; {gray}; "
                        f"font-size:.78rem;'>{_plan_date_str}</div>"
                        f"{_plan_badge}",
                        unsafe_allow_html=True,
                    )

                with row[8]:
                    st.markdown(
                        f"<div style='padding-top:6px; {gray}; "
                        f"font-size:.78rem;'>{p.get('Тикер', '—')}</div>",
                        unsafe_allow_html=True,
                    )

                with row[9]:
                    kq = f"qty_{_id}"
                    if kq not in st.session_state:
                        st.session_state[kq] = int(p.get("Кол-во", 1))
                    qc = st.columns([1, 3, 1])
                    with qc[0]:
                        st.button(
                            "➖", key=f"qm_{_id}",
                            on_click=_cb_qty_dec, args=(kq, _id),
                            use_container_width=True,
                        )
                    with qc[1]:
                        st.number_input(
                            "qty", min_value=-10000, max_value=10000, step=1,
                            key=kq, label_visibility="collapsed",
                            on_change=_cb_qty_sync, args=(kq, _id),
                        )
                    with qc[2]:
                        st.button(
                            "➕", key=f"qp_{_id}",
                            on_click=_cb_qty_inc, args=(kq, _id),
                            use_container_width=True,
                        )

                with row[10]:
                    kp = f"price_{_id}"
                    if kp not in st.session_state:
                        st.session_state[kp] = float(p.get("Цена", 0.0))
                    pc = st.columns([1, 3, 1])
                    with pc[0]:
                        st.button(
                            "➖", key=f"pm_{_id}",
                            on_click=_cb_price_dec, args=(kp, _id),
                            use_container_width=True,
                        )
                    with pc[1]:
                        st.number_input(
                            "price", min_value=0.0,
                            step=0.01, format="%.4f",
                            key=kp, label_visibility="collapsed",
                            on_change=_cb_price_sync, args=(kp, _id),
                        )
                    with pc[2]:
                        st.button(
                            "➕", key=f"pp_{_id}",
                            on_click=_cb_price_inc, args=(kp, _id),
                            use_container_width=True,
                        )

                _theor = float(p.get("Теор.цена") or 0)
                _instr = p.get("Тип инструмента", "Опцион")
                _isBA = (_instr == "БА" or p.get("Опцион") == "БА")
                _com = (0.0 if _isBA
                        else _calc_comm(float(p.get("Цена", 0)), _instr))
                _qty = int(p.get("Кол-во", 0))
                _price = float(p.get("Цена", 0))
                _eff_price = _price + _com
                _pnl = ((_theor - _price) * _qty if _isBA
                        else (_theor - _eff_price) * _qty)

                with row[11]:
                    st.markdown(
                        f"<div style='padding-top:6px; {gray}; "
                        f"font-size:.82rem; font-weight:600; color:#7c4dff;'>"
                        f"{_eff_price:.4f}</div>",
                        unsafe_allow_html=True,
                    )

                with row[12]:
                    _theor_disp = (
                        f"{_theor:.4f}" if _theor and _theor > 0 else "—"
                    )
                    st.markdown(
                        f"<div style='padding-top:6px; {gray}; "
                        f"font-size:.82rem;'>{_theor_disp}</div>",
                        unsafe_allow_html=True,
                    )

                with row[13]:
                    _color = ("#00a651" if _pnl > 0
                              else "#d32f2f" if _pnl < 0 else "#333")
                    st.markdown(
                        f"<div style='padding-top:6px; {gray}; "
                        f"font-weight:700; color:{_color};'>"
                        f"{_pnl:+,.2f} ₽</div>",
                        unsafe_allow_html=True,
                    )

                for ri, gr in zip([14, 15, 16, 17, 18],
                                   ["Дельта", "Гамма", "Вега",
                                    "Тета", "Ро"]):
                    val = p.get(gr)
                    txt = (f"{val:+.4f}"
                           if isinstance(val, (int, float)) else "—")
                    _greek_color = ""
                    if gr == "Дельта":
                        _greek_color = _greek_delta_color(val)
                    elif gr == "Тета":
                        _greek_color = _greek_theta_color(
                            val, p.get("Вега")
                        )
                    _style_color = (
                        f"color:{_greek_color}; font-weight:700;"
                        if _greek_color else ""
                    )
                    with row[ri]:
                        st.markdown(
                            f"<div style='padding-top:6px; {gray}; "
                            f"font-size:.78rem; {_style_color}'>{txt}</div>",
                            unsafe_allow_html=True,
                        )

                with row[19]:
                    st.markdown(
                        f"<div style='padding-top:6px; {gray}; "
                        f"font-size:.78rem;'>{_com:.2f}</div>",
                        unsafe_allow_html=True,
                    )

            st.markdown(
                "<hr style='margin:4px 0 4px 0; border:none; "
                "border-top:1px solid #e6edf4;'>",
                unsafe_allow_html=True,
            )

            _tot_com = _tot_pnl = _tot_delta = _tot_gamma = 0.0
            _tot_vega = _tot_theta = _tot_rho = 0.0
            for p in st.session_state.positions:
                if not p.get("visible", True):
                    continue
                _q = int(p.get("Кол-во", 0))
                _pr = float(p.get("Цена", 0))
                _th = float(p.get("Теор.цена") or 0)
                _instr = p.get("Тип инструмента", "Опцион")
                _isBA = (_instr == "БА" or p.get("Опцион") == "БА")
                if _isBA:
                    _c = 0.0
                    _pl = (_th - _pr) * _q
                else:
                    _c = _calc_comm(_pr, _instr)
                    _pl = (_th - _pr - _c) * _q
                _tot_com += _c * abs(_q)
                _tot_pnl += _pl
                _tot_delta += (p.get("Дельта") or 0) * _q
                _tot_gamma += (p.get("Гамма") or 0) * _q
                _tot_vega += (p.get("Вега") or 0) * _q
                _tot_theta += (p.get("Тета") or 0) * _q
                _tot_rho += (p.get("Ро") or 0) * _q

            tot_row = st.columns(COL_W)
            with tot_row[0]:
                if st.button(
                    "🗑", key="clear_all_positions",
                    help="Удалить все позиции",
                    use_container_width=True,
                ):
                    for k in list(st.session_state.keys()):
                        if k.startswith(("qty_", "price_", "qp_", "qm_",
                                          "pp_", "pm_")):
                            del st.session_state[k]
                    st.session_state.positions = []
                    st.rerun()
            with tot_row[8]:
                st.markdown(
                    "<div style='padding-top:6px; font-size:.8rem; "
                    "color:#2c506d; font-weight:700;'>"
                    "Итого (видимые):</div>",
                    unsafe_allow_html=True,
                )

            _color_pnl_tot = ("#00a651" if _tot_pnl > 0
                              else "#d32f2f" if _tot_pnl < 0 else "#333")
            with tot_row[13]:
                st.markdown(
                    f"<div style='padding-top:6px; font-weight:800; "
                    f"color:{_color_pnl_tot};'>{_tot_pnl:+,.2f} ₽</div>",
                    unsafe_allow_html=True,
                )
            with tot_row[14]:
                st.markdown(
                    f"<div style='padding-top:6px; font-weight:700;'>"
                    f"{_tot_delta:+.3f}</div>",
                    unsafe_allow_html=True,
                )
            with tot_row[15]:
                st.markdown(
                    f"<div style='padding-top:6px; font-weight:700;'>"
                    f"{_tot_gamma:+.4f}</div>",
                    unsafe_allow_html=True,
                )
            with tot_row[16]:
                st.markdown(
                    f"<div style='padding-top:6px; font-weight:700;'>"
                    f"{_tot_vega:+.3f}</div>",
                    unsafe_allow_html=True,
                )
            with tot_row[17]:
                st.markdown(
                    f"<div style='padding-top:6px; font-weight:700;'>"
                    f"{_tot_theta:+.3f}</div>",
                    unsafe_allow_html=True,
                )
            with tot_row[18]:
                st.markdown(
                    f"<div style='padding-top:6px; font-weight:700;'>"
                    f"{_tot_rho:+.3f}</div>",
                    unsafe_allow_html=True,
                )
            with tot_row[19]:
                st.markdown(
                    f"<div style='padding-top:6px; font-weight:700;'>"
                    f"{_tot_com:,.4f}</div>",
                    unsafe_allow_html=True,
                )

            st.markdown("</div>", unsafe_allow_html=True)

        _save_ts = st.session_state.get("_last_save_ts", "—")
        _save_cnt = st.session_state.get("_last_save_count", 0)
        _cache_file_now = _cache_file()
        _cache_file_str = str(_cache_file_now) if _cache_file_now else "—"
        st.caption(
            f"Автосохранение включено · "
            f"последняя запись: {_save_ts} · "
            f"обновлено цен: {_save_cnt} · "
            f"файл: {_cache_file_str}"
        )

        _btn1, _btn2, _btn3, _btn4 = st.columns([1, 1, 1, 2])
        with _btn1:
            _refresh_clicked = st.button(
                "Обновить позицию",
                use_container_width=True,
                type="primary",
                key="positions_refresh_btn",
            )
        with _btn2:
            if "positions_cache" in st.session_state:
                _cur_active = st.session_state.get("_pos_active_key")
                if _cur_active:
                    st.session_state.positions_cache[_cur_active] = list(
                        st.session_state.positions
                    )
                    _save_cache(st.session_state.positions_cache)

            _cache_json_bytes = json.dumps(
                st.session_state.get("positions_cache", {}),
                ensure_ascii=False, indent=2, default=str,
            ).encode("utf-8")
            st.download_button(
                "Только позиции · JSON",
                data=_cache_json_bytes,
                file_name=f"positions_{date.today().isoformat()}.json",
                mime="application/json",
                use_container_width=True,
                key="positions_export_json_btn",
                help="Скачать файл только с позициями "
                     "(без журнала и настроек). "
                     "Для полной резервной копии используйте "
                     "«Резервная копия · JSON».",
            )
        with _btn3:
            _export_rows = []
            for i, p in enumerate(st.session_state.positions):
                _q = int(p.get("Кол-во", 0))
                _pr = float(p.get("Цена", 0))
                _instr = p.get("Тип инструмента", "Опцион")
                _isBA = (_instr == "БА" or p.get("Опцион") == "БА")
                _c = 0.0 if _isBA else _calc_comm(_pr, _instr)
                _export_rows.append({
                    "Актив": st.session_state.get("pos_asset", ""),
                    "Категория": st.session_state.get("pos_category", ""),
                    "#": i + 1, "Тип": _instr,
                    "Опцион": p.get("Опцион", "—"),
                    "Направление": _side_ui(
                        p.get("Направление", _side_from_qty(_q))
                    ),
                    "Страйк": p.get("Страйк", "—"),
                    "Эксп.": p.get("Эксп.", "—"),
                    "Тикер": p.get("Тикер", "—"),
                    "Кол-во": _q,
                    "Цена": _pr,
                    "Комиссия": round(_c, 2),
                    "Эфф. цена": round(_pr + _c, 4),
                })
            _df_export = pd.DataFrame(_export_rows)
            st.download_button(
                "Экспорт CSV",
                data=_df_export.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"portfolio_{date.today().isoformat()}.csv",
                mime="text/csv",
                use_container_width=True,
                key="positions_export_btn",
                help="Только кол-во и цена входа — без live-полей",
            )
        with _btn4:
            st.caption(
                "Для восстановления полного состояния или старого JSON "
                "позиций используйте «Резервная копия · JSON»."
            )

        if _refresh_clicked:
            st.rerun()

    # ==================================================================
    # АНАЛИЗ ПОЗИЦИИ (ДЕБЕТ/КРЕДИТ, РИСК, КЛАССИФИКАЦИЯ СТРАТЕГИИ)
    # ==================================================================
    if st.session_state.positions:
        _visible_positions = [
            p for p in st.session_state.positions
            if p.get("visible", True)
        ]

        _net_cash_flow = 0.0
        for p in _visible_positions:
            if p.get("Опцион") == "БА":
                continue
            _q = int(p.get("Кол-во", 0))
            _pr = float(p.get("Цена", 0))
            _instr = p.get("Тип инструмента", "Опцион")
            _c = _calc_comm(_pr, _instr)
            if _q > 0:
                _net_cash_flow -= (_pr * _q + _c * abs(_q))
            else:
                _net_cash_flow += (_pr * abs(_q) - _c * abs(_q))

        if _net_cash_flow < -0.01:
            _position_type = "ДЕБЕТОВАЯ"
            _position_bg = "rgba(0,166,81,0.10)"
            _position_border = "#00a651"
            _position_fg = "#0a6b32"
        elif _net_cash_flow > 0.01:
            _position_type = "КРЕДИТОВАЯ"
            _position_bg = "rgba(211,47,47,0.10)"
            _position_border = "#d32f2f"
            _position_fg = "#8b1e1e"
        else:
            _position_type = "НУЛЕВАЯ"
            _position_bg = "rgba(122,122,122,0.10)"
            _position_border = "#7a7a7a"
            _position_fg = "#4a4a4a"

        _short_calls_list = []
        _long_calls_list = []
        _short_puts_list = []
        _long_puts_list = []
        _ba_long_qty = 0
        _ba_short_qty = 0

        for p in _visible_positions:
            _opt = p.get("Опцион", "—")
            _q = int(p.get("Кол-во", 0))
            if _q == 0:
                continue
            if _opt in ("Call", "Put"):
                _k = float(p.get("Страйк", 0) or 0)
                if _opt == "Call":
                    if _q < 0:
                        _short_calls_list.append((_k, abs(_q)))
                    else:
                        _long_calls_list.append((_k, abs(_q)))
                else:
                    if _q < 0:
                        _short_puts_list.append((_k, abs(_q)))
                    else:
                        _long_puts_list.append((_k, abs(_q)))
            elif _opt == "БА" or p.get("Тип инструмента") == "БА":
                if _q > 0:
                    _ba_long_qty += _q
                else:
                    _ba_short_qty += abs(_q)

        _short_calls_uncovered = 0
        for _k_s, _q_s in _short_calls_list:
            _covered = _ba_long_qty
            for _k_l, _q_l in _long_calls_list:
                if _k_l <= _k_s:
                    _covered += _q_l
            _short_calls_uncovered += max(0, _q_s - _covered)

        _short_puts_uncovered = 0
        for _k_s, _q_s in _short_puts_list:
            _covered = _ba_short_qty
            for _k_l, _q_l in _long_puts_list:
                if _k_l >= _k_s:
                    _covered += _q_l
            _short_puts_uncovered += max(0, _q_s - _covered)

        _risk_is_unlimited = (_short_calls_uncovered > 0
                              or _short_puts_uncovered > 0)

        _risk_value = 0.0
        try:
            _strikes_all_r = [
                float(p["Страйк"]) for p in _visible_positions
                if p.get("Страйк") is not None
            ]
            _ba_prices_r = [
                float(p["Цена"]) for p in _visible_positions
                if p.get("Опцион") == "БА"
                or p.get("Тип инструмента") == "БА"
            ]
            _all_ref_r = _strikes_all_r + _ba_prices_r

            if _all_ref_r:
                _s_low_r = max(0.01, min(_all_ref_r) * 0.5)
                _s_high_r = max(_all_ref_r) * 1.5
                _S_eval_r = np.linspace(_s_low_r, _s_high_r, 800)
                _pnl_eval_r = compute_payoff(
                    _visible_positions, _S_eval_r,
                    comm_func=_calc_comm,
                )
                _min_pnl_r = float(np.min(_pnl_eval_r))
                _risk_value = max(0.0, -_min_pnl_r)
        except Exception:
            _risk_value = (abs(_net_cash_flow)
                           if _net_cash_flow < 0 else 0.0)

        if _risk_is_unlimited:
            _risk_display = "∞"
            _uncovered_details = []
            if _short_calls_uncovered > 0:
                _uncovered_details.append(f"{_short_calls_uncovered} Call")
            if _short_puts_uncovered > 0:
                _uncovered_details.append(f"{_short_puts_uncovered} Put")
            _risk_extra = f"(непокрытых: {', '.join(_uncovered_details)})"
        else:
            _risk_display = f"{_risk_value:,.2f} ₽"
            _risk_extra = ""

        _pct_risk = ((_risk_value / risk_amount * 100)
                     if risk_amount > 0 else 0)
        _net_label = "кредит" if _net_cash_flow > 0 else "дебет"
        _net_value = abs(_net_cash_flow)

        if _risk_is_unlimited:
            _status_icon = "!"
        elif _risk_value > risk_amount:
            _status_icon = "!"
        else:
            _status_icon = "OK"

        st.markdown(
            f"""<div style='padding:12px 16px; border-radius:10px;
            background:{_position_bg}; border-left:6px solid {_position_border};
            margin-bottom:10px;'>
            <div style='font-size:1.02rem; font-weight:800; color:{_position_fg};'>
            {_status_icon} — Позиция <b>{_position_type}</b></div>
            <div style='font-size:.92rem; color:#333; margin-top:6px;'>
            Риск: <b>{_risk_display}</b> {_risk_extra} / {risk_amount:,.2f} ₽
            ({_pct_risk:.1f}%) &nbsp;·&nbsp;
            Net {_net_label}: <b>{_net_value:,.2f} ₽</b>
            </div></div>""",
            unsafe_allow_html=True,
        )

        if _risk_is_unlimited:
            st.error(
                f"Позиция содержит непокрытые короткие опционы. "
                f"Теоретический риск не ограничен. "
                f"Допустимый риск: {risk_amount:,.2f} ₽"
            )
        elif _risk_value > risk_amount:
            st.error(
                f"Превышен допустимый риск: "
                f"{_risk_value:,.2f} ₽ > {risk_amount:,.2f} ₽"
            )

        _cls_gamma = _cls_theta = _cls_vega = 0.0
        for _cp in _visible_positions:
            _cq = int(_cp.get("Кол-во", 0))
            _cls_gamma += (_cp.get("Гамма") or 0) * _cq
            _cls_theta += (_cp.get("Тета") or 0) * _cq
            _cls_vega += (_cp.get("Вега") or 0) * _cq

        _strategy_cls = classify_strategy(_cls_gamma, _cls_theta, _cls_vega)

        _rvi_strat_data = fetch_rvi_current()
        _rvi_strat_val = (
            _rvi_strat_data.get("value")
            if _rvi_strat_data else None
        )

        _panel_lines = [
            f"<div style='padding:14px 18px; border-radius:12px; "
            f"background:{_strategy_cls['color']}15; "
            f"border-left:6px solid {_strategy_cls['color']}; margin:10px 0;'>",
            f"<div style='font-size:1.05rem; font-weight:800; "
            f"color:{_strategy_cls['color']};'>"
            f"{_strategy_cls['label']}</div>",
            f"<div style='font-size:.85rem; color:#5a6b78; margin-top:4px;'>"
            f"Γ = {_cls_gamma:+.4f} · Θ = {_cls_theta:+.2f} · "
            f"ν = {_cls_vega:+.2f}</div>",
            f"<div style='font-size:.82rem; color:#333; margin-top:6px;'>"
            f"{_strategy_cls['description']}</div>",
        ]

        if _rvi_strat_val is not None:
            if _strategy_cls["class"] == "long_vol":
                if _rvi_strat_val < 20:
                    _panel_lines.append(
                        f"<div style='margin-top:8px; padding:8px 12px; "
                        f"background:#e8f5e9; border-radius:6px; "
                        f"font-size:.82rem;'>"
                        f"RVI = {_rvi_strat_val:.2f}% (низкий) — "
                        f"хороший момент для покупки ν</div>"
                    )
                elif _rvi_strat_val > 40:
                    _panel_lines.append(
                        f"<div style='margin-top:8px; padding:8px 12px; "
                        f"background:#fff3e0; border-radius:6px; "
                        f"font-size:.82rem;'>"
                        f"RVI = {_rvi_strat_val:.2f}% (высокий) — "
                        f"волатильность дорогая, покупка рискованна</div>"
                    )
            elif _strategy_cls["class"] == "short_vol":
                if _rvi_strat_val > 40:
                    _panel_lines.append(
                        f"<div style='margin-top:8px; padding:8px 12px; "
                        f"background:#ffebee; border-radius:6px; "
                        f"font-size:.82rem;'>"
                        f"RVI = {_rvi_strat_val:.2f}% (высокий) — "
                        f"рынок в панике, продажа ν опасна</div>"
                    )
                elif _rvi_strat_val < 20:
                    _panel_lines.append(
                        f"<div style='margin-top:8px; padding:8px 12px; "
                        f"background:#e8f5e9; border-radius:6px; "
                        f"font-size:.82rem;'>"
                        f"RVI = {_rvi_strat_val:.2f}% (низкий) — "
                        f"хороший момент для продажи ν</div>"
                    )

        _panel_lines.append("</div>")
        st.markdown("".join(_panel_lines), unsafe_allow_html=True)

    # ==================================================================
    # ГРАФИК ПРОФИЛЯ ПОЗИЦИИ
    # ==================================================================
    st.markdown("---")
    st.markdown("### График профиля позиции")

    _pos_asset_now = st.session_state.get("pos_asset", "")
    _pos_cat_now = st.session_state.get("pos_category", "")
    payoff_positions = [
        p for p in st.session_state.positions
        if p.get("visible", True)
        and (p.get("_asset") or _pos_asset_now) == _pos_asset_now
        and (p.get("_atype") or _pos_cat_now) == _pos_cat_now
    ]

    if not payoff_positions:
        st.caption("Нет видимых позиций для построения профиля.")
    else:
        _all_exp_dates = []
        for p in payoff_positions:
            _e = p.get("Эксп.", "—")
            if _e and _e != "—":
                try:
                    _all_exp_dates.append(
                        datetime.strptime(_e, "%Y-%m-%d").date()
                    )
                except Exception:
                    pass
        _anchor_date = (max(_all_exp_dates) if _all_exp_dates else None)
        _anchor_str = (_anchor_date.strftime("%Y-%m-%d")
                       if _anchor_date else None)
        _has_multiple_exp = len(set(_all_exp_dates)) > 1

        if _has_multiple_exp:
            st.info(
                f"В портфеле разные даты исполнения. "
                f"Payoff построен на {_anchor_str}."
            )

        F_current = None
        try:
            _atype_f = st.session_state.get("pos_category", "")
            _eng_f, _mkt_f = _get_engine_market(_atype_f)
            _secid_f = resolve_underlying_secid(
                st.session_state.get("pos_asset", ""), _atype_f
            )
            F_current = get_last_close_price(_secid_f, _eng_f, _mkt_f)
        except Exception:
            F_current = None

        all_pos_strikes = sorted({
            float(p["Страйк"]) for p in payoff_positions
            if p.get("Страйк") is not None
        })
        if not all_pos_strikes:
            _ba_prices = [
                float(p["Цена"]) for p in payoff_positions
                if p.get("Тип инструмента") == "БА"
                or p.get("Опцион") == "БА"
            ]
            if _ba_prices:
                all_pos_strikes = _ba_prices

        if not all_pos_strikes:
            st.caption("Нет данных для построения графика профиля.")
        else:
            _range_pts = list(all_pos_strikes)
            if F_current is not None:
                _range_pts.append(F_current)
            s_min = min(_range_pts) * 0.85
            s_max = max(_range_pts) * 1.15

            _reference_points = list(all_pos_strikes)
            if F_current is not None:
                _reference_points.append(float(F_current))

            S_arr = np.unique(np.concatenate([
                np.linspace(s_min, s_max, 1200),
                np.asarray(_reference_points, dtype=float),
            ]))

            def _payoff_at_expiry(S_vals, positions_subset):
                return compute_payoff(
                    positions_subset, S_vals,
                    comm_func=_calc_comm,
                    anchor_expiry=_anchor_str,
                )

            def _payoff_today(S_vals, positions_subset, T_years):
                S_vals = np.asarray(S_vals, dtype=float)
                pnl_arr = np.zeros_like(S_vals)
                if T_years <= 0:
                    return pnl_arr
                _r = float(st.session_state.get(
                    "_calc_riskfree", 0.0
                ) or 0.0)
                _q = float(st.session_state.get(
                    "_calc_dividend", 0.0
                ) or 0.0)
                _vol = float(st.session_state.get(
                    "_calc_volatility", 0.0
                ) or 0.0)
                if _vol <= 0:
                    _vol = 20.0
                for p in positions_subset:
                    qty = int(p["Кол-во"])
                    entry = float(p["Цена"])
                    _instr = p.get("Тип инструмента", "Опцион")
                    com = _calc_comm(entry, _instr)
                    if (p.get("Тип инструмента") == "БА"
                            or p.get("Опцион") == "БА"):
                        pnl_arr += (S_vals - entry) * qty
                        continue
                    K = float(p["Страйк"])
                    opt_type = "call" if p["Опцион"] == "Call" else "put"
                    price_now = np.array([
                        black_scholes_py(
                            S, K, T_years, _r, _vol, _q, opt_type
                        )
                        for S in S_vals
                    ])
                    pnl_arr += (price_now - entry - com) * qty
                return pnl_arr

            pnl_arr = _payoff_at_expiry(S_arr, payoff_positions)
            max_profit = float(np.max(pnl_arr))
            max_loss_pf = float(np.min(pnl_arr))

            be_points = []
            for i in range(1, len(S_arr)):
                if pnl_arr[i - 1] * pnl_arr[i] < 0:
                    denom = pnl_arr[i] - pnl_arr[i - 1]
                    if abs(denom) > 1e-12:
                        x0 = (S_arr[i - 1]
                              + (S_arr[i] - S_arr[i - 1])
                              * (-pnl_arr[i - 1]) / denom)
                        be_points.append(float(x0))

            fig_pf = go.Figure()
            fig_pf.add_trace(go.Scatter(
                x=S_arr, y=np.where(pnl_arr >= 0, pnl_arr, 0),
                fill='tozeroy', fillcolor='rgba(0,255,12,0.20)',
                line=dict(width=0), mode='lines',
                name='Прибыль (эксп.)', hoverinfo='skip',
            ))
            fig_pf.add_trace(go.Scatter(
                x=S_arr, y=np.where(pnl_arr <= 0, pnl_arr, 0),
                fill='tozeroy', fillcolor='rgba(255,0,0,0.20)',
                line=dict(width=0), mode='lines',
                name='Убыток (эксп.)', hoverinfo='skip',
            ))
            fig_pf.add_trace(go.Scatter(
                x=S_arr, y=pnl_arr, mode='lines',
                line=dict(color='#1e5a7a', width=3),
                name=f'P&L на {_anchor_str or "эксп."}',
                hovertemplate=('БА: %{x:.2f} ₽<br>'
                               'P&L: %{y:.2f} ₽<extra></extra>'),
            ))

            _cur_pnl_today = None
            try:
                _exp_date_pt = _anchor_date or date.today()
                _T_now = (max((_exp_date_pt - date.today()).days, 1)
                          / 365.0)
                pnl_today = _payoff_today(
                    S_arr, payoff_positions, _T_now
                )
                fig_pf.add_trace(go.Scatter(
                    x=S_arr, y=pnl_today, mode='lines',
                    line=dict(color='#1e88e5', width=2, dash='dash'),
                    name='P&L на текущую дату',
                    hovertemplate=('БА: %{x:.2f} ₽<br>'
                                   'P&L: %{y:.2f} ₽<extra></extra>'),
                ))

                if F_current is not None:
                    _cur_pnl_today = float(
                        _payoff_today(
                            np.array([F_current]),
                            payoff_positions, _T_now,
                        )[0]
                    )
                    _marker_color = (
                        "#00a651" if _cur_pnl_today > 0
                        else "#d32f2f" if _cur_pnl_today < 0
                        else "#1e88e5"
                    )
                    fig_pf.add_trace(go.Scatter(
                        x=[F_current], y=[_cur_pnl_today],
                        mode='markers',
                        marker=dict(
                            size=16, color=_marker_color,
                            symbol='circle',
                            line=dict(color='white', width=2),
                        ),
                        name='Текущее состояние',
                        hovertemplate=('Текущая цена: %{x:.2f} ₽<br>'
                                       'P&L: %{y:+,.2f} ₽'
                                       '<extra></extra>'),
                    ))
            except Exception:
                pass

            fig_pf.add_hline(y=0, line_dash='dot',
                             line_color='#7f9bb3', line_width=1)
            for k in all_pos_strikes:
                fig_pf.add_vline(
                    x=k, line_dash='dash', line_color='#9c00ff',
                    line_width=1, opacity=0.5,
                    annotation_text=_fmt_strike(k, _pos_cat_now),
                    annotation_position="top",
                    annotation_font_size=10,
                )
            if F_current is not None:
                fig_pf.add_vline(
                    x=F_current, line_dash='dot',
                    line_color='#1e88e5', line_width=2,
                    annotation_text=f"Тек. {F_current:.0f}",
                    annotation_position="bottom right",
                    annotation_font_size=11,
                )
            for be in be_points:
                fig_pf.add_vline(
                    x=be, line_dash='dot', line_color='#00a651',
                    line_width=1.5, opacity=0.8,
                )

            fig_pf.update_layout(
                title=f"Профиль позиции (anchor: {_anchor_str or '—'})",
                xaxis_title="Цена базового актива, ₽",
                yaxis_title="Прибыль / Убыток, ₽",
                height=500, margin=dict(l=20, r=20, t=60, b=20),
                xaxis=dict(tickformat=".0f", hoverformat=".2f"),
                yaxis=dict(tickformat=".2f", hoverformat=".2f"),
                legend=dict(orientation="h", yanchor="bottom",
                            y=1.02, xanchor="left", x=0),
                hovermode='x unified',
            )

            fig_pf.update_xaxes(
                showspikes=True, spikemode='across',
                spikesnap='cursor', spikecolor='#888888',
                spikethickness=1, spikedash='dot',
            )
            fig_pf.update_yaxes(
                showspikes=True, spikemode='across',
                spikesnap='cursor', spikecolor='#888888',
                spikethickness=1, spikedash='dot',
            )

            st.plotly_chart(fig_pf, use_container_width=True)

            pm1, pm2, pm3 = st.columns(3)
            with pm1:
                st.metric("Макс. прибыль", f"{max_profit:+,.2f} ₽")
            with pm2:
                st.metric("Макс. убыток", f"{max_loss_pf:+,.2f} ₽")
            with pm3:
                if _cur_pnl_today is not None:
                    st.metric("Текущий P&L", f"{_cur_pnl_today:+,.2f} ₽")
                else:
                    st.metric("Текущий P&L", "—")

            if be_points:
                be_str = " · ".join(f"{be:,.2f} ₽" for be in be_points)
                st.caption(f"Точки безубыточности: {be_str}")

    # ==================================================================
    # БИРЖЕВЫЕ ГРАФИКИ D1 / H1
    # ==================================================================
    st.markdown("---")
    st.markdown("### Биржевые графики")

    _asset_ch = st.session_state.get("pos_asset", "")
    _atype_ch = st.session_state.get("pos_category", "")
    _series_ch = st.session_state.get("pos_series_code", "")

    if not (_asset_ch and _series_ch):
        st.info("Выберите актив и серию в блоке выше.")
    else:
        try:
            _eng, _mkt = _get_engine_market(_atype_ch)
            _secid_ch = (resolve_underlying_secid(_asset_ch, _atype_ch)
                         or _asset_ch)

            _strikes_ch = []
            try:
                from core.market_data import fetch_bars
                _board_ch = fetch_optionboard(
                    _asset_ch, _atype_ch, _series_ch
                )
                _calls_ch = _board_ch.get('call') or []
                _puts_ch = _board_ch.get('put') or []
                _strikes_ch = sorted({
                    float(c['strike']) for c in _calls_ch
                    if c.get('strike') is not None
                } | {
                    float(p['strike']) for p in _puts_ch
                    if p.get('strike') is not None
                })
            except Exception:
                pass

            from core.market_data import fetch_bars
            _df_d1 = fetch_bars(_secid_ch, interval=24, days=180,
                                 engine=_eng, market=_mkt)
            _df_h1 = fetch_bars(_secid_ch, interval=60, days=25,
                                 engine=_eng, market=_mkt)

            _levels_ch = find_alert_levels(
                _asset_ch, category=_atype_ch
            )
            _buy_ch = (float(_levels_ch["buy"] or 0)
                       if _levels_ch["found"] else 0.0)
            _sell_ch = (float(_levels_ch["sell"] or 0)
                        if _levels_ch["found"] else 0.0)

            _current_price_ch = None
            try:
                _current_price_ch = get_last_close_price(
                    _secid_ch, _eng, _mkt
                )
            except Exception:
                pass

            render_exchange_chart(
                _df_d1, st.session_state.get("positions", []),
                _buy_ch, _sell_ch, _strikes_ch,
                f"D1 — {_asset_ch}", "chart_d1_tab",
                current_price=_current_price_ch,
                comm_func=_calc_comm_ui,
                interval=24,
            )
            render_exchange_chart(
                _df_h1, st.session_state.get("positions", []),
                _buy_ch, _sell_ch, _strikes_ch,
                f"H1 — {_asset_ch}", "chart_h1_tab",
                current_price=_current_price_ch,
                comm_func=_calc_comm_ui,
                interval=60,
            )
        except Exception as e:
            st.warning(f"Не удалось построить графики: {e}")
