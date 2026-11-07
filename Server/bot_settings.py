"""Suc manh doi BOT (may) theo tung che do: the cong (+0..+20) va cap cau thu (LV 1..30).
Luu o gamedata/bot_settings.json (thiet lap rieng cua may, ban va khong ghi de).

Che do: Da voi may, Giai dau (League), tung giai Cup (cup_<metaid>, http_config goi set_cups luc
khoi dong), World Cup, Rank 1vs1 / Rank Gia lap (HLV ao). Rank mac dinh "Theo HLV ao" (AUTO): moi
HLV ao giu hang the / cap rieng theo trinh (Elo) - chon so cu the thi ghi de toan doi.
Giai cup chua luu rieng thi dung gia tri cu cua khoa "cup" (ban truoc chi co 1 dong cho moi cup)."""
import json
import os
import tempfile
import threading
from pathlib import Path

PATH = Path(__file__).resolve().parent / "gamedata" / "bot_settings.json"
LOCK = threading.RLock()
BASE_MODES = {  # khoa -> ten hien tren GM Tool
    "friendly": "Đá với máy",
    "league": "Giải đấu (League)",
}
TAIL_MODES = {
    "worldcup": "World Cup (chế độ World Cup)",
    "rank1v1": "Xếp hạng 1vs1 (HLV ảo)",
    "rankmgr": "Xếp hạng Giả lập (HLV ảo)",
}
AUTO_MODES = {"rank1v1", "rankmgr"}  # cho phep AUTO (theo HLV ao)
AUTO = -1  # grade/level = -1: giu nguyen theo doi hinh HLV ao
_CUPS = {}  # "cup_<metaid>" -> ten giai
_CUP_PROVIDER = None  # http_config: ham tra [(metaid, ten)] (doc ten luc can, khong luc import)
MAX_LEVEL = 30
DEFAULT_LEVEL = 30  # mac dinh LV 30
MAX_GRADE = 20
_DEFAULT_GRADE = min(MAX_GRADE, max(0, int(os.environ.get("FO3_COM_GRADE", "10"))))


def set_cups(cups):
    """cups: [(metaid, ten)] theo thu tu hien trong game, hoac ham tra danh sach do."""
    global _CUP_PROVIDER
    if callable(cups):
        _CUP_PROVIDER = cups
        _CUPS.clear()
        return
    _CUP_PROVIDER = None
    _CUPS.clear()
    for metaid, name in cups:
        _CUPS[f"cup_{metaid}"] = name


def _cups():
    global _CUP_PROVIDER
    if _CUP_PROVIDER is not None:
        try:
            cups = list(_CUP_PROVIDER())
        except Exception as e:  # khong de GM Tool / tran dau hong vi ten giai
            print(f"bot_settings cups: {e!r}", flush=True)
            return dict(_CUPS)
        _CUP_PROVIDER = None
        set_cups(cups)
    return dict(_CUPS)


def modes():
    return {**BASE_MODES, **{k: f"Cúp: {v}" for k, v in _cups().items()}, **TAIL_MODES}


def _default(m):
    if m in AUTO_MODES:
        return {"grade": AUTO, "level": AUTO}
    return {"grade": _DEFAULT_GRADE, "level": DEFAULT_LEVEL}


def defaults():
    return {m: _default(m) for m in modes()}


def _valid(m, g, lv):
    lo = AUTO if m in AUTO_MODES else None
    gok = type(g) is int and (0 <= g <= MAX_GRADE or g == lo)
    lok = type(lv) is int and (1 <= lv <= MAX_LEVEL or lv == lo)
    return gok, lok


def _read():
    with LOCK:
        try:
            saved = json.loads(PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
    return saved if isinstance(saved, dict) else {}


def get():
    saved = _read()
    out = {}
    for m in modes():
        out[m] = dict(_default(m))
        v = saved.get(m)
        if not isinstance(v, dict) and m.startswith("cup_"):
            v = saved.get("cup")  # gia tri chung cua ban cu
        if isinstance(v, dict):
            g, lv = v.get("grade"), v.get("level")
            gok, lok = _valid(m, g, lv)
            if gok:
                out[m]["grade"] = g
            if lok:
                out[m]["level"] = lv
    return out


def set_all(values):
    cur = get()
    names = modes()
    for m, v in (values or {}).items():
        if m not in names or not isinstance(v, dict):
            continue
        g, lv = v.get("grade", cur[m]["grade"]), v.get("level", cur[m]["level"])
        gok, lok = _valid(m, g, lv)
        if not gok:
            raise ValueError(f"{names[m]}: thẻ cộng phải từ +0 đến +{MAX_GRADE}")
        if not lok:
            raise ValueError(f"{names[m]}: cấp cầu thủ phải từ 1 đến {MAX_LEVEL}")
        cur[m] = {"grade": g, "level": lv}
    with LOCK:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=PATH.parent, prefix="bot_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False)
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
    """{grade, level} cua che do (AUTO = -1 chi co o rank1v1/rankmgr). Cup chua dang ky
    (vd. ten cu "cup") -> gia tri chung."""
    vals = get()
    if mode in vals:
        return vals[mode]
    if mode.startswith("cup"):
        v = _read().get("cup")
        if isinstance(v, dict) and all(_valid("cup", v.get("grade"), v.get("level"))):
            return {"grade": v["grade"], "level": v["level"]}
    return _default(mode)
