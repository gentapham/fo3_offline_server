"""Doi > Live Boost: Doi hinh tieu bieu tuan (Team of the Week) cua EA FC (port fo3-server-indo a0b49dd).

Client co san moi thu (LiveCenterLiveBoostScreen, PlayerBoostController trong Module.big) khi account co
features.livecenter.boost + mainmenu.teammanagement.liveboost:
* GET /static/db/LIVEBOOST {weekid, month, weekofmonth, expiretime, liveplayers {nhom: [{pid, boost, best11,
  position}]}}: giu den expiretime (ban cu hon bi bo, nut tat), het han thi hoi lai. Moi the cua cau thu co ten
  duoc +boost (1..10) moi chi so ngay trong client: danh sach doi hinh, chi so o Thi truong (tab "Cau thu xuat
  sac tuan nay"), ca 2 doi trong tran. Server khong cong gi them.
* Man hinh: 1 nhom (LIVEBOOST_GROUP 2900, ten LIVEBOOST_CUSTOM_2900 trong LANGUAGESTRING_ENG.json), best11 xep
  4-3-3 (1 GK, 4 DF, 3 MF, 3 FW), 7 du bi. Co du 11 cau thu (the +1 tro len) -> "Nhan thuong": POST
  /live/boost/collecting/rewards {weekid, leagueid} -> EP vao Tui do, 1 lan / tuan; GET /live/boost/info.
* Tran dau: match.boostweekid = weekid (MatchController hoi lai LIVEBOOST khi lech, truoc khi gui ready).
Nguon: API cua fut.gg (EA khong co danh sach; futbin/futwiz chan may chu bang Cloudflare): the Team of the
Week (rarity 3) cua FC hien tai, dot moi nhat (23 the ra cung luc thu Tu 18:00 gio Anh), nam gioi (FO3 khong
co nu). Doi sang id cau thu FO3: id EA khi fifadb co cung ngay sinh, else cung ngay sinh + ten giong. Tai trong
luong nen moi gio (chi khi den tuan moi), timeout ngan, luu gamedata/live_boost.json; mat mang -> giu tuan cu
(hoac khong co Live Boost). Khong gui du lieu ca nhan nao (chi GET cong khai).
"""
import calendar
import json
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.request
from contextlib import closing

hc = None
DATA_FILE = None      # gamedata/live_boost.json (cache, khong gui trong ban cap nhat)
SETTINGS_FILE = None  # gamedata/liveboost_settings.json (GM Tool)
SETTINGS_DEFAULT = {"enabled": True, "boost": 5, "reward_ep": 10_000_000}
LIVEBOOST_GROUP = 2900
LIVEBOOST_OWN = 11  # LiveCenterLiveBoostScreen.PLAYER_COUNT_FOR_REWARD
FUTGG, UA = "https://www.fut.gg/api/fut", {"User-Agent": "Mozilla/5.0"}  # User-Agent cua urllib bi tu choi
TIMEOUT = 12
MAX_PAGES = 40
WEEK = 7 * 86400
LINES = {"GK": 1, "DF": 4, "MF": 3, "FW": 3}
LINE = {**dict.fromkeys(("RB", "LB", "CB", "RWB", "LWB"), "DF"),
        **dict.fromkeys(("CDM", "CM", "CAM", "RM", "LM"), "MF"),
        **dict.fromkeys(("RW", "LW", "ST", "CF", "RF", "LF"), "FW"), "GK": "GK"}
STATUS = {"fetching": False, "error": "", "at": 0}
_LOCK = threading.Lock()


# --------------------------------------------------------------------------- cai dat / du lieu
def settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        d = d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        d = {}
    out = dict(SETTINGS_DEFAULT)
    if isinstance(d.get("enabled"), bool):
        out["enabled"] = d["enabled"]
    if type(d.get("boost")) is int and 1 <= d["boost"] <= 10:
        out["boost"] = d["boost"]
    if type(d.get("reward_ep")) is int and 0 <= d["reward_ep"] <= 10 ** 10:
        out["reward_ep"] = d["reward_ep"]
    return out


def save_settings(d):
    cur = settings()
    cur.update({k: d[k] for k in SETTINGS_DEFAULT if k in d})
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SETTINGS_FILE)
    return settings()


def stored():
    """Tuan Team of the Week server dang co (ca khi Live Boost tat), hoac None."""
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            lb = json.load(f)
        return lb if isinstance(lb, dict) and lb.get("players") else None
    except (OSError, ValueError):
        return None


def live_boost():
    """Tuan dang ap dung (None khi tat hoac chua co du lieu)."""
    return stored() if settings()["enabled"] else None


def weekid(lb=None):
    """FC 27 tuan 8 = 2708 (0 khi khong co)."""
    lb = lb or live_boost()
    return lb["game"] * 100 + lb["week"] if lb else 0


# --------------------------------------------------------------------------- fut.gg
def _get(path):
    with urllib.request.urlopen(urllib.request.Request(FUTGG + path, headers=UA), timeout=TIMEOUT) as r:
        return json.load(r)


def _utc(s):
    """"2026-11-04T18:00:44.769558Z" -> epoch s."""
    return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))


def totw_cards(now, get=_get):
    """(FC, tuan, gio ra mat, the) cua Team of the Week moi nhat: FC <nam> tu thang 9 (FC nam truoc cho den
    khi FC moi co tuan dau), 1 tuan = cac the tao trong vong 3 ngay."""
    t = time.gmtime(now)
    game = t.tm_year % 100 + (t.tm_mon >= 9)
    for g in (game, game - 1):
        cards, page = [], 1
        while page and page <= MAX_PAGES:
            d = get(f"/players/v2/{g}/?page={page}&rarity_id=3")
            cards += d.get("data") or []
            page = d.get("next")
        if cards:
            break
    else:
        raise ValueError("fut.gg không có thẻ Team of the Week")
    cards.sort(key=lambda c: c["createdAt"])
    weeks = [[cards[0]]]
    for c in cards[1:]:
        if _utc(c["createdAt"]) - _utc(weeks[-1][-1]["createdAt"]) > 3 * 86400:
            weeks.append([])
        weeks[-1].append(c)
    return g, len(weeks), _utc(weeks[-1][0]["createdAt"]), weeks[-1]


def _plain(s):
    return "".join(ch for ch in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(ch)).lower()


def _words(s):
    return {w for w in re.split(r"[^a-z0-9]+", _plain(s)) if len(w) >= 3}


def totw_pid(card, c):
    """Id cau thu FO3 cua 1 the Team of the Week, hoac None: id EA neu fifadb co cung ngay sinh; khong thi cau
    thu cung ngay sinh co ten trung (ho / ten thuong goi)."""
    b = str(card.get("birthDate") or "")
    if len(b) != 8 or not b.isdigit():
        return None
    y, m, d = int(b[:4]), int(b[4:6]), int(b[6:])
    born = f"{m}/{d}/{y}"
    ea = int(card.get("basePlayerEaId") or 0)
    row = c.execute("SELECT birthdate FROM players WHERE playerid = ? LIMIT 1", (ea,)).fetchone()
    if row and str(row[0]).split()[0] == born:
        return ea
    want = _words(card.get("commonName") or "") | _words(card.get("lastName") or "")
    if not want:
        return None
    best = None
    for pid, full, short in c.execute(
            """SELECT DISTINCT p.playerid, COALESCE(n.full_eng, n.full, ''), COALESCE(n.short_eng, n.short, '')
               FROM players p JOIN nameseng n ON n.playerid = p.playerid WHERE p.birthdate LIKE ?""", (born + " %",)):
        if want & (_words(full) | _words(short)):
            best = pid if best is None else best
    return best


def build(game, week, released, cards, now, boost):
    players, missing = [], []
    with closing(hc._db()) as c:
        for card in sorted(cards, key=lambda x: -(x.get("overall") or 0)):
            name = card.get("commonName") or f"{card.get('firstName') or ''} {card.get('lastName') or ''}".strip()
            pid = totw_pid(card, c) if card.get("gender") == 1 else None
            if pid is None or any(p["pid"] == pid for p in players):
                missing.append(name)
                continue
            pos = card.get("position") if card.get("position") in LINE else "CM"
            players.append({"pid": pid, "name": name, "overall": card.get("overall") or 0, "position": pos, "boost": boost})
    left = dict(LINES)
    for p in players:  # nguoi tot nhat moi tuyen cua 4-3-3 da chinh
        line = LINE.get(p["position"], "MF")
        p["best11"] = left[line] > 0
        left[line] -= p["best11"]
    # man hinh lap cau thu cuoi cua tuyen vao cho trong -> nguoi tot ke tiep da tuyen gan nhat (khong ai bat gon)
    order = ("FW", "MF", "DF")
    for p in players:
        line = LINE.get(p["position"], "MF")
        free = [x for x in order if left[x]]
        if not p["best11"] and line != "GK" and free:
            spot = min(free, key=lambda x: abs(order.index(x) - order.index(line)))
            p.update(best11=True, position={"FW": "ST", "MF": "CM", "DF": "CB"}[spot])
            left[spot] -= 1
    return {"game": game, "week": week, "released": released, "fetched": now, "players": players, "missing": missing}


def fetch(now=None, get=_get, force=False):
    """Lay Team of the Week moi nhat vao DATA_FILE (giu nguyen neu van la tuan do, tru force)."""
    now = time.time() if now is None else now
    game, week, released, cards = totw_cards(now, get)
    old = stored()
    boost = settings()["boost"]
    if old and (old["game"], old["week"]) == (game, week) and not force:
        return old
    lb = build(game, week, released, cards, now, boost)
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lb, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)
    print(f"live boost: FC {game} Team of the Week {week}, {len(lb['players'])} cau thu "
          f"({len(lb['missing'])} khong co trong FO3)", flush=True)
    return lb


def fetch_safe(force=False):
    """fetch() khong nem loi, 1 luong tai mot luc; ghi STATUS cho GM Tool."""
    if not _LOCK.acquire(blocking=False):
        return
    STATUS.update(fetching=True)
    try:
        fetch(force=force)
        STATUS.update(error="", at=time.time())
    except Exception as e:  # mat mang / fut.gg loi: giu tuan cu
        STATUS.update(error=f"{type(e).__name__}: {e}"[:200], at=time.time())
        print(f"live boost: {e!r}", flush=True)
    finally:
        STATUS.update(fetching=False)
        _LOCK.release()


def fetch_background(force=False):
    threading.Thread(target=fetch_safe, kwargs={"force": force}, daemon=True, name="liveboost-fetch").start()


def liveboost_loop():
    """Luong nen cua server: luc bat va moi gio khi den tuan moi (tuan cu + 7 ngay)."""
    time.sleep(20)  # de server khoi dong xong truoc
    while True:
        try:
            lb = stored()
            if settings()["enabled"] and (not lb or time.time() >= lb["released"] + WEEK):
                fetch_safe()
        except Exception as e:
            print(f"live boost loop: {e!r}", flush=True)
        time.sleep(3600)


# --------------------------------------------------------------------------- endpoints
def static_body(now=None):
    """GET /static/db/LIVEBOOST. expiretime = luc tuan sau ra (hoac 1 gio nua khi tuan moi den tre)."""
    lb, now = live_boost(), time.time() if now is None else now
    if not lb:
        return {"weekid": 0, "month": 1, "weekofmonth": 1, "expiretime": hc.w3c(now - 60), "liveplayers": {}}
    rel = time.gmtime(lb["released"])
    nxt = lb["released"] + WEEK
    boost = settings()["boost"]
    return {"weekid": weekid(lb), "month": rel.tm_mon, "weekofmonth": (rel.tm_mday - 1) // 7 + 1,
            "expiretime": hc.w3c(nxt if nxt > now + 600 else now + 3600),
            "liveplayers": {str(LIVEBOOST_GROUP): [{"pid": p["pid"], "boost": boost, "best11": p["best11"],
                                                    "position": p["position"]} for p in lb["players"]]}}


def ep_static(query, body):
    return hc._json(static_body())


def _coach(st):
    c = hc.my_coach(st)
    return None if c.get("temp") or not st.get("coaches") else c


def ep_info(query, body):
    """GET /live/boost/info: da nhan thuong tuan nay chua."""
    c = _coach(hc._state())
    return hc._json({"info": {"rewarded": bool(c) and weekid() in c.get("liveboostrewards", [])}})


def reward_ep():
    try:
        import price_settings
        mult = price_settings.get()["multiplier"]
    except Exception:
        mult = 1.0
    return int(settings()["reward_ep"] * mult)


def owned_count(st, lb):
    team = st["teams"].get(str(hc._default_slot(st))) or {"players": []}
    owned = {int(p["spid"]) % 1000000 for p in team["players"] if hc.grade_of(p) > 0}
    return len(owned & {p["pid"] for p in lb["players"]})


def ep_reward(query, body):
    """POST /live/boost/collecting/rewards {weekid, leagueid}: EP vao Tui do khi co du 11 cau thu tuan nay
    (the +1 tro len, nhu man hinh dem), 1 lan / tuan."""
    try:
        req = json.loads(body or b"{}")
    except ValueError:
        req = {}
    lb, st = live_boost(), hc._state()
    coach, week = _coach(st), weekid(lb)
    if not lb or not coach or str(req.get("weekid")) != str(week) or str(req.get("leagueid")) != str(LIVEBOOST_GROUP) \
            or owned_count(st, lb) < LIVEBOOST_OWN:
        return hc._json({"result": False, "reason": "invalid week and league data"})
    if week in coach.get("liveboostrewards", []):
        return hc._json({"result": False, "reason": "rewarded"})
    items = [{"itemid": hc.EP_ITEM, "count": reward_ep()}] if reward_ep() > 0 else []
    if items:
        hc.bag_add(st, f"Thưởng LIVE BOOST - Đội hình tiêu biểu tuần {lb['week']}", items,
                   msg=f"Bạn sở hữu {LIVEBOOST_OWN} cầu thủ của Đội hình tiêu biểu tuần {lb['week']}.")
    coach.setdefault("liveboostrewards", []).append(week)
    hc._save(st)
    return hc._json({"result": True, "items": [{**it, "staticdata": hc.item_def(it["itemid"])} for it in items]})


def install(module):
    global hc, DATA_FILE, SETTINGS_FILE
    hc = module
    hc.liveboost = sys.modules[__name__]
    DATA_FILE = os.path.join(hc.HERE, "gamedata", "live_boost.json")
    SETTINGS_FILE = os.path.join(hc.HERE, "gamedata", "liveboost_settings.json")
    eps = {"/static/db/LIVEBOOST": ep_static, "/live/boost/info": ep_info, "/live/boost/collecting/rewards": ep_reward}
    hc.ENDPOINTS.update(eps)
    hc.DYNAMIC_PATHS.update(eps)
    hc.BACKGROUND.append(liveboost_loop)

    base_account, base_new_match = hc.ENDPOINTS["/account"], hc.new_match

    def ep_account(query, body):
        ctype, data = base_account(query, body)
        if not live_boost():
            return ctype, data
        acc = json.loads(data)
        feats = acc.setdefault("features", {})
        feats.setdefault("livecenter", {})["boost"] = {}
        feats.setdefault("mainmenu", {}).setdefault("teammanagement", {})["liveboost"] = {}
        return hc._json(acc)

    def new_match(mtype, left, right, **extra):
        match = base_new_match(mtype, left, right, **extra)
        try:
            match["boostweekid"] = weekid()  # weekid cua LIVEBOOST trong client
        except Exception as e:
            print(f"live boost weekid: {e!r}", flush=True)
        return match

    hc.ENDPOINTS["/account"] = ep_account
    hc.new_match = new_match
