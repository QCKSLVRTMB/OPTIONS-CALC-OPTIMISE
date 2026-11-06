"""Экспорт/импорт полного состояния приложения в JSON."""
import json
import math
from datetime import date, datetime

import numpy as np
import pandas as pd
import streamlit as st

from config import ASSET_TYPE_MAP
from core.market_data import _normalize_alerts_df
from core.positions_cache import _save_cache


# =====================================================================
# Схема настроек
# =====================================================================
_BACKUP_SETTINGS = {
    "deposit": ("dep_inp", 100000.0, 0.0, None),
    "risk_pct": ("rp_inp", 1.0, 0.1, 100.0),
    "comm_options_pct": ("cop_inp", 3.0, 0.0, 20.0),
    "comm_options_min": ("cmo_inp", 0.02, 0.0, 100.0),
    "comm_futures_pct": ("cfp_inp", 0.1, 0.0, 5.0),
    "comm_stocks_pct": ("csp_inp", 0.3, 0.0, 5.0),
}


# =====================================================================
# Сериализация значений в JSON-совместимый вид
# =====================================================================
def _backup_clean(value):
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        return _backup_clean(value.item())
    if isinstance(value, np.ndarray):
        return _backup_clean(value.tolist())
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, dict):
        return {str(k): _backup_clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_backup_clean(v) for v in value]
    raise TypeError(
        f"Неподдерживаемый тип данных в резервной копии: "
        f"{type(value).__name__}"
    )


def _backup_number(value, name, minimum=None, maximum=None):
    if isinstance(value, bool):
        raise ValueError(f"{name}: ожидалось число, а не bool")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: неверное числовое значение") from None
    if not math.isfinite(number):
        raise ValueError(f"{name}: число должно быть конечным")
    if minimum is not None and number < minimum:
        raise ValueError(f"{name}: значение меньше {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name}: значение больше {maximum}")
    return number


# =====================================================================
# Валидаторы
# =====================================================================
def _validate_positions_cache(raw):
    if not isinstance(raw, dict):
        raise ValueError("positions_cache должен быть JSON-объектом")
    result = {}
    for key, rows in raw.items():
        if not isinstance(key, str) or "|" not in key:
            raise ValueError(f"Неверная привязка позиций к активу: {key!r}")
        asset, category = key.split("|", 1)
        if not asset.strip() or category not in ASSET_TYPE_MAP:
            raise ValueError(f"Неверный ключ актива: {key}")
        if not isinstance(rows, list):
            raise ValueError(f"{key}: ожидался список позиций")

        checked_rows = []
        seen_ids = set()
        for index, source in enumerate(rows):
            if not isinstance(source, dict):
                raise ValueError(f"{key}, строка {index + 1}: ожидался объект")
            row = _backup_clean(source)

            qty = _backup_number(row.get("Кол-во", 0),
                                  f"{key}: количество", -10000, 10000)
            if not qty.is_integer():
                raise ValueError(f"{key}: количество должно быть целым")
            row["Кол-во"] = int(qty)
            row["Цена"] = _backup_number(row.get("Цена", 0),
                                          f"{key}: цена", 0.0)

            numeric_fields = (
                "Страйк", "Теор.цена",
                "Дельта", "Гамма", "Вега", "Тета", "Ро",
                "_vol", "_r", "_q",
            )
            for field in numeric_fields:
                if row.get(field) is not None:
                    row[field] = _backup_number(row[field],
                                                 f"{key}: {field}")

            if not row.get("_asset"):
                row["_asset"] = asset
            if not row.get("_atype"):
                row["_atype"] = category

            # Импорт из helpers, чтобы избежать цикла
            from core.helpers import _new_position_id
            row["_id"] = str(row.get("_id") or _new_position_id())
            if row["_id"] in seen_ids:
                raise ValueError(f"{key}: повторяющийся ID позиции")
            seen_ids.add(row["_id"])
            checked_rows.append(row)

        result[key] = checked_rows
    return result


def _validate_iv_history(raw):
    if not isinstance(raw, dict):
        raise ValueError("iv_history должен быть JSON-объектом")
    result = {}
    for key, bucket in raw.items():
        if not isinstance(bucket, list):
            raise ValueError(f"История IV {key}: ожидался список")
        points = {}
        for point in bucket:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError(f"История IV {key}: неверная точка")
            day = date.fromisoformat(str(point[0])).isoformat()
            iv = _backup_number(point[1], f"История IV {key}", 0.0)
            points[day] = iv
        result[key] = [[day, points[day]] for day in sorted(points)]
    return result


def _validate_history_records(raw, fields):
    if not isinstance(raw, list):
        raise ValueError("Исторические данные должны быть списком")
    result = {}
    for source in raw:
        if not isinstance(source, dict):
            raise ValueError("Неверная запись исторических данных")
        day = date.fromisoformat(str(source.get("date", ""))).isoformat()
        row = {"date": day}
        for field in fields:
            value = source.get(field)
            row[field] = (
                None if value is None
                else _backup_number(value, f"{day}: {field}", 0.0)
            )
        result[day] = row
    return [result[day] for day in sorted(result)]


# =====================================================================
# Экспорт
# =====================================================================
def _export_full_cache():
    """Экспорт без сетевых запросов; активная позиция имеет приоритет."""
    cache = _backup_clean(st.session_state.get("positions_cache", {}))
    current = _backup_clean(st.session_state.get("positions", []))
    active_key = st.session_state.get("_pos_active_key")

    if current and not active_key:
        asset = st.session_state.get("pos_asset", "")
        category = st.session_state.get("pos_category", "")
        if not asset or category not in ASSET_TYPE_MAP:
            raise ValueError("Не определён базовый актив текущей позиции")
        active_key = f"{asset}|{category}"

    if active_key:
        cache[active_key] = current

    alerts_df = st.session_state.get("alerts_df")
    alerts_snapshot = (
        alerts_df.to_dict(orient="records")
        if isinstance(alerts_df, pd.DataFrame) and not alerts_df.empty
        else []
    )

    payload = {
        "_meta": {
            "version": 3,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "app": "MOEX Options & Black-Scholes",
        },
        "positions_cache": cache,
        "positions_current": current,
        "positions_active_key": active_key,
        "journal_rows": st.session_state.get("journal_rows", []),
        "iv_history": st.session_state.get("_iv_history", {}),
        "hv_history": st.session_state.get("_hv_history", {}),
        "rvi_history": st.session_state.get("_rvi_history", []),
        "alert_checks": st.session_state.get("_alert_checks", {}),
        "alerts_snapshot": alerts_snapshot,
        "journal_deposit": {
            "initial": st.session_state.get("journal_initial_deposit",
                                             100000.0),
            "current_manual": st.session_state.get(
                "journal_current_deposit_manual"
            ),
            "mode": st.session_state.get("journal_deposit_mode", "auto"),
        },
        "settings": {
            name: st.session_state.get(spec[0], spec[1])
            for name, spec in _BACKUP_SETTINGS.items()
        },
    }

    return json.dumps(
        _backup_clean(payload),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )


# =====================================================================
# Импорт
# =====================================================================
def _import_full_cache(json_str):
    try:
        data = _backup_clean(json.loads(json_str))

        if not isinstance(data, dict):
            raise ValueError("Ожидался JSON-объект")

        # Старый формат: {"SBER|Акция": [...], ...}
        if "_meta" not in data:
            cache = _validate_positions_cache(data)
            data = {
                "_meta": {"version": 1},
                "positions_cache": cache,
            }

        meta = data.get("_meta")
        if not isinstance(meta, dict):
            raise ValueError("Неверный блок _meta")

        version = meta.get("version")
        if version not in (1, 2, 3):
            raise ValueError(f"Неизвестная версия резервной копии: {version}")

        updates = {}
        loaded = []

        if "positions_cache" in data or "positions_current" in data:
            cache = _validate_positions_cache(
                data.get("positions_cache",
                         st.session_state.get("positions_cache", {}))
            )
            active_key = data.get("positions_active_key")

            if active_key is not None:
                current = data.get("positions_current",
                                    cache.get(active_key, []))
                checked = _validate_positions_cache({active_key: current})
                cache[active_key] = checked[active_key]
            elif data.get("positions_current"):
                raise ValueError(
                    "У текущих позиций отсутствует привязка к базовому активу"
                )
            else:
                previous_key = st.session_state.get("_pos_active_key")
                active_key = (
                    previous_key if previous_key in cache
                    else next(
                        (key for key, rows in cache.items() if rows),
                        next(iter(cache), None),
                    )
                )

            updates["positions_cache"] = cache
            updates["positions"] = _backup_clean(cache.get(active_key, []))
            updates["_pos_active_key"] = active_key

            if active_key:
                asset, category = active_key.split("|", 1)
                updates.update({
                    "pos_asset": asset,
                    "pos_category": category,
                    "pos_asset_input": asset,
                    "pos_category_select": category,
                })

            updates.update({
                "pos_series_list": [],
                "pos_series_code": "",
                "pos_expiry": "",
                "pos_autoloaded_for": (None, None),
            })
            loaded.append("позиции по активам")

        if "journal_rows" in data:
            rows = data["journal_rows"]
            if not isinstance(rows, list) or any(
                not isinstance(row, dict) for row in rows
            ):
                raise ValueError("Неверный формат журнала")
            updates["journal_rows"] = rows
            loaded.append("журнал")

        if "iv_history" in data:
            updates["_iv_history"] = _validate_iv_history(data["iv_history"])
            loaded.append("история IV")

        if "hv_history" in data:
            raw = data["hv_history"]
            if not isinstance(raw, dict):
                raise ValueError("hv_history должен быть JSON-объектом")
            updates["_hv_history"] = {
                key: _validate_history_records(
                    rows, ("hv10", "hv20", "hv50", "hv100")
                )
                for key, rows in raw.items()
            }
            loaded.append("история HV")

        if "rvi_history" in data:
            updates["_rvi_history"] = _validate_history_records(
                data["rvi_history"], ("value",)
            )
            loaded.append("история RVI")

        if "alert_checks" in data:
            checks = data["alert_checks"]
            if not isinstance(checks, dict):
                raise ValueError("Неверный формат выбора оповещений")
            for key, row in checks.items():
                if not isinstance(row, dict):
                    raise ValueError(f"Неверное оповещение: {key}")
                if row.get("buy") not in (None, "1", "2"):
                    raise ValueError(f"Неверный выбор покупки: {key}")
                if row.get("sell") not in (None, "3", "4"):
                    raise ValueError(f"Неверный выбор продажи: {key}")
                for field in ("_lvl_buy", "_lvl_sell"):
                    if row.get(field) is not None:
                        row[field] = _backup_number(
                            row[field], f"{key}: {field}"
                        )
            updates["_alert_checks"] = checks
            loaded.append("выбор оповещений")

        if "alerts_snapshot" in data:
            records = data["alerts_snapshot"]
            if not isinstance(records, list):
                raise ValueError("Неверный снимок таблицы оповещений")
            if records:
                frame, error = _normalize_alerts_df(pd.DataFrame(records))
                if error:
                    raise ValueError(error)
                updates.update({
                    "alerts_df": frame,
                    "alerts_source": "JSON (снимок)",
                    "alerts_loaded_at": datetime.now().strftime("%H:%M:%S"),
                    "alerts_error": None,
                })
                loaded.append("уровни оповещений")

        settings = data.get("settings", {})
        if not isinstance(settings, dict):
            raise ValueError("Неверный формат настроек")
        for name, (state_key, default, low, high) in _BACKUP_SETTINGS.items():
            if name in settings:
                updates[state_key] = _backup_number(
                    settings[name], name, low, high
                )

        deposit = data.get("journal_deposit", {})
        if not isinstance(deposit, dict):
            raise ValueError("Неверный формат депозита журнала")
        if "initial" in deposit:
            updates["journal_initial_deposit"] = _backup_number(
                deposit["initial"], "Начальный депозит журнала", 0.0
            )
        if "current_manual" in deposit:
            value = deposit["current_manual"]
            updates["journal_current_deposit_manual"] = (
                None if value is None
                else _backup_number(value, "Текущий депозит журнала", 0.0)
            )
        if "mode" in deposit:
            if deposit["mode"] not in ("auto", "manual"):
                raise ValueError("Неверный режим депозита")
            updates["journal_deposit_mode"] = deposit["mode"]

        # Все проверки пройдены. Применяем.
        reset_prefixes = (
            "_alert_buy_check_", "_alert_sell_check_",
            "qty_", "price_", "qp_", "qm_", "pp_", "pm_",
            "form_", "journal_edit_",
            "_calc_html_", "_hm_cache_",
        )
        reset_keys = {
            "fallback_series_selector",
            "journal_initial_deposit_inp",
            "journal_current_deposit_inp",
            "journal_deposit_mode_select",
            "journal_edit_sel",
            "journal_del_sel",
            "_prepared_backup",
            "_prepared_backup_at",
        }
        for key in list(st.session_state):
            if isinstance(key, str) and (
                key.startswith(reset_prefixes) or key in reset_keys
            ):
                st.session_state.pop(key, None)

        st.session_state.update(updates)
        st.session_state.update({
            "_alert_levels_cache": {},
            "_g3_migrated": False,
            "_pos_signature": None,
            "_wt_last_asset": None,
            "_current_market_price": None,
            "_full_cache_loaded_at": datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        })

        if "positions_cache" in updates:
            _save_cache(updates["positions_cache"])

        return True, "Восстановлено: " + ", ".join(loaded or ["настройки"])

    except (ValueError, TypeError, KeyError) as error:
        return False, f"Не удалось восстановить копию: {error}"