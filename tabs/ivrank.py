"""Вкладка «IV Rank / Децили и рекомендации»."""
from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.market_data import (
    classify_iv_vs_rvi,
    fetch_rvi_current,
    fetch_rvi_history,
    fetch_bars,
    _get_engine_market,
    resolve_underlying_secid,
)
from core.options import fetch_optionseries
from core.alerts import _weighted_avg_iv
from core.history import (
    _store_iv_snapshot,
    _remember_volatility_history,
)
from core.positions_cache import _get_board_for_analytics
from config import CHECKLIST_RULES  # noqa: F401  (не используется напрямую)


@st.fragment
def _render_ivrank_tab():
    st.header("IV Rank / Децили и рекомендации")
    st.caption("Оценка текущей подразумеваемой волатильности относительно "
               "её исторического диапазона.")

    _iv_asset = st.session_state.get("pos_asset", "")
    _iv_cat = st.session_state.get("pos_category", "")

    if not _iv_asset:
        st.warning("Сначала выберите актив и категорию на вкладке «Позиция».")
        return

    st.info(f"Актив: {_iv_asset} · Категория: {_iv_cat} "
            f"(из вкладки «Позиция»)")

    _series_for_iv = []
    try:
        _series_for_iv = fetch_optionseries(_iv_asset, _iv_cat) or []
    except Exception as e:
        st.error(f"Не удалось получить серии: {e}")
        return

    if not _series_for_iv:
        st.warning(f"ISS не вернул серий для {_iv_asset}")
        return

    from core.market_data import format_series_label
    _series_sorted_iv = sorted(
        _series_for_iv, key=lambda x: x.get("expiry", "")
    )
    _iv_series_labels = [format_series_label(s["expiry"])
                          for s in _series_sorted_iv]
    _iv_series_chosen_label = st.selectbox(
        "Опционная серия", _iv_series_labels, index=0,
        key="ivr_series_select",
    )
    _iv_chosen_idx = _iv_series_labels.index(_iv_series_chosen_label)
    _iv_chosen = _series_sorted_iv[_iv_chosen_idx]
    _iv_chosen_code = _iv_chosen["code"]
    _iv_chosen_expiry = _iv_chosen["expiry"]

    st.markdown("---")

    with st.spinner("Загрузка доски опционов…"):
        _board_iv = _get_board_for_analytics(
            _iv_asset, _iv_cat, _iv_chosen_code
        )

    if not _board_iv:
        st.warning("Не удалось загрузить доску опционов для этой серии.")
        return

    _iv_central = _board_iv.get("central_strike")
    _current_iv = _weighted_avg_iv(_board_iv, _iv_central)
    if _current_iv is None:
        st.warning("Не удалось вычислить текущую IV (нет данных).")
        return

    _store_iv_snapshot(_iv_asset, _iv_cat, _iv_chosen_code, _current_iv)

    st.markdown("### 1) Текущая IV vs. историческая волатильность")

    _hist_vol_map = {}
    _df_bars_iv = pd.DataFrame()

    try:
        _iv_eng, _iv_mkt = _get_engine_market(_iv_cat)
        _iv_secid = resolve_underlying_secid(_iv_asset, _iv_cat)
        if _iv_secid:
            _df_bars_iv = fetch_bars(_iv_secid, interval=24, days=180,
                                      engine=_iv_eng, market=_iv_mkt)
            if not _df_bars_iv.empty:
                _ret = np.log(_df_bars_iv["close"] /
                              _df_bars_iv["close"].shift(1)).dropna()
                for _win, _key in [(10, "hv10"), (20, "hv20"),
                                   (50, "hv50"), (100, "hv100")]:
                    if len(_ret) >= _win:
                        _hv = float(_ret.tail(_win).std()
                                    * np.sqrt(252) * 100)
                        _hist_vol_map[_key] = _hv
    except Exception:
        pass

    try:
        _rvi_history_frame = fetch_rvi_history(days=365)
    except Exception:
        _rvi_history_frame = None

    _remember_volatility_history(
        _iv_asset,
        _iv_cat,
        _df_bars_iv,
        _rvi_history_frame,
    )

    _m1, _m2, _m3, _m4, _m5 = st.columns(5)
    _m1.metric("Текущая IV", f"{_current_iv:.2f}%")
    _m2.metric("HV 10д", f"{_hist_vol_map.get('hv10', float('nan')):.2f}%"
               if "hv10" in _hist_vol_map else "—")
    _m3.metric("HV 20д", f"{_hist_vol_map.get('hv20', float('nan')):.2f}%"
               if "hv20" in _hist_vol_map else "—")
    _m4.metric("HV 50д", f"{_hist_vol_map.get('hv50', float('nan')):.2f}%"
               if "hv50" in _hist_vol_map else "—")
    _m5.metric("HV 100д", f"{_hist_vol_map.get('hv100', float('nan')):.2f}%"
               if "hv100" in _hist_vol_map else "—")

    _hv_avg = np.mean(list(_hist_vol_map.values())) if _hist_vol_map else None
    if _hv_avg is not None:
        _iv_hv_spread = _current_iv - _hv_avg
        if _iv_hv_spread > 3:
            st.warning(f"IV заметно выше средней HV на "
                       f"{_iv_hv_spread:+.2f} п.п. — "
                       f"опционы дорогие, кандидат на продажу волатильности.")
        elif _iv_hv_spread < -3:
            st.info(f"IV заметно ниже средней HV на "
                    f"{_iv_hv_spread:+.2f} п.п. — "
                    f"опционы дешёвые, кандидат на покупку волатильности.")
        else:
            st.success(f"IV около средней HV (дифференциал "
                       f"{_iv_hv_spread:+.2f} п.п.) — "
                       f"явного преимущества нет.")

    _rvi_rank_data = fetch_rvi_current()
    _rvi_rank = _rvi_rank_data.get("value") if _rvi_rank_data else None

    if _rvi_rank is not None:
        st.markdown("### 1b) IV vs RVI (рыночный фон)")
        _col_r1, _col_r2, _col_r3 = st.columns(3)
        with _col_r1:
            st.metric("RVI (рынок)", f"{_rvi_rank:.2f}%")
        with _col_r2:
            st.metric("IV инструмента", f"{_current_iv:.2f}%")
        with _col_r3:
            _spread_rvi = _current_iv - _rvi_rank
            _ratio_rvi = ((_current_iv / _rvi_rank)
                          if _rvi_rank > 0 else 0.0)
            st.metric("Спред / Ratio",
                      f"{_spread_rvi:+.2f} п.п. / {_ratio_rvi:.2f}×")

        _zone_rvi = classify_iv_vs_rvi(_current_iv, _rvi_rank)
        if _zone_rvi:
            st.markdown(
                f"<div style='padding:10px 14px; border-radius:8px; "
                f"background:{_zone_rvi['zone_color']}20; "
                f"border-left:4px solid {_zone_rvi['zone_color']}; "
                f"margin:8px 0;'>"
                f"<b>{_zone_rvi['zone_label']}</b> — {_zone_rvi['action']}"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.markdown("---")
    st.markdown("### 2) IV Rank / IV Percentile")

    _hist_key = f"{_iv_asset}|{_iv_cat}|{_iv_chosen_code}"
    _hist_bucket = st.session_state.get("_iv_history", {}).get(
        _hist_key, []
    )

    if len(_hist_bucket) < 5:
        st.warning(
            f"Недостаточно исторических данных по IV для этой серии: "
            f"собрано {len(_hist_bucket)} точек. "
            f"Данные накапливаются автоматически при каждом открытии "
            f"этой вкладки. Для полноценного IV Rank нужно ≥ 20 точек. "
            f"Пока используется только сравнение с HV."
        )
        if _hist_vol_map:
            _hv_min = min(_hist_vol_map.values())
            _hv_max = max(_hist_vol_map.values())
            if _hv_max > _hv_min:
                _iv_rank_fb = ((_current_iv - _hv_min)
                               / (_hv_max - _hv_min) * 100)
                st.info(f"Ориентировочный IV Rank (по диапазону HV): "
                        f"{_iv_rank_fb:.1f}%")
        _render_iv_recommendations(
            iv_rank=None, iv_pct=None, iv_current=_current_iv,
            hv_avg=_hv_avg, hist_available=False,
        )
        return

    _iv_values = np.array([v for _, v in _hist_bucket], dtype=float)
    _iv_min = float(np.min(_iv_values))
    _iv_max = float(np.max(_iv_values))
    _iv_rank = ((_current_iv - _iv_min) / (_iv_max - _iv_min) * 100) \
        if _iv_max > _iv_min else 50.0
    _iv_pct = float(np.mean(_iv_values <= _current_iv) * 100)

    try:
        _deciles = np.percentile(_iv_values, np.arange(0, 101, 10))
        _decile_of_iv = int(
            np.searchsorted(_deciles, _current_iv, side="right") - 1
        )
        _decile_of_iv = max(0, min(9, _decile_of_iv))
    except Exception:
        _deciles = None
        _decile_of_iv = None

    _r1, _r2, _r3, _r4 = st.columns(4)
    _r1.metric("IV Rank", f"{_iv_rank:.1f}%")
    _r2.metric("IV Percentile", f"{_iv_pct:.1f}%")
    _r3.metric("Мин IV (истор.)", f"{_iv_min:.2f}%")
    _r4.metric("Макс IV (истор.)", f"{_iv_max:.2f}%")

    if _deciles is not None:
        st.markdown("#### Положение текущей IV в децилях")
        _fig_dec = go.Figure()
        _fig_dec.add_trace(go.Bar(
            x=[f"D{i+1}" for i in range(10)],
            y=np.diff(_deciles),
            marker_color=[
                "#00a651" if i == _decile_of_iv else "#b0c4d4"
                for i in range(10)
            ],
            text=[f"{_deciles[i]:.1f}–{_deciles[i+1]:.1f}%"
                  for i in range(10)],
            textposition="outside",
            hovertemplate="Дециль %{x}<br>Диапазон: %{text}<extra></extra>",
        ))
        _fig_dec.add_hline(
            y=float(_current_iv - _deciles[_decile_of_iv]),
            line=dict(color="#d32f2f", width=3, dash="dash"),
            annotation_text=f"Текущая IV = {_current_iv:.2f}%",
            annotation_position="top right",
        )
        _fig_dec.update_layout(
            title=f"Распределение IV по децилям ({len(_iv_values)} наблюдений)",
            xaxis_title="Дециль", yaxis_title="Ширина дециля, п.п.",
            height=360, margin=dict(l=20, r=20, t=60, b=20),
            showlegend=False,
        )
        st.plotly_chart(_fig_dec, use_container_width=True)

        st.caption(
            f"Текущая IV {_current_iv:.2f}% попадает в "
            f"{_decile_of_iv+1}-й дециль "
            f"(диапазон {_deciles[_decile_of_iv]:.2f}–"
            f"{_deciles[_decile_of_iv+1]:.2f}%)."
        )

    st.markdown("---")

    _render_iv_recommendations(
        iv_rank=_iv_rank, iv_pct=_iv_pct, iv_current=_current_iv,
        hv_avg=_hv_avg, hist_available=True,
        decile_of_iv=_decile_of_iv,
    )


def _render_iv_recommendations(iv_rank, iv_pct, iv_current, hv_avg,
                                hist_available=True, decile_of_iv=None):
    st.markdown("### 3) Рекомендации")

    _zone = None
    _zone_color = "#333"
    _zone_label = "—"

    if hist_available and iv_rank is not None:
        if iv_rank <= 20:
            _zone = "low"
            _zone_label = "НИЗКАЯ IV (IV Rank ≤ 20%)"
            _zone_color = "#1e88e5"
        elif iv_rank <= 40:
            _zone = "low_mid"
            _zone_label = "НИЖЕ СРЕДНЕГО (IV Rank 20–40%)"
            _zone_color = "#00a651"
        elif iv_rank <= 60:
            _zone = "mid"
            _zone_label = "СРЕДНЯЯ IV (IV Rank 40–60%)"
            _zone_color = "#7a7a7a"
        elif iv_rank <= 80:
            _zone = "high_mid"
            _zone_label = "ВЫШЕ СРЕДНЕГО (IV Rank 60–80%)"
            _zone_color = "#e6a500"
        else:
            _zone = "high"
            _zone_label = "ВЫСОКАЯ IV (IV Rank > 80%)"
            _zone_color = "#d32f2f"

    if _zone is None and hv_avg is not None:
        _spread = iv_current - hv_avg
        if _spread <= -3:
            _zone = "low"
            _zone_label = "IV НИЖЕ HV (по дифф.)"
            _zone_color = "#1e88e5"
        elif _spread >= 3:
            _zone = "high"
            _zone_label = "IV ВЫШЕ HV (по дифф.)"
            _zone_color = "#d32f2f"
        else:
            _zone = "mid"
            _zone_label = "IV около HV"
            _zone_color = "#7a7a7a"

    st.markdown(
        f"<div style='padding:14px 18px; border-radius:12px; "
        f"background:{_zone_color}20; border-left:6px solid {_zone_color}; "
        f"margin-bottom:14px;'>"
        f"<div style='font-size:1.05rem; font-weight:800; color:{_zone_color};'>"
        f"{_zone_label}</div></div>",
        unsafe_allow_html=True,
    )

    if _zone == "low":
        st.markdown("#### Стратегии: покупка волатильности")
        st.markdown("""
**Логика:** IV в нижней части исторического диапазона — опционы
относительно дешёвые. Ожидаем роста IV → выгодны длинные опционные
позиции.

**Стратегии:**
| Стратегия | Комментарий |
|---|---|
| **Long Straddle** | Buy Call + Buy Put на одном страйке. |
| **Long Strangle** | Дешевле стрэддла, но требует большего движения. |
| **Обратный спрэд (Call / Put)** | Неограниченный потенциал с одной стороны. |
| **Календарный спрэд** | Продажа короткого, покупка длинного на том же страйке. |

**Стопы:** для стрэддлов — 50–60% от уплаченной премии.
""")
    elif _zone == "low_mid":
        st.markdown("#### Стратегии: умеренная покупка волатильности")
        st.markdown("""
IV ниже среднего, но не на дне диапазона. Возможны осторожные
покупки волатильности при наличии катализатора.

**Подходящие инструменты:**
- Long Straddle — с приоритетом на событийные опционы;
- Long Call / Long Put;
- Bull/Bear спреды с перевесом длинной стороны.

**Не рекомендуется:** продажа волатильности (мало премии).
""")
    elif _zone == "mid":
        st.markdown("#### Стратегии: нейтральная зона")
        st.markdown("""
IV в середине диапазона — явного преимущества ни у покупателя,
ни у продавца волатильности нет.

**Рекомендуется:**
- Направленные стратегии;
- Работать по технической картине БА;
- Искать наклон волатильности (вкладка «Детектор Skew»).

**Не рекомендуется:** ставить только на движение IV.
""")
    elif _zone == "high_mid":
        st.markdown("#### Стратегии: умеренная продажа волатильности")
        st.markdown("""
IV выше среднего — опционы относительно дорогие.

**Стратегии:**
| Стратегия | Комментарий |
|---|---|
| **Short Strangle** | Продажа Call и Put «вне денег». |
| **Short Straddle** | Максимум премии, узкий диапазон прибыли. |
| **Iron Condor** | Ограниченный риск. |
| **Пропорциональный спрэд** | Продажа 2 опционов + покупка 1 (1-к-2). |

Проверьте вкладку «Детектор Skew» — нет ли события.
""")
    elif _zone == "high":
        st.warning(
            "Высокая IV может отражать реальное ожидание "
            "крупного события (поглощение, суд, FDA, отчёт). Перед продажей "
            "волатильности проверьте новости и посмотрите наклон IV "
            "(вкладка «Детектор Skew»)."
        )
        st.markdown("#### Стратегии: продажа волатильности (с осторожностью)")
        st.markdown("""
**Стратегии:**
- **Short Strangle / Short Straddle** — с контролем риска;
- **Iron Condor** — ограниченный риск;
- **Крылья** — покупка дешёвых OTM для защиты.

**Правила:**
- Продавайте IV, когда она у верхней границы диапазона;
- Если IV пробивает диапазон на новые максимумы — прекращайте;
- Используйте дельта- и гамма-нейтральные позиции.
""")