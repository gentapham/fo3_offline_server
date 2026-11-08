"""Giao huu (dau nhanh / ngau nhien / phong tu tao, dau voi may), King of the Hill va Exhibition
voi HLV ao offline - port tu fo3-server-indo (issue #36), chay tren duong phong / tran cua rank.py.

- Giao huu (menu Nhieu nguoi > Giao huu, features.friendlymatch): Dau nhanh / Ngau nhien tim 1 HLV ao
  gan Elo; phong tu tao: moi ban (HLV ao vao phong, san sang) hoac dau voi may (o chon may: quoc gia,
  giai, CLB, do kho Nghiep du..Huyen thoai). Ket qua vao statistics.friendly, khong tinh Elo Rank.
- King of the Hill (Giai khu vuc, room esports2, tran Regional_League): chon vung (bo vung Viet Nam
  mac dinh, doi o GM Tool), bang xep hang vung gom HLV ao + minh, gap HLV ao cung vung gan diem KOTH,
  diem Elo rieng (K 32), mua = thang (gio VN). Khong co qua tuan / mua.
- Exhibition (Live Center): moi ben 1 CLB co ban quyen (doi hinh cua CLB), doi thu = ban moi tu danh
  sach (HLV ao chon CLB cua minh), toi da EXHIBITION_PLAYS tran / ngay; the cua minh khong nhan EXP.
Client: data/ui/MenuScreen.big phai la ban KHONG chan friendly / exhibition / regionalleague (ban va cu
hien OFFLINE_ONLINE_ONLY); dung lai bang tools/menu_patch/patch_menu.py (ONLINE = [], giu nut World Cup).
Cai dat: gamedata/multiplay.json {friendly, exhibition, koth, regions}.
"""
import collections
import copy
import functools
import json
import os
import random
import re
import sys
import time

hc = None
SETTINGS_FILE = None
SETTINGS_DEFAULT = {"friendly": True, "exhibition": True, "koth": True, "regions": "vn"}

# --------------------------------------------------------------------------- King of the Hill
KOTH_GROUP, KOTH_LADDER = "1", 5  # elo[5] cua HLV ao = diem KOTH
KOTH_SETS = {  # ten = khoa cua o chon vung: khong trung; moi bo co id rieng
    "vn": (("31", "Miền Bắc", (("3101", "Hà Nội"), ("3102", "Hải Phòng"), ("3103", "Quảng Ninh"), ("3104", "Bắc Ninh"))),
           ("32", "Miền Trung", (("3201", "Đà Nẵng"), ("3202", "Huế"), ("3203", "Vinh"), ("3204", "Nha Trang"))),
           ("33", "Tây Nguyên", (("3301", "Buôn Ma Thuột"), ("3302", "Pleiku"), ("3303", "Đà Lạt"))),
           ("34", "Đông Nam Bộ", (("3401", "TP. Hồ Chí Minh"), ("3402", "Biên Hòa"), ("3403", "Thủ Dầu Một"),
                                  ("3404", "Vũng Tàu"))),
           ("35", "Tây Nam Bộ", (("3501", "Cần Thơ"), ("3502", "Long Xuyên"), ("3503", "Rạch Giá")))),
    "th": (("21", "Greater Bangkok", (("2101", "Bangkok"), ("2102", "Nonthaburi"), ("2103", "Samut Prakan"))),
           ("22", "Central", (("2201", "Ayutthaya"), ("2202", "Nakhon Pathom"))),
           ("23", "North", (("2301", "Chiang Mai"), ("2302", "Chiang Rai"), ("2303", "Phitsanulok"))),
           ("24", "Northeast", (("2401", "Khon Kaen"), ("2402", "Nakhon Ratchasima"), ("2403", "Udon Thani"),
                                ("2404", "Ubon Ratchathani"))),
           ("25", "East", (("2501", "Chonburi"), ("2502", "Pattaya"), ("2503", "Rayong"))),
           ("26", "South", (("2601", "Phuket"), ("2602", "Hat Yai"), ("2603", "Surat Thani")))),
    "id": (("1", "Sumatera", (("101", "Medan"), ("102", "Padang"), ("103", "Palembang"), ("104", "Pekanbaru"))),
           ("2", "Jawa", (("201", "Jakarta"), ("202", "Bandung"), ("203", "Semarang"), ("204", "Yogyakarta"),
                          ("205", "Surabaya"), ("206", "Malang"))),
           ("3", "Kalimantan", (("301", "Pontianak"), ("302", "Balikpapan"), ("303", "Banjarmasin"))),
           ("4", "Sulawesi", (("401", "Makassar"), ("402", "Manado"))),
           ("5", "Bali Nusra", (("501", "Denpasar"), ("502", "Mataram"))),
           ("6", "Maluku Papua", (("601", "Ambon"), ("602", "Jayapura")))),
}
KOTH_SET_NAMES = {"vn": "Việt Nam", "th": "Thái Lan", "id": "Indonesia"}
KOTH_GOALS = {"weekly_winning_reward_condition": 10, "weekly_match_reward_condition": 20,
              "weekly_grade_reward_condition": 20, "season_grade_reward_condition": 50}  # thanh tien do tab dau

EXHIBITION_PLAYS = 5  # tran Exhibition / ngay


# --------------------------------------------------------------------------- settings
def settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        d = d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        d = {}
    out = {k: d[k] if isinstance(d.get(k), bool) else v for k, v in SETTINGS_DEFAULT.items() if k != "regions"}
    out["regions"] = d.get("regions") if d.get("regions") in KOTH_SETS else SETTINGS_DEFAULT["regions"]
    return out


def save_settings(d):
    cur = settings()
    cur.update({k: bool(d[k]) for k in ("friendly", "exhibition", "koth") if k in d})
    if d.get("regions") in KOTH_SETS:
        cur["regions"] = d["regions"]
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SETTINGS_FILE)
    return cur


def koth_set():
    return settings()["regions"]


@functools.lru_cache(maxsize=None)
def koth_leaves(key):
    """{id thanh pho: (ten, id vung, ten vung)} cua 1 bo vung."""
    return {sid: (name, mid, mname) for mid, mname, subs in KOTH_SETS[key] for sid, name in subs}


def vm_region(vid, key=None):
    key = key or koth_set()
    return random.Random(f"region/{key}/{vid}").choice(sorted(koth_leaves(key)))


def koth_sid():
    """Mua KOTH = thang theo gio Viet Nam (client chon mua theo ngay cua no)."""
    return hc.rank.ladder_season(time.time() + hc.WIB)[0]


def koth_season(sid):
    y, m = divmod(sid, 100)
    ny, nm = (y + m // 12, m % 12 + 1)
    divs = len(hc.rank.ladder_divisions())
    return {"_id": str(sid), "title": f"King of the Hill {m:02d}/{y}",
            "schedule": {"startdt": f"{y}{m:02d}01", "enddt": f"{ny}{nm:02d}01", "week": [True] * 7, "midreward": [],
                         "weekdays": {"starttime": "0000", "endtime": "2400"}, "weekend": {"starttime": "0000", "endtime": "2400"}},
            "regiongroup": {"id": KOTH_GROUP}, "scale": {"medium": 30, "large": 60, "exlarge": 100},
            "condition": {"autoreg": False, "regonlyigr": False, "playonlyigr": False},
            "reward": {"popup": False, "config": KOTH_GOALS,
                       "week": {"type": "winning", "win": [], "match": [], "grade": [[] for _ in range(divs)]},
                       "season": {"grade": {"1": [[] for _ in range(divs)]},
                                  "ranking": {"1": {"normal": [], "medium": [], "large": [], "exlarge": []}}}}}


def ep_koth_presently(query, body):
    sid = koth_sid()
    nxt = sid + 1 if sid % 100 < 12 else sid + 89
    tree = {mname: {"id": mid, "name": mname, "rewardgroupid": 1,
                    "children": {n: {"id": s, "name": n, "parent": mname} for s, n in subs}}
            for mid, mname, subs in KOTH_SETS[koth_set()]}
    return hc._json({"seasons": [koth_season(sid), koth_season(nxt)], "regiongrouptrees": {KOTH_GROUP: tree}, "prev": []})


def my_koth(st, coach=None):
    """Ban ghi KOTH mua nay {sid, regionid, introid, elo, wins, draws, losses, streak, week, weekwins, weekgames}."""
    sid = koth_sid()
    r = (coach or hc.my_coach(st)).setdefault("es2", {})
    if r.get("sid") != sid:
        r.update(sid=sid, elo=hc.rank.LADDER_START, wins=0, draws=0, losses=0, streak=0, introid="")
    week = time.strftime("%G%V", time.gmtime())
    if r.get("week") != week:
        r.update(week=week, weekwins=0, weekgames=0)
    return r


def koth_board(st, region):
    """[(elo, thang, thua, id, ten)] cua 1 vung (vung lon: gom ca thanh pho), cao nhat truoc."""
    key = koth_set()
    leaves = koth_leaves(key)
    within = lambda leaf: leaf == region or (leaves.get(leaf) or (0, 0))[1] == region
    rows = [(m["elo"][KOTH_LADDER], m["wins"][KOTH_LADDER], m["losses"][KOTH_LADDER], m["_id"], m["name"])
            for m in hc.rank.virtual_managers() if within(vm_region(m["_id"], key))]
    r, coach = my_koth(st), hc.my_coach(st)
    if within(r.get("regionid")):
        rows.append((r["elo"], r["wins"], r["losses"], coach["_id"], coach["name"]))
    return sorted(rows, key=lambda x: (-x[0], -x[1], x[3]))


def koth_statistics(st, coach):
    """statistics.esports2: seasonid chi co khi da chon vung (chua co -> man chinh hien o chon vung)."""
    r = my_koth(st, coach)
    leaves = koth_leaves(koth_set())
    out = {"seasonid": str(r["sid"]) if r.get("regionid") in leaves else "", "introid": r.get("introid", ""),
           "elo": r["elo"], "wins": r["wins"], "losses": r["losses"], "forfeits": 0,
           "midwins": r["weekwins"], "midmatches": r["weekgames"], "rank": 0, "rewarded": True, "midrewarded": []}
    if r.get("regionid") in leaves:
        name, _, parent = leaves[r["regionid"]]
        out.update(regionid=r["regionid"], regionname={"name": name, "parent": parent})
        out["rank"] = next((k for k, x in enumerate(koth_board(st, r["regionid"]), 1) if x[3] == coach["_id"]), 0)
    return out


def ep_koth_register_counts(sid, query, body):
    """GET /esports2/region/register/<mua>: so nguoi moi vung (o chon vung hien)."""
    key = koth_set()
    count = collections.Counter(vm_region(m["_id"], key) for m in hc.rank.virtual_managers())
    r = my_koth(hc._state())
    if r.get("regionid"):
        count[r["regionid"]] += 1
    return hc._json({"result": True, "registeredplayer": {k: count[k] for k in koth_leaves(key)}})


def ep_koth_register(query, body):
    """POST /esports2/region/register {regiongroupid}: vung cua minh."""
    region = str(json.loads(body or b"{}").get("regiongroupid") or "")
    if region not in koth_leaves(koth_set()):
        return hc._json({"result": False, "reason": "Esports_Grassroot_RegionSelect_Fail"})
    st = hc._state()
    my_koth(st)["regionid"] = region
    hc._save(st)
    return hc._json({"result": True, "resources": hc.coach_me_resources(st, hc._default_slot(st))})


def ep_koth_intro(query, body):
    """PUT /esports2/region/intro {seasonid}: da xem gioi thieu."""
    st = hc._state()
    my_koth(st)["introid"] = str(json.loads(body or b"{}").get("seasonid") or koth_sid())
    hc._save(st)
    return hc._json({"result": True, "resources": hc.coach_me_resources(st, hc._default_slot(st))})


def ep_koth_leaderboard(region, query, body):
    return hc._json([{"cid": i, "name": n, "elo": e, "wins": w, "losses": l}
                     for e, w, l, i, n in koth_board(hc._state(), region)[:100]])


def koth_opponent(st):
    """1 trong 5 HLV ao cung vung gan diem KOTH nhat (vung khong co ai: bat ky)."""
    r = my_koth(st)
    key = koth_set()
    crowd = hc.rank.virtual_managers()
    pool = [m for m in crowd if vm_region(m["_id"], key) == r.get("regionid")] or crowd
    return random.choice(sorted(pool, key=lambda m: abs(m["elo"][KOTH_LADDER] - r["elo"]))[:5])


def koth_record(st, vm, mine, theirs, sh, sa):
    r = my_koth(st)
    score = 1 if (mine, sh) > (theirs, sa) else 0 if (mine, sh) < (theirs, sa) else 0.5
    old = r["elo"]
    expect = 1 / (1 + 10 ** ((vm["elo"][KOTH_LADDER] - old) / 400))
    r["elo"] = max(hc.rank.LADDER_START, round(old + hc.rank.LADDER_K * (score - expect)))
    r[{1: "wins", 0: "losses", 0.5: "draws"}[score]] += 1
    r["streak"] = max(r["streak"], 0) + 1 if score == 1 else min(r["streak"], 0) - 1 if score == 0 else 0
    r["weekgames"] += 1
    r["weekwins"] += score == 1
    multiplay_add(st, "Regional_League", vm, mine, theirs, sh, sa)
    return old, r["elo"], r["streak"]


# --------------------------------------------------------------------------- Exhibition
@functools.lru_cache(maxsize=1)
def exhibition_teams():
    """8 CLB manh nhat (khong tinh doi tuyen quoc gia, doi dac biet) - nut mui ten trong phong."""
    teams = [(hc.club_rating(t), t) for _, ls in hc.club_index() for lid, ts in ls if lid != 78
             for t in ts if t < 110000]
    return tuple(t for _, t in sorted(teams, reverse=True)[:8]) or (hc.gen_teams.DEFAULT_TEAMS[0],)


def exhibition_pick(vid, mine=None):
    teams = [t for t in exhibition_teams() if t != mine] or list(exhibition_teams())
    return random.Random(f"exhib/{vid}").choice(teams)


def exhibition_played(st, add=0):
    """So tran Exhibition hom nay (gio VN)."""
    r = hc.my_coach(st).setdefault("exhibition", {})
    if r.get("day") != hc.today_wib():
        r.update(day=hc.today_wib(), played=0)
    r["played"] += add
    return r["played"]


def ep_exhibition_room(query, body):
    """POST /exhibition/room: phong Exhibition, ben minh = CLB de cu dau tien."""
    st = hc._state()
    if not settings()["exhibition"] or exhibition_played(st) >= EXHIBITION_PLAYS:
        return hc._json({"result": False, "reason": "the point is not enough"})
    try:
        req = json.loads(body or b"{}")
    except ValueError:
        req = {}
    hc.rank.ep_room(query, json.dumps({**(req if isinstance(req, dict) else {}), "type": "pvp_exhibition"}).encode())
    st = hc._state(); room = st["room"]
    room["side"]["left"]["authTeamId"] = exhibition_teams()[0]
    hc._save(st)
    return hc.rank.ladder_room_reply(room)


def _room_team(st, room, req):
    """POST /exhibition/room/<id>/team {teamid, side}: CLB cua ben do."""
    side, teamid = req.get("side"), hc._num(req.get("teamid"))
    if side not in ("left", "right") or not (hc.roster(teamid).get("team") or {}).get("players"):
        return hc._json({"result": False, "reason": "invalid team"})
    room["side"][side]["authTeamId"] = teamid
    room["side"]["left"]["ready"] = False


EXHIBITION_SUBS = 7


def ep_exhibition_team(teamid, query, body):
    """GET /exhibition/info/authentic/team/<id>: doi cua CLB cho man chon doi Exhibition. Danh sach du bi
    (ExhibitionPlayerdisplaylist.setDisplayInfo) doc players[11 + i] va squad.positions[11 + i].pos THEO
    THU TU, ma doi hinh goc chi co 11 vi tri -> TypeError #1010, popup "Tinh nang nay chua chay duoc..."
    (vd Argentinos Juniors 111019). Them 7 du bi (OVR cao nhat con lai), xep players theo dung thu tu
    vi tri: 11 da chinh, 7 du bi, roi phan con lai."""
    ctype, data = hc.ep_authentic_team("all", teamid, query, body)
    try:
        r = json.loads(data)
        team = r.get("team") or {}
        players, pos = team.get("players") or [], (team.get("squad") or {}).get("positions") or []
        key = lambda p: str(p.get("id") or p.get("spid"))
        by = {key(p): p for p in players}
        xi = [q for q in pos if q.get("pos", hc.POS_SUB) < hc.POS_SUB and str(q.get("id") or q.get("spid")) in by]
        used = {str(q.get("id") or q.get("spid")) for q in xi}
        rest = sorted((p for p in players if key(p) not in used), key=lambda p: -hc.card_ovr(p["spid"]))
        subs = rest[:EXHIBITION_SUBS]
        team["squad"]["positions"] = xi + [{"pos": hc.POS_SUB, "id": p.get("id"), "spid": p["spid"]} for p in subs]
        team["players"] = [by[str(q.get("id") or q.get("spid"))] for q in xi] + rest
        return hc._json(r)
    except Exception as e:  # giu nguyen cau tra loi goc neu co gi la
        print(f"exhibition team {teamid}: {e!r}", flush=True)
        return ctype, data


def static_exhibition(query, body):
    return hc._json({"entrycondition": {"NORMAL_use": True, "IGR_use": False, "VIP_use": False},
                     "recommendteams": list(exhibition_teams()),
                     "staticvalues": {k: EXHIBITION_PLAYS for k in ("Normal_count", "IGR_count", "VIP1_count")}})


# --------------------------------------------------------------------------- records
def multiplay_add(st, mtype, vm, mine, theirs, sh, sa):
    """Manager > Info Manager > Ket qua nhieu nguoi: logo, CLB, ti so, loai tran."""
    me = hc.my_coach(st)
    me["multiplay"] = (me.get("multiplay", []) + [{
        "type": mtype, "date": hc.w3c(time.time()), "forfeits": 0,
        "team": {"teamid": me["teamid"], "score": mine, "shootout": sh, "coaches": [{"coachid": me["_id"], "name": me["name"]}]},
        "opponent": {"teamid": vm.get("teamid"), "score": theirs, "shootout": sa, "clubname": "",
                     "coaches": [{"coachid": vm["_id"], "name": vm["name"]}] if vm.get("_id") else []}}])[-hc.rank.MULTIPLAY_MAX:]


def friendly_record(st, p, vm, mine, theirs, sh, sa):
    """statistics.friendly[quick | custom | vscom] (hang 1 vs 1) + danh sach tran nhieu nguoi."""
    fr = hc.my_coach(st).setdefault("statistics", {}).setdefault("friendly", {})
    key = p.get("record") if p.get("record") in ("quick", "custom", "vscom") else "custom"
    row = fr.setdefault(key, {})
    for k in ("wins", "draws", "losses"):
        if not isinstance(row.get(k), list):
            row[k] = [0] * 5
    res = "wins" if (mine, sh) > (theirs, sa) else "losses" if (mine, sh) < (theirs, sa) else "draws"
    row[res][0] += 1
    if key == "quick":  # chuoi thang dai nhat cua Dau nhanh
        run = hc.my_coach(st).setdefault("friendlyrun", {"streak": 0, "best": 0})
        run["streak"] = run["streak"] + 1 if res == "wins" else 0
        run["best"] = max(run["best"], run["streak"])
        row["winningstreak"] = {"max": [run["best"]]}
    multiplay_add(st, {"vscom": "Friendly_PvE_1v1", "exhibition": "Exhibition_PvP"}.get(p.get("kind"), "Friendly_PvP_1v1"),
                  vm, mine, theirs, sh, sa)


def record_result(st, mid, p, vm, mine, theirs, sh, sa):
    """Ket qua tran khong phai Rank (goi tu rank.ladder_result)."""
    kind = p.get("kind")
    if kind == "koth":
        old, new, streak = koth_record(st, vm, mine, theirs, sh, sa)
        match = st.get("match") or {}
        if match.get("id") == mid and isinstance(match.get("reward"), dict):  # o "Diem hang" man thuong
            match["reward"].update(preveloinfo={"elo": old}, eloinfo={"elo": new, "streak": streak, "goaldiff": mine - theirs})
        return
    if kind == "exhibition":
        exhibition_played(st, 1)
    friendly_record(st, p, vm, mine, theirs, sh, sa)


# --------------------------------------------------------------------------- install
def install(module):
    global hc, SETTINGS_FILE
    hc = module
    hc.multiplay = sys.modules[__name__]
    SETTINGS_FILE = os.path.join(hc.HERE, "gamedata", "multiplay.json")
    room_change = hc.rank.room_change
    hc.ROUTES[:0] = [
        (re.compile(r"/exhibition/room/\d+/team"), room_change(_room_team)),
        (re.compile(r"/exhibition/info/authentic/team/(\d+)"), ep_exhibition_team),
        (re.compile(r"/esports2/region/register/(\w+)"), ep_koth_register_counts),
        (re.compile(r"/esports2/region/leaderboard/(\w+)"), ep_koth_leaderboard),
    ]
    balancing = lambda: {"divisions": hc.rank.ladder_divisions()}
    hc.ENDPOINTS.update({
        # Live Center: man truoc doc khong kiem tra -> {} = 2 popup loi (entrycondition, fixture curr)
        "/static/db/EXHIBITIONMATCH": static_exhibition,
        "/exhibition/info/points": lambda q, b: hc._json({"point": exhibition_played(hc._state()), "maxpoint": EXHIBITION_PLAYS}),
        "/exhibition/room": ep_exhibition_room,
        "/livematch/info/fixture": lambda q, b: hc._json({"prev": [], "curr": [], "remainedsecs": 86400}),
        # KOTH: CoachProxy cham diem elo bang ESPORTS2_BALANCING_DEFAULT dong bo -> nap san o /static/initialize
        "/static/initialize": lambda q, b: hc._json({"resources": [
            {"uri": "/static/db/ESPORTS2_BALANCING_DEFAULT", "body": balancing()}]}),
        "/static/db/ESPORTS2_BALANCING_DEFAULT": lambda q, b: hc._json(balancing()),
        "/esports2/region/seasons/presently": ep_koth_presently,
        "/esports2/region/igrregion": lambda q, b: hc._json({"result": True}),
        "/esports2/region/register": ep_koth_register, "/esports2/region/intro": ep_koth_intro,
    })

    base_account, base_coach_me, base_reward = hc.ENDPOINTS["/account"], hc.coach_me, hc.match_reward

    def ep_account(query, body):
        ctype, data = base_account(query, body)
        if not hc.rank.settings()["enabled"]:
            return ctype, data
        acc = json.loads(data)
        feats, s = acc.setdefault("features", {}), settings()
        menu = feats.setdefault("mainmenu", {})
        mm = menu.setdefault("multimode", {})
        if s["friendly"]:  # MultiplayQuickMatchScreen.checkMatchFeature doc featuresAt("/friendlymatch")
            feats["friendlymatch"] = {"quickmatch": {}, "randommatch": {}, "custommatch": {}}
            mm.setdefault("friendly", {})
        if s["exhibition"]:
            mm.setdefault("exhibition", {})
        if s["koth"]:  # RegionalLeagueProxy chi chay khi co features.esports.regionalleague
            feats.setdefault("esports", {})["regionalleague"] = {}
            menu.setdefault("regionalleague", {})
        return hc._json(acc)

    def coach_me(st, slot):
        me = base_coach_me(st, slot)
        if slot == hc._default_slot(st):
            try:
                stats = me.setdefault("statistics", {})
                stats["esports2"] = koth_statistics(st, hc.my_coach(st))
                fr = copy.deepcopy(stats.get("friendly") or {})  # ban sao: khong sua state
                for k in ("quick", "custom", "vscom"):  # dong 1 vs 1 .. 5 vs 5
                    row = fr.setdefault(k, {})
                    for f in ("wins", "draws", "losses"):
                        if not isinstance(row.get(f), list):
                            row[f] = [0] * 5
                if not isinstance(fr["quick"].get("winningstreak"), dict):
                    fr["quick"]["winningstreak"] = {"max": [0]}
                stats["friendly"] = fr
            except Exception as e:
                print(f"multiplay statistics: {e!r}", flush=True)
        return me

    def match_reward(st, stats, *a, **k):
        """Exhibition: da bang CLB khac -> the cua minh khong nhan EXP (chi EP / EXP HLV)."""
        p = st.get("ladder_pending") or {}
        if p.get("kind") == "exhibition" and isinstance(stats, dict):
            stats = copy.deepcopy(stats)
            for side in ("home", "away"):
                if isinstance(stats.get(side), dict):
                    stats[side]["players"] = []
        return base_reward(st, stats, *a, **k)

    hc.ENDPOINTS["/account"] = ep_account
    hc.coach_me, hc.match_reward = coach_me, match_reward
    for path in ("/static/db/EXHIBITIONMATCH", "/static/initialize", "/static/db/ESPORTS2_BALANCING_DEFAULT"):
        hc.DYNAMIC_PATHS.add(path)
