"""Накопление IV / HV / RVI истории."""
import math
from datetime import date

import numpy as np
import pandas as pd
import streamlit as st

from core.backup import _backup_clean


def _store_iv_snapshot(asset, atype, series, iv_value):
    if iv_value is None:
        return
    today_str = date.today().isoformat()
    key = f"{asset}|{atype}|{series}"
    history = st.session_state.get("_iv_history")
    if history is None:
        history = {}
        st.session_state["_iv_history"] = history
    bucket = history.setdefault(key, [])
    for d, _ in bucket:
        if d == today_str:
            return
    bucket.append((today_str, float(iv_value)))
    if len(bucket) > 500:
        history[key] = bucket[-500:]


def _merge_history_records(previous, incoming):
    merged = {
        row["date"]: dict(row)
        for row in previous
        if isinstance(row, dict) and row.get("date")
    }
    for row in incoming:
        day = row["date"]
        target = merged.setdefault(day, {"date": day})
        for key, value in row.items():
            if value is not None:
                target[key] = value
    return [merged[day] for day in sorted(merged)]


def _remember_volatility_history(asset, category, bars, rvi_frame=None):
    if isinstance(bars, pd.DataFrame) and not bars.empty:
        frame = bars[["begin", "close"]].copy()
        frame["begin"] = pd.to_datetime(frame["begin"], errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = (
            frame.dropna()
            .query("close > 0")
            .sort_values("begin")
            .drop_duplicates("begin", keep="last")
        )
        returns = np.log(frame["close"] / frame["close"].shift(1))
        history = pd.DataFrame({
            "date": frame["begin"].dt.strftime("%Y-%m-%d")
        })
        for window in (10, 20, 50, 100):
            history[f"hv{window}"] = (
                returns.rolling(window).std(ddof=1)
                * np.sqrt(252)
                * 100
            )
        fields = ["hv10", "hv20", "hv50", "hv100"]
        history = history.dropna(subset=fields, how="all")
        incoming = _backup_clean(history.to_dict(orient="records"))
        key = f"{asset}|{category}"
        all_hv = dict(st.session_state.get("_hv_history", {}))
        all_hv[key] = _merge_history_records(all_hv.get(key, []), incoming)
        st.session_state["_hv_history"] = all_hv

    if isinstance(rvi_frame, pd.DataFrame) and not rvi_frame.empty:
        frame = rvi_frame[["begin", "close"]].copy()
        frame["begin"] = pd.to_datetime(frame["begin"], errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna().sort_values("begin")
        incoming = [
            {
                "date": timestamp.date().isoformat(),
                "value": float(value),
            }
            for timestamp, value in zip(frame["begin"], frame["close"])
            if math.isfinite(float(value)) and float(value) >= 0
        ]
        st.session_state["_rvi_history"] = _merge_history_records(
            st.session_state.get("_rvi_history", []),
            incoming,
        )