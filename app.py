"""MOEX Options & Black-Scholes — точка входа.

Тонкий слой: настройка страницы, инициализация session_state,
чтение query-параметров, диспетчеризация вкладок.
Вся логика — в пакетах config.py, core/, ui/, tabs/.

Порядок имеет значение:
1. st.set_page_config — первым Streamlit-вызовом
2. import config — до @st.cache_data(ttl=CHECKLIST_RULES[...])
3. стили, RVI-header, session_state
4. _render_backup_controls() — до диспетчера вкладок
5. навигация (selectbox) и рендер выбранной вкладки
"""
import streamlit as st

st.set_page_config(
    page_title="MOEX Options & Black-Scholes",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Гарантируем загрузку констант ДО модулей с @st.cache_data(ttl=...).
import config  # noqa: F401
from config import _TAB_NAMES

from core.helpers import _safe_float_qp
from core.market_data import fetch_rvi_current
from core.positions_cache import _load_cache

from ui.styles import inject_base_styles, inject_responsive_styles
from ui.backup import _render_backup_controls

from tabs.calc import _render_calc_tab
from tabs.board import _render_board_tab
from tabs.position import _render_position_tab
from tabs.whatif import _render_whatif_tab
from tabs.heatmap import _render_heatmap_tab
from tabs.ivrank import _render_ivrank_tab
from tabs.skew import _render_skew_tab
from tabs.journal import _render_journal_tab
from tabs.kelly import _render_kelly_tab
from tabs.alerts import _render_alerts_tab
from tabs.vol_analysis import _render_vol_analysis_tab  # ← NEW


# ==================================================================
# Стили
# ==================================================================
inject_base_styles()
inject_responsive_styles()


# ==================================================================
# Заголовок
# ==================================================================
st.title("MOEX Options & Black-Scholes")


# ==================================================================
# RVI header
# ==================================================================
_rvi_header = fetch_rvi_current()
if _rvi_header and _rvi_header.get("value") is not None:
    _rvi_v = _rvi_header["value"]
    _rvi_chg = _rvi_header.get("change") or 0.0
    _rvi_chg_pct = _rvi_header.get("change_pct") or 0.0

    if _rvi_v < 20:
        _rvi_color = "#00a651"
        _rvi_icon = "🟢"
    elif _rvi_v < 35:
        _rvi_color = "#e6a500"
        _rvi_icon = "🟡"
    else:
        _rvi_color = "#d32f2f"
        _rvi_icon = "🔴"

    st.markdown(
        f"<div style='display:flex; align-items:center; gap:12px; "
        f"padding:6px 14px; background:#f0f4f8; border-radius:8px; "
        f"margin-bottom:12px; font-size:.9rem;'>"
        f"<span style='font-weight:700; color:{_rvi_color};'>"
        f"{_rvi_icon} RVI: {_rvi_v:.2f}%</span>"
        f"<span style='color:{_rvi_color};'>"
        f"({_rvi_chg:+.2f} п.п. / {_rvi_chg_pct:+.2f}%)</span>"
        f"<span style='color:#5a6b78; font-size:.8rem;'>"
        f"обновлено: {_rvi_header.get('update_time', '—')}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ==================================================================
# Query params → session_state
# (для iframe-калькулятора, который синхронизирует значения через URL)
# ==================================================================
st.session_state["_calc_level_buy"] = _safe_float_qp("level_buy", 0.0)
st.session_state["_calc_level_sell"] = _safe_float_qp("level_sell", 0.0)
st.session_state["_calc_riskfree"] = _safe_float_qp("rf_buy", 0.0)
st.session_state["_calc_volatility"] = _safe_float_qp("vol_buy", 0.0)
st.session_state["_calc_dividend"] = _safe_float_qp("div_buy", 0.0)


# ==================================================================
# Инициализация session_state
# ==================================================================
if "pos_asset" not in st.session_state:
    st.session_state.pos_asset = st.session_state.get("board_asset", "SBER")
if "pos_category" not in st.session_state:
    st.session_state.pos_category = st.session_state.get(
        "board_category", "Акция"
    )
if "positions_cache" not in st.session_state:
    st.session_state.positions_cache = _load_cache()
if "positions" not in st.session_state:
    st.session_state.positions = []
if "_pos_signature" not in st.session_state:
    st.session_state["_pos_signature"] = None
if "_g3_migrated" not in st.session_state:
    st.session_state["_g3_migrated"] = False
if "pos_view_mode" not in st.session_state:
    st.session_state.pos_view_mode = "Карточки (мобильный)"


# ==================================================================
# Резервная копия — ДО диспетчера вкладок, вне @st.fragment.
# Импорт должен применяться до создания виджетов вкладки.
# ==================================================================
_render_backup_controls()


# ==================================================================
# Навигация
# ==================================================================
_active_tab = st.selectbox(
    "Раздел",
    _TAB_NAMES,
    key="main_nav",
    label_visibility="collapsed",
)


# ==================================================================
# Диспетчер
# ==================================================================
_RENDERERS = {
    "Калькулятор": _render_calc_tab,
    "Позиция": _render_position_tab,
    "What-if": _render_whatif_tab,
    "Heatmap IV": _render_heatmap_tab,
    "IV Rank": _render_ivrank_tab,
    "Skew": _render_skew_tab,
    "Журнал": _render_journal_tab,
    "Келли": _render_kelly_tab,
    "Оповещения": _render_alerts_tab,
    "Vol Analysis": _render_vol_analysis_tab,  # ← NEW
}

if _active_tab == "Доска опционов":
    # Доска вне адаптивного контейнера — её CSS не переопределяется.
    _render_board_tab()
else:
    with st.container(key="responsive_content"):
        _RENDERERS[_active_tab]()
