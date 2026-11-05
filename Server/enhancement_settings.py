"""Server-wide enhancement override; never reads or writes player profiles."""
import json
import os
from pathlib import Path
import tempfile
import threading

PATH = Path(__file__).resolve().parent / 'gamedata' / 'enhancement_settings.json'
LOCK = threading.RLock()

def get_rate():
    with LOCK:
        try:
            value = json.loads(PATH.read_text(encoding='utf-8')).get('success_percent')
            return value if type(value) is int and 1 <= value <= 100 else None
        except (OSError, ValueError, TypeError, AttributeError):
            return None

def set_rate(value):
    if value is not None and (type(value) is not int or not 1 <= value <= 100):
        raise ValueError('Tỷ lệ phải là số nguyên từ 1 đến 100.')
    with LOCK:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=PATH.parent,
                                             prefix='enhancement_', suffix='.tmp', delete=False) as f:
                name = f.name
                json.dump({'success_percent': value}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, PATH)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)
    return value
