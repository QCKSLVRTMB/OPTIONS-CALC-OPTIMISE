"""Расчёты цен опционов (Black-Scholes), payoff и портфельная стоимость.

Здесь нет Streamlit-виджетов — только математика. Функции принимают
позиции (list of dict) и возвращают числа / numpy-массивы.
"""
import math
from datetime import date, datetime

import numpy as np

from core.market_data import (
    _get_engine_market,
    resolve_underlying_secid,
    get_last_close_price,
)


# =====================================================================
# Внутренние помощники BS
# =====================================================================
def _months_between(d1: date, d2: date) -> float:
    return max((d2 - d1).days, 0) / 365.0


def _bs_price_np(S, K, T, r_pct, vol_pct, q_pct, opt_type):
    if T <= 0 or S <= 0 or K <= 0 or vol_pct <= 0:
        return max(0.0, S - K) if opt_type == "call" else max(0.0, K - S)
    r = r_pct / 100.0
    q = q_pct / 100.0
    sigma = vol_pct / 100.0
    sqrtT = math.sqrt(T)
    try:
        d1 = (math.log(S / K) + (r - q + sigma * sigma / 2) * T) / (sigma * sqrtT)
        d2 = d1 - sigma * sqrtT
        nd1 = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
        nd2 = 0.5 * (1.0 + math.erf(d2 / math.sqrt(2.0)))
        if opt_type == "call":
            return S * math.exp(-q * T) * nd1 - K * math.exp(-r * T) * nd2
        return K * math.exp(-r * T) * (1 - nd2) - S * math.exp(-q * T) * (1 - nd1)
    except Exception:
        return max(0.0, S - K) if opt_type == "call" else max(0.0, K - S)


# =====================================================================
# Payoff-профиль портфеля
# =====================================================================
def compute_payoff(positions, S_values, comm_func=None,
                   anchor_expiry: str = None, pricing_date: str = None,
                   vol_default: float = 20.0, r_default: float = 0.0,
                   q_default: float = 0.0):
    S = np.asarray(S_values, dtype=float)
    pnl = np.zeros_like(S)
    all_expiries = []
    for p in positions:
        exp_str = p.get("Эксп.", "—")
        if exp_str and exp_str != "—":
            try:
                d = datetime.strptime(exp_str, "%Y-%m-%d").date()
                all_expiries.append(d)
            except Exception:
                pass
    if anchor_expiry:
        try:
            anchor_date = datetime.strptime(anchor_expiry, "%Y-%m-%d").date()
        except Exception:
            anchor_date = max(all_expiries) if all_expiries else date.today()
    else:
        anchor_date = max(all_expiries) if all_expiries else date.today()

    for p in positions:
        if not p.get("visible", True):
            continue
        qty = int(p.get("Кол-во", 0))
        entry = float(p.get("Цена", 0))
        _instr = p.get("Тип инструмента", "Опцион")
        com = comm_func(entry, _instr) if comm_func else 0.0
        if (p.get("Тип инструмента") == "БА"
                or p.get("Опцион") == "БА"):
            pnl += (S - entry) * qty
            continue
        K = float(p["Страйк"]) if p.get("Страйк") is not None else 0
        if K == 0:
            continue
        leg_expiry_str = p.get("Эксп.", "—")
        leg_expiry = None
        if leg_expiry_str and leg_expiry_str != "—":
            try:
                leg_expiry = datetime.strptime(leg_expiry_str, "%Y-%m-%d").date()
            except Exception:
                leg_expiry = None
        if leg_expiry is None:
            intrinsic = (np.maximum(0, S - K) if p["Опцион"] == "Call"
                         else np.maximum(0, K - S))
            pnl += (intrinsic - entry - com) * qty
            continue
        if leg_expiry <= anchor_date:
            intrinsic = (np.maximum(0, S - K) if p["Опцион"] == "Call"
                         else np.maximum(0, K - S))
            pnl += (intrinsic - entry - com) * qty
        else:
            T_remaining = _months_between(anchor_date, leg_expiry)
            opt_type = "call" if p["Опцион"] == "Call" else "put"
            vol_leg = float(p.get("_vol", vol_default) or vol_default)
            r_leg = float(p.get("_r", r_default) or r_default)
            q_leg = float(p.get("_q", q_default) or q_default)
            price_at_anchor = np.array([
                _bs_price_np(Si, K, T_remaining, r_leg, vol_leg, q_leg,
                             opt_type)
                for Si in S
            ])
            pnl += (price_at_anchor - entry - com) * qty
    return pnl


def find_breakevens(positions, price_min, price_max, n=500,
                    comm_func=None, anchor_expiry=None):
    prices = np.linspace(price_min, price_max, n)
    pnl = compute_payoff(positions, prices, comm_func=comm_func,
                         anchor_expiry=anchor_expiry)
    be = []
    for i in range(1, len(prices)):
        if pnl[i - 1] * pnl[i] < 0:
            denom = pnl[i] - pnl[i - 1]
            if abs(denom) > 1e-12:
                x0 = (prices[i - 1]
                      + (prices[i] - prices[i - 1])
                      * (-pnl[i - 1]) / denom)
                be.append(float(x0))
    return be


# =====================================================================
# Портфельная стоимость для What-if симулятора
# =====================================================================
def _portfolio_value(positions, S_vals, days_forward, iv_pct,
                     comm_func=None):
    S = np.asarray(S_vals, dtype=float)
    total = np.zeros_like(S)
    if days_forward is None:
        days_forward = 0

    for p in positions:
        if not p.get("visible", True):
            continue
        qty = int(p.get("Кол-во", 0))
        if qty == 0:
            continue
        entry = float(p.get("Цена", 0))
        _instr = p.get("Тип инструмента", "Опцион")
        com = comm_func(entry, _instr) if comm_func else 0.0

        if _instr == "БА" or p.get("Опцион") == "БА":
            total += (S - entry) * qty
            continue

        K = p.get("Страйк")
        if K is None:
            continue
        K = float(K)

        exp_str = p.get("Эксп.", "—")
        leg_expiry = None
        if exp_str and exp_str != "—":
            try:
                leg_expiry = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except Exception:
                leg_expiry = None

        r_leg = float(p.get("_r", 0.0) or 0.0)
        q_leg = float(p.get("_q", 0.0) or 0.0)
        vol_leg = float(p.get("_vol", iv_pct or 20.0) or 20.0)
        opt_type = "call" if p.get("Опцион") == "Call" else "put"

        if leg_expiry is None:
            price_now = (np.maximum(0, S - K) if opt_type == "call"
                         else np.maximum(0, K - S))
        else:
            days_left = (leg_expiry - date.today()).days - days_forward
            if days_left <= 0:
                price_now = (np.maximum(0, S - K) if opt_type == "call"
                             else np.maximum(0, K - S))
            else:
                T_years = days_left / 365.0
                if iv_pct is not None:
                    vol_leg = float(iv_pct)
                price_now = np.array([
                    _bs_price_np(s, K, T_years, r_leg, vol_leg, q_leg,
                                 opt_type)
                    for s in S
                ])

        total += (price_now - entry - com) * qty
    return total


def _portfolio_value_with_rvi(positions, S_vals, days_forward,
                               iv_pct, rvi_shift, correlation,
                               comm_func=None):
    S = np.asarray(S_vals, dtype=float)
    total = np.zeros_like(S)
    if days_forward is None:
        days_forward = 0

    for p in positions:
        if not p.get("visible", True):
            continue
        qty = int(p.get("Кол-во", 0))
        if qty == 0:
            continue
        entry = float(p.get("Цена", 0))
        _instr = p.get("Тип инструмента", "Опцион")
        com = comm_func(entry, _instr) if comm_func else 0.0

        if _instr == "БА" or p.get("Опцион") == "БА":
            total += (S - entry) * qty
            continue

        K = p.get("Страйк")
        if K is None:
            continue
        K = float(K)

        exp_str = p.get("Эксп.", "—")
        leg_expiry = None
        if exp_str and exp_str != "—":
            try:
                leg_expiry = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except Exception:
                leg_expiry = None

        r_leg = float(p.get("_r", 0.0) or 0.0)
        q_leg = float(p.get("_q", 0.0) or 0.0)
        vol_base = float(p.get("_vol", iv_pct or 20.0) or 20.0)
        vol_adjusted = vol_base + rvi_shift * correlation
        opt_type = "call" if p.get("Опцион") == "Call" else "put"

        if leg_expiry is None:
            price_now = (np.maximum(0, S - K) if opt_type == "call"
                         else np.maximum(0, K - S))
        else:
            days_left = (leg_expiry - date.today()).days - days_forward
            if days_left <= 0:
                price_now = (np.maximum(0, S - K) if opt_type == "call"
                             else np.maximum(0, K - S))
            else:
                T_years = days_left / 365.0
                price_now = np.array([
                    _bs_price_np(s, K, T_years, r_leg, vol_adjusted, q_leg,
                                 opt_type)
                    for s in S
                ])

        total += (price_now - entry - com) * qty
    return total


# =====================================================================
# Текущая рыночная цена БА (обёртка)
# =====================================================================
def _get_current_market_price_for_asset(asset, atype):
    if not (asset and atype):
        return None
    try:
        eng, mkt = _get_engine_market(atype)
        secid = resolve_underlying_secid(asset, atype)
        if secid is None:
            return None
        return get_last_close_price(secid, eng, mkt)
    except Exception:
        return None