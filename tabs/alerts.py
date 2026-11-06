"""Вкладка «Оповещения по уровням»."""
from datetime import datetime

import pandas as pd
import streamlit as st

from config import ALERTS_SHEET_URL
from core.alerts import analyze_level_activity
from core.market_data import (
    _get_engine_market,
    _normalize_alerts_df,
    fetch_last_price_from_iss,
    get_last_close_price,
    load_google_sheet_cached,
    resolve_underlying_secid,
)
from ui.alert_card import _render_alert_card


@st.fragment
def _render_alerts_tab():
    st.header("Оповещения по уровням")

    if "alerts_df" not in st.session_state:
        st.session_state.alerts_df = None
    if "alerts_source" not in st.session_state:
        st.session_state.alerts_source = "—"
    if "alerts_loaded_at" not in st.session_state:
        st.session_state.alerts_loaded_at = None
    if "alerts_error" not in st.session_state:
        st.session_state.alerts_error = None
    if "sheet_cache_buster" not in st.session_state:
        st.session_state.sheet_cache_buster = 0
    if "alerts_view_mode" not in st.session_state:
        st.session_state.alerts_view_mode = "Карточки"

    _ctrl1, _ctrl2, _ctrl3, _ctrl4 = st.columns([1, 1, 1.4, 1.6])

    with _ctrl1:
        if st.button(
            "Обновить", use_container_width=True,
            type="primary", key="alerts_manual_refresh",
        ):
            st.session_state.sheet_cache_buster += 1
            st.session_state.alerts_loaded_at = None
            st.session_state["_alert_levels_cache"] = {}
            st.rerun()

    with _ctrl2:
        with st.popover("Excel", use_container_width=True):
            st.markdown("**Загрузить Excel вручную**")
            _xls_file = st.file_uploader(
                "Excel-файл (.xlsx, .xls)",
                type=["xlsx", "xls"],
                key="alerts_excel_uploader",
                label_visibility="collapsed",
            )
            if _xls_file is not None:
                try:
                    _xls_raw = pd.read_excel(_xls_file)
                    _df_norm, _err_norm = _normalize_alerts_df(_xls_raw)
                    if _err_norm:
                        st.error(_err_norm)
                    else:
                        st.session_state.alerts_df = _df_norm
                        st.session_state.alerts_source = (
                            "Excel (ручная загрузка)"
                        )
                        st.session_state.alerts_loaded_at = (
                            datetime.now().strftime("%H:%M:%S")
                        )
                        st.session_state.alerts_error = None
                        st.session_state["_alert_levels_cache"] = {}
                        st.success(f"Загружено: {len(_df_norm)} строк")
                        st.rerun()
                except Exception as _e:
                    st.error(f"Ошибка чтения Excel: {_e}")

    with _ctrl3:
        _view_mode = st.radio(
            "Вид", ["Карточки", "Таблица"],
            index=(0 if st.session_state.alerts_view_mode == "Карточки"
                   else 1),
            horizontal=True, label_visibility="collapsed",
            key="alerts_view_mode_radio",
        )
        st.session_state.alerts_view_mode = _view_mode

    with _ctrl4:
        if st.session_state.alerts_loaded_at:
            _src_label = st.session_state.alerts_source
            st.caption(f"Источник: **{_src_label}** · "
                       f"обновлено: {st.session_state.alerts_loaded_at}")
        else:
            st.caption("Загрузка данных…")

    st.markdown("---")

    if st.session_state.alerts_source != "Excel (ручная загрузка)":
        _xls_gs, _err_gs = load_google_sheet_cached(
            ALERTS_SHEET_URL,
            st.session_state.sheet_cache_buster,
        )

        if _err_gs:
            st.session_state.alerts_error = _err_gs
        elif not _xls_gs.empty:
            _df_norm_gs, _err_norm_gs = _normalize_alerts_df(_xls_gs)
            if _err_norm_gs:
                st.session_state.alerts_error = _err_norm_gs
            else:
                st.session_state.alerts_df = _df_norm_gs
                st.session_state.alerts_source = "Google Sheets"
                st.session_state.alerts_loaded_at = (
                    datetime.now().strftime("%H:%M:%S")
                )
                st.session_state.alerts_error = None

    if st.session_state.alerts_error:
        st.warning(f"{st.session_state.alerts_error}")

    if (st.session_state.alerts_df is None
            or st.session_state.alerts_df.empty):
        st.info("Нет данных для отображения. Загрузите Excel или проверьте "
                "доступность Google Sheets.")
    else:
        @st.fragment(run_every="30s")
        def _render_alerts_live():
            _df_alerts = st.session_state.get("alerts_df")
            if _df_alerts is None or _df_alerts.empty:
                return

            _rows_out = []
            for _, _row in _df_alerts.iterrows():
                _ticker = str(_row["Тикер БА"]).strip()
                _category = str(_row["Категория БА"]).strip()
                _lvl_buy = float(_row["Уровень покупок"])
                _lvl_sell = float(_row["Уровень продаж"])

                _last = None
                try:
                    _secid_alert = resolve_underlying_secid(
                        _ticker.upper(), _category
                    )
                    if _secid_alert:
                        _eng_alert, _mkt_alert = _get_engine_market(
                            _category
                        )
                        _last = get_last_close_price(
                            _secid_alert, _eng_alert, _mkt_alert
                        )
                except Exception:
                    pass

                if _last is None:
                    try:
                        _secid_alert = resolve_underlying_secid(
                            _ticker.upper(), _category
                        )
                        if _secid_alert:
                            _info_fb = fetch_last_price_from_iss(
                                _secid_alert, _category
                            )
                            if _info_fb and _info_fb.get("last"):
                                _last = float(_info_fb["last"])
                    except Exception:
                        pass

                if _last and _last > 0:
                    _buy_dev = (_lvl_buy - _last) / _last * 100.0
                    _sell_dev = (_lvl_sell - _last) / _last * 100.0
                    _buy_active = _last <= _lvl_buy
                    _sell_active = _last >= _lvl_sell
                else:
                    _buy_dev = None
                    _sell_dev = None
                    _buy_active = False
                    _sell_active = False

                from config import CHECKLIST_RULES
                _prox = CHECKLIST_RULES["level_proximity_pct"]
                _need_volume_analysis = (
                    _buy_active or _sell_active
                    or (_buy_dev is not None
                        and abs(_buy_dev) <= _prox)
                    or (_sell_dev is not None
                        and abs(_sell_dev) <= _prox)
                )

                _vol_an = {}
                if _need_volume_analysis:
                    try:
                        _secid_an = resolve_underlying_secid(
                            _ticker.upper(), _category
                        )
                        if _secid_an:
                            _eng_an, _mkt_an = _get_engine_market(
                                _category
                            )
                            _vol_an = analyze_level_activity(
                                _secid_an, _eng_an, _mkt_an,
                                _lvl_buy, _lvl_sell,
                            ) or {}
                    except Exception:
                        _vol_an = {}

                _rows_out.append({
                    "ticker": _ticker,
                    "category": _category,
                    "lvl_buy": _lvl_buy,
                    "lvl_sell": _lvl_sell,
                    "buy_dev_pct": _buy_dev,
                    "sell_dev_pct": _sell_dev,
                    "market_price": _last,
                    "buy_active": _buy_active,
                    "sell_active": _sell_active,
                    "trend": str(_row.get("Тренд", "")).strip(),
                    "wave": str(_row.get("Волна", "")).strip(),
                    "tendency": str(_row.get("Тенденция", "")).strip(),
                    "at_buy_today": bool(_vol_an.get("at_buy_today")),
                    "at_sell_today": bool(_vol_an.get("at_sell_today")),
                    "high_volume": bool(_vol_an.get("high_volume")),
                    "intraday_spike": bool(_vol_an.get("intraday_spike")),
                })

            _n_total = len(_rows_out)
            _n_buy = sum(1 for _r in _rows_out if _r["buy_active"])
            _n_sell = sum(1 for _r in _rows_out if _r["sell_active"])
            _n_quotes = sum(1 for _r in _rows_out if _r["market_price"])

            _s1, _s2, _s3, _s4 = st.columns(4)
            with _s1:
                st.metric("Всего тикеров", _n_total)
            with _s2:
                st.metric("С котировкой", _n_quotes)
            with _s3:
                st.metric("Покупка активна", _n_buy)
            with _s4:
                st.metric("Продажа активна", _n_sell)

            st.caption("Автообновление каждые 30 секунд · "
                       "Цена = CLOSE последнего дневного бара (MOEX ISS)")

            st.markdown("---")

            if st.session_state.alerts_view_mode == "Карточки":
                _cards_per_row = 2
                for _i in range(0, len(_rows_out), _cards_per_row):
                    _cols = st.columns(_cards_per_row)
                    for _j, _r in enumerate(
                        _rows_out[_i:_i + _cards_per_row]
                    ):
                        with _cols[_j]:
                            _render_alert_card(_r)
            else:
                _tbl_rows = []
                for _r in _rows_out:
                    _tbl_rows.append({
                        "Тикер БА": _r["ticker"],
                        "Категория БА": _r["category"],
                        "Уровень покупок": _r["lvl_buy"],
                        "Откл. покупок, %": _r["buy_dev_pct"],
                        "Уровень продаж": _r["lvl_sell"],
                        "Откл. продаж, %": _r["sell_dev_pct"],
                        "Рыночная цена": _r["market_price"],
                        "Покупка активна": _r["buy_active"],
                        "Продажа активна": _r["sell_active"],
                    })
                _df_out = pd.DataFrame(_tbl_rows)

                def _style_alert_row(_row):
                    _styles = []
                    for _col in _row.index:
                        _style = ""
                        if _col in ("Покупка активна",
                                    "Продажа активна"):
                            if _row[_col] is True:
                                _style = ("background-color:#0a8f3c; "
                                          "color:#ffffff; "
                                          "font-weight:700;")
                        elif (_col == "Откл. покупок, %"
                              and _row[_col] is not None):
                            _style = ("color:#00a651; font-weight:700;"
                                      if _row[_col] <= 0
                                      else "color:#d32f2f;")
                        elif (_col == "Откл. продаж, %"
                              and _row[_col] is not None):
                            _style = ("color:#00a651; font-weight:700;"
                                      if _row[_col] >= 0
                                      else "color:#d32f2f;")
                        _styles.append(_style)
                    return _styles

                st.dataframe(
                    _df_out.style.apply(
                        _style_alert_row, axis=1
                    ).format({
                        "Уровень покупок": "{:,.2f}",
                        "Уровень продаж": "{:,.2f}",
                        "Откл. покупок, %": "{:+.2f} %",
                        "Откл. продаж, %": "{:+.2f} %",
                        "Рыночная цена": "{:,.2f}",
                        "Покупка активна":
                            lambda v: "АКТИВНО" if v else "·",
                        "Продажа активна":
                            lambda v: "АКТИВНО" if v else "·",
                    }, na_rep="—"),
                    use_container_width=True, hide_index=True,
                    height=600,
                )

            _df_export = pd.DataFrame(_rows_out)
            if not _df_export.empty:
                _df_export = _df_export.rename(columns={
                    "ticker": "Тикер БА",
                    "category": "Категория БА",
                    "lvl_buy": "Уровень покупок",
                    "buy_dev_pct": "Откл. покупок, %",
                    "lvl_sell": "Уровень продаж",
                    "sell_dev_pct": "Откл. продаж, %",
                    "market_price": "Рыночная цена",
                    "buy_active": "Покупка активна",
                    "sell_active": "Продажа активна",
                })
                st.download_button(
                    "Экспорт оповещений (CSV)",
                    data=_df_export.to_csv(index=False).encode(
                        "utf-8-sig"
                    ),
                    file_name="alerts.csv", mime="text/csv",
                    use_container_width=True,
                    key="alerts_export_csv",
                )

        _render_alerts_live()