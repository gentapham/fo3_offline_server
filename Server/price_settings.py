"""Cai dat gia EP cua the cau thu (trang admin > Tong quan > Gia EP).

gamedata/price_settings.json:
    zero_percent : gia the +0 = bao nhieu % gia the +1 (0 = nhu ban goc: the +0 khong co gia)
    multiplier   : nhan gia toan bo the (1 = goc)
    overrides    : {spid: gia EP o +1} gia tu dat cho tung the; cac cap khac tinh theo he so cap the
Doc lai file khi no thay doi (theo thoi gian sua file), khong can khoi dong lai server.
"""
import json
import os
import tempfile
import threading
import time
from pathlib import Path

PATH = Path(__file__).resolve().parent / "gamedata" / "price_settings.json"
LOCK = threading.RLock()
DEFAULT = {"zero_percent": 0, "multiplier": 1.0, "overrides": {}}
_cache = {"mtime": None, "data": dict(DEFAULT), "checked": -1e9}
RECHECK = 1.0  # s: card_price goi get() hang tram nghin lan / yeu cau -> chi stat file toi da 1 lan/giay


def _clean(d):
    d = d if isinstance(d, dict) else {}
    zero = d.get("zero_percent", 0)
    mult = d.get("multiplier", 1.0)
    ov = d.get("overrides") or {}
    zero = int(zero) if isinstance(zero, (int, float)) and 0 <= zero <= 100 else 0
    mult = float(mult) if isinstance(mult, (int, float)) and 0.1 <= mult <= 10 else 1.0
    ov = {str(int(k)): int(v) for k, v in ov.items()
          if str(k).isdigit() and isinstance(v, (int, float)) and 0 <= v <= 10 ** 12} if isinstance(ov, dict) else {}
    return {"zero_percent": zero, "multiplier": mult, "overrides": ov}


def get():
    with LOCK:
        now = time.monotonic()
        if now - _cache["checked"] < RECHECK:
            return _cache["data"]
        _cache["checked"] = now
        try:
            m = PATH.stat().st_mtime
        except OSError:
            _cache.update(mtime=None, data=dict(DEFAULT))
            return _cache["data"]
        if m != _cache["mtime"]:
            try:
                _cache.update(mtime=m, data=_clean(json.loads(PATH.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                _cache.update(mtime=m, data=dict(DEFAULT))
        return _cache["data"]


def save(data):
    data = _clean(data)
    with LOCK:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=PATH.parent, prefix="price_",
                                             suffix=".tmp", delete=False) as f:
                name = f.name
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(name, PATH)
            name = None
        finally:
            if name and os.path.exists(name):
                os.unlink(name)
        _cache.update(mtime=None, checked=-1e9)
    return get()
