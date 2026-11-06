"""Suc manh doi BOT (may) theo tung che do: the cong (+0..+10) va cap cau thu (LV 1..23).
Luu o gamedata/bot_settings.json (thiet lap rieng cua may, ban va khong ghi de)."""
import json
import os
import tempfile
import threading
from pathlib import Path

PATH = Path(__file__).resolve().parent / "gamedata" / "bot_settings.json"
LOCK = threading.RLock()
MODES = {  # khoa -> ten hien tren GM Tool
    "friendly": "Đá với máy",
    "league": "Giải đấu (League)",
    "cup": "Cúp (châu Âu, Nam Mỹ)",
    "worldcup": "World Cup",
}
MAX_LEVEL = 23
_DEFAULT_GRADE = min(10, max(0, int(os.environ.get("FO3_COM_GRADE", "10"))))


def defaults():
    return {m: {"grade": _DEFAULT_GRADE, "level": 1} for m in MODES}


def get():
    out = defaults()
    with LOCK:
        try:
            saved = json.loads(PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
    for m in MODES:
        v = saved.get(m) if isinstance(saved, dict) else None
        if isinstance(v, dict):
            g, lv = v.get("grade"), v.get("level")
            if type(g) is int and 0 <= g <= 10:
                out[m]["grade"] = g
            if type(lv) is int and 1 <= lv <= MAX_LEVEL:
                out[m]["level"] = lv
    return out


def set_all(values):
    cur = get()
    for m, v in (values or {}).items():
        if m not in MODES or not isinstance(v, dict):
            continue
        g, lv = v.get("grade", cur[m]["grade"]), v.get("level", cur[m]["level"])
        if type(g) is not int or not 0 <= g <= 10:
            raise ValueError(f"{MODES[m]}: thẻ cộng phải từ +0 đến +10")
        if type(lv) is not int or not 1 <= lv <= MAX_LEVEL:
            raise ValueError(f"{MODES[m]}: cấp cầu thủ phải từ 1 đến {MAX_LEVEL}")
        cur[m] = {"grade": g, "level": lv}
    with LOCK:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=PATH.parent, prefix="bot_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cur, f)
            os.replace(name, PATH)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return cur


def reset():
    with LOCK:
        try:
            PATH.unlink()
        except OSError:
            pass
    return get()


def for_mode(mode):
    return get().get(mode) or {"grade": _DEFAULT_GRADE, "level": 1}
