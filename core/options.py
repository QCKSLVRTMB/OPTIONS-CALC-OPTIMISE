"""Опционные серии, доски, IV, кэш доски."""
from datetime import date, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import streamlit as st

from config import (
    API_BASE_URL,
    ASSET_TYPE_MAP,
    CHECKLIST_RULES,
)
from core.iss_api import iss_get, iss_get_json
from core.instruments import (
    get_asset_code_and_type,
    _asset_code_candidates,
)


# =====================================================================
# Уровни и активы из листа оповещений (сессионное состояние)
# =====================================================================
def find_alert_levels(ticker, category=None):
    df = st.session_state.get("alerts_df")
    if df is None or df.empty:
        return {"buy": None, "sell": None, "found": False}

    _cache_key = f"{ticker.upper().strip()}|{category or ''}"
    _cache = st.session_state.get("_alert_levels_cache")
    if _cache is None:
        _cache = {}
        st.session_state["_alert_levels_cache"] = _cache
    if _cache_key in _cache:
        return _cache[_cache_key]

    tk = ticker.upper().strip()
    try:
        mask = df["Тикер БА"].astype(str).str.upper().str.strip() == tk
        if category and "Категория БА" in df.columns:
            mask_cat = df["Категория БА"].astype(str).str.strip() == category
            rows = df[mask & mask_cat]
            if rows.empty:
                rows = df[mask]
        else:
            rows = df[mask]
        if rows.empty:
            result = {"buy": None, "sell": None, "found": False}
        else:
            row = rows.iloc[0]
            result = {
                "buy": float(row["Уровень покупок"]),
                "sell": float(row["Уровень продаж"]),
                "found": True,
            }
    except Exception:
        result = {"buy": None, "sell": None, "found": False}

    _cache[_cache_key] = result
    return result


def get_alert_assets_list():
    df = st.session_state.get("alerts_df")
    if df is None or df.empty:
        return []
    try:
        items = []
        seen = set()
        for _, row in df.iterrows():
            tk = str(row["Тикер БА"]).strip().upper()
            cat = str(row["Категория БА"]).strip()
            key = f"{tk}|{cat}"
            if key in seen:
                continue
            seen.add(key)
            items.append({"ticker": tk, "category": cat})
        items.sort(key=lambda x: (x["ticker"], x["category"]))
        return items
    except Exception:
        return []


# =====================================================================
# Серии опционов
# =====================================================================
@st.cache_data(ttl=300, show_spinner=False)
def fetch_optionseries(asset, asset_type_ui):
    moex_type = ASSET_TYPE_MAP.get(asset_type_ui, 'futures')
    for asset_code in _asset_code_candidates(asset, asset_type_ui):
        url = f"{API_BASE_URL}/assets/{asset_code}/optionseries"
        data = iss_get_json(url, params={'asset_type': moex_type}, timeout=15)
        if data is None:
            continue
        series = []
        if isinstance(data, list):
            for item in data:
                if 'optionseries_code' in item and 'expiration_date' in item:
                    series.append({
                        'code': item['optionseries_code'],
                        'expiry': item['expiration_date'],
                    })
        elif isinstance(data, dict) and 'data' in data:
            for item in data['data']:
                if 'optionseries_code' in item and 'expiration_date' in item:
                    series.append({
                        'code': item['optionseries_code'],
                        'expiry': item['expiration_date'],
                    })
        if series:
            return series
    return []


@st.cache_data(ttl=300, show_spinner=False)
def fetch_all_series_for_asset(asset, asset_type_ui):
    series = fetch_optionseries(asset, asset_type_ui)
    if not series:
        return []
    return sorted(series, key=lambda x: x.get("expiry", ""))


@st.cache_data(ttl=300, show_spinner=False)
def fetch_series_info(asset, asset_type_ui, series_code):
    moex_type = ASSET_TYPE_MAP.get(asset_type_ui, 'futures')
    data = None
    for asset_code in _asset_code_candidates(asset, asset_type_ui):
        url = f"{API_BASE_URL}/assets/{asset_code}/optionseries/{series_code}"
        r = iss_get(url, params={'asset_type': moex_type}, timeout=15)
        if r is None or r.status_code != 200:
            r = iss_get(url, timeout=15)
        if r is None:
            continue
        try:
            data = r.json()
        except Exception:
            continue
        if data:
            break
    if not data:
        return {}
    translated = {
        "Опционная серия": data.get('optionseries_code', '—'),
        "Базовый актив": data.get('asset_code', '—'),
        "Тип БА": data.get('asset_type', '—'),
        "Тикер": data.get('futures_code', '—'),
        "Тип серии": data.get('series_type', '—'),
        "Дата исполнения": data.get('expiration_date', '—'),
        "Центральный страйк": data.get('central_strike', '—'),
    }
    for side_key, label in (('call', 'Опционы Call'), ('put', 'Опционы Put')):
        if side_key in data:
            s = data[side_key]
            translated[label] = {
                "Объем (руб.)": s.get('volume_rub', 0),
                "Контрактов": s.get('volume_contracts', 0),
                "Открытых позиций": s.get('openposition', 0),
                "ОИ изменение": s.get('oichange', 0),
            }
    return translated


# =====================================================================
# Доска опционов
# =====================================================================
@st.cache_data(ttl=CHECKLIST_RULES["cache_board_ttl_sec"], show_spinner=False)
def _fetch_optionboard_raw(asset_code, series_code, asset_type):
    for at in [asset_type, 'share', 'futures', 'index', 'currency', 'commodity']:
        url = (f"{API_BASE_URL}/assets/{asset_code}/optionseries/"
               f"{series_code}/optionboard")
        data = iss_get_json(url, params={'asset_type': at}, timeout=15)
        if data is not None:
            return data
    return None


def fetch_central_strike(asset_code, series_code, asset_type):
    url = f"{API_BASE_URL}/assets/{asset_code}/optionseries/{series_code}"
    data = iss_get_json(url, params={'asset_type': asset_type}, timeout=15)
    if data is not None:
        cs = data.get('central_strike')
        if cs:
            try:
                return float(cs)
            except (TypeError, ValueError):
                pass
    try:
        board = _fetch_optionboard_raw(asset_code, series_code, asset_type)
        if board:
            calls = board.get('call') or []
            puts = board.get('put') or []
            c_map = {c['strike']: c for c in calls
                     if c.get('theorprice') and c.get('strike') is not None}
            p_map = {p['strike']: p for p in puts
                     if p.get('theorprice') and p.get('strike') is not None}
            common = sorted(set(c_map.keys()) & set(p_map.keys()))
            fs_est = []
            for k in common:
                ct = c_map[k]['theorprice']
                pt = p_map[k]['theorprice']
                if ct and pt and ct > 0 and pt > 0:
                    fs_est.append(ct - pt + float(k))
            if fs_est:
                fs_est.sort()
                f_current = fs_est[len(fs_est) // 2]
                all_strikes = list(c_map.keys()) | list(p_map.keys())
                if all_strikes:
                    return float(min(
                        all_strikes,
                        key=lambda s: abs(float(s) - f_current),
                    ))
    except Exception:
        pass
    return None


@st.cache_data(ttl=CHECKLIST_RULES["cache_board_ttl_sec"], show_spinner=False)
def fetch_optionboard(asset, asset_type_ui, series_code):
    board_data, used_asset_type, used_asset_code = None, None, None
    tried = []
    base_type = ASSET_TYPE_MAP.get(asset_type_ui, 'futures')
    for asset_code in _asset_code_candidates(asset, asset_type_ui):
        for at in [base_type, 'share', 'futures', 'index',
                   'currency', 'commodity']:
            key = (asset_code, at)
            if key in tried:
                continue
            tried.append(key)
            url = (f"{API_BASE_URL}/assets/{asset_code}/optionseries/"
                   f"{series_code}/optionboard")
            data = iss_get_json(url, params={'asset_type': at}, timeout=15)
            if data is not None:
                board_data = data
                used_asset_type = at
                used_asset_code = asset_code
                break
        if board_data:
            break
    if not board_data:
        raise RuntimeError("Не удалось получить доску опционов")
    cs = None
    try:
        _s_info = fetch_series_info(asset, asset_type_ui, series_code)
        _cs_raw = _s_info.get("Центральный страйк")
        if _cs_raw not in (None, "—", ""):
            cs = float(_cs_raw)
    except Exception:
        cs = None
    if cs is None and used_asset_code:
        cs = fetch_central_strike(used_asset_code, series_code,
                                   used_asset_type)
    board_data['central_strike'] = cs
    board_data['series_code'] = series_code
    return board_data


@st.cache_data(ttl=300, show_spinner=False)
def fetch_volatility_graph(asset, series_code, asset_type_ui):
    asset_code, moex_type = get_asset_code_and_type(asset, asset_type_ui)
    url = (f"{API_BASE_URL}/assets/{asset_code}"
           f"/optionseries/{series_code}/volatility_graph")
    data = iss_get_json(url, params={'asset_type': moex_type}, timeout=15)
    return data if data is not None else []


@st.cache_data(ttl=300, show_spinner=False)
def fetch_series_overview(asset, asset_type_ui):
    series_list = fetch_optionseries(asset, asset_type_ui)
    if not series_list:
        return pd.DataFrame()

    _today = date.today()

    def _fetch_one(_s):
        try:
            return _s, fetch_series_info(asset, asset_type_ui, _s["code"])
        except Exception:
            return _s, None

    _results = {}
    with ThreadPoolExecutor(max_workers=4) as _ex:
        _futs = [_ex.submit(_fetch_one, _s) for _s in series_list]
        for _f in as_completed(_futs):
            _s, _info = _f.result()
            if _info:
                _results[_s["code"]] = (_s, _info)

    rows = []
    for _s in sorted(series_list, key=lambda x: x.get("expiry", "")):
        if _s["code"] not in _results:
            continue
        _, info = _results[_s["code"]]
        call_info = info.get("Опционы Call", {}) or {}
        put_info = info.get("Опционы Put", {}) or {}
        vol_rub = (float(call_info.get("Объем (руб.)", 0) or 0)
                   + float(put_info.get("Объем (руб.)", 0) or 0))
        vol_contr = (float(call_info.get("Контрактов", 0) or 0)
                     + float(put_info.get("Контрактов", 0) or 0))
        oi = (float(call_info.get("Открытых позиций", 0) or 0)
              + float(put_info.get("Открытых позиций", 0) or 0))
        oi_change = (float(call_info.get("ОИ изменение", 0) or 0)
                     + float(put_info.get("ОИ изменение", 0) or 0))
        series_type = str(info.get("Тип серии", "—")).strip().upper()
        _map_type = {
            "WEEKLY": "W", "W": "W", "НЕДЕЛЬНАЯ": "W",
            "MONTHLY": "M", "M": "M", "МЕСЯЧНАЯ": "M",
            "QUARTERLY": "Q", "Q": "Q", "КВАРТАЛЬНАЯ": "Q",
        }
        series_type_short = _map_type.get(series_type,
                                           series_type[:1] or "—")
        expiry = info.get("Дата исполнения", _s.get("expiry", "—"))
        try:
            d = datetime.strptime(expiry, "%Y-%m-%d").date()
            days_to = (d - _today).days
            expiry_disp = f"{d.strftime('%Y-%m-%d')} ({days_to})"
        except Exception:
            days_to = None
            expiry_disp = expiry
        rows.append({
            "БА": asset,
            "Тип": series_type_short,
            "Экспирация": expiry_disp,
            "_expiry_raw": expiry,
            "_days_to": days_to,
            "_series_type": series_type_short,
            "Объём, ₽": vol_rub,
            "Об.контр.": vol_contr,
            "Откр.поз.": oi,
            "Изменение, ₽": oi_change,
            "_series_code": _s["code"],
        })
    return pd.DataFrame(rows)


# =====================================================================
# 30-секундный кэш доски для «живого» обновления греков
# =====================================================================
@st.cache_data(ttl=30, show_spinner=False)
def _fetch_board_cached_30s(asset, asset_type_ui, series_code):
    if not (asset and asset_type_ui and series_code):
        return None
    base_type = ASSET_TYPE_MAP.get(asset_type_ui, 'futures')
    type_order = [base_type, 'futures', 'share', 'index',
                  'currency', 'commodity']
    tried = []
    for asset_code in _asset_code_candidates(asset, asset_type_ui):
        for at in type_order:
            key = (asset_code, at)
            if key in tried:
                continue
            tried.append(key)
            url = (f"{API_BASE_URL}/assets/{asset_code}"
                   f"/optionseries/{series_code}/optionboard")
            data = iss_get_json(url, params={'asset_type': at}, timeout=15)
            if data is not None:
                return data
    return None