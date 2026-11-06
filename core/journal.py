"""Хелперы для журнала сделок."""
from datetime import date

import pandas as pd
import streamlit as st


def _journal_closed_sorted(rows):
    if not rows:
        return []
    closed = [r for r in rows
              if r.get("Статус") in ("Закрыта", "Сгорел")]
    try:
        return sorted(
            closed,
            key=lambda r: (str(r.get("Дата входа", "2000-01-01")),
                            str(r.get("_id", ""))),
        )
    except Exception:
        return closed


def _journal_equity_series(rows, initial_deposit):
    closed = _journal_closed_sorted(rows)
    if not closed:
        return pd.DataFrame(
            columns=["date", "pnl", "balance", "peak",
                     "drawdown", "drawdown_pct"]
        )

    _balance = float(initial_deposit or 0.0)
    _peak = _balance
    _records = []
    for r in closed:
        _p = float(r.get("P&L") or 0.0)
        _balance += _p
        if _balance > _peak:
            _peak = _balance
        _dd = _balance - _peak
        _dd_pct = (_dd / _peak * 100.0) if _peak > 0 else 0.0
        _records.append({
            "date": str(r.get("Дата входа", "")),
            "pnl": _p,
            "balance": _balance,
            "peak": _peak,
            "drawdown": _dd,
            "drawdown_pct": _dd_pct,
        })
    return pd.DataFrame(_records)


def _journal_max_drawdown(equity_df):
    if equity_df is None or equity_df.empty:
        return 0.0, 0.0
    _min_dd = float(equity_df["drawdown"].min())
    _min_dd_pct = float(equity_df["drawdown_pct"].min())
    return abs(_min_dd), abs(_min_dd_pct)


def _journal_profit_factor(closed):
    if not closed:
        return None
    _wins = sum((r.get("P&L") or 0) for r in closed
                if (r.get("P&L") or 0) > 0)
    _losses = abs(sum((r.get("P&L") or 0) for r in closed
                      if (r.get("P&L") or 0) < 0))
    if _losses > 0:
        return _wins / _losses
    if _wins > 0:
        return float("inf")
    return None


def _journal_streaks(closed):
    result = {
        "max_wins": 0, "max_losses": 0,
        "avg_wins": 0.0, "avg_losses": 0.0,
        "current_type": None, "current_len": 0,
    }
    if not closed:
        return result

    _win_streaks = []
    _loss_streaks = []
    _cur = None
    _cur_len = 0
    for r in closed:
        _p = r.get("P&L") or 0
        _type = "win" if _p > 0 else ("loss" if _p < 0 else None)
        if _type is None:
            continue
        if _type == _cur:
            _cur_len += 1
        else:
            if _cur == "win" and _cur_len > 0:
                _win_streaks.append(_cur_len)
            elif _cur == "loss" and _cur_len > 0:
                _loss_streaks.append(_cur_len)
            _cur = _type
            _cur_len = 1
    if _cur == "win" and _cur_len > 0:
        _win_streaks.append(_cur_len)
    elif _cur == "loss" and _cur_len > 0:
        _loss_streaks.append(_cur_len)

    result["max_wins"] = max(_win_streaks) if _win_streaks else 0
    result["max_losses"] = max(_loss_streaks) if _loss_streaks else 0
    result["avg_wins"] = (sum(_win_streaks) / len(_win_streaks)
                          if _win_streaks else 0.0)
    result["avg_losses"] = (sum(_loss_streaks) / len(_loss_streaks)
                            if _loss_streaks else 0.0)
    result["current_type"] = _cur
    result["current_len"] = _cur_len
    return result


def _journal_stats_by(closed, key):
    if not closed:
        return pd.DataFrame()
    groups = {}
    for r in closed:
        k = str(r.get(key, "—")).strip() or "—"
        if k not in groups:
            groups[k] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
        g = groups[k]
        g["trades"] += 1
        p = r.get("P&L") or 0
        g["pnl"] += p
        if p > 0:
            g["wins"] += 1
        elif p < 0:
            g["losses"] += 1
    rows = []
    for k, g in groups.items():
        wr = (g["wins"] / g["trades"] * 100.0) if g["trades"] > 0 else 0.0
        rows.append({
            key: k,
            "Сделок": g["trades"],
            "Win-rate, %": round(wr, 1),
            "P&L, ₽": round(g["pnl"], 2),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("P&L, ₽", ascending=False).reset_index(drop=True)
    return df


def _journal_get_deposit():
    _init = float(st.session_state.get("journal_initial_deposit",
                                        100000.0) or 100000.0)
    _mode = st.session_state.get("journal_deposit_mode", "auto")
    _rows = st.session_state.get("journal_rows", [])
    _closed = _journal_closed_sorted(_rows)
    _total_closed_pnl = sum((r.get("P&L") or 0) for r in _closed)

    if _mode == "manual":
        _cur = float(st.session_state.get("journal_current_deposit_manual",
                                           _init) or _init)
    else:
        _cur = _init + _total_closed_pnl

    return _init, _cur, _mode