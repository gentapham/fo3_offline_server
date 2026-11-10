"""GM Tool > Nhac nen: thay nhac nen EA Trax trong game bang file mp3 (xem music.py)."""
import base64
import os
import re
import time

import admin
import music
from admin import api, AdminError

admin.READ_ONLY.add("music_info")
admin.NO_LOCK.update({"music_info", "music_upload", "music_remove", "music_move", "music_install", "music_restore"})


def _info(c=None, applied=None):
    c = c or music.load_conf()
    return {"songs": c["songs"], "installed": c["installed"], "installed_songs": c.get("installed_songs", []),
            "pending": c.get("pending"), "error": c.get("error"), "applied_at": c.get("applied_at"),
            "applied_now": applied, "game_running": music.game_running(), "slots": music.SLOTS,
            "original": music.ORIGINAL_TRACKS, "max_mb": music.MAX_MB}


@api("music_info")
def music_info(p):
    return _info()


@api("music_upload")
def music_upload(p):
    name = os.path.basename(str(p.get("name") or "nhac.mp3"))[:120]
    try:
        data = base64.b64decode(p.get("data") or "", validate=False)
    except Exception:
        raise AdminError("Dữ liệu file không hợp lệ.") from None
    if not data:
        raise AdminError("File rỗng.")
    if len(data) > music.MAX_MB * 1024 * 1024:
        raise AdminError(f"File quá lớn (tối đa {music.MAX_MB} MB).")
    with music.LOCK:
        c = music.load_conf()
        if len(c["songs"]) >= music.SLOTS:
            raise AdminError(f"Tối đa {music.SLOTS} bài. Xoá bớt bài rồi thêm lại.")
        try:
            _s, info = music.mp3_to_stream(data)
        except music.MusicError as e:
            raise AdminError(f"{name}: {e}") from None
        except Exception as e:
            raise AdminError(f"{name}: không đọc được file mp3 ({type(e).__name__}).") from None
        os.makedirs(music.DIR, exist_ok=True)
        fn = time.strftime("%Y%m%d%H%M%S") + "_" + str(len(c["songs"])) + ".mp3"
        with open(os.path.join(music.DIR, fn), "wb") as f:
            f.write(data)
        title = re.sub(r"\.mp3$", "", name, flags=re.I)
        c["songs"].append({"file": fn, "name": title, "seconds": info["seconds"], "sr": info["sr"], "ch": info["ch"],
                           "mb": round(len(data) / 1048576, 1)})
        music.save_conf(c)
    return _info(c)


def _idx(c, p):
    try:
        i = int(p.get("index"))
    except (TypeError, ValueError):
        raise AdminError("Thiếu bài.") from None
    if not 0 <= i < len(c["songs"]):
        raise AdminError("Không tìm thấy bài.")
    return i


@api("music_remove")
def music_remove(p):
    with music.LOCK:
        c = music.load_conf()
        s = c["songs"].pop(_idx(c, p))
        try:
            os.remove(os.path.join(music.DIR, s["file"]))
        except OSError:
            pass
        music.save_conf(c)
    return _info(c)


@api("music_move")
def music_move(p):
    with music.LOCK:
        c = music.load_conf()
        i = _idx(c, p)
        j = i + (1 if p.get("dir") == "down" else -1)
        if 0 <= j < len(c["songs"]):
            c["songs"][i], c["songs"][j] = c["songs"][j], c["songs"][i]
            music.save_conf(c)
    return _info(c)


def _do(action):
    try:
        c, now = music.request(action)
    except music.MusicError as e:
        raise AdminError(str(e)) from None
    return _info(c, applied=now)


@api("music_install")
def music_install(p):
    if not music.load_conf()["songs"]:
        raise AdminError("Chưa có bài nào. Hãy thêm file mp3 trước.")
    return _do("install")


@api("music_restore")
def music_restore(p):
    return _do("restore")
