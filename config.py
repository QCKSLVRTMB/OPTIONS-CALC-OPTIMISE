"""Константы проекта. Импортируется первым, до любых @st.cache_data(ttl=CHECKLIST_RULES[...])."""
import os

# ================= Признаки окружения =================
_IS_CLOUD = os.path.exists("/mount/src") or os.path.exists("/app")

LAZY_TABS = True

# ================= MOEX API =================
API_BASE_URL = "https://iss.moex.com/iss/apps/option-calc/v1"

ASSET_TYPE_MAP = {
    'Фьючерс': 'futures', 'Акция': 'share', 'Валюта': 'currency',
    'Товар': 'commodity', 'Индекс': 'index',
}

# ================= Комиссии =================
DEFAULT_COMM_OPTIONS_PCT = 3.0
DEFAULT_COMM_OPTIONS_MIN = 0.02
DEFAULT_COMM_FUTURES_PCT = 0.1
DEFAULT_COMM_STOCKS_PCT = 0.3
DEFAULT_COMM_METALS_PCT = 1.9
DEFAULT_COMM_CURRENCY_PCT = 0.9
DEFAULT_COMM_MIN_OTHER = 0.01

# ================= Чек-лист и настройки =================
CHECKLIST_RULES = {
    "min_business_days_to_expiry": 20,
    "min_qty": 2,
    "volume_spike_multiplier": 2.0,
    "volume_prev_days": 2,
    "fat_strike_min_points_monthly": 1000,
    "right_leg_min_profit_pct": 5.0,
    "right_leg_max_profit_pct": 10.0,
    "level_proximity_pct": 2.0,
    "skew_threshold_pct": 15.0,
    "touch_tolerance_pct": 0.1,
    "close_weekly_days_threshold": 10,
    "close_weekly_days_before": 1,
    "close_monthly_days_before": 2,
    "cache_volume_ttl_sec": 180,
    "cache_board_ttl_sec": 120,
    "cache_rvi_ttl_sec": 60,
    "cache_last_price_ttl_sec": 10,
    "cache_g_curve_ttl_sec": 600,
}

# ================= HTML-калькулятор =================
HTML_PLACEHOLDER = "/*__INJECT_PLACEHOLDER__*/{}"

# ================= Google Sheets с оповещениями =================
ALERTS_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vRKISFld2M8tFEEE1o1Fo5nQgHP6qMmOFu57JDmi-t-Y4Xj67N5SQP9uQ02JW2Ixyd663zewMYY1hfx"
    "/pub?output=xlsx"
)

# ================= Навигация =================
_TAB_NAMES = [
    "Калькулятор",
    "Доска опционов",
    "Позиция",
    "What-if",
    "Heatmap IV",
    "IV Rank",
    "Skew",
    "Журнал",
    "Келли",
    "Оповещения",
]