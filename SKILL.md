# SKILL: Рефакторинг монолитного Streamlit-приложения в пакеты `core/`, `ui/`, `tabs/`

## Назначение

Навык описывает **безопасное** разбиение одного большого `app.py`
(~6000 строк, 120+ функций, 10 вкладок) на пакеты без потери
функциональности. Применялся для проекта MOEX Options & Black-Scholes.

**Ключевой принцип:** монолит остаётся рабочим до самого последнего
этапа. После каждого шага запускаем `streamlit run app.py` — если
сломалось, откатываем шаг.

## Когда применять

- `app.py` > 3000 строк, навигация по нему занимает > 10 секунд.
- Несколько функций повторяются между вкладками (комиссии, форматирование, расчёты).
- Есть `@st.fragment`-ы, которые нужно переиспользовать.
- Хочется тестировать бизнес-логику без запуска Streamlit.

**Когда НЕ применять:**
- < 1000 строк — оверхед на навигацию больше, чем выгода.
- Проект живёт 1–2 недели.
- Нет возможности прогонять тесты после каждого шага.

## Целевая структура

```
project/
├── app.py                # тонкий вход (~100-150 строк)
├── config.py             # все константы (CHECKLIST_RULES, ASSET_TYPE_MAP, ...)
├── check_functions.py    # авто-проверка целостности
├── core/                 # бизнес-логика, БЕЗ Streamlit-виджетов (кроме кэша/сессии)
│   ├── __init__.py
│   ├── iss_api.py        # HTTP-клиент
│   ├── instruments.py    # справочники, резолверы кодов
│   ├── market_data.py    # внешние данные (цены, бары, комиссии)
│   ├── options.py        # серии, доски, IV
│   ├── pricing.py        # математика (BS, payoff)
│   ├── alerts.py         # уровни, чек-боксы, targets
│   ├── strategies.py     # рекомендации, legs, чек-лист
│   ├── history.py        # накопление IV/HV/RVI
│   ├── journal.py        # статистика журнала
│   ├── positions_cache.py# файловый кэш
│   ├── backup.py         # экспорт/импорт JSON
│   └── helpers.py        # утилиты (fmt, id, postMessage)
├── ui/                   # переиспользуемые Streamlit-компоненты
│   ├── __init__.py
│   ├── styles.py
│   ├── checklist.py
│   ├── alert_card.py
│   ├── charts.py
│   └── backup.py
└── tabs/                 # по одному файлу на вкладку
    ├── __init__.py
    ├── calc.py
    ├── board.py
    ├── position.py       # самый большой (~1500 строк)
    ├── whatif.py
    ├── heatmap.py
    ├── ivrank.py
    ├── skew.py
    ├── journal.py
    ├── kelly.py
    └── alerts.py
```

## Граф зависимостей (DAG, без циклов)

```
config ← core.iss_api ← core.instruments ← core.market_data
                                               ↓
                                        core.options
                                               ↓
                                        core.pricing
                                               ↓
                                         core.alerts
                                               ↓
                                       core.strategies
                                               ↓
                    core.history  core.journal  core.positions_cache
                                               ↓
                                         core.backup
                                               ↓
                                         core.helpers
                                               ↓
                    ui.styles ui.checklist ui.alert_card ui.charts ui.backup
                                               ↓
                    tabs.calc tabs.board tabs.position ... tabs.alerts
                                               ↓
                                            app.py
```

**Правило:** импорт идёт **только сверху вниз**. Если появляется
`from core.pricing import ...` в `core/iss_api.py` — это цикл,
остановитесь и перенесите символ.

Исключение: ленивые импорты **внутри функции**, когда обратное
направление действительно нужно (например, `backup.py` импортирует
`_new_position_id` из `helpers.py` внутри цикла, чтобы избежать цикла
`backup ↔ helpers`).

---

## Поэтапный план

### ЭТАП 0 — подготовка (обязательно)

1. **Создайте ветку** `refactor/packages` — не работайте на `main`.
2. **Сделайте полный бэкап** `app.py` → `app_backup.py` (не коммитится).
3. **Соберите карту функций** — AST-парсинг:

   ```bash
   python - <<'PY'
   import ast
   tree = ast.parse(open("app.py", encoding="utf-8").read())
   for node in ast.walk(tree):
       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
           print(node.name)
   PY
   ```

   Сохраните список — это ваш чек-лист. Все имена должны появиться
   в пакетах к концу этапа 9.

4. **Проверьте, что приложение работает:** `streamlit run app.py`,
   пройдите 10 вкладок, убедитесь, что всё зелёное.

### ЭТАП 1 — `config.py` + `core/iss_api.py` + `core/instruments.py`

- `config.py` — все константы: `_IS_CLOUD`, `API_BASE_URL`, `ASSET_TYPE_MAP`,
  `CHECKLIST_RULES`, `DEFAULT_COMM_*`, `MOEX_INSTRUMENTS`, `HTML_PLACEHOLDER`,
  `_TAB_NAMES`, `ALERTS_SHEET_URL`.
- `core/iss_api.py` — `iss_get`, `iss_get_json`, `_make_iss_session`,
  `_is_failed_recently`, `_mark_failed`. Глобал `_FAILED_UNTIL` **живёт
  в том же файле**.
- `core/instruments.py` — `MOEX_INSTRUMENTS`, `resolve_canonical_asset_code`,
  `resolve_option_calc_code`, `_load_option_calc_assets`, `_asset_code_candidates`.
  Глобалы `_OPTION_CALC_ASSETS` и `_OPTION_CALC_ASSETS_TS` **живут
  в том же файле**, что и `_load_option_calc_assets` — иначе `global` не работает.

**Проверка:**

```bash
python -m py_compile config.py core/*.py
python -c "from config import CHECKLIST_RULES, _TAB_NAMES; print('OK')"
python -c "from core.instruments import resolve_option_calc_code; print(resolve_option_calc_code('SBER', 'Акция'))"
streamlit run app.py   # старый app.py ещё работает
```

### ЭТАП 2 — `core/market_data.py` + `core/options.py`

- `core/market_data.py` — комиссии, дивиденды, G-кривая, RVI, бары,
  последняя цена, фьючерсы, `_calc_comm_ui`, `classify_iv_vs_rvi`,
  `classify_strategy`, `format_series_label`, `_normalize_alerts_df`,
  `load_google_sheet_cached`, `apply_parity_delta`.
- `core/options.py` — `fetch_optionseries`, `fetch_all_series_for_asset`,
  `fetch_series_info`, `fetch_optionboard`, `fetch_volatility_graph`,
  `fetch_series_overview`, `_fetch_board_cached_30s`,
  `find_alert_levels`, `get_alert_assets_list`.

**Проверка:**

```bash
python -c "from core.market_data import calc_commission, fetch_rvi_current; print('OK')"
python -c "from core.options import fetch_optionboard, find_alert_levels; print('OK')"
streamlit run app.py
```

### ЭТАП 3 — `core/pricing.py` + `core/alerts.py`

- `core/pricing.py` — чистая математика: `compute_payoff`, `find_breakevens`,
  `_bs_price_np`, `_months_between`, `_portfolio_value`,
  `_portfolio_value_with_rvi`, `_get_current_market_price_for_asset`.
  **Никакого `st.session_state`** — только данные in/out.
- `core/alerts.py` — `build_alert_targets`, `_nearest_strike_above/below`,
  `_get_board_price_for`, `find_alert_full_info`, `_trend_emoji*`,
  `_get_recommendation`, `analyze_level_activity`, `_level_proximity`,
  `get_alert_checks`, `get_alert_recommendation_text`, `_weighted_avg_iv`.

**Проверка:**

```bash
python -c "
from core.pricing import compute_payoff
import numpy as np
pos = [{'Тип инструмента':'Опцион','Опцион':'Call','Страйк':100,
        'Кол-во':1,'Цена':5,'Эксп.':'2026-12-31','visible':True,
        '_vol':20,'_r':10,'_q':0}]
print(compute_payoff(pos, np.array([90, 100, 110]), anchor_expiry='2026-12-31'))
"
```

### ЭТАП 4 — `core/strategies.py`, `history.py`, `journal.py`, `positions_cache.py`, `backup.py`, `helpers.py`

- `strategies.py` — `_suggest_strategy`, `_suggestion_legs`,
  `_apply_suggested_strategy`, `_calc_range_strikes`, `_compute_r_q_for_leg`,
  `_migrate_positions_r_q`, `_pretrade_checklist_state`,
  `_validate_options_leg`, `_planned_close_date*`.
- `history.py` — `_store_iv_snapshot`, `_merge_history_records`,
  `_remember_volatility_history`.
- `journal.py` — `_journal_closed_sorted`, `_journal_equity_series`,
  `_journal_max_drawdown`, `_journal_profit_factor`, `_journal_streaks`,
  `_journal_stats_by`, `_journal_get_deposit`.
- `positions_cache.py` — `_cache_file`, `_load_cache`, `_save_cache`,
  `_positions_signature`, `_find_board_row`, `_find_board_row_by_secid`,
  `_strike_eq`, `_get_board_for_analytics`, `_fetch_board_nocache`.
  Глобал `_CACHE_FILE` **живёт в том же файле**.
- `backup.py` — `_BACKUP_SETTINGS`, `_backup_clean`, `_backup_number`,
  `_validate_*`, `_export_full_cache`, `_import_full_cache`.
- `helpers.py` — `_fmt_strike`, `_new_position_id`, `_color_call_put`,
  `_side_*`, `_greek_*_color`, `_safe_float_qp`, `_send_to_iframes`,
  `push_*_to_calculator`.

**Проверка:**

```bash
python -c "from core.strategies import _suggest_strategy; print(_suggest_strategy('1', None, '', '', '', None))"
python -c "from core.backup import _backup_clean; print(_backup_clean({'a': float('nan')}))"
```

### ЭТАП 5 — `ui/`

- `styles.py` — `inject_base_styles()`, `inject_responsive_styles()`
  **обёрнуты в функции**. Нельзя делать `st.markdown` на модульном уровне
  в `ui/styles.py` — будет ошибка «st.* before set_page_config».
- `checklist.py` — `_render_pretrade_checklist`.
- `alert_card.py` — `_render_alert_card`.
- `charts.py` — `render_exchange_chart`.
- `backup.py` — `_render_backup_controls`.

**Проверка:**

```bash
python -c "from ui.charts import render_exchange_chart; print('OK')"
python -c "from ui.alert_card import _render_alert_card; print('OK')"
```

### ЭТАП 6 — `tabs/calc.py`, `board.py`, `whatif.py`, `heatmap.py`, `ivrank.py`, `skew.py`

- Каждая вкладка — `@st.fragment`, без аргументов.
- Если функция > 500 строк — разбейте на две части в одном файле
  (например, `_render_position_tab` — часть 7a и 7b).

**Проверка после каждой:**

```bash
python -c "from tabs.calc import _render_calc_tab; print('OK')"
```

### ЭТАП 7 — `tabs/position.py` (самая большая, ~1500 строк)

Разбить на **две части в одном файле**:

- **7a:** импорты + начало `_render_position_tab` до формы добавления позиции.
- **7b:** форма, живое обновление, таблица/карточки, анализ, графики.

**Проверка:**

```bash
python -c "from tabs.position import _render_position_tab; print('OK')"
```

### ЭТАП 8 — `tabs/journal.py`, `kelly.py`, `alerts.py`

**Проверка:**

```bash
python -c "
import tabs.calc, tabs.board, tabs.position, tabs.whatif
import tabs.heatmap, tabs.ivrank, tabs.skew
import tabs.journal, tabs.kelly, tabs.alerts
print('ALL TABS OK')
"
```

### ЭТАП 9 — финальная сборка

**Новый `app.py` (~130 строк):**

```python
import streamlit as st

st.set_page_config(
    page_title="...",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ВАЖНО: config — до @st.cache_data(ttl=CHECKLIST_RULES[...])
import config  # noqa: F401
from config import _TAB_NAMES

from core.helpers import _safe_float_qp
from core.market_data import fetch_rvi_current
from core.positions_cache import _load_cache
from ui.styles import inject_base_styles, inject_responsive_styles
from ui.backup import _render_backup_controls

from tabs.calc import _render_calc_tab
# ... остальные вкладки

inject_base_styles()
inject_responsive_styles()
st.title("...")

# RVI header
_rvi = fetch_rvi_current()
# ...

# query params
st.session_state["_calc_level_buy"] = _safe_float_qp("level_buy", 0.0)
# ...

# session_state init
if "positions" not in st.session_state:
    st.session_state.positions = []
# ...

# Резервная копия — ДО диспетчера, вне @st.fragment
_render_backup_controls()

# Навигация
_active_tab = st.selectbox("Раздел", _TAB_NAMES, key="main_nav",
                            label_visibility="collapsed")

# Диспетчер
_RENDERERS = {...}
if _active_tab == "Доска опционов":
    _render_board_tab()
else:
    with st.container(key="responsive_content"):
        _RENDERERS[_active_tab]()
```

**Обновить `check_functions.py`** — сканирует весь проект
(см. диагностический скрипт ниже).

**Финальный прогон:**

```bash
python -m py_compile app.py config.py core/*.py ui/*.py tabs/*.py
python check_functions.py
streamlit run app.py
# пройти 10 вкладок + резервную копию
```

---

## Критические предупреждения

### 1. `st.set_page_config` — первым `st.*`-вызовом

Все `st.markdown`, `st.title` — **после** `set_page_config`. Обёртки
в `ui/styles.py` должны быть функциями, не модульным кодом.

### 2. `CHECKLIST_RULES` — до `@st.cache_data(ttl=...)`

Если в каком-то `core/*.py` есть
`@st.cache_data(ttl=CHECKLIST_RULES["cache_..."])`, то `config` **должен
быть импортирован первым**. В новом `app.py` это делается явной
строкой `import config` перед остальными импортами.

### 3. `global`-переменные — только в одном модуле

Если `_load_option_calc_assets()` использует
`global _OPTION_CALC_ASSETS`, то:
- `_OPTION_CALC_ASSETS` и `_OPTION_CALC_ASSETS_TS` объявлены в **том же файле**.
- Аналогично `_FAILED_UNTIL` — в `iss_api.py`, `_CACHE_FILE` — в `positions_cache.py`.

### 4. Дубли в `tabs/position.py` — `_fetch_board_cached_30s`

**Реальный случай из этого рефакторинга:** функция `_fetch_board_cached_30s`
логически живёт в `core/options.py` (там же, где `fetch_optionboard`),
но в импорт попала в блок `core.positions_cache`. Это вызывает
`ImportError` — Streamlit обрезает traceback, и кажется, что ошибка
в другом. Правило: **сверяйте, откуда реально импортируется каждая функция**.

### 5. `_render_backup_controls` — до диспетчера, вне `@st.fragment`

Если импорт JSON применить **после** рендера вкладки — текущая вкладка
не перерисуется. Функция должна быть вызвана один раз в `app.py`
до `_RENDERERS[...]()`.

### 6. `@st.cache_data` живёт между сессиями

Не очищайте `st.cache_data` при старте новой сессии — это чистит кэш
для всех пользователей. Уберите блок `if "cache_cleared_v7" not in
st.session_state: st.cache_data.clear()`.

---

## Карта типичных ошибок (из реального рефакторинга)

| Ошибка | Симптом | Причина | Решение |
|---|---|---|---|
| `NameError: _load_cache` | падает на старте | функция не определена до первого использования | проверить `check_functions.py` → MISSING |
| `NameError: push_strikes_to_calculator` | падает на Доске | потерян слой alert-хелперов | восстановить `core/helpers.py` |
| `NameError: find_alert_full_info` | падает на Позиции | потерян слой тренд-хелперов | восстановить `core/alerts.py` |
| `ImportError: cannot import name 'X'` | падает при импорте модуля | функция в другом пакете | проверить карту «функция → пакет» |
| `FragmentHandledException: ... NameError` | падает на одной вкладке | `@st.fragment` изолирует | пройти все вкладки, найти сломанную |
| `st.*` before `set_page_config` | падает на старте | стили/`st.markdown` в модульном коде | обернуть в функцию и вызывать после `set_page_config` |
| `st.cache_data` TTL: NameError | падает на старте | `CHECKLIST_RULES` объявлен ниже декоратора | `import config` первым |

---

## Диагностический скрипт `check_functions.py`

Сканирует все `.py` в проекте, проверяет:

- **MISSING** — имена в Load-контексте, но без определения/импорта/builtin.
- **DUPLICATES** — `def` с одним именем >1 раз в файле.
- **UNUSED** — функции без использований (Streamlit-колбэки сюда попадают ложно).

```python
"""
check_functions.py — проверка целостности проекта.

    python check_functions.py              # сканирует весь проект
    python check_functions.py app.py       # один файл
    python check_functions.py /path/to/    # конкретная папка
"""
from __future__ import annotations
import ast, sys, collections
from pathlib import Path

IGNORE_DIRS = {"__pycache__", ".git", ".venv", "venv", "env",
               ".idea", ".vscode", "node_modules", ".streamlit"}
IGNORE_FILES = {"check_functions.py", "setup.py"}

BUILTINS = {
    "print", "len", "range", "int", "float", "str", "bool", "dict", "list",
    "set", "tuple", "min", "max", "abs", "round", "sum", "any", "all",
    "enumerate", "zip", "sorted", "reversed", "iter", "next", "map", "filter",
    "type", "isinstance", "issubclass", "getattr", "setattr", "hasattr",
    "delattr", "callable", "id", "hash", "vars", "dir", "object",
    "super", "property", "staticmethod", "classmethod",
    "open", "repr", "format", "input", "eval", "exec", "compile",
    "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError",
    "NameError", "RuntimeError", "Exception", "BaseException",
    "ImportError", "OSError", "UnicodeError",
    "True", "False", "None", "NotImplemented", "Ellipsis",
    "__file__", "__name__", "__doc__", "__builtins__",
    "__spec__", "__loader__", "__package__",
}

def parse_file(path: Path) -> dict:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    defined, used, assigned, called = set(), set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        if isinstance(node, ast.Name):
            (used if isinstance(node.ctx, ast.Load) else assigned).add(node.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)

    imported, has_star = set(), False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*": has_star = True; continue
                imported.add(a.asname or a.name)

    params = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in (*node.args.args, *node.args.kwonlyargs,
                        *getattr(node.args, "posonlyargs", [])):
                params.add(arg.arg)
            if node.args.vararg: params.add(node.args.vararg.arg)
            if node.args.kwarg: params.add(node.args.kwarg.arg)

    class_methods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    class_methods.add(item.name)

    counts = collections.Counter(
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    return dict(defined=defined, imported=imported, used=used, called=called,
                assigned=assigned, params=params, class_methods=class_methods,
                defs_count=counts, has_star=has_star)

def find_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(p for p in root.rglob("*.py")
                  if not any(part in IGNORE_DIRS for part in p.parts)
                  and p.name not in IGNORE_FILES)

def analyze(root: Path) -> dict:
    files = find_files(root)
    parsed = {}
    for f in files:
        try:
            parsed[f] = parse_file(f)
        except SyntaxError as e:
            return {"error": f"{f}:{e.lineno}: {e.msg}", "file": f}

    project_defined = set()
    for info in parsed.values():
        project_defined |= info["defined"]

    missing, dups = {}, {}
    for f, info in parsed.items():
        if info["has_star"]:
            continue
        miss = (info["used"] - info["defined"] - info["imported"]
                - info["assigned"] - info["params"] - BUILTINS)
        if miss:
            missing[f] = miss
        d = {n: c for n, c in info["defs_count"].items() if c > 1}
        if d:
            dups[f] = d

    all_used, all_methods = set(), set()
    for info in parsed.values():
        all_used |= info["used"]
        all_methods |= info["class_methods"]
    unused = sorted(project_defined - all_used - all_methods)

    return dict(files=files, missing=missing, dups=dups, unused=unused,
                defined=project_defined)

def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    if not root.exists():
        print(f"Путь не найден: {root}"); return 2
    result = analyze(root)
    if "error" in result:
        print(f"ОШИБКА: {result['error']}"); return 2

    print(f"Файлов: {len(result['files'])}  |  функций: {len(result['defined'])}\n")

    print("MISSING:")
    if not result["missing"]:
        print("   OK")
    else:
        for f, names in sorted(result["missing"].items()):
            print(f"   {f}:")
            for n in sorted(names):
                print(f"      {n}")

    print("\nDUPLICATES:")
    if not result["dups"]:
        print("   OK")
    else:
        for f, d in sorted(result["dups"].items()):
            print(f"   {f}:")
            for n, c in sorted(d.items()):
                print(f"      {n} — {c} раз")

    print("\nUNUSED (возможно, колбэки / dead code):")
    if not result["unused"]:
        print("   OK")
    else:
        for n in result["unused"]:
            print(f"   {n}")

    ok = not result["missing"] and not result["dups"]
    print("\nИТОГ: " + ("✅ проблем нет" if ok else "❌ есть проблемы"))
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
```

**Ограничения:** проверяет только AST, не выполняет импорты. `ImportError`
(когда имя есть в `from x import y`, но самого `y` в `x` нет) не ловится
этим скриптом — только `python -c "import app"` или `streamlit run`.

---

## Финальный чек-лист после рефакторинга

```bash
# 1. Синтаксис всех файлов
python -m py_compile app.py config.py
find core ui tabs -name "*.py" -exec python -m py_compile {} +

# 2. Реальные импорты (ловит ImportError, которые AST не видит)
python -c "import app; print('APP OK')"

# 3. Целостность имён
python check_functions.py

# 4. Ручной прогон в браузере:
#    Калькулятор → Доска опционов → Позиция → What-if → Heatmap IV
#    → IV Rank → Skew → Журнал → Келли → Оповещения
#    Резервная копия · JSON → 1. Подготовить → 2. Скачать → загрузить обратно

# 5. Только если всё зелёное — merge в main
```

## Что осталось нетронутым (сохранено «как было»)

- Все `st.session_state`-ключи.
- Все `st.cache_data` — TTL, `show_spinner=False`.
- Все `@st.fragment` — включая `run_every="30s"` на `_render_alerts_live`.
- Все `key=`-ы виджетов (важно для `st.rerun` и сброса состояния).
- CSS в `ui/styles.py` — побайтово те же стили.
- Формулы, логика расчётов, порядок проверок.

**Рефакторинг — только физическое перемещение кода**, без логических
изменений. Если после рефакторинга приложение ведёт себя иначе —
это баг переноса, откатывайте шаг.

## Профилактика в будущем

1. **`check_functions.py`** — в pre-commit hook или CI:
   ```bash
   python check_functions.py || exit 1
   ```
2. **`python -c "import app"`** — в CI перед `streamlit run`.
3. **Один PR = один этап** рефакторинга. Не смешивайте «переехали 3 пакета»
   и «добавили новую фичу».
4. **Перед каждым крупным патчем** — `grep -c '^def ' app.py` до/после
   (если монолит ещё не разбит) или `python check_functions.py`
   (если разбит).
