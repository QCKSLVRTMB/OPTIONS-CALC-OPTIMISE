"""HTTP-клиент к MOEX ISS. Без Streamlit — чистый requests."""
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _make_iss_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=0.6,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retry,
                          pool_connections=8, pool_maxsize=8)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "User-Agent": "MOEX-Options-Calc/1.0",
        "Accept": "application/json, */*",
    })
    return s


_ISS_SESSION = _make_iss_session()

# ключ → timestamp, до которого запрос считается «недавно упавшим»
_FAILED_UNTIL = {}


def iss_get(url, params=None, timeout=20):
    for attempt in range(3):
        try:
            r = _ISS_SESSION.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException:
            if attempt == 2:
                return None
            time.sleep(0.7 * (attempt + 1))
    return None


def iss_get_json(url, params=None, timeout=20):
    r = iss_get(url, params=params, timeout=timeout)
    if r is None:
        return None
    try:
        return r.json()
    except Exception:
        return None


def _is_failed_recently(key, cooldown_sec=60):
    now = time.time()
    ts = _FAILED_UNTIL.get(key)
    return ts is not None and now < ts


def _mark_failed(key, cooldown_sec=60):
    _FAILED_UNTIL[key] = time.time() + cooldown_sec