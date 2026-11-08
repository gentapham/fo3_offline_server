"""Ty le dap the (nang cap) cho ca server - GM Tool > Ty le dap the. Khong dong vao ho so nguoi choi.

gamedata/enhancement_settings.json:
    success_percent : 1..100 = mot ty le cho moi cap; null = ty le goc cua game
    per_grade       : [20 so 0..100, le 0.1] ty le cua tung cap (+0->+1 ... +19->+20); co thi uu tien hon success_percent
"""
import json
import os
from pathlib import Path
import tempfile
import threading

PATH = Path(__file__).resolve().parent / 'gamedata' / 'enhancement_settings.json'
LOCK = threading.RLock()

DEFAULT_RATE = 30  # mac dinh khi chua chinh tren GM Tool: 30% thanh cong
GRADES = 20  # +0->+1 ... +19->+20


def _load():
    try:
        d = json.loads(PATH.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def get_rate():
    """Ty le thanh cong (%), hoac None = ty le goc cua game (nguoi dung bam "Dung ty le goc")."""
    with LOCK:
        d = _load()
        if d is None:
            return DEFAULT_RATE  # chua co file -> mac dinh 30%
        value = d.get('success_percent')
        return value if type(value) is int and 1 <= value <= 100 else None


def get_grades():
    """[20 ty le %] theo cap the dang dap (index = cap hien tai), hoac None = khong dung che do nay."""
    with LOCK:
        g = (_load() or {}).get('per_grade')
        if isinstance(g, list) and len(g) == GRADES and all(type(v) in (int, float) and 0 <= v <= 100 for v in g):
            return g
        return None


def _write(data):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=PATH.parent,
                                         prefix='enhancement_', suffix='.tmp', delete=False) as f:
            name = f.name
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, PATH)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def set_rate(value):
    """Mot ty le cho moi cap (hoac None = ty le goc); tat che do theo tung cap."""
    if value is not None and (type(value) is not int or not 1 <= value <= 100):
        raise ValueError('Tỷ lệ phải là số nguyên từ 1 đến 100.')
    with LOCK:
        d = _load() or {}
        d['success_percent'] = value
        d['per_grade'] = None
        _write(d)
    return value


def set_grades(values):
    """Ty le theo tung cap: list 20 so nguyen 0..100."""
    if not isinstance(values, list) or len(values) != GRADES:
        raise ValueError(f'Cần đủ {GRADES} tỷ lệ (+0→+1 … +19→+20).')
    out = []
    for v in values:
        if type(v) not in (int, float) or not 0 <= v <= 100:
            raise ValueError('Mỗi tỷ lệ phải là số từ 0 đến 100.')
        v = round(float(v), 1)
        out.append(int(v) if v.is_integer() else v)
    with LOCK:
        d = _load() or {'success_percent': DEFAULT_RATE}
        d['per_grade'] = out
        _write(d)
    return out
