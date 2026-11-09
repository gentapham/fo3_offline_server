"""Sao luu / khoi phuc tai khoan FO3 Offline (dung chung cho Launcher va GM Tool).

File sao luu .fo3save = zip:
    fo3save.json                      thong tin: ten HLV, cap, EP, so cau thu, thoi gian, ban va
    Server/gamedata/state.json        tai khoan (doi hinh, the, EP, HLV, tui do...)
    Server/gamedata/<cai dat>.json    cai dat GM Tool (ty le dap the, gia, thuong tran, Shop...) neu co
    _cache/.../staffs/<id>.png        anh HLV tu tao (custom_staff.json)
Khoi phuc chi giai nen dung danh sach file tren (khong ghi file la / duong dan ../), truoc do tu sao luu tai
khoan hien tai vao Server/gamedata/backup/truoc_khoi_phuc_<thoi gian>.fo3save.
Launcher goi module nay tu thu muc Server (sua module = ban va thuong, khong can build lai Launcher)."""
import io
import json
import os
import re
import shutil
import time
import zipfile

EXT = ".fo3save"
META = "fo3save.json"
SETTINGS = ("enhancement_settings.json", "daily_rewards.json", "bot_settings.json", "custom_packs.json",
            "custom_staff.json", "match_rewards.json", "server_options.json", "market_settings.json",
            "rank_settings.json", "multiplay.json", "price_settings.json", "missions.json", "shop_settings.json")
STAFF_DIRS = ("_cache/games/fo3sg/externalAssets/staffs", "_cache/cdn/externalAssets/staffs")
VERSION = 1


def desktop_dir():
    """Thu muc Desktop that cua Windows (ke ca khi chuyen sang OneDrive), khong thi ~/Desktop."""
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0 and os.path.isdir(buf.value):
            return buf.value
    except Exception:
        pass
    d = os.path.join(os.path.expanduser("~"), "Desktop")
    return d if os.path.isdir(d) else os.path.expanduser("~")


def _gamedata(game_dir):
    return os.path.join(game_dir, "Server", "gamedata")


def _custom_staff_ids(game_dir):
    try:
        with open(os.path.join(_gamedata(game_dir), "custom_staff.json"), encoding="utf-8") as f:
            return {str(k) for k in (json.load(f).get("staff") or {})}
    except (OSError, ValueError, AttributeError):
        return set()


def _members(game_dir):
    """[(duong dan trong zip, duong dan that)] cua moi file dang co."""
    out = []
    gd = _gamedata(game_dir)
    for name in ("state.json",) + SETTINGS:
        p = os.path.join(gd, name)
        if os.path.isfile(p):
            out.append((f"Server/gamedata/{name}", p))
    for sid in sorted(_custom_staff_ids(game_dir)):
        for d in STAFF_DIRS:
            p = os.path.join(game_dir, *d.split("/"), f"{sid}.png")
            if os.path.isfile(p):
                out.append((f"{d}/{sid}.png", p))
    return out


def summary(state):
    """Thong tin ngan cua tai khoan: HLV dang chon (ten, cap, EP, so the)."""
    try:
        slot = state.get("defaultslot", 0)
        coaches = state.get("coaches") or []
        c = next((x for x in coaches if x.get("slot") == slot), coaches[0] if coaches else {})
        team = (state.get("teams") or {}).get(str(c.get("slot", slot))) or {}
        return {"name": c.get("name", ""), "level": (c.get("levelinfo") or {}).get("level", 1), "ep": c.get("ep", 0),
                "players": len(team.get("players") or []), "coaches": len(coaches)}
    except Exception:
        return {"name": "", "level": 0, "ep": 0, "players": 0, "coaches": 0}


def create(game_dir, out_dir=None, note=""):
    """Tao file sao luu, tra ve duong dan. out_dir mac dinh = Desktop."""
    state_p = os.path.join(_gamedata(game_dir), "state.json")
    if not os.path.isfile(state_p):
        raise FileNotFoundError("Chưa có tài khoản để sao lưu (không thấy Server/gamedata/state.json).")
    with open(state_p, encoding="utf-8") as f:
        st = json.load(f)
    info = summary(st)
    try:
        with open(os.path.join(game_dir, "patch_version.txt"), encoding="utf-8") as f:
            patch = f.read().strip()
    except OSError:
        patch = ""
    meta = {"format": "fo3save", "version": VERSION, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "patch": patch, "note": note, **info}
    safe = re.sub(r"[^0-9A-Za-z_-]+", "", info["name"].replace(" ", "_")) or "TaiKhoan"
    out_dir = out_dir or desktop_dir()
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"FO3_SaoLuu_{safe}_{time.strftime('%Y-%m-%d_%H%M')}{EXT}")
    tmp = path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(META, json.dumps(meta, ensure_ascii=False, indent=1))
        for arc, real in _members(game_dir):
            z.write(real, arc)
    os.replace(tmp, path)
    return path


def read_meta(path):
    """Thong tin file sao luu (kiem tra dung dinh dang); loi -> ValueError."""
    try:
        with zipfile.ZipFile(path) as z:
            meta = json.loads(z.read(META).decode("utf-8"))
            names = set(z.namelist())
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as e:
        raise ValueError(f"File không phải bản sao lưu FO3 hợp lệ ({e.__class__.__name__}).")
    if meta.get("format") != "fo3save" or "Server/gamedata/state.json" not in names:
        raise ValueError("File không phải bản sao lưu FO3 hợp lệ.")
    return meta


def _allowed(arc):
    if arc == "Server/gamedata/state.json" or arc in {f"Server/gamedata/{n}" for n in SETTINGS}:
        return True
    return any(re.fullmatch(re.escape(d) + r"/\d+\.png", arc) for d in STAFF_DIRS)


def restore(game_dir, path):
    """Khoi phuc tu file sao luu. Truoc do sao luu tai khoan hien tai vao gamedata/backup. Tra ve (meta, ban cu)."""
    meta = read_meta(path)
    before = None
    if os.path.isfile(os.path.join(_gamedata(game_dir), "state.json")):
        before = create(game_dir, os.path.join(_gamedata(game_dir), "backup"), note="truoc khi khoi phuc")
        stamp = os.path.join(os.path.dirname(before), f"truoc_khoi_phuc_{time.strftime('%Y%m%d_%H%M%S')}{EXT}")
        os.replace(before, stamp)
        before = stamp
    with zipfile.ZipFile(path) as z:
        for arc in z.namelist():
            if arc == META or not _allowed(arc):
                continue
            dst = os.path.join(game_dir, *arc.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            data = z.read(arc)
            if arc.endswith(".json"):
                json.loads(data.decode("utf-8-sig"))  # file hong -> dung truoc khi ghi
            tmp = dst + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dst)
    return meta, before


def restore_bytes(game_dir, data, name="upload" + EXT):
    """Khoi phuc tu noi dung file (GM Tool tai len): ghi tam vao gamedata/backup roi restore."""
    bk = os.path.join(_gamedata(game_dir), "backup")
    os.makedirs(bk, exist_ok=True)
    tmp = os.path.join(bk, f"tai_len_{time.strftime('%Y%m%d_%H%M%S')}{EXT}")
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        return restore(game_dir, tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def find_pending(game_dir):
    """File .fo3save nguoi choi chep vao thu muc game (Launcher hoi khoi phuc), moi nhat truoc."""
    try:
        files = [os.path.join(game_dir, f) for f in os.listdir(game_dir) if f.lower().endswith(EXT)]
    except OSError:
        return []
    return sorted(files, key=os.path.getmtime, reverse=True)


def mark_done(game_dir, path):
    """Chuyen file da khoi phuc vao gamedata/backup (launcher khong hoi lai)."""
    bk = os.path.join(_gamedata(game_dir), "backup")
    os.makedirs(bk, exist_ok=True)
    dst = os.path.join(bk, "da_khoi_phuc_" + os.path.basename(path))
    try:
        shutil.move(path, dst)
    except OSError:
        pass
    return dst
