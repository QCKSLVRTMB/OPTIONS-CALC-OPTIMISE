"""Уровни оповещений, чек-боксы, alert targets, тренд, активность на уровне.

`_render_alert_card` сюда НЕ входит — это UI-компонент, он поедет в
`ui/alert_card.py` на этапе 5.
"""
import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import streamlit as st

from config import CHECKLIST_RULES
from core.iss_api import iss_get_json
from core.options import find_alert_levels


# =====================================================================
# Alert targets (поиск ближайших страйков, цены с доски)
# =====================================================================
def _nearest_strike_above(strikes, level):
    if not strikes or level is None:
        return None
    s_sorted = sorted([float(s) for s in strikes])
    for k in s_sorted:
        if k > float(level):
            return k
    return s_sorted[-1]


def _nearest_strike_below(strikes, level):
    if not strikes or level is None:
        return None
    s_sorted = sorted([float(s) for s in strikes], reverse=True)
    for k in s_sorted:
        if k < float(level):
            return k
    return s_sorted[-1]


def _get_board_price_for(board_data, opt_type, strike, side):
    if not board_data or strike is None:
        return None
    src = board_data.get('call' if opt_type == 'call' else 'put') or []
    row = next((x for x in src if x.get('strike') == strike), None)
    if row is None:
        return None
    v = row.get('offer') if side == 'Buy' else row.get('bid')
    if v is None or v <= 0:
        v = row.get('theorprice')
    if v is None or v <= 0:
        v = row.get('last')
    try:
        return float(v) if v and v > 0 else None
    except Exception:
        return None


def build_alert_targets(asset, asset_type_ui, series_code, board_data, levels,
                        current_price=None):
    if not board_data:
        return None
    calls = board_data.get('call') or []
    puts = board_data.get('put') or []
    all_strikes = sorted(
        {float(c['strike']) for c in calls if c.get('strike') is not None}
        | {float(p['strike']) for p in puts if p.get('strike') is not None}
    )
    if not all_strikes:
        return None

    lvl_buy = levels.get("buy") if levels else None
    lvl_sell = levels.get("sell") if levels else None

    if current_price is not None and current_price > 0:
        _cp = float(current_price)

        _bcands = [k for k in all_strikes if k > _cp]
        K_buy_call = _bcands[0] if _bcands else all_strikes[-1]

        _bpcands = [k for k in all_strikes if k < _cp]
        K_buy_put = _bpcands[-1] if _bpcands else all_strikes[0]

        _sell_call_target = float(lvl_sell) if lvl_sell is not None else _cp
        _scands = [k for k in all_strikes if k > _sell_call_target]
        K_sell_call = _scands[0] if _scands else all_strikes[-1]

        _sell_put_target = float(lvl_buy) if lvl_buy is not None else _cp
        _spcands = [k for k in all_strikes if k < _sell_put_target]
        K_sell_put = _spcands[-1] if _spcands else all_strikes[0]
    else:
        K_buy_call = _nearest_strike_above(all_strikes, lvl_buy)
        K_buy_put = _nearest_strike_below(all_strikes, lvl_buy)
        K_sell_call = _nearest_strike_above(all_strikes, lvl_sell)
        K_sell_put = _nearest_strike_below(all_strikes, lvl_buy)

    def _price_for(strike, opt_type, side):
        if strike is None:
            return {}
        p = _get_board_price_for(board_data, opt_type, strike, side)
        return {str(strike): p} if p is not None else {}

    return {
        "asset": asset,
        "atype": asset_type_ui,
        "series_code": series_code,
        "lvl_buy": lvl_buy,
        "lvl_sell": lvl_sell,
        "current_price": current_price,
        "strikes_buy": {"call": K_buy_call, "put": K_buy_put},
        "strikes_sell": {"call": K_sell_call, "put": K_sell_put},
        "prices_buy": {
            "call": _price_for(K_buy_call, 'call', 'Buy'),
            "put": _price_for(K_buy_put, 'put', 'Sell'),
        },
        "prices_sell": {
            "call": _price_for(K_sell_call, 'call', 'Sell'),
            "put": _price_for(K_sell_put, 'put', 'Buy'),
        },
    }


# =====================================================================
# Тренд / волна / тенденция → эмодзи и рекомендация
# =====================================================================
_ALERT_TREND_EMOJI = {
    "вверх": "🟩",
    "вниз": "🟥",
    "рейндж": "🟦",
}

_ALERT_RECOMMENDATION_MAP = {
    ("вверх", "вверх", "вверх"): ("CALL", "Присоединиться к движению вверх в восходящей тенденции. Ожидание - обновление максимума волны и тренда."),
    ("вверх", "вверх", "вниз"): ("CALL", "Ожидание появления объема на уровне покупок в нисходящей тенденции и ослабления продаж"),
    ("вверх", "вниз", "вверх"): ("CALL", "Ожидание разворота волны вверх от сильного уровня покупок и продолжения восходящего тренда. Присоединиться к движению вверх в восходящей тенденции на тесте. Держать сделку до появления объема продавца или разворота тенденции вниз"),
    ("вверх", "вниз", "вниз"): ("CALL", "Ожидание разворота волны вверх от сильного уровня покупок и продолжения восходящего тренда. Дождаться разворота тенденции и волны. Вход на 3 ноге, тесте ПК или тесте перебивки объема."),
    ("вниз", "вверх", "вверх"): ("PUT", "Ожидание разворота тенденции, а затем и волны вниз и продолжение нисходящего тренда. Дождаться разворота тенденции и волны вниз. Вход на 3 ноге, тесте ПК или тесте перебивки объема."),
    ("вниз", "вверх", "вниз"): ("PUT", "Ожидание разворота волны вниз от сильного уровня продаж и продолжения нисходящего тренда. Присоединиться к движению вниз в нисходящей тенденции на тесте. Держать сделку до появления объема покупателя или разворота тенденции вверх"),
    ("вниз", "вниз", "вверх"): ("PUT", "Ожидание появления объема на уровне продаж в восходящей тенденции и ослабления покупок"),
    ("вниз", "вниз", "вниз"): ("PUT", "Присоединиться к движению вниз в нисходящей тенденции. Ожидание - обновление минимума волны и тренда."),
    ("рейндж", "вверх", "вверх"): ("CALL", "Торговля от нижней границы рейнджа (ICE) до верхней границы рейнджа (CREEK). Присоединиться к движению вверх в восходящей тенденции."),
    ("рейндж", "вверх", "вниз"): ("CALL", "Торговля от нижней границы рейнджа (ICE) до верхней границы рейнджа (CREEK). Ожидание появления объема на уровне покупок в зигзаге тенденции вверх и ослабления продаж."),
    ("рейндж", "вниз", "вверх"): ("PUT", "Торговля от верхней границы рейнджа (CREEK) до нижней границы рейнджа (ICE). Ожидание появления объема на уровне продаж в зигзаге тенденции вниз и ослабления покупок."),
    ("рейндж", "вниз", "вниз"): ("PUT", "Торговля от верхней границы рейнджа (CREEK) до нижней границы рейнджа (ICE). Присоединиться к движению вниз в нисходящей тенденции."),
}


def _trend_emoji(value):
    if value is None:
        return ""
    v = str(value).strip().lower()
    return _ALERT_TREND_EMOJI.get(v, "")


def _trend_emoji_triplet(trend, wave, tendency):
    parts = [_trend_emoji(trend), _trend_emoji(wave), _trend_emoji(tendency)]
    parts = [p for p in parts if p]
    return " ".join(parts)


def _get_recommendation(trend, wave, tendency):
    if not (trend and wave and tendency):
        return (None, None)
    key = (str(trend).strip().lower(),
           str(wave).strip().lower(),
           str(tendency).strip().lower())
    return _ALERT_RECOMMENDATION_MAP.get(key, (None, None))


def find_alert_full_info(ticker, category=None):
    df = st.session_state.get("alerts_df")
    if df is None or df.empty:
        return None
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
            return None
        row = rows.iloc[0]
        return {
            "ticker": tk,
            "category": str(row.get("Категория БА", "")),
            "lvl_buy": float(row["Уровень покупок"]),
            "lvl_sell": float(row["Уровень продаж"]),
            "trend": str(row.get("Тренд", "")).strip(),
            "wave": str(row.get("Волна", "")).strip(),
            "tendency": str(row.get("Тенденция", "")).strip(),
            "tf_level": str(row.get("ТФ уровня", "")).strip(),
        }
    except Exception:
        return None


# =====================================================================
# Активность на уровне (часовые свечи + всплески объёма)
# =====================================================================
@st.cache_data(ttl=CHECKLIST_RULES["cache_volume_ttl_sec"], show_spinner=False)
def analyze_level_activity(secid, engine, market, buy_level, sell_level):
    out = {
        "at_buy_today": False,
        "at_sell_today": False,
        "today_max_vol": None,
        "prev_max_vol": None,
        "high_volume": False,
        "intraday_spike": False,
        "volume_bar_idx": None,
        "level_bar_idx_buy": None,
        "level_bar_idx_sell": None,
    }
    if not secid:
        return out

    today = date.today()
    end = datetime.now()
    start = end - timedelta(days=14)
    url = (f"https://iss.moex.com/iss/engines/{engine}/markets/{market}"
           f"/securities/{secid}/candles.json")

    h_params = {
        "from": start.strftime("%Y-%m-%d"),
        "till": end.strftime("%Y-%m-%d"),
        "interval": 60,
        "iss.meta": "off",
    }
    data_h = iss_get_json(url, params=h_params, timeout=20)
    if data_h:
        cols = data_h.get("candles", {}).get("columns", [])
        rows = data_h.get("candles", {}).get("data", [])
        if rows and cols:
            df_h = pd.DataFrame(rows, columns=cols)
            df_h["begin"] = pd.to_datetime(df_h["begin"], errors="coerce")
            df_h = df_h.dropna(subset=["begin", "volume"])
            df_h["day"] = df_h["begin"].dt.date
            df_h["wd"] = df_h["begin"].dt.weekday
            df_h = df_h[df_h["wd"] <= 4]
            df_h = df_h.sort_values("begin").reset_index(drop=True)

            if not df_h.empty:
                all_days = sorted(df_h["day"].unique())
                live_days = [d for d in all_days if d <= today]
                if live_days:
                    recent = live_days[-3:]
                    today_h = df_h[df_h["day"] == recent[-1]].reset_index(drop=True)
                    prev_h = df_h[df_h["day"].isin(recent[:-1])]

                    if buy_level is not None and not today_h.empty:
                        touch_b = ((today_h["low"] <= buy_level)
                                   & (today_h["high"] >= buy_level))
                        if touch_b.any():
                            out["at_buy_today"] = True
                            _idx = touch_b[touch_b].index.tolist()
                            if _idx:
                                out["level_bar_idx_buy"] = int(_idx[0])
                    if sell_level is not None and not today_h.empty:
                        touch_s = ((today_h["low"] <= sell_level)
                                   & (today_h["high"] >= sell_level))
                        if touch_s.any():
                            out["at_sell_today"] = True
                            _idx = touch_s[touch_s].index.tolist()
                            if _idx:
                                out["level_bar_idx_sell"] = int(_idx[0])

                    if not today_h.empty:
                        out["today_max_vol"] = float(today_h["volume"].max())
                        out["volume_bar_idx"] = int(today_h["volume"].idxmax())
                    if not prev_h.empty:
                        out["prev_max_vol"] = float(prev_h["volume"].max())

                    _at_any = out["at_buy_today"] or out["at_sell_today"]
                    if (_at_any
                            and out["today_max_vol"] is not None
                            and out["prev_max_vol"] is not None
                            and out["today_max_vol"] > out["prev_max_vol"]):
                        out["intraday_spike"] = True
                        level_bars = {
                            i for i in (out["level_bar_idx_buy"],
                                        out["level_bar_idx_sell"])
                            if i is not None
                        }
                        if out["volume_bar_idx"] not in level_bars:
                            out["high_volume"] = True

    d_params = {
        "from": start.strftime("%Y-%m-%d"),
        "till": end.strftime("%Y-%m-%d"),
        "interval": 24,
        "iss.meta": "off",
    }
    data_d = iss_get_json(url, params=d_params, timeout=15)
    if data_d:
        cols_d = data_d.get("candles", {}).get("columns", [])
        rows_d = data_d.get("candles", {}).get("data", [])
        if rows_d and cols_d:
            df_d = pd.DataFrame(rows_d, columns=cols_d)
            df_d["begin"] = pd.to_datetime(df_d["begin"], errors="coerce")
            df_d = df_d.dropna(subset=["begin"])
            df_d["day"] = df_d["begin"].dt.date
            today_bar = df_d[df_d["day"] == today]
            if not today_bar.empty:
                tb = today_bar.iloc[-1]
                if (buy_level is not None
                        and tb["low"] <= buy_level <= tb["high"]):
                    out["at_buy_today"] = True
                if (sell_level is not None
                        and tb["low"] <= sell_level <= tb["high"]):
                    out["at_sell_today"] = True

    return out


# =====================================================================
# Вспомогательные: близость к уровню, чек-боксы
# =====================================================================
def _level_proximity(current_price, level, other_level):
    if current_price is None or level is None or other_level is None:
        return None
    try:
        cp = float(current_price)
        lv = float(level)
        ov = float(other_level)
    except (TypeError, ValueError):
        return None
    span = abs(ov - lv)
    if span <= 0:
        return None
    ratio = max(0.0, min(1.0, 1.0 - abs(cp - lv) / span))
    return ratio * 100.0


def get_alert_checks(ticker, category=None):
    if not ticker:
        return {"buy": None, "sell": None}
    key = f"{(ticker or '').upper().strip()}|{(category or '').strip()}"
    _all_checks = st.session_state.get("_alert_checks", {})
    _stored = _all_checks.get(key) or {}
    if not _stored:
        return {"buy": None, "sell": None}

    _cur_levels = find_alert_levels(ticker, category)
    if _cur_levels.get("found"):
        _cur_buy = _cur_levels.get("buy")
        _cur_sell = _cur_levels.get("sell")
        _stored_buy = _stored.get("_lvl_buy")
        _stored_sell = _stored.get("_lvl_sell")

        if (_stored_buy is not None
                and _stored_sell is not None
                and _cur_buy is not None
                and _cur_sell is not None):
            _changed = (
                abs(float(_stored_buy) - float(_cur_buy)) > 0.01
                or abs(float(_stored_sell) - float(_cur_sell)) > 0.01
            )
            if _changed:
                _all_checks[key] = {
                    "buy": None,
                    "sell": None,
                    "_lvl_buy": _cur_buy,
                    "_lvl_sell": _cur_sell,
                }
                st.session_state["_alert_checks"] = _all_checks
                st.session_state.pop(f"_alert_buy_check_{key}", None)
                st.session_state.pop(f"_alert_sell_check_{key}", None)
                return {"buy": None, "sell": None}
        elif _stored_buy is None and _stored_sell is None:
            pass
        else:
            _all_checks[key]["_lvl_buy"] = _cur_buy
            _all_checks[key]["_lvl_sell"] = _cur_sell
            st.session_state["_alert_checks"] = _all_checks

    return {"buy": _stored.get("buy"), "sell": _stored.get("sell")}


def get_alert_recommendation_text(ticker, category=None):
    checks = get_alert_checks(ticker, category)
    b, s = checks.get("buy"), checks.get("sell")
    parts = []
    if b == "1":
        parts.append("на уровне покупок рекомендуется **+C** "
                     "(покупка Call, сильный отскок)")
    elif b == "2":
        parts.append("на уровне покупок рекомендуется **−P** "
                     "(продажа Put, слабый рост)")
    if s == "4":
        parts.append("на уровне продаж рекомендуется **+P** "
                     "(покупка Put, сильное падение)")
    elif s == "3":
        parts.append("на уровне продаж рекомендуется **−C** "
                     "(продажа Call, слабое падение)")
    return "; ".join(parts) if parts else None


# =====================================================================
# Взвешенная средняя IV по доске (для IV Rank / Skew)
# =====================================================================
def _weighted_avg_iv(board, central_strike=None):
    if not board:
        return None
    calls = board.get("call") or []
    puts = board.get("put") or []
    ivs, weights = [], []
    for side in (calls, puts):
        for row in side:
            iv = row.get("volatility")
            k = row.get("strike")
            if iv is None or k is None:
                continue
            try:
                iv_f = float(iv)
                k_f = float(k)
            except (TypeError, ValueError):
                continue
            if not (0 < iv_f < 500):
                continue
            w = (1.0 / (abs(k_f - float(central_strike)) + 1.0)
                 if central_strike is not None else 1.0)
            ivs.append(iv_f)
            weights.append(w)
    if not ivs:
        return None
    total_w = sum(weights)
    if total_w <= 0:
        return None
    return sum(iv * w for iv, w in zip(ivs, weights)) / total_w