"""core/volatility.py — метрики волатильности по книге Коннолли.

К. Коннолли, «Покупка и продажа волатильности» (ИК «Аналитика», Москва):
  • греки call/put                     — гл. 3.9–3.11
  • длинная/короткая волатильность     — гл. 4.7, 5.2  (по знаку Γ)
  • break-even daily move              — гл. 4.3
  • σ-диапазон цены                     — гл. 2.7
  • паритет пут-колл и синтетика        — гл. 6.5

Никаких st.*, никаких импортов из core/. Тестируется в чистом Python:
    python -c "from core.volatility import bs_greeks; print(bs_greeks(99,100,0,0,15,1,'C'))"
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Iterable, Optional


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass(frozen=True)
class Greeks:
    price: float
    delta: float
    gamma: float
    theta: float   # в ₽ за 1 день
    vega: float    # в ₽ на +1% IV
    rho: float     # в ₽ на +1% ставки


def bs_greeks(
    S: float, K: float,
    r_pct: float, q_pct: float, sigma_pct: float,
    T_years: float, option_type: str = "C",
) -> Greeks:
    """Греки европейского опциона в единицах проекта (см. докстринг)."""
    if T_years <= 0 or sigma_pct <= 0 or S <= 0 or K <= 0:
        itm = max(0.0, S - K) if option_type.upper() == "C" else max(0.0, K - S)
        return Greeks(itm, 0.0, 0.0, 0.0, 0.0, 0.0)

    r = r_pct / 100.0
    q = q_pct / 100.0
    sigma = sigma_pct / 100.0
    sqrtT = math.sqrt(T_years)

    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T_years) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    Nd1, Nd2 = _ncdf(d1), _ncdf(d2)
    n1 = _npdf(d1)
    dq, dr = math.exp(-q * T_years), math.exp(-r * T_years)

    gamma = dq * n1 / (S * sigma * sqrtT)
    vega = S * dq * n1 * sqrtT * 0.01              # на +1% IV

    if option_type.upper() == "C":
        price = S * dq * Nd1 - K * dr * Nd2
        delta = dq * Nd1
        theta_year = (-S * dq * n1 * sigma / (2 * sqrtT)
                      + q * S * dq * Nd1
                      - r * K * dr * Nd2)
        rho = K * T_years * dr * Nd2 * 0.01        # на +1% ставки
    else:
        price = K * dr * (1 - Nd2) - S * dq * (1 - Nd1)
        delta = -dq * (1 - Nd1)
        theta_year = (-S * dq * n1 * sigma / (2 * sqrtT)
                      - q * S * dq * (1 - Nd1)
                      + r * K * dr * (1 - Nd2))
        rho = -K * T_years * dr * (1 - Nd2) * 0.01

    return Greeks(price, delta, gamma, theta_year / 365.0, vega, rho)


def long_or_short_volatility(gamma_portfolio: float) -> str:
    """Гл. 4.7 / 5.2: знак Γ определяет длинную/короткую позицию по vol."""
    if gamma_portfolio > 1e-12:
        return "длинная"
    if gamma_portfolio < -1e-12:
        return "короткая"
    return "нейтральная"


def break_even_daily_move(
    theta_portfolio: float, gamma_portfolio: float,
) -> Optional[float]:
    """Гл. 4.3: |ΔS| в день, при котором Γ-прибыль покрывает Θ-распад.

    P&L ≈ ½ · Γ · (ΔS)² − |Θ_день| = 0   →   ΔS = √(2·|Θ| / |Γ|)
    """
    if abs(gamma_portfolio) < 1e-12:
        return None
    if theta_portfolio * gamma_portfolio >= 0:
        return None                                 # знаки совпали — break-even нет
    return math.sqrt(2.0 * abs(theta_portfolio) / abs(gamma_portfolio))


def sigma_range_forecast(
    S: float, sigma_pct: float, T_years: float,
    n_sigmas: Iterable[float] = (1, 2, 3),
) -> dict:
    """Гл. 2.7: вероятностный диапазон цены через T лет."""
    if T_years <= 0 or sigma_pct <= 0 or S <= 0:
        return {n: (S, S) for n in n_sigmas}
    s = (sigma_pct / 100.0) * math.sqrt(T_years)
    return {n: (S * math.exp(-n * s), S * math.exp(n * s)) for n in n_sigmas}


def put_call_parity_deviation(
    C: float, P: float, S: float, K: float,
    r_pct: float, q_pct: float, T_years: float,
) -> float:
    """Гл. 6.5: C − P − (S·e^(−qT) − K·e^(−rT)). |dev| > 0 — арбитраж."""
    r, q = r_pct / 100.0, q_pct / 100.0
    return (C - P) - (S * math.exp(-q * T_years) - K * math.exp(-r * T_years))


def synthetic_put_from_call(
    C: float, S: float, K: float,
    r_pct: float, q_pct: float, T_years: float,
) -> float:
    """Гл. 6.5:  P = C − S·e^(−qT) + K·e^(−rT)."""
    r, q = r_pct / 100.0, q_pct / 100.0
    return C - S * math.exp(-q * T_years) + K * math.exp(-r * T_years)


def synthetic_call_from_put(
    P: float, S: float, K: float,
    r_pct: float, q_pct: float, T_years: float,
) -> float:
    """Гл. 6.5:  C = P + S·e^(−qT) − K·e^(−rT)."""
    r, q = r_pct / 100.0, q_pct / 100.0
    return P + S * math.exp(-q * T_years) - K * math.exp(-r * T_years)


def aggregate_portfolio_greeks(positions: list[dict]) -> dict:
    """Σ греков по позициям с учётом знака qty. Γ>0 — длинная vol."""
    tot = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    for p in positions:
        if not p.get("visible", True):
            continue
        if p.get("Тип инструмента") != "Опцион":
            continue
        qty = int(p.get("Кол-во", 0) or 0)
        if qty == 0:
            continue
        for key, field in (("delta", "Дельта"), ("gamma", "Гамма"),
                           ("theta", "Тета"), ("vega", "Вега"),
                           ("rho", "Ро")):
            val = p.get(field)
            if isinstance(val, (int, float)):
                tot[key] += float(val) * qty
    return tot