"""Файловый кэш позиций + вспомогательные функции поиска на доске."""
import hashlib
import json
import tempfile
from pathlib import Path

import streamlit as st

from config import API_BASE_URL, ASSET_TYPE_MAP, _IS_CLOUD
from core.iss_api import iss_get_json
from core.instruments import _asset_code_candidates


# =====================================================================
# Файловый кэш
# =====================================================================
def _get_cache_file_path():
    candidates = []
    try:
        candidates.append(Path(__file__).resolve().parent / "positions_cache.json")
    except Exception:
        pass
    try:
        candidates.append(Path.cwd() / "positions_cache.json")
    except Exception:
        pass
    try:
        candidates.append(Path(tempfile.gettempdir()) / "positions_cache.json")
    except Exception:
        pass

    for _p in candidates:
        try:
            _p.parent.mkdir(parents=True, exist_ok=True)
            _test = _p.parent / ".write_test"
            _test.write_text("ok", encoding="utf-8")
            _test.unlink()
            return _p
        except Exception:
            continue
    return None


_CACHE_FILE = None


def _cache_file():
    global _CACHE_FILE
    if _CACHE_FILE is None:
        _CACHE_FILE = _get_cache_file_path()
    return _CACHE_FILE


def _load_cache():
    if _IS_CLOUD:
        return {}
    _f = _cache_file()
    if _f is None or not _f.exists():
        return {}
    try:
        with open(_f, "r", encoding="utf-8") as _fp:
            _data = json.load(_fp)
        if isinstance(_data, dict):
            return {k: v for k, v in _data.items()
                    if isinstance(k, str) and isinstance(v, list)}
    except Exception:
        pass
    return {}


def _save_cache(_cache):
    if _IS_CLOUD:
        return False
    _f = _cache_file()
    if _f is None:
        return False
    try:
        with open(_f, "w", encoding="utf-8") as _fp:
            json.dump(_cache, _fp, ensure_ascii=False,
                      indent=2, default=str)
        return True
    except Exception:
        return False


def _positions_signature(positions):
    if not positions:
        return ""
    try:
        _minimal = [
            {k: p.get(k) for k in ("_id", "Кол-во", "Цена", "Теор.цена",
                                    "Дельта", "Гамма", "Вега", "Тета",
                                    "Ро", "visible")}
            for p in positions
        ]
        return hashlib.md5(
            json.dumps(_minimal, ensure_ascii=False,
                       sort_keys=True, default=str).encode()
        ).hexdigest()
    except Exception:
        return ""


# =====================================================================
# Вспомогательные функции поиска на доске
# =====================================================================
def _strike_eq(a, b):
    try:
        return abs(float(a) - float(b)) < 0.001
    except (TypeError, ValueError):
        return False


def _find_board_row(board, side_key, strike):
    if not board or strike is None:
        return None
    for row in (board.get(side_key) or []):
        if _strike_eq(row.get("strike"), strike):
            return row
    return None


def _find_board_row_by_secid(board, secid):
    if not board or not secid or str(secid).strip() in ("", "—"):
        return None
    _needle = str(secid).strip().upper()
    for _side in ("call", "put"):
        for row in (board.get(_side) or []):
            if str(row.get("secid", "")).strip().upper() == _needle:
                return row
    return None


def _get_board_for_analytics(asset, atype, series):
    if not (asset and atype and series):
        return None
    try:
        from core.options import fetch_optionboard
        return fetch_optionboard(asset, atype, series)
    except Exception:
        return None


def _fetch_board_nocache(asset, asset_type_ui, series_code):
    """Используется только при «живом» обновлении теор.цены и греков."""
    if not (asset and asset_type_ui and series_code):
        return None
    base_type = ASSET_TYPE_MAP.get(asset_type_ui, 'futures')
    type_order = [base_type, 'futures', 'share', 'index',
                  'currency', 'commodity']
    tried = []
    for asset_code in _asset_code_candidates(asset, asset_type_ui):
        for at in type_order:
            key = (asset_code, at)
            if key in tried:
                continue
            tried.append(key)
            url = (f"{API_BASE_URL}/assets/{asset_code}"
                   f"/optionseries/{series_code}/optionboard")
            data = iss_get_json(url, params={'asset_type': at}, timeout=15)
            if data is not None:
                return data
    return None