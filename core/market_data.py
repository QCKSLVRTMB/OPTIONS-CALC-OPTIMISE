"""Рыночные данные: комиссии, дивиденды, G-кривая, RVI, бары, цены, фьючерсы."""
import math
import re
from datetime import datetime, date, timedelta
from io import BytesIO

import numpy as np
import pandas as pd
import requests
import streamlit as st

from config import (
    API_BASE_URL,
    ASSET_TYPE_MAP,
    CHECKLIST_RULES,
    DEFAULT_COMM_OPTIONS_PCT,
    DEFAULT_COMM_OPTIONS_MIN,
    DEFAULT_COMM_FUTURES_PCT,
    DEFAULT_COMM_STOCKS_PCT,
)
from core.iss_api import (
    iss_get_json,
    _is_failed_recently,
    _mark_failed,
)


# =====================================================================
# Комиссии
# =====================================================================
def calc_commission(premium, instrument_type="Опцион",
                    min_comm_options=0.02,
                    comm_options_pct=3.0,
                    comm_futures_pct=0.1,
                    comm_stocks_pct=0.3):
    if premium is None or premium <= 0:
        return 0.0

    t = (instrument_type or "").strip().lower()
    _MIN_OTHER = 0.01

    if t in ("опцион", "option"):
        _c = max((comm_options_pct / 100.0) * premium, min_comm_options)
    elif t in ("фьючерс", "futures"):
        _c = max((comm_futures_pct / 100.0) * premium, _MIN_OTHER)
    elif t in ("акция", "stock", "share", "облигация", "etf", "bond"):
        _c = max((comm_stocks_pct / 100.0) * premium, _MIN_OTHER)
    elif t in ("металл", "metal", "драгметалл", "золото", "серебро"):
        _c = max(0.019 * premium, _MIN_OTHER)
    elif t in ("валюта", "currency", "валютный"):
        _c = max(0.009 * premium, _MIN_OTHER)
    else:
        _c = 0.0

    return round(_c, 2)


def _calc_comm_ui(premium, instr_type="Опцион"):
    cop = st.session_state.get("cop_inp", DEFAULT_COMM_OPTIONS_PCT)
    cmo = st.session_state.get("cmo_inp", DEFAULT_COMM_OPTIONS_MIN)
    cfp = st.session_state.get("cfp_inp", DEFAULT_COMM_FUTURES_PCT)
    csp = st.session_state.get("csp_inp", DEFAULT_COMM_STOCKS_PCT)
    return calc_commission(
        premium, instrument_type=instr_type,
        min_comm_options=cmo,
        comm_options_pct=cop,
        comm_futures_pct=cfp,
        comm_stocks_pct=csp,
    )


# =====================================================================
# Дивиденды
# =====================================================================
@st.cache_data(ttl=21600, show_spinner=False)
def fetch_dividends_smartlab() -> pd.DataFrame:
    url = "https://smart-lab.ru/dividends/index/order_by_ticker/desc/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
    except Exception:
        return pd.DataFrame(
            columns=["ticker", "dividend_rub", "record_date", "stock_price"]
        )
    rows = re.findall(
        r'<tr[^>]*>(.*?)</tr>', r.text, flags=re.DOTALL | re.IGNORECASE
    )
    records = []
    for row_html in rows:
        cells = re.findall(
            r'<td[^>]*>(.*?)</td>', row_html, flags=re.DOTALL | re.IGNORECASE
        )
        if len(cells) < 10:
            continue
        clean = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        ticker = clean[1].upper() if len(clean) > 1 else ""
        if not ticker or not re.match(r'^[A-Z0-9]+$', ticker):
            continue
        try:
            dividend = float(clean[3].replace(",", ".").replace(" ", ""))
        except Exception:
            continue
        date_str = None
        for idx in (7, 6, 8):
            if idx < len(clean) and re.match(
                    r'\d{2}\.\d{2}\.\d{4}', clean[idx]):
                date_str = clean[idx]
                break
        if not date_str:
            continue
        try:
            record_date = datetime.strptime(date_str, "%d.%m.%Y").date()
        except Exception:
            continue
        stock_price = None
        try:
            price_str = (clean[9].replace(",", ".")
                         .replace(" ", "").replace("₽", ""))
            stock_price = float(price_str)
        except Exception:
            pass
        records.append({
            "ticker": ticker,
            "dividend_rub": dividend,
            "record_date": record_date,
            "stock_price": stock_price,
        })
    return pd.DataFrame(records)


def get_dividend_yield_for_ticker(ticker, expiry_str):
    df = fetch_dividends_smartlab()
    if df.empty:
        return None, None, None
    try:
        exp_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    except Exception:
        return None, None, None
    candidates = df[
        (df["ticker"] == ticker.upper())
        & (df["record_date"] <= exp_date)
        & (df["record_date"] >= date.today())
    ]
    if candidates.empty:
        return None, None, None
    row = candidates.sort_values("record_date").iloc[0]
    div = float(row["dividend_rub"])
    price = row["stock_price"]
    if price is None or price <= 0:
        return None, None, None
    return div / price, float(price), row["record_date"]


# =====================================================================
# G-кривая ОФЗ
# =====================================================================
_GC_A = [0.0, 0.4, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0]
_GC_B = [0.4, 0.6, 1.0, 1.6, 2.4, 4.0, 6.4, 9.6, 16.0]


@st.cache_data(ttl=CHECKLIST_RULES["cache_g_curve_ttl_sec"], show_spinner=False)
def fetch_g_curve_params():
    if _is_failed_recently("g_curve", cooldown_sec=30):
        return None
    url = "https://iss.moex.com/iss/engines/stock/zcyc/securities.json"
    data = iss_get_json(url, timeout=15)
    if data is None:
        _mark_failed("g_curve", cooldown_sec=30)
        return None
    params = data.get('params', {})
    columns = params.get('columns', [])
    values = params.get('data', [])
    if not columns or not values:
        _mark_failed("g_curve", cooldown_sec=30)
        return None
    df = pd.DataFrame(values, columns=columns)
    row = df.iloc[0]
    try:
        return {
            'beta0': float(row['B1']),
            'beta1': float(row['B2']),
            'beta2': float(row['B3']),
            'tau': float(row['T1']),
            'g': [float(row[f'G{i}']) for i in range(1, 10)],
        }
    except Exception:
        return None


def g_curve_yield(t_years, p):
    if p is None or t_years <= 0:
        return None
    b0, b1, b2, tau = p['beta0'], p['beta1'], p['beta2'], p['tau']
    g = p['g']
    if tau <= 0:
        tau = 1.0
    exp_term = math.exp(-t_years / tau)
    frac = (1 - exp_term) * tau / t_years
    term1 = b0
    term2 = b1 * frac
    term3 = b2 * (frac - exp_term)
    term4 = 0.0
    for i in range(9):
        if _GC_B[i] != 0:
            term4 += g[i] * math.exp(
                -((t_years - _GC_A[i]) ** 2) / (_GC_B[i] ** 2)
            )
    raw = term1 + term2 + term3 + term4
    rate_pct = raw / 10000.0
    if rate_pct < 0.5 or rate_pct > 50:
        rate_pct = raw / 100.0 if raw > 100 else raw
    return rate_pct


def get_risk_free_rate_for_expiry(expiry_str, current_str=None):
    params = fetch_g_curve_params()
    if params is None:
        return None
    try:
        exp_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        cur_date = (datetime.strptime(current_str, "%Y-%m-%d").date()
                    if current_str else date.today())
        days = (exp_date - cur_date).days
        if days <= 0:
            return None
        return round(g_curve_yield(days / 365.0, params), 4)
    except Exception:
        return None


# =====================================================================
# RVI
# =====================================================================
@st.cache_data(ttl=CHECKLIST_RULES["cache_rvi_ttl_sec"], show_spinner=False)
def fetch_rvi_current():
    url = ("https://iss.moex.com/iss/engines/stock/markets/index/"
           "securities/RVI.json")
    data = iss_get_json(url, params={"iss.meta": "off"}, timeout=15)
    if data is None:
        return {"value": None, "error": "Ошибка запроса ISS"}

    result = {
        "value": None, "open": None, "change": None, "change_pct": None,
        "high": None, "low": None, "annual_high": None, "annual_low": None,
        "update_time": None, "trade_date": None, "error": None,
    }

    sec = data.get("securities", {})
    if sec.get("data"):
        rd = dict(zip(sec["columns"], sec["data"][0]))
        result["annual_high"] = rd.get("ANNUALHIGH")
        result["annual_low"] = rd.get("ANNUALLOW")

    md = data.get("marketdata", {})
    if md.get("data"):
        rd = dict(zip(md["columns"], md["data"][0]))
        for key, rkey in [
            ("LASTVALUE", "value"), ("OPENVALUE", "open"),
            ("LASTCHANGE", "change"), ("LASTCHANGEPRC", "change_pct"),
            ("HIGH", "high"), ("LOW", "low"),
            ("UPDATETIME", "update_time"), ("TRADEDATE", "trade_date"),
        ]:
            v = rd.get(key)
            if v is None or v == "":
                continue
            if rkey in ("update_time", "trade_date"):
                result[rkey] = v
            else:
                try:
                    result[rkey] = float(v)
                except (TypeError, ValueError):
                    pass

    if result["value"] is None:
        result["error"] = "RVI не найден в ответе ISS"
    return result


@st.cache_data(ttl=300, show_spinner=False)
def fetch_rvi_history(days=365):
    end = datetime.now()
    start = end - timedelta(days=days)
    url = ("https://iss.moex.com/iss/engines/stock/markets/index/"
           "securities/RVI/candles.json")
    params = {
        "from": start.strftime("%Y-%m-%d"),
        "till": end.strftime("%Y-%m-%d"),
        "interval": 24,
        "iss.meta": "off",
    }
    data = iss_get_json(url, params=params, timeout=20)
    if data is None:
        return pd.DataFrame()
    cols = data.get("candles", {}).get("columns", [])
    rows = data.get("candles", {}).get("data", [])
    if not rows or not cols:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=cols)
    df["begin"] = pd.to_datetime(df["begin"], errors="coerce")
    df = df.dropna(subset=["begin", "close"])
    df = df.sort_values("begin").reset_index(drop=True)
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_rvi_stats(days=365):
    df = fetch_rvi_history(days=days)
    if df.empty:
        return None
    values = df["close"].values
    current = values[-1] if len(values) > 0 else None
    if current is None:
        return None
    return {
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "current": float(current),
        "percentile": float(np.mean(values <= current) * 100),
        "count": len(values),
    }


def classify_iv_vs_rvi(iv, rvi):
    if iv is None or rvi is None or rvi <= 0:
        return None
    spread = iv - rvi
    ratio = iv / rvi
    if ratio > 2.0:
        zone, label, color = "very_high", "Очень высокая", "#d32f2f"
        action = "Продавать волатильность (проверить новости!)"
    elif ratio > 1.5:
        zone, label, color = "high", "Повышенная", "#e6a500"
        action = "Осторожно, проверить контекст"
    elif ratio > 0.7:
        zone, label, color = "normal", "Норма", "#7a7a7a"
        action = "Направленные стратегии"
    elif ratio > 0.5:
        zone, label, color = "low", "Пониженная", "#00a651"
        action = "Кандидат на покупку волатильности"
    else:
        zone, label, color = "very_low", "Очень низкая", "#1e88e5"
        action = "Покупать волатильность (дёшево)"
    return {
        "spread": round(spread, 2),
        "ratio": round(ratio, 3),
        "zone": zone,
        "zone_label": label,
        "zone_color": color,
        "action": action,
    }


def classify_strategy(total_gamma, total_theta, total_vega):
    if total_gamma > 0 and total_theta < 0 and total_vega > 0:
        return {
            "class": "long_vol",
            "label": "ПОКУПКА ВОЛАТИЛЬНОСТИ",
            "color": "#00a651",
            "description": "Платите временной распад, ждёте движения или роста IV",
        }
    elif total_gamma < 0 and total_theta > 0 and total_vega < 0:
        return {
            "class": "short_vol",
            "label": "ПРОДАЖА ВОЛАТИЛЬНОСТИ",
            "color": "#d32f2f",
            "description": "Получаете премию за счёт распада, несёте риск движения и роста IV",
        }
    else:
        return {
            "class": "mixed",
            "label": "СМЕШАННАЯ",
            "color": "#7a7a7a",
            "description": "Комбинированная стратегия (календарь, бабочка и т.п.)",
        }


# =====================================================================
# Метки дат
# =====================================================================
def expiry_marker(expiry_str):
    try:
        d = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    except Exception:
        return ""
    today = date.today()
    monday_this_week = today - timedelta(days=today.weekday())
    end_next_week = monday_this_week + timedelta(days=13)
    end_week_after = monday_this_week + timedelta(days=20)
    if d <= end_next_week:
        return "🔴"
    if d <= end_week_after:
        return "🔵"
    return "🟢"


def format_series_label(expiry_str):
    try:
        d = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        marker = expiry_marker(expiry_str)
        prefix = f"{marker} " if marker else ""
        return f"{prefix}{d.strftime('%d.%m.%Y')}"
    except Exception:
        return f"{expiry_str}"


# =====================================================================
# Последняя цена БА
# =====================================================================
@st.cache_data(ttl=CHECKLIST_RULES["cache_last_price_ttl_sec"], show_spinner=False)
def fetch_last_price_from_iss(secid, asset_type_ui):
    if not secid:
        return {"last": None, "secid": secid, "source": "—"}
    if asset_type_ui in ("Фьючерс", "Валюта", "Товар"):
        engine, market = "futures", "forts"
    elif asset_type_ui == "Индекс":
        engine, market = "stock", "index"
    else:
        engine, market = "stock", "shares"
    url = (f"https://iss.moex.com/iss/engines/{engine}/markets/{market}"
           f"/securities/{secid}.json")
    data = iss_get_json(url,
                        params={"iss.meta": "off", "iss.only": "marketdata"},
                        timeout=15)
    if data is None:
        return {"last": None, "secid": secid, "source": "ошибка запроса"}
    md = data.get("marketdata", {})
    cols = md.get("columns", [])
    rows = md.get("data", [])
    if not rows or not cols:
        return {"last": None, "secid": secid, "source": "нет данных"}
    rd = dict(zip(cols, rows[0]))
    for key in ("LAST", "MARKETPRICE", "LCLOSEPRICE",
                "LASTTOPREVPRICE", "OPEN", "SETTLEPRICE"):
        val = rd.get(key)
        if val and val > 0:
            return {"last": float(val), "secid": secid, "source": key}
    return {"last": None, "secid": secid, "source": "нет цены"}


# =====================================================================
# Google Sheets
# =====================================================================
@st.cache_data(ttl=30, show_spinner=False)
def load_google_sheet_cached(sheet_url, cache_buster):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(sheet_url, headers=headers, timeout=20)
        resp.raise_for_status()
        df = pd.read_excel(BytesIO(resp.content), engine="openpyxl")
        return df, None
    except requests.exceptions.Timeout:
        return pd.DataFrame(), "Таймаут при обращении к Google Sheets"
    except Exception as e:
        return pd.DataFrame(), f"Ошибка загрузки Google Sheets: {e}"


def _normalize_alerts_df(_xls):
    _col_map = {}
    
    # Находим индексы, чтобы отделить колонки покупок от колонок продаж
    buy_idx = -1
    sell_idx = -1
    for i, c in enumerate(_xls.columns):
        c_str = str(c).strip().lower()
        if "уровень покуп" in c_str or "bid" in c_str:
            buy_idx = i
        elif "уровень продаж" in c_str or "ask" in c_str:
            sell_idx = i

    for i, c in enumerate(_xls.columns):
        c_str = str(c).strip().lower()
        if "тикер" in c_str:
            _col_map[c] = "Тикер БА"
        elif "категор" in c_str:
            _col_map[c] = "Категория БА"
        elif c_str == "тренд":
            _col_map[c] = "Тренд"
        elif c_str == "волна":
            _col_map[c] = "Волна"
        elif c_str == "тенденция":
            _col_map[c] = "Тенденция"
        elif c_str in ("тф уровня", "tf уровня", "tf", "timeframe"):
            _col_map[c] = "ТФ уровня"
        elif "уровень покуп" in c_str or "bid" in c_str:
            _col_map[c] = "Уровень покупок"
        elif "уровень продаж" in c_str or "ask" in c_str:
            _col_map[c] = "Уровень продаж"
        elif "ожидание" in c_str:
            if i < sell_idx:
                _col_map[c] = "Ожидание покупки"
            else:
                _col_map[c] = "Ожидание продажи"
        elif "комментар" in c_str:
            if i < sell_idx:
                _col_map[c] = "Комментарий покупки"
            else:
                _col_map[c] = "Комментарий продажи"

    _xls = _xls.rename(columns=_col_map)
    
    required = ["Тикер БА", "Категория БА", "Уровень покупок", "Уровень продаж"]
    missing = [c for c in required if c not in _xls.columns]
    if missing:
        return pd.DataFrame(), f"В таблице нет колонок: {', '.join(missing)}"
        
    _xls["Уровень покупок"] = pd.to_numeric(_xls["Уровень покупок"], errors="coerce")
    _xls["Уровень продаж"] = pd.to_numeric(_xls["Уровень продаж"], errors="coerce")
    _xls = _xls.dropna(subset=["Тикер БА", "Уровень покупок", "Уровень продаж"])
    
    # Заполняем пустые новые колонки
    for _opt_col in ("Тренд", "Волна", "Тенденция", "ТФ уровня", 
                     "Ожидание покупки", "Комментарий покупки",
                     "Ожидание продажи", "Комментарий продажи"):
        if _opt_col not in _xls.columns:
            _xls[_opt_col] = ""
        else:
            _xls[_opt_col] = (_xls[_opt_col].fillna("")
                              .astype(str).str.strip())
                              
    # Очищаем от лишних .0 (например, "2.0" -> "2")
    for _exp_col in ("Ожидание покупки", "Ожидание продажи"):
        _xls[_exp_col] = _xls[_exp_col].apply(
            lambda x: str(int(float(x))) if x and str(x).replace('.','',1).isdigit() else str(x)
        )
        
    return _xls, None
