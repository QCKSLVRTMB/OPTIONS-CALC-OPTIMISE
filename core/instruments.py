"""Справочник инструментов MOEX и резолверы кодов."""
import time

from config import ASSET_TYPE_MAP, API_BASE_URL
from core.iss_api import iss_get_json


# =====================================================================
# ⚠️ ВАЖНО: скопируйте сюда ваш полный MOEX_INSTRUMENTS из app.py
#    (все 5 категорий: Индексы, Акции, Фьючерсы, Валюты, Товары).
#    Ниже — только те две, что были показаны; остальное — placeholder.
# =====================================================================
MOEX_INSTRUMENTS = {
    "Индексы": {
        "RTS": ("Индекс", "Индекс РТС"),
        "MIX": ("Индекс", "Индекс МосБиржи"),
        "RVI": ("Индекс", "Индекс волатильности RVI"),
        "MOEXCNY": ("Индекс", "Индекс МосБиржи в юанях"),
        "RGBI": ("Индекс", "Индекс RGBI"),
        "MMI": ("Индекс", "Индекс металлов и добычи"),
        "FNI": ("Индекс", "Индекс финансов"),
        "OGI": ("Индекс", "Индекс нефти и газа"),
        "MXI": ("Индекс", "Индекс МосБиржи (мини)"),
        "RTSM": ("Индекс", "Индекс РТС (мини)"),
    },
    "Акции": {
        "GAZP": ("Акция", "Газпром"),
        "SBER": ("Акция", "Сбербанк о.с."),
        "SBERP": ("Акция", "Сбербанк п.с."),
        "LKOH": ("Акция", "ЛУКОЙЛ"),
        "ROSN": ("Акция", "Роснефть"),
        "NOTK": ("Акция", "НОВАТЭК"),
        "TATN": ("Акция", "Татнефть о.с."),
        "TATNP": ("Акция", "Татнефть п.с."),
        "SNGSP": ("Акция", "Сургутнефтегаз п.с."),
        "MTSS": ("Акция", "МТС"),
        "MGNT": ("Акция", "Магнит"),
        "GMKN": ("Акция", "Норникель"),
        "NLMK": ("Акция", "НЛМК"),
        "CHMF": ("Акция", "Северсталь"),
        "ALRS": ("Акция", "АЛРОСА"),
        "VTBR": ("Акция", "ВТБ"),
        "MOEX": ("Акция", "Московская Биржа"),
        "AFKS": ("Акция", "АФК Система"),
        "IRAO": ("Акция", "Интер РАО"),
        "HYDR": ("Акция", "РусГидро"),
        "RTKM": ("Акция", "Ростелеком"),
        "PLZL": ("Акция", "Полюс"),
        "MAGN": ("Акция", "ММК"),
        "YDEX": ("Акция", "Яндекс"),
        "PHOR": ("Акция", "ФосАгро"),
        "RUAL": ("Акция", "РУСАЛ"),
        "FEES": ("Акция", "ФСК ЕЭС"),
        "TRNFP": ("Акция", "Транснефть п.с."),
        "AFLT": ("Акция", "Аэрофлот"),
        "SIBN": ("Акция", "Газпром нефть"),
        "PIKK": ("Акция", "ПИК"),
        "FLOT": ("Акция", "Совкомфлот"),
        "CBOM": ("Акция", "МКБ"),
        "SGZH": ("Акция", "Сегежа"),
        "BSPB": ("Акция", "Банк Санкт-Петербург"),
        "KMAZ": ("Акция", "КАМАЗ"),
        "ASTR": ("Акция", "Группа Астра"),
        "SVCB": ("Акция", "Совкомбанк"),
    },
    # "Фьючерсы": { ... СКОПИРУЙТЕ ИЗ ВАШЕГО app.py ... },
    # "Валюты":   { ... СКОПИРУЙТЕ ИЗ ВАШЕГО app.py ... },
    # "Товары":   { ... СКОПИРУЙТЕ ИЗ ВАШЕГО app.py ... },
}

_SHARE_TO_FUTURE_ALIAS = {
    "GAZP": "GAZR", "SBER": "SBRF", "SBERP": "SBPR",
    "MTSS": "MTSI", "SNGS": "SNGR", "SNGSP": "SNGP", "TRNFP": "TRNF",
}

_UI_TO_ASSET_TYPES = {
    "Акция":   [("share", None)],
    "Фьючерс": [("futures", None)],
    "Индекс":  [("index", None), ("futures", "index")],
    "Валюта":  [("currency", None), ("futures", "currency")],
    "Товар":   [("commodity", None), ("futures", "commodity")],
}


# ---------------------------------------------------------------------
# Глобалы и функция живут в ОДНОМ модуле — иначе global не работает.
# ---------------------------------------------------------------------
_OPTION_CALC_ASSETS = None
_OPTION_CALC_ASSETS_TS = 0.0


def resolve_canonical_asset_code(user_input, asset_type_ui=None):
    if not user_input:
        return user_input
    s = user_input.strip()
    if not s:
        return s
    s_upper = s.upper()
    if asset_type_ui and asset_type_ui in MOEX_INSTRUMENTS:
        items = MOEX_INSTRUMENTS[asset_type_ui]
        for code in items.keys():
            if code.upper() == s_upper:
                return code
        return s_upper
    for cat_items in MOEX_INSTRUMENTS.values():
        for code in cat_items.keys():
            if code.upper() == s_upper:
                return code
    return s_upper


def _load_option_calc_assets():
    global _OPTION_CALC_ASSETS, _OPTION_CALC_ASSETS_TS
    now = time.time()
    if (_OPTION_CALC_ASSETS is not None
            and (now - _OPTION_CALC_ASSETS_TS) < 21600):
        return _OPTION_CALC_ASSETS
    data = iss_get_json(f"{API_BASE_URL}/assets", timeout=15)
    if isinstance(data, list):
        _OPTION_CALC_ASSETS = data
        _OPTION_CALC_ASSETS_TS = now
    return _OPTION_CALC_ASSETS or []


def resolve_option_calc_code(asset_input, asset_type_ui):
    ticker = (asset_input or "").strip().upper()
    moex_type = ASSET_TYPE_MAP.get(asset_type_ui, "futures")
    if not ticker:
        return ticker, moex_type

    if asset_type_ui == "Индекс" and ticker == "RTSI":
        ticker = "RTS"

    if asset_type_ui == "Фьючерс":
        alias = _SHARE_TO_FUTURE_ALIAS.get(ticker)
        if alias:
            ticker = alias

    assets = _load_option_calc_assets()
    if assets:
        wanted = _UI_TO_ASSET_TYPES.get(asset_type_ui, [(moex_type, None)])
        supported = {
            (a.get("asset_code"), a.get("asset_type"), a.get("asset_subtype"))
            for a in assets
        }
        for atype, sub in wanted:
            if (ticker, atype, sub) in supported:
                return ticker, atype
        for atype, _sub in wanted:
            for a in assets:
                if (a.get("asset_code") == ticker
                        and a.get("asset_type") == atype):
                    return ticker, atype

    return ticker, moex_type


def get_asset_code_and_type(asset_input, asset_type_ui):
    return resolve_option_calc_code(asset_input, asset_type_ui)


def _asset_code_candidates(asset, asset_type_ui):
    s_upper = (asset or "").strip().upper()
    code, _ = resolve_option_calc_code(asset, asset_type_ui)
    cands = [code] if code else []
    if s_upper and s_upper not in cands:
        cands.append(s_upper)
    if asset_type_ui == "Фьючерс":
        alias = _SHARE_TO_FUTURE_ALIAS.get(s_upper)
        if alias and alias not in cands:
            cands.append(alias)
    return cands or [s_upper]