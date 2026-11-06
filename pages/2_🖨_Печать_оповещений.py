"""
Страница «Печать оповещений (A4)» для Streamlit.

Читает данные из:
  1) st.session_state (alerts_df, _alert_checks) — если пользователь уже
     открывал вкладку «Оповещения» в app.py в этой же сессии;
  2) Google Sheets (серверный запрос) — если session_state пуст
     (защита от CORS: браузер не нужен, fetch идёт с сервера Streamlit).

Все значения проходят через _safe_str() — NaN/None/Inf/#N/A
превращаются в пустые строки ещё до инъекции в HTML.

app.py НЕ МОДИФИЦИРУЕТСЯ.
"""

import io
import json
import math
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

# ============================================================
# КОНФИГУРАЦИЯ СТРАНИЦЫ
# ============================================================
st.set_page_config(
    page_title="🖨 Печать оповещений — A4",
    page_icon="🖨",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# URL ЛИСТА (тот же, что в app.py)
# ============================================================
ALERTS_SHEET_CSV = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vRKISFld2M8tFEEE1o1Fo5nQgHP6qMmOFu57JDmi-t-Y4Xj67N5SQP9uQ02JW2Ixyd663zewMYY1hfx"
    "/pub?output=csv"
)

# Канонический порядок колонок вкладки «Оповещения»
KEEP_COLS = [
    "Тикер БА",
    "Категория БА",
    "Тренд",
    "Волна",
    "Тенденция",
    "ТФ уровня",
    "Уровень покупок",
    "Уровень продаж",
]

PLACEHOLDER = "/*__INJECT_ALERTS__*/null"


# ============================================================
# ПУТЬ К HTML (ищем рядом с app.py, а не в pages/)
# ============================================================
def _find_html_path() -> Path | None:
    """Ищем alerts_print.html в нескольких разумных местах."""
    candidates = []
    try:
        # pages/2_....py → родитель pages/ → родитель проекта
        candidates.append(
            Path(__file__).resolve().parent.parent / "alerts_print.html"
        )
    except Exception:
        pass
    try:
        candidates.append(Path.cwd() / "alerts_print.html")
    except Exception:
        pass

    for p in candidates:
        if p.exists():
            return p
    return None


HTML_PATH = _find_html_path()


# ============================================================
# УТИЛИТЫ: NaN → ""
# ============================================================
_EMPTY_TOKENS = {
    "", "nan", "none", "nat", "null", "undefined",
    "inf", "-inf", "infinity", "-infinity",
    "<na>", "#n/a", "#div/0!", "#value!", "#ref!",
    "#name?", "#num!", "#null!",
}


def _safe_str(v) -> str:
    """Любое значение → чистая строка. NaN/None/Inf/пустое → ''."""
    if v is None:
        return ""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return ""
    try:
        s = str(v).strip()
    except Exception:
        return ""
    if s.lower() in _EMPTY_TOKENS:
        return ""
    return s


def _clean_records(df: pd.DataFrame, keep_cols: list[str]) -> list[dict]:
    """DataFrame → список dict[str, str] без NaN-мусора."""
    if df is None or df.empty:
        return []
    cols = [c for c in keep_cols if c in df.columns]
    if not cols:
        return []
    records = []
    for _, row in df[cols].iterrows():
        rec = {c: _safe_str(row[c]) for c in cols}
        # Пропускаем полностью пустые строки
        if not any(rec.values()):
            continue
        records.append(rec)
    return records


# ============================================================
# ЗАГРУЗКА ДАННЫХ
# ============================================================
@st.cache_data(ttl=30, show_spinner=False)
def _fetch_alerts_from_gsheets() -> pd.DataFrame:
    """Серверный запрос к Google Sheets (без CORS)."""
    r = requests.get(
        ALERTS_SHEET_CSV,
        timeout=15,
        headers={"User-Agent": "Mozilla/5.0 (Streamlit Alerts Print)"},
    )
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text))


def _normalize_alerts(df: pd.DataFrame) -> pd.DataFrame:
    """Гибкое приведение заголовков Google Sheets к каноническим."""
    col_map = {}
    for c in df.columns:
        cs = str(c).strip().lower()
        if "тикер" in cs:
            col_map[c] = "Тикер БА"
        elif "категор" in cs:
            col_map[c] = "Категория БА"
        elif cs == "тренд":
            col_map[c] = "Тренд"
        elif cs == "волна":
            col_map[c] = "Волна"
        elif cs == "тенденция":
            col_map[c] = "Тенденция"
        elif cs in ("тф уровня", "tf уровня", "tf", "timeframe"):
            col_map[c] = "ТФ уровня"
        elif "покуп" in cs:
            col_map[c] = "Уровень покупок"
        elif "продаж" in cs:
            col_map[c] = "Уровень продаж"
    return df.rename(columns=col_map)


def _build_inject() -> dict:
    """Собираем INJECT: session_state → Google Sheets → пусто."""
    inject = {
        "alerts": [],
        "checks": {},
        "source": "none",
        "loaded_at": datetime.now().strftime("%H:%M:%S"),
    }

    # --- 1. session_state (если открывали «Оповещения» в app.py) ---
    df_ss = st.session_state.get("alerts_df")
    if isinstance(df_ss, pd.DataFrame) and not df_ss.empty:
        inject["alerts"] = _clean_records(df_ss, KEEP_COLS)
        if inject["alerts"]:
            inject["source"] = "app.py → session_state"

    # --- 2. Google Sheets с сервера ---
    if not inject["alerts"]:
        try:
            raw = _fetch_alerts_from_gsheets()
            norm = _normalize_alerts(raw)
            inject["alerts"] = _clean_records(norm, KEEP_COLS)
            inject["source"] = "Google Sheets (сервер)"
        except Exception as e:
            inject["source"] = f"ошибка: {e}"

    # --- 3. Чек-боксы из session_state ---
    checks_raw = st.session_state.get("_alert_checks") or {}
    checks_clean = {}
    if isinstance(checks_raw, dict):
        for key, val in checks_raw.items():
            if not isinstance(val, dict):
                continue
            b = _safe_str(val.get("buy"))
            s = _safe_str(val.get("sell"))
            entry = {}
            if b:
                entry["buy"] = b
            if s:
                entry["sell"] = s
            if entry:
                checks_clean[key] = entry
    inject["checks"] = checks_clean

    return inject


# ============================================================
# ИНЪЕКЦИЯ В HTML
# ============================================================
def _inject_data(html: str, inject: dict) -> str:
    """Подставляем JSON в плейсхолдер, защищая от </script>."""
    payload = json.dumps(inject, ensure_ascii=False, default=str)
    # Защита: закрывающий </script> не должен оборвать тег
    payload = payload.replace("</", "<\\/")
    return html.replace(PLACEHOLDER, payload)


# ============================================================
# UI
# ============================================================
st.title("🖨 Печать оповещений — A4")

if HTML_PATH is None:
    st.error(
        "Файл `alerts_print.html` не найден рядом с `app.py`.\n\n"
        "**Ожидаемая структура проекта:**\n"
        "```\n"
        "your-project/\n"
        "├── app.py\n"
        "├── alerts_print.html            ← должен быть здесь\n"
        "└── pages/\n"
        "    └── 2_🖨_Печать_оповещений.py\n"
        "```\n\n"
        "Положите `alerts_print.html` в корень проекта и перезапустите страницу."
    )
    st.stop()

# --- Читаем HTML ---
try:
    raw_html = HTML_PATH.read_text(encoding="utf-8")
except Exception as e:
    st.error(f"Не удалось прочитать `alerts_print.html`: {e}")
    st.stop()

if PLACEHOLDER not in raw_html:
    st.error(
        f"В `alerts_print.html` не найден плейсхолдер `{PLACEHOLDER}`.\n\n"
        "Убедитесь, что используете актуальную версию файла "
        "(с панелью настроек колонок)."
    )
    st.stop()

# --- Собираем данные ---
inject = _build_inject()

# --- Инжектим ---
html_final = _inject_data(raw_html, inject)

# --- Мини-панель управления ---
_ctrl1, _ctrl2, _ctrl3 = st.columns([2, 2, 1])

with _ctrl1:
    _n_rows = len(inject["alerts"])
    _src = inject["source"]
    _icon = "✅" if _n_rows > 0 else "⚠️"
    st.markdown(
        f"{_icon} **Загружено:** {_n_rows} строк · "
        f"**Источник:** `{_src}`"
    )

with _ctrl2:
    st.caption(
        f"Чек-боксов из `session_state`: "
        f"{len(inject['checks'])} · "
        f"обновлено в {inject['loaded_at']}"
    )

with _ctrl3:
    if st.button("🔄 Обновить", use_container_width=True, type="primary"):
        _fetch_alerts_from_gsheets.clear()
        st.rerun()

# --- Подсказка ---
if _n_rows == 0:
    st.warning(
        "Данных нет. Проверьте:\n"
        "1. Google Sheets опубликован (Файл → Поделиться → Опубликовать в интернете).\n"
        "2. Лист доступен по ссылке `.../pub?output=csv`.\n"
        "3. Если данные есть, но не грузятся — откройте вкладку «Оповещения» "
        "в основном приложении, затем вернитесь сюда."
    )
else:
    with st.expander("ℹ️ Что можно настроить внутри таблицы", expanded=False):
        st.markdown(
            """
- **⚙ Настройки таблицы** (раскрывающаяся панель) — чек-боксы «Показать / Заполнить» для каждой колонки.
- **6 пресетов** — одним кликом: *Полная / Без цен / Без тренда / Пустая / Только тикер / Тикер + уровни*.
- **💾 Скачать HTML** — сохраняет текущее состояние (данные + настройки колонок) в standalone-файл. Открывается в браузере без Streamlit, печатается через `Ctrl+P`.
- **🖨 Печать / PDF (A4)** — диалог печати. Выберите *Сохранить как PDF* + альбомная ориентация.
- **📥 Из Google Sheets** — принудительно перезагрузить данные прямо в браузере (если серверный кэш устарел).
- **🗑 Очистить** — 15 пустых строк для ручного заполнения.
            """
        )

st.markdown("---")

# --- Рендер iframe ---
components.html(html_final, height=1800, scrolling=True)
