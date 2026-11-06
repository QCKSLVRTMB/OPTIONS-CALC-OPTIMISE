"""Стратегии: рекомендации, legs, чек-лист перед входом, плановая дата закрытия."""
import math
from datetime import date, datetime, timedelta

import numpy as np

from config import CHECKLIST_RULES
from core.helpers import _new_position_id
from core.market_data import (
    get_risk_free_rate_for_expiry,
    get_dividend_yield_for_ticker,
)


# =====================================================================
# r/q для конкретной ноги
# =====================================================================
def _compute_r_q_for_leg(asset, atype, expiry_str):
    r_val = 0.0
    q_val = 0.0
    if expiry_str and expiry_str not in ("—", ""):
        try:
            _r = get_risk_free_rate_for_expiry(expiry_str)
            if _r is not None:
                r_val = float(_r)
        except Exception:
            r_val = 0.0
    if atype == "Акция" and asset:
        try:
            _q_frac, _, _ = get_dividend_yield_for_ticker(asset, expiry_str)
            if _q_frac is not None:
                q_val = float(_q_frac) * 100.0
        except Exception:
            q_val = 0.0
    return r_val, q_val


def _migrate_positions_r_q(positions):
    for p in positions:
        if p.get("Тип инструмента") != "Опцион":
            continue
        _exp = p.get("Эксп.", "")
        if not _exp or _exp == "—":
            continue
        _r_new, _q_new = _compute_r_q_for_leg(
            p.get("_asset", ""), p.get("_atype", ""), _exp
        )
        p["_r"] = float(_r_new)
        p["_q"] = float(_q_new)
    return positions


# =====================================================================
# Ширина диапазона в страйках
# =====================================================================
def _calc_range_strikes(lvl_buy, lvl_sell, strikes):
    if (lvl_buy is None or lvl_sell is None
            or not strikes or len(strikes) < 2):
        return None
    try:
        steps = np.diff(sorted(float(s) for s in strikes))
        step_median = float(np.median(steps))
        if step_median <= 0:
            return None
        return abs(float(lvl_sell) - float(lvl_buy)) / step_median
    except Exception:
        return None


# =====================================================================
# Рекомендация конструкции по чек-боксам
# =====================================================================
def _suggest_strategy(buy_check, sell_check, trend, wave, tendency,
                      range_strikes, iv_zone=None, skew_type=None):
    result = {
        "side": None,
        "strategy": None,
        "direction_ui": "—",
        "rationale": "",
        "confidence": "low",
    }

    if buy_check == "1":
        result["side"] = "CALL"
        if iv_zone == "high":
            result["strategy"] = "Bull Call Spread"
            result["direction_ui"] = "бычий Call-спред (защита при высокой IV)"
        elif skew_type == "forward":
            result["strategy"] = "Ratio Call Spread"
            result["direction_ui"] = "пропорциональный Call-спред (прямой skew)"
        else:
            result["strategy"] = "Bull Call Spread"
            result["direction_ui"] = "бычий Call-спред"
        result["rationale"] = "чек-бокс 1: сильный отскок от уровня покупок"
        result["confidence"] = "high"
        return result

    if buy_check == "2":
        result["side"] = "CALL"
        result["strategy"] = "Bull Put Spread"
        result["direction_ui"] = "бычий Put-спред"
        result["rationale"] = ("чек-бокс 2: слабый медленный рост "
                               "от уровня покупок")
        result["confidence"] = "high"
        return result

    if sell_check == "4":
        result["side"] = "PUT"
        if iv_zone == "high":
            result["strategy"] = "Bear Put Spread"
            result["direction_ui"] = "медвежий Put-спред (защита при высокой IV)"
        elif skew_type == "reverse":
            result["strategy"] = "Ratio Put Spread"
            result["direction_ui"] = "пропорциональный Put-спред (обратный skew)"
        else:
            result["strategy"] = "Bear Put Spread"
            result["direction_ui"] = "медвежий Put-спред"
        result["rationale"] = "чек-бокс 4: сильное падение от уровня продаж"
        result["confidence"] = "high"
        return result

    if sell_check == "3":
        result["side"] = "PUT"
        result["strategy"] = "Bear Call Spread"
        result["direction_ui"] = "медвежий Call-спред"
        result["rationale"] = ("чек-бокс 3: слабое медленное падение "
                               "от уровня продаж")
        result["confidence"] = "high"
        return result

    t = (trend or "").strip().lower()
    w = (wave or "").strip().lower()
    te = (tendency or "").strip().lower()

    if not (t and w and te):
        return result

    if t == w == te == "вверх":
        result["side"] = "CALL"
        result["strategy"] = "Bull Call Spread"
        result["direction_ui"] = "бычий Call-спред"
        result["rationale"] = "восходящий тренд (чек-бокс не выбран)"
        result["confidence"] = "medium"
    elif t == w == te == "вниз":
        result["side"] = "PUT"
        result["strategy"] = "Bear Put Spread"
        result["direction_ui"] = "медвежий Put-спред"
        result["rationale"] = "нисходящий тренд (чек-бокс не выбран)"
        result["confidence"] = "medium"
    elif "рейндж" in (t, w, te):
        result["side"] = "RANGE"
        if range_strikes is not None and range_strikes > 1:
            result["strategy"] = "Long Straddle"
            result["direction_ui"] = "Long Straddle"
            result["rationale"] = (
                f"рейндж {range_strikes:.1f} стр — покупка волатильности"
            )
        else:
            result["strategy"] = "Short Strangle"
            result["direction_ui"] = "Short Strangle"
            _rs = f" ({range_strikes:.1f} стр.)" if range_strikes else ""
            result["rationale"] = f"рейндж узкий{_rs} — продажа волатильности"
        result["confidence"] = "medium"

    return result


# =====================================================================
# Список ног для рекомендованной стратегии
# =====================================================================
def _suggestion_legs(strategy_name, alert_targets):
    if not alert_targets or not strategy_name:
        return []

    sb = alert_targets.get("strikes_buy") or {}
    ss = alert_targets.get("strikes_sell") or {}
    K_bc = sb.get("call")
    K_bp = sb.get("put")
    K_sc = ss.get("call")
    K_sp = ss.get("put")

    q2 = 2

    if strategy_name in ("Bull Call Spread", "Bear Call Spread"):
        if K_bc is not None and K_sc is not None and K_bc >= K_sc:
            K_bc, K_sc = K_sc, K_bc
    if strategy_name in ("Bull Put Spread", "Bear Put Spread"):
        if K_bp is not None and K_sp is not None and K_bp >= K_sp:
            K_bp, K_sp = K_sp, K_bp

    if strategy_name == "Bull Call Spread":
        return [
            {"option": "Call", "side": "Buy", "strike": K_bc, "qty": q2,
             "label": "Buy Call (низ)"},
            {"option": "Call", "side": "Sell", "strike": K_sc, "qty": q2,
             "label": "Sell Call (верх)"},
        ]
    if strategy_name == "Bear Put Spread":
        return [
            {"option": "Put", "side": "Buy", "strike": K_bp, "qty": q2,
             "label": "Buy Put (верх)"},
            {"option": "Put", "side": "Sell", "strike": K_sp, "qty": q2,
             "label": "Sell Put (низ)"},
        ]
    if strategy_name == "Ratio Call Spread":
        return [
            {"option": "Call", "side": "Buy", "strike": K_bc, "qty": q2,
             "label": "Buy Call"},
            {"option": "Call", "side": "Sell", "strike": K_sc, "qty": q2 * 2,
             "label": "Sell Call ×2"},
        ]
    if strategy_name == "Ratio Put Spread":
        return [
            {"option": "Put", "side": "Buy", "strike": K_bp, "qty": q2,
             "label": "Buy Put"},
            {"option": "Put", "side": "Sell", "strike": K_sp, "qty": q2 * 2,
             "label": "Sell Put ×2"},
        ]
    if strategy_name == "Short Call":
        return [
            {"option": "Call", "side": "Sell", "strike": K_sc, "qty": q2,
             "label": "Sell Call"},
        ]
    if strategy_name == "Short Put":
        return [
            {"option": "Put", "side": "Sell", "strike": K_sp, "qty": q2,
             "label": "Sell Put"},
        ]
    if strategy_name == "Bull Put Spread":
        return [
            {"option": "Put", "side": "Buy", "strike": K_bp, "qty": q2,
             "label": "Buy Put (низ)"},
            {"option": "Put", "side": "Sell", "strike": K_sp, "qty": q2,
             "label": "Sell Put (верх)"},
        ]
    if strategy_name == "Bear Call Spread":
        return [
            {"option": "Call", "side": "Sell", "strike": K_sc, "qty": q2,
             "label": "Sell Call (низ)"},
            {"option": "Call", "side": "Buy", "strike": K_bc, "qty": q2,
             "label": "Buy Call (верх)"},
        ]
    if strategy_name in ("Long Straddle", "Short Straddle"):
        _side = "Buy" if strategy_name == "Long Straddle" else "Sell"
        _k = K_bc or K_sc or K_bp or K_sp
        return [
            {"option": "Call", "side": _side, "strike": _k, "qty": q2,
             "label": f"{_side} Call"},
            {"option": "Put", "side": _side, "strike": _k, "qty": q2,
             "label": f"{_side} Put"},
        ]
    if strategy_name == "Short Strangle":
        return [
            {"option": "Call", "side": "Sell", "strike": K_sc, "qty": q2,
             "label": "Sell Call"},
            {"option": "Put", "side": "Sell", "strike": K_sp, "qty": q2,
             "label": "Sell Put"},
        ]
    if strategy_name == "Iron Condor":
        return [
            {"option": "Put", "side": "Buy", "strike": K_bp, "qty": q2,
             "label": "Buy Put (крыло)"},
            {"option": "Put", "side": "Sell", "strike": K_sp, "qty": q2,
             "label": "Sell Put"},
            {"option": "Call", "side": "Sell", "strike": K_sc, "qty": q2,
             "label": "Sell Call"},
            {"option": "Call", "side": "Buy", "strike": K_bc, "qty": q2,
             "label": "Buy Call (крыло)"},
        ]
    return []


# =====================================================================
# Применение рекомендованной стратегии
# =====================================================================
def _apply_suggested_strategy(strategy_name, asset, atype, series_code,
                               expiry_str, alert_targets, board):
    legs = _suggestion_legs(strategy_name, alert_targets)
    if not legs or not board:
        return []

    _calls = board.get("call") or []
    _puts = board.get("put") or []
    _c_map = {c.get("strike"): c for c in _calls
              if c.get("strike") is not None}
    _p_map = {p.get("strike"): p for p in _puts
              if p.get("strike") is not None}

    _r_leg, _q_leg = _compute_r_q_for_leg(asset, atype, expiry_str)

    created = []
    for leg in legs:
        _opt = leg["option"]
        _side = leg["side"]
        _k = leg["strike"]
        _qty = int(leg["qty"])
        if _k is None:
            continue
        _src = _c_map if _opt == "Call" else _p_map
        _row = _src.get(_k, {})
        _price = 0.0
        _v = _row.get("offer") if _side == "Buy" else _row.get("bid")
        if _v is None or _v <= 0:
            _v = _row.get("theorprice")
        if _v is None or _v <= 0:
            _v = _row.get("last")
        try:
            _price = float(_v) if _v and _v > 0 else 0.0
        except Exception:
            _price = 0.0
        if _price <= 0:
            continue

        _signed = _qty if _side == "Buy" else -_qty
        _pos = {
            "_id": _new_position_id(),
            "Конструкция": strategy_name,
            "Тип инструмента": "Опцион",
            "Опцион": _opt,
            "Направление": _side,
            "Страйк": _k,
            "Эксп.": expiry_str,
            "Тикер": _row.get("secid", "—"),
            "Кол-во": _signed,
            "Цена": float(_price),
            "Теор.цена": None,
            "Дельта": _row.get("delta"),
            "Гамма": _row.get("gamma"),
            "Вега": _row.get("vega"),
            "Тета": _row.get("theta"),
            "Ро": _row.get("rho"),
            "visible": True,
            "_vol": float(_row.get("volatility") or 20.0),
            "_r": float(_r_leg),
            "_q": float(_q_leg),
            "_series_code": series_code,
            "_asset": asset,
            "_atype": atype,
        }
        created.append(_pos)

    return created


# =====================================================================
# Чек-лист перед входом
# =====================================================================
def _pretrade_checklist_state(trend, wave, tendency, tf_level,
                               side, opt_type, strike, current_price,
                               expiry_str, qty):
    items = []

    t = (trend or "").strip().lower()
    w = (wave or "").strip().lower()
    te = (tendency or "").strip().lower()
    if t and w and te and t == w == te and t in ("вверх", "вниз"):
        items.append(("Фаза волны", "fail",
                      "Продолжение волны — вход не рекомендуется"))
    elif t and w and te:
        items.append(("Фаза волны", "ok", "Не продолжение волны"))
    else:
        items.append(("Фаза волны", "skip",
                      "Тренд / волна / тенденция не заданы"))

    tf = (tf_level or "").strip().upper()
    if tf in ("D", "D1", "1D", "DAY"):
        items.append(("Уровень дневной", "ok", "ТФ D1"))
    elif not tf:
        items.append(("Уровень дневной", "skip",
                      "ТФ не задан в таблице — проверьте вручную"))
    else:
        items.append(("Уровень дневной", "fail", f"ТФ {tf} — не дневной"))

    if side == "Buy" and opt_type in ("Call", "Put") and strike is not None:
        if current_price is None:
            items.append(("OTM при покупке", "skip", "Нет текущей цены БА"))
        else:
            try:
                k = float(strike)
                cp = float(current_price)
                if opt_type == "Call" and k > cp:
                    items.append(("OTM при покупке", "ok",
                                  f"Call {k:g} выше цены {cp:.2f}"))
                elif opt_type == "Put" and k < cp:
                    items.append(("OTM при покупке", "ok",
                                  f"Put {k:g} ниже цены {cp:.2f}"))
                else:
                    items.append(("OTM при покупке", "fail",
                                  f"{opt_type} {k:g} — не вне денег "
                                  f"(цена {cp:.2f})"))
            except Exception:
                items.append(("OTM при покупке", "skip",
                              "Не удалось проверить"))
    else:
        items.append(("OTM при покупке", "skip",
                      "Правило только для покупки опциона"))

    _MIN_DAYS = CHECKLIST_RULES["min_business_days_to_expiry"]
    if (side == "Buy" and opt_type in ("Call", "Put")
            and expiry_str and expiry_str != "—"):
        try:
            exp_d = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            today = date.today()
            if exp_d < today:
                items.append((f"Календарь {_MIN_DAYS} дней", "fail",
                              "Экспирация в прошлом"))
            else:
                bus = int(np.busday_count(today, exp_d))
                if bus < _MIN_DAYS:
                    items.append((f"Календарь {_MIN_DAYS} дней", "fail",
                                  f"{bus} раб. дней — меньше {_MIN_DAYS}"))
                else:
                    items.append((f"Календарь {_MIN_DAYS} дней", "ok",
                                  f"{bus} раб. дней — не меньше {_MIN_DAYS}"))
        except Exception:
            items.append((f"Календарь {_MIN_DAYS} дней", "skip",
                          "Не удалось разобрать дату"))
    else:
        items.append((f"Календарь {_MIN_DAYS} дней", "skip",
                      "Правило только для покупки"))

    _MIN_QTY = CHECKLIST_RULES["min_qty"]
    try:
        q = int(qty)
        if q < _MIN_QTY:
            items.append(("Чётное количество", "fail",
                          f"Минимум {_MIN_QTY} (введено {q})"))
        elif q % 2 != 0:
            items.append(("Чётное количество", "fail",
                          f"Введено {q} — нечётное"))
        else:
            items.append(("Чётное количество", "ok", f"{q}"))
    except Exception:
        items.append(("Чётное количество", "skip", "Не число"))

    if opt_type == "Put":
        try:
            q = int(qty)
            items.append(("Путы вдвое меньше коллов", "warn",
                          f"Проверьте объём: путы обычно вдвое меньше "
                          f"коллов (сейчас {q})"))
        except Exception:
            items.append(("Путы вдвое меньше коллов", "skip", ""))
    else:
        items.append(("Путы вдвое меньше коллов", "skip",
                      "Правило применяется к путам"))

    return items


def _validate_options_leg(side, opt_type, strike, expiry_str, qty,
                           current_price):
    errors, warnings = [], []
    _MIN_QTY = CHECKLIST_RULES["min_qty"]
    _MIN_DAYS = CHECKLIST_RULES["min_business_days_to_expiry"]

    try:
        _q = int(qty)
    except (TypeError, ValueError):
        _q = 0
    if _q < _MIN_QTY:
        errors.append(f"Чётное количество: минимум {_MIN_QTY} (введено {_q})")
    elif _q % 2 != 0:
        errors.append(f"Чётное количество: введено {_q} — нечётное")

    if side == "Buy":
        _k = None
        if strike is not None:
            try:
                _k = float(strike)
            except (TypeError, ValueError):
                _k = None

        if _k is not None:
            if current_price is None:
                warnings.append("OTM не проверен — нет текущей цены БА")
            else:
                _cp = float(current_price)
                if opt_type == "Call" and _k <= _cp:
                    errors.append(
                        f"Покупка Call должна быть вне денег: "
                        f"страйк {_k:g} не выше цены {_cp:.2f}"
                    )
                elif opt_type == "Put" and _k >= _cp:
                    errors.append(
                        f"Покупка Put должна быть вне денег: "
                        f"страйк {_k:g} не ниже цены {_cp:.2f}"
                    )

        if expiry_str and expiry_str not in ("—", ""):
            try:
                _exp_d = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                _today = date.today()
                if _exp_d < _today:
                    errors.append("Экспирация в прошлом")
                else:
                    _bus = int(np.busday_count(_today, _exp_d))
                    if _bus < _MIN_DAYS:
                        errors.append(
                            f"До экспирации {_bus} раб. дней — "
                            f"меньше {_MIN_DAYS}"
                        )
            except Exception:
                warnings.append("Не удалось разобрать дату экспирации")

    return errors, warnings


# =====================================================================
# Плановая дата закрытия
# =====================================================================
def _planned_close_date(expiry_str):
    try:
        exp_d = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    except Exception:
        return None
    days_to = (exp_d - date.today()).days
    if days_to <= CHECKLIST_RULES["close_weekly_days_threshold"]:
        return exp_d - timedelta(days=CHECKLIST_RULES["close_weekly_days_before"])
    return exp_d - timedelta(days=CHECKLIST_RULES["close_monthly_days_before"])


def _planned_close_badge(expiry_str):
    planned = _planned_close_date(expiry_str)
    if planned is None:
        return ""
    today = date.today()
    days_left = (planned - today).days
    if days_left < 0:
        return ("<div style='color:#d32f2f; font-weight:700; "
                "font-size:.78rem;'>🔴 закрыть сегодня</div>")
    if days_left <= 2:
        return ("<div style='color:#e6a500; font-weight:700; "
                "font-size:.78rem;'>🟡 готов к закрытию</div>")
    return ""


def _planned_close_date_str(expiry_str):
    planned = _planned_close_date(expiry_str)
    if planned is None:
        return "—"
    return planned.strftime("%d.%m.%Y")