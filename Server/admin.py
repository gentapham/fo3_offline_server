"""Admin page for the FO3ID private server: http://127.0.0.1:8088/ (localhost only).

Started by http_config.py in its own thread (env FO3_ADMIN_PORT, default 8088, 0 = off). The
page (admin/index.html) calls the JSON API below, which reads and edits gamedata/state.json
through http_config's own helpers: EP, players (add / remove / grade / Tim inti-Cadangan),
packs, Bag gifts, the daily login reward table, scout cooldowns and the server log.

Guards: only 127.0.0.1 / localhost may connect, and every change must be a POST carrying the
X-FO3-Admin header (a page on another origin cannot send it without a CORS preflight this server
never grants). ponytail: no login; anyone on this PC can use it, add a password if that matters.
"""
import http.server
import json
import os
import re
import threading
import time
import urllib.parse
from contextlib import closing

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "admin", "index.html")
POS_NAMES = "GK SW RWB RB RCB CB LCB LB LWB RDM CDM LDM RM RCM CM LCM LM RAM CAM LAM RF CF LF RW RS ST LS LW".split()
API = {}
READ_ONLY = {"bot_settings", "enhancement_settings", "summary", "players", "search", "packs", "daily", "log", "seasons", "facets", "season_cards",
             "coach_info", "price_settings", "card", "staff_catalog", "staffs"}  # the rest must be POST + header
NO_LOCK = {"search", "facets", "seasons", "packs", "staff_catalog", "log"}
hc = None  # the http_config module, set by start() (or by tests)


class AdminError(Exception):
    pass


def api(name):
    def reg(fn):
        API[name] = fn
        return fn
    return reg


def coach(st):
    try:
        return hc.my_coach(st)
    except StopIteration:
        raise AdminError("Chưa có tài khoản HLV. Hãy vào game tạo HLV trước, rồi tải lại trang này.") from None


def team(st):
    coach(st)
    return st["teams"][str(hc._default_slot(st))]


def names(spids):
    """{spid: player name} from fifadb."""
    spids = sorted({int(s) for s in spids})
    if not spids:
        return {}
    with closing(hc._db()) as c:
        return {s: n for s, n in c.execute(
            f"""SELECT p.spid, COALESCE(n.full_eng, n.full, n.short) FROM players p JOIN nameseng n ON n.playerid = p.playerid
                WHERE p.spid IN ({','.join('?' * len(spids))})""", spids)}


_NAMEFILES = {}


def name_table(kind):
    """{id: ten} tu data/ui/localization/<kind>NAMES_ENG.json cua game (TEAM / NATION)."""
    if kind not in _NAMEFILES:
        f = os.path.join(hc.GAME, "data", "ui", "localization", f"{kind}NAMES_ENG.json")
        try:
            with open(f, "rb") as fh:
                _NAMEFILES[kind] = {int(k): str(v) for k, v in json.loads(fh.read().decode("utf-8-sig")).items()}
        except Exception:
            _NAMEFILES[kind] = {}
    return _NAMEFILES[kind]


LEAGUE_TOP = (13, 53)  # Ngoai Hang Anh, LaLiga luon dung dau danh sach giai
LEAGUE_RENAME = {13: "Ngoại Hạng Anh (Premier League)", 53: "LaLiga (Tây Ban Nha)"}


def league_name(lid):
    return LEAGUE_RENAME.get(lid) or name_table("LEAGUE").get(lid, "")


def card_extra(spids):
    """{spid: (doi bong, quoc gia, vi tri)} cho bang cau thu."""
    spids = sorted({int(s) for s in spids})
    if not spids:
        return {}
    teams, nations = name_table("TEAM"), name_table("NATION")
    with closing(hc._db()) as c:
        rows = c.execute(f"SELECT spid, teamid, nationality, preferredposition1 FROM players "
                         f"WHERE spid IN ({','.join('?' * len(spids))})", spids).fetchall()
    return {s: (teams.get(t, ""), nations.get(n, ""), POS_NAMES[pp] if pp is not None and 0 <= pp < 28 else "?")
            for s, t, n, pp in rows}


def player(st, pid):
    p = next((x for x in team(st)["players"] if hc.iid(x) == str(pid)), None)
    if p is None:
        raise AdminError("Không tìm thấy cầu thủ")
    return p


# Ten truong hien thi tieng Viet trong thong bao loi
LABEL = {"level": "Cấp độ", "grade": "Cấp thẻ", "pack": "Gói", "quantity": "Số lượng", "spid": "SPID", "n": "Số dòng"}


def as_int(v, lo, hi, what):
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise AdminError(f"{LABEL.get(what, what)} phải là số")
    if not lo <= v <= hi:
        raise AdminError(f"{LABEL.get(what, what)} phải trong khoảng {lo} đến {hi}")
    return v


@api("summary")
def summary(p):
    st = hc._state(); c = coach(st); slot = hc._default_slot(st)
    d = c.get("daily") or {"last": "", "streak": 0, "total": 0}
    return {"coach": {"name": c.get("name"), "ep": c.get("ep", hc.START_EP), "teamid": c.get("teamid")},
            "vip_until": time.strftime("%Y-%m-%d", time.gmtime(hc.VIP_UNTIL / 1000)), "igr": True,
            "players": len(team(st)["players"]), "max_players": hc.MAX_PLAYERS,
            "packs": sum(i["remains"] for i in c.get("items", [])), "bag": len(c.get("bag", [])),
            "daily": {**d, "today": hc.today_wib(), "granted_today": d.get("last") == hc.today_wib()},
            "scouts": hc.coach_scouters(st, slot),
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + hc.WIB)) + " (giờ VN)"}


@api("reset_coach")
def reset_coach(p):
    """Xoa nhan vat (HLV + CLB) de vao game tao lai tu dau. state.json cu duoc chep vao gamedata/backup."""
    if p.get("confirm") != "RESET":
        raise AdminError("Thiếu xác nhận")
    try:
        import subprocess
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq fifazf.exe", "/NH"], capture_output=True, text=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        if "fifazf.exe" in out.lower():
            raise AdminError("Game đang mở. Hãy thoát game trước rồi bấm lại.")
    except AdminError:
        raise
    except Exception:
        pass
    saved = ""
    if os.path.isfile(hc.STATE):
        bdir = os.path.join(os.path.dirname(hc.STATE), "backup")
        os.makedirs(bdir, exist_ok=True)
        saved = os.path.join(bdir, time.strftime("state_truoc_reset_%Y%m%d_%H%M%S.json"))
        import shutil
        shutil.copyfile(hc.STATE, saved)
    hc._save({"coaches": [], "defaultcoachid": "", "teams": {}})
    return {"ok": True, "backup": os.path.relpath(saved, os.path.dirname(os.path.dirname(hc.STATE))) if saved else ""}


@api("players")
def players(p):
    st = hc._state(); t = team(st)
    pos = {str(s.get("id") or s["spid"]): s["pos"] for s in t["squad"]["positions"]}
    nm = names(x["spid"] for x in t["players"])
    ex = card_extra(x["spid"] for x in t["players"])
    out = []
    for x in t["players"]:
        i, g = hc.iid(x), hc.grade_of(x)
        tm, na, ps = ex.get(int(x["spid"]), ("", "", "?"))
        out.append({"id": i, "spid": x["spid"], "name": nm.get(x["spid"], "?"), "season": x["spid"] // 1000000,
                    "team": tm, "nation": na, "pos": ps,
                    # = cach game tinh: OVR goc + cong cap the + (cap do - 1), cong vao moi chi so
                    # (PlayerListUtil.fillPlayerStaticData). Chua tinh Team Color / vi tri dang xep.
                    "ovr": hc.card_ovr(x["spid"]) + hc.GRADE_BOOST[g] + hc.player_levelinfo(x)["level"] - 1, "grade": g, "price": hc.card_price(x["spid"], g),
                    "role": "MAIN" if pos.get(i, 99) < hc.POS_SUB else "SUB" if i in pos else "RES",
                    "team2": bool(x.get("team2")), "level": hc.player_levelinfo(x)["level"]})
    return {"players": out}


# ---------------------------------------------------------------- danh sach the (tab Them cau thu)
# Toan bo ~60.000 the duoc doc 1 lan vao bo nho va luu cache ra gamedata/card_index.json
# (tu lam moi khi fifadb.sqlite thay doi), nen loc / sap xep / phan trang gan nhu tuc thi.
POS_GROUPS = {"GK": {0}, "DEF": set(range(1, 9)), "MID": set(range(9, 20)), "FWD": set(range(20, 28))}
_INDEX = None
_INDEX_LOCK = threading.Lock()


def _fold(t):
    """chu thuong, bo dau (Müller -> muller) de tim khong phan biet dau."""
    import unicodedata
    t = unicodedata.normalize("NFD", str(t or "").lower())
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn").replace("đ", "d")


def card_index():
    global _INDEX
    with _INDEX_LOCK:
        if _INDEX is not None:
            return _INDEX
        db = os.path.join(HERE, "gamedata", "fifadb.sqlite")
        cache = os.path.join(HERE, "gamedata", "card_index.json")
        st = os.stat(db)
        sig = [st.st_size, int(st.st_mtime)]
        raw = None
        try:
            with open(cache, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("sig") == sig and d.get("v") == 3:
                raw = d["rows"]
        except (OSError, ValueError, KeyError):
            pass
        if raw is None:
            with closing(hc._db()) as c:
                raw = [list(r) for r in c.execute(
                    f"""SELECT p.spid, COALESCE(n.full_eng, n.full, n.short),
                               COALESCE(n.full, '') || '|' || COALESCE(n.short, '') || '|' || COALESCE(n.full_eng, '') || '|' || COALESCE(n.short_eng, ''),
                               {hc.POSOVR}, p.preferredposition1, p.teamid, p.nationality, p.leagueid
                        FROM players p JOIN nameseng n ON n.playerid = p.playerid""")]
            for r in raw:
                r[2] = _fold(r[2])  # ten da bo dau, luu luon vao cache
            try:
                tmp = cache + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"v": 3, "sig": sig, "rows": raw}, f, ensure_ascii=False, separators=(",", ":"))
                os.replace(tmp, cache)
            except OSError:
                pass
        teams, nations = name_table("TEAM"), name_table("NATION")
        folded = {}  # moi ten doi / nuoc / giai chi bo dau 1 lan

        def ff(t):
            if t not in folded:
                folded[t] = _fold(t)
            return folded[t]
        lnames = {}
        rows = []
        for spid, name, allnames, ovr, pp, tid, nat, lid in raw:
            if lid not in lnames:
                lnames[lid] = league_name(lid)
            team_n, nat_n, lg_n = teams.get(tid, ""), nations.get(nat, ""), lnames[lid]
            rows.append({"spid": spid, "name": name, "season": spid // 1000000, "ovr": ovr or 0,
                         "pp": pp if pp is not None else -1, "pos": POS_NAMES[pp] if pp is not None and 0 <= pp < 28 else "?",
                         "team": team_n, "nation": nat_n, "league": lg_n, "_lid": lid,
                         "_n": allnames, "_t": ff(team_n), "_c": ff(nat_n), "_g": ff(lg_n)})
        rows.sort(key=lambda r: (-r["ovr"], r["name"]))
        _INDEX = rows
        return _INDEX


# ---------------------------------------------------------------- ten mua (ma mua -> nhan + ten)
_SEASONS = None


def season_table():
    """{ma: {tag, name, kind}}: nhan ngan tu FA_SEASONS (tac gia doi chieu fifaaddict), ten day du
    tu TRANSFERMARKET_SEASON_<ma> trong file ngon ngu cua game; kind -> mau nhan tren GM Tool."""
    global _SEASONS
    if _SEASONS is not None:
        return _SEASONS
    loc = {}
    try:
        f = os.path.join(hc.GAME, "data", "ui", "localization", "LANGUAGESTRING_ENG.json")
        with open(f, "rb") as fh:
            d = json.loads(fh.read().decode("utf-8-sig"))
        loc = {int(k.rsplit("_", 1)[1]): str(v) for k, v in d.items()
               if k.startswith("TRANSFERMARKET_SEASON_") and k.rsplit("_", 1)[1].isdigit() and str(v).strip(" .")}
    except Exception:
        pass
    tags = {7: "07"}
    for label, codes, _ in hc.FA_SEASONS:
        for c in codes:
            tags[c] = label
    codes = {r["season"] for r in card_index()} if _INDEX is not None else set(tags) | set(loc)
    out = {}
    for c in sorted(codes | set(tags)):
        tag = tags.get(c)
        name = loc.get(c, "")
        if tag and tag.isdigit():
            name = f"Mùa 20{tag}"  # mua thuong: ten theo nam (loc cua 88 ghi nham 2016)
        if c == 0:
            tag, name = "Gốc", "Thẻ gốc (chỉ số engine)"
        if c == 51:
            name = ""  # file ngon ngu ghi nham "Chon quoc tich" cho ma 51 (TL)
        if c == 41:
            tag, name = "16B", "Mùa 2016 (bộ thẻ phụ)"
        name = name or (f"Mùa {tag}" if tag else f"Mùa đặc biệt (mã {c})")
        tag = tag or (name if 0 < len(name) <= 6 else f"S{c}")
        low = name.lower()
        legend = {"WL", "UL", "EL", "KL02", "ML", "TL", "VL", "CL", "MUL", "MUA", "CEL", "TCO92", "KXI"}
        cup = {"06WC", "10WC", "14WC", "08E", "10U", "06U", "16EC"}
        kind = ("base" if c == 0 else "year" if tag.isdigit() else
                "legend" if tag in legend or any(w in low for w in ("legend", "huyền thoại", "icons"))
                else "cup" if tag in cup or any(w in low for w in (" wc", "europe", "copa", "ucl", "champion", "châu âu"))
                else "special")
        out[c] = {"code": c, "tag": tag, "name": name, "kind": kind}
    _SEASONS = out
    return out


@api("seasons")
def seasons(p):
    count = {}
    for r in card_index():
        count[r["season"]] = count.get(r["season"], 0) + 1
    global _SEASONS
    _SEASONS = None  # dung lai sau khi da co danh sach the
    t = season_table()
    return {"seasons": [dict(v, count=count.get(c, 0)) for c, v in sorted(t.items(), key=lambda x: (x[1]["kind"] != "year", x[0]))
                        if count.get(c)]}


def _by_league(rows, p):
    gq = _fold(p.get("league", "").strip())
    if not gq:
        return rows
    return [r for r in rows if (gq == r["_g"] if p.get("league_exact") else gq in r["_g"])]


_FACETS = {}


@api("facets")
def facets(p):
    key = (str(p.get("league") or ""), str(p.get("league_exact") or ""))
    if key not in _FACETS:
        _FACETS[key] = _facets(p)
    return _FACETS[key]


def _facets(p):
    """Danh sach giai dau / doi bong / quoc gia (kem so the) cho dropdown co o tim kiem.
    Co league=... -> doi bong + quoc gia chi lay trong giai do (loc day chuyen)."""
    all_rows = card_index()
    leagues, teams, nations, top = {}, {}, {}, {}
    for r in all_rows:
        if r["league"]:
            leagues[r["league"]] = leagues.get(r["league"], 0) + 1
            if r["_lid"] in LEAGUE_TOP:
                top[r["league"]] = LEAGUE_TOP.index(r["_lid"])
    for r in _by_league(all_rows, p):
        if r["team"]:
            teams[r["team"]] = teams.get(r["team"], 0) + 1
        if r["nation"]:
            nations[r["nation"]] = nations.get(r["nation"], 0) + 1
    srt = lambda d: [{"name": k, "count": v} for k, v in sorted(d.items(), key=lambda x: _fold(x[0]))]
    lg = [{"name": k, "count": v, "top": k in top} for k, v in
          sorted(leagues.items(), key=lambda x: (top.get(x[0], 99), -x[1]))]
    return {"leagues": lg, "teams": srt(teams), "nations": srt(nations)}


@api("search")
def search(p):
    """Loc toan bo the: q (ten), team, nation, pos (GK/DEF/MID/FWD hoac vi tri), season;
    sort (ovr name season pos team nation price), dir (asc/desc), page, size."""
    q, tq, nq = _fold(p.get("q", "").strip()), _fold(p.get("team", "").strip()), _fold(p.get("nation", "").strip())
    pq = str(p.get("pos") or "").strip().upper()
    sq = str(p.get("season") or "").strip()
    rows = _by_league(card_index(), p)
    if q:
        rows = [r for r in rows if q in r["_n"]]
    # chon tu dropdown -> loc dung ten (team_exact / nation_exact), go tay -> loc chua chuoi
    if tq:
        rows = [r for r in rows if (tq == r["_t"] if p.get("team_exact") else tq in r["_t"])]
    if nq:
        rows = [r for r in rows if (nq == r["_c"] if p.get("nation_exact") else nq in r["_c"])]
    if pq in POS_GROUPS:
        rows = [r for r in rows if r["pp"] in POS_GROUPS[pq]]
    elif pq in POS_NAMES:
        rows = [r for r in rows if r["pos"] == pq]
    if sq.isdigit():
        rows = [r for r in rows if r["season"] == int(sq)]
    key = str(p.get("sort") or "ovr")
    desc = str(p.get("dir") or "desc") != "asc"
    if key in ("name", "team", "nation"):
        rows = sorted(rows, key=lambda r: (_fold(r[key]), -r["ovr"]), reverse=desc)
    elif key == "pos":
        rows = sorted(rows, key=lambda r: (r["pp"], -r["ovr"]), reverse=desc)
    elif key == "season":
        rows = sorted(rows, key=lambda r: (r["season"], r["ovr"]), reverse=desc)
    elif not desc:  # ovr / price tang dan (danh sach goc da xep OVR giam dan)
        rows = rows[::-1]
    size = min(200, max(10, int(p.get("size") or 50)))
    total = len(rows)
    pages = max(1, -(-total // size))
    page = min(max(0, int(p.get("page") or 0)), pages - 1)
    out = []
    for r in rows[page * size:(page + 1) * size]:
        out.append({k: v for k, v in r.items() if not k.startswith("_") and k != "pp"} |
                   {"market": hc.on_market(r["spid"]), "price": hc.card_price(r["spid"])})
    return {"rows": out, "total": total, "page": page, "pages": pages, "size": size}


@api("packs")
def packs(p):
    return {"packs": [{"itemid": i, "name": n, "price": hc.pack_price(i)} for i, (n, _) in hc.PACKS.items()]}


@api("ep")
def set_ep(p):
    st = hc._state(); c = coach(st)
    if "set" in p:
        c["ep"] = as_int(p["set"], 0, hc.MAX_EP, "EP")
    else:
        hc.add_ep(st, as_int(p.get("add"), -hc.MAX_EP, hc.MAX_EP, "EP"))  # add_ep giu trong 0..MAX_EP
    hc._save(st)
    return {"ep": c["ep"]}


@api("player_add")
def player_add(p):
    st = hc._state(); t = team(st)
    spid, grade = as_int(p.get("spid"), 1, 10 ** 9, "spid"), as_int(p.get("grade", 1), 1, 10, "grade")
    if not names([spid]):
        raise AdminError("Không có SPID này trong dữ liệu")
    if len(t["players"]) >= hc.MAX_PLAYERS:
        raise AdminError(f"CLB đã đầy ({hc.MAX_PLAYERS} cầu thủ)")
    x = hc.add_player(t, spid, grade)
    hc._save(st)
    return {"id": hc.iid(x)}


@api("player_level")
def player_level(p):
    """Cap do cau thu (LV 1-23): moi cap cong them 1 vao moi chi so trong game."""
    st = hc._state()
    lv = as_int(p.get("level"), 1, hc.PLAYER_MAX_LEVEL, "level")
    player(st, p.get("id"))["levelinfo"] = {"level": lv, "exp": 0}
    hc._save(st)
    return {"ok": True}


@api("player_grade")
def player_grade(p):
    st = hc._state()
    player(st, p.get("id"))["grade"] = as_int(p.get("grade"), 0, 10, "grade")
    hc._save(st)
    return {"ok": True}


@api("player_remove")
def player_remove(p):
    st = hc._state(); t = team(st); x = player(st, p.get("id"))
    if hc.in_squad(t, x):
        raise AdminError("Cầu thủ đang trong đội hình 18 người; hãy đổi ra ở mục Đội hình trước")
    hc.remove_player(t, x)
    hc._save(st)
    return {"ok": True}


@api("player_team")
def player_team(p):
    st = hc._state(); t = team(st); x = player(st, p.get("id"))
    if p.get("team") == "second":
        if hc.in_squad(t, x):
            raise AdminError("Cầu thủ trong đội hình 18 người phải ở đội chính")
        x["team2"] = True
    else:
        x.pop("team2", None)
    hc._save(st)
    return {"ok": True}


def give_pack(st, itemid, count):
    inv = coach(st).setdefault("items", [])
    stack = next((i for i in inv if i["itemid"] == itemid), None)
    if stack is None:
        stack = {"_id": hc.new_iid(), "itemid": itemid, "remains": 0}
        inv.append(stack)
    stack["remains"] += count


@api("give_pack")
def api_give_pack(p):
    st = hc._state()
    itemid = as_int(p.get("itemid"), 1, 10 ** 10, "pack")
    if itemid not in hc.PACKS:
        raise AdminError("Gói không tồn tại")
    give_pack(st, itemid, as_int(p.get("count", 1), 1, 999, "quantity"))
    hc._save(st)
    return {"ok": True}


@api("bag_send")
def bag_send(p):
    """A gift in the Bag (Item saya > Items): EP and/or a pack and/or a player."""
    st = hc._state()
    items = hc.reward_items({"ep": as_int(p.get("ep") or 0, 0, 10 ** 12, "EP"),
                             "pack": as_int(p["pack"], 1, 10 ** 10, "pack") if p.get("pack") else None,
                             "count": as_int(p.get("count") or 1, 1, 999, "quantity")})
    if any(i["itemid"] != hc.EP_ITEM and i["itemid"] not in hc.PACKS for i in items):
        raise AdminError("Gói không tồn tại")
    players_ = []
    if p.get("spid"):
        spid = as_int(p["spid"], 1, 10 ** 9, "spid")
        if not names([spid]):
            raise AdminError("Không có SPID này trong dữ liệu")
        players_ = [{"spid": spid, "grade": as_int(p.get("grade") or 1, 1, 10, "grade")}]
    if not items and not players_:
        raise AdminError("Hãy nhập EP, gói hoặc cầu thủ")
    e = hc.bag_add(st, str(p.get("title") or "Quà Admin")[:60], items, players_, msg="Quà từ admin.")
    hc._save(st)
    return {"id": e["_id"]}


@api("daily")
def daily(p):
    st = hc._state()
    return {"table": hc.daily_table(), "status": coach(st).get("daily") or {"last": "", "streak": 0, "total": 0},
            "today": hc.today_wib(), "file": os.path.relpath(hc.DAILY_FILE, HERE)}


@api("daily_set")
def daily_set(p):
    table = []
    for i, r in enumerate(p.get("table") or []):
        ep = as_int(r.get("ep") or 0, 0, 10 ** 12, f"EP ngày {i + 1}")
        pack = as_int(r["pack"], 1, 10 ** 10, f"Gói ngày {i + 1}") if r.get("pack") else None
        if pack is not None and pack not in hc.PACKS:
            raise AdminError(f"Gói ngày {i + 1} không tồn tại")
        if not ep and pack is None:
            raise AdminError(f"Ngày {i + 1} chưa có quà")
        table.append({**({"ep": ep} if ep else {}),
                      **({"pack": pack, "count": as_int(r.get("count") or 1, 1, 99, "quantity")} if pack else {})})
    if not 1 <= len(table) <= 31:
        raise AdminError("Bảng quà phải có từ 1 đến 31 ngày")
    with open(hc.DAILY_FILE, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=1)
    return {"table": table}


@api("daily_reset")
def daily_reset(p):
    st = hc._state()
    coach(st)["daily"] = {"last": "", "streak": 0, "total": 0}
    hc._save(st)
    return {"ok": True}


@api("daily_grant")
def daily_grant(p):
    """Give the next day's reward now (as if logging in tomorrow), for testing."""
    st = hc._state()
    d = coach(st).setdefault("daily", {"last": "", "streak": 0, "total": 0})
    if d["last"] == hc.today_wib():
        d["last"] = hc.today_wib(time.time() - 86400)  # keep the streak going
    e = hc.daily_login(st)
    hc._save(st)
    return {"title": e["title"] if e else None}


@api("scouts_reset")
def scouts_reset(p):
    st = hc._state()
    coach(st)["scout_used"] = {}
    hc._save(st)
    return {"ok": True}


@api("log")
def log(p):
    n = as_int(p.get("n", 80), 1, 1000, "n")
    try:
        with open(hc.LOG, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-n:]
    except OSError:
        lines = []
    return {"lines": [ln.rstrip("\n")[:300] for ln in lines]}


# ---------------------------------------------------------------- thao tac hang loat
@api("player_bulk")
def player_bulk(p):
    """{ids: [...], action: grade|second|first|remove, grade} -> {done, skipped: [{name, reason}]}."""
    st = hc._state(); t = team(st)
    ids = [str(i) for i in (p.get("ids") or [])][:500]
    action = p.get("action")
    if action not in ("grade", "level", "second", "first", "remove"):
        raise AdminError("Thao tác không hợp lệ")
    if not ids:
        raise AdminError("Chưa chọn cầu thủ nào")
    grade = as_int(p.get("grade"), 0, 10, "grade") if action == "grade" else None
    level = as_int(p.get("level"), 1, hc.PLAYER_MAX_LEVEL, "level") if action == "level" else None
    nm = names(x["spid"] for x in t["players"])
    done, skipped = 0, []
    for i in ids:
        x = next((y for y in t["players"] if hc.iid(y) == i), None)
        if x is None:
            continue
        name = nm.get(x["spid"], str(x["spid"]))
        if action == "grade":
            x["grade"] = grade
        elif action == "level":
            x["levelinfo"] = {"level": level, "exp": 0}
        elif action == "first":
            x.pop("team2", None)
        elif hc.in_squad(t, x):
            skipped.append({"name": name, "reason": "đang trong đội hình 18 người"})
            continue
        elif action == "second":
            x["team2"] = True
        else:
            hc.remove_player(t, x)
        done += 1
    hc._save(st)
    return {"done": done, "skipped": skipped}


@api("player_add_bulk")
def player_add_bulk(p):
    """{spids: [...], grade, level} -> {done, skipped, players, max}: them nhieu the vao CLB (du bi)."""
    st = hc._state(); t = team(st)
    spids = [as_int(s, 1, 10 ** 9, "spid") for s in (p.get("spids") or [])][:1000]
    grade = as_int(p.get("grade", 1), 1, 10, "grade")
    level = as_int(p.get("level") or 1, 1, hc.PLAYER_MAX_LEVEL, "level")
    if not spids:
        raise AdminError("Chưa chọn thẻ nào")
    nm = names(spids)
    done, skipped = 0, []
    for s in spids:
        if s not in nm:
            skipped.append({"name": str(s), "reason": "không có trong dữ liệu"})
        elif len(t["players"]) >= hc.MAX_PLAYERS:
            skipped.append({"name": nm[s], "reason": f"CLB đã đầy ({hc.MAX_PLAYERS})"})
        else:
            x = hc.add_player(t, s, grade)
            if level > 1:
                x["levelinfo"] = {"level": level, "exp": 0}
            done += 1
    hc._save(st)
    return {"done": done, "skipped": skipped, "players": len(t["players"]), "max": hc.MAX_PLAYERS}


@api("season_cards")
def season_cards(p):
    """The cho tab Them hang loat: theo mua (season) va/hoac theo doi bong (team, dung ten),
    xep OVR giam dan; gia tinh theo cap the da chon. Kem so cho trong CLB de canh bao."""
    season = str(p.get("season") or "").strip()
    tq = str(p.get("team") or "").strip()
    if not season and not tq:
        raise AdminError("Hãy chọn mùa hoặc đội bóng")
    grade = as_int(p.get("grade") or 1, 1, 10, "grade")
    level = as_int(p.get("level") or 1, 1, hc.PLAYER_MAX_LEVEL, "level")
    rows = card_index()
    if season:
        code = as_int(season, 0, 999, "season")
        rows = [r for r in rows if r["season"] == code]
    if tq:
        rows = [r for r in rows if r["team"] == tq]
    boost = hc.GRADE_BOOST[grade] + level - 1
    # gia: cung OVR -> cung gia (tinh 1 lan / OVR), the co gia tu dat thi tinh rieng -> nhanh voi mua lon
    import price_settings
    ps = price_settings.get()
    factor = (hc.GRADE_PRICE[grade] if grade > 0 else ps["zero_percent"] / 100) * ps["multiplier"]
    by_ovr = {}

    def price(r):
        ov = ps["overrides"].get(str(r["spid"]))
        if ov:
            return hc.sig3(ov * factor)
        if r["ovr"] not in by_ovr:
            by_ovr[r["ovr"]] = hc.sig3(hc.stat_price(r["ovr"]) * factor)
        return by_ovr[r["ovr"]]
    out = [{"spid": r["spid"], "name": r["name"], "team": r["team"], "nation": r["nation"], "pos": r["pos"], "league": r["league"],
            "season": r["season"], "ovr": r["ovr"], "ovr_now": r["ovr"] + boost, "price": price(r)}
           for r in rows[:20000]]
    st = hc._state()
    owned = {int(x["spid"]) for x in team(st)["players"]}
    for o in out:
        o["owned"] = o["spid"] in owned
    return {"rows": out, "total": len(rows), "players": len(team(st)["players"]), "max": hc.MAX_PLAYERS}


# ---------------------------------------------------------------- thong tin HLV
@api("coach_info")
def coach_info(p):
    st = hc._state(); c = coach(st)
    li = hc.coach_levelinfo(c)
    tv = sum(hc.card_price(x["spid"], hc.grade_of(x)) for x in team(st)["players"])
    return {"name": c.get("name", ""), "level": li["level"], "exp": li["exp"], "to": li["to"],
            "max_level": hc.COACH_MAX_LEVEL, "teamvalue": tv}


@api("coach_set")
def coach_set(p):
    st = hc._state(); c = coach(st)
    name = str(p.get("name") or "").strip()
    if not 2 <= len(name) <= 16:
        raise AdminError("Tên HLV phải từ 2 đến 16 ký tự")
    level = as_int(p.get("level"), 1, hc.COACH_MAX_LEVEL, "level")
    exp = as_int(p.get("exp") or 0, 0, 10 ** 7, "exp")
    exp = 0 if level >= hc.COACH_MAX_LEVEL else min(exp, hc.coach_need(level) - 1)
    c["name"] = name
    c["levelinfo"] = {"level": level, "exp": exp}
    hc._save(st)
    return coach_info(p)


# ---------------------------------------------------------------- gia EP
@api("price_settings")
def price_settings_get(p):
    import price_settings
    d = price_settings.get()
    ov = d["overrides"]
    nm = names(int(s) for s in ov)
    return {"zero_percent": d["zero_percent"], "multiplier": d["multiplier"],
            "overrides": [{"spid": int(s), "name": nm.get(int(s), "?"), "price": v} for s, v in ov.items()]}


@api("price_settings_set")
def price_settings_set(p):
    import price_settings
    cur = price_settings.get()
    data = {"zero_percent": cur["zero_percent"], "multiplier": cur["multiplier"], "overrides": dict(cur["overrides"])}
    if "zero_percent" in p:
        data["zero_percent"] = as_int(p["zero_percent"], 0, 100, "zero")
    if "multiplier" in p:
        try:
            m = float(p["multiplier"])
        except (TypeError, ValueError):
            raise AdminError("Hệ số nhân phải là số")
        if not 0.1 <= m <= 10:
            raise AdminError("Hệ số nhân phải trong khoảng 0,1 đến 10")
        data["multiplier"] = m
    if "override_spid" in p:
        s = as_int(p["override_spid"], 1, 10 ** 9, "spid")
        if p.get("override_price") in (None, ""):
            data["overrides"].pop(str(s), None)
        else:
            if not names([s]):
                raise AdminError("Không có SPID này trong dữ liệu")
            data["overrides"][str(s)] = as_int(p["override_price"], 0, 10 ** 12, "price")
    price_settings.save(data)
    return price_settings_get(p)


# ---------------------------------------------------------------- chi tiet the
STAT_COLS = ("acceleration sprintspeed finishing shotpower longshots volleys penalties positioning shortpassing longpassing "
             "crossing vision curve freekickaccuracy dribbling ballcontrol agility balance reactions marking standingtackle "
             "slidingtackle interceptions headingaccuracy stamina strength jumping aggression gkdiving gkhandling gkkicking "
             "gkpositioning gkreflexes").split()


@api("card")
def card(p):
    
    spid = as_int(p.get("spid"), 1, 10 ** 9, "spid")
    grade = as_int(p.get("grade") or 0, 0, 10, "grade")
    level = as_int(p.get("level") or 1, 1, hc.PLAYER_MAX_LEVEL, "level")
    cols = ("spid, teamid, nationality, preferredposition1, preferredposition2, preferredposition3, preferredposition4, "
            "height, weight, preferredfoot, skillmoves, weakfootabilitytypecode, attackingworkrate, defensiveworkrate, "
            "birthdate, " + ", ".join(STAT_COLS))
    with closing(hc._db()) as c:
        c.row_factory = __import__("sqlite3").Row
        r = c.execute(f"SELECT {cols} FROM players WHERE spid = ?", (spid,)).fetchone()
    if r is None:
        raise AdminError("Không có SPID này trong dữ liệu")
    boost = hc.GRADE_BOOST[grade] + level - 1  # cap the + cap do (giong game)
    positions = [POS_NAMES[r[k]] for k in ("preferredposition1", "preferredposition2", "preferredposition3",
                                           "preferredposition4") if r[k] is not None and 0 <= r[k] < 28]
    born = None  # fifadb luu dang chu "M/D/YYYY 12:00:00 AM" -> dd/mm/yyyy
    try:
        mo, da, yr = str(r["birthdate"]).split()[0].split("/")
        born = f"{int(da):02d}/{int(mo):02d}/{int(yr)}"
    except Exception:
        pass
    wr = {0: "Trung bình", 1: "Thấp", 2: "Cao"}
    return {"spid": spid, "name": names([spid]).get(spid, "?"), "season": spid // 1000000, "grade": grade, "level": level,
            "ovr": hc.card_ovr(spid) + boost, "base_ovr": hc.card_ovr(spid), "price": hc.card_price(spid, grade),
            "team": name_table("TEAM").get(r["teamid"], ""), "nation": name_table("NATION").get(r["nationality"], ""),
            "positions": positions, "height": r["height"], "weight": r["weight"],
            "foot": "Trái" if r["preferredfoot"] == 2 else "Phải", "born": born,
            "skillmoves": (r["skillmoves"] or 0) + 1, "weakfoot": r["weakfootabilitytypecode"] or 0,
            "workrate": f'{wr.get(r["attackingworkrate"], "?")} / {wr.get(r["defensiveworkrate"], "?")}',
            "stats": {k: (r[k] or 0) + boost for k in STAT_COLS}}


@api("bot_settings")
def bot_settings_get(p):
    import bot_settings
    return {"modes": bot_settings.MODES, "values": bot_settings.get(), "max_level": bot_settings.MAX_LEVEL}


@api("bot_settings_set")
def bot_settings_set(p):
    import bot_settings
    try:
        vals = bot_settings.reset() if p.get("reset") else bot_settings.set_all(p.get("values"))
    except ValueError as e:
        raise AdminError(str(e))
    return {"values": vals}


@api("enhancement_settings")
def enhancement_settings_get(p):
    import enhancement_settings
    return {"success_percent": enhancement_settings.get_rate()}


@api("enhancement_settings_set")
def enhancement_settings_set(p):
    import enhancement_settings
    value = p.get("success_percent")
    if "success_percent" not in p:
        raise AdminError("Thiếu tỷ lệ thành công.")
    try:
        enhancement_settings.set_rate(value)
    except ValueError as e:
        raise AdminError(str(e))
    return {"success_percent": value}



# ---------------- HLV / nhan vien (staff) ----------------
STAFF_POS = {"PREMIUM": "HLV trưởng", "FRONT": "HLV kỹ thuật", "MEDICAL": "Y tế", "FW": "HLV tấn công (FW)",
             "MF": "HLV tiền vệ (MF)", "DF": "HLV phòng ngự (DF)", "GK": "HLV thủ môn (GK)"}


def _ss():
    import staff_service
    return staff_service


# ten chi so tieng Viet (nhan tieng Anh trong catalog + khoa chi so cua game)
STAT_VI = {
    "acceleration": "Tăng tốc", "aggression": "Quyết đoán", "agility": "Khéo léo", "balance": "Thăng bằng",
    "ballcontrol": "Giữ bóng", "crossing": "Tạt bóng", "dribbling": "Rê bóng", "finishing": "Dứt điểm",
    "freekickaccuracy": "Đá phạt", "gkdiving": "TM đổ người", "gkkicking": "TM phát bóng", "gkhandling": "TM bắt bóng",
    "gkpositioning": "TM chọn vị trí", "gkreflexes": "TM phản xạ", "headingaccuracy": "Đánh đầu", "interceptions": "Cắt bóng",
    "jumping": "Nhảy", "longpassing": "Chuyền dài", "longshots": "Sút xa", "marking": "Kèm người", "ovr": "Tất cả chỉ số",
    "penalties": "Penalty", "positioning": "Chọn vị trí", "reactions": "Phản ứng", "curve": "Sút xoáy",
    "shotpower": "Lực sút", "shortpassing": "Chuyền ngắn", "slidingtackle": "Xoạc bóng", "sprintspeed": "Tốc độ",
    "stamina": "Thể lực", "strength": "Sức mạnh", "Boost Mental Status": "Tinh thần", "standingtackle": "Tranh bóng", "vision": "Tầm nhìn", "volleys": "Vô lê",
}


def stat_vi(name):
    ss = _ss()
    key = ss.STAT_KEYS.get(name, name)
    return STAT_VI.get(key, name)

@api("staff_catalog")
def staff_catalog(p):
    ss = _ss()
    out = []
    for sid, info in ss.CATALOG.items():
        if info.get("material"):
            continue
        has_img = os.path.isfile(os.path.join(hc.ASSETS, "externalAssets", "staffs", sid + ".png"))
        out.append({"stfid": int(sid), "name": info["name"], "position": info["position"],
                    "pos_name": STAFF_POS.get(info["position"], info["position"]),
                    "boosts": [" / ".join(stat_vi(x) for x in g) for g in info.get("boost_groups", [])], "img": has_img})
    out.sort(key=lambda x: (list(STAFF_POS).index(x["position"]) if x["position"] in STAFF_POS else 99, x["name"]))
    return {"staff": out, "max_grade": ss.MAX_GRADE}


@api("staffs")
def staffs(p):
    ss = _ss(); st = hc._state(); t = team(st); ss.normalize_team(t)
    out = [{"id": x["_id"], "stfid": x.get("stfid"), "name": x.get("commonname"), "position": x.get("position"),
            "pos_name": STAFF_POS.get(x.get("position"), x.get("position")), "grade": x.get("grade", 1),
            "status": x.get("status"), "effect": [{"effect": stat_vi(e.get("effect")), "key": e.get("effect"), "idx": e.get("effectidx"),
                                                   "amount": e.get("amount"), "choices": _staff_choices(x, e.get("effectidx"))}
                                                  for e in x.get("effect") or []]}
           for x in t.get("staffs", [])]
    return {"staffs": out, "max": t.get("maxstaff", 30)}


@api("staff_add")
def staff_add(p):
    ss = _ss(); st = hc._state(); t = team(st); ss.normalize_team(t)
    sid = str(as_int(p.get("stfid"), 1, 10 ** 6, "stfid"))
    if sid not in ss.CATALOG or ss.CATALOG[sid].get("material"):
        raise AdminError("Không có HLV này")
    grade = as_int(p.get("grade", ss.MAX_GRADE), 1, ss.MAX_GRADE, "grade")
    n = as_int(p.get("count", 1), 1, 30, "count")
    free = t["maxstaff"] - len(t["staffs"])
    if free <= 0:
        raise AdminError(f"Đã đủ {t['maxstaff']} HLV. Hãy xoá bớt trước.")
    n = min(n, free)
    for _ in range(n):
        t["staffs"].append(ss.make_staff(int(sid), grade))
    hc._save(st)
    return {"added": n, "total": len(t["staffs"])}


@api("staff_grade")
def staff_grade(p):
    ss = _ss(); st = hc._state(); t = team(st)
    x = next((x for x in t.get("staffs", []) if x["_id"] == p.get("id")), None)
    if x is None:
        raise AdminError("Không tìm thấy HLV")
    ss.set_grade(x, as_int(p.get("grade"), 1, ss.MAX_GRADE, "grade")); x["exp"] = 0
    hc._save(st)
    return {"ok": True}



def _staff_choices(staff, idx):
    ss = _ss(); info = ss.CATALOG.get(str(staff.get("stfid"))) or {}
    groups = info.get("boost_groups") or []
    if not idx or idx > len(groups):
        return []
    keys = [ss.STAT_KEYS[l] for l in groups[idx - 1] if l in ss.STAT_KEYS]
    return [{"key": k, "name": STAT_VI.get(k, k)} for k in keys] if len(keys) > 1 else []


def _full(staff):
    ss = _ss(); ss.set_grade(staff, ss.MAX_GRADE); staff["exp"] = 0
    for e in staff.get("effect") or []:
        e["amount"] = 5; e["open"] = True


@api("staff_full")
def staff_full(p):
    """Full sao (5 sao = cap 10) + moi dong chi so +5; id rong = tat ca HLV."""
    ss = _ss(); st = hc._state(); t = team(st); ss.normalize_team(t)
    n = 0
    for x in t.get("staffs", []):
        if not p.get("id") or x["_id"] == p.get("id"):
            _full(x); n += 1
    hc._save(st)
    return {"done": n}


@api("staff_effect")
def staff_effect(p):
    """Chon chi so cho 1 dong (trong nhom cua HLV do)."""
    st = hc._state(); t = team(st)
    x = next((x for x in t.get("staffs", []) if x["_id"] == p.get("id")), None)
    if x is None:
        raise AdminError("Không tìm thấy HLV")
    idx = as_int(p.get("idx"), 1, 3, "idx")
    if p.get("key") not in {c["key"] for c in _staff_choices(x, idx)}:
        raise AdminError("Chỉ số này không thuộc nhóm của HLV")
    e = next((e for e in x.get("effect") or [] if e.get("effectidx") == idx), None)
    if e is None:
        raise AdminError("HLV chưa mở dòng chỉ số này")
    e["effect"] = p["key"]
    hc._save(st)
    return {"ok": True}

@api("staff_remove")
def staff_remove(p):
    ss = _ss(); st = hc._state(); t = team(st); ss.normalize_team(t)
    x = next((x for x in t.get("staffs", []) if x["_id"] == p.get("id")), None)
    if x is None:
        raise AdminError("Không tìm thấy HLV")
    if x.get("status") == "appointed":
        raise AdminError("HLV đang được bổ nhiệm. Hãy bỏ bổ nhiệm trong game trước.")
    t["staffs"] = [y for y in t["staffs"] if y["_id"] != x["_id"]]
    hc._save(st)
    return {"ok": True}

class Admin(http.server.BaseHTTPRequestHandler):
    def _send(self, code, data, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _route(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if self.client_address[0] not in ("127.0.0.1", "::1") or host not in ("127.0.0.1", "localhost", "[::1]"):
            return self._send(403, b"forbidden", "text/plain")
        url = urllib.parse.urlparse(self.path)
        if url.path == "/notice.html":
            with open(os.path.join(HERE, "admin", "notice.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        m = re.fullmatch(r"/pimg/(\d+)\.png", url.path)
        if m:  # anh cau thu tu kho anh cua game (the mua dac biet -> anh theo pid)
            r = hc.cdn_file(f"externalAssets/players/p{m.group(1)}.png")
            if r:
                return self._send(200, r[1], r[0])
            return self._send(404, b"", "image/png")
        if url.path == "/hud.jpg":  # anh dau trang GM Tool
            with open(os.path.join(HERE, "admin", "hud.jpg"), "rb") as f:
                return self._send(200, f.read(), "image/jpeg")
        if url.path in ("/", "/index.html"):
            with open(PAGE, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        m = re.fullmatch(r"/staffimg/(\d+)\.png", url.path)
        if m:  # anh HLV tu cache game (externalAssets/staffs)
            f = os.path.join(hc.ASSETS, "externalAssets", "staffs", m.group(1) + ".png")
            if os.path.isfile(f):
                with open(f, "rb") as fh:
                    return self._send(200, fh.read(), "image/png")
            return self._send(404, b"", "image/png")
        name = url.path[len("/api/"):] if url.path.startswith("/api/") else ""
        fn = API.get(name)
        if fn is None:
            return self._send(404, '{"error": "Không tìm thấy"}'.encode())
        if name not in READ_ONLY and (self.command != "POST" or self.headers.get("X-FO3-Admin") != "1"):
            return self._send(403, '{"error": "Thay đổi phải gửi bằng POST kèm header X-FO3-Admin"}'.encode())
        params = dict(urllib.parse.parse_qsl(url.query))
        n = int(self.headers.get("Content-Length") or 0)
        try:
            if n:
                params.update(json.loads(self.rfile.read(n) or b"{}"))
            if name in NO_LOCK:  # chi doc du lieu the (khong dung state) -> khong giu khoa, game khong phai cho
                out = fn(params)
            else:
                with hc.STATE_LOCK:  # same lock as the game's requests
                    out = fn(params)
        except AdminError as e:
            out = {"error": str(e)}
        except StopIteration:  # chua co HLV (acc moi / vua reset): moi ham tim HLV deu roi vao day
            out = {"error": "Chưa có tài khoản HLV. Hãy vào game tạo HLV trước, rồi tải lại trang này."}
        except Exception as e:  # keep the page usable; the detail shows in its message bar
            out = {"error": f"{type(e).__name__}: {e}"}
        self._send(200, json.dumps(out).encode())

    do_GET = _route
    do_POST = _route

    def log_message(self, *a):
        pass


def start(module, port):
    """Serve the admin page on 127.0.0.1:<port> in a daemon thread."""
    global hc
    hc = module
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Admin)
    threading.Thread(target=srv.serve_forever, daemon=True, name="admin").start()
    threading.Thread(target=lambda: _safe(_warm), daemon=True, name="card-index").start()  # dung san du lieu cho GM Tool
    return srv


def _warm():
    """Lam nong khi server vua bat: dung san danh sach the, doi/nuoc/giai, mua, cau thu CLB
    -> yeu cau dau tien tu GM Tool khong phai cho."""
    card_index()
    facets({})
    seasons({})
    for fn in (players, summary):
        try:
            fn({})
        except Exception:
            pass


def _safe(fn):
    try:
        fn()
    except Exception as e:
        print("Không dựng được danh sách thẻ:", e, flush=True)
