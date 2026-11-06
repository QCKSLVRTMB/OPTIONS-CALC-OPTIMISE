# SKILL: Поддержка пакетированного Streamlit-проекта `options-calc-optimise`

## Назначение

Руководство для **текущего** состояния проекта: `app.py` (~130 строк) +
пакеты `core/`, `ui/`, `tabs/` + `config.py`. Здесь описано:

- как устроен проект и куда что класть;
- как быстро найти причину `NameError` / `ImportError`;
- какие места в коде выглядят как баги, но ими не являются;
- как проверять целостность перед каждым коммитом.

**Историческая справка** (как проект собирался из монолита) — в конце файла,
раздел «Рефакторинг: история». Для ежедневной работы он не нужен.

---

## Структура проекта

```
options-calc-optimise/
├── app.py                    # тонкий вход (~130 строк)
├── config.py                 # все константы
├── requirements.txt
├── index.html                # iframe-калькулятор
├── alerts_print.html         # печать оповещений A4
├── SKILL.md                  # ← этот файл
├── check_functions.py        # авто-проверка
├── positions_cache.json      # файловый кэш позиций (не в git)
├── core/                     # бизнес-логика
│   ├── __init__.py
│   ├── iss_api.py            # HTTP-клиент MOEX ISS
│   ├── instruments.py        # MOEX_INSTRUMENTS + резолверы
│   ├── market_data.py        # цены, бары, комиссии, RVI, G-кривая, дивиденды
│   ├── options.py            # серии, доски, IV, кэш доски
│   ├── pricing.py            # BS, payoff, portfolio value
│   ├── alerts.py             # уровни, чек-боксы, targets
│   ├── strategies.py         # рекомендации, legs, чек-лист
│   ├── history.py            # IV/HV/RVI история
│   ├── journal.py            # статистика журнала
│   ├── positions_cache.py    # файловый кэш
│   ├── backup.py             # экспорт/импорт JSON
│   └── helpers.py            # fmt, id, postMessage
├── ui/
│   ├── __init__.py
│   ├── styles.py             # CSS (2 блока, обёрнуты в функции)
│   ├── checklist.py
│   ├── alert_card.py
│   ├── charts.py             # render_exchange_chart
│   └── backup.py             # _render_backup_controls
└── tabs/
    ├── __init__.py
    ├── calc.py               # Калькулятор
    ├── board.py              # Доска опционов
    ├── position.py           # Позиция (~1500 строк)
    ├── whatif.py             # What-if
    ├── heatmap.py            # Heatmap IV
    ├── ivrank.py             # IV Rank
    ├── skew.py               # Skew
    ├── journal.py            # Журнал
    ├── kelly.py              # Келли
    └── alerts.py             # Оповещения
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

**Правило:** импорт идёт только сверху вниз. Если появляется
`from core.pricing import ...` в `core/iss_api.py` — это цикл.
Исключение: ленивые импорты внутри функций, когда обратное
направление действительно нужно (например, `backup.py` импортирует
`_new_position_id` из `helpers.py` внутри функции).

## Карта «вкладка → модули»

| Вкладка | `tabs/` файл | Ключевые `core/` модули |
|---|---|---|
| Калькулятор | `calc.py` | `options`, `alerts`, `strategies`, `pricing`, `market_data`, `helpers` |
| Доска опционов | `board.py` | `options`, `alerts`, `strategies`, `pricing`, `market_data`, `helpers` |
| Позиция | `position.py` | все `core/*` + `ui/checklist`, `ui/charts` |
| What-if | `whatif.py` | `pricing`, `market_data` |
| Heatmap IV | `heatmap.py` | `options`, `pricing`, `market_data`, `helpers` |
| IV Rank | `ivrank.py` | `options`, `alerts`, `history`, `positions_cache`, `market_data` |
| Skew | `skew.py` | `options`, `pricing`, `positions_cache`, `market_data`, `helpers` |
| Журнал | `journal.py` | `journal`, `helpers` |
| Келли | `kelly.py` | `journal`, `market_data` |
| Оповещения | `alerts.py` | `alerts`, `market_data`, `ui/alert_card` |

## Карта «функция → пакет» (важные)

Полный список — в `check_functions.py` (UNUSED покажет всё, что есть).
Здесь — самые «подвижные»:

| Функция | Пакет | Модуль |
|---|---|---|
| `fetch_optionboard`, `_fetch_optionboard_raw`, `fetch_central_strike` | `core` | `options.py` |
| **`_fetch_board_cached_30s`** | **`core`** | **`options.py`** ← важно! |
| `_fetch_board_nocache` | `core` | `positions_cache.py` |
| `_cache_file`, `_load_cache`, `_save_cache`, `_positions_signature` | `core` | `positions_cache.py` |
| `_find_board_row`, `_find_board_row_by_secid`, `_strike_eq` | `core` | `positions_cache.py` |
| `_get_board_for_analytics` | `core` | `positions_cache.py` |
| `compute_payoff`, `find_breakevens`, `_bs_price_np`, `_portfolio_value*` | `core` | `pricing.py` |
| `_get_current_market_price_for_asset` | `core` | `pricing.py` |
| `_suggest_strategy`, `_suggestion_legs`, `_apply_suggested_strategy` | `core` | `strategies.py` |
| `_calc_range_strikes`, `_compute_r_q_for_leg`, `_migrate_positions_r_q` | `core` | `strategies.py` |
| `_pretrade_checklist_state`, `_validate_options_leg`, `_planned_close_*` | `core` | `strategies.py` |
| `build_alert_targets`, `find_alert_levels`, `find_alert_full_info` | `core` | `alerts.py` |
| `get_alert_checks`, `get_alert_recommendation_text`, `analyze_level_activity` | `core` | `alerts.py` |
| `_weighted_avg_iv`, `_level_proximity`, `_trend_emoji*` | `core` | `alerts.py` |
| `_backup_clean`, `_backup_number`, `_export_full_cache`, `_import_full_cache` | `core` | `backup.py` |
| `_store_iv_snapshot`, `_merge_history_records`, `_remember_volatility_history` | `core` | `history.py` |
| `_journal_*` (7 функций) | `core` | `journal.py` |
| `_fmt_strike`, `_new_position_id`, `_side_*`, `_color_call_put`, `_greek_*_color` | `core` | `helpers.py` |
| `_safe_float_qp`, `_send_to_iframes`, `push_*_to_calculator` | `core` | `helpers.py` |
| `render_exchange_chart` | `ui` | `charts.py` |
| `_render_alert_card` | `ui` | `alert_card.py` |
| `_render_pretrade_checklist` | `ui` | `checklist.py` |
| `_render_backup_controls` | `ui` | `backup.py` |
| `inject_base_styles`, `inject_responsive_styles` | `ui` | `styles.py` |

## Правила архитектуры

**Кладите в `core/`, если:**
- функция не рисует виджеты Streamlit (`st.button`, `st.dataframe`, `st.markdown`);
- функция работает с числами / списками / DataFrame / ISS;
- функция использует `st.session_state` и `@st.cache_data` — это допустимо.

**Кладите в `ui/`, если:**
- функция содержит `st.markdown`, `st.container`, `st.radio` и т.п.;
- функция переиспользуется между несколькими вкладками.

**Кладите в `tabs/`, если:**
- функция — это `@st.fragment` для конкретной вкладки;
- функция содержит уникальную для одной вкладки логику.

**Никогда не кладите в `core/`:**
- `st.title`, `st.header`, `st.dataframe`, `st.columns` — это UI;
- `st.set_page_config` — только в `app.py`.

## Известные особенности (не баги)

1. **`resolve_tv_ticker`** в `core/instruments.py` ссылается на
   `TV_TICKER_MAP`, которого нет. Функция не вызывается — runtime-ошибки
   нет. При необходимости — добавьте `TV_TICKER_MAP` или удалите функцию.

2. **`st.cache_data(ttl=CHECKLIST_RULES["..."])`** в `core/*.py`
   вычисляет TTL **при импорте**. Поэтому `config.py` обязан
   импортироваться первым. В `app.py` это сделано через
   `import config` перед остальными импортами.

3. **`_render_backup_controls()` вызывается один раз** в `app.py` до
   диспетчера, вне `@st.fragment`. Не переносите в `tabs/position.py` —
   импорт JSON должен применяться до создания виджетов вкладки.

4. **`_render_alerts_live`** — вложенный `@st.fragment(run_every="30s")`
   внутри `_render_alerts_tab`. Если убрать `run_every` — пропадёт
   автообновление карточек раз в 30 секунд.

5. **`rangebreaks` в `ui/charts.py`** — склейка сессий через
   `dict(bounds=["sat", "mon"])` (D1/H1) и `dict(bounds=[23, 7],
   pattern="hour")` (только H1). Данные в `df` **не фильтруются** —
   Plotly скрывает эти участки на оси X. Если понадобится вернуть
   субботы/воскресенья для фьючерсов FORTS — уберите первую строку
   из `_rangebreaks`.

6. **`form_use_auto_price`** в `tabs/position.py` инициализируется
   как `value=False`. Пользователь может поставить галочку, и она
   сохранится до конца сессии. Чтобы форма была всегда пустой —
   добавьте `st.session_state.pop("form_use_auto_price", None)` перед
   `st.rerun()` после успешного добавления позиции.

7. **`_show_spinner=False`** у всех `@st.cache_data` — сохранено из
   монолита. Не включайте обратно, это замедлит UI.

## Типичные ошибки

| Ошибка | Симптом | Причина | Решение |
|---|---|---|---|
| `NameError: X` | падает на вкладке | функции нет ни в одном файле | `python check_functions.py` → MISSING |
| **`ImportError: cannot import name 'X' from 'core.Y'`** | падает при импорте `app.py` | функция в **другом** `core/`-модуле | проверить карту «функция → пакет» (см. выше) |
| `FragmentHandledException` | падает на одной вкладке | `@st.fragment` изолирует ошибку | пройти все 10 вкладок |
| `st.*` before `set_page_config` | падает на старте | `st.markdown` в модульном коде `ui/styles.py` | обернуть в функцию |
| `st.cache_data` TTL: NameError | падает на старте | `CHECKLIST_RULES` объявлен ниже декоратора | `import config` первым в `app.py` |
| `DuplicateWidgetID` | падает на вкладке | `key=` у двух виджетов одинаковый | сделать ключи уникальными (по `_id`, `_idx`) |

**Реальный кейс из этого проекта** (ошибка была в `tabs/position.py`):

```python
# ❌ НЕПРАВИЛЬНО — _fetch_board_cached_30s живёт в core.options
from core.positions_cache import (
    _cache_file, _load_cache, _save_cache,
    _positions_signature,
    _fetch_board_cached_30s,  # ← ImportError
    _find_board_row,
)

# ✅ ПРАВИЛЬНО
from core.options import (
    fetch_optionseries, fetch_all_series_for_asset,
    fetch_optionboard,
    _fetch_board_cached_30s,
)
from core.positions_cache import (
    _cache_file, _load_cache, _save_cache,
    _positions_signature,
    _find_board_row,
)
```

AST-проверка это **не ловит** (в файле имя импортировано, ошибка
возникает при выполнении `import`). Ловится только режимом
`check_functions.py --imports` или `python -c "import app"`.

## Диагностика — `check_functions.py`

```bash
# 1. Быстро: только AST (MISSING / DUPLICATES / UNUSED)
python check_functions.py

# 2. Полно: AST + реальные импорты (ловит ImportError)
python check_functions.py --imports

# 3. Один файл
python check_functions.py tabs/position.py

# 4. В CI / pre-commit
python check_functions.py --imports || exit 1
```

**Ожидаемый вывод `--imports` для рабочего проекта:**

```
Файлов просканировано: 26
Функций/классов в проекте: ~130

MISSING: OK
DUPLICATES: OK
UNUSED: OK

IMPORTS (реальный importlib):
   OK
   Пропущено (нужен streamlit runtime):
      app.py

ИТОГ: ✅ проблем не найдено
```

**`app.py` пропускается в `--imports`**, потому что вызывает
`st.set_page_config` на верхнем уровне — без Streamlit runtime это
упадёт. Для полной проверки `app.py` используйте:

```bash
python -c "import app; print('APP OK')"
# или
streamlit run app.py
```

## Финальная проверка перед коммитом

```bash
# 1. Синтаксис
python -m py_compile app.py config.py
find core ui tabs -name "*.py" -exec python -m py_compile {} +

# 2. Реальные импорты (ловит ImportError)
python -c "import app; print('APP OK')"

# 3. Целостность + импорты
python check_functions.py --imports

# 4. Ручной прогон в браузере:
#    Калькулятор → Доска опционов → Позиция → What-if → Heatmap IV
#    → IV Rank → Skew → Журнал → Келли → Оповещения
#    Резервная копия · JSON → 1. Подготовить → 2. Скачать → загрузить обратно
```

## Что важно НЕ ломать при правках

- **`key=`** у виджетов — от них зависит `st.rerun` и сброс состояния.
- **`st.session_state`** ключи: `positions`, `positions_cache`,
  `_pos_active_key`, `_pos_signature`, `_g3_migrated`, `pos_view_mode`,
  `journal_*`, `_alert_checks`, `_iv_history`, `_hv_history`,
  `_rvi_history`, `_wt_last_asset`, `_current_market_price`.
- **`@st.cache_data`** TTL — не менять без причины.
- **`@st.fragment`** — особенно `run_every="30s"` в `_render_alerts_live`.
- **`rangebreaks`** в `ui/charts.py` — если меняете, проверяйте D1 и H1 отдельно.
- **`_render_backup_controls()`** — вызывается **до** `_RENDERERS[...]()` в `app.py`.

---

## Рефакторинг: история (для справки)

Проект собран из монолита ~6000 строк за 9 этапов:

| # | Что |
|---|---|
| 0 | Бэкап `app.py`, ветка `refactor/packages`, карта функций через AST |
| 1 | `config.py`, `core/iss_api.py`, `core/instruments.py` |
| 2 | `core/market_data.py`, `core/options.py` |
| 3 | `core/pricing.py`, `core/alerts.py` |
| 4 | `core/strategies.py`, `history.py`, `journal.py`, `positions_cache.py`, `backup.py`, `helpers.py` |
| 5 | `ui/styles.py`, `checklist.py`, `alert_card.py`, `charts.py`, `backup.py` |
| 6 | `tabs/calc.py`, `board.py`, `whatif.py`, `heatmap.py`, `ivrank.py`, `skew.py` |
| 7 | `tabs/position.py` (в 2 частях — 7a и 7b) |
| 8 | `tabs/journal.py`, `kelly.py`, `alerts.py` |
| 9 | Новый `app.py` + обновлённый `check_functions.py` |

**Ключевые принципы рефакторинга (на будущее, если придётся делать снова):**

1. **Монолит работает до последнего этапа.** После каждого шага —
   `streamlit run app.py`, пройти 10 вкладок.
2. **Один PR = один этап.** Не смешивать «переехали 3 пакета» и «добавили фичу».
3. **`config.py` первым.** Все `@st.cache_data(ttl=CHECKLIST_RULES[...])`
   читают TTL при импорте.
4. **`global`-переменные — только в одном модуле.** `_OPTION_CALC_ASSETS`
   в `instruments.py`, `_FAILED_UNTIL` в `iss_api.py`, `_CACHE_FILE`
   в `positions_cache.py`.
5. **Стили — в функциях, не в модульном коде.** `inject_base_styles()`,
   `inject_responsive_styles()` вызываются из `app.py` после `set_page_config`.
6. **После каждого этапа `python check_functions.py --imports`.**
   Это ловит ImportError ещё до `streamlit run`.

**Результат:**

| До | После |
|---|---|
| `app.py` ~6000 строк | 26 файлов, самый большой `tabs/position.py` ~1500 строк |
| Найти ошибку = прочитать пол-файла | Найти ошибку = зайти в `tabs/<вкладка>.py` |
| Изменение в стратегии = правка в 3 местах | Изменение = одна функция в `core/strategies.py` |
| `NameError` обнаруживается только в браузере | `check_functions.py --imports` ловит до запуска |

**Функциональность сохранена на 100%.** Все `st.session_state`-ключи,
все `st.cache_data` (TTL, `show_spinner=False`), все `@st.fragment`
(`run_every="30s"`), все `key=`-ы виджетов, весь CSS — идентичны.
Рефакторинг — только физическое перемещение кода, без логических
изменений.
