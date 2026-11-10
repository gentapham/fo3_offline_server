"""HTTP config/publisher service stub for FO3ID.

The client requests these relative paths (found in client memory):
    /info/server/config        -> expected: server list / endpoints config
    /game/my/ipaddress         -> expected: client's public IP echo
    /publisher/garena/sign     -> publisher token sign/verify
    /publisher/nexon/2ndauth   -> secondary auth
    /telemetry/gameconnection  -> telemetry sink (can be a no-op)
    /shop/cash/charge/url, /shop/cash/history/url -> cash shop URLs

Phase 1: log every request (method, path, query, headers, body) and return a minimal
placeholder so we can see which endpoint the client hits first and what it expects.
The real response shapes are unknown until captured — every handler is a guess.

Usage: python -I http_config.py [port=8080] [--tls]
Default port 8080 to avoid colliding with a local Apache on :80.

--tls serves HTTPS with certs/server.pem. fifazf.exe calls a hardcoded
https://APSEODESK172 (port 443) for /info/server/locale etc. — no launch arg
overrides it — so run `http_config.py 443 --tls` and map APSEODESK172 to
127.0.0.1 in the hosts file.
"""
import bisect, collections, functools, gzip, http.server, math, os, random, re, sqlite3, ssl, statistics, sys, threading, time

try:  # in tieng Viet co dau an toan ca khi output bi chuyen huong
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from contextlib import closing

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # `python -I` leaves the script dir off sys.path
import gen_teams
TLS = "--tls" in sys.argv
_args = [a for a in sys.argv[1:] if a != "--tls"]
PORT = int(_args[0]) if _args else 8080
LOG = os.path.join(HERE, "captures", "http_config.log")
CERT = os.path.join(HERE, "certs", "server.pem")  # self-signed, CN/SAN APSEODESK172
os.makedirs(os.path.dirname(LOG), exist_ok=True)

import json

# Where this service tells the client the game servers live.
REDIR_HOST, REDIR_PORT = "127.0.0.1", 28000
NOTI_HOST, NOTI_PORT = "127.0.0.1", 28001

# --------------------------------------------------------------------------- #
# Endpoint bodies — TENTATIVE. The real response format (XML vs JSON, field
# names, the token/sign scheme) is unknown: the client has not been captured
# reaching these (XignCode blocks it earlier — see README). These return minimal
# plausible JSON so the client has *something* to parse; correct each shape from
# a capture. Every request is logged regardless, which is the point for now.
# --------------------------------------------------------------------------- #

def _json(obj):
    return ("application/json", json.dumps(obj).encode())


def ep_server_config(query, body):
    return _json({
        "redirector": {"host": REDIR_HOST, "port": REDIR_PORT},
        "notification": {"host": NOTI_HOST, "port": NOTI_PORT},
        "region": "ID",
        "status": "ok",
    })


def ep_locale(query, body):
    # First call fifazf.exe makes. Body "OK" -> JSON_PARSING_ERROR in-game; {"result": true}
    # passes. `language` must be a WebCache.setBaseUrl key (kor eng chi vie tha ind jpn,
    # default kor): "id" crashed the UI (the old black screen); "ind" = Indonesian, its
    # externalAssets/language_ind.big is in the client cache. Publisher (Garena) is chosen
    # by launch args `region id token <t>`, not by this response.
    # `url` = WebCache base for externalAssets (crests, uniforms, player cards...). Without
    # it the client uses the dead Garena CDN, every refresh fails and crests show
    # "PLACE HOLDER" even though the files sit in its own _cache. Served by cdn_file().
    # language theo ngon ngu GM Tool chon (i18n: vi / en -> "eng", th -> "tha"; 2026-11-10)
    return _json({"result": True, "language": LANGUAGE_OF(), "url": f"https://APSEODESK172/{CDN_PREFIX}"})


LANGUAGE = os.environ.get("FO3_LANGUAGE", "eng")
LANGUAGE_OF = lambda: LANGUAGE  # i18n.install thay bang ngon ngu dang chon trong GM Tool


def _detect_game_dir():
    if "FO3_GAME" in os.environ and os.path.isdir(os.environ["FO3_GAME"]):
        return os.path.abspath(os.environ["FO3_GAME"])
    for candidate in [
        os.path.join(HERE, ".."),
        os.path.join(HERE, "..", "Game"),
        os.path.join(HERE, "Game"),
        r"E:\FO3TH - Copy",
    ]:
        if os.path.isfile(os.path.join(candidate, "fifazf.exe")):
            return os.path.abspath(candidate)
    return os.path.abspath(os.path.join(HERE, ".."))

GAME = _detect_game_dir()


def _detect_assets_dir():
    if "FO3_ASSETS" in os.environ and os.path.isdir(os.environ["FO3_ASSETS"]):
        return os.path.abspath(os.environ["FO3_ASSETS"])
    for candidate in [
        os.path.join(GAME, "_cache", "games", "fo3sg"),
        os.path.join(GAME, "_cache", "games", "fo3th"),
        os.path.join(GAME, "_cache", "games", "fo3id"),
        os.path.join(GAME, "_cache", "games", "fo3vn"),
        os.path.join(GAME, "_cache"),
    ]:
        if os.path.isdir(candidate):
            return os.path.abspath(candidate)
    return os.path.join(GAME, "_cache")

# The client's own downloaded copy of the Garena CDN (externalAssets/...).
ASSETS = _detect_assets_dir()
CDN_PREFIX = "cdn/"


# Mini face thieu trong kho: tai 1 lan tu CDN cong khai (cung ma cau thu EA pid, anh 128x128)
# roi luu vao externalAssets/players; that bai -> _noface.png. BAN OFFLINE: mac dinh TAT (moi anh da nam san
# trong may); chi bat khi can bang FO3_FACE_FETCH=1.
# Nguon 2 (du phong): anh mini FO3 cua fifaaddict (JPEG 40x40, co ca cau thu cu/huyen thoai ma CDN
# tren khong co); luu nguyen JPEG duoi ten p<pid>.png (trinh duyet va game nhan dang theo noi dung).
FACE_SOURCES = ("https://fo4.dn.nexoncdn.co.kr/live/externalAssets/common/players/p{pid}.png",
                "https://fifaaddict.com/fo3img/players/small/p{pid}.jpg")
_FACE_MISS = set()
_FACE_LOCK = threading.Lock()


def fetch_face(path):
    """path = .../players/p<pid>.png that is not on disk -> path after downloading, or None."""
    name = os.path.basename(path)
    m = re.fullmatch(r"p(\d+)\.png", name.lower())
    if not m or os.environ.get("FO3_FACE_FETCH", "0") != "1" or name in _FACE_MISS:
        return None
    import urllib.request
    pid = int(m.group(1)) % 1000000
    with _FACE_LOCK:
        if os.path.isfile(path):
            return path
        for src in FACE_SOURCES:
            try:
                req = urllib.request.Request(src.format(pid=pid), headers={
                    "User-Agent": "FO3Offline/1.0", "Referer": "https://vn.fifaaddict.com/"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    data = r.read(2_000_000)
                if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff":
                    tmp = path + ".part"
                    with open(tmp, "wb") as fh:
                        fh.write(data)
                    os.replace(tmp, path)
                    return path
            except Exception:
                pass
        _FACE_MISS.add(name)
    return None


def cdn_file(rel):
    """(content-type, bytes) of ASSETS/rel, or None (-> 404, client shows its default)."""
    f = os.path.normpath(os.path.join(ASSETS, rel))
    if not f.startswith(os.path.normpath(ASSETS) + os.sep):
        return None
    if not os.path.isfile(f):
        # Hinh cua hang (goi the, vat pham) khong co trong cache -> dung hinh mac dinh
        # shop/_fallback.png thay vi de game hien "NOT FOUND".
        parts = rel.replace("\\", "/").split("/")
        if len(parts) >= 2 and parts[-2] == "shop" and f.lower().endswith(".png"):
            f = os.path.join(os.path.dirname(f), "_fallback.png")
        # Anh cau thu: the mua dac biet xin p<spid>.png (vd p93214100 = mua 93 + pid 214100)
        # nhung kho anh chi co p<pid>.png -> lay anh theo pid (6 so cuoi).
        elif len(parts) >= 2 and parts[-2] == "players" and re.fullmatch(r"p\d{7,}\.png", parts[-1].lower()):
            pid = int(parts[-1][1:-4]) % 1000000
            f = os.path.join(os.path.dirname(f), f"p{pid}.png")
        # icon Team Color khong co (mau co ten / dac biet chua co hinh) -> huy hieu chung, khong de "NOT FOUND"
        elif len(parts) >= 2 and parts[-2] == "teamcolor" and f.lower().endswith(".png"):
            f = os.path.join(os.path.dirname(f), "_special.png")
        if len(parts) >= 2 and parts[-2] == "players" and not os.path.isfile(f):
            f = fetch_face(f) or os.path.join(os.path.dirname(f), "_noface.png")
        if not os.path.isfile(f):
            return None
    ctype = {".png": "image/png", ".jpg": "image/jpeg", ".swf": "application/x-shockwave-flash"}
    with open(f, "rb") as fh:
        return (ctype.get(os.path.splitext(f)[1].lower(), "application/octet-stream"), fh.read())


def ep_server_time(query, body):
    # TimerUtil.getTime: retries every second (and holds the bootflow) until `timestamp`
    # (ms, fed to new Date()) is present. timezone guessed as JS getTimezoneOffset() for
    # WIB (UTC+7) — the backend looked like Node/Express.
    return _json({"timestamp": int(time.time() * 1000), "timezone": -420})


def ep_my_ip(query, body):
    # real HTTP PUT from the lobby; every reply is JSON-parsed
    return _json({"result": True, "ipaddress": "127.0.0.1"})


def ep_garena_sign(query, body):
    # Accept any token for local offline use; echo a success + a fake signed token.
    return _json({"result": "ok", "token": "LOCAL-SIGNED-TOKEN", "uid": 1})


def ep_nexon_2ndauth(query, body):
    return _json({"result": "ok", "passport": "LOCAL-PASSPORT"})


def ep_telemetry(query, body):
    # Client JSON-parses every reply; plain "OK" -> JSON_PARSING_ERROR in-game.
    return _json({"result": True})


# The starter song (music/starter.mp3) lives in the game's own EA Trax playlist as TrackID 1,
# put there by tools/eatrax_song.py; the server does not play music.


def ep_empty(query, body):
    return _json({})


def ep_shop_url(query, body):
    return _json({"url": "about:blank"})


# ponytail: one local account kept in a JSON file; a real multi-user store comes later.
STATE = os.path.join(HERE, "gamedata", "state.json")
# Handlers read-modify-write state.json; game requests and the admin page (admin.py) take this
# so one never reads a half-written file or overwrites the other's change.
STATE_LOCK = threading.Lock()


def _state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"coaches": [], "defaultcoachid": "", "teams": {}}


def _save(st):
    """Ghi state.json: dumps 1 lan (nhanh hon json.dump ghi tung manh) vao file tam roi thay the -> may
    tat / Launcher khoi dong lai giua luc ghi khong de lai state.json cut ngan (mat ban luu)."""
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    # coach_me lay ngay tao HLV cu (khong co createdat) tu ctime cua state.json; thay file lam doi ctime
    # -> ghi lai gia tri do vao HLV truoc (1 lan)
    if any(not c.get("createdat") for c in st.get("coaches") or ()) and os.path.exists(STATE):
        made = w3c(os.path.getctime(STATE))
        for c in st["coaches"]:
            if not c.get("createdat"):
                c["createdat"] = made
    data = json.dumps(st)
    tmp = STATE + ".tmp"
    try:
        with open(tmp, "w") as f:
            f.write(data)
        for i in range(5):
            try:
                os.replace(tmp, STATE)
                return
            except PermissionError:  # Windows: file dang bi mo (antivirus / sao luu) -> thu lai
                time.sleep(0.02 * (i + 1))
    except OSError:
        pass
    with open(STATE, "w") as f:  # du phong: ghi thang nhu truoc
        f.write(data)


def coach_id(slot):
    return f"{slot + 1:024x}"  # Mongo-style _id


def ep_coaches(query, body):
    # StartupCoachCreationScreen: coachlist entries need _id, slot, name, state "active"
    # ("postpone" = deleted, restorable until hideat; recoveryperiod s goes in the delete notice).
    # initializeMyCoach also shows team name/crest from summary and teamvalue.val, same
    # values as the lobby HUD (coach_me).
    st = _state()
    if purge_deleted_coaches(st):
        _save(st)
    def entry(c):
        me = coach_me(st, c["slot"])
        team = me["team"]
        starters = {p["id"] for p in team["squad"]["positions"] if 0 < p["pos"] < POS_SUB}
        candidates = [p for p in team["players"] if p["id"] in starters]
        front = next((p for p in candidates if p["id"] == team["captain"]),
                     max(candidates, key=player_ovr) if candidates else None)
        # UpdateCoaches reads frontplayer.kit.home/goalie.id, not kit.availables.
        preview = ({"frontplayer": dict(front, kit=team_kit(c["teamid"])["availables"],
                                        lr=front.get("lr") or {})} if front else {})
        return dict(c, **preview, summary=me["summary"], teamvalue={"val": sum(p["price"] for p in me["team"]["players"])},
                    levelinfo=me["levelinfo"],
                    recoveryperiod=COACH_RECOVERY, **({"hideat": w3c(c["deleteat"])} if "deleteat" in c else {}))
    usable = {c["_id"] for c in st["coaches"] if c.get("state", "active") == "active"}
    return _json({"coachlist": [entry(c) for c in st["coaches"]],
                  "defaultcoachid": st["defaultcoachid"] if st["defaultcoachid"] in usable else ""})


def coach_name_valid(st, name):
    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 12 or any(ord(c) < 32 for c in name):
        return 'invalid name'
    if any(c['name'].casefold() == name.strip().casefold() for c in st['coaches']):
        return 'busy'
    return None


def ep_coach_reserve(query, body):
    req = json.loads(body or b'{}')
    st = _state()
    reason = coach_name_valid(st, req.get('name'))
    if len(st['coaches']) >= 2:
        reason = 'busy'
    return _json({'result': False, 'reason': reason} if reason else {'result': True})


def new_manager_team(teamid, chosen=()):
    import copy
    team = copy.deepcopy(roster(teamid)['team'])
    if chosen:
        chosen = {int(p.get('spid')) if isinstance(p, dict) else int(p) for p in chosen}
        selected = [p for p in team['players'] if int(p['spid']) in chosen]
        # Keep the requested cards and supply the bench required by the native client.
        selected += [p for p in team['players'] if p not in selected][:max(0,18-len(selected))]
        team['players'] = selected
    fix_formation(team, teamid)
    repair_squad(team)
    return team


def ep_new_team(slot, query, body):
    st = _state()
    if slot not in (0, 1) or any(c['slot'] == slot for c in st['coaches']):
        return _json({'result': False, 'reason': 'busy'})
    req = json.loads(body or b'{}')
    try:
        teamid = int(req.get('id') or 0)
        team = new_manager_team(teamid, req.get('spids') or [])
    except (ValueError, TypeError):
        return _json({'result': False, 'reason': 'Đội không hợp lệ'})
    if len(team['players']) < 18:
        return _json({'result': False, 'reason': 'Đội không hợp lệ'})
    st.setdefault('pendingteams', {})[str(slot)] = {'teamid': teamid, 'team': team}
    _save(st)
    return _json({'result': True, 'team': owned_team(team)})


def ep_create_coach(slot, query, body):
    req = json.loads(body or b'{}')
    st = _state()
    if slot not in (0, 1) or any(c['slot'] == slot for c in st['coaches']):
        return _json({'result': False, 'reason': 'busy'})
    name = req.get('coachName')
    reason = coach_name_valid(st, name)
    if reason:
        return _json({'result': False, 'reason': reason})
    try:
        teamid = int(req.get('id') or 0)
        pending = st.get('pendingteams', {}).get(str(slot))
        team = pending['team'] if pending and pending['teamid'] == teamid else new_manager_team(teamid)
    except (ValueError, TypeError):
        return _json({'result': False, 'reason': 'Đội không hợp lệ'})
    if len(team['players']) < 18:
        return _json({'result': False, 'reason': 'Đội không hợp lệ'})
    st['teams'][str(slot)] = team
    st.setdefault('pendingteams', {}).pop(str(slot), None)
    st['coaches'].append({'_id': coach_id(slot), 'slot': slot, 'name': name.strip(), 'state': 'active',
        'teamid': teamid, 'teamvalue': {'val': 0}, 'levelinfo': {'level': 1}, 'summary': {}, 'createdat': w3c(time.time())})
    _save(st)
    return _json({'result': True})


# Deleting a manager (StartupCoachCreationScreen > Delete): PUT /coach/<slot>/postponed puts it in
# state "postpone" until hideat; "Kembalikan manager" = DELETE /coach/<_id>/postponed undoes it.
# Once hideat has passed CoachUtil.shouldDeleteCoach shows the slot as free ("Buat manager baru").
COACH_RECOVERY = 300  # s a deleted manager can still be restored (the real game: 72 h)


def purge_deleted_coaches(st):
    """Drop managers whose restore time is over, with their club. The account-wide club data
    (league, cups, market offers, last match, trade) belongs to the default manager's club."""
    gone = [c for c in st["coaches"] if c.get("state") == "postpone" and c.get("deleteat", 0) <= time.time()]
    for c in gone:
        st["coaches"].remove(c)
        st["teams"].pop(str(c["slot"]), None)
        if c["_id"] == st["defaultcoachid"]:
            st["defaultcoachid"] = ""
            for k in COACH_CONTEXT_KEYS:
                st.pop(k, None)
        st.get("coachcontexts", {}).pop(str(c["slot"]), None)
    return gone


def ep_coach_postponed(key, query, body):
    """PUT /coach/<slot>/postponed: delete a manager; DELETE /coach/<_id>/postponed: restore it."""
    st = _state()
    purge_deleted_coaches(st)
    c = next((c for c in st["coaches"] if key in (str(c["slot"]), c["_id"])), None)
    if c is None:
        reply = {"result": False, "reason": "HLV này đã bị xoá."}  # restore: COACH_CREATION_RESTORE_ERROR
    elif json.loads(body or b"{}").get("_method") == "DELETE":
        c["state"] = "active"
        c.pop("deleteat", None)
        reply = {"result": True}
    else:
        c["state"] = "postpone"
        c["deleteat"] = time.time() + COACH_RECOVERY
        reply = {"result": True}
    _save(st)
    return _json(reply)


POS_SUB = 28  # Enums.PLAYERPOSITION: 0..27 pitch positions, 28 SUB, 29.. RESERVE


# OptionManager.applySoundSetting hands native volume * Master (each 0..10) for Commentary,
# PAVolume, PlayerCalls, SFX, Crowd and BGM; a missing key is NaN = silent (no music).
# OptionManager: OPTION_DEFAULT_CAM_HEIGHT/ZOOM = 10, CAMPRO_ZOOM/SPEED/SWING = 10 (max 20).
# cameraOption thieu cac khoa nay -> OptionGaugePopmenu.draw: value.number undefined -> Loi giao dien.
CAMERA_DEFAULTS = {"Camera": 0, "CamDetail": 0, "CamHeight": 10, "CamZoom": 10,
                   "CamProZoom": 10, "CamProSpeed": 10, "CamProSwing": 10}
SOUND_DEFAULTS = {"Master": 10, "Commentary": 10, "PAVolume": 10, "PlayerCalls": 10, "SFX": 10, "Crowd": 10, "BGM": 10}


# Complete client-native keyboard/pad layouts. Missing controlOption makes
# OptionManager.applyControlSetting return before assigning the controller.
CONTROL_DEFAULTS = {'Controller': 0, 'Type': 0, 'JoyAttType1': [4, 3, 2, 1, 5, 7, 6, 8], 'JoyDefType1': [4, 3, 2, 1, 5, 7, 6, 8], 'JoyMovType1': [9, 11, 10], 'JoyAttType2': [4, 2, 3, 1, 5, 6, 8, 7], 'JoyDefType2': [4, 2, 3, 1, 5, 6, 8, 7], 'JoyMovType2': [9, 11, 10], 'JoyAttType3': [4, 3, 2, 1, 5, 7, 6, 8], 'JoyDefType3': [4, 3, 2, 1, 5, 7, 6, 8], 'JoyMovType3': [11, 9, 10], 'KeyAttType1': [87, 65, 68, 83, 81, 90, 67, 69], 'KeyDefType1': [87, 65, 68, 83, 81, 90, 67, 69], 'KeyMovType1': [38, 40, 37, 39, 256, 257, 258], 'KeyAttType2': [87, 65, 68, 83, 81, 90, 67, 69], 'KeyDefType2': [87, 65, 83, 81, 68, 90, 67, 69], 'KeyMovType2': [38, 40, 37, 39, 256, 257, 258], 'KeyAttType3': [69, 65, 68, 83, 81, 90, 67, 87], 'KeyDefType3': [69, 65, 68, 83, 81, 90, 67, 87], 'KeyMovType3': [38, 40, 37, 39, 256, 257, 258], 'KeyAttType4': [80, 75, 186, 76, 73, 190, 191, 79], 'KeyDefType4': [80, 75, 186, 76, 73, 190, 191, 79], 'KeyMovType4': [87, 83, 65, 68, 256, 257, 258], 'KeyDefType5': [87, 65, 68, 32, 81, 90, 67, 69], 'KeyDefType6': [87, 65, 83, 32, 68, 90, 67, 69], 'KeyDefType7': [69, 65, 68, 32, 81, 90, 67, 87], 'KeyDefType8': [80, 75, 186, 32, 73, 190, 191, 79], 'KeyAttType5': [87, 65, 68, 83, 81, 90, 67, 69], 'KeyAttType6': [87, 65, 68, 83, 81, 90, 67, 69], 'KeyAttType7': [69, 65, 68, 83, 81, 90, 67, 87], 'KeyAttType8': [80, 75, 186, 76, 73, 190, 191, 79], 'KeyMovType5': [38, 40, 37, 39, 256, 257, 258], 'KeyMovType6': [38, 40, 37, 39, 256, 257, 258], 'KeyMovType7': [38, 40, 37, 39, 256, 257, 258], 'KeyMovType8': [87, 83, 65, 68, 256, 257, 258]}


def control_options(saved=None):
    """Complete partial profiles; never hand native input an empty key map."""
    saved = saved if isinstance(saved, dict) else {}
    out = {}
    for key, default in CONTROL_DEFAULTS.items():
        value = saved.get(key)
        if isinstance(default, list):
            valid = (isinstance(value, list) and len(value) == len(default)
                     and all(type(n) is int and 0 <= n <= 65535 for n in value))
            out[key] = list(value if valid else default)
    controller = saved.get("Controller", 0)
    out["Controller"] = controller if type(controller) is int and controller in (0, 1) else 0
    layout = saved.get("Type", 0)
    count = 8 if out["Controller"] == 0 else 3
    out["Type"] = layout if type(layout) is int and 0 <= layout < count else 0
    return out


# Cai dat ho tro mac dinh (OptionAISettingScreen): textList theo thu tu
# NewbieCustom [Nguoi moi, Tu chinh] | AutoSwitching [Tu chinh, Chi bong bong, Tu dong]
# AutoSwitchMoveing/PassAssist/ShotAssist/CrossAssist [Tu chinh, TB, Tu dong]
# PassPowerAssist/ThroughPassAssist/LobPassAssist [Tu chinh, Tu dong]
AI_DEFAULTS = {"NewbieCustom": 1, "AutoSwitching": 1, "AutoSwitchMoveing": 2, "PassAssist": 2,
               "PassPowerAssist": 0, "ThroughPassAssist": 1, "LobPassAssist": 1, "ShotAssist": 2, "CrossAssist": 1}
AI_DEFAULTS_VERSION = 1


def ai_defaults_once(st):
    """Ap AI_DEFAULTS mot lan cho ho so (ke ca ho so cu); sau do nguoi choi tu chinh tu do."""
    if st.get("ai_defaults_v") == AI_DEFAULTS_VERSION:
        return False
    opt = st.setdefault("option", {})
    ai = dict(opt.get("aiOption") or {})
    ai.update(AI_DEFAULTS)
    ai["Custom"] = {**(ai.get("Custom") or {}), **{k: v for k, v in AI_DEFAULTS.items() if k != "NewbieCustom"}}
    opt["aiOption"] = ai
    st["ai_defaults_v"] = AI_DEFAULTS_VERSION
    return True


def ep_account(query, body):
    """GET /account (AccountProxy.accountData): responses/account_base.json, with the options
    the client saved through PUT /account/option layered over `option`."""
    with open(os.path.join(RESPONSES, "account_base.json"), encoding="utf-8") as f:
        acc = json.load(f)
    st = _state()
    if ai_defaults_once(st):
        _save(st)
    acc["option"]["aiOption"] = {**AI_DEFAULTS, **(acc["option"].get("aiOption") or {})}
    acc["option"].update(st.get("option", {}))
    acc["option"]["controlOption"] = control_options(acc["option"].get("controlOption"))
    acc["option"]["soundOption"] = {**SOUND_DEFAULTS, **(acc["option"].get("soundOption") or {})}
    acc["option"]["cameraOption"] = {**CAMERA_DEFAULTS, **(acc["option"].get("cameraOption") or {})}
    return _json(acc)


# Moi thiet bi giu kieu dieu khien rieng (ban phim "FO2 moi", tay cam "Wing"... truoc day reset lan
# nhau). controlOption chi co MOT Type = kieu cua thiet bi dang dung: ban phim 0..7, tay cam 0..2.
# Man Dieu khien (OptionControlSettingScreen.onControllerSelectorChanged) dat Type 0 o lan doi thiet
# bi dau tien moi lan mo; huong dan tan thu doi Controller ma giu Type cua thiet bi kia. Server nho
# st["controlType"] {thiet bi: kieu cuoi} va tra lai kieu do khi gap mot lan "reset" nhu vay.
CONTROL_TYPE_MAX = {0: 7, 1: 2}


def remember_control_type(st, ctl, prev, raw_type=None):
    """ctl (controlOption da chuan hoa tu client) voi kieu da nho cua thiet bi nay khi man hinh vua
    reset no; prev = controlOption da luu truoc do; raw_type = Type client gui (truoc khi kep)."""
    types = st.setdefault("controlType", {})
    types.setdefault(str(prev["Controller"]), prev["Type"])
    c, t = ctl["Controller"], ctl["Type"]
    top = CONTROL_TYPE_MAX.get(c, 0)
    keys = lambda o: [o.get(f"{'Key' if c == 0 else 'Joy'}{k}Type1") for k in ("Att", "Def", "Mov")]
    reset = c != prev["Controller"] and t == 0 and keys(ctl) == keys(prev)  # khong phai sua phim kieu 0
    out_of_range = raw_type is not None and not (type(raw_type) is int and 0 <= raw_type <= top)
    if reset or out_of_range:
        t = types.get(str(c), 0)
    t = t if type(t) is int and 0 <= t <= top else 0
    types[str(c)] = t
    return {**ctl, "Type": t}


def ep_account_option(query, body):
    """Save complete control profiles and return the option resource expected by the UI."""
    req = json.loads(body or b"{}")
    st = _state()
    options = st.setdefault("option", {})
    prev_ctl = control_options(options.get("controlOption"))
    options.update({k: v for k, v in req.items() if k.endswith("Option") and isinstance(v, dict)})
    options["controlOption"] = control_options(options.get("controlOption"))
    if isinstance(req.get("controlOption"), dict):
        options["controlOption"] = remember_control_type(st, options["controlOption"], prev_ctl,
                                                         req["controlOption"].get("Type"))
    options["cameraOption"] = {**CAMERA_DEFAULTS, **(options.get("cameraOption") or {})}
    _save(st)
    return _json({"result": True, "resources": [{"uri": "/account/option", "body": options}]})


DUP_PLAYER_REASON = "Không được đưa cùng một cầu thủ (dù khác mùa thẻ) vào đội hình 2 lần."


def has_dup_player(positions):
    """Cung mot cau thu that (spid % 1000000 = playerid, khac mua the) xuat hien 2 lan trong 18 nguoi."""
    pids = [int(p["spid"]) % 1000000 for p in positions if p.get("spid") is not None]
    return len(pids) != len(set(pids))


def ep_coach_squad(query, body):
    """PUT /coach/me/squad {squad:{positions,formation,...}, kickers, captain} from Tim > Formasi.
    Player ids are instance ids (iid)."""
    req = json.loads(body or b"{}")
    st = _state(); slot = _default_slot(st); team = st["teams"].get(str(slot))
    sq = req.get("squad") or {}
    spid_of = {iid(p): p["spid"] for p in team["players"]} if team is not None else {}
    positions = [{"id": str(p["id"]), "spid": spid_of[str(p["id"])], "pos": p["pos"],
                  **({"starting": p["starting"]} if p.get("starting") else {})}
                 for p in sq.get("positions") or [] if str(p.get("id")) in spid_of and p.get("pos", 99) <= POS_SUB]
    # a client still holding old instance ids (e.g. from before a card migration) must not
    # wipe the XI: keep the stored squad unless the new one has 11 known starters
    if (sum(p["pos"] < POS_SUB for p in positions) == 11
            and len({p["id"] for p in positions}) == len(positions)
            and len({p["pos"] for p in positions if p["pos"] < POS_SUB}) == 11):
        if has_dup_player(positions):
            return _json({"result": False, "reason": DUP_PLAYER_REASON, "resources": coach_me_resources(st, slot)})
        squad_presets(team)
        selected = req.get('presetidx', team.get('presetidx', 0))
        if type(selected) is not int or not 0 <= selected < 3:
            return _json({'result': False, 'reason': 'INVALID_SQUAD'})
        team['presetidx'] = selected
        team["squad"]["positions"] = positions
        for k in ("formation", "workrate"):
            if k in sq:
                team["squad"][k] = sq[k]
        if str(req.get("captain")) in spid_of:
            team["squad"]["captain"] = str(req["captain"])
        if req.get("kickers") and all(str(k) in spid_of for k in req["kickers"] if k):
            team["squad"]["kickers"] = [str(k) for k in req["kickers"] if k]
        squad_presets(team)
        _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


def ep_team_role(role, query, body):
    """Native Role tab saves one role at a time, separately from the formation."""
    if role != 'captain' and role not in ('0', '1', '2', '3', '4'):
        return _json({'result': False, 'reason': 'INVALID_SQUAD'})
    req = json.loads(body or b'{}')
    playerid = req.get('id')
    if not isinstance(playerid, (str, int)) or isinstance(playerid, bool):
        return _json({'result': False, 'reason': 'INVALID_SQUAD'})
    playerid = str(playerid)
    st = _state()
    slot = _default_slot(st)
    team = st['teams'].get(str(slot))
    if team is None:
        return _json({'result': False, 'reason': 'INVALID_SQUAD'})
    repair_squad(team)
    starters = {p['id'] for p in team['squad']['positions'] if p['pos'] < POS_SUB}
    if playerid not in starters:
        return _json({'result': False, 'reason': 'INVALID_SQUAD'})
    if role == 'captain':
        team['squad']['captain'] = playerid
    else:
        team['squad']['kickers'][int(role)] = playerid
    team['captain'] = team['squad']['captain']
    team['kickers'] = list(team['squad']['kickers'])
    squad_presets(team)
    _save(st)
    return _json({'result': True, 'resources': coach_me_resources(st, slot)})


def roster(teamid):
    """{summary, team} of a club: the gen_teams.py roster file if there is one, else built
    the same way straight from fifadb (league opponents)."""
    if teamid in _NATIONAL_TEAMS:
        with closing(_db()) as c:
            # doi tuyen that (cau thu co nationalteamid = doi nay, phan lon dang o CLB); it hon 18
            # nguoi thi nhu truoc: cac cau thu manh nhat cung quoc tich
            r = gen_teams.team(c, teamid, nationalteam=True)
            if r is None or len(r["team"]["players"]) < gen_teams.SELECTABLE:
                r = gen_teams.team(c, teamid, nationality=_NATIONAL_TEAMS[teamid])
        if r is not None:
            info = _CLUB_TEAMS[teamid]
            nation = _CLUB_NATIONS[_NATIONAL_TEAMS[teamid]]
            r["summary"].update(name=info["teamname"], league={"id":78},
                                nation={"id":nation["nationid"], "name":nation["nationname"]}, rival={"name":""})
            return r
    if teamid in WC_NATION_SQUADS and teamid not in gen_teams.SQUAD_LISTS:
        gen_teams.SQUAD_LISTS[teamid] = nation_squad_spids(WC_NATION_SQUADS[teamid])
    if teamid in gen_teams.NATIONAL_SQUADS or teamid in WC_NATION_SQUADS:
        with closing(_db()) as c:
            r = gen_teams.team(c, teamid)
        grade, level = NATIONAL_SQUAD_GRADE
        r["team"]["players"] = [dict(p, grade=grade, levelinfo={"level": level, "exp": 0}) for p in r["team"]["players"]]
        nid = WC_NATION_SQUADS.get(teamid, 192)
        nat = _CLUB_NATIONS.get(nid) or {"nationid": nid, "nationname": ""}
        name = "Việt Nam" if teamid == 113666 else (_CLUB_TEAMS.get(teamid) or {}).get("teamname") or nat["nationname"]
        r["summary"].update(name=name, league={"id": 78}, rival={"name": ""},
                            nation={"id": nat["nationid"], "name": nat["nationname"]})
        return r
    f = os.path.join(RESPONSES, f"static_info_authentic_team_all_{teamid}.json")
    if os.path.isfile(f):
        with open(f) as fh:
            return json.load(fh)
    with closing(_db()) as c:
        return gen_teams.team(c, teamid) or {"summary": {}, "team": {"players": [], "squad": {"positions": [], "kickers": []}}}


NATIONAL_SQUAD_GRADE = (10, 20)  # doi tuyen tu dung (World Cup): moi the +10, LV 20 (user 2026-11-09)
# Ao 3D: engine lay ao theo ma doi (teamid << 5 | loai) -> doi khong co ao trong client duoc file ao roi
# data/sceneassets/kit/kit_<doi>_*.rx3 (tools/borrow_kits.py). KIT_ALIAS chi con cho truong hop thieu file roi.
KIT_ALIAS = {}
# Doi tuyen chau A them cho World Cup (user: "chau A con it quoc gia, tuong tu VN, tranh trung cau thu"):
# ma doi client -> quoc tich; doi hinh = moi cau thu quoc tich do 1 the OVR cao nhat (4 GK + 26 nguoi khac).
WC_NATION_SQUADS = {1411: 163, 974: 167, 111114: 183, 111115: 161, 111512: 162, 111533: 188}  # Nhat, Han, Saudi, Iran, Iraq, Thai


@functools.lru_cache(maxsize=None)
def nation_squad_spids(nationality):
    with closing(_db()) as c:
        best = {}
        for s, pid, pos, o in c.execute(f"SELECT p.spid, p.playerid, p.preferredposition1, {POSOVR} FROM players p "
                                        "WHERE p.nationality = ? AND p.spid >= 1000000", (nationality,)):
            if o is not None and (pid not in best or o > best[pid][2]):
                best[pid] = (s, pos, o)
    rows = sorted(best.values(), key=lambda r: -r[2])
    gk = [r for r in rows if r[1] == 0][:4]
    return tuple(r[0] for r in gk + [r for r in rows if r[1] != 0][:30 - len(gk)])


def roster_team(teamid):
    """A club as a stored team, for AI opponents."""
    return roster(teamid)["team"]


MAX_GRADE = 20  # cap the toi da (+0..+20); PLAYERGRADE.json co 21 dong
COM_GRADE = min(MAX_GRADE, max(0, int(os.environ.get("FO3_COM_GRADE", "10"))))


def match_side(raw, teamid, user=None, tactics=None, mode="friendly", keep_grades=False):
    """match.side.<left|right>: PlayerListUtil.generateCaptainFromMatchInfo reads side.team
    {teamid, squad, players}, side.players (the users: id, name, levelinfo), MatchProxy
    fetchMatchSide* read squad, tactics, ready. tactics is the preset ARRAY (as
    /game/my/tactic/all): native 0x949170 iterates it ({id, bus, cc, def, teammentality})."""
    # Match kits use five low bits for the type (native fallback: teamid << 5).
    # Lobby/freeroam resources retain their separate ten-bit encoding.
    kt = KIT_ALIAS.get(int(teamid), int(teamid))
    kit = {"availables": {k: {"id": kt << 5 | t}
                          for k, t in (("home", 0), ("away", 1), ("goalie", 2))}}
    if user is None and not keep_grades:
        # AI side: the cong + cap cau thu theo che do (GM Tool > Suc manh BOT, bot_settings.py;
        # mac dinh +10, LV 23) de BOT van du suc voi doi da nang the
        import bot_settings
        bs = bot_settings.for_mode(mode)
        raw = {**raw, "players": [{**p, "grade": bs["grade"], "levelinfo": {"level": bs["level"], "exp": 0}}
                                  for p in raw.get("players", [])]}
    team = owned_team(raw)
    team.update(teamid=teamid, summary=team_summary(team, teamid), kit=kit)
    # "Doi doi hinh" truoc tran (SquadScreenHandler.initializeFormationComboBox) doc
    # side.team.formations: owned_team de [] -> danh sach so do trong rong.
    if len(team.get("formations") or []) < 2:  # doi COM/World Cup chi co 1 so do tu gen_teams
        team["formations"] = team_formations(teamid)
    # team color for the user's side only: an AI side is a whole club (+4 for free)
    team["teamcolors"] = active_teamcolors(team) if user else []
    # ready: a user side starts unready -- MatchController.sendReady skips the PUT .../ready
    # (no kick-off) when isMySideReady is already true; AI sides are always ready.
    return {"team": team, "squad": team["squad"], "players": [user] if user else [],
            "tactics": tactics or [default_tactic(0)], "ready": user is None, "kit": kit, "club": {}}


# gamesettings.halfLength, in 1/10 minute per half (the game setting screen shows
# halfLength/10*2 "menit"): 30 = a 6-minute match.
HALF_LENGTH = int(os.environ.get("FO3_HALF_LENGTH", "30"))


# Luat thay nguoi hien hanh (user 2026-11-09): 5 luot thay, ca 7 du bi deu duoc dung (truoc: native mac dinh 3 / 5).
# 7 = toi da cua doi hinh FO3 (11 + 7 SUB, man Quan ly doi). Doc boi native ApplyGameSettings (0x946FD0).
MATCH_BENCH, MATCH_SUBS = 7, 5


def new_match(mtype, left, right, **extra):
    """A /match/<id> resource (MatchProxy, base uri /match). MatchController.onMatchChangeReceived
    runs processMatch* over: side.X {ready, club, players, squad, team{teamid, players, captain,
    kit, summary.league}}, elo.X.rating, gamesettings {kit, ball, stadium, tod, weather,
    halfLength, difficulty}, result, reward, boostweekid, stage (created|squad|playing|canceled).
    These run inside a native->AS3 callback: a TypeError there aborts the client (0xDEAD)."""
    mid = f"{int(time.time()):08x}{os.urandom(8).hex()}"  # Mongo-style id
    lt, rt = left["team"]["teamid"], right["team"]["teamid"]
    match = {"id": mid, "type": mtype, "created": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
             "stage": "created", "side": {"left": left, "right": right},
             "elo": {"left": {"rating": 1000}, "right": {"rating": 1000}},
             "result": None, "reward": None, "boostweekid": 0,
             # native ApplyGameSettings gets this object as-is.
             # kit: [left uniform id, right uniform id] as STRINGS: AS3 reads them in
             # processMatchSideStandingPlayerResource, native (0x9480D0) with asCString
             # home: Boolean (MatchProxy.isLeftTeamHome = gamesettings.home as Boolean)
        "gamesettings": {"home": True, "simulated": False, "canHaveReferee": False,
                              "stadiumchange": False, "timechange": False, "weatherchange": False,
                              "matchtimechange": False, "cardstrictness": 0, "foulstrictness": 0,
                              # kit: per side the KIT TYPE ("home"/"away"); native (0x948260) loads
                              # side kit.availables[<type>].id -- uniform ids here = checkerboard kits.
                              # ball.availables feeds MatchProxy.ballsDataProvider; empty -> the
                              # game setting screen's ball stepper reads item 0 = null (UI error).
                              # 0 = the ball the practice match loads (id -> db.ball model).
                              "kit": ["home", "away"],
                              "ball": {"id": 0, "availables": [{"id": 0}]},
                              # stadium 112 = the lobby stadium (data/sceneassets/stadium/stadium_112.rx3
                              # is known to load); null -> crash while loading the GAME scene
                              "stadium": 112, "tod": 0, "weather": 0, "difficulty": 0,
                              "halfLength": HALF_LENGTH,
                              # the rest is what native ApplyGameSettings (0x946FD0) reads; values are
                              # its own defaults (0x959050). Missing gameSpeed broke the match clock.
                              "gameSpeed": 1, "crowd": 100, "pitchWear": 1, "refereeIdSelector": 112,
                              "subRefereeIds": [178, 157], "disableMomentum": False, "bookingsOn": True,
                              "offsidesOn": True, "injuriesOn": True, "foulsOn": True, "fatigueOn": True,
                              "subOnBench": MATCH_BENCH, "availableSubs": MATCH_SUBS, "drawMode": 0, "gameType": mtype,
                              "shootoutmode": False, "prevScore": {"home": 0, "away": 0}}}
    match.update(extra)
    return match


# --------------------------------------------------------------------------- #
# Cai dat truoc tran: san, thoi diem (ngay/dem), thoi tiet, thoi luong hiep, bong. Chi man cai dat
# cua che do Xep hang (MultiplayGameSettingScreen) co nut doi (gamesettings stadiumchange/timechange/
# weatherchange/matchtimechange, danh sach san = GET /stadium/list, doi = PUT /match/<id>/gamesettings);
# Giai dau/Cup/World Cup/Giao huu chi hien nhan -> lay theo lua chon gan nhat (st["matchprefs"],
# chinh o GM Tool > Tran dau). Gia tri (theo ban port fo3-indo-server, kiem tra tren client):
# - san can model + texture ngay/dem + khan gia + stadiumdb + dong DB; native khong kiem tra id.
STADIUMS_SAFE = (1, 2, 5, 13, 14, 26, 28, 29, 30, 32, 33, 34, 35, 41, 100, 112, 113, 115, 116, 135, 137,
                 147, 149, 153, 156, 157, 158, 175, 176, 178, 179, 180, 181, 182, 183, 192, 193, 194, 195,
                 196, 197, 212, 228, 229, 246, 247, 248, 249, 261, 262, 264, 265, 326, 327, 329, 330, 331,
                 332, 333, 335, 336, 337, 341, 343, 344, 345, 347, 348, 349, 355)
STADIUM_DEFAULT = 112  # san o sanh: moi tran deu dung san nay truoc day
# - tod: 1 ban ngay, 3 ban dem (moi san chi co 2 bo anh sang nay)
TODS = (1, 3)
# - weather: 0 dep troi, 1 mua, 2 tuyet, 3 am u (ngay) / chang vang (dem)
WEATHERS = (0, 1, 2, 3)
WEATHERS_RANDOM = (0, 1, 3)  # "ngau nhien": dep, mua hoac am u (tuyet chi khi chon)
HALF_LENGTHS = tuple(sorted({30, 40, 50, 60, HALF_LENGTH}))  # 6, 8, 10, 12 phut (+ FO3_HALF_LENGTH)
# bong co model (data_2.big ball_<id>.rx3), ten (BALL_NAME_TYPE<id>) va anh (externalAssets/balls)
BALLS = (0, 6, 23, 34, 39, 40, 41, 45, 69)
MATCH_PREFS = {"stadium": STADIUMS_SAFE + ("home", "random"), "tod": TODS + ("random",),
               "weather": WEATHERS + ("random",), "halfLength": HALF_LENGTHS, "ball": BALLS}


def match_prefs(st):
    """Lua chon truoc tran: stadium id | "home" (san doi chu nha) | "random", tod | "random",
    weather | "random", halfLength, ball. Chua dat = nhu truoc day (112, ngay, dep troi, HALF_LENGTH, bong 0)."""
    p = {"stadium": STADIUM_DEFAULT, "tod": TODS[0], "weather": 0, "halfLength": HALF_LENGTH, "ball": 0}
    p.update({k: v for k, v in (st.get("matchprefs") or {}).items()
              if not isinstance(v, bool) and v in MATCH_PREFS.get(k, ())})
    return p


def apply_prematch(st, match, rnd=random):
    """San / thoi diem / thoi tiet / thoi luong / bong cua tran moi theo match_prefs. Goi sau khi da
    dat gamesettings.home ("home" = san cua doi chu nha)."""
    p, gs = match_prefs(st), match["gamesettings"]
    stadium = p["stadium"]
    if stadium == "home":
        stadium = club_stadium(match["side"]["left" if gs.get("home", True) else "right"]["team"]["teamid"])
    elif stadium == "random":
        stadium = rnd.choice(STADIUMS_SAFE)
    pick = lambda v, opts: rnd.choice(opts) if v == "random" else v
    gs.update(stadium=stadium if stadium in STADIUMS_SAFE else STADIUM_DEFAULT,
              tod=pick(p["tod"], TODS), weather=pick(p["weather"], WEATHERS_RANDOM), halfLength=p["halfLength"],
              ball={"id": p["ball"], "availables": list(BALLS)},  # so nguyen: MatchProxy doi thanh BALL_NAME_TYPE<id>
              stadiumchange=True, timechange=True, weatherchange=True, matchtimechange=True)


def ep_stadium_list(query, body):
    """GET /stadium/list (MatchProxy.requestStadiumInfo): [{id, name, todWeatherAvailables}]."""
    return _json(stadium_choices())


def stadium_choices():
    """[{id, name, todWeatherAvailables}] cua STADIUMS_SAFE theo ten: ten trong client
    (STADIUMNAMES_ENG), neu khong co thi ten trong DB cua client."""
    names, db = client_stadium_names(), stadium_table()[0]
    rows = [{"id": s, "name": names.get(str(s)) or re.sub(r"\s*\(.*\)$", "", (db.get(s) or {}).get("name") or f"Sân {s}"),
             "todWeatherAvailables": {str(t): list(WEATHERS) for t in TODS}} for s in STADIUMS_SAFE]
    return sorted(rows, key=lambda r: r["name"].lower())


def client_big(path, name):
    """Mot file trong kho .big cua client (tools/re/bigdir.py), hoac None."""
    try:
        if os.path.join(HERE, "tools", "re") not in sys.path:
            sys.path.append(os.path.join(HERE, "tools", "re"))  # append: tools/re/abc.py khong de len thu vien abc
        import bigdir
        return bigdir.read(path, name)
    except (OSError, KeyError, ValueError, ImportError) as e:
        print(f"client archive {name}: {e!r}", flush=True)
        return None


def client_localization(name):
    """{key: text} cua data/ui/localization/<name> (file roi neu co, khong thi trong <lang>.big)."""
    loose = os.path.join(GAME, "data", "ui", "localization", name)
    try:
        if os.path.isfile(loose):
            with open(loose, "rb") as f:
                return json.loads(f.read().decode("utf-8-sig"))
        raw = client_big(os.path.join(GAME, "data", "ui", "localization", LANGUAGE + ".big"),
                         "data/ui/localization/" + name)
        return json.loads(raw.decode("utf-8-sig")) if raw else {}
    except (OSError, ValueError) as e:
        print(f"localization {name}: {e!r}", flush=True)
        return {}


@functools.lru_cache(maxsize=None)
def ball_names():
    """{ball id: ten trong goi ngon ngu cua client (BALL_NAME_TYPE<id>)} cua BALLS."""
    s = client_localization(f"LANGUAGESTRING_{LANGUAGE.upper()}.json")
    return {b: s.get(f"BALL_NAME_TYPE{b}") or f"Bóng {b}" for b in BALLS}


@functools.lru_cache(maxsize=None)
def client_stadium_names():
    """{"<id>": ten} cua STADIUMNAMES_<LANG>.json (Localization.STADIUMNAME)."""
    return client_localization(f"STADIUMNAMES_{LANGUAGE.upper()}.json")


STADIUMS_CACHE = os.path.join(HERE, "gamedata", "stadiums.json")


@functools.lru_cache(maxsize=None)
def stadium_table():
    """({stadium id: {name, yearbuilt, capacity}}, {club teamid: stadium id}) tu fifa_ng_db cua
    client (data/data_0.big), luu cache gamedata/stadiums.json. San cua CLB = san co hometeamid,
    neu khong thi dong teamstadiumlinks (teamid o do = id that - 1)."""
    try:
        with open(STADIUMS_CACHE, encoding="utf-8") as f:
            t = json.load(f)
    except (OSError, ValueError):
        try:
            if os.path.join(HERE, "tools", "re") not in sys.path:
                sys.path.append(os.path.join(HERE, "tools", "re"))  # append: tools/re/abc.py khong de len thu vien abc
            import bigdir, eadb
            big = os.path.join(GAME, "data", "data_0.big")
            db = bigdir.read(big, "data/db/fifa_ng_db.db")
            meta = bigdir.read(big, "data/db/fifa_ng_db-meta.xml").decode("utf-8", "replace")
            rows, links = eadb.rows(db, meta, "stadiums"), eadb.rows(db, meta, "teamstadiumlinks")
        except (OSError, KeyError, ValueError, ImportError) as e:
            print("stadiums unavailable:", e, flush=True)
            return {}, {}
        t = {"stadiums": {str(r["stadiumid"]): {"name": r["name"].lstrip("_"), "yearbuilt": 1800 + r["yearbuilt"],
                                                "capacity": r["capacity"]} for r in rows},
             "teams": {**{str(r["teamid"] + 1): r["stadiumid"] for r in links},
                       **{str(r["hometeamid"]): r["stadiumid"] for r in rows if r["hometeamid"]}}}
        os.makedirs(os.path.dirname(STADIUMS_CACHE), exist_ok=True)
        with open(STADIUMS_CACHE, "w", encoding="utf-8") as f:
            json.dump(t, f, ensure_ascii=False)
    return {int(k): v for k, v in t["stadiums"].items()}, {int(k): v for k, v in t["teams"].items()}


def club_stadium(teamid):
    """San nha cua CLB (0: khong co trong DB client, vd. doi tuyen quoc gia)."""
    return stadium_table()[1].get(int(teamid or 0), 0)


def ep_match_campaign(query, body):
    """POST /match/campaign (tutorial > Training Ground): MatchController
    .onMatchPracticeResponseReceived registers resources[] and fires ON_PRACTICE_STARTED with
    the match body -> ApplyGameSettings + StartSingleMatch (a local match in the client)."""
    st = _state(); slot = _default_slot(st); me = coach_me(st, slot)
    user = {"id": me["id"], "name": me["name"], "levelinfo": me["levelinfo"]}
    ai = 243 if me["teamid"] != 243 else 241
    # type must be a non-PvP one (MatchController.isPvPMatchType: League_league, Cup_PvE,
    # WorldCup_PvE, WorldCup_Live, League_2_league, League_2_league_MGR); anything else makes
    # stage "created" call connectToTheSession -> native LobbyManager::ConnectSession, which
    # needs relayServer.address (crash without it).
    match = new_match("League_league", match_side(st["teams"][str(slot)], me["teamid"], user, _tactics(st)[1]),
                      match_side(roster_team(ai), ai, mode="friendly"))
    apply_prematch(st, match)
    return _json({"result": True, "resources": [{"uri": f"/match/{match['id']}", "body": match}]})


def roster_summary(teamid):
    """Static team info (name, league, nation, rival) written by gen_teams.py."""
    s = club_summary_response(teamid)["summary"]
    return {k: s[k] for k in ("name", "league", "nation", "rival") if k in s}


# --------------------------------------------------------------------------- #
# Player cards. Ratings come from the client's own fifadb, EP prices from fifaaddict.com's
# FO3 DB (Indonesia server, grade +1; gamedata/fa_prices.json, see tools/fa_sync.py).
# --------------------------------------------------------------------------- #
# all-attribute boost by grade +0..+20 (= responses/static_db_PLAYERGRADE.json): fifaaddict's
# playerEnchantList, e.g. World Best Neymar 83 base shows 88 at +1 and 93 at +5; +11..+20 ours
GRADE_BOOST = (0, 5, 6, 7, 8, 10, 12, 14, 17, 20, 24, 28, 32, 36, 40, 44, 48, 52, 55, 58, 60)
# EP price of grade g relative to +1: geometric mean over 16 fifaaddict cards (Indonesia server);
# +11..+20: x2.3 per grade (ours), card_price caps them under a rising ceiling (price_ladder: strictly increasing)
# +1..+10: bang gia that FO3 (fifaaddict). +11..+20 (tu them): truoc x2.3 moi cap -> the +20 toi ~1000B; nay x1.5
GRADE_PRICE = (0, 1, 2.65, 7.1, 19.6, 53, 162, 420, 1090, 2680, 6290) + tuple(round(6290 * 1.5 ** k) for k in range(1, 11))
FA_PRICES = os.path.join(HERE, "gamedata", "fa_prices.json")
_FA = None
_OVR = {}
_OVR_ALL = []  # [True] khi _OVR da nap ca bang players
_OVR_LOCK = threading.Lock()


def fa_prices():
    """{spid: Indonesia-server EP price at +1 (0 = listed on fifaaddict without a price)};
    {} when the file is missing."""
    global _FA
    if _FA is None:
        try:
            with open(FA_PRICES) as f:
                _FA = {int(k): int(v) for k, v in json.load(f).items()}
        except OSError:
            _FA = {}
    return _FA


# The rating the client shows for a card ("Atribut"/OVR, as on fifaaddict) is Enums.calcPosOvr
# at its preferredposition1: floor(sum(stat * weight) / 100) with STAT_POSOVRS weights below
# (fifadb's overallrating column runs ~5 higher for 2017 cards). POS_ROLE[Enums.PLAYERPOSITION].
ROLE_WEIGHTS = {
    "GK": {"gkdiving": 21, "gkhandling": 21, "gkkicking": 5, "gkpositioning": 21, "gkreflexes": 21, "reactions": 11},
    "SW": {"aggression": 8, "ballcontrol": 5, "headingaccuracy": 10, "jumping": 4, "marking": 15, "reactions": 5,
           "shortpassing": 5, "slidingtackle": 15, "standingtackle": 15, "strength": 10, "interceptions": 8},
    "WB": {"acceleration": 4, "ballcontrol": 8, "crossing": 12, "dribbling": 4, "marking": 7, "reactions": 8, "shortpassing": 10,
           "slidingtackle": 11, "sprintspeed": 6, "stamina": 10, "standingtackle": 8, "interceptions": 12},
    "FB": {"acceleration": 5, "ballcontrol": 7, "crossing": 9, "headingaccuracy": 4, "marking": 8, "reactions": 8, "shortpassing": 7,
           "slidingtackle": 14, "sprintspeed": 7, "stamina": 8, "standingtackle": 11, "interceptions": 12},
    "CB": {"aggression": 7, "ballcontrol": 4, "headingaccuracy": 10, "jumping": 3, "marking": 14, "reactions": 5, "shortpassing": 5,
           "slidingtackle": 17, "sprintspeed": 2, "standingtackle": 10, "strength": 10, "interceptions": 13},
    "DM": {"aggression": 5, "ballcontrol": 10, "longpassing": 10, "marking": 9, "reactions": 7, "shortpassing": 14, "slidingtackle": 5,
           "stamina": 6, "standingtackle": 12, "strength": 4, "interceptions": 14, "vision": 4},
    "WM": {"acceleration": 7, "ballcontrol": 13, "crossing": 10, "dribbling": 15, "finishing": 6, "longpassing": 5, "positioning": 8,
           "reactions": 7, "shortpassing": 11, "sprintspeed": 6, "stamina": 5, "vision": 7},
    "CM": {"ballcontrol": 14, "dribbling": 7, "finishing": 2, "longpassing": 13, "longshots": 4, "positioning": 6, "reactions": 8,
           "shortpassing": 17, "stamina": 6, "standingtackle": 5, "interceptions": 5, "vision": 13},
    "AM": {"acceleration": 4, "agility": 3, "ballcontrol": 15, "dribbling": 13, "finishing": 7, "longpassing": 4, "longshots": 5,
           "positioning": 9, "reactions": 7, "shortpassing": 16, "sprintspeed": 3, "vision": 14},
    "CF": {"acceleration": 5, "ballcontrol": 15, "dribbling": 14, "finishing": 11, "headingaccuracy": 2, "longshots": 4, "positioning": 13,
           "reactions": 9, "shortpassing": 9, "shotpower": 5, "sprintspeed": 5, "vision": 8},
    "W": {"acceleration": 7, "agility": 3, "ballcontrol": 14, "crossing": 9, "dribbling": 16, "finishing": 10, "longshots": 4,
          "positioning": 9, "reactions": 7, "shortpassing": 9, "sprintspeed": 6, "vision": 6},
    "ST": {"acceleration": 4, "ballcontrol": 10, "dribbling": 7, "finishing": 18, "headingaccuracy": 10, "longshots": 3, "positioning": 13,
           "reactions": 8, "shortpassing": 5, "shotpower": 10, "sprintspeed": 5, "strength": 5, "volleys": 2},
}
POS_ROLE = "GK SW WB FB CB CB CB FB WB DM DM DM WM CM CM CM WM AM AM AM CF CF CF W ST ST ST W".split()
POSOVR = "(CASE p.preferredposition1 {} END)".format(" ".join(  # SQL: client rating of row p
    f"WHEN {i} THEN ({' + '.join(f'{w} * p.{k}' for k, w in ROLE_WEIGHTS[r].items())}) / 100" for i, r in enumerate(POS_ROLE)))


def card_ratings(spids):
    """{spid: (client rating, fifadb overallrating)} (cached)."""
    spids = [int(s) for s in spids]
    need = [s for s in set(spids) if s not in _OVR]
    if need and not _OVR_ALL:
        # lan dau: nap ca bang 1 lan (~60k dong, <1 s). Truoc day moi the 1 ket noi + 1 truy van ->
        # Cua hang / /coaches luc may lanh mat 10-30 s (gia goi, draft_top) va giu STATE_LOCK.
        with _OVR_LOCK:
            if not _OVR_ALL:
                with closing(_db()) as c:
                    _OVR.update({s: (o, d) for s, o, d in c.execute(
                        f"SELECT p.spid, {POSOVR}, p.overallrating FROM players p")})
                _OVR_ALL.append(True)
        need = [s for s in need if s not in _OVR]
    if need:  # khong co trong fifadb: (0, 0) nhu truoc
        with closing(_db()) as c:
            for i in range(0, len(need), 500):
                chunk = need[i:i + 500]
                rows = {s: (o, d) for s, o, d in c.execute(
                    f"SELECT p.spid, {POSOVR}, p.overallrating FROM players p WHERE p.spid IN ({','.join('?' * len(chunk))})", chunk)}
                _OVR.update({s: rows.get(s, (0, 0)) for s in chunk})
    return {s: _OVR[s] for s in spids}


def card_ovrs(spids):
    """{spid: rating the client shows}."""
    return {s: r[0] for s, r in card_ratings(spids).items()}


def card_ovr(spid):
    return card_ovrs([spid])[int(spid)]


def grade_of(p):
    """+0..+20. A club's starting players (stored without a grade) are +0: no market value
    (CoachProxy.expandTeam counts only grade > 0), not sellable; bought/packed cards start at +1."""
    return min(MAX_GRADE, max(0, int(p.get("grade") or 0)))


def player_ovr(p):
    """Card rating plus its enhancement boost."""
    return card_ovr(p["spid"]) + GRADE_BOOST[grade_of(p)]


def sig3(x):
    x = int(x)
    return round(x, 3 - len(str(x))) if x >= 1000 else x  # 3 chu so co nghia (23,6 ty giu nguyen)


def price_formula(ovr):
    """Fallback without gamedata/fa_prices.json: an early least-squares fit of fifaaddict's
    Indonesia-server prices on the client rating: exp(2.53 + 0.1113 * ovr), 65 ~ 17k, 85 ~ 160k."""
    return sig3(max(1000, math.exp(2.53 + 0.1113 * ovr)))


def on_market(spid):
    """Tradeable card: listed on fifaaddict (season-0 engine cards are not real cards)."""
    # Ban offline: mo khoa toan bo - moi the (ke ca the an / mua an / the Goc) deu mua ban duoc.
    return True


@functools.lru_cache(maxsize=None)
def rating_prices():
    """{client rating: EP price at +1}: the median fifaaddict price of all listed cards with that
    rating (ratings with 5+ priced cards), each at least 5% above the rating below; {} without
    fa_prices.json."""
    fa = fa_prices()
    by = {}
    for s, o in card_ovrs([s for s, p in fa.items() if p]).items():
        if o:
            by.setdefault(o, []).append(fa[s])
    med = {o: statistics.median(v) for o, v in by.items() if len(v) >= 5}
    table, top = {}, 0
    for o in range(min(med), max(med) + 1) if med else ():
        top = max(top * 1.05, med.get(o, 0))
        table[o] = top
    return table


def stat_price(ovr):
    """EP price at +1 of a card rated ovr."""
    t = rating_prices()
    if len(t) < 6:
        return price_formula(ovr)
    lo, hi = min(t), max(t)
    if ovr > hi:  # above every listed rating: keep the top five ratings' growth per point
        return t[hi] * (t[hi] / t[hi - 5]) ** ((ovr - hi) / 5)
    return t[max(lo, ovr)]


# Gia that cua the tu fifaaddict (FO3 VN) theo spid, gia o +1: gamedata/player_price_overrides.json
# ({"spid": gia}). The khong co trong file -> tinh theo chi so (stat_price) nhu cu.
PLAYER_PRICE_OVERRIDES = os.path.join(HERE, "gamedata", "player_price_overrides.json")
_PLAYER_PRICE_OVERRIDES = None


def player_price_overrides():
    global _PLAYER_PRICE_OVERRIDES
    if _PLAYER_PRICE_OVERRIDES is None:
        try:
            with open(PLAYER_PRICE_OVERRIDES, encoding="utf-8") as f:
                _PLAYER_PRICE_OVERRIDES = {int(k): int(v) for k, v in json.load(f).items() if int(v) > 0}
        except (OSError, ValueError, TypeError, AttributeError):
            _PLAYER_PRICE_OVERRIDES = {}
    return _PLAYER_PRICE_OVERRIDES


def card_price(spid, grade=1):
    """EP value of a card: GM Tool price (price_settings) > fifaaddict price file
    (player_price_overrides.json) > from its rating (stat_price)."""
    # Cai dat tren trang admin (price_settings.json): gia tu dat theo the, gia the +0, he so nhan gia.
    import price_settings
    ps = price_settings.get()
    g = min(MAX_GRADE, max(0, int(grade)))
    base = (ps["overrides"].get(str(int(spid))) or player_price_overrides().get(int(spid))
            or stat_price(card_ovr(spid)))
    mult = ps["multiplier"] * ps.get("season_mult", {}).get(str(int(spid) // 1000000), 1.0)  # GM Tool: he so mua
    if g == 0:  # +0 = 0 EP o ban goc
        return sig3(base * ps["zero_percent"] / 100 * mult)
    return price_ladder(base * mult)[g]


PRICE_TOP = 50_000_000_000  # gia cao nhat cua 1 the (+20) (truoc 999B - qua cao); < MAX_EP
PRICE_CAP_STEP = 1.2  # tran gia moi cap +11..+20: PRICE_TOP / 1.2^(20 - cap)


def next_price(x):
    """Gia 3 chu so co nghia ngay tren x."""
    x = int(x)
    return x + (10 ** (len(str(x)) - 3) if x >= 1000 else 1)


@functools.lru_cache(maxsize=65536)
def price_ladder(unit):
    """[gia +0 (bo qua), +1 .. +20] cua the co gia +1 = unit: tang dan NGHIEM NGAT theo cap. +11..+20 bi
    chan boi tran tang dan (PRICE_TOP / PRICE_CAP_STEP^(20 - cap)) thay vi cung 1 muc MAX_EP (truoc day
    +16..+20 cua the dat cung gia 999.999.999.999 -> bang gia Thi truong xep lon xon)."""
    out = [0] * (MAX_GRADE + 1)
    for g in range(1, MAX_GRADE + 1):
        p = sig3(unit * GRADE_PRICE[g])
        if g > 10:
            p = min(p, sig3(PRICE_TOP / PRICE_CAP_STEP ** (MAX_GRADE - g)))
        out[g] = p if g == 1 else max(p, next_price(out[g - 1]))
    return tuple(out)


def iid(p):
    """Player instance id: str(spid) for squads made by gen_teams, a hex id for cards bought,
    opened or upgraded here (so two cards of one player can coexist, e.g. for upgrades)."""
    return str(p.get("_id") or p["spid"])


def new_iid():
    return os.urandom(12).hex()  # Mongo-style


# Levels (user: "akun dan pemain belum bisa dilevel up"). Both levelinfo = {level, exp within
# the level, to = exp the level needs}. Players: the client adds level - 1 to every attribute
# (PlayerListUtil.fillPlayerStaticData: totalBonus = level - 1 + grade boost; fifaaddict lists
# LV.1-30); EXPPLAYER {level: {requiredplayerexp}}, max level = rows - 1 (getMaxLevel). Coach:
# EXPCOACH {level: {totalexp}} = exp to finish levels 1..level (TeamManagementCoachExpScreen),
# max from STATICVALUES/max_coachlevel. The original tables were server data; curves made up.
PLAYER_MAX_LEVEL, COACH_MAX_LEVEL = 30, 100  # cau thu LV.1-30 (yeu cau)
EXP_WIN, EXP_DRAW, EXP_LOSS = (300, 150), (200, 100), (100, 60)  # (coach, each player who played)


def player_need(level):
    return 100 * level


def coach_need(level):
    return 500 + 100 * (level - 1)


EXPPLAYER = {str(lv): {"requiredplayerexp": player_need(lv)} for lv in range(1, PLAYER_MAX_LEVEL + 2)}
EXPCOACH = {str(lv): {"totalexp": sum(coach_need(k) for k in range(1, lv + 1))} for lv in range(1, COACH_MAX_LEVEL + 1)}


def make_levelinfo(info, need, top):
    level = min(top, max(1, int((info or {}).get("level") or 1)))
    return {"level": level, "exp": 0 if level >= top else int((info or {}).get("exp") or 0), "to": need(level)}


def player_levelinfo(p):
    return make_levelinfo(p.get("levelinfo"), player_need, PLAYER_MAX_LEVEL)


def coach_levelinfo(coach):
    return make_levelinfo(coach.get("levelinfo"), coach_need, COACH_MAX_LEVEL)


def gain_exp(owner, n, need, top):
    """Add n exp to owner["levelinfo"] (levels up, stops at top); (prev, current) levelinfo."""
    prev = make_levelinfo(owner.get("levelinfo"), need, top)
    level, exp = prev["level"], prev["exp"] + n
    while level < top and exp >= need(level):
        exp -= need(level)
        level += 1
    owner["levelinfo"] = {"level": level, "exp": 0 if level >= top else exp}
    return prev, make_levelinfo(owner["levelinfo"], need, top)


# Tim > Formasi combobox = coach team.formations (SquadScreenHandler.initializeFormationComboBox:
# entry 0 is shown with the club's name; picking one runs changeFormation: positions[i] -> squad
# slot i, offsets[i] -> starting x/y, then SquadUtil.getBestSquadPositions). Entries come from the
# client's own formations table (data_0.big data/db/fifa_ng_db.db): teamid 1 holds the 33 common
# shapes (their sweeper variants left out, like FORMATIONS_COMMON_WITHOUT_SWEEPER), other teams
# their own. That table numbers positions without SW: client Enums.PLAYERPOSITION = code + 1 (GK 0).
# GAME directory is auto-detected above
FORMATIONS_CACHE = os.path.join(HERE, "gamedata", "formations.json")
_FORMATIONS = None


def formation_table():
    """{"common": [entry], "teams": {teamid: entry}}; entry = {formationid, formationname,
    positions, offsets}. Read once from the client, then cached in gamedata/formations.json."""
    global _FORMATIONS
    if _FORMATIONS is None:
        try:
            with open(FORMATIONS_CACHE) as f:
                _FORMATIONS = json.load(f)
        except OSError:
            _FORMATIONS = {"common": [], "teams": {}}
            try:
                sys.path.append(os.path.join(HERE, "tools", "re"))  # append: tools/re/abc.py khong de len thu vien abc
                import bigdir, eadb
                big = os.path.join(GAME, "data", "data_0.big")
                rows = eadb.rows(bigdir.read(big, "data/db/fifa_ng_db.db"),
                                 bigdir.read(big, "data/db/fifa_ng_db-meta.xml").decode("utf-8", "replace"), "formations")
            except (OSError, KeyError, ImportError) as e:
                print("Không đọc được sơ đồ chiến thuật:", e, flush=True)
                return _FORMATIONS
            entry = lambda r: {"formationid": r["formationid"], "formationname": r["formationname"],
                               "positions": [r[f"position{i}"] + (r[f"position{i}"] > 0) for i in range(11)],
                               "offsets": [{"x": round(r[f"offset{i}x"], 4), "y": round(r[f"offset{i}y"], 4)} for i in range(11)]}
            _FORMATIONS["common"] = sorted((entry(r) for r in rows if r["teamid"] == 1 and not r["issweeper"]),
                                           key=lambda e: e["formationname"])
            _FORMATIONS["teams"] = {str(r["teamid"]): entry(r) for r in rows if r["teamid"] != 1 and not r["issweeper"]}
            os.makedirs(os.path.dirname(FORMATIONS_CACHE), exist_ok=True)
            with open(FORMATIONS_CACHE, "w") as f:
                json.dump(_FORMATIONS, f)
    return _FORMATIONS


def team_formations(teamid):
    """The club's own formation first (else 4-4-2(1), the shape of the gen_teams XI), then the
    common ones."""
    t = formation_table()
    own = t["teams"].get(str(teamid)) or next((e for e in t["common"] if e["formationname"] == "4-4-2(1)"), None)
    return ([own] if own else []) + [e for e in t["common"] if own is None or e["formationid"] != own["formationid"]]


def fix_formation(team, teamid):
    """A stored team built by gen_teams carries formations = [its 4-4-2, id 1] and squad
    .formation 1, which is 3-4-1-2 in the client's table: point it at the club's own entry."""
    if team.pop("formations", None) is not None and team["squad"].get("formation") in (None, 1):
        forms = team_formations(teamid)
        if forms:
            team["squad"]["formation"] = forms[0]["formationid"]


# Team color (user: "belum ada bonus stats pada saat memenuhi syarat tim/negara/ sesion").
# TeamColorController (data/ui/libs/Module.big) runs only with features.teamcolor
# (responses/account_base.json) and GET /static/info/teamcolor {teamColors, teamColorSpecials}.
# A color: {teamcolorid, type club|nation|special, name, description (both go through
# getLocalizedString, which returns an unknown key as-is), teamid (-1 = any club/nation),
# boostgroups [{count_min, count_max, boosts [{type "ovr" = every attribute, amount}]}]}.
# The server picks the colors the starting XI meets: team.teamcolors [{teamcolorid, boostindex,
# teamid}] in coach/me and the user's match side (isValidTeamData needs the array). The client
# then boosts the XI players that fit (availableTeamColor: club = staticdata.teamid or a club in
# clubhistory, nation = nationality, special = spid in teamColorSpecials[teamid]); per player only
# the best of club / nation / special applies, they do not stack. 17 and 131 are the client's own
# "Single team" / "Satu Negara" (LANGUAGESTRING TEAMCOLOR_ATTRIBUTES_TEAM<id>).
TC_CLUB, TC_NATION, TC_SEASON = 17, 131, 50000  # a season color is 50000 + its first season code


def _tc_groups(*tiers):
    return [{"count_min": lo, "count_max": hi, "boosts": [{"type": "ovr", "amount": n}]} for lo, hi, n in tiers]


TC_CLUB_GROUPS = _tc_groups((4, 5, 1), (6, 7, 2), (8, 9, 3), (10, 11, 4))
TC_GROUPS = _tc_groups((5, 6, 1), (7, 8, 2), (9, 11, 3))  # nation, season
# Team color nang cap (type "grade": the trong doi hinh chinh co hang >= color.grade;
# availableTeamColor: player.grade >= color.grade, native 0x94EB70 doc grade la so, khong teamid).
# Moi cau thu chi nhan 1 mau nang cap tot nhat (ChooseTeamColorBoostsPerPlayer giu 1 __gradeTeamColor)
# CONG THEM vao mau CLB / quoc gia / mua / dac biet tot nhat. Ban nay co the +0..+20 (GRADE_BOOST toi
# +60 chi so) nen bac tang deu toi +20: muc theo khoang (5-6 / 7-8 / 9-11 nguoi) va muc "ca 11 nguoi
# >= +N". Ten = khoa client (EVOLVE*: "Team Color nâng cấp (+2)", "Thẻ +3".. "Thẻ +10"); +11..+20 khong
# co khoa trong client -> ten tieng Viet (getLocalizedString tra lai chuoi la nguyen van).
TC_GRADE = (  # (teamcolorid, grade, name key | ten, boostgroups)
    (10002, 2, "TEAMCOLOR_ATTRIBUTES_EVOLVE24", TC_GROUPS),
    (10005, 5, "TEAMCOLOR_ATTRIBUTES_EVOLVE10011", _tc_groups((5, 6, 2), (7, 8, 3), (9, 11, 4))),
    (10008, 8, "TEAMCOLOR_ATTRIBUTES_EVOLVE810", _tc_groups((5, 6, 3), (7, 8, 4), (9, 11, 5))),
    (10012, 12, "Team Color nâng cấp (+12)", _tc_groups((5, 6, 4), (7, 8, 5), (9, 11, 6))),
    (10016, 16, "Team Color nâng cấp (+16)", _tc_groups((5, 6, 5), (7, 8, 6), (9, 11, 7))),
)
TC_GRADE += tuple((10100 + g, g, f"TEAMCOLOR_ATTRIBUTES_EVOLVE{g}" if g <= 10 else f"Thẻ +{g}", _tc_groups((11, 11, n)))
                  for g, n in ((3, 4), (4, 4), (5, 5), (6, 5), (7, 5), (8, 6), (9, 6), (10, 7), (11, 7), (12, 8),
                               (13, 8), (14, 9), (15, 9), (16, 10), (17, 10), (18, 11), (19, 11), (20, 12)))


def tc_seasons():
    """(label, card season codes) of every season color: fifaaddict's seasons and '07."""
    return (("07", (7,)),) + tuple((label, codes) for label, codes, _ in FA_SEASONS)


def tc_season_of(code):
    """Season color id of a card season code (None: no color, e.g. engine cards)."""
    return next((TC_SEASON + codes[0] for _, codes in tc_seasons() if code in codes), None)


def tc_season_key(cid):
    """teamid of a season color = its key in teamColorSpecials. The transfer market filter "TC đặc
    biệt" (TransferPurchaseFilterSmoothPanel.configUI) labels its rows with those keys as-is, so
    the key is the color's name ("Season 07"), not its numeric id."""
    return next(f"Season {label}" for label, codes in tc_seasons() if TC_SEASON + codes[0] == cid)


# Mau co ten cua client (khoa TEAMCOLOR_ATTRIBUTES_TEAM<id>, da Viet hoa trong LANGUAGESTRING_ENG):
# danh sach cau thu/chi so goc cua chung la du lieu server da mat; client chi con 1 dong TOOLTIP<id>
# -> moi mau co ten = bac co ban + chi so cua tooltip, va thang mau chung no trung (moi cau thu lay mau
# CLB / quoc gia / dac biet tot nhat: getBoostStatPoint cong cac boost hop vi tri, 1 boost OVR tinh 1
# lan moi chi so). Boost {type, amount, target}: cong amount vao chi so (type "ovr" = moi chi so) cua
# cau thu nhom vi tri fw/mf/df/gk (khong target = tat ca; ApplyTeamColorBoostStat). Boost =
# (target, chi so, so); tooltip "Cap +N" / "{0} +{1}" (khong ro chi so) tinh la OVR.
TC_NAMED_CLUBS = (  # (TEAM<id>, CLB; cau thu tinh neu dang o hoac tung o CLB)
    (1, 243, ("fw", "sprintspeed", 2)), (2, 241, ("mf", "agility", 3)), (3, 9, ("fw", "balance", 1)),
    (4, 10, ("fw", "longpassing", 2)), (5, 11, ("fw", "reactions", 2)), (6, 1, ("mf", "stamina", 2)),
    (7, 5, ("mf", "dribbling", 1)), (8, 18, ("mf", "acceleration", 2)), (9, 22, ("mf", "positioning", 2)),
    (10, 32, ("fw", "sprintspeed", 8)), (11, 21, ("mf", "finishing", 2)), (12, 47, ("fw", "positioning", 1)),
    (13, 45, ("df", "stamina", 2)), (14, 44, ("fw", "dribbling", 1)), (15, 73, ("fw", "ballcontrol", 2)),
    (16, 982, ("mf", "stamina", 12)), (43, 240, ("", "ovr", 1)), (44, 52, ("", "ovr", 1)),
    (45, 236, ("", "ballcontrol", 3)), (46, 69, ("", "stamina", 2)), (47, 110374, ("", "positioning", 3)),
    (48, 48, ("", "dribbling", 2)), (146, 981, ("", "ovr", 1)), (885, 110955, ("", "headingaccuracy", 4)),
    (20054, 112172, ("", "ovr", 1)), (20059, 72, ("", "ovr", 1)), (20060, 1477, ("", "ovr", 1)),
)
TC_NAMED_NATIONS = (  # (TEAM<id>, quoc tich)
    (101, 45, ("mf", "ballcontrol", 2)), (102, 21, ("fw", "positioning", 3)), (103, 54, ("fw", "dribbling", 2)),
    (104, 38, ("fw", "dribbling", 2)), (105, 52, ("fw", "balance", 2)), (106, 60, ("fw", "agility", 2)),
    (107, 56, ("mf", "shotpower", 3)), (108, 27, ("mf", "shortpassing", 3)), (109, 14, ("mf", "stamina", 1)),
    (110, 7, ("mf", "ballcontrol", 2)), (111, 95, ("fw", "jumping", 2)), (112, 34, ("fw", "reactions", 1)),
    (113, 18, ("fw", "positioning", 2)), (114, 10, ("fw", "headingaccuracy", 2)), (115, 40, ("fw", "ballcontrol", 2)),
    (116, 83, ("fw", "reactions", 2)), (117, 108, ("mf", "reactions", 2)), (118, 117, ("mf", "shortpassing", 2)),
    (119, 167, ("mf", "aggression", 2)), (120, 163, ("mf", "shortpassing", 2)), (121, 195, ("fw", "positioning", 2)),
    (122, 55, ("fw", "sprintspeed", 2)), (123, 49, ("fw", "finishing", 1)), (124, 50, ("fw", "reactions", 2)),
    (125, 46, ("fw", "shotpower", 2)), (126, 13, ("fw", "headingaccuracy", 2)), (127, 51, ("fw", "jumping", 2)),
    (128, 103, ("fw", "acceleration", 2)), (129, 133, ("fw", "sprintspeed", 1)), (130, 12, ("fw", "positioning", 2)),
)
# mau mua co ten trong client: ma mua -> (TEAM<id>, boost); mau mua giu toi thieu +3
TC_SEASON_NAMES = {91: (18, ("", "ovr", 5)), 93: (100, ("", "ovr", 3)), 95: (19, ("", "ovr", 3)),
                   77: (42, ("", "reactions", 1)), 65: (140, ("", "ovr", 3)), 53: (141, ("", "ovr", 3)),
                   47: (142, ("", "ovr", 3)), 63: (143, ("", "ovr", 3)), 69: (137, ("", "ovr", 3)),
                   67: (138, ("", "ovr", 3)), 35: (145, ("", "ovr", 3)), 27: (20044, ("", "ovr", 3))}
TC_LEGENDS = (93, 30, 27, 71, 51, 61, 89, 48, 55, 49)  # WL UL EL ML TL VL CL MUL MUA TCO92
TC_YEARS = (6, 7, 8, 9, 10, 11, 14, 15, 16, 88, 325, 300)  # + FO4 25 LIVE / 26 LIVE


def _in(ids):
    return ",".join(map(str, ids))


def _S(*codes):  # mua cua the
    return f"p.spid / 1000000 IN ({_in(codes)})"


def _CLUB(*ids):  # CLB hien tai hoac tung choi
    return f"(p.teamid IN ({_in(ids)}) OR p.playerid IN (SELECT playerid FROM playerhistory WHERE teamid IN ({_in(ids)})))"


def _PIDS(*ids):  # cac cau thu nay, the nao cung duoc
    return f"p.playerid IN ({_in(ids)})"


_SPECIAL = f"p.spid >= 1000000 AND p.spid / 1000000 NOT IN ({_in(TC_YEARS + TC_LEGENDS)})"  # mua dac biet
_TC_OVR = ("", "ovr", 3)
# (TEAM<id>, khoa teamColorSpecials = nhan o bo loc "TC đặc biệt" Chuyen nhuong, mo ta, dieu kien SQL tren
# cau thu p, boost, N the tot nhat | None). Nhan = ten client (LANGUAGESTRING_ENG da Viet hoa).
TC_NAMED_SPECIALS = (
    *((tid, label, "Cầu thủ 14 WC của quốc gia này", f"{_S(77)} AND p.nationality = {n}", b, None) for tid, label, n, b in (
        (20, "14 WC TBN", 45, ("", "ovr", 2)), (21, "14 WC Đức", 21, ("", "ovr", 3)),
        (22, "14 WC Brazil", 54, ("", "ballcontrol", 2)), (23, "14 WC BĐN", 38, ("", "positioning", 2)),
        (24, "14 WC Argentina", 52, ("", "dribbling", 2)), (25, "14 WC Uruguay", 60, ("fw", "longshots", 3)),
        (26, "14 WC Colombia", 56, ("", "strength", 1)), (27, "14 WC Ý", 27, ("", "agility", 2)),
        (28, "14 WC Anh", 14, ("", "longpassing", 2)), (29, "14 WC Bỉ", 7, ("", "shortpassing", 2)),
        (30, "14 WC Mỹ", 95, ("mf", "stamina", 2)), (31, "14 WC Hà Lan", 34, ("", "ballcontrol", 2)),
        (32, "14 WC Pháp", 18, ("", "strength", 1)), (33, "14 WC Croatia", 10, ("fw", "acceleration", 2)),
        (34, "14 WC Nga", 40, ("", "positioning", 2)), (35, "14 WC Mexico", 83, ("", "headingaccuracy", 2)),
        (36, "14 WC Bờ Biển Ngà", 108, ("", "marking", 3)), (37, "14 WC Ghana", 117, ("", "reactions", 2)),
        (38, "14 WC Hàn Quốc", 167, ("", "reactions", 2)), (39, "14 WC Nhật Bản", 163, ("", "shortpassing", 2)))),
    *((tid, label, "50 thẻ tốt nhất của quốc gia này", f"p.nationality = {n}", _TC_OVR, 50) for tid, label, n in (
        (20007, "BEST Đức", 21), (20008, "BEST TBN", 45), (20009, "BEST Pháp", 18), (20010, "BEST Ý", 27),
        (20011, "BEST Anh", 14), (20012, "BEST Brazil", 54), (20013, "BEST Argentina", 52), (20014, "BEST Bỉ", 7),
        (20015, "BEST BĐN", 38), (20016, "BEST Colombia", 56), (20017, "BEST Uruguay", 60))),
    *((tid, label, "Cầu thủ EC / Copa của quốc gia này", f"{_S(35, 24)} AND p.nationality = {n}", _TC_OVR, None)
      for tid, label, n in (
        (20018, "EC Đức", 21), (20019, "EC TBN", 45), (20020, "EC Pháp", 18), (20021, "EC Ý", 27), (20022, "EC Anh", 14),
        (20023, "EC Brazil", 54), (20024, "EC Argentina", 52), (20025, "EC Bỉ", 7), (20026, "EC BĐN", 38),
        (20027, "EC Colombia", 56))),
    *((tid, label, "Cầu thủ mùa '10 của CLB này", f"{_S(10)} AND {_CLUB(club)}", b, None) for tid, label, club, b in (
        (133, "Barcelona '10", 241, ("fw", "positioning", 5)), (134, "AC Milan '10", 47, ("fw", "sprintspeed", 5)),
        (135, "Dortmund '10", 22, ("mf", "aggression", 5)), (136, "Atletico Madrid '10", 240, ("mf", "vision", 5)))),
    (132, "Cầu thủ đặc biệt", "Cầu thủ mùa đặc biệt (không phải mùa thường, không phải huyền thoại)", _SPECIAL, ("", "ovr", 2), None),
    (139, "Vô địch Championship", "Nhà vô địch các giải 2016/17 (mùa '17)", f"{_S(88)} AND p.teamid IN (5, 243, 21, 45, 69)", ("", "ovr", 3), None),
    (41, "Cầu thủ xuất sắc", "30 thẻ tốt nhất", "p.spid >= 1000000", ("", "shortpassing", 2), 30),
    (2014, "Cầu thủ Premium", "Thẻ chỉ số 85 trở lên", f"p.spid >= 1000000 AND {POSOVR} >= 85", ("", "ovr", 4), None),
    (886, "(CN) Huyền Thoại", "Huyền thoại Trung Quốc", f"p.nationality = 155 AND {_S(*TC_LEGENDS)}", ("", "ovr", 3), None),
    (20002, "Bộ ba MSN", "Messi, Suarez, Neymar", _PIDS(158023, 176580, 190871), _TC_OVR, None),
    (20003, "Bộ ba BBC", "Bale, Benzema, Cristiano Ronaldo", _PIDS(173731, 165153, 20801), _TC_OVR, None),
    (3059, "Pháo hai nòng", "Messi và Cristiano Ronaldo", _PIDS(158023, 20801), _TC_OVR, None),
    (3060, "Bức tường Turin", "Ramos, Pepe, Marcelo, Carvajal, Varane, Navas",
     _PIDS(155862, 120533, 176676, 204963, 201535, 193041), _TC_OVR, None),
    (3058, "Mùa giải thần thánh", "Leicester City vô địch 2015/16", _PIDS(208830, 204485, 215914, 186132, 136113, 137785,
                                                                         163587, 183130, 194365, 169600, 139313), _TC_OVR, None),
    (3064, "Derby Milano", "Cầu thủ AC Milan và Inter", f"p.spid >= 1000000 AND {_CLUB(47, 44)}", _TC_OVR, None),
    (34024, "Rossoneri", "Cầu thủ AC Milan", f"p.spid >= 1000000 AND {_CLUB(47)}", _TC_OVR, None),
    (20030, "Arsenal vô đối 03/04", "Arsenal bất bại 2003/04", _PIDS(805, 388, 119152, 34079, 6975, 1419, 1605, 4000, 1625,
                                                                     140526, 111093), _TC_OVR, None),
    (20031, "Song sát AC Milan", "Shevchenko và Kaka", _PIDS(13128, 138449), _TC_OVR, None),
    (20032, "Trái tim màu xanh", "Terry, Lampard, Drogba, Cech", _PIDS(13732, 5471, 31432, 48940), _TC_OVR, None),
    (20033, "Galaticos", "Figo, Ronaldo, Beckham, Raul, Roberto Carlos, Casillas", _PIDS(5589, 37576, 250, 45661, 1040, 5479),
     _TC_OVR, None),
    (20028, "Ngày Quỷ Đỏ (Mùa hiện tại)", "Cầu thủ Man Utd mùa '17", f"{_S(88)} AND {_CLUB(11)}", _TC_OVR, None),
    (20029, "Ngày Quỷ Đỏ (Các mùa)", "Cầu thủ Man Utd (hiện tại hoặc từng thi đấu), mọi mùa",
     f"p.spid >= 1000000 AND {_CLUB(11)}", ("", "ovr", 2), None),
    (20043, "Huyền thoại", "Cầu thủ mùa huyền thoại", _S(*TC_LEGENDS), ("", "ovr", 4), None),
    (40001, "MASTERS", "50 thẻ tốt nhất", "p.spid >= 1000000", _TC_OVR, 50),
    (40002, "PREMIUM TOP 100", "100 thẻ tốt nhất", "p.spid >= 1000000", _TC_OVR, 100),
    (40003, "SPECIAL TOP 100", "100 thẻ mùa đặc biệt tốt nhất", _SPECIAL, _TC_OVR, 100),
    (40004, "CURRENT TOP 100", "100 thẻ mùa '17 tốt nhất", _S(88), _TC_OVR, 100),
    (40005, "LEGACY TOP 100", "100 thẻ huyền thoại tốt nhất", _S(*TC_LEGENDS), _TC_OVR, 100),
    (20063, "Vũ công Samba", "Cầu thủ Brazil kỹ thuật 4 sao trở lên", "p.spid >= 1000000 AND p.nationality = 54 AND p.skillmoves >= 3",
     ("", "ballcontrol", 5), None),
    (20064, "Bộ tứ kiến tạo", "Chuyền ngắn và tầm nhìn 85 trở lên", "p.spid >= 1000000 AND p.shortpassing >= 85 AND p.vision >= 85",
     ("", "shortpassing", 5), None),
    (20065, "2 thế hệ", "Cầu thủ Real Madrid và Barcelona", f"p.spid >= 1000000 AND {_CLUB(243, 241)}", ("", "dribbling", 5), None),
    (20066, "Bức tường thép", "Cướp bóng 85 trở lên", "p.spid >= 1000000 AND p.standingtackle >= 85", ("", "aggression", 5), None),
    (20067, "Bộ tứ siêu đẳng", "Tinh thần tấn công và phòng ngự đều cao",
     "p.spid >= 1000000 AND p.attackingworkrate = 2 AND p.defensiveworkrate = 2", ("", "positioning", 5), None),
    (20068, "Hộ pháp", "Hậu vệ cao 188 cm trở lên", "p.spid >= 1000000 AND p.height >= 188 AND p.preferredposition1 BETWEEN 1 AND 8",
     ("", "strength", 5), None),
    (20069, "Ngôi sao đương đại", "Sinh từ năm 1995 trở về sau",
     "p.spid >= 1000000 AND CAST(substr(p.birthdate, instr(p.birthdate, ' ') - 4, 4) AS INTEGER) >= 1995", ("", "longshots", 5), None),
    (20070, "Thiết đầu công", "Đánh đầu 85 trở lên", "p.spid >= 1000000 AND p.headingaccuracy >= 85", ("", "strength", 5), None),
    (20071, "Danh tiếng quốc tế", "Danh tiếng quốc tế 4 sao trở lên", "p.spid >= 1000000 AND p.internationalrep >= 4",
     ("", "ovr", 4), None),
)
# Ten: khoa TEAMCOLOR_ATTRIBUTES_TEAM<id> co trong LANGUAGESTRING_ENG cua ban nay; khoa thieu -> ten viet san
TC_NAMES = {146: "Seongnam FC", 20054: "RB Leipzig", 20059: "OGC Nice", 20060: "Jeonbuk Hyundai", 20044: "Huyền thoại Châu Âu",
            3064: "Derby Milano", 34024: "Rossoneri", 20071: "Danh tiếng quốc tế"}
# bo loc "TC đặc biệt" cua Chuyen nhuong loc theo danh sach the trong teamColorSpecials: mau dac biet co
# toi da TC_FULL_MAX the gui du danh sach (loc duoc moi the), lon hon chi gui the minh co (tinh boost).
TC_FULL_MAX = 3000


def tc_name(cid):
    return TC_NAMES.get(cid) or f"TEAMCOLOR_ATTRIBUTES_TEAM{cid}"


def _tc_boost(groups, boost):
    """groups voi boost tooltip (target, chi so, so) them vao moi bac."""
    target, stat, n = boost
    extra = {"type": stat, "amount": n, **({"target": target} if target else {})}
    return [dict(g, boosts=g["boosts"] + [extra]) for g in groups]


def _tc_special_groups(boost, players=5):
    """Bac cua mau dac biet: 5-6 / 7-8 / 9-11 nguoi, OVR toi muc tooltip (else +3, kem chi so tooltip).
    Bo it hon 5 cau thu: 1 bac = du ca bo."""
    target, stat, n = boost
    top = n if stat == "ovr" else 3
    groups = (_tc_groups((players, 11, top)) if players < 5
              else _tc_groups((5, 6, max(1, top - 2)), (7, 8, max(1, top - 1)), (9, 11, top)))
    return groups if stat == "ovr" else _tc_boost(groups, boost)


@functools.lru_cache(maxsize=None)
def tc_special_sets():
    """{teamcolorid dac biet: (frozenset spid, so cau thu)}: mau mua theo ma mua, mau co ten theo dieu
    kien ("N tot nhat": theo chi so)."""
    sets = {}
    with closing(_db()) as c:
        rows = c.execute("SELECT spid, playerid FROM players").fetchall()
        for _, codes in tc_seasons():
            sets[TC_SEASON + codes[0]] = [(s, p) for s, p in rows if s // 1000000 in codes]
        for cid, _, _, cond, _, best in TC_NAMED_SPECIALS:
            got = c.execute(f"SELECT p.spid, p.playerid FROM players p WHERE {cond} ORDER BY {POSOVR} DESC, p.spid").fetchall()
            sets[cid] = [r for r in got if on_market(r[0])][:best] if best else got
    return {cid: (frozenset(s for s, _ in got), len({p for _, p in got})) for cid, got in sets.items()}


@functools.lru_cache(maxsize=None)
def tc_colors():
    """Moi mau (GET /static/info/teamcolor teamColors): CLB / quoc gia chung, nang cap, CLB va quoc gia
    co ten, mau mua, mau dac biet co ten (mau khong co the nao trong fifadb bi bo)."""
    club = {"teamcolorid": TC_CLUB, "type": "club", "teamid": -1, "name": tc_name(TC_CLUB),
            "description": "Cầu thủ cùng câu lạc bộ", "boostgroups": TC_CLUB_GROUPS}
    nation = {"teamcolorid": TC_NATION, "type": "nation", "teamid": -1, "name": tc_name(TC_NATION),
              "description": "Cầu thủ cùng quốc gia", "boostgroups": TC_GROUPS}
    grades = [{"teamcolorid": cid, "type": "grade", "grade": g, "teamid": -1, "name": name,
               "description": (f"Cả 11 cầu thủ đá chính từ +{g} trở lên" if cid > 10100 else f"Cầu thủ đá chính từ +{g} trở lên"),
               "boostgroups": groups} for cid, g, name, groups in TC_GRADE]
    clubs = [{"teamcolorid": cid, "type": "club", "teamid": t, "name": tc_name(cid),
              "description": "Cầu thủ của CLB này (hiện tại hoặc từng thi đấu)", "boostgroups": _tc_boost(TC_CLUB_GROUPS, b)}
             for cid, t, b in TC_NAMED_CLUBS]
    nations = [{"teamcolorid": cid, "type": "nation", "teamid": n, "name": tc_name(cid), "description": "Cầu thủ của quốc gia này",
                "boostgroups": _tc_boost(TC_GROUPS, b)} for cid, n, b in TC_NAMED_NATIONS]
    # mau dac biet: teamid la CHUOI (TeamColorController chep sang __teamid, native doc bang asCString o
    # hiep 2 - so o day lam crash). = khoa teamColorSpecials = nhan bo loc Chuyen nhuong.
    sets = tc_special_sets()
    specials = []
    for label, codes in tc_seasons():
        cid = TC_SEASON + codes[0]
        tid, b = TC_SEASON_NAMES.get(codes[0], (None, None))
        specials.append({"teamcolorid": cid, "type": "special", "teamid": f"Season {label}",
                         "name": tc_name(tid) if tid else f"Season {label}",
                         "description": f"Cầu thủ mùa {label}", "boostgroups": _tc_special_groups(b) if b else TC_GROUPS})
    for cid, key, desc, _, b, _ in TC_NAMED_SPECIALS:
        if sets[cid][1]:
            specials.append({"teamcolorid": cid, "type": "special", "teamid": key, "name": tc_name(cid),
                             "description": desc, "boostgroups": _tc_special_groups(b, min(5, sets[cid][1]))})
    return [club, nation] + grades + clubs + nations + specials


def ep_teamcolor(query, body):
    """GET /static/info/teamcolor (1 lan moi lan dang nhap). Mau dac biet liet ke the theo spid: mau mua
    va mau dac biet nho (<= TC_FULL_MAX) gui du (bo loc "TC đặc biệt" o Chuyen nhuong), mau lon chi gui
    the minh co (mua them the: server tinh ngay, client cong boost tu lan dang nhap sau)."""
    st = _state()
    owned = {int(p["spid"]) for p in st["teams"].get(str(_default_slot(st)), {}).get("players", [])}
    sets = tc_special_sets()
    specials = {}
    for c in tc_colors():
        if c["type"] == "special":
            spids = sets[c["teamcolorid"]][0]
            full = c["teamcolorid"] >= TC_SEASON or len(spids) <= TC_FULL_MAX
            specials[c["teamid"]] = sorted(spids if full else owned & spids)
    return _json({"teamColors": tc_colors(), "teamColorSpecials": specials})


CLUB_HISTORY = os.path.join(HERE, "gamedata", "club_history.json")  # tools/clientbuild/club_history.py


@functools.lru_cache(maxsize=None)
def tc_static():
    """({spid: (playerid, teamid, nationality)}, {playerid: {former club ids}}) from fifadb."""
    with closing(_db()) as c:
        cards = {s: (p, t, n) for s, p, t, n in c.execute("SELECT spid, playerid, teamid, nationality FROM players")}
        hist = {}
        for p, t in c.execute("SELECT playerid, teamid FROM playerhistory"):
            hist.setdefault(p, set()).add(t)
    # su nghiep CLB toi mua hien tai (gamedata/club_history.json, Wikidata) thay cho playerhistory cu cua FO3
    # (den ~2017) voi cau thu co trong do: giong bang playerhistory bo dung ghi vao client -> Team Color o man
    # doi hinh, trong tran va cua HLV ao khop nhau (theo ban Indo 326ad2c; vd Ronaldo tinh cho Juventus)
    try:
        with open(CLUB_HISTORY, encoding="utf-8") as f:
            hist.update({int(p): {r[0] for r in rs} for p, rs in (json.load(f).get("players") or {}).items()})
    except (OSError, ValueError, AttributeError):
        pass
    return cards, hist


def _tc_tier(groups, n):
    return next((i for i, g in enumerate(groups) if g["count_min"] <= n <= g["count_max"]), None)


@functools.lru_cache(maxsize=None)
def _tc_by_id():
    return {c["teamcolorid"]: c for c in tc_colors()}


def _tc_xi(team):
    """[(o doi hinh, the)] cua 11 cau thu chinh (khong trung cau thu, nhu updatePlayerCount)."""
    cards = {iid(p): p for p in team.get("players", [])}
    out, seen = [], set()
    static = tc_static()[0]
    for s in team["squad"]["positions"]:
        if s.get("pos", POS_SUB) >= POS_SUB:
            continue
        c = static.get(int(s["spid"]))
        if c is None or c[0] in seen:
            continue
        seen.add(c[0])
        out.append((s, cards.get(str(s.get("id") or s["spid"]), {"spid": s["spid"]})))
    return out


def _tc_keys(spid, sets):
    """Cac khoa mau ma 1 the dap ung: (TC_CLUB, CLB), (TC_NATION, quoc tich), ("special", id)."""
    cards, hist = tc_static()
    c = cards.get(int(spid))
    if c is None:
        return []
    pid, teamid, nation = c
    keys = [(TC_CLUB, t) for t in {teamid} | hist.get(pid, set())] + [(TC_NATION, nation)]
    return keys + [("special", cid) for cid, (spids, _) in sets.items() if int(spid) in spids]


def active_teamcolors(team):
    """team.teamcolors: cac mau doi hinh chinh dat du so cau thu khac nhau (nhu TeamColorController
    .updatePlayerCount): 1 mau CLB / quoc gia chung moi CLB va quoc gia, mau co ten, mau dac biet,
    mau nang cap."""
    sets = tc_special_sets()
    counts = {}
    xi = _tc_xi(team)
    for s, _ in xi:
        for k in _tc_keys(s["spid"], sets):
            counts[k] = counts.get(k, 0) + 1
    out = []
    for (cid, teamid), n in sorted((k, n) for k, n in counts.items() if k[0] != "special"):
        i = _tc_tier(TC_CLUB_GROUPS if cid == TC_CLUB else TC_GROUPS, n)
        if i is not None and teamid > 0:
            out.append({"teamcolorid": cid, "boostindex": i, "teamid": teamid})
    for c in tc_colors():
        if c["type"] == "grade" or c["teamid"] == -1:
            continue
        n = counts.get(("special", c["teamcolorid"]) if c["type"] == "special"
                       else (TC_CLUB if c["type"] == "club" else TC_NATION, c["teamid"]), 0)
        i = _tc_tier(c["boostgroups"], n)
        if i is not None:
            out.append({"teamcolorid": c["teamcolorid"], "boostindex": i, "teamid": c["teamid"]})
    grades = [grade_of(p) for _, p in xi] if server_options().get("tc_grade", True) else []
    for cid, g, _, groups in TC_GRADE:
        i = _tc_tier(groups, sum(x >= g for x in grades))
        if i is not None:
            out.append({"teamcolorid": cid, "boostindex": i, "teamid": -1})
    return out


def _tc_line(pos):
    return "gk" if pos == 0 else "df" if pos <= 8 else "mf" if pos <= 19 else "fw"


def _tc_ovr(color, idx, pos):
    """Diem OVR (boost type "ovr" hop vi tri) cua 1 bac mau; boost chi so le khong tinh vao OVR."""
    return sum(b["amount"] for b in color["boostgroups"][idx]["boosts"]
               if b["type"] == "ovr" and b.get("target", _tc_line(pos)) == _tc_line(pos))


def tc_bonus(team):
    """{spid: diem OVR team color} cho doi hinh chinh: mau CLB / quoc gia / dac biet tot nhat (khong cong
    don) + mau nang cap tot nhat (cong them, ChooseTeamColorBoostsPerPlayer). Chi tinh boost OVR."""
    try:
        act = active_teamcolors(team)
        sets = tc_special_sets()
        by_id = _tc_by_id()
    except Exception:
        return {}
    generic = {(c["teamcolorid"], c["teamid"]): c["boostindex"] for c in act if c["teamcolorid"] in (TC_CLUB, TC_NATION)}
    named = {c["teamcolorid"]: c["boostindex"] for c in act if c["teamcolorid"] not in (TC_CLUB, TC_NATION)}
    grade_cols = [(by_id[cid], named[cid], g) for cid, g, _, _ in TC_GRADE if cid in named]
    out = {}
    for s, p in _tc_xi(team):
        spid, pos = int(s["spid"]), s.get("pos", 0)
        keys = set(_tc_keys(spid, sets))
        best = 0
        for (cid, t), i in generic.items():
            if (cid, t) in keys:
                best = max(best, _tc_ovr(by_id[cid], i, pos))
        for cid, i in named.items():
            c = by_id[cid]
            if c["type"] == "grade":
                continue
            k = ("special", cid) if c["type"] == "special" else (TC_CLUB if c["type"] == "club" else TC_NATION, c["teamid"])
            if k in keys:
                best = max(best, _tc_ovr(c, i, pos))
        g = grade_of(p)
        best += max([_tc_ovr(c, i, pos) for c, i, need in grade_cols if g >= need] + [0])
        out[spid] = best
    return out


def repair_squad(team):
    """Restore missing starters and keep all squad references on owned instances."""
    sq = team["squad"]
    cards = {iid(p): p for p in team["players"]}
    used, slots, positions = set(), set(), []
    for entry in sq.get("positions", []):
        pid = str(entry.get("id") or entry.get("spid"))
        pos = entry.get("pos", POS_SUB)
        if pid not in cards or pid in used or not 0 <= pos <= POS_SUB:
            continue
        if pos < POS_SUB and (pos in slots or len(slots) >= 11):
            continue
        positions.append(dict(entry, id=pid, spid=cards[pid]["spid"]))
        used.add(pid)
        if pos < POS_SUB:
            slots.add(pos)
    # Preserve every valid starter. Fill vacancies from the existing bench/reserves.
    starters = [p for p in positions if p["pos"] < POS_SUB]
    starting_ids = {p["id"] for p in starters}
    for pos, x, y in gen_teams.FORMATION:
        if len(starters) >= 11:
            break
        if pos in slots:
            continue
        candidates = [p for pid, p in cards.items() if pid not in starting_ids and not p.get("team2")]
        if not candidates:
            break
        chosen = max(candidates, key=player_ovr)
        pid = iid(chosen)
        starters.append({"id": pid, "spid": chosen["spid"], "pos": pos,
                         "starting": [1, 1, x, y]})
        slots.add(pos)
        starting_ids.add(pid)
    bench = [p for p in positions if p["pos"] == POS_SUB and p["id"] not in starting_ids][:7]
    selected = starting_ids | {p["id"] for p in bench}
    for p in sorted(cards.values(), key=player_ovr, reverse=True):
        if len(bench) >= 7:
            break
        if iid(p) not in selected and not p.get("team2"):
            bench.append({"id": iid(p), "spid": p["spid"], "pos": POS_SUB})
            selected.add(iid(p))
    sq["positions"] = starters + bench
    if str(sq.get("captain")) not in starting_ids:
        sq["captain"] = next((p["id"] for p in starters if p["pos"] != 0), "")
    else:
        sq["captain"] = str(sq["captain"])
    fallback = [p["id"] for p in starters if p["pos"] != 0]
    kickers = sq.get("kickers", [])
    sq["kickers"] = [str(kickers[i]) if i < len(kickers) and str(kickers[i]) in starting_ids
                     else (fallback[i % len(fallback)] if fallback else "") for i in range(5)]


def squad_presets(team):
    """Native SquadUtil needs team.teamsquadlist.preset, even on opening the first preset."""
    import copy
    stored = team.get('teamsquadlist')
    entries = stored.get('preset', []) if isinstance(stored, dict) else []
    if not isinstance(entries, list):
        entries = []
    active = team.get('presetidx', 0)
    active = active if type(active) is int and 0 <= active < 3 else 0
    presets = []
    for index in range(3):
        candidate = entries[index] if index < len(entries) and isinstance(entries[index], dict) else team['squad']
        temp = {'players': team['players'], 'squad': copy.deepcopy(candidate)}
        repair_squad(temp)
        presets.append(temp['squad'])
    presets[active] = copy.deepcopy(team['squad'])
    team['presetidx'] = active
    team['teamsquadlist'] = {'preset': presets}
    return team['teamsquadlist']


def fix_starting(positions, formationid):
    """So do tren san (Doi hinh) ve moi cau thu tai starting[2:4]. Hai cau thu trung toa do (toa do
    cu sot lai khi doi so do / doi nguoi) -> mot nguoi bi che, so do chi con 10 (vd Isco LW).
    Cau thu thieu toa do hoac trung voi nguoi khac lay toa do o cua so do dang dung (hoac so do
    thuong dau tien co o do)."""
    t = formation_table()
    forms = t.get("common", []) + list(t.get("teams", {}).values())
    cur = next((f for f in forms if f.get("formationid") == formationid), None)

    def spot(pos):
        for f in ([cur] if cur else []) + forms:
            if pos in f.get("positions", []):
                o = f["offsets"][f["positions"].index(pos)]
                return [o["x"], o["y"]]
        return None
    seen = []
    for p in positions:
        if not isinstance(p, dict) or p.get("pos", POS_SUB) >= POS_SUB:
            continue
        st = p.get("starting")
        xy = st[2:4] if isinstance(st, list) and len(st) >= 4 else None
        if xy is None or any(abs(xy[0] - a) < 0.03 and abs(xy[1] - b) < 0.03 for a, b in seen):
            new = spot(p["pos"])
            if new is not None and not any(abs(new[0] - a) < 0.03 and abs(new[1] - b) < 0.03 for a, b in seen):
                p["starting"] = (list(st[:2]) if xy is not None else [1, 1]) + new
                xy = new
        if xy is not None:
            seen.append((xy[0], xy[1]))
    return positions


def owned_team(raw):
    """A stored team ({players:[{spid,...}], squad:{positions,captain,kickers,...}}) in the
    shape the client expects for a club it can show and play with (coach.team, match side.team)."""
    import copy
    team = copy.deepcopy(raw)
    repair_squad(team)
    # An owned club refers to player *instances*: PlayerListUtil.getBestEleven maps
    # squad.positions[].id -> players[]._id (spid alone gives an empty XI -> null -> the
    # MenuScreen constructor throws = black FREEROAM).
    # CoachProxy.expandTeam: teamvalue = sum(price) of players with state "first" and grade > 0
    # getBestEleven matches positions[].id on players[]._id, attachSquadInfo and
    # generateFormatedPlayerList (Tim > Formasi / Peran pemain) on players[].id: send both.
    # Instance defaults: life = remaining contract matches (SquadUtil.needToReject drops <= 0),
    # levelinfo.level (FirstTeamInfo.updateAvgInfo average level). price = EP market value.
    # state "first" = Tim inti; no state = Tim Cadangan (Enums.getPlayerState: undefined ->
    # SECONDTEAM), set by /player/<id>/move/secondteam (stored as team2); Tukar Scout only
    # lists Tim Cadangan cards.
    team["players"] = [{**({} if p.get("team2") else {"state": "first"}), "grade": 0, "life": 100,
                        **{k: v for k, v in p.items() if k != "team2"}, "levelinfo": player_levelinfo(p),
                        "_id": iid(p), "id": iid(p), "price": card_price(p["spid"], grade_of(p))}
                       for p in team["players"]]
    sq = dict(team["squad"])
    sq["positions"] = [dict(p, id=str(p.get("id") or p["spid"])) for p in sq["positions"]]
    if not any(p["pos"] == POS_SUB for p in sq["positions"]):
        # 11 starters + 7 subs (Enums.PLAYERPOSITION "SUB"); without a bench the squad screen
        # says "Tidak cukup pemain". Best remaining players by rating.
        used = {p["id"] for p in sq["positions"]}
        bench = sorted((p for p in team["players"] if p["id"] not in used), key=lambda p: -player_ovr(p))
        sq["positions"] += [{"spid": p["spid"], "pos": POS_SUB, "id": p["id"]} for p in bench[:7]]
    sq["captain"] = str(sq["captain"]) if sq.get("captain") else ""
    sq["kickers"] = [str(k) for k in sq.get("kickers", [])]
    fix_starting(sq["positions"], sq.get("formation"))
    team["squad"] = sq
    team.update({"staffs": team.get("staffs", []),
                 "staffroles": team.get("staffroles") or [
                     {"roleidx": 1, "slotidx": i, "open": True, "staffid": None}
                     for i in range(1, 8)],
                 "staffroleidx": team.get("staffroleidx") or 1,
                 "maxstaff": team.get("maxstaff") or 30, "staffsmc": team.get("staffsmc") or 0})
    # PlayerListUtil.generateCaptainFromCoachInfo (Tim > Formasi) reads these at team level
    team.update({"captain": sq["captain"], "kickers": sq["kickers"]})
    squad_presets(team)
    for pr in (team.get("teamsquadlist") or {}).get("preset") or []:
        if isinstance(pr, dict) and isinstance(pr.get("positions"), list):
            fix_starting(pr["positions"], pr.get("formation", sq.get("formation")))
    staff_service.normalize_team(team)
    team["staffs"] = [staff_service.client_staff(x) for x in team.get("staffs", [])]  # effectidx 0-based cho client
    team.setdefault("formations", [])
    return team


@functools.lru_cache(maxsize=None)
def card_pos(spid):
    with closing(_db()) as c:
        r = c.execute("SELECT preferredposition1 FROM players WHERE spid = ?", (int(spid),)).fetchone()
    return int(r[0]) if r else 0


def growth_ovr(p):
    """OVR the hien tang nho Phat trien cau thu (p.sp.stats, growth.py) o vi tri chinh: floor(sum(+n * trong so)/100)
    nhu cach game tinh OVR. Trong tran client tu cong (checkGrowthBoostPlayer); day cho chi so doi FW/MF/DF
    (theo ban Indo 77d4651: xi_ratings tinh ca phat trien)."""
    grown = (p.get("sp") or {}).get("stats") or {}
    if not grown:
        return 0
    try:
        w = ROLE_WEIGHTS[POS_ROLE[card_pos(p["spid"])]]
    except (IndexError, KeyError):
        return 0
    return sum(int(n) * w.get(k, 0) for k, n in grown.items()) // 100


def team_summary(team, teamid):
    # MenuScreen TeamInfoNew.setData: TEAMNAME(summary.id) + loadTeamCrest(summary.id), setOVR(ovr);
    # TeamStaticInfo (Tim > Klub) also reads name, league.id, nation{id,name}, rival.name.
    # TeamInfoNew.setOVR reads ovr.FW/MF/DF (rating bars); Enums.PLAYERPOSITION index ranges.
    # chi so nhu trong game: the + cap the + (cap do - 1)
    ovr = {iid(p): player_ovr(p) + player_levelinfo(p)["level"] - 1 + growth_ovr(p) for p in team["players"]}
    lines = {"DF": range(1, 9), "MF": range(9, 20), "FW": range(20, 28)}
    tcb = tc_bonus(team)
    def line_ovr(r):
        v = [ovr.get(str(p.get("id") or p["spid"]), 0) + tcb.get(int(p["spid"]), 0)
             for p in team["squad"]["positions"] if p.get("pos") in r]
        return round(sum(v) / len(v)) if v else 0
    return dict(roster_summary(teamid), id=teamid, ovr={k: line_ovr(r) for k, r in lines.items()})


def team_kit(teamid):
    # Chi dung cho /coach/me (cau thu FREEROAM) va frontplayer o man chon HLV: hai cho nay da kiem
    # chung voi << 10 (FREEROAM lay doi o cho khac, chi doc loai ao o 5 bit thap nen << 5 cung chay).
    # Tran dau (match_side), Doi doi (club_summary_response) va World Cup dung teamid << 5.
    # kit ids go to native as-is (freeroam kicker, 3D previews); {} = default goalie kit.
    # kit id = teamid << 10 | kittype (0 home, 1 away, 2 goalie): Enums.GOALIE_DEFAULT_KIT_ID
    # 45058 = 44 << 10 | 2; checked in FREEROAM (Barca home kit). id = teamid alone renders
    # a missing-texture checkerboard.
    return {"availables": {k: {"id": teamid << 10 | t} for k, t in (("home", 0), ("away", 1), ("goalie", 2))}}


START_EP = 1000000
# Enums.VIP_DETAIL_*: NONE 0, VIP 1<<1, VVIP 1<<2, VVIP_SEA 1<<4
VIP_DETAIL = 16  # VVIP (ban SEA = VIP_DETAIL_VVIP_SEA 1<<4). Client coi VIP/VVIP la 1 gia tri (khong cong bit): 16 = VVIP,
# het han VVIP (vipStatus.VIP4) thi tu lui ve VIP theo vipStatus.VIP1 -> ca hai deu 9999 ngay.
VIP_UNTIL = int((time.time() // 86400 + 10000) * 86400 * 1000)  # VIP + VVIP luon con 9999 ngay (tinh tu hom nay), ms


# Tran EP: game hien dung so tien hang tram ty (gia tri doi 126 ty, the dat nhat 40,9 ty) nhung so qua
# lon (vd 10^13 tu GM Tool) se loi hien thi/tran so -> gioi han ~1.000 ty.
MAX_EP = 999_999_999_999


def add_ep(st, n):
    """Credit match/season/cup EP to the active coach (shown by /coach/me money.ep)."""
    coach = coach_of(st)
    coach["ep"] = max(0, min(MAX_EP, coach.get("ep", START_EP) + int(n)))


def _deep_merge(base, extra):
    for k, v in (extra or {}).items():
        base[k] = _deep_merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return base


def coach_statistics(coach):
    """coach/me statistics (tab Thanh tich HLV - CoachTotalRecordTabPage). Day du cac nhanh
    league/friendly/ladder/simulation/club: thieu nhanh "club" -> TypeError #1009 o setClubRecord,
    thieu "friendly" -> loi khi doc friendly.roleplay."""
    rec = lambda: {"wins": 0, "draws": 0, "losses": 0, "winningstreak": 0}
    base = {
        "league": {"wins": 0, "draws": 0, "losses": 0, "goals": 0, "goalsconceded": 0, "leaguewins": [],
                   "normalwins": 0, "ultimatewins": 0, "noramlbest": 0, "ultimatebest": 0,
                   "winningstreak": {"max": 0, "current": 0}},
        "friendly": {"quick": rec(), "custom": rec(), "vscom": rec(),
                     "roleplay": {"comwin": 0, "friendwin": 0, "randomwin": 0}},
        "simulation": {"quick": rec(), "custom": rec(), "vscom": rec()},
        "ladder": {"highrecords": {"count": 0, "elo": 0, "rank": 0, "winningstreak": 0}},
        "winstreak": [],
        "club": {"friendly": {"total": 0, "wins": 0, "forfeitwins": 0},
                 "ranked": {"total": 0, "wins": 0, "forfeitwins": 0}},
    }
    return _deep_merge(base, coach.get("statistics"))


def coach_me(st, slot):
    """Body of the /coach/me resource (CoachProxy). Field paths are the ones its
    fetchCoach* methods read; values are a fresh account. startFreeRoam waits for this
    resource to validate before signing on."""
    coach = coach_of(st, slot)
    team = owned_team(st["teams"].get(str(slot), {"squad": {"positions": [], "kickers": []}, "players": []}))
    teamid = coach["teamid"]
    team["formations"] = team_formations(teamid)
    team["teamcolors"] = active_teamcolors(team)
    if team["formations"] and team["squad"].get("formation") is None:
        team["squad"]["formation"] = team["formations"][0]["formationid"]
    return {
        "id": coach["_id"], "name": coach["name"], "slot": slot, "teamid": coach["teamid"], "team": team,
        # CoachProxy.expandMoney parses money.mpdata.until as a W3C date; an exception there
        # silently aborts onChangeCoachMeResource before squad/players are read.
        # es = diem nang cap (nang cap v2.5: CoachProxy.expandMoney money.es)
        "money": {"ep": min(MAX_EP, coach.get("ep", START_EP)), "es": int(coach.get("es", 0)),
                  # MP + Cash: so du rieng cua HLV (mac dinh 0, chinh o GM Tool > Tong quan). Chi hien thi -
                  # moi mon Cua hang tinh bang EP, khong mon nao tru MP / Cash.
                  "cash": coach_money(coach, "cash"), "mp": coach_money(coach, "mp"), "useable_mp": coach_money(coach, "mp"),
                  "mpdata": {"point": coach_money(coach, "mp"), "until": "2035-01-01T00:00:00.000Z"}},
        # always a VIP member playing from an official PC warnet (IGR / GCA): CoachProxy.igr,
        # isVip() = vipDetail > 0 and vipExpireAt in the future; vipStatus.VIP1 = VIP expiry
        # after a VVIP period (updateVipExpireAt)
        "igr": True, "vipDetail": VIP_DETAIL, "vipExpireAt": VIP_UNTIL, "vipStatus": {"VIP1": VIP_UNTIL, "VIP2": VIP_UNTIL, "VIP4": VIP_UNTIL},
        "sp": {"point": 0}, "gpdata": {"gp": 0}, "badges": [], "currentbadge": 0,
        # league.leagueid -> CoachProxy.leagueId: Single > Liga builds the league from it
        "levelinfo": coach_levelinfo(coach), "statistics": coach_statistics(coach),
        # CoachSummaryTabPage.onCoachSummaryPopupCreated doc Coach.profile.picture (255 = chua chon);
        # thieu "profile" -> TypeError #1010 khi mo cua so chon anh dai dien HLV
        # picture = _id cua cau thu duoc chon (chuoi hex); 255 = chua chon / cau thu da bi thanh ly
        "profile": {"picture": profile_picture(coach, team)},
        "league": {"leagueid": roster_summary(teamid).get("league", {}).get("id", 0)},
        # limits.cups -> CupProxy.playMax (cups you can run at once); limits.players/baseplayers
        # -> the market's player-slot check (TransferPurchaseTabPage._limitPlayerCount)
        # limits.firstTeam: Skuad tab moveToFirstTeam reads it first and silently gives up when
        # it is missing, so Tim Cadangan cards (packs, trades) could never reach Tim inti/Formasi
        "club": {}, "uimode": 0, "limits": {"cups": 5, "players": MAX_PLAYERS, "baseplayers": MAX_PLAYERS,
                                             "firstTeam": FIRST_TEAM_MAX},
        "transferfavorites": [{"spid": f[0], "grade": f[1], "expandme": "player"} for f in coach.get("favorites", [])],
        "currdivision": "",
        # playlimit {modeid: {playcount}}: Liga's Mulai/Simulasi buttons compare playcount with
        # PLAYLIMIT.maxplay; without the key playcount stays undefined and they stay disabled
        "playlimit": {},
        "kit": team_kit(teamid), "summary": dict(team_summary(team, teamid),
            stadium={"id": int(coach.get('stadiumid') or 1)},
            appat=coach.get('createdat') or w3c(os.path.getctime(os.path.join(HERE, 'gamedata', 'state.json')))),
        "stadium": {"id": int(coach.get('stadiumid') or 1)},
        "career": {"appat": coach.get('createdat') or w3c(os.path.getctime(os.path.join(HERE, 'gamedata', 'state.json'))),
                   # CoachTotalRecordTabPage doc Career.buyplayers/sellplayers/maxbuy/maxsell/clubs/maxplayers
                   "buyplayers": 0, "sellplayers": 0, "maxbuy": 0, "maxsell": 0, "clubs": 0, "maxplayers": 0},
        # cups -> CupProxy.cupPlayList: {metaid, cupid} of cups not left yet (front screen resumes
        # them; an ended one shows its won/eliminated screen with the reward button -> /leave)
        "items": coach_items(st, slot), "matchstats": [], "scouters": coach_scouters(st, slot),
        "cups": [{"metaid": c["metaid"], "cupid": c["_id"]} for c in (st.get("cups") or {}).values()],
        # SquadScreenHandler: formation combobox reads /coach/me?path=formations
        "formations": team["formations"], "teamsquadlist": team["teamsquadlist"],
    }


def profile_picture(coach, team):
    pic = coach.get("picture", 255)
    if isinstance(pic, str):
        ids = {str(p.get("_id") or p.get("id")) for p in (team or {}).get("players", [])}
        return pic if pic in ids else 255
    try:
        return int(pic)
    except (TypeError, ValueError):
        return 255


def ep_coach_profile(query, body):
    """PUT /coach/me/profile {picture: "<_id cau thu>"}: luu anh dai dien HLV (CoachSummaryTabPage
    gui _id cua cau thu trong doi; ban cu luu so -> int() loi -> anh khong luu)."""
    pic = json.loads(body or b"{}").get("picture")
    if pic is None or pic == "":
        return _json({"result": False})
    st = _state()
    slot = _default_slot(st)
    coach = coach_of(st, slot)
    if isinstance(pic, str) and not pic.isdigit():
        coach["picture"] = pic
    else:
        try:
            coach["picture"] = max(0, min(255, int(pic)))
        except (TypeError, ValueError):
            return _json({"result": False})
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


def coach_me_resources(st, slot):
    """The /coach/me resource for a reply's `resources`. CoachProxy's fetchCoach* read
    getData("/coach/me?path=X"), which NetEvent.getDataEx resolves by walking this body; pushing
    every ?path= part too re-ran onChangeCoachMeResource per part and nested the client's
    resource chain until AS3 "Error #1023: Stack overflow" at login."""
    return [{"uri": "/coach/me", "body": coach_me(st, slot)}]


def _default_slot(st):
    return next((c["slot"] for c in st["coaches"] if c["_id"] == st["defaultcoachid"]),
                st["coaches"][0]["slot"] if st["coaches"] else 0)  # default manager deleted: the other one


NO_COACH_TEAM = 241  # doi mac dinh cho HLV tam (chi de ve giao dien, khong luu)


def coach_of(st, slot=None):
    """HLV o o `slot` (mac dinh: HLV dang chon). Tai khoan chua co HLV (cai moi, hoac GM Tool "Reset nhan vat"
    khi game dang mo): tra HLV TAM khong nam trong st["coaches"] -> khong bi luu, cac request dinh ky
    (/coach/me, /achievement/me, /shop/list...) khong loi StopIteration -> popup "khong nhan duoc du lieu
    tu may chu (undefined)" (2026-11-10)."""
    if slot is None:
        slot = _default_slot(st)
    c = next((c for c in st.get("coaches") or () if c["slot"] == slot), None)
    if c is None:
        c = next(iter(st.get("coaches") or ()), None)
    if c is None:
        c = {"_id": coach_id(slot), "slot": slot, "name": "", "state": "active", "teamid": NO_COACH_TEAM,
             "ep": START_EP, "items": [], "bag": [], "depot": [], "favorites": [], "scout_used": {}, "temp": True}
    return c


def ep_coach_me(query, body):
    st = _state()
    return _json(coach_me(st, _default_slot(st)))


def ep_coach_onready(query, body):
    # POST /coach/me/onready comes right after CoachProxy.invalidate() -> reset() (which nulls
    # _squad); without /coach/me in this reply the menu is built with squad null (black FREEROAM).
    # /inbox/my/notifications (mang) lam SystemMessageProxy san sang - nguon du lieu duy nhat cua no:
    # thieu thi MenuScreenMediator.onReady khong chay, man Tin nhan khong gan va nut Tin nhan khong lam gi.
    st = _state()
    return _json({"result": True, "resources": coach_me_resources(st, _default_slot(st))
                  + [{"uri": "/inbox/my/notifications", "body": []}]})


NOTE_MS = [0]  # thoi diem thong bao cuoi: AlarmWindow bo thong bao khong muon hon thong bao truoc


def note(category, kind, details, now=None):
    """Mot thong bao he thong (SystemMessageProxy: /inbox/my/notifications[/latest]); date tang dan.
    details.title = dong chu trong danh sach (khoa ngon ngu hoac chu)."""
    ms = NOTE_MS[0] = max(int((time.time() if now is None else now) * 1000), NOTE_MS[0] + 1)
    date = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms // 1000)) + f".{ms % 1000:03d}Z"
    return {"_id": new_iid(), "category": category, "type": kind, "date": date, "details": details}


# Du lieu rieng cua tung HLV (cung tai khoan co 2 HLV): giai dau, cup/World Cup, tran dang da, chien
# thuat, de nghi chuyen nhuong... Thieu "tactics"/"wc_record" -> HLV 2 nhan chien thuat/ky luc cua HLV 1.
COACH_CONTEXT_KEYS = ('league2', 'league2_archives', 'cups', 'offers', 'match', 'trade', 'cup_pending',
                      'tactics', 'wc_record', 'cup_records', 'room', 'ladder_pending')


def ep_default_coach(slot, query, body):
    # PUT /coach/<slot>/default: the client calls setLocalCoachId(body.resources[0].body.id);
    # resources are also fed to cached resources, so /coach/me (CoachProxy) goes first.
    st = _state()
    if not any(c['slot'] == slot and c.get('state', 'active') == 'active' for c in st['coaches']):
        return _json({'result': False, 'reason': 'INVALID_COACH'})
    import copy
    previous = _default_slot(st)
    if previous != slot:
        context_keys = COACH_CONTEXT_KEYS
        contexts = st.setdefault('coachcontexts', {})
        contexts[str(previous)] = {k: copy.deepcopy(st[k]) for k in context_keys if k in st}
        for key in context_keys:
            st.pop(key, None)
        st.update(copy.deepcopy(contexts.get(str(slot), {})))
    st['defaultcoachid'] = coach_id(slot)
    daily_login(st)  # first login of the day: today's reward goes to the Bag
    if str(slot) in st["teams"]:
        balance_first_team(st["teams"][str(slot)])
        fix_formation(st["teams"][str(slot)], coach_of(st, slot)["teamid"])
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


TACTIC_SLOTS = 6


def default_tactic(i):
    # TacticsController.setCurrentTactics fields; sliders 1..100, positioning/defenderline are
    # 0/1 radios, teammentality 1..5. name "" -> client fills the localized preset name.
    return {"id": i, "name": "", "teammentality": 3,
            "bus": {"buildupspeed": 50, "passing": 50, "positioning": 0},
            "cc": {"passing": 50, "crossing": 50, "shooting": 50, "positioning": 0},
            "def": {"mentality": 50, "aggression": 50, "teamwidth": 50, "defenderline": 0}}


def _tactics(st):
    slot = str(_default_slot(st))
    saved = st.setdefault("tactics", {}).setdefault(slot, {})
    return saved, [saved.get(str(i)) or default_tactic(i) for i in range(TACTIC_SLOTS)]


def ep_tactic_all(query, body):
    # GET /game/my/tactic/all -> Array of 6 tactics (TeamManagementTeamTacticsTabPage.onReceived)
    return _json(_tactics(_state())[1])


def ep_tactic(i, query, body):
    """Save (PUT, body = tactic) or reset (DELETE -> {result, resources:[{body: default}]})."""
    req = json.loads(body or b"{}")
    st = _state(); saved, _ = _tactics(st)
    if "bus" in req:
        saved[str(i)] = dict({k: v for k, v in req.items() if k != "_method"}, id=i)
        _save(st)
        return _json({"result": True})
    saved.pop(str(i), None); _save(st)
    return _json({"result": True, "resources": [{"uri": f"/game/my/tactic/{i}", "body": default_tactic(i)}]})


# --------------------------------------------------------------------------- #
# Single > Liga (League 2.0). LeagueCupProxy builds the whole calendar client-side from
# /league2/me: league.teams + openingday, one match per round from createSchedule (ported
# below as league_round). The server keeps results and the table:
#   creation screen: GET /league2/me (difficultybest/difficultyselect), /league2/reward,
#     POST /league2/sponsor/<difficulty>, POST /league2/calendar {difficulty, sponsor}
#   a round: POST /league2/match (-> /match/<id> resource, stage created) ->
#     PUT /match/<id>/side/<s>/ready (-> stage playing -> StartSingleMatch) ->
#     PUT /match/<id>/end {home:{id,score}, away:{id,score}} -> leagueparam + /league2/me
#   or POST /league2/match/sim (no play), POST /league2/sleep (bye round)
# laststatus "league:<round>" tells the client which day it is (LeagueCupProxy.setToday).
# --------------------------------------------------------------------------- #
LEAGUE_SIZE = 64  # = moi doi cua giai (user: "mo khoa tat ca doi bong thay vi gioi han 20"; giai lon nhat 40 doi); man tao giai chi ve 10 logo dau (makeSchedule lap 10 lan),
# bang xep hang la ScrollingList nen cuon duoc


def _db():
    c = sqlite3.connect(gen_teams.DB); c.row_factory = sqlite3.Row
    return c


def league_teams(leagueid, mine):
    """Use the same valid domestic/national rosters as the team selector."""
    choices = next((ts for _, leagues in club_index() for lid, ts in leagues if lid == leagueid), [])
    return [mine] + [tid for tid in choices if tid != mine][:LEAGUE_SIZE - 1]


def league_round(teams, rnd):
    """Fixtures [(home, away)] of round rnd: LeagueCupProxy.createSchedule/getRotatedIndexAt
    (circle method; second half swaps home/away). None = bye (odd team count)."""
    n = len(teams); size = n + n % 2; per = size - 1
    flip, r = (rnd // per) % 2 == 1, rnd % per

    def rot(i):
        k = 0 if i == 0 else (i - 1 + r) % per + 1
        return teams[k] if k < n else None
    pairs = []
    for k in range(size // 2):
        a, b = rot(k), rot(size - 1 - k)
        f = (not flip) if (k == 0 and r % 4 > 1) else flip
        pairs.append((b, a) if f else (a, b))
    return pairs


def _poisson(rng, lam):
    lim, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= lim:
            return k
        k += 1


def team_strength(st, teamid):
    """Average rating (+ grade boost) of the best 11."""
    raw = st["teams"].get(str(_default_slot(st))) if teamid == my_teamid(st) and teamid not in WC_TEAM_CONF else None
    ovrs = sorted((player_ovr(p) for p in (raw or roster_team(teamid))["players"]), reverse=True)[:11]
    return sum(ovrs) / len(ovrs) if ovrs else 60


def sim_score(rng, home, away):
    """ponytail: Poisson goals from the rating gap (+3 home edge); no match engine."""
    d = (home + 3 - away) / 10
    return _poisson(rng, max(0.3, 1.4 + 0.6 * d)), _poisson(rng, max(0.2, 1.1 - 0.6 * d))


def league_stats(teams, records, prev=None):
    """league.statistics {teamid: {wins, draws, losses, goals, goalsconceded, awaygoals,
    point, rank, before{rank, point}}} (LeagueCupProxy.makeLeagueRanking)."""
    s = {str(t): {"wins": 0, "draws": 0, "losses": 0, "goals": 0, "goalsconceded": 0,
                  "awaygoals": 0, "point": 0} for t in teams}
    for m in records:
        h, a = s[str(m["home"])], s[str(m["away"])]
        gh, ga = m["goals"]["home"], m["goals"]["away"]
        h["goals"] += gh; h["goalsconceded"] += ga
        a["goals"] += ga; a["goalsconceded"] += gh; a["awaygoals"] += ga
        if gh != ga:
            win, lose = (h, a) if gh > ga else (a, h)
            win["wins"] += 1; win["point"] += 3; lose["losses"] += 1
        else:
            h["draws"] += 1; a["draws"] += 1; h["point"] += 1; a["point"] += 1
    order = sorted(teams, key=lambda t: tuple(-v for v in (
        s[str(t)]["point"], s[str(t)]["goals"] - s[str(t)]["goalsconceded"], s[str(t)]["goals"], s[str(t)]["awaygoals"])))
    for i, t in enumerate(order):
        s[str(t)]["rank"] = i + 1
    for t, v in s.items():
        p = (prev or {}).get(t) or v
        v["before"] = {"rank": p["rank"], "point": p["point"]}
    return s


def my_teamid(st):
    return coach_of(st)["teamid"]


def l2_me(st):
    """GET /league2/me body; LeagueCupProxy.fetch* read each key as a path."""
    teamid = my_teamid(st)
    lg = st.get("league2") or {}
    if lg.get("league") and teamid not in lg["league"].get("teams", []):  # giai cua CLB khac (issue #40): man tao giai
        lg = {"difficultyselect": lg.get("difficultyselect", 10)}
    me = {"teamid": teamid, "leagueid": lg.get("leagueid") or roster_summary(teamid).get("league", {}).get("id", 0),
          # creation screen: selectable = difficultybest - 10 (24 unlocks all 10 normal + 5
          # ultimate levels), preselected = difficultyselect - 10
          "difficultybest": 24, "difficultyselect": lg.get("difficultyselect", 10),
          # no cupqualifier/cupfinal keys: any object there makes fetchCupQualifier build
          # cup groups (makeLeagueCupGroupTeams -> TypeError on missing teamgroups)
          "laststatus": lg.get("laststatus", ""), "cupinfo": {"entered": False},
          "leader": {"league": {}, "cup": {}},
          # MenuScreen: season.status started/finished -> league front, else creation
          "sponsoroffice": lg.get("sponsoroffice", {"season": {"status": "none"}})}
    if lg.get("league"):
        me.update(openingday=lg["openingday"], league=lg["league"])
    return me


def l2_resources(st):
    return [{"uri": "/league2/me", "body": l2_me(st)}]


def repair_league_context(st):
    lg = st.get("league2") or {}
    league = lg.get("league") or {}
    if not league:
        return False
    teamid = my_teamid(st)
    teams = league.get("teams", [])
    owner = lg.get("teamid", teams[0] if teams else None)
    leagueid = roster_summary(teamid).get("league", {}).get("id", 0)
    if owner == teamid and lg.get("leagueid") == leagueid and teamid in teams:
        return False
    # Preserve the previous season rather than relabel its fixtures as a different team.
    st.setdefault("league2_archives", []).append({"slot": _default_slot(st),
        "reason": "Đội đã thay đổi", "archivedat": int(time.time()*1000), "season": lg})
    st["league2"] = {"difficultyselect": lg.get("difficultyselect", 10)}
    return True


def ep_l2_me(query, body):
    st = _state()
    if repair_league_context(st):
        _save(st)
    return _json(l2_me(st))


def ep_l2_reward(query, body):
    # LeagueCupCreationScreen.onCalendarRewardsResourceReceived: Array indexed by difficulty
    # level (0-9 normal, 10-14 ultimate) of {ep, items[]}
    return _json([{"ep": 20000 * (i + 1), "items": []} for i in range(15)])


# sponsorid -> its mission id; LEAGUE2SPONSOR.mission[mid] = League2_sponsor_title_<mid> with
# the target in "lv1": 2 = finish in the top N, 3 = N wins in a row, 4 = N clean sheets in a row
SPONSORS = {"1": "2", "2": "3", "3": "4"}
MISSION_TARGET = {"2": 3, "3": 3, "4": 2}  # keep in sync with LEAGUE2SPONSOR lv1


def ep_l2_sponsor(difficulty, query, body):
    """POST /league2/sponsor/<difficulty>: offers. OnReceivedSponsorList reads level, maxround
    and sponsors[] {_id, sponsorid, missions[{mid}]} (names/logos/goals from LEAGUE2SPONSOR)."""
    st = _state(); me = my_teamid(st)
    n = len(league_teams(roster_summary(me).get("league", {}).get("id", 0), me))
    return _json({"result": True, "level": 1, "maxround": 2 * (max(2, n) - 1) - 1,
                  "sponsors": [{"_id": sid, "sponsorid": sid, "missions": [{"mid": mid}]} for sid, mid in SPONSORS.items()]})


def mission_progress(st, mid):
    """(progress args, achieved) of a sponsor mission from my league results so far."""
    lg = st.get("league2") or {}; league = lg.get("league") or {}
    me = my_teamid(st)
    mine = sorted((m for m in league.get("matchrecords", []) if me in (m["home"], m["away"])), key=lambda m: m["round"])
    goals = [(m["goals"]["home"], m["goals"]["away"]) if m["home"] == me else (m["goals"]["away"], m["goals"]["home"]) for m in mine]

    def best_run(ok):
        best = run = 0
        for g in goals:
            run = run + 1 if ok(g) else 0; best = max(best, run)
        return best
    target = MISSION_TARGET.get(mid, 1)
    if mid == "2":
        rank = league.get("statistics", {}).get(str(me), {}).get("rank", 0)
        done = len(mine) > league.get("maxround", 0)  # season over
        return [rank], done and 0 < rank <= target
    value = best_run((lambda g: g[0] > g[1]) if mid == "3" else (lambda g: g[1] == 0))
    return [value], value >= target


def ep_l2_achievement(query, body):
    """PUT /league2/sponsor/achievement -> the chosen sponsor's goals for the calendar tab
    (setSponsorGoalList: missions[{mid, achievement, progress[args]}]; getProgressString fills
    LEAGUE2SPONSOR.mission[mid].progress with progress)."""
    st = _state(); lg = st.get("league2") or {}
    mid = SPONSORS.get(str(lg.get("sponsor")), "2")
    progress, done = mission_progress(st, mid)
    return _json({"result": True, "level": 1, "missions": [{"mid": mid, "achievement": done, "progress": progress}]})


# Club creation > "Tim lain" (StartupTeamCreationScreen + TeamSelectionUtil): nation -> league ->
# club steppers from SORTNATION [nationid], SORTLEAGUE {nationid: [leagueid]} and SORTTEAM
# {leagueid: [teamid]}; the picked club's squad is /static/info/authentic/team/licensed/<id>
# ({teamid, summary, team}, built by gen_teams.team like the six recommended clubs).
CLUB_MIN_PLAYERS = 18
_CLUBS = []
with open(os.path.join(HERE, "gamedata", "club_catalog.json"), encoding="utf-8") as _f:
    _CLUB_CATALOG = json.load(_f)
_CLUB_TEAMS = {t["teamid"]: t for t in _CLUB_CATALOG["teams"]}
_CLUB_LEAGUES = {t["leagueid"]: t for t in _CLUB_CATALOG["leagues"]}
_CLUB_NATIONS = {t["nationid"]: t for t in _CLUB_CATALOG["nations"]}
_CLUB_LINKS = {t["teamid"]: t["leagueid"] for t in _CLUB_CATALOG["leagueteamlinks"]}
_NATIONAL_TEAMS = {n["teamid"]: n["nationid"] for n in _CLUB_NATIONS.values()
                   if n["teamid"] in _CLUB_TEAMS and n["teamid"] > 0}
_CLUB_LINKS.update({tid: 78 for tid in _NATIONAL_TEAMS})

_CLUB_STADIUMS = {t["stadiumid"]: t for t in _CLUB_CATALOG["stadiums"]}
_CLUB_STADIUM_LINKS = {t["teamid"]: t["stadiumid"] for t in _CLUB_CATALOG["teamstadiumlinks"]}


def club_summary_response(teamid):
    r = roster(teamid)
    summary = dict(r["summary"])
    info = _CLUB_TEAMS.get(teamid, {})
    leagueid = _CLUB_LINKS.get(teamid, summary.get("league", {}).get("id", 0))
    league = _CLUB_LEAGUES.get(leagueid, {})
    nationid = league.get("countryid", 0)
    # National squads and Rest of World must not inherit the league's placeholder nation.
    if leagueid == 78:
        nationid = next((n["nationid"] for n in _CLUB_NATIONS.values() if n["teamid"] == teamid), 0)
    elif leagueid == 76:
        nationid = next((n["nationid"] for n in _CLUB_CATALOG.get("rowteamnationlinks", []) if n["teamid"] == teamid), 0)
    summary.update(id=teamid, teamid=teamid,
                   name=info.get("teamname") or summary.get("name", ""),
                   # nationid: man chon doi cua Cup (CupCreationTeamSelectionScene) doc summary.league.nationid
                   # cho ten + co quoc gia (thieu -> "World" + co trong)
                   league={"id": leagueid, "name": league.get("leaguename", ""), "nationid": nationid},
                   nation={"id": nationid, "name": _CLUB_NATIONS.get(nationid, {}).get("nationname", "")},
                   rival={"name": _CLUB_TEAMS.get(info.get("rivalteam"), {}).get("teamname", "")})
    stadiumid = _CLUB_STADIUM_LINKS.get(teamid)
    stadium = _CLUB_STADIUMS.get(stadiumid, {})
    stadium = {"id": stadiumid or 0, "name": stadium.get("name", "N/A"),
               "yearbuilt": stadium.get("yearbuilt", "N/A"), "capacity": stadium.get("capacity", "N/A")}
    # Doi doi (TeamChangeScreenSelectPage.update3DPlayerInfo) dua kit.availables.<home|away|goalie>.id
    # vao native changeRight3DPlayerOnTheFly - cung ham voi man chon doi World Cup, noi id = teamid << 5
    # da duoc kiem chung (14001370 << 5 -> ao ca ro). teamid << 10 o day = doi (teamid << 5) khong ton tai.
    kit = {"availables": {k: {"id": int(teamid) << 5 | t} for k, t in (("home", 0), ("away", 1), ("goalie", 2))}}
    return {**summary, "summary": summary, "stadium": stadium, "kit": kit}


_CLUBS_KIT = []


@functools.lru_cache(maxsize=1)
def kit_teams():
    """teamid co ao 3D trong client (data/sceneassets/kit/kit_<id>_*.rx3 trong data_*.big hoac file roi
    data/sceneassets/kit), None khi khong doc duoc client. Da voi CLB khong co ao -> game crash truoc khi vao
    tran (6 CLB K League 2: Ansan Greeners, Asan, Bucheon, Anyang, Gyeongnam, Seoul E-Land; theo ban Indo
    a135d79, 2026-11-10)."""
    try:
        import glob
        cb = os.path.join(HERE, "tools", "clientbuild")
        if cb not in sys.path:
            sys.path.insert(0, cb)
        import bigdir
        kits = set()
        for big in glob.glob(os.path.join(GAME, "data", "data_*.big")):
            for n, *_ in bigdir.entries(big):
                m = re.match(r"data/sceneassets/kit/kit_(\d+)_", n)
                if m:
                    kits.add(int(m.group(1)))
        loose = os.path.join(GAME, "data", "sceneassets", "kit")
        for f in (os.listdir(loose) if os.path.isdir(loose) else ()):
            m = re.match(r"kit_(\d+)_", f)
            if m:
                kits.add(int(m.group(1)))
    except Exception as e:
        print(f"client kits: {e!r}", flush=True)
        return None
    return kits or None


def has_kit(teamid):
    kits = kit_teams()
    return kits is None or int(teamid) in kits


def club_index():
    """all_clubs() bo CLB khong co ao trong client (has_kit): chon CLB, giai dau, da voi may, cup.
    Giai / quoc gia khong con CLB nao thi bo luon."""
    if not _CLUBS_KIT:
        _CLUBS_KIT[:] = [(n, ls) for n, ls in ((n, [(lid, ts) for lid, ts in ((lid, [t for t in ts if has_kit(t)])
                                                                             for lid, ts in leagues) if ts])
                                               for n, leagues in all_clubs()) if ls]
    return _CLUBS_KIT


def all_clubs():
    """Native country/league links; alphabetical clubs and top division first (ca CLB khong co ao)."""
    if not _CLUBS:
        with closing(_db()) as c:
            rows = c.execute("""SELECT p.teamid, COUNT(DISTINCT p.playerid),
                MAX(p.preferredposition1 = 0) FROM players p
                JOIN permid m ON m.spid=p.spid AND m.licensed=1
                WHERE p.spid / 1000000 = ? GROUP BY p.teamid""",
                (gen_teams.CURRENT_SEASON,)).fetchall()
        leagues = {}
        for tid, count, goalie in rows:
            lid = _CLUB_LINKS.get(tid)
            if count < CLUB_MIN_PLAYERS or not goalie or lid not in _CLUB_LEAGUES or lid in (76, 78, 2028):
                continue
            leagues.setdefault(lid, []).append(tid)
        # CLB them tay (gen_teams.EXTRA_SQUADS, vd AC Milan 47): khong co the nao mang teamid nay
        for tid in gen_teams.EXTRA_SQUADS:
            lid = _CLUB_LINKS.get(tid)
            if tid in _CLUB_TEAMS and lid in _CLUB_LEAGUES and tid not in leagues.get(lid, []):
                leagues.setdefault(lid, []).append(tid)
        nations = {}
        for lid, teams in leagues.items():
            nation = _CLUB_LEAGUES[lid]["countryid"]
            teams.sort(key=lambda tid: (_CLUB_TEAMS[tid]["teamname"].casefold(), tid))
            nations.setdefault(nation, []).append((lid, teams))
        # International is a dedicated category, never mixed with domestic leagues.
        with closing(_db()) as c:
            counts = {n:(total,goalie) for n,total,goalie in c.execute("""
                SELECT p.nationality, COUNT(DISTINCT p.playerid), MAX(p.preferredposition1=0)
                FROM players p JOIN permid m ON m.spid=p.spid AND m.licensed=1
                WHERE p.spid / 1000000 = ? GROUP BY p.nationality""",
                (gen_teams.CURRENT_SEASON,))}
        internationals = [tid for tid,n in _NATIONAL_TEAMS.items()
                          if counts.get(n,(0,0))[0]>=CLUB_MIN_PLAYERS and counts[n][1]]
        if internationals:
            nations[75] = [(78, sorted(internationals, key=lambda tid:_CLUB_TEAMS[tid]["teamname"].casefold()))]
        for nation in sorted(nations, key=lambda n: _CLUB_NATIONS.get(n, {}).get("nationname", "")):
            ls = sorted(nations[nation], key=lambda x: (_CLUB_LEAGUES[x[0]]["level"], x[0]))
            _CLUBS.append((nation, ls))
    return _CLUBS


def ep_sortnation(query, body):
    return _json([n for n, _ in club_index()])


def ep_sortleague(query, body):
    return _json({str(n): [lid for lid, _ in ls] for n, ls in club_index()})


def ep_sortteam(query, body):
    return _json({str(lid): ts for _, ls in club_index() for lid, ts in ls})


def ep_authentic_team(kind, teamid, query, body):
    """/static/info/authentic/team/licensed|all/<teamid>: a club's starter squad {teamid, summary,
    team} (responses/ files override for the recommended clubs). StartupPlayerSelectionScreen
    (after "Tim lain") looks up team.captain among team.players by id and dresses its 3D player
    in kit.availables.home/goalie."""
    r = roster(int(teamid))
    # state "first": man chon doi Cup chi liet ke cau thu doi hinh chinh (Enums.isFirstTeam(state)) o o
    # xem cau thu 3D; thieu -> danh sach rong, loi #1009 va con sot ten cau thu cua doi truoc
    first = {str(x.get("id") or x.get("spid")) for x in r["team"]["squad"].get("positions", [])
             if x.get("pos") is not None and int(x.get("pos") or 0) < 28}
    team = dict(r["team"], players=[dict(p, id=str(p["spid"]), **({"state": "first"} if not first or str(p.get("id") or p["spid"]) in first
                                                                     or str(p["spid"]) in first else {}))
                                    for p in r["team"]["players"]])
    team["captain"] = str(team["squad"].get("captain") or "")
    return _json({**r, **club_summary_response(int(teamid)), "team": team, "teamid": int(teamid)})


def ep_l2_calendar(query, body):
    """POST /league2/calendar {difficulty, sponsor}: start a season."""
    req = json.loads(body or b"{}")
    st = _state(); teamid = my_teamid(st)
    lid = roster_summary(teamid).get("league", {}).get("id", 0)
    teams = league_teams(lid, teamid)
    day = int(time.time() // 86400 * 86400 * 1000)  # today 00:00 UTC, ms
    st["league2"] = {"teamid": teamid, "leagueid": lid, "difficultyselect": int(req.get("difficulty") or 10),
                     "sponsor": req.get("sponsor"), "laststatus": "opening", "openingday": day,
                     "sponsoroffice": {"season": {"status": "started"}},
                     "league": {"teams": teams, "maxround": 2 * (len(teams) - 1) - 1, "nextround": 0,
                                "matchrecords": [], "statistics": league_stats(teams, [])}}
    _save(st)
    return _json({"result": True, "resources": l2_resources(st)})


def league_play_round(st, rnd, mine=None, seed=None):
    """Play league round rnd: my fixture gets `mine` (home, away goals) or is simulated,
    the rest is simulated. Updates table + laststatus, returns the client's leagueparam."""
    lg = st["league2"]; league = lg["league"]
    if any(m["round"] == rnd for m in league["matchrecords"]):
        return {"matchtype": "league", "round": rnd, "leaguematches": [m for m in league["matchrecords"] if m["round"] == rnd]}
    rng = random.Random(seed)
    me = my_teamid(st); played = []
    for h, a in league_round(league["teams"], rnd):
        if h is None or a is None:
            continue
        if mine is not None and me in (h, a):
            gh, ga = mine
        else:
            gh, ga = sim_score(rng, team_strength(st, h), team_strength(st, a))
        if me in (h, a):  # da hoac gia lap: thanh tich Giai dau trong Info Manager
            record_match(st, "league", *((gh, ga) if h == me else (ga, gh)))
        played.append({"type": "league", "matchtype": "league", "round": rnd, "home": h, "away": a,
                       "goals": {"home": gh, "away": ga}})
    league["matchrecords"] += played
    league["statistics"] = league_stats(league["teams"], league["matchrecords"], league["statistics"])
    league["nextround"] = rnd + 1
    lg["laststatus"] = f"league:{rnd}"
    return {"matchtype": "league", "round": rnd, "leaguematches": played}


def league_match_body(st, req, playtype):
    me = coach_me(st, _default_slot(st)); teamid = me["teamid"]
    opp = req["away"] if req["home"] == teamid else req["home"]
    user = {"id": me["id"], "name": me["name"], "levelinfo": me["levelinfo"]}
    lg = st["league2"]
    match = new_match("League_2_league_MGR" if playtype == "mgr" else "League_2_league",
                      match_side(st["teams"][str(_default_slot(st))], teamid, user, _tactics(st)[1]),
                      match_side(roster_team(opp), opp, mode="league"))
    gs = match["gamesettings"]
    gs["home"] = req["home"] == teamid  # I am always the left side
    gs["kit"] = ["home", "away"] if gs["home"] else ["away", "home"]
    apply_prematch(st, match)
    # league level 10..24: the in-match overlay shows LEAGUE2_DIFFICULTY_<difficulty - 9>
    gs["difficulty"] = lg.get("difficultyselect", 10)
    # MatchProxy.leagueparam: LeagueCupGameSettingScreen.onPlaying reads leagueparam.matchtype
    # unguarded (`p != null && t == "cupqualifier" || p.matchtype == "cupfinal"`) -> required
    match["leagueparam"] = {"matchtype": req.get("matchtype", "league"), "round": req.get("round", 0),
                            "home": req["home"], "away": req["away"], "leagueid": lg.get("leagueid")}
    return match


def sim_events(match, mine, opp, rng=random):
    """eventlist cho man mo phong Giai dau (LeagueCupFrontScreenSimulation.rebuildSimulationEvent):
    [{time 1..90, team 0 = minh / 1, player = spid cau thu da chinh, event}] theo thoi gian. Moi
    GOAL_EVENT cong vao ti so hien tren man (co ASSIST_EVENT truoc mot so ban); SHOT_ON_GOAL_EVENT la
    sut truot neu khong co SAVE_EVENT cua thu mon theo sau; YELLOW_CARD_EVENT thu hai = the do.
    Truoc day gui rong -> tran mo phong luon hien 0-0, khong dien bien."""
    xi = [[(int(p["spid"]), p["pos"]) for p in match["side"][s]["team"]["squad"]["positions"] if p["pos"] < POS_SUB]
          for s in ("left", "right")]
    keeper = [next((s for s, pos in x if pos == 0), x[0][0]) for x in xi]
    def outfield(t, w=(1, 3, 6)):  # HV / TV / TD: tien dao ghi ban va sut nhieu nhat
        field = [(s, w[0] if pos <= 8 else w[1] if pos <= 19 else w[2]) for s, pos in xi[t] if pos]
        return rng.choices([s for s, _ in field], [n for _, n in field])[0]
    ev = []
    for t, goals in ((0, mine), (1, opp)):
        for _ in range(goals):
            minute, scorer = rng.randint(1, 90), outfield(t)
            if rng.random() < 0.6:
                ev.append({"time": minute, "team": t, "player": outfield(t, (2, 4, 2)), "event": "ASSIST_EVENT"})
            ev.append({"time": minute, "team": t, "player": scorer, "event": "GOAL_EVENT"})
    for _ in range(rng.randint(3, 7)):
        t, minute = rng.randint(0, 1), rng.randint(1, 90)
        ev.append({"time": minute, "team": t, "player": outfield(t), "event": "SHOT_ON_GOAL_EVENT"})
        if rng.random() < 0.6:
            ev.append({"time": minute, "team": 1 - t, "player": keeper[1 - t], "event": "SAVE_EVENT"})
    for _ in range(rng.randint(0, 3)):
        t = rng.randint(0, 1)
        ev.append({"time": rng.randint(1, 90), "team": t, "player": outfield(t, (4, 3, 1)), "event": "YELLOW_CARD_EVENT"})
    return sorted(ev, key=lambda e: e["time"])  # on dinh: kien tao truoc ban, cuu thua sau cu sut


def sim_result(match, events, my_home, rng=random):
    """match.result cho tran mo phong, cung dang bao cao ma tran da that gui o /match/<id>/end
    (PostMatchResultDefaultScene.onResultReceived): ti so, chi so tung cau thu, kiem soat bong, MOTM.
    id/dbid = id cau thu (spid % 1e6), spid = spid cua the; y tinh tu khung thanh xa."""
    pair = lambda lo, hi, rate: (lambda n: [round(n * rate), n])(rng.randint(lo, hi))
    teams = []
    for t, s in ((0, "left"), (1, "right")):
        pos = match["side"][s]["team"]["squad"]["positions"]
        players = []
        for p in (p for p in pos if p["pos"] < POS_SUB):
            mine = [e["event"] for e in events if e["team"] == t and e["player"] == int(p["spid"])]
            goals, assists, shots = mine.count("GOAL_EVENT"), mine.count("ASSIST_EVENT"), mine.count("SHOT_ON_GOAL_EVENT")
            saves, yellow = mine.count("SAVE_EVENT"), mine.count("YELLOW_CARD_EVENT")
            pid = int(p["spid"]) % 1000000
            start = p.get("starting") if isinstance(p.get("starting"), list) and len(p["starting"]) >= 4 else [1, 1, 0.5, 0.5]
            players.append({
                "id": str(pid), "spid": int(p["spid"]), "dbid": pid, "role": p["pos"], "x": start[2], "y": round(1 - start[3], 4),
                "rating": round(min(10.0, max(4.0, 6.0 + goals + 0.5 * assists + 0.3 * saves - 0.4 * yellow
                                                   + rng.uniform(-0.4, 0.9))), 1),
                "goals": goals, "assists": assists, "saves": saves, "shotsOnGoal": goals + shots,
                "shotAttempt": goals + shots + rng.randint(0, 1) * bool(p["pos"]),
                "passes": pair(20, 50, rng.uniform(0.7, 0.92)) if p["pos"] else pair(5, 15, 0.8),
                "dribbles": pair(0, 6, 0.6), "tackles": pair(0, 5, 0.6) if p["pos"] else [0, 0],
                "cards": [min(yellow, 1), int(yellow >= 2)], "injure": [0, 0],
                "runDistanceMeter": round(rng.uniform(8000, 12000) if p["pos"] else rng.uniform(3000, 5000), 1),
                "longTermStamina": 1})
        total = lambda k: sum(p[k] for p in players)
        sums = lambda k: [sum(p[k][0] for p in players), sum(p[k][1] for p in players)]
        teams.append({
            "id": match["side"][s]["team"]["teamid"], "score": total("goals"), "goals": total("goals"),
            "assists": total("assists"), "saves": total("saves"), "shotsOnGoal": total("shotsOnGoal"),
            "shotAttempt": total("shotAttempt"), "passes": sums("passes"), "dribbles": sums("dribbles"),
            "tackles": sums("tackles"), "cards": sums("cards"), "crossPasses": pair(5, 20, 0.35),
            "throughPasses": pair(2, 10, 0.4), "blocks": pair(0, 5, 0.5), "fouls": sums("cards")[0] + rng.randint(2, 8),
            "offsides": rng.randint(0, 3), "clearances": rng.randint(5, 20), "intercepts": rng.randint(5, 20),
            "tackles_inbox": 0, "passes_inbox": 0, "intercepts_inbox": 0, "dribble_inbox": 0,
            "rating": round(sum(p["rating"] for p in players) / max(1, len(players)), 2),
            "runDistanceKm": round(total("runDistanceMeter") / 1000, 1),
            "players": players, "substitutes": [], "users": [], "shootout": 0, "injuredPlayers": 0,
            "reserved": [{"id": str(int(p["spid"]) % 1000000), "rating": 0, "dbid": int(p["spid"]) % 1000000}
                         for p in pos if p["pos"] == POS_SUB]})
    mine_poss = rng.randint(40, 60)  # kiem soat bong cong lai = 100; trai/giua/phai cung vay
    for team, share in zip(teams, (mine_poss, 100 - mine_poss)):
        left = rng.randint(15, 45); center = rng.randint(20, 100 - left - 10)
        team["possession"] = {"total": share, "left": left, "center": center, "right": 100 - left - center,
                              "df": rng.randint(10, 30), "mf": rng.randint(40, 60), "fw": rng.randint(10, 30)}
    me, opp = ("home", "away") if my_home else ("away", "home")
    side = {me: teams[0], opp: teams[1]}
    team_of, best = max(((name, p) for name, team in side.items() for p in team["players"]), key=lambda x: x[1]["rating"])
    return {"myTeam": me, "opponentTeam": opp, "home": side["home"], "away": side["away"], "matchFinishedTime": 5400,
            "forfeits": 0, "motm": {"team": team_of, "id": best["id"], "spid": best["spid"], "dbid": best["dbid"],
                                    "rating": best["rating"], "name": "XXDISPLAYNAMEXX"}}


def ep_l2_match(query, body, sim=False):
    """POST /league2/match {matchtype, playtype, round, home, away, date} -> a /match/<id>
    resource (MatchProxy.registerByResource). /match/sim plays the round right away."""
    req = json.loads(body or b"{}")
    st = _state(); lg = st.get("league2")
    if not lg or not lg.get("league"):
        return _json({"result": False, "reason": "LEAGUE_FAIL"})
    ticket = None
    if sim:
        coach = my_coach(st)
        ticket = next((item for item in coach.get('items', []) if item['itemid'] == 2001 and item.get('remains', 0) > 0), None)
        if ticket is None:
            return _json({'result': False, 'reason': 'ITEM_USE_ERROR'})
    match = league_match_body(st, req, req.get("playtype"))
    st["match"] = match
    lg["pending"] = {"id": match["id"], "round": req.get("round", 0), "home": req["home"], "away": req["away"]}
    out = {"result": True}
    if sim:
        param = league_play_round(st, req.get("round", 0))
        mine = next((m for m in param["leaguematches"] if my_teamid(st) in (m["home"], m["away"])), None)
        match.update(stage="closed", simulated=True)
        match["gamesettings"]["simulated"] = True
        g = mine["goals"] if mine else {"home": 0, "away": 0}
        home_goals, away_goals = int(g["home"]), int(g["away"])
        match["gamesettings"]["prevScore"] = {"home": home_goals, "away": away_goals}
        is_home = bool(mine) and mine["home"] == my_teamid(st)
        mine_g, opp_g = (home_goals, away_goals) if is_home else (away_goals, home_goals)
        events = sim_events(match, mine_g, opp_g)
        # man ket qua sau mo phong (doc match.result) roi man thuong (match.reward) nhu tran da that
        match["result"] = sim_result(match, events, match["gamesettings"]["home"])
        match["reward"] = match_reward(st, match["result"])
        add_ep(st, match["reward"]["money"]["reward"]["total"])
        league_record(st, match["result"])
        lg.pop("pending", None)  # vong nay da da xong
        out.update(leagueparam=param, eventlist=events, score=mine and mine["goals"])
        ticket['remains'] -= 1
        if ticket['remains'] == 0:
            coach['items'].remove(ticket)
    _save(st)
    out["resources"] = [{"uri": f"/match/{match['id']}", "body": match}] + (l2_resources(st) if sim else [])
    if sim:
        out['resources'] += coach_me_resources(st, _default_slot(st))
    return _json(out)


def ep_l2_sleep(query, body):
    """POST /league2/sleep {matchtype, round}: my bye round; the others still play."""
    req = json.loads(body or b"{}")
    st = _state(); lg = st.get("league2")
    param = league_play_round(st, req.get("round", 0)) if lg and lg.get("league") else {}
    _save(st)
    return _json({"result": True, "leagueparam": param, "resources": l2_resources(st)})


def ep_l2_finish(query, body):
    """PUT /league2/abandon: give up the season (back to the creation screen)."""
    st = _state()
    lg = st.get("league2") or {}
    st["league2"] = {"difficultyselect": lg.get("difficultyselect", 10)}
    _save(st)
    return _json({"result": True, "resources": l2_resources(st)})


STAT_LEADERS = 3  # so cau thu moi chi so o tab "Thanh tich tong hop"


def league_record(st, stats):
    """Cong tran giai dau vua da vao thanh tich mua cua cau thu toi (st.league2.playerrecords, mua moi
    thi xoa): tab Thanh tich giai dau > cua toi. Du bi khong vao san khong co rating; passes/tackles
    la [thanh cong, thu]; role = vi tri da choi (Enums.PLAYERPOSITION)."""
    lg = st.get("league2")
    if not isinstance(lg, dict):
        return
    recs = lg.setdefault("playerrecords", {})
    for p, s in match_cards(st, stats):
        try:
            rating = float(s.get("rating") or 0)
        except (TypeError, ValueError):
            rating = 0
        if not rating:
            continue
        r = recs.setdefault(iid(p), dict.fromkeys(("matches", "ratings", "goals", "assists", "passes",
                                                     "passtries", "tackles", "saves"), 0))
        pair = lambda k: (list(s.get(k) or []) + [0, 0])[:2] if isinstance(s.get(k), list) else [0, 0]
        r.update(id=iid(p), spid=p["spid"], role=_num(s.get("role")))
        r["matches"] += 1
        r["ratings"] = round(r["ratings"] + rating, 2)
        for k in ("goals", "assists", "saves"):
            r[k] += _num(s.get(k))
        r["passes"] += _num(pair("passes")[0]); r["passtries"] += _num(pair("passes")[1])
        r["tackles"] += _num(pair("tackles")[0])


def ep_l2_history_players(query, body):
    """GET /league2/history/players/league: [{id: _id the, matches, ratings (tong), goals, assists,
    passes, tackles, saves}] (LeagueCupRecordTabPage.joinPlayerData; the khong co thi hien 0)."""
    return _json(list(((_state().get("league2") or {}).get("playerrecords") or {}).values()))


def ep_l2_leaders(query, body):
    """PUT /league2/leader/league -> Thanh tich tong hop (LeagueCupTotalRecordTabPage): doi hinh tieu
    bieu theo tuyen (so vi tri trong so do cua toi, rating TB cao nhat o vi tri do) va top cau thu
    moi chi so, moi muc {spid, teamid, value}, tu thanh tich mua cua cau thu toi (cau thu AI khong
    luu chi so). Trang gop moi khoa trong reply thanh 1 danh sach -> khong them khoa khac."""
    st = _state(); me = my_teamid(st)
    recs = [r for r in ((st.get("league2") or {}).get("playerrecords") or {}).values() if r["matches"]]
    avg = lambda r: round(r["ratings"] / r["matches"], 1)
    entry = lambda r, v: {"spid": r["spid"], "teamid": me, "value": v}
    squad = (st["teams"].get(str(_default_slot(st))) or {"squad": {"positions": []}})["squad"]["positions"]
    out = {}
    # setBestEleven lap DUNG 11 lan qua FW+MF+DF+GK (khong kiem tra do dai): thieu 1 nguoi -> undefined ->
    # TypeError, ca tab "Chi so chung" trong. Moi tuyen du so o trong so do: thieu thi lay cau thu da chinh
    # cua tuyen do (diem 0.0); tong luon 11 (so do khong du 11 o -> bu bang cau thu da chinh con lai).
    starters = [s for s in squad if s.get("pos", POS_SUB) < POS_SUB and s.get("spid")]
    used = set()
    for line, rng in LINES.items():
        slots = sum(s["pos"] in rng for s in starters)
        rows = [entry(r, avg(r)) for r in sorted((r for r in recs if r["role"] in rng), key=avg, reverse=True)[:slots]]
        for s in starters:
            if len(rows) >= slots:
                break
            if s["pos"] in rng and s["spid"] not in {x["spid"] for x in rows}:
                rows.append({"spid": s["spid"], "teamid": me, "value": 0.0})
        out[line] = rows
        used |= {x["spid"] for x in rows}
    extra = [s for s in starters if s["spid"] not in used]
    while sum(len(out[k]) for k in LINES) < 11 and extra:
        s = extra.pop(0)
        out[next(k for k, rng in LINES.items() if s["pos"] in rng)].append({"spid": s["spid"], "teamid": me, "value": 0.0})
    stat = {"ratings": avg, "goals": lambda r: r["goals"], "assists": lambda r: r["assists"],
            "tackles": lambda r: r["tackles"], "saves": lambda r: r["saves"],
            "passes": lambda r: round(r["passes"] / r["passtries"], 2) if r["passtries"] else 0}  # ti le chuyen dung
    for k, f in stat.items():
        out[k] = [entry(r, f(r)) for r in sorted(recs, key=f, reverse=True) if f(r)][:STAT_LEADERS]
    return _json(out)


def season_leaders(st, me, rank, goals):
    """leaderRewards for LeagueCupLeagueRewardScreen: best XI by line (FW/MF/DF/GK, 11 in
    all) + one leader per stat. ponytail: no per-player season stats are kept, so these
    come from my own squad (rating = card rating, stat values derived from team goals)."""
    team = owned_team(st["teams"][str(_default_slot(st))])
    rating = {p["spid"]: player_ovr(p) for p in team["players"]}
    lines = {"GK": range(0, 1), "DF": range(1, 9), "MF": range(9, 20), "FW": range(20, 28)}
    starters = [p for p in team["squad"]["positions"] if p["pos"] < POS_SUB]
    entry = lambda spid, value, ep=0: {"spid": spid, "teamid": me, "value": value, "reward": {"count": ep}}
    out = {line: [entry(p["spid"], round(min(9.9, 6 + (rating.get(p["spid"], 80) - 80) / 25), 1))
                  for p in starters if p["pos"] in r] for line, r in lines.items()}
    best = lambda line: max((p["spid"] for p in starters if p["pos"] in lines[line]),
                            key=lambda s: rating.get(s, 0), default=0)
    played = 2 * (len((st.get("league2") or {}).get("league", {}).get("teams") or [0] * LEAGUE_SIZE) - 1)
    out.update(ratings=[entry(best("FW"), 7.5, 3000)], goals=[entry(best("FW"), max(1, goals // 2), 3000)],
               assists=[entry(best("MF"), max(1, goals // 3), 2000)], tackles=[entry(best("DF"), played * 3, 2000)],
               passes=[entry(best("MF"), 0.85, 1000)],  # pass success rate; the UI shows value*100 %
               saves=[entry(best("GK"), played * 2, 1000)])
    return out


def ep_l2_end(query, body):
    """PUT /league2/end (closing day): season result for LeagueCupLeagueRewardScreen
    {promotion{current,next}, rewards{coach,money.reward{total,detail}}, sponsorRewards,
    leaderRewards, seasonRewards{items}}. The season stays (the reward screen reads it) but
    is marked finished: back on the front, the closing day then has seasonoff -> ON_CLOSED
    -> the creation screen, whose POST /league2/calendar starts the next season."""
    st = _state(); lg = st.get("league2") or {}; league = lg.get("league")
    if not league:
        return _json({"result": False, "reason": "LEAGUE_FAIL"})
    me = my_teamid(st); stats = league["statistics"][str(me)]; rank = stats["rank"]
    n = len(league["teams"])
    cur = lg.get("difficultyselect", 10)
    if cur >= 20:  # ultimate: promote top 3, demote bottom 3
        nxt = min(24, cur + 1) if rank <= 3 else max(20, cur - 1) if rank > n - 3 else cur
    else:
        nxt = min(19, cur + 1) if rank <= 3 else cur
    mid = SPONSORS.get(str(lg.get("sponsor")), "2")
    progress, done = mission_progress(st, mid)
    with open(os.path.join(RESPONSES, "static_db_LEAGUE2REWARD.json")) as f:
        sponsor_ep = json.load(f)["sponsor"].get(str(int(mid) - 1), {}).get(str(cur), {}).get("count", 0) if done else 0
    leaders = season_leaders(st, me, rank, stats["goals"])
    leader_ep = sum(e["reward"]["count"] for k in ("ratings", "goals", "assists", "tackles", "passes", "saves")
                    for e in leaders[k])
    place = round((n - rank + 1) * 10 / n)  # thuong theo thu hang quy ve thang 10 nhu giai 10 doi cu
    base_ep = place * 2000
    season_exp = place * 100
    cprev, ccur = gain_exp(my_coach(st), season_exp, coach_need, COACH_MAX_LEVEL)
    out = {"result": True, "promotion": {"current": cur, "next": nxt},
           # setRewardRankExp: coach.prev/current/reward.total; setRewardRankEP: money.prev +
           # reward.detail.season; the sponsor/leader scenes read detail.sponsor/.leader
           "rewards": {"coach": {"prev": cprev, "current": ccur, "reward": {"total": season_exp}},
                       "money": {"prev": coach_me(st, _default_slot(st))["money"]["ep"],
                                 "reward": {"total": base_ep + sponsor_ep + leader_ep,
                                            "detail": {"season": base_ep, "sponsor": sponsor_ep, "leader": leader_ep}}}},
           "sponsorRewards": {"sponsorid": str(lg.get("sponsor")), "level": 1, "achievement": done,
                              "missions": [{"mid": mid, "achievement": done, "progress": progress}]},
           "leaderRewards": leaders, "seasonRewards": {"items": []}}
    if (lg.get("sponsoroffice") or {}).get("season", {}).get("status") != "finished":  # 1 lan / mua
        r = coach_record(my_coach(st), "league")
        track, best = ("ultimate", "ultimatebest") if cur >= 20 else ("normal", "noramlbest")
        r[track + "wins"] = r.get(track + "wins", 0) + (rank == 1)
        r[best] = max(r.get(best, 0), max(cur, nxt) + 1)  # Info Manager hien - 10 / - 20
    lg.update(difficultyselect=nxt, sponsoroffice={"season": {"status": "finished"}})
    add_ep(st, out["rewards"]["money"]["reward"]["total"])
    _save(st)
    out["resources"] = l2_resources(st)
    return _json(out)


def match_step(mid, body, step):
    """Apply step(match, request) to the live league match (state["match"]) and answer with
    the updated /match/<id> resource; MatchController re-runs processMatchStageResource."""
    req = json.loads(body or b"{}")
    st = _state(); match = st.get("match")
    if not match or match["id"] != mid:
        return _json({"result": False})
    step(match, req)
    _save(st)
    return _json({"result": True, "resources": [{"uri": f"/match/{mid}", "body": match}]})



def ep_match_squad(mid, side, query, body):
    """Save the user's pre-match XI; instance ids are authoritative after a swap."""
    import copy
    st = _state()
    match = st.get("match")
    if not match or match.get("id") != mid or match.get("stage") not in ("created", "squad", "setting"):
        return _json({"result": False})
    current = match.get("side", {}).get(side)
    if not current or not current.get("players"):
        return _json({"result": False})
    req = json.loads(body or b"{}")
    sq = req.get("squad") or {}
    cards = {iid(p): p for p in current["team"]["players"]}
    positions = []
    for entry in sq.get("positions") or []:
        pid, pos = str(entry.get("id")), entry.get("pos")
        if pid not in cards or type(pos) is not int or not 0 <= pos <= POS_SUB:
            return _json({"result": False})
        positions.append({**entry, "id": pid, "spid": cards[pid]["spid"]})
    starters = [p for p in positions if p["pos"] < POS_SUB]
    if (len(starters) != 11 or len({p["pos"] for p in starters}) != 11
            or len({p["id"] for p in positions}) != len(positions)
            or len(positions) > 18 or not any(p["pos"] == 0 for p in starters)):
        return _json({"result": False})
    if has_dup_player(positions):
        return _json({"result": False, "reason": DUP_PLAYER_REASON,
                      "resources": [{"uri": f"/match/{mid}", "body": match}]})
    team = copy.deepcopy(current["team"])
    team["squad"].update({k: sq[k] for k in ("formation", "workrate") if k in sq})
    team["squad"]["positions"] = positions
    for key in ("captain", "kickers"):
        if key in req or key in sq:
            team["squad"][key] = req.get(key, sq.get(key))
    repair_squad(team)
    team["captain"] = team["squad"]["captain"]
    team["kickers"] = team["squad"]["kickers"]
    team["teamcolors"] = active_teamcolors(team)
    team["summary"] = team_summary(team, team["teamid"])
    current["team"] = team
    current["squad"] = team["squad"]
    # Mui ten cong/thu (starting[0..1]) chi chinh duoc o man hinh doi hinh truoc tran
    # (SquadScreenHandler.updateFormationMode: menu Doi hinh chi hien, khong cho sua) ->
    # ghi lai vao doi hinh CLB de tran sau va menu Doi hinh deu giu muc da chinh.
    if "WorldCup" not in str(match.get("type", "")):
        mine = st.get("teams", {}).get(str(_default_slot(st)))
        if mine:
            wr = {p["id"]: p["starting"][:2] for p in positions if isinstance(p.get("starting"), list) and len(p["starting"]) >= 2}
            for p in mine["squad"]["positions"]:
                s = wr.get(str(p.get("id")))
                if s and isinstance(p.get("starting"), list) and len(p["starting"]) >= 2:
                    p["starting"][:2] = s
    _save(st)
    return _json({"result": True, "resources": [{"uri": f"/match/{mid}", "body": match}]})


def ep_match_gamesettings(mid, query, body):
    """PUT /match/<id>/gamesettings tu man cai dat truoc tran: {kit: home|away, side: 0|1} (nut ao) va
    (man Xep hang) {stadium}, {tod}, {weather}, {halfLength}, {ball: {id}}; nhieu khoa co the gop
    trong mot body. San/thoi diem/thoi tiet/thoi luong/bong chon o day duoc nho cho cac tran sau."""
    req = json.loads(body or b"{}")
    st = _state()
    match = st.get("match")
    if not match or match["id"] != mid or match.get("stage") != "setting":
        return _json({"result": False})
    gs = match["gamesettings"]
    side, kit = req.get("side"), req.get("kit")
    changed = False
    if kit is not None or side is not None:
        if type(side) is not int or side not in (0, 1) or kit not in ("home", "away"):
            return _json({"result": False})
        gs["kit"][side] = kit
        changed = True
    for key, ok in (("stadium", STADIUMS_SAFE), ("tod", TODS), ("weather", WEATHERS), ("halfLength", HALF_LENGTHS)):
        v = req.get(key)
        v = int(v) if (isinstance(v, (int, float)) and not isinstance(v, bool)) or str(v).isdigit() else None
        if v in ok:
            gs[key] = st.setdefault("matchprefs", {})[key] = v
            changed = True
    ball = req.get("ball").get("id") if isinstance(req.get("ball"), dict) else None
    avail = [b.get("id") if isinstance(b, dict) else b for b in (gs.get("ball") or {}).get("availables", [])]
    if not isinstance(ball, bool) and ball is not None and ball in avail:
        gs["ball"]["id"] = ball
        if ball in BALLS:
            st.setdefault("matchprefs", {})["ball"] = ball
        changed = True
    if not changed:
        return _json({"result": False})
    _save(st)
    return _json({"result": True, "resources": [{"uri": f"/match/{mid}", "body": match}]})


def _side_ready(side):
    def step(match, req):
        if side in match["side"]:
            match["side"][side]["ready"] = bool(req.get("ready", True))
        if req.get("ready", True):
            match["stage"] = "playing"
    return step


def ep_match_ready(mid, side, query, body):
    """PUT /match/<id>/side/<side>/ready {ready, stage}: single-player, so the match starts
    (stage playing -> LeagueCupGameSettingScreen.onPlaying -> StartSingleMatch)."""
    return match_step(mid, body, _side_ready(side))


NEXT_STAGE = {"created": "squad", "squad": "setting"}


def _player_ready(match, req):
    if req.get("ready", True) and req.get("stage", match["stage"]) == match["stage"]:
        match["stage"] = NEXT_STAGE.get(match["stage"], match["stage"])


def ep_match_player_ready(mid, query, body):
    """PUT /match/<id>/player/ready {ready, stage}: created -> squad (ON_SQUADING: squad screen),
    squad -> setting (LeagueCupSquadSettingHandler.updateStage: game setting screen)."""
    return match_step(mid, body, _player_ready)


def match_cards(st, stats):
    """[(the cua toi, dong matchStats cua no)] cho cac cau thu ben toi. spid trong tran = id cau thu
    (spid % 1e6); the dang trong doi hinh duoc uu tien hon the khac cua cung cau thu."""
    team, done, out = st["teams"].get(str(_default_slot(st))) or {"players": []}, set(), []
    for s in (stats.get(stats.get("myTeam", "home")) or {}).get("players") or []:
        pid = _num(s.get("spid")) % 1000000
        mine = [p for p in team["players"] if int(p["spid"]) % 1000000 == pid and iid(p) not in done]
        p = next((p for p in mine if in_squad(team, p)), mine[0] if mine else None)
        if p is not None:
            done.add(iid(p))
            out.append((p, s))
    return out


# The thuong sau tran: PostMatchRewardDefaultScene chia 3 the ("normal"); the bam chon hien
# extra.availables[extra.selected.normal[0]] (RewardCardFront.setItemData: staticdata name/unit/
# itemicon, count), openRestCards lat cac the con lai; selected {type: [index]} bo the da chon.
# GM Tool > Tran dau chinh tat ca (gamedata/match_rewards.json): EP + EXP (HLV, moi cau thu) theo
# ket qua, va the [[trong so, itemid | {"ep": so EP}]].
MATCH_REWARD_FILE = os.path.join(HERE, "gamedata", "match_rewards.json")
MATCH_REWARD_DEFAULT = {  # = muc thuong truoc day (EP 2000/1000/500, EXP_WIN/DRAW/LOSS) + the thuong
    # EP x1000 (2026-11-09, user: goi 26TY All +8 ~139 trieu EP chi nen ton 50-100 tran thang -> thang 2 trieu)
    "result": {"win": {"ep": 2_000_000, "coach": EXP_WIN[0], "player": EXP_WIN[1]},
               "draw": {"ep": 1_000_000, "coach": EXP_DRAW[0], "player": EXP_DRAW[1]},
               "loss": {"ep": 500_000, "coach": EXP_LOSS[0], "player": EXP_LOSS[1]}},
    "cards": [[30, {"ep": 100_000}], [25, {"ep": 300_000}], [15, {"ep": 500_000}], [8, {"ep": 1_000_000}],
              [10, 92000300], [7, 92000014], [5, 501]]}  # 92000300 = 26L Top 200 (92000000 khong con)


def match_reward_cfg():
    try:
        with open(MATCH_REWARD_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return MATCH_REWARD_DEFAULT
    # file sua tay / hong (khong phai dict, thieu win/draw/loss): khong de PUT /match/<id>/end vo
    # (mat ket qua tran) - phan thieu lay mac dinh
    d = d if isinstance(d, dict) else {}
    res = d.get("result") if isinstance(d.get("result"), dict) else {}
    result = {k: {**v, **(res.get(k) if isinstance(res.get(k), dict) else {})}
              for k, v in MATCH_REWARD_DEFAULT["result"].items()}
    cards = d.get("cards") if isinstance(d.get("cards"), list) else MATCH_REWARD_DEFAULT["cards"]
    return {**MATCH_REWARD_DEFAULT, **d, "result": result, "cards": cards}


def prize_item(p):
    """Phan thuong ("ep", so) | itemid -> {itemid, count, staticdata}."""
    if isinstance(p, tuple):
        return {"itemid": EP_ITEM, "count": p[1], "staticdata": EP_ITEM_DEF}
    return {"itemid": p, "count": 1, "staticdata": item_def(p)}


def match_cards_reward(st):
    """extra/selected cua match.reward: 3 the, the dau la cua toi (cong ngay tai day). The co vat
    pham khong con trong cua hang bi bo qua (staticdata se loi)."""
    rows = [r for r in match_reward_cfg().get("cards") or [] if isinstance(r, (list, tuple)) and len(r) == 2]
    pool = [(w, ("ep", int(p["ep"])) if isinstance(p, dict) else p) for w, p in rows
            if (isinstance(p, dict) and "ep" in p) or (isinstance(p, int) and (p in ITEMS or p in PACKS))]
    pool = [(w, p) for w, p in pool if isinstance(w, (int, float)) and w > 0]
    if not pool:
        return {"extra": {"selected": {}, "availables": []}, "selected": {}}
    cards = random.choices([p for _, p in pool], [w for w, _ in pool], k=3)
    if isinstance(cards[0], tuple):
        add_ep(st, cards[0][1])
    else:
        inventory_add(my_coach(st), cards[0], 1)
    pick = {"normal": [0]}
    return {"extra": {"selected": pick, "availables": [prize_item(p) for p in cards]}, "selected": pick}


def match_outcome(stats):
    """1 thang, 0 hoa, -1 thua cho ben minh trong matchStats; ti so hoa thi loat luan luu quyet dinh
    (Rank / KOTH co hiep phu + luan luu tu 2026-11-10, theo ban Indo b2cd8e7)."""
    k = lambda s: tuple(_num((stats.get(s) or {}).get(f)) for f in ("score", "shootout"))
    me, opp = k(stats.get("myTeam", "home")), k(stats.get("opponentTeam", "away"))
    return (me > opp) - (me < opp)


def match_reward(st, stats):
    """match.reward for PostMatchRewardDefaultScene.onRewardReceived (paths it reads) and its
    player list (PlayerRewardListItemRenderer: id, spid, prev, current, reward.detail.extra).
    Credits EP, coach exp and exp for every player of mine in matchStats (in-match spid = the
    card's player id, i.e. spid % 1e6) theo gamedata/match_rewards.json, cong 1 the thuong."""
    r = match_reward_cfg()["result"][("loss", "draw", "win")[match_outcome(stats) + 1]]
    ep, cexp, pexp = int(r["ep"]), int(r["coach"]), int(r["player"])
    reward = lambda n: {"total": n, "detail": {"base": n, "extra": 0}}
    prev, cur = gain_exp(my_coach(st), cexp, coach_need, COACH_MAX_LEVEL)
    team = st["teams"].get(str(_default_slot(st))) or {"players": []}
    team['staffsmc'] = int(team.get('staffsmc') or 0) + 1
    players = []
    for p, s in match_cards(st, stats):
        n = pexp + 20 * _num(s.get("goals")) + 10 * _num(s.get("assists"))
        pprev, pcur = gain_exp(p, n, player_need, PLAYER_MAX_LEVEL)
        players.append({"id": iid(p), "spid": p["spid"], "prev": pprev, "current": pcur, "reward": reward(n)})
    return {"matchRewardBackType": 0, **match_cards_reward(st),
            "coach": {"reward": reward(cexp), "prev": prev, "current": cur},
            "money": {"reward": reward(ep)},
            "players": players, "missions": [], "exptankinfo": {}}


def ep_match_get(mid, query, body):
    """GET /match/<id>: the post-match screens re-read result/reward from the match."""
    match = _state().get("match") or {}
    return _json(match if match.get("id") == mid else {})


def _num(v):
    """So tu matchStats (da nhanh PvE gui ti so dang chuoi, co the go sai) -> int, loi = 0."""
    try:
        return int(float(str(v).strip() or 0))
    except (TypeError, ValueError):
        return 0


def fill_players(match, stats):
    """Game gui matchStats voi role 0 va spid = id cau thu (spid % 1e6) du x/y dung. San trong man ket
    qua (GameResultPlayerInfo) dat role 0 = GK o khung thanh -> ca 11 nguoi chong len nhau, va nhan
    mua the lay tu spid (SeasonIcon.setSeasonBySpid) -> moi the hien mua hien tai. players[i] = vi tri
    i trong doi hinh: role = pos cua vi tri do; spid = spid day du cua the (cau thu, du bi, MOTM)."""
    sides = match.get("side") or {}
    if not sides:
        return
    for key, default in (("home", "left"), ("away", "right")):
        t = stats.get(key) or {}
        if not isinstance(t, dict):
            continue
        hits = [s for s in sides if str(sides[s]["team"]["teamid"]) == str(t.get("id"))]
        pos = sides[hits[0] if len(hits) == 1 else default]["squad"]["positions"]
        for p, slot in zip(t.get("players") or [], pos):
            if isinstance(p, dict) and not p.get("role"):
                p["role"] = slot["pos"]
        full = {int(s["spid"]) % 1000000: int(s["spid"]) for s in pos}
        mine = list(t.get("players") or []) + list(t.get("substitutes") or [])
        if (stats.get("motm") or {}).get("team") == key:
            mine.append(stats["motm"])
        for e in mine:
            if isinstance(e, dict) and e.get("spid"):
                e["spid"] = full.get(_num(e["spid"]) % 1000000, e["spid"])


MATCH_END_HOOKS = []  # fn(st, match id, matchStats) sau khi tinh thuong 1 tran (module tinh nang them vao)


# Thanh tich HLV + nhiem vu (manager.py thay cac ham nay khi cai dat; mac dinh khong lam gi)
def coach_record(coach, part):
    return coach.setdefault("record", {}).setdefault(part, {})


def record_match(st, part, gf, ga):
    pass


def record_trade(coach, kind, spid, amount, now=None):
    pass


def mission_event(st, now=None, **counts):
    pass


def achievement_resources(st):
    return []


def ep_match_end(mid, query, body):
    """PUT /match/<id>/end matchStats {home:{id, score, players...}, away:{...}, myTeam,
    opponentTeam, motm, ...}. It comes back as match.result (PostMatchResultDefaultScene
    .onResultReceived reads exactly these fields) plus a match.reward."""
    req = json.loads(body or b"{}")
    try:  # log goc bi cat 2000 ky tu, mat ti so -> ghi rieng ti so + ban thang tung cau thu de doi chieu
        _h, _a = req.get("home") or {}, req.get("away") or {}
        _g = lambda s: sum(_num(x.get("goals")) for x in (s.get("players") or []) if isinstance(x, dict))
        _line = (f"{time.strftime('%H:%M:%S')} MATCH END {mid} home={_h.get('id')} score={_h.get('score')!r} "
                 f"(ban thang cau thu {_g(_h)}) away={_a.get('id')} score={_a.get('score')!r} (ban thang cau thu {_g(_a)}) "
                 f"myTeam={req.get('myTeam')} forfeits={req.get('forfeits')}")
        print(_line, flush=True)
        with open(LOG, "a", encoding="utf-8") as _f:
            _f.write(_line + "\n")
    except Exception:
        pass
    st = _state(); lg = st.get("league2") or {}; p = lg.get("pending")
    match = st.get("match") if (st.get("match") or {}).get("id") == mid else None
    out = {"result": True}
    if match is not None and match.get("stage") != "closed":  # credit once
        fill_players(match, req)
        match["result"] = {k: v for k, v in req.items() if k != "_method"}
        match["reward"] = match_reward(st, req)
        add_ep(st, match["reward"]["money"]["reward"]["total"])
        for hook in MATCH_END_HOOKS:  # rank.ladder_result ...
            hook(st, mid, req)
    cp = st.get("cup_pending") or {}
    if cp.get("id") == mid and cp.get("cupid") in (st.get("cups") or {}):
        h, a = req.get("home") or {}, req.get("away") or {}
        gh, ga = _num(h.get("score")), _num(a.get("score"))
        sh, sa = _num(h.get("shootout")), _num(a.get("shootout"))
        if str(h.get("id")) == str(cp["away"]):
            gh, ga, sh, sa = ga, gh, sa, sh
        st["cups"][cp["cupid"]]["_mine"] = (gh, ga, sh, sa)  # applied by PUT /cup/<id>/next
        st.pop("cup_pending")
    if p and p["id"] == mid:
        h, a = req.get("home") or {}, req.get("away") or {}
        gh, ga = _num(h.get("score")), _num(a.get("score"))
        if str(h.get("id")) == str(p["away"]):  # matchStats home/away are match sides
            gh, ga = ga, gh
        out["leagueparam"] = league_play_round(st, p["round"], (gh, ga))
        league_record(st, req)
        lg.pop("pending", None)
        out["resources"] = l2_resources(st)
    if match is not None:
        match["stage"] = "closed"
        out["resources"] = [{"uri": f"/match/{mid}", "body": match}] + out.get("resources", []) + achievement_resources(st)
    _save(st)
    return _json(out)


# --------------------------------------------------------------------------- #
# Single > Cup (PvE cups; also the "Eropa/Amerika Selatan" shortcut). CupProxy/CupController.
#   GET /cup/list -> metas the front screen lists; GET /cup/record -> my cup records.
# --------------------------------------------------------------------------- #
def cup_meta(metaid, name, trophy, quality, matchcount, teams, reward, desc="PVECUP_INFO_OPTION_NO", skin=None):
    """A /cup/list entry: CupFrontScreen.onCupDetailInternal + CupProxy.onCupListReceived
    fields. difficulty = minimum AI level index (0 amateur .. 4 legendary)."""
    # visible: Boolean (isVisibleValue indexes it per condition otherwise); joinable.allteams
    # skips the teamids/leagueids whitelist ("Tim ini tidak dapat bergabung")
    # name / proviso_desc / phases[].name go through Localization.getLocalizedString: a
    # PVE_CUP_MATCH_TITLE_/DESC_/ROUND_ key shows the game's own text, plain text shows as is.
    # skin: only externalAssets cupskin 1/2 exist (others -> notfound); trophy 13/66/300000.
    return {"metaid": metaid, "_id": str(metaid), "name": name, "type": "pve", "teamselect": "user", "visible": True,
            "difficulty": 0, "quality": quality, "matchcount": matchcount, "teamcount_desc": teams,
            "proviso_desc": desc, "trophy": trophy, "skin": skin or str(metaid),
            # onCupDetailInternal sums bestrewards[-1][*].count where itemid == 1 (EP)
            "bestrewards": [[{"itemid": 1, "count": reward}]],
            "startat": "2020-01-01T00:00:00.000Z", "endat": "2030-12-31T00:00:00.000Z",
            "joinable": {"fee": 0, "igr": False, "vip": 0, "allteams": True, "dayofweek": [1, 1, 1, 1, 1, 1, 1]}}


# Cup formats (CUP_SPECS[metaid]["fmt"]): phases in order, each
#   ("rr", groups, name)      round-robin groups of 4, top 2 go through (A1-B2, B1-A2, ...)
#   ("ko", rounds|None, name) knock-out over `rounds` rounds (None = down to the final); the
#                             winners of its last round start the next phase
# The client (CupMainScreen / CupMainStandingTabPage / CupProxy) works off cup.phases[]
# (system "round-robin"/"knock-out", name, maxrounds, groups[0].rounds) with no fixed phase
# count: a knock-out-only cup is just phases = [knock-out]; the tournament tree is built from
# groups[0].rounds (isFinalPhase = last phase).
# Pools: ("lg", leagueids, n) = n strongest clubs of those leagues (club_index rosters);
#        ("nt", teamids, n) = n strongest of those national teams.
EURO_LEAGUES = (13, 53, 19, 31, 16, 68, 1, 41, 66, 50, 56, 65, 14, 54, 20, 32, 17, 60, 61, 2076)
SAM_LEAGUES = (353, 336)  # Argentina, Colombia (no Brazil/Chile clubs in fifadb)
NONEU_LEAGUES = (353, 336, 39, 83, 2111, 350)  # + MLS, K-League, Saudi
CUP_SPECS = {}
RR_KO = lambda key: [("rr", 4, f"PVE_CUP_MATCH_ROUND_{key}_1"), ("ko", None, f"PVE_CUP_MATCH_ROUND_{key}_2")]


def def_cup(metaid, name, trophy, quality, reward, fmt, pool, desc="PVECUP_INFO_OPTION_NO", skin="1", nteams=16,
            authentic=None):
    """Register a cup: meta for /cup/list + its format/pool. authentic = national teamids the
    player picks from (teamselect "authentic": CupCreationScreen nation/league/team steppers,
    TeamSelectionUtil.rearrangeData keeps only joinable.leagueids/teamids)."""
    n, matches = nteams, 0
    for kind, k, _ in fmt:  # matches the winner plays
        if kind == "rr":
            matches += 3; n = 2 * k
        else:
            r = k or int(math.log2(n)); matches += r; n >>= r
    meta = cup_meta(metaid, name, trophy, quality, matches, nteams, reward, desc, skin)
    if authentic:
        meta.update(teamselect="authentic")
        meta["joinable"].update(allteams=False, leagueids=[78], teamids=list(authentic))
    else:  # cup CLB: chon CLB o man tao cup (quoc gia -> giai -> CLB); chon dung CLB chinh = doi hinh the cua minh
        meta.update(teamselect="authentic")
        clubs = sorted(t for t, lg in _CLUB_LINKS.items() if lg != 78 and t not in _NATIONAL_TEAMS and has_kit(t))
        meta["joinable"].update(allteams=False, leagueids=sorted({_CLUB_LINKS[t] for t in clubs}), teamids=clubs)
    CUP_SPECS[metaid] = {"fmt": fmt, "pool": pool, "n": nteams, "authentic": list(authentic or [])}
    return meta


def nat_cup(metaid, leagues, n, reward, fmt=None, quality=2):
    """National club cup: knock-out, n clubs of the country (names/descs/rounds from the
    game's PVE_CUP_MATCH_*_<metaid> strings)."""
    return def_cup(metaid, f"PVE_CUP_MATCH_TITLE_{metaid}", "13", quality, reward,
                   fmt or [("ko", None, f"PVE_CUP_MATCH_ROUND_{metaid}_1")], [("lg", leagues, n - 1)],
                   desc=f"PVE_CUP_MATCH_DESC_{metaid}", skin="2", nteams=n)


CUPS = [
    # 1/2 keep their metaids (running saves in state.json resume by metaid)
    def_cup(1, "UEFA Champions League", "1001", 4, 60000, [("rr", 4, "Vòng bảng"), ("ko", None, "Vòng loại trực tiếp")],
            [("lg", EURO_LEAGUES, 15)], desc="PVE_CUP_MATCH_DESC_2", skin="3"),
    def_cup(2, "PVE_CUP_MATCH_TITLE_7", "300000", 3, 40000, RR_KO(7), [("lg", SAM_LEAGUES, 15)],
            desc="PVE_CUP_MATCH_DESC_7", skin="2"),
    # continental club cups
    def_cup(9, "UEFA Champions League – Vòng knock-out", "66", 4, 50000, [("ko", None, "PVE_CUP_MATCH_ROUND_9_1")],
            [("lg", EURO_LEAGUES, 15)], desc="PVE_CUP_MATCH_DESC_9"),
    def_cup(12, "PVE_CUP_MATCH_TITLE_12", "300000", 3, 40000, RR_KO(12), [("lg", NONEU_LEAGUES, 15)],
            desc="PVE_CUP_MATCH_DESC_12", skin="2"),
    def_cup(14, "FIFA Club World Cup", "66", 5, 80000, [("ko", None, "PVE_CUP_MATCH_ROUND_14_1")],
            [("lg", EURO_LEAGUES, 9), ("lg", (353,), 2), ("lg", (336,), 1), ("lg", (39,), 1), ("lg", (83, 2111), 1),
             ("lg", (350,), 1)], desc="PVE_CUP_MATCH_DESC_14"),
    # national club cups (only countries with clubs in fifadb)
    nat_cup(134, (13, 14, 60, 61), 32, 35000, [("ko", 1, "PVE_CUP_MATCH_ROUND_134_1"), ("ko", 1, "PVE_CUP_MATCH_ROUND_134_2"),
                                                ("ko", None, "PVE_CUP_MATCH_ROUND_134_3")], quality=3),
    nat_cup(144, (53, 54), 32, 35000, [("ko", 1, "PVE_CUP_MATCH_ROUND_144_1"), ("ko", None, "PVE_CUP_MATCH_ROUND_144_2")],
            quality=3),
    nat_cup(136, (19, 20, 2076), 32, 35000, quality=3),
    nat_cup(137, (31, 32), 32, 35000, quality=3),
    nat_cup(135, (16, 17), 32, 35000, [("ko", 1, "PVE_CUP_MATCH_ROUND_135_1"), ("ko", None, "PVE_CUP_MATCH_ROUND_135_2")],
            quality=3),
    nat_cup(158, (68,), 16, 25000),                     # Thổ Nhĩ Kỳ
    nat_cup(151, (353,), 16, 25000),                    # Argentina
    nat_cup(157, (336,), 16, 25000),                    # Colombia
    nat_cup(153, (39,), 16, 25000),                     # Mỹ
    nat_cup(148, (83, 2111), 16, 20000),                # Hàn Quốc
    nat_cup(145, (56,), 16, 20000, RR_KO(145)),         # Thụy Điển: vòng bảng + loại trực tiếp
    nat_cup(138, (41,), 16, 20000),                     # Nauy
    nat_cup(139, (66,), 16, 20000),                     # Ba Lan
    nat_cup(133, (1,), 8, 15000),                       # Đan Mạch
    nat_cup(143, (50,), 8, 15000),                      # Scotland
    nat_cup(141, (65,), 8, 15000),                      # Ai-len
    nat_cup(149, (350,), 8, 15000),                     # Ả Rập Saudi
]


# World Cup PvE (WCFrontScreen / WCControllerImpl extends CupController): same /cup/* API as the
# club cups, list via /cup/list?type=wcpve, the player picks a NATIONAL team (WCCreation:
# /static/db/SORTWORLDCUPTEAM {confederation: [teamid]}, /worldcup/info/authentic/team/<id>).
# 32 national teams, 8 groups of 4, then round of 16 / QF / SF / final.
WC_METAID = 100
WC_CONF = {  # WORLDCUP_CONFEDERATION_<n>: 2 chau Au, 3 chau Phi, 4 Nam My, 5 chau A, 6 chau Dai Duong, 7 Bac-Trung My
    2: [1354, 1353, 1337, 1325, 1362, 1335, 1318, 1343, 1361, 105035, 1367, 1341, 1364, 1365, 1886, 1357, 1355, 1352,
        1356, 1334, 1330, 1327],
    3: [111130, 111112, 1395, 111099],
    4: [1370, 1377, 111109, 111465, 111487, 1375, 111108, 111451],
    5: [1413, 1415, 111466, 113666, 1411, 974, 111114, 111115, 111512, 111533],  # + Viet Nam, WC_NATION_SQUADS
    7: [1386, 111455, 1387],
}
WC_TEAMS = [t for v in WC_CONF.values() for t in v]
WC_TEAM_CONF = {t: c for c, v in WC_CONF.items() for t in v}
# WCCreationGroupSettingScene: 3 nut cach boc tham = 3 meta dau cua WCProxy.cupList (sap xep):
# 0 = boc tham that 2014, 1 = ngau nhien theo chau luc, 2 = hoan toan ngau nhien. Ban offline: cung luat.
# teamselect "authentic": CupController.selectTeam chi ghi doi da chon khi khac "user" (user = lay doi CLB
# cua HLV -> man xac nhan hien Real Madrid va /cup/new gui teamid CLB).
WC_METAS = [dict(cup_meta(WC_METAID + i, name, "66", 5, 7, 32, 200000), type="wcpve", teamselect="authentic")
            for i, name in enumerate(("World Cup 2014 Brazil", "World Cup (ngẫu nhiên theo châu lục)",
                                      "World Cup (hoàn toàn ngẫu nhiên)"))]
WC_META = WC_METAS[0]
# National-team cups in Single > Cup (teamselect "authentic": pick the national team in the
# creation screen; International league 78 only, whitelisted teamids)
CUPS += [
    def_cup(602, "UEFA EURO", "66", 4, 80000, RR_KO(602), [("nt", WC_CONF[2], 15)],
            desc="PVE_CUP_MATCH_DESC_602", authentic=WC_CONF[2]),
    def_cup(610, "Copa América", "300000", 4, 60000,
            [("rr", 2, "PVE_CUP_MATCH_ROUND_610_1"), ("ko", None, "PVE_CUP_MATCH_ROUND_610_2")],
            [("nt", WC_CONF[4], 7)], desc="PVE_CUP_MATCH_DESC_610", skin="2", nteams=8, authentic=WC_CONF[4]),
    def_cup(311, "FIFA World Cup", "66", 5, 100000, RR_KO(311), [("nt", WC_TEAMS, 15)],
            desc="PVE_CUP_MATCH_DESC_311", authentic=WC_TEAMS),
]
ALL_CUP_METAS = CUPS + WC_METAS


def _bot_cup_names():
    """[(metaid, ten hien trong game)] cho GM Tool > Suc manh BOT (khoa PVE_CUP_* -> chu cua client)."""
    loc = client_localization(f"LANGUAGESTRING_{LANGUAGE.upper()}.json")
    return [(m["metaid"], str(loc.get(m["name"]) or m["name"]) if str(m["name"]).startswith("PVE_") else m["name"])
            for m in CUPS]


import bot_settings as _bot_settings  # noqa: E402
_bot_settings.set_cups(_bot_cup_names)


def cup_meta_of(metaid):
    return next((m for m in ALL_CUP_METAS if m["metaid"] == int(metaid or 0)), None)


def wc_my_cup(st):
    return next((c for c in (st.get("cups") or {}).values() if c.get("type") == "wcpve"), None)


def ep_cup_list(query, body):
    if "type=wcpve" in query:  # World Cup: meta + the running one (WCProxy.getWCById finds it by _id)
        cup = wc_my_cup(_state())
        if cup:
            cup_refresh_stats(cup)  # du cot ban thang/thua cho giai tao truoc ban sua
            # CupProxy.checkEndatCupByDays doc endat cua giai dang da (thieu -> "con 0 ngay")
            meta = cup_meta_of(cup.get("metaid")) or WC_META
            # CupProxy.onCupListReceived doc joinable.dayofweek cua MOI muc trong danh sach (ke ca giai dang da):
            # thieu -> TypeError #1010, popup COMMON_ERROR_UI khi quay lai man World Cup (2026-11-09). Lay du
            # truong cua giai goc (joinable, ...), truong cua giai dang da de len tren.
            cup = dict(meta, **cup, startat=meta["startat"], endat=meta["endat"])
        return _json(WC_METAS + ([cup] if cup else []))
    return _json(CUPS)


_CLUB_RATING = {}


def club_rating(teamid):
    """Average of the best 11 card ratings of a club this season (cached, one query)."""
    if not _CLUB_RATING:
        by = collections.defaultdict(list)
        with closing(_db()) as c:
            for tid, ovr in c.execute("""SELECT p.teamid, p.overallrating FROM players p
                                         JOIN permid m ON m.spid = p.spid AND m.licensed = 1
                                         WHERE p.spid / 1000000 = ?""", (gen_teams.CURRENT_SEASON,)):
                by[tid].append(ovr)
        for tid, v in by.items():
            v.sort(reverse=True)
            _CLUB_RATING[tid] = sum(v[:11]) / len(v[:11])
    return _CLUB_RATING.get(teamid, 0)


def cup_pool(metaid, mine):
    """The AI teams of a new cup (CUP_SPECS pool, strongest first, never `mine`)."""
    spec = CUP_SPECS.get(int(metaid)) or CUP_SPECS[1]
    leagues = dict(lid_ts for _, ls in club_index() for lid_ts in ls)
    chosen = []
    for kind, ids, k in spec["pool"]:
        if kind == "nt":
            wc_pool(mine)  # fills _WC_STRENGTH
            cands = sorted((t for t in ids if t != mine and t not in chosen), key=lambda t: -_WC_STRENGTH.get(t, 0))
        else:
            cands = sorted({t for lid in ids for t in leagues.get(lid, []) if t != mine and t not in chosen},
                           key=lambda t: (-club_rating(t), t))
        chosen += cands[:k]
    need = spec["n"] - 1
    if len(chosen) < need:  # not enough clubs in those leagues: top European clubs fill up
        extra = sorted({t for lid in EURO_LEAGUES for t in leagues.get(lid, []) if t != mine and t not in chosen},
                       key=lambda t: (-club_rating(t), t))
        chosen += extra[:need - len(chosen)]
    return chosen[:need]


def cup_team(st, teamid, mine=None):
    me = mine if mine is not None else my_teamid(st)
    return {"teamid": teamid, "coach": coach_id(_default_slot(st)) if teamid == me else None}


_WC_STRENGTH = {}


def wc_pool(mine):
    """31 strongest national teams besides mine (cached ratings)."""
    for t in WC_TEAMS:
        if t not in _WC_STRENGTH:
            ovrs = sorted((player_ovr(p) for p in roster_team(t)["players"]), reverse=True)[:11]
            _WC_STRENGTH[t] = sum(ovrs) / len(ovrs) if ovrs else 50
    return sorted((t for t in WC_TEAMS if t != mine), key=lambda t: -_WC_STRENGTH[t])[:31]


def new_cup(st, meta, difficulty, myteam=None):
    """A cup instance (CupProxy.updateCurrentCupData): phases built from the cup's format
    (CUP_SPECS; World Cup = 8 groups + knock-out). pairs[].legs[leg] = {homeidx, awayidx,
    score?, shootout?} index into that group's teams; rounds[].shuffle -> the draw screen."""
    wc = meta.get("type") == "wcpve"
    spec = CUP_SPECS.get(meta["metaid"])
    me = myteam if (wc or myteam is not None) else my_teamid(st)
    teams = [me] + (wc_pool(me) if wc else cup_pool(meta["metaid"], me))
    random.shuffle(teams)
    fmt = [list(f) for f in spec["fmt"]] if spec else [["rr", len(teams) // 4, "Vòng bảng"],
                                                         ["ko", None, "Vòng loại trực tiếp"]]
    cupid = f"{int(time.time()):08x}{os.urandom(8).hex()}"
    cup = {"_id": cupid, "metaid": meta["metaid"], "name": meta["name"], "type": meta.get("type", "pve"), "myteam": me,
           "teamselect": meta.get("teamselect", "user"), "trophy": meta["trophy"], "quality": meta["quality"],
           "difficulty": difficulty, "current": {"phase": 0, "round": 0, "leg": 0}, "_fmt": fmt,
           "ownsquad": not wc and myteam is None,  # da bang doi hinh the cua minh (CLB chinh)
           "phases": [cup_build_phase(fmt[0], [cup_team(st, t, me) for t in teams])],
           "coaches": [{"_id": coach_id(_default_slot(st)), "state": "playing"}],
           "teams": [cup_team(st, t, me) for t in teams]}
    cup_refresh_stats(cup)
    return cup


def cup_fmt(cup):
    """Phase list of a cup; cups made before formats existed are 4/8 groups + knock-out."""
    return cup.get("_fmt") or [["rr", len(cup["phases"][0]["groups"]), "Vòng bảng"], ["ko", None, "Vòng loại trực tiếp"]]


def cup_build_phase(f, teams):
    """One phase over `teams` (team objects, already in draw order)."""
    kind, k, name = f
    if kind == "rr":
        size = len(teams) // k
        groups = [{"teams": [dict(t) for t in teams[g * size:(g + 1) * size]],
                   "rounds": [{"shuffle": r == 0, "pairs": [{"legs": [{"homeidx": h, "awayidx": a}]}
                                                             for h, a in league_round(list(range(size)), r)]}
                              for r in range(size - 1)]} for g in range(k)]
        return {"system": "round-robin", "name": name, "maxrounds": size - 1, "groups": groups}
    nrounds = k or max(1, int(math.log2(len(teams))))
    rounds, n = [], len(teams) // 2
    for _ in range(nrounds):
        rounds.append({"pairs": [{"legs": [{}]} for _ in range(max(1, n))]}); n //= 2
    rounds[0] = {"shuffle": True, "pairs": [{"legs": [{"homeidx": i, "awayidx": i + 1}]} for i in range(0, len(teams), 2)]}
    return {"system": "knock-out", "name": name, "maxrounds": nrounds,
            "groups": [{"teams": [dict(t) for t in teams], "rounds": rounds}]}


def cup_ko_rounds(cup):
    """Knock-out rounds the champion plays (for the reward share)."""
    n, total = len(cup.get("teams") or []) or 16, 0
    for kind, k, _ in cup_fmt(cup):
        if kind == "rr":
            n = 2 * k
        else:
            r = k or max(1, int(math.log2(n))); total += r; n >>= r
    return max(1, total)


def cup_my_pair(cup):
    """(group, leg) of my fixture in the current round, or (None, None)."""
    cur = cup["current"]
    for g in cup["phases"][cur["phase"]]["groups"]:
        for pair in g["rounds"][cur["round"]]["pairs"]:
            leg = pair["legs"][cur["leg"]]
            idx = [leg.get("homeidx"), leg.get("awayidx")]
            if any(i is not None and g["teams"][i]["coach"] for i in idx):
                return g, leg
    return None, None


def group_table(g):
    """[(team index, points, goal diff, goals)] best first."""
    t = {i: [0, 0, 0] for i in range(len(g["teams"]))}
    for rnd in g["rounds"]:
        for pair in rnd["pairs"]:
            leg = pair["legs"][0]
            if "score" not in leg:
                continue
            gh, ga = leg["score"]["home"], leg["score"]["away"]
            for i, f, a in ((leg["homeidx"], gh, ga), (leg["awayidx"], ga, gh)):
                t[i][0] += 3 if f > a else 1 if f == a else 0
                t[i][1] += f - a; t[i][2] += f
    return sorted(t, key=lambda i: tuple(-v for v in t[i]))


def cup_refresh_stats(cup):
    """Per-team W/D/L over the whole cup + group points/rank on every team object (group
    tables, "Juara - <rank> (W/D/L)" next-match panel, "Grup C - Peringkat <rank>")."""
    wdl = {}
    for phase in cup["phases"]:
        for g in phase["groups"]:
            for rnd in g["rounds"]:
                for pair in rnd["pairs"]:
                    leg = pair["legs"][0]
                    if "score" not in leg:
                        continue
                    for i, f, a in ((leg["homeidx"], leg["score"]["home"], leg["score"]["away"]),
                                    (leg["awayidx"], leg["score"]["away"], leg["score"]["home"])):
                        r = wdl.setdefault(g["teams"][i]["teamid"], [0, 0, 0])
                        r[0 if f > a else 1 if f == a else 2] += 1
    gfa = {}  # (group, team index) -> (ban thang, ban thua) trong bang
    for phase in cup["phases"]:
        for g in phase["groups"]:
            for rnd in g["rounds"]:
                for pair in rnd["pairs"]:
                    leg = pair["legs"][0]
                    if "score" not in leg:
                        continue
                    for i, f, a in ((leg["homeidx"], leg["score"]["home"], leg["score"]["away"]),
                                    (leg["awayidx"], leg["score"]["away"], leg["score"]["home"])):
                        x = gfa.get((id(g), i), (0, 0)); gfa[(id(g), i)] = (x[0] + f, x[1] + a)
    for phase in cup["phases"]:
        for g in phase["groups"]:
            order = group_table(g) if phase["system"] == "round-robin" else range(len(g["teams"]))
            for rank, i in enumerate(order):
                t = g["teams"][i]; w, d, l = wdl.get(t["teamid"], [0, 0, 0])
                gf, ga = gfa.get((id(g), i), (0, 0))
                # cot Ban thang / Ban thua / Hieu so (thieu -> "NaN"); dat nhieu ten cho chac
                t.update(win=w, draw=d, lose=l, point=3 * w + d, rank=rank + 1,
                         goals=gf, goalsconceded=ga)  # WCMainGroupRankListItemRenderer / CupGroupRankListItemRenderer


def cup_play_round(st, cup, mine=None):
    """Play the current round (my fixture = `mine` (home, away) if given, the rest simulated),
    then advance current / build the knock-out / settle my state."""
    cur = cup["current"]; phase = cup["phases"][cur["phase"]]; ko = phase["system"] == "knock-out"
    rng = random.Random()
    for g in phase["groups"]:
        for pair in g["rounds"][cur["round"]]["pairs"]:
            leg = pair["legs"][cur["leg"]]
            if "score" in leg or leg.get("homeidx") is None:
                continue
            h, a = g["teams"][leg["homeidx"]], g["teams"][leg["awayidx"]]
            played = mine is not None and (h["coach"] or a["coach"])
            if played:
                gh, ga = mine[:2]
            else:
                gh, ga = sim_score(rng, team_strength(st, h["teamid"]), team_strength(st, a["teamid"]))
            leg["score"] = {"home": gh, "away": ga}
            if h["coach"] or a["coach"]:  # thanh tich Cup trong Info Manager
                record_match(st, "cup", *((gh, ga) if h["coach"] else (ga, gh)))
            if ko and gh == ga and played and len(mine) > 3 and mine[2] != mine[3]:  # shootout played in game
                leg["shootout"] = {"home": mine[2], "away": mine[3]}
            elif ko and gh == ga:  # penalties
                ph = rng.randint(3, 5); pa = ph + rng.choice((-1, 1)) if ph < 5 else ph - 1
                leg["shootout"] = {"home": ph, "away": pa}
    me_state = cup["coaches"][0]
    fmt = cup_fmt(cup)
    last_phase = cur["phase"] + 1 >= len(fmt)
    if not ko:
        if cur["round"] + 1 < len(phase["groups"][0]["rounds"]):
            cur["round"] += 1
            return
        # groups done: A1-B2, B1-A2, C1-D2, D1-C2 (... G1-H2, H1-G2 for 8 groups)
        tables = [group_table(g) for g in phase["groups"]]
        qual = []
        for x in range(0, len(phase["groups"]), 2):
            y = x + 1
            qual += [phase["groups"][x]["teams"][tables[x][0]], phase["groups"][y]["teams"][tables[y][1]],
                     phase["groups"][y]["teams"][tables[y][0]], phase["groups"][x]["teams"][tables[x][1]]]
        if last_phase:  # a cup ending on a group phase: the group winner takes it
            cup["finished"] = True
            if me_state["state"] == "playing":
                me_state["state"] = "won" if qual and qual[0]["coach"] else "eliminated"
            return
    else:
        g = phase["groups"][0]
        winners = []
        for pair in g["rounds"][cur["round"]]["pairs"]:
            leg = pair["legs"][0]; s = leg.get("shootout") or leg["score"]
            winners.append(leg["homeidx"] if s["home"] > s["away"] else leg["awayidx"])
        my_idx = next(i for i, t in enumerate(g["teams"]) if t["coach"]) if any(t["coach"] for t in g["teams"]) else None
        if my_idx is not None and me_state["state"] == "playing" and my_idx not in winners and \
                any(my_idx in (p["legs"][0].get("homeidx"), p["legs"][0].get("awayidx")) for p in g["rounds"][cur["round"]]["pairs"]):
            me_state["state"] = "eliminated"
        if cur["round"] + 1 < len(g["rounds"]):
            for k, pair in enumerate(g["rounds"][cur["round"] + 1]["pairs"]):
                pair["legs"][0] = {"homeidx": winners[2 * k], "awayidx": winners[2 * k + 1]}
            cur["round"] += 1
            return
        if last_phase or len(winners) < 2:  # final played
            cup["finished"] = True
            if me_state["state"] == "playing":
                me_state["state"] = "won" if my_idx in winners else "eliminated"  # CupMainScreen._cupState
            return
        qual = [g["teams"][i] for i in winners]
    # phase over: the qualifiers start the next one (knock-out keeps the bracket order;
    # a group phase after a knock-out is drawn afresh)
    nxt = fmt[cur["phase"] + 1]
    if nxt[0] == "rr":
        qual = list(qual); random.shuffle(qual)
    cup["phases"].append(cup_build_phase(nxt, qual))
    cup.update(current={"phase": cur["phase"] + 1, "round": 0, "leg": 0})
    if not any(t["coach"] for t in qual) and me_state["state"] == "playing":
        me_state["state"] = "eliminated"


def cup_resources(cup):
    cup_refresh_stats(cup)  # luon du goals / goalsconceded cho bang xep hang (hieu so)
    meta = cup_meta_of(cup.get("metaid"))
    if meta:  # CupMainScreen / tournament tree read currentCup.trophy / quality
        cup.setdefault("quality", meta["quality"])
        # ten / cup / nen luon theo meta hien tai (giai dang da do van doi theo, vd UEFA Champions League)
        cup["trophy"] = meta["trophy"]
        if "name" in cup or "name" in meta:
            cup["name"] = meta["name"]
        # CupBackground.setData (in-cup screens) loads skins/s<skin>_bg.png from currentCup.skin
        cup["skin"] = meta["skin"]
    uri = f"/cup/{cup['_id']}"
    return [{"resource": uri, "uri": uri, "body": cup}]


def ep_cup_new(query, body):
    """POST /cup/new {metaid, difficulty, teamid, lineup}."""
    req = json.loads(body or b"{}")
    meta = cup_meta_of(req.get("metaid"))
    if meta is None:
        return _json({"result": False, "reason": "Không tìm thấy giải cup này."})
    st = _state()
    myteam = None
    if meta.get("type") == "wcpve":
        try:
            myteam = wc_base(int(req.get("teamid") or 0))
        except (TypeError, ValueError):
            myteam = 0
        if myteam not in WC_TEAM_CONF:
            return _json({"result": False, "reason": "Đội tuyển này chưa có trong World Cup offline."})
        for k in [k for k, c in (st.get("cups") or {}).items() if c.get("type") == "wcpve"]:
            st["cups"].pop(k)  # chi 1 World Cup cung luc
    elif meta.get("teamselect") == "authentic":  # national-team cup: the team picked in the creation screen
        allowed = CUP_SPECS.get(meta["metaid"], {}).get("authentic") or []
        try:
            myteam = wc_base(int(req.get("teamid") or 0))
        except (TypeError, ValueError):
            myteam = 0
        if allowed and myteam not in allowed:
            return _json({"result": False, "reason": "Đội tuyển này không tham gia giải này."})
        if not allowed:  # cup CLB: CLB da chon (khong phai doi tuyen); khong chon / CLB chinh -> doi cua minh
            if myteam in _NATIONAL_TEAMS or (myteam and myteam not in _CLUB_TEAMS):
                return _json({"result": False, "reason": "Hãy chọn một CLB (không phải đội tuyển)."})
            if not myteam or myteam == my_teamid(st):
                myteam = None
    cup = new_cup(st, meta, int(req.get("difficulty") or 0), myteam)
    st.setdefault("cups", {})[cup["_id"]] = cup
    _save(st)
    return _json({"result": True, "resources": cup_resources(cup)})


def cup_step(cupid, fn):
    st = _state(); cup = (st.get("cups") or {}).get(cupid)
    if cup is None:  # client con giu cup da bo: day lai coach (path=cups) cho the cup het "Dang choi"
        return _json({"result": False, "reason": "Giải cup này không còn tồn tại.",
                      "resources": coach_me_resources(st, _default_slot(st))})
    out = fn(st, cup) or {}
    _save(st)
    return _json(dict({"result": True, "resources": cup_resources(cup)}, **out))


def ep_cup_resume(cupid, query, body):
    return cup_step(cupid, lambda st, cup: None)


def ep_cup_next(cupid, query, body):
    """PUT /cup/<id>/next: play out the current round (my result from the match just played,
    if any) and advance (CupController.onMatchEndReceived -> ON_CUP_SCHEDULE_NEXT_ROUND)."""
    def step(st, cup):
        if cup.get("finished"):
            return
        cup_play_round(st, cup, cup.pop("_mine", None))
        cup_refresh_stats(cup)
    return cup_step(cupid, step)


def cup_reward_ep(cup):
    """EP for an ended cup: the meta's prize for the champion, otherwise 10% plus up to 40%
    more by knock-out matches played (group exit 10%, losing the final ~50%); 0 while playing."""
    state = cup["coaches"][0]["state"]
    meta = cup_meta_of(cup["metaid"]) or CUPS[0]
    prize = meta["bestrewards"][-1][0]["count"]
    if state == "won":
        return prize
    if state != "eliminated":
        return 0
    played = 0
    for ph in cup["phases"]:
        if ph["system"] != "knock-out":
            continue
        g = ph["groups"][0]
        mine = next((i for i, t in enumerate(g["teams"]) if t["coach"]), None)
        played += 0 if mine is None else sum(
            1 for r in g["rounds"] for p in r["pairs"]
            if mine in (p["legs"][0].get("homeidx"), p["legs"][0].get("awayidx")) and "score" in p["legs"][0])
    return prize * (10 + 40 * min(played, cup_ko_rounds(cup)) // cup_ko_rounds(cup)) // 100


def cup_record_add(st, cup, ep=0):
    """recordsbymetaid entry for /cup/record (CupFrontScreen "Thang/So lan": wins/challenges)."""
    rec = st.setdefault("cup_records", {}).setdefault(str(cup["metaid"]), {"wins": 0, "challenges": 0})
    rec["challenges"] += 1
    rec["wins"] += cup["coaches"][0]["state"] == "won"
    rec["prize"] = rec.get("prize", 0) + ep


def ep_cup_leave(cupid, query, body):
    """PUT /cup/<id>/leave: give up, or collect an ended cup's reward. CupReward*Scene.onItemExpand
    reads body.rewards[]: itemid 1 = EP (count), anything else is an item card."""
    st = _state(); cup = (st.get("cups") or {}).pop(cupid, None)
    ep = cup_reward_ep(cup) if cup else 0
    if cup and cup.get("type") == "wcpve":
        rec = st.setdefault("wc_record", {"win": 0, "join": 0, "won": 0})
        rec["join"] += 1
        rec["won"] += cup["coaches"][0]["state"] == "won"
        rec["win"] += sum(t.get("win", 0) for g in cup["phases"][0]["groups"] for t in g["teams"] if t["coach"])
    elif cup:
        cup_record_add(st, cup, ep)
    if cup:  # Info Manager: bo giua chung, vo dich (theo do kho AI)
        r, state = coach_record(my_coach(st), "cup"), cup["coaches"][0]["state"]
        r["abandonments"] = r.get("abandonments", 0) + (state == "playing")
        if state == "won":
            wins = r.setdefault("cupwins", [0] * 5)
            wins[min(4, max(0, int(cup.get("difficulty") or 0)))] += 1
        if ep > (r.get("best") or {}).get("prize", 0):
            r["best"] = {"prize": ep, "metaid": cup["metaid"], "date": w3c(time.time())}
    add_ep(st, ep)
    _save(st)
    # CoachProxy.onCupInfoChanged nghe /coach/me?path=cups: khong day lai coach thi CupProxy.cupPlayList
    # van giu cup vua bo -> the cup hien "Dang choi", bam vao bao "Giai cup nay khong con ton tai"
    return _json({"result": True, "rewards": [{"itemid": 1, "count": ep}] if ep else [],
                  "resources": coach_me_resources(st, _default_slot(st))})


def ep_cup_match(cupid, query, body):
    """POST /cup/<id>/match -> my fixture of the current round as a /match/<id> resource."""
    st = _state(); cup = (st.get("cups") or {}).get(cupid)
    g, leg = cup_my_pair(cup) if cup else (None, None)
    if leg is None or "score" in leg:
        return _json({"result": False, "reason": "Trận này đã đá rồi."})
    me = coach_me(st, _default_slot(st)); wc = cup.get("type") == "wcpve"
    # World Cup / cup doi tuyen / cup CLB voi CLB khac CLB chinh: doi hinh that cua doi da chon
    picked = wc or (cup.get("teamselect") == "authentic" and not cup.get("ownsquad"))
    teamid = cup.get("myteam") if picked else me["teamid"]
    home = g["teams"][leg["homeidx"]]["teamid"]; away = g["teams"][leg["awayidx"]]["teamid"]
    opp = away if home == teamid else home
    user = {"id": me["id"], "name": me["name"], "levelinfo": me["levelinfo"]}
    mine = roster_team(teamid) if picked else st["teams"][str(_default_slot(st))]
    match = new_match("WorldCup_PvE" if wc else "Cup_PvE", match_side(mine, teamid, user, _tactics(st)[1]),
                      match_side(roster_team(opp), opp, mode="worldcup" if wc else f"cup_{cup.get('metaid')}"))
    gs = match["gamesettings"]
    gs["home"] = home == teamid
    gs["kit"] = ["home", "away"] if gs["home"] else ["away", "home"]
    gs["difficulty"] = cup.get("difficulty", 0)  # AI level 0..4
    apply_prematch(st, match)
    if cup["phases"][cup["current"]["phase"]]["system"] == "knock-out":
        # native maps drawMode 1/2/3 to end rules 9/6/7 (0 = a draw stands). 2 plays extra time
        # (FIRST_EXTRA_HALF seen in game); a draw after it is settled by cup_play_round
        gs["drawMode"] = 2
    st["match"] = match
    st["cup_pending"] = {"id": match["id"], "cupid": cupid, "home": home, "away": away}
    _save(st)
    return _json({"result": True, "resources": [{"uri": f"/match/{match['id']}", "body": match}]})


def wc_status(st):
    """GET /worldcup body.pve (WCFrontScreen.updatePopupPve / getCurrentWCStageStatus)."""
    rec = st.setdefault("wc_record", {"win": 0, "join": 0, "won": 0})
    cup = wc_my_cup(st)
    base = {"total": {"win": rec["won"], "join": rec["join"]}, "rp": rec["win"], "wonteams": rec["won"],
            "join": {"max": 99, "rest": 99}}
    if not cup:
        return dict(base, ongoing=False)
    cur = cup["current"]; phase = cup["phases"][cur["phase"]]
    grp = next((i for i, g in enumerate(phase["groups"]) if any(t["coach"] for t in g["teams"])), 0)
    me_team = next((t for g in phase["groups"] for t in g["teams"] if t["coach"]), {})
    # updatePopupPve: nut VS COM hien "V.bang Bang " + _groupString[pve.group]
    return dict(base, ongoing=True, cupid=cup["_id"], team=cup["myteam"], difficulty=cup.get("difficulty", 0), group=grp,
                confederation=WC_TEAM_CONF.get(cup["myteam"], 2),
                coach={"state": cup["coaches"][0]["state"], "rank": me_team.get("rank", 0)},
                current={"phase": cur["phase"], "round": cur["round"], "group": grp})


def ep_worldcup(query, body):
    return _json({"pve": wc_status(_state()), "pvp": {"state": "idle"}, "live": {}})


# Danh vong World Cup (WCFrontScreen: GET /worldcup/reputation/rewards -> reputation.weeklyrp/accumrp
# [{rewardby, items}], POST /worldcup/reputation/reward -> rpinfo {weeklyrp, accumrp}, rewardeditems,
# rewardedquests). Diem = 100 moi tran thang World Cup.
WC_RP_TIERS = [{"rewardby": n, "items": [{"itemid": 1, "count": n * 50}]} for n in (300, 600, 1000, 1500, 2100)]


def wc_rp_tiers():
    """Moc danh vong kem staticdata vat pham (o phan thuong WCCoachReputationPointViewer can de ve icon)."""
    return [{"rewardby": t["rewardby"], "items": [dict(i, staticdata=EP_ITEM_DEF if i["itemid"] == EP_ITEM else item_def(i["itemid"]))
                                                   for i in t["items"]]} for t in WC_RP_TIERS]


def wc_rpinfo(st):
    rec = st.setdefault("wc_record", {"win": 0, "join": 0, "won": 0})
    rp = rec["win"] * 100
    return {"weeklyrp": rp, "accumrp": rp, "rp": rp}


def ep_wc_rep_rewards(query, body):
    return _json({"result": True, "reputation": {"weeklyrp": wc_rp_tiers(), "accumrp": wc_rp_tiers()}})


def ep_wc_rep_reward(query, body):
    return _json({"result": True, "rpinfo": wc_rpinfo(_state()), "rewardeditems": [], "rewardedquests": []})


def ep_wc_rpinfo(query, body):
    """GET /worldcup/rpinfo (WCCoachFameTabPage: resources[0].body -> initFamePoint accumrp/weeklyrp/
    order, initWeeklyFame until, initFameReward reputation.{accumrp,weeklyrp})."""
    st = _state(); rp = wc_rpinfo(st); rec = st.get("wc_record") or {}
    until = time.strftime("%Y-%m-%dT00:00:00.000Z", time.gmtime(time.time() + 7 * 86400))
    # WCCoachFameTabPage: order = _fameRecordOrder[do kho] -> mang nationid theo tung do kho (0..4)
    body = dict(rp, order=[[] for _ in range(5)], until=until, champion=rec.get("won", 0), join=rec.get("join", 0),
                reputation={"weeklyrp": wc_rp_tiers(), "accumrp": wc_rp_tiers()}, records=[])
    return _json({"result": True, "resources": [{"uri": "/worldcup/rpinfo", "body": body}]})


# Doi mac dinh man chon doi WC (DEFAULT_TEAM_INFO "default") goc la 14001370; WCCreationScreen.big
# da va thanh 1370. wc_base() van nhan id 1400xxxx neu client cu gui len.
WC_ID_PREFIX = 14000000
WC_DEFAULT_IDS = {1370}  # Brazil


def wc_base(t):
    return t - WC_ID_PREFIX if t >= WC_ID_PREFIX else t


def wc_sort_team():
    # WCCreationScreen.big da va: doi mac dinh = 1370 (Brazil, id goc -> ao 3D dung). Dat doi mac
    # dinh o vi tri 1: game chi tai doi hinh khi selectedIndex doi gia tri (o vi tri 0 thi khong).
    out = {}
    for k, v in WC_CONF.items():
        ids = list(v)
        if len(ids) > 1 and ids[0] in WC_DEFAULT_IDS:
            ids[0], ids[1] = ids[1], ids[0]
        out[str(k)] = ids
    return out


def ep_wc_team(teamid, query, body):
    """/worldcup/info/authentic/team/<id>[?metaid&difficulty] (WCCreationTeamSelectionScene closure:
    body.team -> danh sach cau thu, body.kit, body.extra -> bang thong tin: fifarank, preliminary_result,
    worldcup_results, qualification, coach, grade (sao), captain (spid))."""
    t = wc_base(int(teamid)); r = roster(t); team = dict(r["team"], teamid=int(teamid))
    wc_pool(t)  # nap _WC_STRENGTH
    order = sorted(WC_TEAMS, key=lambda x: -_WC_STRENGTH.get(x, 0))
    rank = order.index(t) + 1 if t in order else len(order)
    grade = 5 if rank <= 6 else 4 if rank <= 14 else 3 if rank <= 24 else 2 if rank <= 34 else 1
    extra = {"fifarank": rank, "grade": grade, "captain": team.get("squad", {}).get("captain"),
             "preliminary_result": "Vượt qua vòng loại", "worldcup_results": "—",
             "qualification": "—", "coach": "HLV đội tuyển"}
    # ao 3D: engine dung teamid client gui (co the la 1400xxxx) -> ma ao phai theo dung id do
    kt = KIT_ALIAS.get(t, int(teamid))
    kit = {"availables": {k: {"id": kt << 5 | i} for k, i in (("home", 0), ("away", 1), ("goalie", 2))}}
    return _json(dict(r, team=team, kit=kit, extra=extra))


def ep_cup_record(query, body):
    """GET /cup/record: CupProxy.onCupRecordResourceChanged takes resources[] entries whose
    `resource` is /cup/record and body._id is my coach id."""
    st = _state(); coach = coach_me(st, _default_slot(st)); recs = st.get("cup_records") or {}
    return _json({"result": True, "resources": [{"resource": "/cup/record", "uri": "/cup/record",
                  "body": {"_id": coach["id"],
                           # CupRecordPveTabPage: totalwins / totalprize / totalchallenges labels (no
                           # firstwinat -> nothing goes to the trophy tile list)
                           "totalwins": sum(r["wins"] for r in recs.values()),
                           "totalchallenges": sum(r["challenges"] for r in recs.values()),
                           "totalprize": sum(r.get("prize", 0) for r in recs.values()),
                           "recordsbymetaid": [
                      {"metaid": int(m), "records": [{"wins": r["wins"], "challenges": r["challenges"]}]}
                      for m, r in recs.items()]}}]})


# --------------------------------------------------------------------------- #
# Market (Bursa transfer): an NPC market over the cards fifaaddict lists, at their Indonesia-
# server prices (card_price). Offer rows are {spid, expandme: "player", grade, price, count};
# the client fills staticdata (names, position, ratings) from its own DB via `expandme`.
# --------------------------------------------------------------------------- #
LINES = {"GK": range(0, 1), "DF": range(1, 9), "MF": range(9, 20), "FW": range(20, 28)}  # Enums.PLAYERPOSITION
MAX_CASH = 999_999_999  # MP / Cash toi da (so nguyen 32-bit an toan)


def coach_money(coach, key):
    """MP / Cash cua HLV (coach["mp"] / coach["cash"], mac dinh 0)."""
    return max(0, min(MAX_CASH, int(coach.get(key) or 0)))
MAX_PLAYERS = 9999  # coach.limits.players (gioi han cau thu trong CLB; theo yeu cau 9999)
# Tim inti (player state "first": Formasi lists only these) holds 68 = 11 + 7 du bi + 50 cho (user 2026-11-10;
# client doc /coach/me limits.firstTeam - TeamManagementPlayerPoolTabPage); truoc: 36.
# cards beyond that wait in Tim Cadangan, which Tukar Scout trades from.
FIRST_TEAM_MAX = 68


def first_team_count(team):
    return sum(1 for p in team["players"] if not p.get("team2"))


def balance_first_team(team):
    """Tim inti over FIRST_TEAM_MAX (clubs from before the cap): the least valuable cards outside
    the 18 of the squad move to Tim Cadangan."""
    extra = first_team_count(team) - FIRST_TEAM_MAX
    spare = [p for p in team["players"] if not p.get("team2") and not in_squad(team, p)]
    for p in sorted(spare, key=lambda p: card_price(p["spid"], grade_of(p)))[:max(0, extra)]:
        p["team2"] = True


SERVER_OPTIONS_FILE = os.path.join(HERE, "gamedata", "server_options.json")
SERVER_OPTIONS_DEFAULT = {"hide_engine_cards": False, "tc_grade": True}  # the mua 0 hien mac dinh (2026-11-09: Koeman/Cantona/Zico... chi co the mua 0)  # tc_grade: Team Color nang cap (the +2..+20)  # GM Tool > Tran dau > Thi truong


def server_options():
    """Tuy chon server (gamedata/server_options.json; GM Tool sua khong can khoi dong lai). Doc lai khi
    file doi (market_listed goi ham nay cho tung the ~60k lan / Top 50 Thi truong)."""
    try:
        s = os.stat(SERVER_OPTIONS_FILE)
        sig = (SERVER_OPTIONS_FILE, s.st_mtime_ns, s.st_size)
    except OSError:
        return dict(SERVER_OPTIONS_DEFAULT)
    if _SERVER_OPTIONS_CACHE[0] != sig:
        try:
            with open(SERVER_OPTIONS_FILE, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        _SERVER_OPTIONS_CACHE[:] = [sig, {**SERVER_OPTIONS_DEFAULT, **(d if isinstance(d, dict) else {})}]
    return dict(_SERVER_OPTIONS_CACHE[1])


_SERVER_OPTIONS_CACHE = [None, {}]


def card_cap():
    """Cap the toi da dang mo trong game (GM Tool > Ty le dap the; server_options "max_grade", mac dinh +20 = MAX_GRADE).
    Ap cho the MOI (goi, qua, doi cau thu, Thi truong, GM Tool) va dap the; the dang co cao hon duoc giu nguyen."""
    try:
        cap = max(5, min(MAX_GRADE, int(server_options().get("max_grade") or MAX_GRADE)))
    except (TypeError, ValueError):
        cap = MAX_GRADE
    if _CARD_CAP[0] != cap:  # vua doi tren GM Tool: gia goi (theo khoang cap) tinh lai, game tai lai Shop
        first = _CARD_CAP[0] is None
        _CARD_CAP[0] = cap
        try:
            pack_price.cache_clear()
            if not first:
                shop_changed()
        except Exception:
            pass
    return cap


_CARD_CAP = [None]


def market_listed(spid):
    """Co rao ban tren Thi truong chuyen nhuong: on_market, tru the mua 0 (the mau cua engine,
    spid < 1e6, chi so 120+) khi bat "an the mua 0" o GM Tool."""
    if int(spid) < 1000000 and server_options().get("hide_engine_cards"):
        return False
    return on_market(spid)


def market_offer(spid, grade=1):
    return {"spid": spid, "expandme": "player", "grade": grade, "price": card_price(spid, grade), "count": 1}


def ep_transfer_top(position, query, body):
    """GET /transfer/top/50[/position/<GK|DF|MF|FW>]: the 50 priciest tradeable cards."""
    rng = LINES.get(str(position or "").upper())
    where = f"AND p.preferredposition1 BETWEEN {rng.start} AND {rng.stop - 1}" if rng else ""
    with closing(_db()) as c:
        both = c.execute(f"""SELECT p.spid, {POSOVR}, p.overallrating FROM players p
                             WHERE 1 {where}""").fetchall()  # every Market card, licensed or not
    _OVR.update((s, (o, d)) for s, o, d in both)
    rows = [(s, o) for s, o, _ in both]
    hide0 = server_options().get("hide_engine_cards")
    spids = [s for s, ovr in sorted((r for r in rows if on_market(r[0]) and not (hide0 and r[0] < 1000000)),
                                    key=lambda r: (-r[1], r[0]))[:50]]
    return _json([market_offer(s) for s in spids])


def ep_transfer_search(spid, query, body):
    """GET /transfer/search/<spid>: that card's offers (grades 1..20); none if not tradeable."""
    return _json([market_offer(int(spid), g) for g in range(1, card_cap() + 1)] if market_listed(spid) else [])


def add_player(team, spid, grade=1, cap=True):
    """A new card instance in my club (reserves); returns it. cap: grade <= card_cap() (GM Tool)."""
    if cap:
        grade = min(int(grade), card_cap())
    with closing(_db()) as c:
        r = c.execute("SELECT playerid FROM players WHERE spid = ?", (int(spid),)).fetchone()
        name = gen_teams.jersey_name(c, r[0]) if r else ""
    used = {p.get("jersey") for p in team["players"]}
    p = {"_id": new_iid(), "spid": int(spid), "expandme": "player", "grade": int(grade),
         "jersey": next((n for n in range(12, 100) if n not in used), 99), "jerseyname": name}
    if first_team_count(team) >= FIRST_TEAM_MAX:
        p["team2"] = True  # Tim inti full
    team["players"].append(p)
    return p


def remove_player(team, p):
    """Drop a card instance from my club and from the squad, captain and kickers."""
    team["players"].remove(p)
    sq, pid = team["squad"], iid(p)
    sq["positions"] = [s for s in sq["positions"] if str(s.get("id") or s["spid"]) != pid]
    sq["kickers"] = [k for k in sq.get("kickers", []) if str(k) != pid]
    repair_squad(team)
    if str(sq.get("captain")) == pid:
        sq["captain"] = next((str(s.get("id") or s["spid"]) for s in sq["positions"] if s["pos"]), "")


def is_starter(team, p):
    return any(str(s.get("id") or s["spid"]) == iid(p) and s["pos"] < POS_SUB for s in team["squad"]["positions"])


def in_squad(team, p):
    """Starter or sub (the 18 on the team sheet); reserves are not."""
    return any(str(s.get("id") or s["spid"]) == iid(p) for s in team["squad"]["positions"])


def ep_transfer_purchase(spid, grade, price, query, body):
    """POST /transfer/purchase/<spid>/<grade>/<price>/purchase: pay the market price (not the
    path's) and add a new instance of the card to my players (reserves)."""
    spid, grade = int(spid), int(grade)
    st = _state(); slot = _default_slot(st); team = st["teams"][str(slot)]
    if not on_market(spid):
        return _json({"result": False, "reason": "NOT_EXIST_OFFER"})
    if len(team["players"]) >= MAX_PLAYERS:
        return _json({"result": False, "reason": "CLB đã đầy, hãy thanh lý bớt cầu thủ."})
    if int(grade) > card_cap():
        return _json({"result": False, "reason": f"Cấp thẻ tối đa hiện tại là +{card_cap()}."})
    cost = card_price(spid, grade)
    if coach_me(st, slot)["money"]["ep"] < cost:
        return _json({"result": False, "reason": "NOT_ENOUGH_EP"})
    add_ep(st, -cost)
    add_player(team, spid, grade)
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


# Selling: the NPC market buys at once. The offer shows as "sold" in Daftar jual saya until
# its EP is collected (price minus SALE_FEE). Asking more than the market price is refused,
# so buying back is never cheaper than selling.
SALE_FEE = 0.10
# transfer-fee discounts (TransferMarketProxy.modifyOffersFee: floor(factor * completefee.base)):
# PC warnet (IGR) 20%, VIP 30% -- fifaaddict's FO3 tax calculator; both always active here
NO_BOOST = {"igr": 0.2, "vip": 0.3, "event": 0, "item": 0, "discountlimit": 0}


def sale_net(o):
    fee = o["completefee"]["base"]
    return o["price"] - fee + int(NO_BOOST["igr"] * fee) + int(NO_BOOST["vip"] * fee)


def offer_list(st):
    """/transfer/my/offer/list (TransferMarketProxy.fetch*): offers[] {_id, status
    selling|sold|expired, issuedat, completefee.base (fee in EP), spid, grade, price}."""
    return {"offerexpireoffset": 86400000, "boost": NO_BOOST, "offers": st.get("offers", [])}


def offer_resources(st):
    return [{"uri": "/transfer/my/offer/list", "body": offer_list(st)}] + coach_me_resources(st, _default_slot(st))


def ep_transfer_price(spid, grade, query, body):
    """GET /transfer/price/<spid>/<grade>: the Jual tab's price band and default."""
    p = card_price(spid, int(grade))
    return _json({"lower": p * 8 // 10, "upper": p, "default": p, "lowest": p, "registrationFee": 0})


def ep_transfer_favorites(query, body):
    """POST /transfer/favorites {add|remove|delete: [[spid, grade], ...]} (Chuyen nhuong > Thich):
    luu vao coach["favorites"] (toi da 30), tra lai /coach/me -> CoachEvent.ON_TRANSFERFAVORITES_CHANGED
    doc coach.transferfavorites."""
    req = json.loads(body or b"{}")
    st = _state(); slot = _default_slot(st); coach = coach_of(st, slot)
    fav = [list(x) for x in coach.get("favorites", [])]
    def norm(x):
        try:
            return [int(x[0]), int(x[1]) if len(x) > 1 else 1]
        except (TypeError, ValueError, IndexError):
            return None
    for k in ("remove", "delete", "del"):
        for x in req.get(k) or []:
            n = norm(x)
            if n:
                fav = [f for f in fav if f[0] != n[0]]
    for x in req.get("add") or []:
        n = norm(x)
        if n and all(f[0] != n[0] for f in fav):
            fav.append(n)
    coach["favorites"] = fav[-30:]
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


def ep_transfer_offer(query, body):
    """POST /transfer/offer {playerobjectid, price}: sell one of my players (not a starter)."""
    req = json.loads(body or b"{}")
    st = _state(); team = st["teams"][str(_default_slot(st))]
    pid = str(req.get("playerobjectid"))
    p = next((p for p in team["players"] if iid(p) == pid), None)
    price = int(req.get("price") or 0)
    if p is None or is_starter(team, p):
        return _json({"result": False, "reason": "TRANSFERMARKET_SELECT_SELLABLE_PLAYER"})
    if not 0 < price <= card_price(p["spid"], grade_of(p)):
        return _json({"result": False, "reason": "TRANSFERMARKET_LOWERLIMIT"})
    remove_player(team, p)
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    st.setdefault("offers", []).append({
        "_id": f"{int(time.time()):08x}{os.urandom(8).hex()}", "status": "sold", "issuedat": now, "soldat": now,
        "spid": p["spid"], "expandme": "player", "grade": grade_of(p), "price": price,
        "playerobjectid": pid, "completefee": {"base": int(price * SALE_FEE)}})
    _save(st)
    return _json({"result": True, "resources": offer_resources(st)})


def ep_offer_collect(offerid, coupon, query, body):
    """DELETE /transfer/my/offer/completed/<id|all>[/<coupon>]: collect sold offers' EP."""
    st = _state(); offers = st.get("offers", [])
    done = [o for o in offers if o["status"] == "sold" and offerid in ("all", o["_id"])]
    if not done:
        return _json({"result": False, "reason": "NOT_EXIST_OFFER"})
    ep = sum(sale_net(o) for o in done)
    add_ep(st, ep)
    st["offers"] = [o for o in offers if o not in done]
    _save(st)
    return _json({"result": True, "ep": ep, "resources": offer_resources(st)})


# --------------------------------------------------------------------------- #
# Tim > Manajemen pemain > Upgrade (enhancement v1, TeamManagementPlayerEnhancementOldTabPage
# + TeamManagementPlayerEnhancingPopupScreen): two cards of the same player, the lower-grade
# one (material) is consumed. Buy a second copy on the Market to have a pair.
# --------------------------------------------------------------------------- #
ENHANCE_RATE = (1.0, 1.0, 0.81, 0.64, 0.50, 0.26, 0.15, 0.07, 0.04, 0.02,  # +g -> +(g+1) chance
                0.015, 0.012, 0.01, 0.008, 0.006, 0.005, 0.004, 0.003, 0.002, 0.001, 0)
ENHANCE_DROP_FROM = 5  # a failed upgrade from +5 or higher drops one grade


def enhance_pair(team, a, b):
    pa = next((p for p in team["players"] if iid(p) == str(a)), None)
    pb = next((p for p in team["players"] if iid(p) == str(b)), None)
    if pa is None or pb is None or pa is pb or pa["spid"] != pb["spid"] or grade_of(pa) >= card_cap():
        return None, None
    return pa, pb


def cap_reason(team, a):
    """Thong bao khi the muc tieu da o cap toi da GM Tool cho phep (thay cho INVALID_PARAM kho hieu)."""
    p = next((x for x in team["players"] if iid(x) == str(a)), None)
    if p is not None and grade_of(p) >= card_cap():
        return f"Thẻ đã đạt cấp tối đa (+{card_cap()})."
    return "INVALID_PARAM"


def enhance_rate(target, material):
    import enhancement_settings
    grades = enhancement_settings.get_grades()  # GM Tool: ty le rieng tung cap (+g -> +g+1)
    if grades is not None:
        return grades[min(len(grades) - 1, max(0, grade_of(target)))] / 100.0
    override = enhancement_settings.get_rate()
    if override is not None:
        return override / 100.0
    return default_enhance_rate(target, material)


def default_enhance_rate(target, material):
    """Base chance by target grade times the bonus the Upgrade tab shows as "Rasio bonus
    keberhasilan" (TeamManagementPlayerEnhancementOldTabPage.updatePreviewText): material +1 or
    lower = Kosong, at least 2/3 of the target grade = High, at least 1/3 = Normal, else Low.
    The multipliers are ours (user: "Ya, samakan"); the original ones were server data."""
    tg, mg = grade_of(target), grade_of(material)
    bonus = 1.0 if mg <= 1 else 1.5 if mg >= tg * 2 / 3 else 1.25 if mg >= tg / 3 else 1.1
    return min(1.0, ENHANCE_RATE[tg] * bonus)


ENHANCE_FEE_PCT = 5  # phi nang cap / phuc hoi = 5% gia tri the (user 2026-11-09: "chi phi nang cap cao qua, de vua phai")


def enhance_cost(target, material=None, rate=None, v25=False, mat_value=None):
    """EP 1 lan nang = ENHANCE_FEE_PCT % gia tri the (it nhat 1.000), moi che do Shop. (Truoc: Shop tinh phi lay gia
    tri trung binh lan nang them vao the - gap 20-40 lan, ban be keu dat.)"""
    g = grade_of(target)
    return max(1000, sig3(card_price(target["spid"], g) * ENHANCE_FEE_PCT // 100))


def _enhance_cost_value(target, material=None, rate=None, v25=False, mat_value=None):
    """(Khong dung) cach tinh cu khi Shop tinh phi: gia tri trung binh lan nang them - phoi."""
    g = grade_of(target)
    base = max(1000, sig3(card_price(target["spid"], g) // 50))
    if (material is None and mat_value is None) or rate is None or not shop_paid():
        return base
    mv = getattr(sys.modules[__name__], "market_value", None) or card_price
    v = lambda x: max(card_price(target["spid"], x), mv(target["spid"], x))  # gia cao nhat: ban duoc
    if v25:  # that bai: giu cap hoac rot toi da 2 cap (enh25_roll_restore)
        fails = list(range(max(1, g - 2), g + 1))
        fail_v = sum(v(x) for x in fails) / len(fails)
    else:
        fail_v = v(g - 1 if g >= ENHANCE_DROP_FROM else g)
    mat = mat_value if material is None else card_price(material["spid"], grade_of(material))
    gain = rate * v(min(MAX_GRADE, g + 1)) + (1 - rate) * fail_v - v(g) - mat
    return max(base, nice_ceil(gain))


def merge_into(team, keep, gone):
    """The consumed card's squad slot (starter > sub) passes to the kept card of the same
    player, so an upgrade never leaves a hole in the XI; then the consumed card goes."""
    sq, k, g = team["squad"], iid(keep), iid(gone)
    slot = {str(s.get("id") or s["spid"]): s for s in sq["positions"]}
    if g in slot and (k not in slot or slot[g]["pos"] < POS_SUB <= slot[k]["pos"]):
        if k in slot:
            sq["positions"].remove(slot[k])
        slot[g]["id"] = k
        if str(sq.get("captain")) == g:
            sq["captain"] = k
        sq["kickers"] = [k if str(x) == g else x for x in sq.get("kickers", [])]
    remove_player(team, gone)


def ep_enhance_preview(a, b, query, body):
    """GET /player/<target>/enhance/<material>/preview -> {grade, levelinfo, price, enhancepay}."""
    st = _state(); team = st["teams"][str(_default_slot(st))]
    pa, pb = enhance_pair(team, a, b)
    if pa is None:
        return _json({"result": False, "reason": "INVALID_PARAM"})
    g = grade_of(pa) + 1
    return _json({"result": True, "grade": g, "levelinfo": pa.get("levelinfo", {"level": 1, "exp": 0}),
                  "price": card_price(pa["spid"], g), "enhancepay": enhance_cost(pa, pb, enhance_rate(pa, pb)),
                  "probability": round(enhance_rate(pa, pb) * 100)})


def ep_enhance(a, b, boost, guard, query, body):
    """PUT /player/<target>/enhance/<material>[/<boostitem>/<guarditem>] -> {success, grade,
    rgrade, rprice, attribPlus} (TeamManagementPlayerEnhancingPopupScreen.onEnhanceComplete)."""
    st = _state(); slot = _default_slot(st); team = st["teams"][str(slot)]
    pa, pb = enhance_pair(team, a, b)
    if pa is None:
        return _json({"result": False, "reason": cap_reason(team, a)})
    cost = enhance_cost(pa, pb, enhance_rate(pa, pb))
    if coach_me(st, slot)["money"]["ep"] < cost:
        return _json({"result": False, "reason": "Giá EP mục tiêu không hợp lệ."})
    add_ep(st, -cost)
    before = grade_of(pa)
    success = random.random() < enhance_rate(pa, pb)
    after = before + 1 if success else before - 1 if before >= ENHANCE_DROP_FROM else before
    pa["grade"] = after
    merge_into(team, pa, pb)
    mission_event(st, upgrade=int(after > before))  # nhiem vu: nang cap thanh cong
    _save(st)
    return _json({"result": True, "success": success, "grade": after, "rgrade": after,
                  "rprice": card_price(pa["spid"], after), "attribPlus": GRADE_BOOST[after] - GRADE_BOOST[before],
                  "resources": coach_me_resources(st, slot) + achievement_resources(st)})


# --------------------------------------------------------------------------- #
# Nang cap v2.5 (TeamManagementPlayerEnhancement2_5TabPage + EnhancingPopupScreen, bat bang
# features teammanagement.playermanagement.enhancement = {"v2_5": {}}):
#   GET  /player/<t>/enhance2.5/<m>/preview/<diem 0|1>/<vat pham|-1>/<su kien|-1>
#   PUT  /player/<t>/enhance2.5/<m>/<diem 0|1>/<vat pham|-1>/<su kien|-1>
#   PUT  /player/enhance2.5/restore   (phuc hoi lai co tra phi, 1 lan sau moi lan that bai)
# Diem nang cap (es) luu o coach["es"]; that bai duoc cong diem, tick "dung diem" tang ti le.
# That bai: the ve rgrade (phuc hoi lan dau mien phi, game tu chay hieu ung), sau do co the
# "Thu phuc hoi lai" 1 lan (ton diem). Bang PE2STATICVALUES: responses/static_db_PE2STATICVALUES.json.
# --------------------------------------------------------------------------- #
ENH25_POINT_BONUS = 150  # % ti le khi dung diem (= PE2STATICVALUES.bonus.use)


def enh25_need_es(target):
    return 10 * max(1, grade_of(target))


def enh25_item(coach, target, item):
    """Bua nang cap trong kho (staticdata ENHANCEMENT_BOOST, dung cap the) hoac None."""
    try:
        iid_ = int(item)
    except (TypeError, ValueError):
        return None
    it = next((i for i in coach.get("items", []) if i["itemid"] == iid_ and i.get("remains", 0) > 0), None)
    if it is None or iid_ not in ITEMS or ITEMS[iid_][2] != "ENHANCEMENT_BOOST":
        return None
    eff = ITEMS[iid_][3]
    # mingrade/maxgrade = cap SAU khi nang (EnhanceControlPanel: targetGrade = cap cao nhat + 1)
    return it if eff["mingrade"] <= grade_of(target) + 1 <= eff["maxgrade"] else None


def enh25_rate(target, material, use_point, item_eff=None):
    r = enhance_rate(target, material)
    if use_point:
        r *= ENH25_POINT_BONUS / 100
    if item_eff:
        r = 1.0 if item_eff.get("sure") else r * item_eff.get("mult", 1)
    return min(1.0, r)


def enh25_roll_restore(before):
    """Cap sau phuc hoi: giu nguyen hoac rot toi da 2 cap (khong ve +0)."""
    return random.choice(range(max(1, before - 2), before + 1))


def ep_enh25_preview(a, b, use_point, item, event, query, body):
    st = _state(); team = st["teams"][str(_default_slot(st))]
    pa, pb = enhance_pair(team, a, b)
    if pa is None:  # luon tra du truong (thieu levelinfo -> loi giao dien)
        return _json({"grade": 0, "price": 0, "levelinfo": {"level": 1, "exp": 0}, "exp": 0,
                      "enhancepay": 0, "needes": 0, "probability": 0})
    g = grade_of(pa) + 1
    lv = player_levelinfo(pa)
    it = enh25_item(my_coach(st), pa, item) if item != "-1" else None
    eff = ITEMS[it["itemid"]][3] if it else None
    return _json({"result": True, "grade": g, "price": card_price(pa["spid"], g), "levelinfo": lv,
                  "exp": lv.get("exp", 0), "needes": enh25_need_es(pa),
                  "enhancepay": enhance_cost(pa, pb, enh25_rate(pa, pb, use_point == "1", eff), True),
                  "probability": round(enh25_rate(pa, pb, use_point == "1", eff) * 100)})


def ep_enh25(a, b, use_point, item, event, query, body):
    st = _state(); slot = _default_slot(st); team = st["teams"][str(slot)]; coach = my_coach(st)
    pa, pb = enhance_pair(team, a, b)
    if pa is None:
        return _json({"result": False, "reason": cap_reason(team, a)})
    need = enh25_need_es(pa)
    use = use_point == "1" and int(coach.get("es", 0)) >= need
    it = None
    if item != "-1":
        it = enh25_item(coach, pa, item)
        if it is None:
            return _json({"result": False, "reason": "expired item"})
    cost = enhance_cost(pa, pb, enh25_rate(pa, pb, use, ITEMS[it["itemid"]][3] if it else None), True)
    if coach_me(st, slot)["money"]["ep"] < cost:
        return _json({"result": False, "reason": "Invalid target ep price"})
    add_ep(st, -cost)
    if use:
        coach["es"] = int(coach.get("es", 0)) - need
    eff = ITEMS[it["itemid"]][3] if it else None
    if it:  # bua dung mot lan
        it["remains"] -= 1
        if it["remains"] <= 0:
            coach["items"].remove(it)
    before = grade_of(pa)
    success = random.random() < enh25_rate(pa, pb, use, eff)
    out = {"result": True, "success": success}
    if success:
        pa["grade"] = before + 1
        out.update(grade=before + 1, rgrade=before + 1, rprice=card_price(pa["spid"], before + 1),
                   attribPlus=GRADE_BOOST[before + 1] - GRADE_BOOST[before])
        coach.pop("enh25", None)
    else:
        rg = enh25_roll_restore(before)
        grant = 20 + 10 * before
        pa["grade"] = rg
        coach["es"] = int(coach.get("es", 0)) + grant
        usepoint = restore_cost(pa, before, rg, need)
        coach["enh25"] = {"id": iid(pa), "before": before, "price": usepoint["price"], "currency": usepoint["currency"],
                          "used": rg == before}
        out.update(grade=rg, rgrade=rg, rprice=card_price(pa["spid"], rg), grantes=grant, usepoint=usepoint, attribPlus=0)
    merge_into(team, pa, pb)
    mission_event(st, upgrade=int(success))
    _save(st)
    out["resources"] = coach_me_resources(st, slot) + achievement_resources(st)
    return _json(out)


def restore_cost(p, before, now, need):
    """Gia 1 lan phuc hoi sau khi nang that bai: diem nang cap (Shop mien phi) hoac, khi Shop tinh
    phi, EP = gia tri trung binh lan phuc hoi tra lai (diem nang cap kiem duoc mien phi -> khong in EP)."""
    if not shop_paid():
        return {"currency": "ES", "price": need}
    # Shop tinh phi: EP = ENHANCE_FEE_PCT % gia tri the o cap truoc khi rot (truoc: gia tri trung binh tra lai, rat dat)
    return {"currency": "EP", "price": max(1000, nice_ceil(card_price(p["spid"], before) * ENHANCE_FEE_PCT / 100))}


def ep_enh25_restore(query, body):
    st = _state(); slot = _default_slot(st); team = st["teams"][str(slot)]; coach = my_coach(st)
    rec = coach.get("enh25") or {}
    p = next((x for x in team["players"] if iid(x) == rec.get("id")), None)
    if p is None or rec.get("used"):
        return _json({"result": False, "reason": "ENHANCE_RESTORE_ERROR"})
    if rec.get("currency") == "EP":  # Shop tinh phi: phuc hoi tra bang EP (= gia tri tra lai)
        if coach_me(st, slot)["money"]["ep"] < rec["price"]:
            return _json({"result": False, "reason": "NOT_ENOUGH_EP"})
        add_ep(st, -rec["price"])
    else:
        if int(coach.get("es", 0)) < rec["price"]:
            return _json({"result": False, "reason": "Không đủ điểm nâng cấp để phục hồi"})
        coach["es"] = int(coach.get("es", 0)) - rec["price"]
    g = enh25_roll_restore(rec["before"])
    p["grade"] = g
    rec["used"] = True
    _save(st)
    return _json({"result": True, "success": False, "grade": g, "rprice": card_price(p["spid"], g), "grantes": 0,
                  "resources": coach_me_resources(st, slot)})


# --------------------------------------------------------------------------- #
# Tim > Manajemen pemain > Tukar (player trade v1, TeamManagementPlayerRecycleTabPage +
# TeamManagementPlayerRecyclePopupScreen). Give 2..10 reserves; clubs offer Market cards
# rated at least avg(card OVR) + playertrade_cardovrbonus[n] (the tab's "expected OVR"):
# playertrade_normal[n] offers plus one each for PC warnet and VIP (the popup has 5 slots).
# An offer is a card, a card plus EP, or EP only (no spid). Tables: static_db_STATICVALUES.json.
# --------------------------------------------------------------------------- #
_TRADE_POOL = {}


def static_values():
    with open(os.path.join(RESPONSES, "static_db_STATICVALUES.json"), encoding="utf-8") as f:
        return json.load(f)


def trade_grades(gsum):
    """PLAYERTRADE.playergrade[sum of the given cards' grades] -> {grade: weight}: +1 always,
    one more possible grade per 8 grade points traded (the tab shows its first..last key)."""
    return {str(g): 0.5 ** (g - 1) for g in range(1, min(MAX_GRADE, 1 + int(gsum) // 8) + 1)}


def trade_pool():
    """{client rating: [(spid, teamid)]} of every Market card, any season (cached). It used to
    take only cards licensed for the ID region, which left out most old and special seasons
    (user: "trading munculkan semua playernya")."""
    if not _TRADE_POOL:
        with closing(_db()) as c:
            for s, t, o in c.execute(f"SELECT p.spid, p.teamid, {POSOVR} FROM players p"):
                if on_market(s) and s >= 1000000:  # bo the mua 0 (the mau engine, hien nham '17')
                    _TRADE_POOL.setdefault(o, []).append((s, t))
    return _TRADE_POOL


def trade_props(give, rng):
    sv = static_values()
    lim, n = sv["playertrade_limit"], len(give)
    avg = sum(card_ovr(p["spid"]) for p in give) / n
    lo = int(min(max(avg + sv["playertrade_cardovrbonus"][str(n)], lim["CardMinValue"]), lim["CardMaxValue"]))
    total = sum(card_price(p["spid"], grade_of(p)) for p in give)
    weights = trade_grades(sum(grade_of(p) for p in give))
    pool = trade_pool()
    props = []
    for i, boost in enumerate([None] * int(sv["playertrade_normal"][str(n)]) + ["igr", "vip"]):
        ovr = lo
        while ovr < min(lo + 8, lim["CardMaxValue"]) and rng.random() < 0.55:  # sometimes better
            ovr += 1
        s, t = rng.choice(pool[min(pool, key=lambda r: (abs(r - ovr), -r))])
        pp = prime_pick(0, card_cap(), rng, by_ovr=ovr)
        if pp:
            s, t = pp[1], pp[2]
        if i == 1 and boost is None:  # a club buys the lot for EP
            props.append({"teamid": t, "ep": sig3(total * rng.uniform(0.9, 1.15))})
            continue
        g = min(card_cap(), int(rng.choices(list(weights), list(weights.values()))[0]))
        offer = {"spid": s, "grade": g, "teamid": t, "price": card_price(s, g)}
        if i == 2 and boost is None:  # card plus a top-up
            offer["ep"] = sig3(total * rng.uniform(0.05, 0.2))
        if boost:
            offer["boost"] = boost  # always a PC warnet + VIP coach here
        props.append(offer)
    return props


def trade_cap_props(props, give, gmax=None):
    """Shop tinh phi: khong de nghi trao doi nao (the + EP kem) dang gia hon cac the da dua - ca theo
    gia tri the lan gia thi truong (khong thi doi qua doi lai la in ra EP). The vuot muc -> the dat nhat
    vua muc (cap <= gmax); EP kem bot lai cho vua. Shop mien phi: giu nguyen."""
    gmax = min(gmax or MAX_GRADE, card_cap())  # GM Tool: cap the toi da
    if not shop_paid() or not give:
        return [dict(o, grade=min(o["grade"], gmax), price=card_price(o["spid"], min(o["grade"], gmax))) if o.get("grade", 0) > gmax else o
                for o in props]
    mv = getattr(sys.modules[__name__], "market_value", None) or card_price
    cap_v = sum(card_price(p["spid"], grade_of(p)) for p in give)
    cap_m = sum(mv(p["spid"], grade_of(p)) for p in give)
    pool = trade3_pool()
    rng = random.Random()
    used = {o["spid"] for o in props if "spid" in o}
    out = []
    for o in props:
        o = dict(o)
        if "spid" not in o:  # CLB mua ca lo bang EP
            if "ep" in o:
                o["ep"] = max(0, min(int(o["ep"]), int(min(cap_v, cap_m))))
            out.append(o)
            continue
        val = lambda s, g: max(card_price(s, g), mv(s, g))
        cap = min(cap_v, cap_m)
        if val(o["spid"], o["grade"]) > cap:
            # the vua muc: ngau nhien trong cac the gia 80..100% muc, moi de nghi 1 the khac (truoc: luon the dat
            # nhat vua muc -> ca 5 de nghi cung 1 cau thu, user 2026-11-09)
            j = bisect.bisect_right(pool, (cap, math.inf)) - 1
            cands, first = [], None
            while j >= 0 and pool[j][0] >= cap * 0.8 and len(cands) < 60:
                r = pool[j]
                if r[3] <= gmax and val(r[1], r[3]) <= cap:
                    first = first or r
                    if r[1] not in used:
                        cands.append(r)
                j -= 1
            if not cands and first is None:  # khong co the nao trong khoang: the dat nhat vua muc nhu truoc
                while j > 0 and (pool[j][3] > gmax or val(pool[j][1], pool[j][3]) > cap):
                    j -= 1
                first = pool[max(0, j)]
            price, s, t, g = rng.choice(cands) if cands else first
            used.add(s)
            o.update(spid=s, teamid=t, grade=g, price=price)
        if o.get("ep"):
            left = min(cap_v - o["price"], cap_m - mv(o["spid"], o["grade"]))
            o["ep"] = max(0, min(int(o["ep"]), int(left)))
            if not o["ep"]:
                o.pop("ep")
        out.append(o)
    return out


def ep_trade_proposal(query, body):
    """POST /players/trade/proposal {players: [iid...]} -> {result, props}."""
    req = json.loads(body or b"{}")
    st = _state(); team = st["teams"][str(_default_slot(st))]
    ids = [str(i) for i in req.get("players") or []]
    give = [p for p in team["players"] if iid(p) in ids]
    if not 2 <= len(give) <= 10 or len(give) != len(set(ids)) or any(in_squad(team, p) for p in give):
        return _json({"result": False, "reason": "Cầu thủ không hợp lệ."})  # the tab lists reserves only
    props = trade_cap_props(trade_props(give, random.Random()), give)
    st["trade"] = {"players": ids, "props": props}
    _save(st)
    return _json({"result": True, "props": props})


def ep_trade_contract(query, body):
    """POST /players/trade/contract {select: offer index}: the given players leave, the
    offer's card and/or EP arrive. With the trade3 feature on, the popup (shared with the
    Scout tab) posts /trade3/contract {slotnum, selectnum} instead, also for this tab."""
    req = json.loads(body or b"{}")
    st = _state(); slot = _default_slot(st); team = st["teams"][str(slot)]
    tr = st.get("trade") or {}
    props, ids = tr.get("props") or [], tr.get("players") or []
    sel = int(req.get("select", req.get("selectnum", -1)))
    give = [p for p in team["players"] if iid(p) in ids]
    if not 0 <= sel < len(props) or len(give) != len(ids):
        return _json({"result": False, "reason": "Cầu thủ không hợp lệ."})
    for p in give:
        remove_player(team, p)
    offer = props[sel]
    if "spid" in offer:
        add_player(team, offer["spid"], offer["grade"])
    add_ep(st, offer.get("ep", 0))
    st.pop("trade")
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


# --------------------------------------------------------------------------- #
# Tim > Manajemen pemain > Tukar #2 (trade3 "Scout": TeamManagementPlayerTrade3TabPage +
# ScouterController). Pick a scout, give 2..10 reserves: offers are Market cards worth about
# their total value (fifaaddict prices) times the scout's range. Scouts (coach/me `scouters`)
# differ in range and cooldown; meeting a scout's quest shifts the range up. Same popup and
# /trade3/contract as Tukar v1 (the shared popup runs in trade3 mode while the feature is on).
# --------------------------------------------------------------------------- #
NO_LIMIT = 9_000_000_000_000_000  # "unlimited": below 2^53, so exact as an AS3 Number
TRADE3_STATIC = {  # GET /trade3/staticvalues -> ScouterController.staticvalues
    # total EP given / reward cap; user: "buat tidak terbatas" (was 30 / 10 billion EP: the tab
    # warned "Hanya dapat memperoleh pemain maximum 10,000,000,000 EP")
    "playertrade3_limit": {"max_trade": NO_LIMIT, "max": NO_LIMIT},
    "vvip_playertrade3_limit": {"max_trade": NO_LIMIT, "max": NO_LIMIT},
    "playertrade3_normal": {str(n): 1 + (n >= 4) + (n >= 7) for n in range(2, 11)},  # offers by #players
    "playertrade3_effect_price": [10_000_000, 3_000_000, 1_000_000],  # popup premium effect tiers
}
SCOUTS = (  # (key = scoutid, string id, cooldown s, offer value x total, quest); always PC + VIP here
    # no cooldowns (user: "buat scout tidak ada reset"; were 1 day / 1 hour / 1 hour)
    ("default_A01", "STR_SCOUTER_KR_DEFAULT_A01", 0, (0.8, 1.1), None),
    ("default_B01", "STR_SCOUTER_KR_DEFAULT_B01", 0, (0.9, 1.3), {"require_value": 100000}),
    ("igr", "STR_SCOUTER_KR_IGR_A01", 0, (0.9, 1.4), {"require_grade": 2, "require_grade_count": 2}),
    ("vip", "STR_SCOUTER_KR_VIP_A01", 0, (1.0, 1.5), {"require_season": "88", "require_season_count": 2}),
    # VVIP (client co san: anh scouter_vvip.png, gioi han vvip_playertrade3_limit) - tai khoan VVIP mien phi
    ("vvip", "STR_SCOUTER_KR_VVIP_A01", 0, (1.2, 1.8), {"require_grade": 5, "require_grade_count": 2}),
)
QUEST_BONUS = 0.2  # quest met: the offer value range moves up by this x total
_T3_POOL = []


def w3c(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


def coach_scouters(st, slot):
    """coach/me `scouters` (CoachProxy.tradeScouters): a scout on cooldown is locked and carries
    `cooltime` (W3C) for the countdown (ScouterController re-asks /trade3/info when it ends)."""
    used, now = coach_of(st, slot).get("scout_used", {}), time.time()
    out = []
    for key, sid, cool, _, quest in SCOUTS:
        until = used.get(key, 0) + cool
        out.append({"key": key, "scoutid": key, "scoutername": sid + "_NAME", "scouterdesc": sid + "_DESC",
                    "lock": until > now, **({"cooltime": w3c(until)} if until > now else {}),
                    **({"quest": quest} if quest else {})})
    return out


def quest_met(quest, give):
    """The tab's quest checks: total value, cards of grade >= G, cards of the listed seasons."""
    if not quest:
        return False
    if sum(card_price(p["spid"], grade_of(p)) for p in give) < quest.get("require_value", 0):
        return False
    if "require_grade" in quest and sum(grade_of(p) >= quest["require_grade"] for p in give) < quest["require_grade_count"]:
        return False
    seasons = {int(s) for s in str(quest.get("require_season", "")).split(",") if s}
    return not seasons or sum(int(p["spid"]) // 1000000 in seasons for p in give) >= quest["require_season_count"]


def trade3_pool():
    """[(price, spid, teamid, grade)] of Market cards at every grade +1..+20, cheapest first
    (cached). Grade +1 only capped an offer at the dearest +1 card (~20 billion EP), so a trade
    of ~71 billion got far less back (user: "pertukaran tidak sebanding dengan nilai yang diawal")."""
    if not _T3_POOL:
        # moi the cua moi mua (ca huyen thoai: WL, EL, UL, CC, LP...); bo the goc khong mua (spid < 1e6,
        # game hien thanh '17) de giao dich ra dung cac mua
        rows = [r for o in trade_pool().values() for r in o if r[0] >= 1000000]
        card_ovrs([s for s, _ in rows])  # one bulk query instead of one per unpriced card
        _T3_POOL.extend(sorted((card_price(s, g), s, t, g) for s, t in rows for g in range(1, MAX_GRADE + 1)))
    return _T3_POOL


# Giao dich / Trao doi: the PRIME (94) va INFINITY PRIME (76) chi ~1.4% kho the -> gan nhu khong bao gio ra.
# Moi de nghi co PRIME_TRADE_CHANCE co hoi la mot the 2 mua nay gia tri gan nhat.
PRIME_SEASONS = (94, 76, 18, 19)  # + 26 TOTY / 26 TOTS
PRIME_TRADE_CHANCE = 0.3
_T3_PRIME = []


def prime_pick(target, gmax, rng, by_ovr=None):
    """(price, spid, teamid, grade) the PRIME/INFINITY PRIME gan target nhat (cap <= gmax), hoac None.
    by_ovr: chon theo chi so (giao dich thuong) thay vi theo gia."""
    if not _T3_PRIME:
        _T3_PRIME.extend(r for r in trade3_pool() if r[1] // 1000000 in PRIME_SEASONS)
    if not _T3_PRIME or rng.random() >= PRIME_TRADE_CHANCE:
        return None
    if by_ovr is not None:
        cands = [r for r in _T3_PRIME if r[3] <= gmax]
        cands = [r for r in cands if abs(card_ovr(r[1]) - by_ovr) <= 3]
        return rng.choice(cands) if cands else None
    ok = [r for r in _T3_PRIME if r[3] <= gmax]  # da sap theo gia
    if not ok:
        return None
    k = bisect.bisect_left(ok, (target,))
    near = sorted(ok[max(0, k - 20):k + 20], key=lambda r: abs(r[0] - target))[:20]
    return rng.choice(near)


def trade3_props(give, scout, rng):
    """playertrade3_normal[n] cards worth total x the scout's range (capped at the reward max;
    the last of them adds an EP top-up) + PC warnet and VIP extras."""
    lo, hi = scout[3]
    if quest_met(scout[4], give):
        lo, hi = lo + QUEST_BONUS, hi + QUEST_BONUS
    total = sum(card_price(p["spid"], grade_of(p)) for p in give)
    pool, normal = trade3_pool(), TRADE3_STATIC["playertrade3_normal"][str(len(give))]
    props = []
    for i, boost in enumerate([None] * normal + ["igr", "vip"]):
        target = min(TRADE3_STATIC["playertrade3_limit"]["max"], total * rng.uniform(lo, hi))
        k = bisect.bisect_left(pool, (target,))
        price, s, t, gr = prime_pick(target, card_cap(), rng) or rng.choice(pool[max(0, k - 10):k + 10])  # 20 the gan gia nhat
        if gr > card_cap():  # GM Tool gioi han cap the
            gr = card_cap(); price = card_price(s, gr)
        offer = {"spid": s, "grade": gr, "teamid": t, "price": price}
        if i == normal - 1 and normal > 1:
            offer["ep"] = sig3(max(1000, total * rng.uniform(0.05, 0.15)))
        if boost:
            offer["boost"] = boost
        props.append(offer)
    return props


def ep_trade3_proposal(query, body):
    """POST /trade3/proposal {id: scout key, slotnum, playerids: [{id, spid, grade, price}]}
    -> {result, props} (tab.onResponseRecycle opens the popup; it posts /trade3/contract)."""
    req = json.loads(body or b"{}")
    st = _state(); slot = _default_slot(st); team = st["teams"][str(slot)]; coach = my_coach(st)
    scout = next((s for s in SCOUTS if s[0] == req.get("id")), None)
    sent = {str(p.get("id")): p for p in req.get("playerids") or []}
    give = [p for p in team["players"] if iid(p) in sent]
    if (scout is None or not 2 <= len(give) <= 10 or len(give) != len(sent)
            or any(in_squad(team, p) or not p.get("team2") for p in give)):  # Tim Cadangan only
        return _json({"result": False, "reason": "Cầu thủ không hợp lệ."})
    if any(int(sent[iid(p)].get("price") or 0) != card_price(p["spid"], grade_of(p)) for p in give):
        return _json({"result": False, "reason": "TRADE3_NOTICE_PLAYERPRICE03"})  # client shows a recheck notice
    used = coach.setdefault("scout_used", {})
    if used.get(scout[0], 0) + scout[2] > time.time():
        return _json({"result": False, "reason": "TRADE2_ERROR_TOO_MANY_TRY"})  # still cooling down
    if sum(card_price(p["spid"], grade_of(p)) for p in give) >= TRADE3_STATIC["playertrade3_limit"]["max_trade"]:
        return _json({"result": False, "reason": "Cầu thủ không hợp lệ."})  # the tab blocks this first
    props = trade_cap_props(trade3_props(give, scout, random.Random()), give)
    if scout[2]:
        used[scout[0]] = time.time()  # a negotiation uses the scout, picked or not
    st["trade"] = {"players": [iid(p) for p in give], "props": props}
    _save(st)
    return _json({"result": True, "props": props, "resources": coach_me_resources(st, slot)})


def ep_player_move(pid, where, query, body):
    """POST /player/<_id>/move/firstteam|secondteam (Tim > Manajemen pemain > Skuad): Tim inti <->
    Tim Cadangan. Players in the 18 of the squad stay in Tim inti."""
    st = _state(); slot = _default_slot(st); team = st["teams"][str(slot)]
    p = next((x for x in team["players"] if iid(x) == pid), None)
    if p is None or (where == "secondteam" and in_squad(team, p)):
        return _json({"result": False, "reason": "TRANSFERMARKET_SELL_ERROR_FIRSTTEAM"})
    if where == "firstteam" and p.get("team2") and first_team_count(team) >= FIRST_TEAM_MAX:
        return _json({"result": False, "reason": "FIRSTTEAM_REGISTRATION_FAIL_POPUP_TITLE"})  # Tim inti full
    if where == "secondteam":
        p["team2"] = True
    else:
        p.pop("team2", None)
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


def ep_trade3_info(query, body):
    """POST /trade3/info {}: the client only reads the pushed resources (scouters)."""
    st = _state()
    return _json({"result": True, "resources": coach_me_resources(st, _default_slot(st))})


# --------------------------------------------------------------------------- #
# Shop > Pemain sells player packs for EP; Item saya > Pemain opens them one card at a time
# (TeamManagementPlayerCardScreen: POST /item/<_id>/use -> {player}). Icons are the real FO3 ID
# shop images in the client's CDN cache (externalAssets/shop/<itemid>.png). A pack draws a
# Market card from its pool, cheaper cards more likely (weight price^-0.75: ~0.5% for a card
# worth 10x the pool median); its price is the expected card value at fifaaddict prices.
# --------------------------------------------------------------------------- #
POSITIONS = {"GK": (0, 0), "DF": (1, 8), "MF": (9, 19), "FW": (20, 27)}  # preferredposition1
PACKS = {  # itemid (= shop icon): (name, pool)
    28001: ("Premier League 65+", {"league": 13, "ovr": 65}),
    28002: ("Bundesliga 65+", {"league": 19, "ovr": 65}),
    28003: ("LaLiga 65+", {"league": 53, "ovr": 65}),
    28004: ("Serie A 65+", {"league": 31, "ovr": 65}),
    28005: ("Ligue 1 65+", {"league": 16, "ovr": 65}),
    28006: ("FW 60+", {"pos": "FW", "ovr": 60}),
    28007: ("MF 60+", {"pos": "MF", "ovr": 60}),
    28008: ("DF 60+", {"pos": "DF", "ovr": 60}),
    28009: ("GK 60+", {"pos": "GK", "ovr": 60}),
    8223600: ("FW 65+", {"pos": "FW", "ovr": 65}),
    8223601: ("MF 65+", {"pos": "MF", "ovr": 65}),
    8223602: ("DF 65+", {"pos": "DF", "ovr": 65}),
    8223603: ("GK 65+", {"pos": "GK", "ovr": 65}),
    8223508: ("FW NEW 70+ 2017", {"pos": "FW", "ovr": 70, "season": 88}),
    8223509: ("MF NEW 70+ 2017", {"pos": "MF", "ovr": 70, "season": 88}),
    8223510: ("DF NEW 70+ 2017", {"pos": "DF", "ovr": 70, "season": 88}),
    82214068: ("World Best 300 2017", {"top": 300, "season": 88}),
    82214180: ("World Best 50 2017", {"top": 50, "season": 88}),
    # This Season = 2017 (spid season 88 = the client's DEFAULT_SEASON_ID, badge '17)
    45011: ("This Season Top 10", {"top": 10, "season": 88}),
    45012: ("This Season Top 20", {"top": 20, "season": 88}),
    45013: ("This Season Top 30", {"top": 30, "season": 88}),  # (Top 50 = World Best 50 2017)
    45097: ("This Season Top 30 +8", {"top": 30, "season": 88, "grade": 8}),
    # older seasons; fifaaddict season codes: 2006..2011 = 6..11, 2014 = 14, 2015 = 15
    45004: ("06-11 Player Top 10", {"top": 10, "seasons": (6, 7, 8, 9, 10, 11)}),
    45006: ("06-11 Player Top 30", {"top": 30, "seasons": (6, 7, 8, 9, 10, 11)}),
    45007: ("06-11 Player Top 50", {"top": 50, "seasons": (6, 7, 8, 9, 10, 11)}),
    82214356: ("All '07", {"season": 7}),
    739: ("'08 Top 3", {"top": 3, "season": 8}),
    15031: ("'14 Top 30 +5", {"top": 30, "season": 14, "grade": 5}),
    16001: ("'15 Top 30 +5", {"top": 30, "season": 15, "grade": 5}),
    # special seasons: wc2010 = 47, ucl2010 = 69, wc2014 = 77, tots2014 = 53, 16ec = 35, lp = 23,
    # wb (World Best) = 91, wl (World Legend) = 93
    7622: ("World Cup '10 Top 30", {"top": 30, "season": 47}),
    7618: ("Europe '10 Top 30 +3", {"top": 30, "season": 69, "grade": 3}),
    880047: ("World Cup '14 Top 50", {"top": 50, "season": 77}),
    45020: ("Special Season '14 Top 30", {"top": 30, "season": 77}),
    45063: ("Special Season '14 Top 50 +5", {"top": 50, "season": 77, "grade": 5}),
    45026: ("Team of the Season '14 Top 20", {"top": 20, "season": 53}),
    45027: ("Team of the Season '14 Top 30", {"top": 30, "season": 53}),
    45028: ("Team of the Season '14 Top 50", {"top": 50, "season": 53}),
    45130: ("Team of the Season '14 All", {"season": 53}),
    8157: ("EC Top 10", {"top": 10, "season": 35}),
    82232124: ("EC Top 30 +3", {"top": 30, "season": 35, "grade": 3}),
    8135: ("Best EC 30 +5", {"top": 30, "season": 35, "grade": 5}),
    8329: ("LP Top 50 +3", {"top": 50, "season": 23, "grade": 3}),
    8334: ("World Best 5", {"top": 5, "season": 91}),
    8336: ("World Best 1", {"top": 1, "season": 91}),
    82231307: ("World Best +5", {"season": 91, "grade": 5}),
    8194: ("World Legend Top 30", {"top": 30, "season": 93}),
    8335: ("World Legend Top 3", {"top": 3, "season": 93}),
    # every season
    82214007: ("Top 300", {"top": 300}),
    8223562: ("Best 30", {"top": 30}),
    # user: "pack baru khusus all session grade 1-8", then "all session pack  top 50 ganti menjadi top 200"
    7675: ("All Season Top 200 +8", {"top": 200, "grade": 8, "all": True}),
}
# "All <season>" like All '07 for every season fifaaddict lists (user: "yang keluar cuma 07,
# saya mau semua session"): (fifaaddict label, card season codes, closest shop icon). Codes
# checked against fifaaddict's own listings (e.g. tco92 = 49, mufclegend = 55, cel = 31 + 33).
FA_SEASONS = (
    ("06", (6,), 45004), ("06U", (63,), 7618), ("06WC", (65,), 7622), ("08", (8,), 739), ("08E", (67,), 8157),
    ("09", (9,), 45006), ("10", (10,), 45007), ("10U", (69,), 7618), ("10WC", (47,), 7622), ("11", (11,), 45004),
    ("14", (14,), 15031), ("14WC", (77,), 880047), ("14T", (53,), 45026), ("15", (15,), 16001), ("16", (16,), 16001),
    ("17", (88,), 45011), ("25L", (325,), 45011), ("26L", (300,), 45011), ("16EC", (35,), 8157), ("16TH", (45,), 8545), ("WB", (91,), 8334), ("WL", (93,), 8194),
    ("UB", (28,), 8336), ("UL", (30,), 8335), ("EL", (27,), 8194), ("LP", (23,), 8329), ("CP", (22,), 8549),
    ("CC", (24,), 8547), ("17TH", (44,), 8545), ("TCO92", (49,), 8546), ("MUA", (55,), 8547), ("ML", (71,), 8549),
    ("TL", (51,), 8549), ("VL", (61,), 8549), ("CL", (89,), 8549), ("KL02", (95,), 8546), ("CEL", (31, 33), 8335),
    ("U23", (29,), 8545), ("KXI", (97,), 8546), ("MUL", (48,), 8547), ("VS", (73,), 2000006),
    ("PRM", (94,), 8335), ("IPRM", (76,), 8335),  # PRIME / INFINITY (ma 94/76: ma 838/865 lam treo man Quan ly doi): 2 mua moi cua moi huyen thoai (icon FC Online)
    ("26TY", (18,), 8335), ("26TS", (19,), 8335),  # 26 TOTY / 26 TOTS (FO4 854/866, add_toty.py): the hien hanh +6 / +4
)
OWN_SEASONS_FILE = os.path.join(HERE, "gamedata", "seasons.json")  # GM Tool > Database: mua tu tao (ma < 100)


def own_seasons():
    try:
        with open(OWN_SEASONS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return {int(k): v for k, v in d.items() if str(k).isdigit() and isinstance(v, dict)} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


FA_SEASONS += tuple((str(v.get("tag") or f"S{k}")[:6], (k,), 8335) for k, v in sorted(own_seasons().items())
                    if k not in {c for _, cs, _ in FA_SEASONS for c in cs})  # goi Top 200 + nhan (tu lan bat server sau)
# each one the season's 200 best cards (user: "all session pack  top 50 ganti menjadi top 200")
PACKS.update({92000000 + codes[0]: (f"{label} Top 200", {"seasons": codes, "icon": icon, "top": 200, "all": True})
              for label, codes, icon in FA_SEASONS})
PACKS[82214356] = ("07 Top 200", {"season": 7, "top": 200, "all": True})  # the original All '07 item
# Goi theo bac cho 4 mua moi (user: "tao nhieu goi cho 4 mua moi, tu goi cui den goi cao, mo tu +8 -> +20"):
# itemid 93000000 + ma mua * 100 + bac; the ra o cap ngau nhien grade_min..grade (cap thap ra nhieu hon).
# Anh goi: externalAssets/shop/<itemid>.png (ve bang tools/season_pack_icons.py). Xep dau tab Goi cau thu.
SEASON_TIERS = ((None, 8, 8), (100, 8, 10), (50, 10, 13), (20, 13, 16), (10, 16, 18), (3, 18, 20))
SEASON_TIERS_SMALL = ((None, 8, 8), (15, 8, 10), (10, 10, 13), (5, 13, 16), (3, 16, 18), (1, 18, 20))  # 26TY: 23 the
SEASON_TIER_SEASONS = (("26TY", 18, SEASON_TIERS_SMALL), ("26TS", 19, SEASON_TIERS), ("PRM", 94, SEASON_TIERS),
                       ("IPRM", 76, SEASON_TIERS))
SEASON_TIER_PACKS = []
for _label, _code, _tiers in SEASON_TIER_SEASONS:
    for _n, (_top, _gmin, _gmax) in enumerate(_tiers, 1):
        _iid = 93000000 + _code * 100 + _n
        _g = f"+{_gmin}" if _gmin == _gmax else f"+{_gmin}~+{_gmax}"
        _f = {"season": _code, "grade_min": _gmin, "grade": _gmax, "all": True}
        if _top:
            _f["top"] = _top
        PACKS[_iid] = (f"{_label} {'All' if not _top else f'Top {_top}'} {_g}", _f)
        SEASON_TIER_PACKS.append(_iid)
# Hinh goi ("icon") khong co trong cache cua may nay -> dung hinh rieng <itemid>.png
# (da tao san trong _cache/.../externalAssets/shop), de moi goi co hinh rieng.
for _iid, (_name, _f) in PACKS.items():
    if "icon" in _f and not os.path.isfile(os.path.join(ASSETS, "externalAssets", "shop", f"{_f['icon']}.png")):
        _f["icon"] = _iid
PACK_BUNCHES = (1, 10, 30, 50, 100, 200)  # so luong mua 1 lan (goi the + vat pham dung)
_PACK_POOL = {}


@functools.lru_cache(maxsize=None)
def _pack_rows():
    """[(spid, POSOVR, preferredposition1, leagueid)] cua moi the theo thu tu spid (nhu quet bang)."""
    with closing(_db()) as c:
        return tuple(tuple(r) for r in c.execute(
            f"SELECT p.spid, {POSOVR}, p.preferredposition1, p.leagueid FROM players p ORDER BY p.spid"))


def pack_pool(itemid):
    """[(spid, weight)] of the Market cards a pack can give (cached)."""
    if itemid not in _PACK_POOL and PACKS[itemid][1].get("custom"):
        f = PACKS[itemid][1]  # goi tu chon: moi the co co hoi nhu nhau
        _PACK_POOL[itemid] = [(s, 1.0) for s in custom_pack_cards(f["spids"], f["seasons"])]
    if itemid not in _PACK_POOL:
        f = PACKS[itemid][1]
        lo, hi = POSITIONS.get(f.get("pos"), (0, 27))
        seasons = f.get("seasons") or ((f["season"],) if "season" in f else None)
        # "65+" = the rating the card shows once opened (POSOVR base + its grade's boost, as on fifaaddict)
        # "all" packs take every Market card; the others only cards licensed for the ID region
        # (permid), which leaves e.g. 37 of the 395 '06 cards
        # = SELECT p.spid, POSOVR AS ovr FROM players p WHERE ovr >= ? AND preferredposition1 BETWEEN lo AND hi
        # AND (league IS NULL OR leagueid = league) (mo khoa: moi the, khong loc permid), loc tren bang nap
        # san 1 lan (truoc day ~90 ms/goi x 96 goi = 8-9 s luc mo Cua hang lan dau)
        omin, league = f.get("ovr", 0) - GRADE_BOOST[1], f.get("league")
        rows = [(s, o) for s, o, pos, lg in _pack_rows()
                if o is not None and o >= omin and pos is not None and lo <= pos <= hi
                and (league is None or lg == league)]
        # spid < 1.000.000 = the goc khong co mua (game hien huy hieu '17): bo khoi goi, neu khong cac
        # goi "moi mua" (Top 300, Best 30, All Season...) chi ra toan the nay -> nguoi choi thay toan '17
        rows = sorted(((s, o) for s, o in rows if on_market(s) and s >= 1000000
                       and (not seasons or s // 1000000 in seasons)),
                      key=lambda r: -r[1])[:f.get("top")]
        # a price far below the pool's (odd fifaaddict data, e.g. a 120k World Best card among 20M+
        # ones) would win almost every draw: weigh anything under a tenth of the median as that tenth
        prices = [card_price(s) for s, _ in rows]
        # goi khong co the nao (vd. mua chua co trong fifadb cua server): pool rong thay vi IndexError
        floor = sorted(prices)[len(prices) // 2] / 10 if prices else 0
        _PACK_POOL[itemid] = [(s, max(p, floor) ** -0.75) for (s, _), p in zip(rows, prices)]
    return _PACK_POOL[itemid]


def pack_grades(itemid):
    """[(grade, weight)]: a "+N" pack's card comes at a random grade +1..+N, lower grades more
    likely (weight N-g+1: for +5 that is 33/27/20/13/7%); other packs give +1."""
    f = PACKS[itemid][1]
    cap = card_cap()  # GM Tool: cap the toi da -> goi vat ngang gioi han ra toi da +cap
    n = min(f.get("grade", 1), cap)
    lo = min(f.get("grade_min", 1), n)  # goi tu chon: +min..+max (cap thap ra nhieu hon)
    return [(g, n - g + 1) for g in range(lo, n + 1)]


def pack_over_cap(itemid):
    """(an khoi Shop?, ghi chu ten goi): goi nam han tren gioi han -> an; vat ngang -> "(tối đa +N)"."""
    f = PACKS.get(itemid, ("", {}))[1]
    cap = card_cap()
    note = f" (tối đa +{cap})" if f.get("grade", 1) > cap else ""  # ca goi da an con trong tui do
    return f.get("grade_min", 1) > cap, note


def pack_grade(itemid):
    grades = pack_grades(itemid)
    return random.choices([g for g, _ in grades], [w for _, w in grades])[0]


@functools.lru_cache(maxsize=None)
def pack_price(itemid):
    pool, grades = pack_pool(itemid), pack_grades(itemid)
    if not pool:
        return 0
    gw = sum(w for _, w in grades)
    return sig3(sum(card_price(s, g) * w * gwt for s, w in pool for g, gwt in grades) / sum(w for _, w in pool) / gw)


def warm_packs():
    """Build every pack pool and price and the trade pools once (~20 s) so the first Shop or
    Tukar request does not stall the client."""
    card_ratings([0])  # nap bang chi so 1 lan
    tc_colors(); tc_static()  # /static/info/teamcolor, tc_bonus (dang nhap)
    for itemid in list(PACKS):
        pack_price(itemid)
    trade3_pool()


# --------------------------------------------------------------------------- #
# Goi tuy chon (GM Tool > Goi tuy chon): gamedata/custom_packs.json
# [{"id": 96000001, "name", "spids": [...], "seasons": [...], "grade_min", "grade_max", "color": 0..7}]
# Moi goi la mot muc PACKS (itemid 96000000+n) -> tu co trong Cua hang, mo nhu goi thuong.
# Icon: anh ve san externalAssets/shop/95990001..95990008.png (8 mau "GOI TU CHON").
# --------------------------------------------------------------------------- #
CUSTOM_PACKS_FILE = os.path.join(HERE, "gamedata", "custom_packs.json")
CUSTOM_PACK_BASE = 96_000_000
CUSTOM_ICON_BASE = 95_990_001
CUSTOM_ICON_COUNT = 8
_CUSTOM_SIG = None


def custom_packs_load():
    try:
        with open(CUSTOM_PACKS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [p for p in data if isinstance(p, dict) and isinstance(p.get("id"), int)]
    except (OSError, ValueError):
        return []


def custom_packs_save(packs):
    tmp = CUSTOM_PACKS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(packs, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CUSTOM_PACKS_FILE)
    custom_packs_sync(force=True)


def custom_packs_sync(force=False):
    """Dua goi trong custom_packs.json vao PACKS (doc lai khi file doi)."""
    global _CUSTOM_SIG
    try:
        st = os.stat(CUSTOM_PACKS_FILE); sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    if sig == _CUSTOM_SIG and not force:
        return
    _CUSTOM_SIG = sig
    for iid_ in [i for i, (_, f) in PACKS.items() if f.get("custom")]:
        PACKS.pop(iid_, None)
        _PACK_POOL.pop(iid_, None)
    for p in custom_packs_load():
        gmax = min(max(int(p.get("grade_max") or 1), 1), MAX_GRADE)
        gmin = min(max(int(p.get("grade_min") or gmax), 1), gmax)
        color = int(p.get("color") or 0) % CUSTOM_ICON_COUNT
        PACKS[p["id"]] = (str(p.get("name") or "Gói tự chọn")[:40],
                          {"custom": True, "spids": [int(s) for s in p.get("spids") or []],
                           "seasons": [int(s) for s in p.get("seasons") or []],
                           "grade_min": gmin, "grade": gmax, "icon": CUSTOM_ICON_BASE + color,
                           **custom_pack_staff_conf(p)})
        _PACK_POOL.pop(p["id"], None)
    pack_price.cache_clear()


def custom_pack_staff_conf(p):
    """HLV trong goi tu chon: {"staffs": [stfid], "staff_rate": % ra HLV moi lan mo (khi goi co ca
    cau thu), "staff_gmin"/"staff_gmax": cap sao 1..10 (2 = 1 sao, 10 = 5 sao)}."""
    try:
        staffs = list(dict.fromkeys(int(s) for s in p.get("staffs") or []))
    except (TypeError, ValueError):
        staffs = []
    if not staffs:
        return {}
    gmax = min(max(int(p.get("staff_gmax") or 10), 1), 10)
    gmin = min(max(int(p.get("staff_gmin") or gmax), 1), gmax)
    rate = min(max(int(p.get("staff_rate") or 10), 1), 100)
    return {"staffs": staffs, "staff_rate": rate, "staff_gmin": gmin, "staff_gmax": gmax}


def pack_staff_draw(itemid):
    """Mo goi tu chon co HLV: stfid neu lan nay ra HLV, None = ra cau thu nhu cu. Goi chi co HLV
    (khong cau thu) -> luon ra HLV."""
    f = PACKS[itemid][1]
    staffs = [s for s in f.get("staffs") or [] if str(s) in staff_service.CATALOG]
    if not staffs:
        return None
    if pack_pool(itemid) and random.random() * 100 >= f.get("staff_rate", 10):
        return None
    return random.choice(staffs)


def ep_pack_staff(st, slot, coach, team, it, stfid):
    """Lan mo goi ra HLV. Man mo goi cua game (TeamManagementPlayerCardScreen) chi biet hien the cau
    thu, khong co hieu ung cho HLV -> HLV vao thang CLB (Quan ly HLV), goi tru 1, tra result False kem
    reason = thong bao tieng Viet (game hien hop thoai) + resources (/coach/me co danh sach HLV moi)."""
    f = PACKS[it["itemid"]][1]
    staff_service.normalize_team(team)
    if len(team["staffs"]) >= team["maxstaff"]:
        return _json({"result": False, "reason": f"Đã đủ {team['maxstaff']} HLV, hãy xoá bớt HLV rồi mở lại gói."})
    grade = random.randint(f.get("staff_gmin", 10), f.get("staff_gmax", 10))
    staff = staff_service.make_staff(stfid, grade)
    team["staffs"].append(staff)
    it["remains"] -= 1
    if not it["remains"]:
        coach["items"].remove(it)
    _save(st)
    stars = f"{grade // 2}" + (",5" if grade % 2 else "")
    msg = (f"Chúc mừng! Bạn nhận được HLV {staff['commonname']} ({stars} sao). "
           "HLV đã vào CLB: Quản lý đội > Quản lý HLV > HLV của tôi.")
    return _json({"result": False, "reason": msg, "message": msg, "staff": staff,
                  "resources": coach_me_resources(st, slot)})


def custom_pack_cards(spids, seasons):
    """[spid] cua goi tu chon: cau thu chon tay + moi the cua cac mua da chon (the co tren cho)."""
    out = [s for s in dict.fromkeys(spids)]
    if seasons:
        sset = set(seasons)
        with closing(_db()) as c:
            rows = c.execute("SELECT spid FROM players").fetchall()
        out += [s for (s,) in rows if s >= 1000000 and s // 1000000 in sset and on_market(s)]
    have = set()
    if out:
        with closing(_db()) as c:
            for i in range(0, len(out), 900):
                chunk = out[i:i + 900]
                have |= {r[0] for r in c.execute(f"SELECT spid FROM players WHERE spid IN ({','.join('?' * len(chunk))})", chunk)}
    return [s for s in dict.fromkeys(out) if s in have]


custom_packs_sync()

SHOP_FREE = True  # Shop 0 EP chi khi GM Tool tat "Shop tinh phi" (mac dinh bat: SHOP_SETTINGS_DEFAULT)

# --------------------------------------------------------------------------- #
# Shop tinh phi (port fo3-server-indo #30): gamedata/shop_settings.json {paid, packs}. Khi bat, moi
# mon co gia bang gia tri cua no (lam tron len): vat pham chua EP = EP no cho (ky vong), goi the =
# gia tri ky vong cac the (x he so goi >= 1, + bien do thi truong dong vi goi de danh mo vao ngay
# gia cao), goi qua / goi thang = tong ben trong; mon khong chua EP (tang ti le, vien EXP, doi ten,
# doi doi...) = gia co ban x he so gia the (tab Gia EP the). Khong con duong tao EP tu hu khong:
# nang cap ton it nhat gia tri no them vao, phuc hoi tra bang EP, de nghi trao doi khong vuot gia tri
# the da dua, gia rao ban tren thi truong toi da 105%.
# --------------------------------------------------------------------------- #
SHOP_SETTINGS_FILE = os.path.join(HERE, "gamedata", "shop_settings.json")
SHOP_SETTINGS_DEFAULT = {"paid": True, "packs": 1.0}  # mac dinh tinh phi (user: "dung de gia 0 EP")
_SHOP_SET = {"mtime": None, "data": dict(SHOP_SETTINGS_DEFAULT)}
ES_EP = 1000  # 1 diem nang cap
EXP_EP = 10  # 1 diem EXP (cau thu / HLV)
SET_EP = {"COACHNAME_CHANGE": 100_000, "TEAM_CHANGE": 300_000, "PLAYER_POOL_INCREASE": 1_000_000,
          "PLAYER_GROWTH_RESET": 500_000, "LEAGUE_SIMULATION": 100_000}
OTHER_EP = 100_000  # mon khong chua EP khac (manh ghep, hop dong...)


def shop_settings():
    try:
        m = os.path.getmtime(SHOP_SETTINGS_FILE)
    except OSError:
        return dict(SHOP_SETTINGS_DEFAULT)
    if m != _SHOP_SET["mtime"]:
        try:
            with open(SHOP_SETTINGS_FILE, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        packs = d.get("packs", 1.0)
        _SHOP_SET.update(mtime=m, data={"paid": bool(d.get("paid", True)),
                                        "packs": float(packs) if isinstance(packs, (int, float)) and 1 <= packs <= 100 else 1.0})
    return dict(_SHOP_SET["data"])


def set_shop_settings(cfg):
    packs = float(cfg.get("packs", 1.0))
    if not 1 <= packs <= 100:
        raise ValueError("packs")
    new = {"paid": bool(cfg.get("paid")), "packs": packs}
    os.makedirs(os.path.dirname(SHOP_SETTINGS_FILE), exist_ok=True)
    tmp = SHOP_SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(new, f, indent=1)
    os.replace(tmp, SHOP_SETTINGS_FILE)
    shop_changed()
    return new


def shop_paid():
    return not SHOP_FREE or shop_settings()["paid"]


def nice_ceil(x):
    """Lam tron LEN toi 3 chu so co nghia (gia Shop khong bao gio thap hon gia tri)."""
    x = float(x)
    if x <= 0:
        return 0
    e = max(0, int(math.floor(math.log10(x))) - 2)
    return int(math.ceil(x / 10 ** e - 1e-9)) * 10 ** e


def _player_mult():
    try:
        import price_settings
        return float(price_settings.get()["multiplier"])
    except Exception:
        return 1.0


def _market_swing():
    try:
        cfg = market.settings()
        return 1 + float(cfg["swing"]) if cfg.get("dynamic") else 1.0
    except Exception:
        return 1.0


def shop_worth(itemid, depth=0):
    """Gia tri EP cua 1 mon trong Shop tinh phi."""
    if depth > 4:  # goi tu tao chua chinh no
        return 0
    worth = lambda i: shop_worth(i, depth + 1)
    if itemid == EP_ITEM:
        return 1
    if itemid in PACKS:
        return pack_price(itemid) * shop_settings()["packs"] * _market_swing()
    if itemid in ITEMS:
        typ, eff = ITEMS[itemid][2:4]
        if "ep" in eff:  # random.triangular(lo, hi, lo): ky vong (2lo + hi) / 3
            return (2 * eff["ep"][0] + eff["ep"][1]) / 3
        if "prizes" in eff:
            total = sum(w for w, _ in eff["prizes"])
            return sum(w * (p[1] if isinstance(p, tuple) else worth(p)) for w, p in eff["prizes"]) / total if total else 0
        if typ in ("ENHANCEMENT_BOOST", "ITEM_ENHANCEMENT"):  # = diem nang cap cho cung ti le o +9
            gain = 4.0 if eff.get("sure") else max(0.05, eff.get("mult", 1) - 1)
            return gain / (ENH25_POINT_BONUS / 100 - 1) * 100 * ES_EP * _player_mult()
        if "exp" in eff:
            return eff["exp"] * EXP_EP * _player_mult()
        return SET_EP.get(typ, OTHER_EP) * _player_mult()
    if itemid in MONTHLY:
        _, _, days, now, daily, _ = MONTHLY[itemid]
        return sum(worth(i) * n for i, n in now) + days * sum(worth(i) * n for i, n in daily)
    if itemid in PACKAGES:
        return sum(worth(i) * n for i, n in PACKAGES[itemid][2])
    return 0


def product_price(itemid, n=1):
    """EP cho 1 lan mua n mon: 0 khi Shop mien phi, khong thi gia tri lam tron len (10 goi khong giam gia)."""
    if not shop_paid():
        return 0
    try:
        return nice_ceil(shop_worth(itemid) * n)
    except Exception as e:
        print(f"shop price {itemid}: {e!r}", flush=True)
        return 0


def shop_price(value, n):
    return 0 if not shop_paid() else nice_ceil(value * n)


SHOP_CHANGED = 0  # ms cua lan shop_changed() cuoi: GET /shop/list/clear


def shop_changed():
    """Gia Shop doi tren GM Tool: game giu /shop/list trong StaticData ca phien; push /shop/list/clear
    lam no bo cache -> lan mo Shop sau hoi lai (tab da ve giu gia cu toi khi vao lai game). Cung 1
    push 2 lan thi lan sau khong tac dung -> moi lan mang thoi gian moi."""
    global SHOP_CHANGED
    SHOP_CHANGED = max(int(time.time() * 1000), SHOP_CHANGED + 1)
    try:
        messenger_push("/shop/list/clear", {"changed": SHOP_CHANGED})
    except Exception as e:
        print(f"shop push: {e!r}", flush=True)


VI_NAME = (("This Season", "Mùa này"), ("All Season", "Mọi mùa"), ("Special Season", "Mùa đặc biệt"),
           ("Team of the Season", "Đội hình mùa"), ("Player Top", "Top"), (" NEW ", " Mới "), (" max ", " tối đa "),
           ("VIP Premium", "VIP cao cấp"), ("World Cup", "World Cup"), ("Lucky EP", "EP may mắn"))


def vi_name(name):
    """Viet hoa phan chu chung trong ten goi/vat pham (giu ma mua: WB, UL, 17TH...)."""
    for a, b in VI_NAME:
        name = name.replace(a, b)
    return name


def item_def(itemid):
    d = _item_def(itemid)
    if isinstance(d, dict):
        d = dict(d)
        for k in ("name", "description"):
            if isinstance(d.get(k), str):
                d[k] = vi_name(d[k])
        if itemid in PACKS and isinstance(d.get("name"), str):
            d["name"] += pack_over_cap(itemid)[1]  # goi vat ngang gioi han cap the: "(tối đa +N)"
    return d


def _item_def(itemid):
    """Item staticdata (POST /static/cache/item, shop products, coach items)."""
    if itemid == EP_ITEM:
        return EP_ITEM_DEF
    if itemid in ITEMS:
        return use_item_def(itemid)
    if itemid in PACKAGES or itemid in MONTHLY:
        return package_def(itemid)
    name, f = PACKS[itemid]
    return {"itemid": itemid, "productid": itemid, "name": name, "category": "PLAYERPACK",
            "type": "LEAGUE" if "league" in f else "POSITION" if "pos" in f else "CUSTOM",
            "unit": "COUNT", "itemicon": f.get("icon", itemid), "currency": "ep",
            **({"bundleicon": f["icon"]} if "icon" in f else {}),  # several packs share one icon
            "bunches": [{"bunch": n, "price": product_price(itemid, n)} for n in PACK_BUNCHES],
            **({"draw": {"year": 2017}} if f.get("season") == 88 else {})}


# --------------------------------------------------------------------------- #
# Shop items besides player packs (icons = the real FO3 ID shop images in the client cache).
# Category USE (Shop > Dapat dikonsumsi; Item saya > Dapat dikonsumsi): "Gunakan" opens the
# item's screen (GeneralInventoryTileItemRenderer.useUseItem by staticdata.type), which posts
# /item/<_id>/use (ItemUtil.postItemUse):
#   EP_LOTTERY (TeamManagementEPCardScreen) {id} -> {result, ep}
#   ITEM_LOTTERY (TeamManagementRandomCardScreen) {id} -> {result, ep} | {result, item:{itemid, count}}
#   COACHNAME_CHANGE {name} -> {result} (the name check first posts /coach/reservename {name})
#   TEAM_CHANGE (nation/league/club steppers, /static/info/team/summary/<id>) {teamid} -> {result}
# PACKAGE (Shop > Paket) bundles drop their items into the inventory on purchase; MILEAGE
# (MP Shop) sells copies of some items. All free while SHOP_FREE.
# --------------------------------------------------------------------------- #
EP_ITEM = 1  # externalAssets/shop/1.png = EP coin: EP in Bag entries and package contents
EP_ITEM_DEF = {"itemid": EP_ITEM, "productid": EP_ITEM, "name": "EP", "category": "EP", "type": "EP",
               "unit": "EP", "itemicon": EP_ITEM}
ITEMS = {  # itemid: (name, icon, type, effect, description)
    # Phieu nang cap (nang cap v2.5: EnhanceControlPanel loc coach items type ENHANCEMENT_BOOST theo
    # staticdata.mingrade <= cap sau khi nang <= maxgrade; chon trong o "Dung vat pham" khi nang cap)
    80501: ("Phiếu nâng cấp Thường", 80511, "ENHANCEMENT_BOOST", {"mult": 1.5, "mingrade": 2, "maxgrade": 20},
            "Dùng khi nâng cấp (v2.5): tỉ lệ thành công x1,5. Áp dụng thẻ +1 đến +19."),
    80502: ("Phiếu nâng cấp Cao cấp", 80512, "ENHANCEMENT_BOOST", {"mult": 2.0, "mingrade": 2, "maxgrade": 20},
            "Dùng khi nâng cấp (v2.5): tỉ lệ thành công x2. Áp dụng thẻ +1 đến +19."),
    80503: ("Phiếu nâng cấp Tuyệt đối", 80513, "ENHANCEMENT_BOOST", {"sure": True, "mingrade": 2, "maxgrade": 20},
            "Dùng khi nâng cấp (v2.5): chắc chắn thành công. Áp dụng thẻ +1 đến +19."),
    # +11..+20 (cap the toi da MAX_GRADE): mingrade/maxgrade = cap SAU khi nang
    80504: ("Phiếu Huyền thoại", 80514, "ENHANCEMENT_BOOST", {"mult": 2.0, "mingrade": 11, "maxgrade": 15},
            "Dùng khi nâng cấp (v2.5): tỉ lệ thành công x2. Áp dụng thẻ +10 đến +14 (lên +11..+15)."),
    80505: ("Phiếu Siêu sao", 80515, "ENHANCEMENT_BOOST", {"mult": 2.0, "mingrade": 16, "maxgrade": 20},
            "Dùng khi nâng cấp (v2.5): tỉ lệ thành công x2. Áp dụng thẻ +15 đến +19 (lên +16..+20)."),
    80506: ("Phiếu Huyền thoại Tuyệt đối", 80516, "ENHANCEMENT_BOOST", {"sure": True, "mingrade": 11, "maxgrade": 20},
            "Dùng khi nâng cấp (v2.5): chắc chắn thành công. Áp dụng thẻ +10 đến +19 (lên +11..+20)."),
    # Nut [+] canh "Cau thu N / gioi han" (TeamManagementPlayerPoolTabPage.ShowPopupBuyPlayerPoolIncreaseItem)
    # tim trong /shop/list mot muc shopcategory "USE" co product.staticdata.items[].itemid =
    # Enums.PLAYER_POOL_INCREASE_ITEM_ID (5026); thieu -> hop thoai "Do Not Find PlayerPoolIncreaseItem".
    # Gioi han CLB da la MAX_PLAYERS nen mua / dung chi bao gioi han da toi da (khong tru EP, khong vao kho).
    5026: ("Thẻ mở rộng đội hình", 5026, "PLAYER_POOL_INCREASE", {},
           "Tăng giới hạn số cầu thủ trong CLB. Bản offline: giới hạn đã ở mức tối đa."),
    501: ("Lucky EP", 501, "EP_LOTTERY", {"ep": (10_000, 1_000_000)}, "Mở thẻ: nhận 10.000 - 1.000.000 EP."),
    50029: ("Lucky EP max 20.000.000", "ID50029", "EP_LOTTERY", {"ep": (100_000, 20_000_000)},
            "Mở thẻ: nhận 100.000 - 20.000.000 EP."),
    50028: ("Lucky EP max 50.000.000", "ID50028", "EP_LOTTERY", {"ep": (200_000, 50_000_000)},
            "Mở thẻ: nhận 200.000 - 50.000.000 EP."),
    908: ("Xổ số triệu phú EP", 908, "EP_LOTTERY", {"ep": (1_000_000, 10_000_000)}, "Xổ số EP: 1 - 10 triệu EP."),
    50019: ("EP +200.000", "ID50019", "EP_LOTTERY", {"ep": (200_000, 200_000)}, "Nhận ngay 200.000 EP."),
    50020: ("EP +500.000", "ID50020", "EP_LOTTERY", {"ep": (500_000, 500_000)}, "Nhận ngay 500.000 EP."),
    50021: ("EP +1.000.000", "ID50021", "EP_LOTTERY", {"ep": (1_000_000, 1_000_000)}, "Nhận ngay 1.000.000 EP."),
    50840: ("Túi EP", "ID50840", "EP_LOTTERY", {"ep": (500_000, 5_000_000)}, "Chứa 500.000 - 5.000.000 EP."),
    50841: ("Rương EP", "ID50841", "EP_LOTTERY", {"ep": (5_000_000, 30_000_000)}, "Chứa 5 - 30 triệu EP."),
    119082304: ("Máy Jackpot EP", 119082304, "EP_LOTTERY", {"ep": (100_000, 100_000_000)},
                "Quay Jackpot: nhận 100.000 - 100.000.000 EP."),
    # random boxes: one prize from the table, (weight, EP amount | pack itemid)
    50121: ("Hộp Bạch kim", "ID50121GP", "ITEM_LOTTERY",
            {"prizes": [(10, 8334), (5, 8194), (25, 82214007), (30, 45011), (30, ("ep", 10_000_000))]},
            "Quà ngẫu nhiên: World Best 5, World Legend Top 30, Top 300, This Season Top 10 hoặc 10 triệu EP."),
    50122: ("Hộp Vàng", "ID50122GP", "ITEM_LOTTERY",
            {"prizes": [(30, 82214180), (40, 45012), (30, ("ep", 3_000_000))]},
            "Quà ngẫu nhiên: World Best 50 2017, This Season Top 20 hoặc 3 triệu EP."),
    50123: ("Hộp Bạc", "ID50123GP", "ITEM_LOTTERY",
            {"prizes": [(25, 8223600), (25, 8223601), (25, 8223602), (25, ("ep", 1_000_000))]},
            "Quà ngẫu nhiên: gói FW/MF/DF 65+ hoặc 1 triệu EP."),
    50124: ("Hộp Đồng", "ID50124GP", "ITEM_LOTTERY",
            {"prizes": [(25, 28006), (25, 28007), (25, 28008), (25, ("ep", 300_000))]},
            "Quà ngẫu nhiên: gói FW/MF/DF 60+ hoặc 300.000 EP."),
    50125: ("Hộp Kim loại", "ID50125GP", "ITEM_LOTTERY",
            {"prizes": [(50, 82214356), (50, ("ep", 100_000))]}, "Quà ngẫu nhiên: gói 07 Top 200 hoặc 100.000 EP."),
    200130000: ("Hộp quà Bạc", 200130000, "ITEM_LOTTERY",
                {"prizes": [(40, ("ep", 200_000)), (30, 28009), (30, 82214068)]},
                "Quà ngẫu nhiên: 200.000 EP, gói GK 60+ hoặc World Best 300 2017."),
    200130001: ("Hộp quà Vàng", 200130001, "ITEM_LOTTERY",
                {"prizes": [(40, ("ep", 1_000_000)), (30, 45007), (30, 45013)]},
                "Quà ngẫu nhiên: 1 triệu EP, 06-11 Player Top 50 hoặc This Season Top 30."),
    3002: ("Đổi tên HLV", 3002, "COACHNAME_CHANGE", {}, "Đổi tên HLV của bạn."),
    3001: ("Đổi đội", 3001, "TEAM_CHANGE", {}, "Chuyển sang đội khác (logo, áo đấu, giải); giữ nguyên cầu thủ."),
}
# Player EXP capsules use the client's native player selection screen.
ITEMS.update({
    910001: ('EXP cầu thủ +1.000', 300, 'PLAYER_EXP_CAPSULE', {'exp': 1000},
             'Cộng 1.000 EXP cho một cầu thủ. Cấp tối đa: 30.'),
    910002: ('EXP cầu thủ +5.000', 300, 'PLAYER_EXP_CAPSULE', {'exp': 5000},
             'Cộng 5.000 EXP cho một cầu thủ. Cấp tối đa: 30.'),
    910003: ('EXP cầu thủ +30.000', 300, 'PLAYER_EXP_CAPSULE', {'exp': 30000},
             'Cộng 30.000 EXP cho một cầu thủ. Cấp tối đa: 30.'),
})
# Additional consumables supported by the native inventory screens.
ITEMS.update({
    910004: ('EXP cầu thủ +2.000', 300, 'PLAYER_EXP_CAPSULE', {'exp': 2000}, 'Cộng 2.000 EXP cầu thủ. Cấp tối đa: 30.'),
    910005: ('EXP cầu thủ +10.000', 300, 'PLAYER_EXP_CAPSULE', {'exp': 10000}, 'Cộng 10.000 EXP cầu thủ. Cấp tối đa: 30.'),
    910006: ('EXP cầu thủ +20.000', 300, 'PLAYER_EXP_CAPSULE', {'exp': 20000}, 'Cộng 20.000 EXP cầu thủ. Cấp tối đa: 30.'),
    911001: ('EXP HLV +1.000', 200, 'COACH_EXP_CAPSULE', {'exp': 1000}, 'Cộng 1.000 EXP HLV. Cấp tối đa: 100.'),
    911002: ('EXP HLV +5.000', 200, 'COACH_EXP_CAPSULE', {'exp': 5000}, 'Cộng 5.000 EXP HLV. Cấp tối đa: 100.'),
    911003: ('EXP HLV +30.000', 200, 'COACH_EXP_CAPSULE', {'exp': 30000}, 'Cộng 30.000 EXP HLV. Cấp tối đa: 100.'),
})
ITEMS[2001] = ('Vé giả lập', 2001, 'LEAGUE_SIMULATION', {}, 'Dùng cho một trận giả lập trong Đấu giải. Tự động dùng khi giả lập.')
PACKAGES = {  # itemid: (name, icon, [(itemid, count)], description); bought = contents to Item saya
    801: ("Gói khởi đầu", 801, [(28006, 1), (28007, 1), (28008, 1), (28009, 1), (50020, 2)],
          "Gói FW/MF/DF/GK 60+ và 2x EP +500.000."),
    50880: ("Hộp tri ân", "ID50880", [(50021, 1), (50122, 1), (501, 3)],
            "EP +1.000.000, Hộp Vàng và 3x Lucky EP."),
    50881: ("Hộp World Best", "ID50881", [(8334, 1), (82214180, 3), (82214068, 5)],
            "World Best 5, 3x World Best 50 2017, 5x World Best 300 2017."),
    50882: ("Hộp Huyền thoại 06-11", "ID50882", [(45004, 1), (45006, 2), (45007, 3)],
            "06-11 Player Top 10, 2x Top 30, 3x Top 50."),
    118012902: ("Hộp nâng cấp", 118012902, [(15031, 1), (7618, 1), (82232124, 1)],
                "'14 Top 30 +5, Europe '10 Top 30 +3, EC Top 30 +3."),
    80000001: ("VIP Premium", "vipterbaru", [(50028, 1), (50121, 1), (50021, 5)],
               "Lucky EP tối đa 50 triệu, Hộp Bạch kim, 5x EP +1.000.000."),
}
PACKAGES.update({  # Serial product steps (CHAINED below), bought in order
    70000001: ("Gói nối tiếp 1/3", 200130000, [(501, 3), (28007, 1)], "Bước 1: 3x Lucky EP và gói MF 60+."),
    70000002: ("Gói nối tiếp 2/3", 200130001, [(50122, 1), (50021, 1)], "Bước 2: Hộp Vàng và EP +1.000.000."),
    70000003: ("Gói nối tiếp 3/3", "ID50121GP", [(50121, 1), (8334, 1)], "Bước 3: Hộp Bạch kim và World Best 5."),
})
PACKAGES.update({
    912001: ('Gói EXP cầu thủ', 300, [(910001, 5), (910002, 3), (910003, 1)],
             '5x EXP cầu thủ +1.000, 3x +5.000 và 1x +30.000.'),
    912002: ('Gói EXP HLV', 200, [(911001, 5), (911002, 3), (911003, 1)],
             '5x EXP HLV +1.000, 3x +5.000 và 1x +30.000.'),
})
PACKAGES.update({
    913001: ('Vé giả lập x10', 2001, [(2001, 10)], '10 vé giả lập Đấu giải.'),
    913002: ('Vé giả lập x50', 2001, [(2001, 50)], '50 vé giả lập Đấu giải.'),
    913003: ('Vé giả lập x100', 2001, [(2001, 100)], '100 vé giả lập Đấu giải.'),
})
MP_SHOP = (501, 50123, 50122, 45013, 45006, 3002)  # MP Shop (shopcategory MILEAGE) copies
MP_SHOPID = 900_000_000  # MP Shop entries: shopid = MP_SHOPID + itemid (productid stays itemid)
# Serial product (ShopChainedPackageTabPage, ShopController.currentChainedItemCheck): one entry
# {shopcategory CHAINED, step, chained: [later steps]}; the client shows step 0 = the entry
# itself, step k = chained[k-1]; remainedperaccount 0 marks a bought step ("complete").
CHAINED = {70000001: (70000001, 70000002, 70000003)}  # first step's itemid: all steps
CHAINED_NAMES = {70000001: "Gói nối tiếp Bạc - Vàng - Bạch kim"}
# Special Bonus (ShopSpecialTabPage): GET /specifictarget {issell, targets:[{stid, endtime}]} and
# GET /shop/list/STSHOP = [entries with those stids] (ShopController keeps the targeted ones).
SPECIAL = (8335, 8194, 50841, 50881)
STSHOPID = 910_000_000
# Paket Bulanan (ShopMonthlyPackageTabPage: shopcategory MONTHLYPACK; ShopMonthlyPackageProxy):
# items now, then dailyitems every day for daily_period days; GET /monthlypack = my packages
# [{productid, product, expireat, rewardedat}], POST /monthlypack (the proxy, once a day while
# active) drops the day's items into the Bag as type "monthlypack".
MONTHLY = {  # itemid: (name, icon, days, items now, daily items, description)
    81000001: ("Gói tháng EP", "ID50840", 30, [(50021, 1)], [(EP_ITEM, 200_000)],
               "Nhận ngay EP +1.000.000, sau đó 200.000 EP mỗi ngày trong 30 ngày."),
    81000002: ("Gói tháng thẻ", 200130001, 30, [(8223601, 1)], [(28007, 1), (EP_ITEM, 50_000)],
               "Nhận ngay gói MF 65+, sau đó gói MF 60+ và 50.000 EP mỗi ngày trong 30 ngày."),
    81000003: ("Gói tháng cao cấp", "ID50841", 30, [(50121, 1)], [(45013, 1), (EP_ITEM, 500_000)],
               "Nhận ngay Hộp Bạch kim, sau đó This Season Top 30 và 500.000 EP mỗi ngày trong 30 ngày."),
}


def use_item_def(itemid):
    name, icon, typ, eff, desc = ITEMS[itemid]
    return {"itemid": itemid, "productid": itemid, "name": name, "category": "USE", "type": typ, "unit": "COUNT",
            "itemicon": icon, "bundleicon": icon, "currency": "ep", "stackable": True, "description": desc,
            **({'capsuleexp': eff['exp'], 'capsuleseason': None, 'capsulelv': None}
               if typ == 'PLAYER_EXP_CAPSULE' else {}),
            **({"mingrade": eff["mingrade"], "maxgrade": eff["maxgrade"], "season": None}
               if typ == "ENHANCEMENT_BOOST" else {}),
            # TeamManagementEPCardScreen.onQuery sizes the coin counter from count.max
            **({"count": {"min": eff["ep"][0], "max": eff["ep"][1]}} if "ep" in eff else {}),
            "bunches": [{"bunch": n, "price": product_price(itemid, n)} for n in PACK_BUNCHES]}


def package_def(itemid):
    if itemid in MONTHLY:
        name, icon, days, now, daily, desc = MONTHLY[itemid]
        contents, extra = now, {"daily_period": days, "monthly_platform": "all",
                                "dailyitems": [{"itemid": i, "count": n, "staticdata": item_def(i)} for i, n in daily]}
    else:
        name, icon, contents, desc = PACKAGES[itemid]
        extra = {}
    return {"itemid": itemid, "productid": itemid, "name": name, "category": "PACKAGE", "type": "ITEM",
            "unit": "COUNT", "itemicon": icon, "bundleicon": icon, "currency": "ep", "description": desc,
            "bunches": [{"bunch": 1, "price": product_price(itemid, 1)}], **extra,
            "items": [{"itemid": i, "count": n, "staticdata": item_def(i)} for i, n in contents]}


SHOP_NEW = (45011, 45004, 50121, 50881, 501)  # Rekomendasi: "new" order
SHOP_BEST = (82214180, 8223600, 28001, 28003, 8223601)  # Rekomendasi: Terlaris rank


def shop_entry(itemid, category="PLAYERPACK", shopid=None):
    d = item_def(itemid)
    rank = {k: t.index(itemid) + 1 for k, t in (("new", SHOP_NEW), ("best", SHOP_BEST)) if itemid in t and not shopid}
    icon = d.get("bundleicon", itemid)
    items = d["items"] if category == "PACKAGE" else [{"itemid": itemid, "count": 1, "staticdata": d}]
    return {"_id": str(shopid or itemid), "shopid": shopid or itemid, "shopcategory": category, "forceenable": True,
            "visible": True, **rank, "salestart": "2014-01-01T00:00:00.000Z", "saleend": "2035-01-01T00:00:00.000Z",
            "product": {"itemid": itemid, "staticdata": {**d, "bundleicon": icon, "items": items}}}


CHAIN_STEPS = {i: (first, k) for first, steps in CHAINED.items() for k, i in enumerate(steps)}


def chained_entry(st, first):
    """A Serial product at my progress: step = steps bought (capped at the last)."""
    steps, done = CHAINED[first], my_coach(st).get("chain", {}).get(str(first), 0)
    parts = [dict(shop_entry(i, "CHAINED"), remainedperaccount=0 if k < done else 1, buy=k < done)
             for k, i in enumerate(steps)]
    # chainedproductname: tieu de dai goi (ShopChainedListItemRenderer: chainedproductname + " (" + so buoc +
    # " mon)"); thieu -> "undefined (3 mon)" (2026-11-10)
    return {**parts[0], "step": min(done, len(steps) - 1), "chained": parts[1:], "chainedproductname": CHAINED_NAMES.get(first, "Gói nối tiếp")}


# Banner tab Nen mua: trang 1 = SOGE Store - muc rieng shopcategory "SOGELINK" (khong tab nao khac liet ke):
# Module.big da va (tools/menu_patch/patch_shoplink.py) -> bam vao goi GET /game/inweburl/soge, server mo
# https://sogestore.vn/ (browser_links), khong mo popup mua. Client chua va: mo popup goi 26L Top 200 nhu truoc.
PROMO_BANNERS = {7675: (3, "fo3off_7675"), 8335: (4, "fo3off_8335"), 8334: (5, "fo3off_8334")}
SOGE_SHOPID = 99999990


def soge_banner():
    e = shop_entry(92000300, "SOGELINK", SOGE_SHOPID)
    e.update(promotion=1, promotionbanner="fo3off_soge")
    return e


def shop_list(st):
    """GET /shop/list: every Shop tab's products (ShopController filters them by shopcategory)."""
    hidden = {i for i in PACKS if pack_over_cap(i)[0]}  # goi nam han tren cap the toi da (GM Tool) -> an
    custom = [i for i in PACKS if PACKS[i][1].get("custom") and i not in hidden]  # goi tu chon len dau tab Goi cau thu
    # Banner tab Nen mua (ShopRecommendationTabPage: promotion > 0, xep tang dan; anh
    # externalAssets/shop/banner/<promotionbanner>.png, toi da 5)
    promo = dict(PROMO_BANNERS)
    if custom:
        promo[custom[-1]] = (2, "fo3off_custom")
    def entry(i):
        e = shop_entry(i)
        if i in promo:
            e["promotion"], e["promotionbanner"] = promo[i]
        return e
    first = [i for i in SEASON_TIER_PACKS if i in PACKS and i not in custom and i not in hidden]  # goi 4 mua moi: ngay sau goi tu chon
    return ([soge_banner()] + [entry(i) for i in custom[::-1]] + [entry(i) for i in first]
            + [entry(i) for i in PACKS if i not in custom and i not in first and i not in hidden]
            + [shop_entry(i, "USE") for i in ITEMS]
            + [shop_entry(i, "PACKAGE") for i in PACKAGES if i not in CHAIN_STEPS]
            + [shop_entry(i, "MILEAGE", MP_SHOPID + i) for i in (() if shop_paid() else MP_SHOP)]
            + [shop_entry(i, "MONTHLYPACK") for i in MONTHLY]
            + [chained_entry(st, first) for first in CHAINED])


def product_category(itemid):
    return "PLAYERPACK" if itemid in PACKS else "USE" if itemid in ITEMS else "PACKAGE"


def ep_specifictarget(query, body):
    """GET /specifictarget: Special Bonus is open for me (ShopController.getSpecialShopInfo)."""
    return _json({"issell": True, "targets": [{"stid": STSHOPID + i, "endtime": "2035-01-01T00:00:00.000Z"}
                                              for i in SPECIAL]})


def ep_stshop(query, body):
    """GET /shop/list/STSHOP: the Special Bonus products."""
    return _json([dict(shop_entry(i, product_category(i), STSHOPID + i), stid=STSHOPID + i) for i in SPECIAL])


def monthly_list(st):
    """GET /monthlypack body: my active monthly packages."""
    return [{"_id": f"{i:024x}", "productid": i, "product": {"itemid": i, "staticdata": item_def(i)},
             "purchasedat": w3c(m["since"]), "expireat": w3c(m["expire"]), "rewardedat": w3c(m["rewarded"])}
            for i, m in ((int(k), v) for k, v in my_coach(st).get("monthly", {}).items()) if m["expire"] > time.time()]


def ep_monthlypack(query, body):
    """GET /monthlypack: my packages; POST: today's daily items of each (once a day) to the Bag."""
    st = _state()
    if body:  # POST (ShopMonthlyPackageProxy.requestReward)
        today = today_wib()
        for k, m in my_coach(st).get("monthly", {}).items():
            if m["expire"] > time.time() and today_wib(m["rewarded"]) != today:
                name, _, _, _, daily, _ = MONTHLY[int(k)]
                e = bag_add(st, f"{name} - quà hằng ngày", [{"itemid": i, "count": n} for i, n in daily],
                            msg=f"Quà hằng ngày: {name}.")
                e["type"] = "monthlypack"
                m["rewarded"] = time.time()
        _save(st)
        return _json({"result": True, "resources": [{"uri": "/monthlypack", "body": monthly_list(st)},
                                                    {"uri": "/bag", "body": bag_list(st)}]})
    return _json(monthly_list(st))


def inventory_add(coach, itemid, count):
    inv = coach.setdefault("items", [])
    it = next((i for i in inv if i["itemid"] == itemid), None)
    if it is None:
        it = {"_id": new_iid(), "itemid": itemid, "remains": 0}
        inv.append(it)
    it["remains"] += count
    return it


def my_coach(st):
    return coach_of(st)


# Locker Room catalog uses assets present in this client, not shop products.
LOCKER_SHOE_ASSETS = [0, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 123, 126, 127, 128, 129, 130, 131, 132, 133, 134, 135, 136, 137, 138, 139, 140, 141, 142, 143, 144, 145, 146, 147, 148, 149, 150, 151, 152, 153, 154, 155, 156, 157, 158, 159, 160, 161, 162, 163, 164, 165, 166, 167, 168, 169, 170, 171, 172, 173, 174, 175, 176, 177, 178, 179, 180, 181, 182, 183, 184, 185, 186, 187, 188, 189, 190, 191, 192, 193, 194, 195, 196, 197, 198, 199, 200, 201, 202, 203, 204, 205, 206, 207, 208, 209, 210, 211, 212, 213, 214, 215, 216, 217, 218, 219, 220, 221, 222, 223, 224, 225, 226, 227, 232, 233, 234, 235, 236, 237, 238, 240, 241, 242, 243, 244, 245, 246, 247, 248, 249, 250, 251, 252, 253, 13126, 13127, 13128, 13129, 13130, 13131, 13132, 13133, 13134, 13135, 13136, 13137, 13138, 13139, 13140, 13141, 13142, 13143, 13144, 13145, 13146, 13147, 13148, 13149, 13150, 13152, 13153, 13154, 13155, 13156, 13157, 13158, 13159, 13160, 13170, 13171, 13172, 13173, 13174, 13175, 13176, 13177, 13178, 13182, 13183, 13185, 13186, 13188, 13197, 13198, 13199, 13201, 13202, 13203, 13204, 13205, 13207, 13208, 13209, 13210, 13212, 13213, 13214, 13215, 13216, 13217, 13218, 13219, 15000, 15015, 15016, 15017, 15018, 15019, 15020, 15021, 15022, 15023, 15024, 15025, 15026, 15027, 15028, 15029, 15030, 15031, 15032, 15033, 15034, 15035, 15036, 15037, 15038, 15039, 15040, 15041, 15042, 15043, 15044, 15045, 15046, 15047, 15048, 15049, 15050, 15051, 15052, 15053, 15054, 15055, 15056, 15057, 15058, 15059, 15060, 15061, 15062, 15063, 15064, 15065, 15066, 15067, 15068, 15069, 15070, 15071, 15072, 15073, 15074, 15075, 15076, 15077, 15078, 15079, 15080, 15081, 15082, 15083, 15084, 15085, 15086, 15087, 15088, 15089, 15090, 15091, 15092, 15093, 15094, 15095, 15096, 15097, 15098, 15099, 15100, 15101, 15102, 15103, 15104, 15105, 15106, 15107, 15108, 15109, 15110, 15111, 15112, 15113, 15114, 15115, 15116, 15117, 15126, 15127, 15128, 15129, 15130, 15131, 15132, 15133, 15134, 15135, 15136, 15137, 15138, 15139, 15140, 15141, 15142, 15143, 15144, 15145, 15146, 15147, 15148, 15149, 15150, 15151, 15152, 15153, 15154, 15155, 15156, 15157, 15158, 15159, 15160, 15161, 15162, 15164, 15167, 15168, 15169, 15170, 15171, 15172, 15173, 15174, 15175, 15176, 15177, 15178, 15179, 15180, 15181, 15182, 15183, 15184, 15185, 15186, 15187, 15188, 15189, 15190, 15191, 15192, 15193, 15194, 15195, 15196, 15197, 15198, 15199, 15200, 15201, 15202, 15203, 15204, 15205, 15206, 15207, 15208, 15209, 15210, 15211, 15212, 15213, 15215, 15216, 15217, 15218, 15219, 15220, 15221, 15222, 15223, 15224, 15225, 15226, 15227, 15228, 15229, 15230, 15231, 15232, 15233, 15234, 15235, 15236, 15237, 15238, 15239, 15240, 15241, 15242, 15243, 15244, 15245, 15246, 15247, 15248, 15249, 15250, 15251, 15252, 15253, 15254, 15255, 16131, 16147, 16148]


def locker_catalog():
    rows = {}
    def add(key, kind, category, asset, label, option="", colors=None):
        rows[str(key)] = {"id": str(key), "lockerroomid": str(key), "type": kind,
                          "category": category, "asset": asset, "label": label,
                          "itemname": label, "option": option, "order": len(rows),
                          "visible": True, "slot": [], "color": list(colors or [])}
    add(1001, "TOP", "UNIFORMKIND", 0, "Áo thường")
    add(1002, "TOP", "UNIFORMKIND", 1, "Sơ vin")
    add(1003, "TOP", "UNIFORMKIND", 1, "Áo ôm", "FIT")
    add(1101, "TOP", "UNIFORMARM", 0, "Tay ngắn")
    add(1102, "TOP", "UNIFORMARM", 1, "Tay dài")
    add(1201, "TOP", "TATTOO", 0, "Mặc định")
    add(1301, "TOP", "WRIST", 0, "Không băng cổ tay")
    add(1302, "TOP", "WRIST", 6, "Băng cổ tay", colors=range(14))
    add(1401, "TOP", "GLOVE", 0, "Không găng tay", "PLAYERGLOVE")
    add(1402, "TOP", "GLOVE", 18, "Găng thủ môn", "GKGLOVE", [1])
    add(2001, "PANTS", "SOCKS", 0, "Tất mặc định")
    add(2002, "PANTS", "SOCKS", 1, "Tất dài")
    add(2003, "PANTS", "SOCKS", 2, "Tất ngắn")
    add(2101, "PANTS", "KNEE", 0, "Không băng đầu gối")
    add(2102, "PANTS", "KNEE", 10, "Bọc đầu gối", colors=range(14))
    add(2103, "PANTS", "KNEE", 12, "Băng đầu gối", colors=range(14))
    # The client opens ADIDAS initially. A neutral default row initializes the
    # grid, while actual variants stay in ETC rather than guessing their brand.
    add(99999, "SHOES", "SHOES", 0, "Mặc định", "ADIDAS")
    for order, asset in enumerate(LOCKER_SHOE_ASSETS):
        add(100000 + asset, "SHOES", "SHOES", asset,
            "Mặc định" if asset == 0 else f"Mẫu {order:03d}", "ETC")
    return rows


def ep_locker_jersey(player_id, query, body):
    """Change the shared shirt/shorts number, swapping an occupied number if needed."""
    st = _state()
    slot = _default_slot(st)
    players = st.get("teams", {}).get(str(slot), {}).get("players", [])
    player = next((p for p in players if iid(p) == str(player_id)), None)
    if player is None:
        return _json({"result": False, "reason": "INVALID_PLAYER"})
    if not body:
        return _json({"jersey": player.get("jersey", 0)})
    try:
        req = json.loads(body)
    except (ValueError, TypeError):
        return _json({"result": False, "reason": "INVALID_JERSEY"})
    desired = req.get("desired") if isinstance(req, dict) else None
    if type(desired) is not int or not 1 <= desired <= 99:
        return _json({"result": False, "reason": "INVALID_JERSEY"})
    # CLB > 99 the nen co so trung (add_player het so thi cho 99: co doi 259 the cung so 99). Truoc day
    # holder = the DAU TIEN mang so do (khac the client chon -> INVALID_GIVER) va so cu cua cau thu bi
    # trung voi the khac -> tim so trong, het so -> NO_FREE_JERSEY: the mang so trung khong doi duoc.
    holders = [p for p in players if p is not player and p.get("jersey") == desired]
    giver = req.get("giverplayerid")
    if giver is not None:
        holder = next((p for p in holders if iid(p) == str(giver)), None)
        if holder is None:
            return _json({"result": False, "reason": "INVALID_GIVER"})
    else:
        holder = holders[0] if holders else None
    if holder is not None:
        old = player.get("jersey", 0)
        if type(old) is not int or not 1 <= old <= 99:  # so cu hong: so trong dau tien, het thi 99
            used = {p.get("jersey") for p in players if p is not player and p is not holder}
            old = next((n for n in range(1, 100) if n != desired and n not in used), 99)
        holder["jersey"] = old  # doi cho (so cu co the dang trung voi the khac - van hop le)
    player["jersey"] = desired
    _save(st)
    return _json({"result": True, "jersey": desired, "resources": coach_me_resources(st, slot)})


def ep_locker_itemset(player_id, query, body):
    """Read/save one owned player's customization; an empty set restores defaults."""
    st = _state()
    slot = _default_slot(st)
    team = st.get("teams", {}).get(str(slot), {})
    player = next((p for p in team.get("players", []) if iid(p) == str(player_id)), None)
    if player is None:
        return _json({"result": False, "reason": "INVALID_PLAYER"})
    if not body:
        return _json(player.get("lr", {}))
    try:
        req = json.loads(body)
    except (ValueError, TypeError):
        return _json({"result": False, "reason": "INVALID_ITEMSET"})
    selected = req.get("itemset") if isinstance(req, dict) else None
    if not isinstance(selected, dict):
        return _json({"result": False, "reason": "INVALID_ITEMSET"})
    catalog = locker_catalog()
    normalized = {}
    categories = set()
    for key, value in selected.items():
        row = catalog.get(str(key))
        if row is None or not isinstance(value, dict) or str(value.get("id")) != str(key):
            return _json({"result": False, "reason": "INVALID_ITEMSET"})
        group = (row["category"], row["option"] if row["category"] == "GLOVE" else "")
        if group in categories:
            return _json({"result": False, "reason": "INVALID_ITEMSET"})
        categories.add(group)
        item = {"id": str(key)}
        if "pos" in value:
            if value["pos"] not in ("", "L", "R", "LR"):
                return _json({"result": False, "reason": "INVALID_ITEMSET"})
            item["pos"] = value["pos"]
        if "clr" in value:
            color = value["clr"]
            if str(color) != "-1":
                if str(color) not in {str(n) for n in row["color"]}:
                    return _json({"result": False, "reason": "INVALID_ITEMSET"})
                item["clr"] = int(color)
        normalized[str(key)] = item
    player["lr"] = normalized
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)
                  + [{"uri": f"/lockerroom/{player_id}/itemset", "body": normalized}]})


def locker_room_access(slot):
    """Offline Locker Room access, supplied on every coach refresh without a purchase."""
    now = int(time.time() * 1000)
    access_id = f"{0x801000 + int(slot):024x}"
    return {"_id": access_id, "id": access_id, "itemid": 801,
            "createat": now - 86400000, "expireat": now + 3650 * 86400000,
            "remains": 1, "count": 1, "expireable": True, "expired": False,
            "staticdata": {"itemid": 801, "name": "Phòng thay đồ (Offline)",
                           "category": "EQUIP", "type": "LOCKERROOM", "itemicon": 80800, "bundleicon": 80800,
                           "description": "Mở khoá Phòng thay đồ miễn phí, dùng vĩnh viễn."}}


def coach_items(st, slot, key="items"):
    """/coach/me items: one stack per pack ({_id, itemid, remains}); expireable marks an owned
    item (GeneralInventoryTileItemRenderer: Gunakan button instead of Beli). key="depot" = Kho chua."""
    coach = coach_of(st, slot)
    return ([locker_room_access(slot)] if key == "items" else []) + [{"_id": i["_id"], "id": i["_id"], "itemid": i["itemid"], "remains": i["remains"], "count": i["remains"],
             "expireable": True, "expired": False, "staticdata": item_def(i["itemid"])}
            for i in coach.get(key, []) if i["itemid"] in PACKS or i["itemid"] in ITEMS]


# --------------------------------------------------------------------------- #
# Kho chua (Vat pham > Kho, ItemDepotTabPage): coach["depot"] giu cac chong do nhu coach["items"].
# O trong Vat pham (goi, vat pham dung: GeneralInventoryTileItemRenderer.isMovableItem) gui
# POST /items/itemtodepot {moveitems: [id]}; o trong Kho gui POST /item/depottoitem/<_id> {}.
# Chuyen ca chong. {result: true} -> client GET /item/depot (mang coach_items) va hien
# DepotItemMoveCompletePopup; khong thi hien hop thoai voi `reason`.
# --------------------------------------------------------------------------- #
def stack_move(coach, item_id, src, dst):
    """Chuyen chong item_id tu coach[src] sang coach[dst], gop vao chong cung vat pham o do."""
    it = next((i for i in coach.get(src, []) if i["_id"] == item_id and i.get("remains", 0) > 0), None)
    if it is None:
        return False
    coach[src].remove(it)
    same = next((i for i in coach.setdefault(dst, []) if i["itemid"] == it["itemid"]), None)
    if same is None:
        coach[dst].append(it)
    else:
        same["remains"] += it["remains"]
    return True


def depot_reply(st, ok):
    if not ok:  # khong luu gi: lan chuyen bi tu choi khong thay doi gi
        return _json({"result": False, "reason": "Không tìm thấy vật phẩm."})
    _save(st)
    slot = _default_slot(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)
                  + [{"uri": "/item/depot", "body": coach_items(st, slot, "depot")}]})


def ep_item_to_depot(query, body):
    """POST /items/itemtodepot {moveitems: [_id...]}: Vat pham -> Kho (ve Phong thay do o lai)."""
    st = _state(); coach = my_coach(st)
    ids = [str(i) for i in json.loads(body or b"{}").get("moveitems") or []]
    lr = locker_room_access(_default_slot(st))["_id"]
    moved = [stack_move(coach, i, "items", "depot") for i in ids if i != lr]
    return depot_reply(st, bool(moved) and all(moved))


def ep_depot_to_item(item_id, query, body):
    """POST /item/depottoitem/<_id> {}: Kho -> Vat pham."""
    st = _state()
    return depot_reply(st, stack_move(my_coach(st), item_id, "depot", "items"))


def ep_item_depot(query, body):
    """GET /item/depot: danh sach Kho."""
    st = _state()
    return _json(coach_items(st, _default_slot(st), "depot"))


def ep_shop_purchase(query, body):
    """POST /shop/purchase {shopid, productid, bunch: price option index}: pay EP; packs and
    items go to Item saya, a package's contents too."""
    req = json.loads(body or b"{}")
    itemid = int(req.get("productid") or 0) or int(req.get("shopid") or 0) % MP_SHOPID % STSHOPID
    if not any(itemid in t for t in (PACKS, ITEMS, PACKAGES, MONTHLY)):
        return _json({"result": False, "reason": "NOT_EXIST_PRODUCT"})
    if itemid in ITEMS and ITEMS[itemid][2] == "PLAYER_POOL_INCREASE":
        # ShopController.onShopPurchaseResponseReceived: result false -> hop thoai voi reason (chu thuong hien nguyen van)
        return _json({"result": False, "reason": player_pool_full_msg()})
    if itemid in PACKS and pack_over_cap(itemid)[0]:  # goi da an (cap the toi da GM Tool) - Shop cu trong cache
        return _json({"result": False, "reason": f"Gói này tạm ngừng bán: cấp thẻ tối đa hiện tại là +{card_cap()}."})
    bunches = item_def(itemid)["bunches"]
    b = bunches[min(max(int(req.get("bunch") or 0), 0), len(bunches) - 1)]
    st = _state(); slot = _default_slot(st); coach = my_coach(st)
    if coach_me(st, slot)["money"]["ep"] < b["price"]:
        return _json({"result": False, "reason": "NOT_ENOUGH_EP"})
    add_ep(st, -b["price"])
    contents = PACKAGES[itemid][2] if itemid in PACKAGES else MONTHLY[itemid][3] if itemid in MONTHLY else [(itemid, 1)]
    for i, n in contents:
        if i == EP_ITEM:
            add_ep(st, n * b["bunch"])
        else:
            inventory_add(coach, i, n * b["bunch"])
    if itemid in MONTHLY:  # a month of daily items (renewing extends it)
        m = coach.setdefault("monthly", {}).setdefault(str(itemid), {"since": time.time(), "expire": time.time(), "rewarded": 0})
        m["expire"] = max(m["expire"], time.time()) + MONTHLY[itemid][2] * 86400
    if itemid in CHAIN_STEPS:
        first, k = CHAIN_STEPS[itemid]
        chain = coach.setdefault("chain", {})
        chain[str(first)] = max(chain.get(str(first), 0), k + 1)
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)
                  + ([{"uri": "/monthlypack", "body": monthly_list(st)}] if itemid in MONTHLY else [])})


def player_pool_full_msg():
    return f"Giới hạn cầu thủ trong CLB đã ở mức tối đa ({MAX_PLAYERS}), không cần mở rộng thêm."


def use_item(st, slot, coach, it, req):
    """/item/<_id>/use for a USE item (see ITEMS): its effect, one used up."""
    _, _, typ, eff, _ = ITEMS[it["itemid"]]
    out = {"result": True}
    consume = 1
    if typ == 'LEAGUE_SIMULATION':
        return {'result': False, 'reason': 'ITEM_USE_ERROR'}
    if typ == "PLAYER_POOL_INCREASE":
        return {"result": False, "reason": player_pool_full_msg()}
    if typ == 'ENHANCEMENT_BOOST':  # chi dung trong man Nang cap (o "Dung vat pham")
        return {'result': False, 'reason': 'Phiếu nâng cấp dùng trong màn Nâng cấp cầu thủ.'}
    if typ == 'COACH_EXP_CAPSULE':
        count = req.get('count', 1)
        if type(count) is not int or count < 1 or count > it['remains']:
            return {'result': False, 'reason': 'ITEM_USE_ERROR'}
        if coach_levelinfo(coach)['level'] >= COACH_MAX_LEVEL:
            return {'result': False, 'reason': 'ITEM_USE_ERROR'}
        # Stop at max level without spending additional unused capsules.
        consume = 0
        while consume < count and coach_levelinfo(coach)['level'] < COACH_MAX_LEVEL:
            gain_exp(coach, eff['exp'], coach_need, COACH_MAX_LEVEL)
            consume += 1
        out['exp'] = eff['exp'] * consume
    elif typ == 'PLAYER_EXP_CAPSULE':
        targetid = req.get('targetid')
        player = next((p for p in st['teams'][str(slot)]['players']
                       if isinstance(targetid, str) and iid(p) == targetid), None)
        if player is None or player_levelinfo(player)['level'] >= PLAYER_MAX_LEVEL:
            return {'result': False, 'reason': 'ITEM_USE_ERROR'}
        before, after = gain_exp(player, eff['exp'], player_need, PLAYER_MAX_LEVEL)
        out.update({'targetid': iid(player), 'before': before, 'after': after})
    elif typ == "EP_LOTTERY":  # lower amounts more likely
        lo, hi = eff["ep"]
        out["ep"] = lo if lo == hi else max(lo, round(random.triangular(lo, hi, lo), -4))
        add_ep(st, out["ep"])
    elif typ == "ITEM_LOTTERY":
        prize = random.choices([p for _, p in eff["prizes"]], [w for w, _ in eff["prizes"]])[0]
        if isinstance(prize, tuple):
            out["ep"] = prize[1]
            add_ep(st, prize[1])
        else:
            inventory_add(coach, prize, 1)
            out["item"] = {"itemid": prize, "count": 1, "staticdata": item_def(prize)}
    elif typ == "COACHNAME_CHANGE":
        name = str(req.get("name") or "").strip()
        if not 2 <= len(name) <= 12:
            return {"result": False, "reason": "COACH_CREATION_ERROR_COACH_TOO_SHORT"}
        coach["name"] = name
    elif typ == "TEAM_CHANGE":
        teamid = int(req.get("teamid") or 0)
        if not roster(teamid)["team"]["players"]:
            return {"result": False, "reason": "ITEM_USE_ERROR"}
        coach["teamid"] = teamid
        repair_league_context(st)
    it["remains"] -= consume
    if not it["remains"]:
        coach["items"].remove(it)
    return out


def ep_item_use(item_id, query, body):
    """POST /item/<_id>/use: open one pack -> {result, player: {spid, grade, price}}; USE items
    see use_item."""
    st = _state(); slot = _default_slot(st); coach = my_coach(st); team = st["teams"][str(slot)]
    it = next((i for i in coach.get("items", []) if i["_id"] == item_id and i["remains"] > 0), None)
    if it is not None and it["itemid"] in ITEMS:
        out = use_item(st, slot, coach, it, json.loads(body or b"{}"))
        if out["result"]:
            _save(st)
        return _json({**out, "resources": coach_me_resources(st, slot)})
    if it is None or it["itemid"] not in PACKS:
        return _json({"result": False, "reason": "INVALID_ITEM"})
    stfid = pack_staff_draw(it["itemid"]) if PACKS[it["itemid"]][1].get("staffs") else None
    if stfid is not None:  # goi tu chon co HLV (GM Tool): lan nay ra HLV
        return ep_pack_staff(st, slot, coach, team, it, stfid)
    if len(team["players"]) >= MAX_PLAYERS:
        return _json({"result": False, "reason": "CLB đã đầy, hãy thanh lý bớt cầu thủ."})  # client shows "squad full"
    pool = pack_pool(it["itemid"])
    if not pool:
        return _json({"result": False, "reason": "Gói này chưa có cầu thủ nào (sửa gói trên GM Tool)."})
    spid = random.choices([s for s, _ in pool], [w for _, w in pool])[0]
    g = pack_grade(it["itemid"])
    p = add_player(team, spid, g)
    it["remains"] -= 1
    if not it["remains"]:
        coach["items"].remove(it)
    _save(st)
    return _json({"result": True, "player": {"_id": p["_id"], "spid": spid, "grade": g, "price": card_price(spid, g)},
                  "resources": coach_me_resources(st, slot)})


def ep_static_item(query, body):
    """POST /static/cache/item {itemids} -> {itemid: staticdata} (ItemUtil cache)."""
    ids = []
    for i in json.loads(body or b"{}").get("itemids") or []:
        try:
            ids.append(int(i))
        except (TypeError, ValueError):
            pass
    return _json({str(i): EP_ITEM_DEF if i == EP_ITEM else item_def(i) for i in ids
                  if i == EP_ITEM or i in PACKS or i in ITEMS or i in PACKAGES})


# --------------------------------------------------------------------------- #
# Daily login reward: the first login of a day (WIB, at PUT /coach/<slot>/default) drops that
# day's reward into the Bag (Item saya > Items, ItemBagTabPage: GET /bag; "Terima" = POST
# /bag/<id>/unpack, "Terima semua" = POST /bag/unpackall {reqUnpackItems}). 7-day cycle on a
# consecutive-login streak; the table is gamedata/daily_rewards.json (admin page edits it).
# Bag entries are event packs (ItemUtil.isEventItempackData: no product, type "event",
# items [{itemid, count}] expanded via /static/cache/item, players [{spid, grade}]).
# --------------------------------------------------------------------------- #
DAILY_FILE = os.path.join(HERE, "gamedata", "daily_rewards.json")
# mac dinh: moi ngay 100 trieu EP + 100 goi VIP nhat (All Season Top 200 +8, itemid 7675)
DAILY_DEFAULT = [{"ep": 100_000_000, "pack": 7675, "count": 100} for _ in range(7)]  # day 1..7
WIB = 7 * 3600


def daily_table():
    try:
        with open(DAILY_FILE, encoding="utf-8") as f:
            return json.load(f) or DAILY_DEFAULT
    except (OSError, ValueError):
        return DAILY_DEFAULT


def today_wib(ts=None):
    return time.strftime("%Y-%m-%d", time.gmtime((time.time() if ts is None else ts) + WIB))


def reward_items(reward):
    return ([{"itemid": EP_ITEM, "count": int(reward["ep"])}] if reward.get("ep") else []) + \
           ([{"itemid": int(reward["pack"]), "count": int(reward.get("count") or 1)}] if reward.get("pack") else [])


def bag_add(st, title, items=(), players=(), msg=""):
    entry = {"_id": new_iid(), "title": title, "msg": msg, "items": list(items), "players": list(players),
             "createdat": int(time.time())}
    my_coach(st).setdefault("bag", []).append(entry)
    return entry


def daily_login(st, now=None):
    """Grant today's reward once per WIB day; returns the new bag entry or None."""
    now = time.time() if now is None else now
    d = my_coach(st).setdefault("daily", {"last": "", "streak": 0, "total": 0})
    if d["last"] == today_wib(now):
        return None
    d["streak"] = d["streak"] + 1 if d["last"] == today_wib(now - 86400) else 1
    d["total"] += 1
    d["last"] = today_wib(now)
    table, n = daily_table(), d["streak"]
    return bag_add(st, f"Quà đăng nhập - ngày {n}", reward_items(table[(n - 1) % len(table)]),
                   msg=f"Đã đăng nhập {n} ngày liên tiếp. Mai vào tiếp để nhận quà ngày {n + 1} nhé.")


def bag_list(st):
    out = []
    for e in my_coach(st).get("bag", []):
        icon = next((PACKS.get(i["itemid"], ("", {}))[1].get("icon", i["itemid"])
                     for i in e["items"] if i["itemid"] != EP_ITEM), EP_ITEM)
        # ShopItemDescription.MakeEventItemDesc tooltip: msg = text, date (W3C) = "Tanggal diterima"
        out.append({"_id": e["_id"], "id": e["_id"], "type": e.get("type", "event"), "title": e["title"],
                    "msg": e.get("msg") or "Quà từ máy chủ.", "date": w3c(e["createdat"]),
                    "items": e["items"], "players": e["players"], "bundleicon": icon})
    return out


def bag_apply(st, entry):
    """Credit a bag entry: EP, packs and items to Item saya, players to the club."""
    coach = my_coach(st); team = st["teams"][str(_default_slot(st))]
    for it in entry["items"]:
        if it["itemid"] == EP_ITEM:
            add_ep(st, it["count"])
        elif it["itemid"] in PACKS or it["itemid"] in ITEMS:
            inventory_add(coach, it["itemid"], it["count"])
    for p in entry["players"]:
        x = add_player(team, p["spid"], p.get("grade", 1))
        lv = int(p.get("level") or 1)
        if lv > 1:  # cap do do GM Tool chon khi gui vao Tui
            x["levelinfo"] = {"level": min(PLAYER_MAX_LEVEL, lv), "exp": 0}
    coach["bag"].remove(entry)


def bag_reply(st):
    return _json({"result": True, "overdueitems": [],
                  "resources": coach_me_resources(st, _default_slot(st)) + [{"uri": "/bag", "body": bag_list(st)}]})


def ep_bag_unpack(bid, query, body):
    """POST /bag/<id>/unpack: take one bag entry."""
    st = _state()
    entry = next((e for e in my_coach(st).get("bag", []) if e["_id"] == bid), None)
    if entry is None:
        return _json({"result": False, "reason": "ITEM_USE_ERROR"})
    if len(st["teams"][str(_default_slot(st))]["players"]) + len(entry["players"]) > MAX_PLAYERS:
        return _json({"result": False, "reason": "TRANSFERMARKET_FULLSLOT_DESC"})
    bag_apply(st, entry)
    _save(st)
    return bag_reply(st)


def ep_bag_unpackall(query, body):
    """POST /bag/unpackall {reqUnpackItems: [id...]}: take them all (club slots permitting)."""
    ids = set(map(str, json.loads(body or b"{}").get("reqUnpackItems") or []))
    st = _state(); team = st["teams"][str(_default_slot(st))]
    for e in [e for e in my_coach(st).get("bag", []) if e["_id"] in ids]:
        if len(team["players"]) + len(e["players"]) <= MAX_PLAYERS:
            bag_apply(st, e)
    _save(st)
    return bag_reply(st)


# Path-pattern routes (ids in the path); checked after the exact ENDPOINTS.
ROUTES = [
    (re.compile(r"/lockerroom/(\w+)/itemset"), ep_locker_itemset),
    (re.compile(r"/lockerroom/(\w+)/jersey"), ep_locker_jersey),
    (re.compile(r"/item/(\w+)/use"), ep_item_use),
    (re.compile(r"/bag/(\w+)/unpack"), ep_bag_unpack),
    (re.compile(r"/coach/(\w+)/postponed"), ep_coach_postponed),
    (re.compile(r"/static/info/authentic/team/(licensed|all)/(\d+)"), ep_authentic_team),
    # Ganti tim screen: a club's summary for its TeamInfoNew (name/crest id, ovr, teamgrade)
    (re.compile(r"/static/info/team/summary/(\d+)"), lambda t, q, b: _json(club_summary_response(int(t)))),
    (re.compile(r"/player/(\w+)/move/(firstteam|secondteam)"), ep_player_move),
    (re.compile(r"/player/(\w+)/enhance2\.5/(\w+)/preview/([01])/(-?\w+)/(-?\w+)/?"), ep_enh25_preview),
    (re.compile(r"/player/(\w+)/enhance2\.5/(\w+)/([01])/(-?\w+)/(-?\w+)/?"), ep_enh25),
    (re.compile(r"/player/(\w+)/enhance/(\w+)/preview"), ep_enhance_preview),
    (re.compile(r"/player/(\w+)/enhance/(\w+)(?:/(-?\w+)/(-?\w+))?"), ep_enhance),
    (re.compile(r"/transfer/top/50(?:/position/(\w+))?"), ep_transfer_top),
    (re.compile(r"/transfer/search/(\d+)"), ep_transfer_search),
    (re.compile(r"/transfer/purchase/(\d+)/(\d+)/(\d+)/purchase"), ep_transfer_purchase),
    (re.compile(r"/transfer/price/(\d+)/(\d+)"), ep_transfer_price),
    (re.compile(r"/transfer/my/offer/completed/(\w+)(?:/(\w+))?"), ep_offer_collect),
    (re.compile(r"/match/([0-9a-f]+)/gamesettings"), ep_match_gamesettings),
    (re.compile(r"/match/([0-9a-f]+)/side/(left|right)/squad"), ep_match_squad),
    (re.compile(r"/match/([0-9a-f]+)/player/ready"), ep_match_player_ready),
    (re.compile(r"/match/([0-9a-f]+)/side/(\w+)/ready"), ep_match_ready),
    (re.compile(r"/match/([0-9a-f]+)/end"), ep_match_end),
    (re.compile(r"/match/([0-9a-f]{24})"), ep_match_get),
    (re.compile(r"/league2/sponsor/(\d+)"), ep_l2_sponsor),
    (re.compile(r"/worldcup/info/authentic/team/(\d+)"), ep_wc_team),
    (re.compile(r"/cup/([0-9a-f]{24})/match"), ep_cup_match),
    (re.compile(r"/cup/([0-9a-f]{24})/next"), ep_cup_next),
    (re.compile(r"/cup/([0-9a-f]{24})/(?:resume|test)"), ep_cup_resume),
    (re.compile(r"/cup/([0-9a-f]{24})/leave"), ep_cup_leave),
    (re.compile(r"/cup/([0-9a-f]{24})"), lambda cupid, q, b: _json((_state().get("cups") or {}).get(cupid, {}))),
    (re.compile(r"/item/depottoitem/(\w+)"), ep_depot_to_item),
    # thong bao he thong bi bam xoa (SystemMessageCommand.deleteNotification, DELETE): khong luu
    (re.compile(r"/inbox/my/notifications/(\w+)"), lambda nid, q, b: _json({"result": True})),
    # bescript di thang vao native (BeginnersGuideManager -> ApplyCampaignScript); "{}" cho GET la
    # lam vo trinh doc script. Script khong co file responses/ -> script rong cua camera_controller.
    (re.compile(r"/static/bescript/(\w+)"), lambda name, q, b: from_file("/static/bescript/camera_controller")
        or ("application/json", json.dumps("function main() {}\n").encode())),
    # GET /game/inweburl/<ten> {url}: StartupNoticeScreen.onReceivedNoticeUrl dua url thang vao native
    # LoadWebAddress; thieu url (tra "{}") = chuoi NULL -> crash. File responses/ (vd. notice) van uu tien.
    (re.compile(r"/game/inweburl/(\w+)"), lambda name, q, b: _json({"result": True, "url": "about:blank"})),
]


def route(path):
    for pat, fn in ROUTES:
        m = pat.fullmatch(path)
        if m:
            return lambda q, b, g=m.groups(): fn(*g, q, b)


ENDPOINTS = {
    "/info/server/config": ep_server_config,
    "/info/server/locale": ep_locale,
    "/info/server/time": ep_server_time,
    "/game/my/ipaddress": ep_my_ip,
    "/publisher/garena/sign": ep_garena_sign,
    "/publisher/nexon/2ndauth": ep_nexon_2ndauth,
    "/telemetry/gameconnection": ep_telemetry,
    "/shop/cash/charge/url": ep_shop_url,
    "/shop/cash/history/url": ep_shop_url,
}
ENDPOINTS["/coaches"] = ep_coaches
ENDPOINTS["/player/enhance2.5/restore"] = ep_enh25_restore
ENDPOINTS["/coach/me"] = ep_coach_me

def ep_staff_status_expand(query, body):
    """GET /staff/status/expand (mo man Quan ly HLV): kem /coach/me de danh sach HLV cap nhat
    ngay (HLV vua them tu GM Tool hien luon, khong can thoat game)."""
    st = _state()
    return _json({"result": True, "expandable": False, "nextmaxcount": 30, "nextfee": 0,
                  "resources": coach_me_resources(st, _default_slot(st))})


ENDPOINTS["/staff/status/expand"] = ep_staff_status_expand
ENDPOINTS["/coach/me/onready"] = ep_coach_onready
for _slot in (0, 1):  # two coach slots
    ENDPOINTS[f"/coach/{_slot}"] = lambda q, b, s=_slot: ep_create_coach(s, q, b)
    ENDPOINTS[f"/coach/{_slot}/new/step/3"] = lambda q, b, s=_slot: ep_new_team(s, q, b)
    ENDPOINTS[f"/coach/{_slot}/default"] = lambda q, b, s=_slot: ep_default_coach(s, q, b)
ENDPOINTS["/account"] = ep_account
ENDPOINTS["/account/option"] = ep_account_option
ENDPOINTS["/coach/me/squad"] = ep_coach_squad
ENDPOINTS["/match/campaign"] = ep_match_campaign
ENDPOINTS["/game/my/tactic/all"] = ep_tactic_all
for _i in range(TACTIC_SLOTS):
    ENDPOINTS[f"/game/my/tactic/{_i}"] = lambda q, b, i=_i: ep_tactic(i, q, b)
RECONTRACT_MAX_LIFE = 100  # so tran hop dong toi da (life mac dinh cua moi cau thu)


def ep_recontract_preview(query, body):
    """PUT /players/recontract/preview {players:[_id]} (Doi > Lam moi HD).
    RecontractPopupUtil doc {total, players[{id, base, availablematchcount, contractfee}]} va
    costInfo.boost{igr,vip,tgp,discountlimit}; thieu players -> TypeError #1010 (Loi giao dien).
    Ban offline: gia han mien phi."""
    req = json.loads(body or b"{}")
    ids = [str(x) for x in (req.get("players") or [])]
    rows = [{"id": i, "base": 0, "availablematchcount": RECONTRACT_MAX_LIFE, "contractfee": 0} for i in ids]
    return _json({"result": True, "total": 0, "players": rows, "boostfactor": 1,
                  "boost": {"igr": 0, "vip": 0, "tgp": 0, "discountlimit": 0}})


def ep_recontract(query, body):
    """PUT /players/recontract {players:[_id]}: dat lai so tran hop dong ve toi da (mien phi)."""
    req = json.loads(body or b"{}")
    ids = {str(x) for x in (req.get("players") or [])}
    st = _state(); slot = _default_slot(st); team = st["teams"][str(slot)]
    for p in team["players"]:
        if iid(p) in ids:
            p["life"] = RECONTRACT_MAX_LIFE
    _save(st)
    return _json({"result": True, "resources": coach_me_resources(st, slot)})


ENDPOINTS.update({
    "/stadium/list": ep_stadium_list,
    "/items/itemtodepot": ep_item_to_depot, "/item/depot": ep_item_depot,
    # Thanh tich giai dau: cua toi (thanh tich cau thu) va tong hop (doi hinh tieu bieu, top chi so);
    # cup rieng cua giai (cupinfo) khong bao gio vao -> thanh tich cup de trong
    "/league2/history/players/league": ep_l2_history_players, "/league2/leader/league": ep_l2_leaders,
    "/league2/history/players/cup": lambda q, b: _json([]),
    "/league2/leader/cup": lambda q, b: _json({k: [] for k in (*LINES, "ratings", "goals", "assists", "tackles", "passes", "saves")}),
    "/players/recontract/preview": ep_recontract_preview, "/players/recontract": ep_recontract,
    "/league2/me": ep_l2_me, "/league2/reward": ep_l2_reward, "/league2/calendar": ep_l2_calendar,
    "/league2/match": ep_l2_match, "/league2/match/sim": lambda q, b: ep_l2_match(q, b, sim=True),
    "/league2/sleep": ep_l2_sleep, "/league2/end": ep_l2_end, "/league2/abandon": ep_l2_finish,
    "/league2/back": ep_telemetry, "/static/db/SORTTEAM": ep_sortteam,
    "/static/db/SORTNATION": ep_sortnation, "/static/db/SORTLEAGUE": ep_sortleague,
    "/league2/sponsor/achievement": ep_l2_achievement,
    "/cup/list": ep_cup_list, "/cup/record": ep_cup_record, "/cup/new": ep_cup_new,
    "/worldcup": ep_worldcup,
    "/worldcup/reputation/rewards": ep_wc_rep_rewards, "/worldcup/reputation/reward": ep_wc_rep_reward,
    "/worldcup/rpinfo": lambda q, b: ep_wc_rpinfo(q, b),
    "/static/db/SORTWORLDCUPTEAM": lambda q, b: _json(wc_sort_team()),
    # ShopController.onRemainedChange: body phai la mang (so luong con lai cua goi gioi han); {} -> UI error
    "/shop/list/clear": lambda q, b: _json({"changed": SHOP_CHANGED}),
    "/shop/remained/limited": lambda q, b: _json([]), "/shop/remained/limited/STSHOP": lambda q, b: _json([]),
    "/static/db/WORLDCUPTEAMS": lambda q, b: _json({str(t): {"teamid": t, "confederation": c} for t, c in WC_TEAM_CONF.items()}),
    "/transfer/offer": ep_transfer_offer, "/transfer/favorites": ep_transfer_favorites, "/transfer/my/offer/list": lambda q, b: _json(offer_list(_state())),
    "/transfer/my/boostfactor": lambda q, b: _json(NO_BOOST),
    "/players/trade/proposal": ep_trade_proposal, "/players/trade/contract": ep_trade_contract,
    "/trade3/contract": ep_trade_contract, "/trade3/proposal": ep_trade3_proposal,
    "/trade3/staticvalues": lambda q, b: _json(TRADE3_STATIC), "/trade3/info": ep_trade3_info,
    "/trade3/notification": lambda q, b: _json({"result": True}),
    "/static/db/PLAYERTRADE": lambda q, b: _json({"playergrade": {str(s): trade_grades(s) for s in range(2, 101)}}),
    "/static/db/EXPPLAYER": lambda q, b: _json(EXPPLAYER), "/static/db/EXPCOACH": lambda q, b: _json(EXPCOACH),
    "/static/db/LOCKERROOMITEM": lambda q, b: _json(locker_catalog()),
    "/static/db/STATICVALUES/max_coachlevel": lambda q, b: _json(COACH_MAX_LEVEL),
    "/static/info/teamcolor": ep_teamcolor,
    "/shop/list": lambda q, b: _json(shop_list(_state())), "/shop/list/STSHOP": ep_stshop,
    "/specifictarget": ep_specifictarget, "/monthlypack": ep_monthlypack,
    "/shop/purchase": ep_shop_purchase, "/static/cache/item": ep_static_item,
    "/bag": lambda q, b: _json(bag_list(_state())), "/bag/unpackall": ep_bag_unpackall,
    "/players/trade/proposal/resend": lambda q, b: _json({"result": False, "reason": "NOT_SUPPORTED_REQUEST"}),
})


# Che do bat buoc da voi nguoi (PvP/matchmaking): ban offline khong co may chu ghep tran.
# Tra result:false + reason = khoa ngon ngu OFFLINE_ONLINE_ONLY (LANGUAGESTRING_ENG.json) de game
# hien thong bao de hieu thay vi "Loi giao dien" hay goi LobbyManager (crash khi thieu relay).
ONLINE_ONLY_PREFIXES = ("/ladder/", "/esports", "/friendlymatch", "/matchup", "/club/ranked/", "/quickmatch",
                        "/livecenter/exhibition", "/match/quick", "/match/random", "/match/ladder", "/roleplay",
                        "/clubmatch", "/club/match", "/boxingday/pvp", "/worldcup/pvp", "/worldcup/live")


def ep_online_only(query, body):
    return _json({"result": False, "reason": "OFFLINE_ONLINE_ONLY", "message": "OFFLINE_ONLINE_ONLY"})


RESPONSES = os.path.join(HERE, "responses")

MESSENGER_PUSH = ("127.0.0.1", int(os.environ.get("FO3_MESSENGER_PUSH_PORT", "28003")))  # messenger.py PUSH_PORT
PUSH_MAX = 65000  # 1 goi UDP (<= 65507) + khung u16 cua messenger (<= 65535 sau ma hoa): lon hon thi khong day


def messenger_push(uri, body):
    """Day mot resource vao game qua ket noi tin nhan (co gang, khong dam bao): messenger.py nhan 1
    goi UDP {"cmd": "push", "uri", "body"} va gui cho moi client -> MessengerConnection.handlePacket
    "push" -> ResourceController.feedResources (nhu resources trong reply HTTP). True = da gui."""
    import socket
    data = json.dumps({"cmd": "push", "uri": uri, "body": body}, separators=(",", ":")).encode()
    if len(data) > PUSH_MAX:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(data, MESSENGER_PUSH)
        return True
    except OSError as e:
        print(f"messenger push {uri} failed: {e}", flush=True)
        return False


def push_gift(st, title, bag=False):
    """Bao cho game biet GM Tool vua gui qua: /coach/me (vat pham/cau thu/HLV moi hien ngay, neu CLB
    du nho de vua 1 goi), /bag khi qua vao Tui, va 1 thong bao he thong (category bag)."""
    slot = _default_slot(st)
    sent = messenger_push("/coach/me", coach_me(st, slot))
    if bag:
        messenger_push("/bag", bag_list(st))
    # details.message = dong chu trong khung nhac cua bieu tuong thu (SystemMessage "message"); thieu -> game
    # hien khoa "NOT_FOUND"
    messenger_push("/inbox/my/notifications/latest",
                   {**note("bag", "gift", {"title": title, "message": "Bạn có Vật Phẩm Mới"}), "stackable": True})
    return sent


NOTE_ACTIVITY = None
DYNAMIC_PATHS = set()  # duong dan module tinh nang tra loi (vd. /contact/list): bo qua file responses/
BACKGROUND = []  # ham chay nen (luong daemon) khi server khoi dong: rank.presence_loop, market.market_loop
# responses/staff_status_expand.json (ban cu, khong co resources) che mat ep_staff_status_expand -> HLV
# vua them tu GM Tool khong hien ngay khi mo Quan ly HLV
DYNAMIC_PATHS.add("/staff/status/expand")


def from_file(path):
    """responses/<path, '/' -> '_'>.json overrides the handler; re-read every request so
    response shapes can be iterated against the live client without a restart."""
    if path in DYNAMIC_PATHS:
        return None
    f = os.path.join(RESPONSES, path.strip("/").replace("/", "_") + ".json")
    if os.path.isfile(f):
        with open(f, "rb") as fh:
            return ("application/json", fh.read())


class H(http.server.BaseHTTPRequestHandler):
    def _log(self, body):
        line = (f"{time.strftime('%H:%M:%S')} {self.command} {self.path} "
                f"from {self.client_address[0]} host={self.headers.get('Host')} "
                f"len={len(body)} body={body[:2000]!r}")  # UI error stack traces run long
        print(line, flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _respond(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if body[:2] == b"\x1f\x8b":  # the client gzips larger bodies (telemetry, /coach/me/squad)
            body = gzip.decompress(body)
        self._log(body)
        path = self.path.split("?")[0]
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        # Unknown GETs get {}: a wrong shape in AS3 only raises a logged UI error, but []
        # where native code expects an object crashes the client (seen with
        # /static/db/PLAYER_EXTRA_GROWTH), and tables are id->row maps whose values get
        # iterated, so a stray "result": true becomes a bogus row. List endpoints get a
        # responses/ file with []. Unknown POSTs get {"result": true}.
        fallback = ep_telemetry if self.command == "POST" else ep_empty
        if self.command != "GET" and path.startswith(ONLINE_ONLY_PREFIXES):
            fallback = ep_online_only
        if path.startswith("/" + CDN_PREFIX):
            hit = cdn_file(path[len(CDN_PREFIX) + 1:])
            if hit is None:
                self.send_error(404)
                return
            ctype, data = hit
        else:
            try:
                with STATE_LOCK:
                    custom_packs_sync()  # goi tu chon moi tao/sua tren GM Tool
                    if path in browser_links.URLS:
                        if self.client_address[0] not in ("127.0.0.1", "::1"):
                            ctype, data = _json({"result": False})
                        else:
                            ctype, data = _json({"result": browser_links.open_link(path)})
                    else:
                        if NOTE_ACTIVITY:  # manager.py: dem thoi gian choi
                            NOTE_ACTIVITY()
                        ctype, data = from_file(path) or (ENDPOINTS.get(path) or route(path) or fallback)(query, body)
            except Exception:
                # an empty reply is a curl error and the client quits (CLIENT_CLOSE); answer
                # something parseable and keep the traceback in the log instead
                import traceback
                self._log(traceback.format_exc().encode())
                ctype, data = _json({"result": False})
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _respond
    do_POST = _respond
    do_PUT = _respond  # /game/my/ipaddress is a real PUT; 501 there made the client quit
    do_DELETE = _respond

    def log_message(self, *a):
        pass  # we do our own logging


# Offline overview stadium metadata (unknown real details are shown explicitly).
ENDPOINTS['/static/stadium/1'] = lambda q,b: _json({'id':1, 'name':'Sân vận động Offline', 'yearbuilt':'Không rõ', 'capacity':'Không rõ'})
ENDPOINTS['/stadium/1'] = ENDPOINTS['/static/stadium/1']
ENDPOINTS['/coach/new/reserve'] = ep_coach_reserve
ENDPOINTS['/coach/me/profile'] = ep_coach_profile
# Dau xep hang (ladder, PvP) khong choi duoc offline. LadderMatchFrontRecordWindow doc
# _rankedInfo.ladder_total; tra {} rong -> _rankedInfo = null -> TypeError #1009. Tra ban ghi
# "chua co du lieu" (nodata) de cua so thanh tich hien trong thay vi bao loi.
ENDPOINTS['/ladder/my/info'] = lambda q, b: _json({"ladder_total": {
    "nodata": True, "highelo": 1001, "hightype": 0, "playedgames": 0, "totalwins": 0, "midrewardable": False}})
ENDPOINTS['/game/my/team/captain'] = lambda q,b: ep_team_role('captain', q, b)
ROUTES.insert(0, (re.compile(r'/game/my/team/kickers/(\d+)'), ep_team_role))


def ep_sort_sub(table):
    """StaticData.fetch("SORTLEAGUE", nationid) -> GET /static/db/SORTLEAGUE/<nationid> (cung vay
    SORTTEAM/<leagueid>): 1 dong cua bang = MANG id. RoomProxy (bo chon doi AI, chay ca luc khoi
    tao RoomController) lam `data as Array` roi .map -> {} (mac dinh) = null -> TypeError #1009,
    RoomProxy hong (phong Rank lan sau khong nhan push, nut Huy khong gui gi)."""
    def ep(key, query, body):
        return _json(json.loads(table(query, body)[1]).get(str(int(key)), []))
    return ep


ROUTES.insert(0, (re.compile(r'/static/db/SORTLEAGUE/(\d+)/?'), ep_sort_sub(ep_sortleague)))
ROUTES.insert(0, (re.compile(r'/static/db/SORTTEAM/(\d+)/?'), ep_sort_sub(ep_sortteam)))
ENDPOINTS['/shop/products/2001'] = lambda q,b: _json([shop_entry(2001, 'USE')])
import browser_links
import staff_service
staff_service.install(sys.modules[__name__])
import seasoncare, trade2, craft, growth  # tinh nang an: nang cap bang vat pham, trao doi v2, ghep manh, dao tao
for _feature in (seasoncare, trade2, craft, growth):
    _feature.install(sys.modules[__name__])
import rank, market, draftpack  # Xep hang voi HLV ao, thi truong song, goi chon cau thu (port fo3-indo-server dot 2)
for _feature in (rank, market, draftpack):
    _feature.install(sys.modules[__name__])
import manager  # Info Manager, xep hang HLV, nhiem vu / thanh tich / Quest (port fo3-server-indo #33)
manager.install(sys.modules[__name__])
import levelreward, multiplay  # thuong len cap HLV; Giao huu / KOTH / Exhibition voi HLV ao (port indo #35 #36)
for _feature in (levelreward, multiplay):
    _feature.install(sys.modules[__name__])
import events, liveboost, chat  # thuong thoi gian choi / thong bao qua, Live Boost (TOTW), chat kenh (port indo a0b49dd)
for _feature in (events, liveboost, chat):
    _feature.install(sys.modules[__name__])
import i18n  # da ngon ngu vi / en / th (GM Tool chon): dich JSON tra ve cho game, locale theo ngon ngu (2026-11-10)
i18n.install(sys.modules[__name__])


if __name__ == "__main__":
    # Windows SO_REUSEADDR lets a second copy bind the same port and split the client's
    # requests between two servers; refuse instead (a second play.bat then just fails)
    http.server.ThreadingHTTPServer.allow_reuse_address = os.name != "nt"

    class QuietServer(http.server.ThreadingHTTPServer):
        # Launcher kiem tra cong (mo ket noi TLS roi dong ngay) va game thoat giua chung
        # -> SSLEOFError / ConnectionReset: vo hai, khong in traceback lam roi nhat ky.
        def handle_error(self, request, client_address):
            err = sys.exc_info()[1]
            if isinstance(err, (ssl.SSLError, ConnectionError, TimeoutError)):
                return
            super().handle_error(request, client_address)
    srv = QuietServer(("0.0.0.0", PORT), H)
    if TLS:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT)
        # Lazy handshake: a failed handshake (e.g. client rejects the cert) then
        # surfaces as a logged traceback in the handler thread instead of vanishing.
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
    print(f"Đang chạy tại cổng {PORT}{' (https)' if TLS else ''}, nhật ký lưu ở: {LOG}", flush=True)
    admin_port = int(os.environ.get("FO3_ADMIN_PORT", "8088"))  # 0 = no admin page
    if admin_port:
        import admin
        admin.start(sys.modules[__name__], admin_port)
        try:  # nhac nen mp3: lenh cai dang cho (game con mo luc server tat) -> theo doi tiep
            import music
            music.start(sys.modules[__name__])
        except Exception as e:
            print("Nhạc nền:", e, flush=True)
        print(f"Trang quản trị: http://127.0.0.1:{admin_port}/", flush=True)
    threading.Thread(target=warm_packs, daemon=True, name="packs").start()
    for _fn in BACKGROUND:
        threading.Thread(target=_fn, daemon=True, name=_fn.__name__).start()
    srv.serve_forever()
