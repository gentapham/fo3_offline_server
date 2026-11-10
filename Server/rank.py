"""Xep hang (Multiple > Rank) voi HLV ao offline - port tu fo3-indo-server (issue #30).

- Mot dam dong HLV ao (100 luc dau, moi ngay co nguoi moi, toi da 300): ten, cap, CLB, phong cach
  choi, hang cap the / cap do theo trinh (cao thu: the +6..+11, LV toi 30), diem Elo moi che do.
  Tat ca sinh tu hat giong theo id + ngay -> khong luu gi, moi lan chay server deu cung dam dong.
  Doi hinh moi HLV thay doi tung ngay (mua ban, nang cap the, len cap).
- Rank 1v1 / Rank HLV = tran that tren may (client tu dieu khien doi kia bang AI) gap doi hinh cua
  HLV ao co Elo gan minh nhat; ket qua -> Elo (K 32), 5 tran xep hang, hang (division), bang xep
  hang, ban be (20 HLV ao, trang thai online/ban/offline mo phong), chat trong tran (tieng Viet).
- Giao dien: LadderMatchController (data/ui/libs/Module.big) + cac man LadderMatch*.big; phong =
  RoomController (POST /room, /room/<id>/matchmaking); tran = loai PvP Ladder_PvP_1v1 /
  Ladder_MGR_PvP_1v1 (man doi hinh -> man cai dat MultiplayGameSettingScreen -> da).
- Phong cung dung cho Giao huu / dau voi may / KOTH / Exhibition (room_kind; xem multiplay.py). Feature flags them o GET /account (tat duoc o
  GM Tool > Tran dau > Xep hang).
Cai dat: gamedata/rank_settings.json {enabled, chat}; thuong mua / tuan: gamedata/ladder_rewards.json (ladder_rewards).
"""
import calendar
import copy
import functools
import html
import json
import os
import random
import re
import sys
import threading
import time
from contextlib import closing

hc = None

SETTINGS_FILE = None  # gamedata/rank_settings.json (dat trong install)
SETTINGS_DEFAULT = {"enabled": True, "chat": True}

VM_COUNT, VM_SEED, VM_FRIENDS = 100, 30, 20
LADDER_COUNT = 6  # moi chi so ladder (Enums.LADDER_MATCH_COUNT va 3vs3 roleplay)
# (ti le, hang the cua HLV, cap HLV, diem Elo): moi cau thu = hang HLV +-1 (toi da +2 sau nang cap)
VM_TIERS = (
    (0.50, (1, 3), (3, 30), (1000, 1250)),    # moi choi: Nghiep du -> Ban chuyen
    (0.35, (3, 6), (25, 60), (1200, 1500)),   # trung binh: Ban chuyen -> Dang cap the gioi
    (0.15, (6, 9), (50, 100), (1450, 1780)),  # cao thu: Dang cap the gioi, Huyen thoai
)
# Ten HLV ao = ten / kenh cua streamer noi tieng Viet Nam va the gioi (ten kenh, khong dau, <= 16 ky tu).
# VM_TOP (Mixigaming) la HLV manh nhat, khon nhat, luon dung top 1 o moi che do Rank (vm_top_fix).
VM_TOP = "Mixigaming"
VM_NAMES_VN = (
    "ThayGiaoBa", "PewPew", "ViruSs", "Xemesis", "Bomman", "ChimSeDiNang", "CrisDevilGamer", "MisThy",
    "LinhNgocDam", "Optimus", "QTV", "Rambo", "HungAkira", "MrVit", "DungCT", "QuangCuon", "NamBlue",
    "TienZombieV4", "PhongCanTV", "MaiLinhZuto", "HoangLuan", "BacGau", "LamVlog", "SiroOfficial",
    "ThayOngNoi", "SofM", "Levi", "Zeros", "BLVAnhQuan", "KhoaPug", "RefundGaming", "TuanTienTi",
    "TheAnh28", "MeoSimmy", "HeroTeam", "Kairon", "SammyDao", "NTNVlogs",
)
VM_NAMES_WORLD = (
    "Ninja", "Shroud", "xQc", "Pokimane", "Ibai", "TheGrefg", "AuronPlay", "Rubius", "Gaules",
    "KaiCenat", "IShowSpeed", "Tfue", "DrLupo", "TimTheTatman", "NICKMERCS", "summit1g", "Asmongold",
    "HasanAbi", "Ludwig", "Sykkuno", "Valkyrae", "Myth", "Lirik", "Sodapoppin", "Tyler1", "Faker",
    "Caedrel", "Doublelift", "Imaqtpie", "Mizkif", "Moistcr1tikal", "Pestily", "DisguisedToast",
    "CohhCarnage", "Trainwreckstv", "Jynxzi", "Castro1021", "Gotaga", "Squeezie", "Kameto", "ZeratoR",
    "Bugha", "Clix", "Mongraal", "SypherPK", "CourageJD", "LilyPichu", "Fuslie", "Tarik", "s1mple",
    "Alanzoka", "Casimiro", "LoudCoringa", "MontanaBlack", "Trymacs", "Papaplatte", "Knossi", "Gronkh",
    "HandOfBlood", "Nepenthez", "Bateson87", "Miniminter", "Vikkstar123", "W2S", "KSI", "Behzinga",
    "Zerkaa", "TBJZL", "ChrisMD", "TheoBaker", "DjMaRiiO", "Vegetta777", "Willyrex", "Spreen",
    "Juansguarnizo", "Quackity", "Westcol", "Rivers", "Coscu", "SLAKUN10", "Uzi", "Dream",
    "GeorgeNotFound", "Sapnap", "TommyInnit", "Tubbo", "Ranboo", "Technoblade", "Markiplier",
    "Jacksepticeye", "PewDiePie", "MrBeast", "Nadeshot", "Scump", "FormaL", "Aceu", "Hiko", "Stewie2K",
    "Gorilla", "Tekkz", "MsDossary", "Agent00", "Sketch", "BruceDropEmOff", "CaseOh", "Fanum",
    "DukeDennis", "StableRonaldo", "Lacy", "N3on", "Amouranth", "QTCinderella", "Emiru", "Kyedae",
    "TenZ", "Sinatraa", "Vicens", "Perxitaa", "Mangel", "Lolito", "Biyin", "ElMariana", "Robleis",
)
VM_NAMES = tuple(dict.fromkeys(n for n in VM_NAMES_VN + VM_NAMES_WORLD if n != VM_TOP))

# Dam dong song: lich bat dau tu VM_EPOCH (ngay 0 = dam dong dau, vm_base); tu ngay 1 co nguoi moi
# (vm_newcomers) va doi hinh moi HLV doi theo ngay (vm_squad). Tat ca sinh theo HLV + ngay.
VM_EPOCH = calendar.timegm((2026, 11, 1, 0, 0, 0))
VM_MAX = min(300, len(VM_NAMES) + 1)  # dam dong ngung tang o day (het ten streamer)
VM_JOIN = (0, 1, 1, 2, 3)  # so nguoi gia nhap 1 ngay (boc 1)
VM_JOIN_TIER = (0.7, 0.25, 0.05)  # nguoi moi chu yeu la nguoi moi choi
VM_LIFE = (0.12, 0.08, 0.03)  # moi ngay: chuyen nhuong 1 the, thu nang cap 1 the, 1 cau thu chinh len cap
VM_GAMES = (0, 0, 1, 1, 2, 3)  # so tran Rank mot ngay moi che do (boc 1)
# gio (UTC) HLV choi / gia nhap: 01..19 = 08:00..02:00 gio Viet Nam, buoi toi (16-23h) dong gap doi
VM_HOURS = tuple(range(1, 20)) + tuple(range(9, 17))

LADDER_START, LADDER_K = 1000, 32
LADDER_DEPLOY = 5  # Enums.LADDER_MAX_DEPLOY_MATCH: tran xep hang
LADDER_MY_KEYS = {0: "ladder_pvp_1v1", 1: "ladder_pvp_2v2", 2: "ladder_pvp_3v3", 3: "ladder_mgr_pvp_1v1"}
LADDER_TYPES = ("Ladder_PvP_1v1", "Ladder_PvP_2v2", "Ladder_PvP_3v3", "Ladder_MGR_PvP_1v1")  # LadderMatchController.matchTypes
LADDER_SEARCH = (3, 7)  # giay "dang tim" truoc khi gap doi thu
LADDER_RECENT, MULTIPLAY_MAX = 3, 20  # 3 tran gan nhat (RANKEDMATCH_RECENT_MATCH_MAX); Thong tin HLV
CARD_LEVEL_TOP = 28  # cap cao nhat khi tao the cua HLV ao (len cap hang ngay toi PLAYER_MAX_LEVEL)

# Phong cach choi (VM_STYLES): tran doc side.<x>.tactics[squad.tactic or 0] va side.team.squad (so do,
# starting [tham gia tan cong, tham gia phong ngu, x, y]) cho ca doi AI. Thanh truot 1..100; radio
# positioning 1 to chuc / 2 tu do, defenderline 1 boc lot / 2 bay viet vi; tham gia 0 vua, 1 thap, 2 cao.
VM_STYLES = {  # (bus speed, pass, positioning, cc pass, cross, shoot, positioning, def pressure,
               # aggression, width, defenderline), tinh than, so do, (att, def) cho HV / TV / TD
    "possession": ((20, 20, 2, 25, 25, 25, 2, 50, 25, 50, 1), 3, "4-3-3(1)", ((0, 0), (0, 0), (2, 1))),
    "counter": ((95, 50, 1, 50, 50, 50, 1, 10, 50, 60, 1), 2, "4-4-1-1(1)", ((1, 2), (0, 2), (2, 1))),
    "press": ((50, 50, 1, 75, 50, 75, 1, 85, 75, 25, 2), 4, "4-2-3-1(1)", ((0, 2), (2, 2), (2, 2))),
    "longball": ((90, 90, 1, 75, 75, 75, 1, 50, 75, 50, 1), 3, "4-4-2(1)", ((1, 2), (0, 0), (2, 1))),
    "parkbus": ((30, 70, 1, 30, 40, 30, 1, 15, 40, 25, 1), 1, "5-4-1(2)", ((1, 2), (1, 2), (2, 1))),
    "allout": ((80, 45, 2, 80, 70, 85, 2, 75, 70, 70, 2), 5, "4-2-4", ((2, 0), (2, 0), (2, 1))),
}

# Chat cua HLV ao trong tran Rank (OverlayChatScreen: push /match/<id>/messages/latest
# [{type "chat", target, coachname, body, myside}]; chi nghe trong tran co session = Rank). Kich hoat
# tu PUT /match/<id>/gameplay/event/<n> {type STARTHALF | SHOT | GOAL_TYPE_* ..., teamid 0 = minh}.
# Cau thoai: responses/vm_chat.json ("vi"); overlay che chu trong FORBIDDENWORDS_CHAT_ENG.json.
VM_PERSONALITIES = ("friendly", "competitive", "funny", "confident", "playful")
VM_CHAT_ODDS = {"hello": 0.9, "goal": 1, "conceded": 0.85, "miss": 0.5, "save": 0.45, "reply": 0.6}
VM_CHAT_GAP, VM_CHAT_SHOT = 6, 4  # giay giua 2 cau (tru ban thang); giay de 1 cu sut thanh ban
VM_CHAT = {}  # match id -> {"last", "used", "shots"}
VM_CHAT_AFTER = {}  # "last": {vm, outcome} - noi lai khi ve man Rank


# --------------------------------------------------------------------------- settings
def settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        d = d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        d = {}
    return {k: d[k] if isinstance(d.get(k), bool) else v for k, v in SETTINGS_DEFAULT.items()}


def save_settings(d):
    cur = settings()
    cur.update({k: bool(d[k]) for k in SETTINGS_DEFAULT if k in d})
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SETTINGS_FILE)
    return cur


# --------------------------------------------------------------------------- crowd
def vm_day(t=None):
    """So ngay cua dam dong (ngay UTC tron tu VM_EPOCH)."""
    return (int(time.time() if t is None else t) - VM_EPOCH) // 86400


def virtual_managers():
    """Dam dong hien tai: [{_id, name, level, teamid, grade, elo [i], wins [i], losses [i], forfeits [i]}]."""
    now = time.time()
    t = time.gmtime(now)
    return vm_crowd(ladder_season(now)[0], t.tm_mday, t.tm_hour)


@functools.lru_cache(maxsize=2)
def vm_crowd(sid, day, hour):
    """Dam dong o gio `hour` (UTC) ngay `day` cua mua sid: moi HLV choi vai tran Rank moi ngay o gio
    sinh san, gap nguoi gan Elo minh, thang theo xac suat Elo + ky nang rieng (+-60)."""
    first = vm_day(calendar.timegm((sid // 100, sid % 100, 1, 0, 0, 0)))  # ngay dam dong cua ngay 1 mua
    today = first + day - 1
    joined = {m["_id"]: (jd, jh) for jd, jh, m in vm_newcomers(today) if (jd, jh) <= (today, hour)}
    out = copy.deepcopy(vm_base() + [m for _, _, m in vm_newcomers(today) if m["_id"] in joined])
    for vm in out:
        jd, jh = joined.get(vm["_id"], (None, 0))
        skill = random.Random(f"{vm['_id']}/skill").randint(-60, 60)
        for d in range(1 if jd is None else max(1, jd - first + 1), day + 1):
            rnd = random.Random(f"{vm['_id']}/{sid}/{d}")
            for i in range(LADDER_COUNT):
                for _ in range(rnd.choice(VM_GAMES)):
                    at, opp = rnd.choice(VM_HOURS), vm["elo"][i] + rnd.randint(-80, 80)
                    won = rnd.random() < 1 / (1 + 10 ** ((opp - vm["elo"][i] - skill) / 400))
                    if first + d - 1 == jd and at < jh:  # truoc khi gia nhap
                        continue
                    if d == day and i in (0, 3):  # gio choi hom nay: trang thai online (vm_status)
                        vm.setdefault("hours", []).append(at)
                    if d < day or at <= hour:
                        expect = 1 / (1 + 10 ** ((opp - vm["elo"][i]) / 400))
                        vm["elo"][i] = max(LADDER_START, round(vm["elo"][i] + LADDER_K * (won - expect)))
                        vm["wins" if won else "losses"][i] += 1
    return vm_top_fix(out)


@functools.lru_cache(maxsize=None)
def vm_thirds():
    """CLB theo suc manh moi giai (club_rating): moi choi = 1/3 yeu, trung binh = giua, cao thu = manh."""
    thirds = ([], [], [])
    # all_clubs (ca CLB khong co ao): giu nguyen CLB cua cac HLV ao khac; vm_make doi rieng CLB khong co ao
    for _, leagues in hc.all_clubs():
        for lid, teams in leagues:
            if lid == 78:  # doi tuyen quoc gia
                continue
            ranked = sorted(teams, key=lambda t: -hc.club_rating(t))
            for k, t in enumerate(ranked):
                thirds[2 - min(2, 3 * k // len(ranked))].append(t)
    return thirds


def vm_make(rnd, n, name, tier, played=True):
    _, grades, levels, ratings = VM_TIERS[tier]
    thirds = vm_thirds()
    base = rnd.randint(*ratings)
    elo = [max(LADDER_START, base + rnd.randint(-40, 40)) for _ in range(LADDER_COUNT)]
    games = [rnd.randint(10, 60) * (tier + 1) if played else 0 for _ in range(LADDER_COUNT)]
    wins = [round(g * min(0.8, 0.35 + (e - 1000) / 2000)) for g, e in zip(games, elo)]
    forfeits = [rnd.randint(0, g // 25) for g in games]
    pool = thirds[tier] or thirds[0] or [hc.gen_teams.DEFAULT_TEAMS[0]]
    # CLB khong co ao (da voi -> game crash): CLB ke tiep cung nhom co ao, khong boc them (cac HLV khac giu CLB)
    kit = lambda t: next((c for c in pool[pool.index(t):] + pool if hc.has_kit(c)), t)
    return {"_id": f"vm{n:04d}", "name": name, "level": rnd.randint(*levels),
            "teamid": kit(rnd.choice(pool)), "grade": rnd.randint(*grades),
            "elo": elo, "wins": wins, "forfeits": forfeits,
            "losses": [g - w - f for g, w, f in zip(games, wins, forfeits)]}


@functools.lru_cache(maxsize=None)
def vm_base():
    rnd = random.Random(VM_SEED)
    names = rnd.sample(VM_NAMES, VM_COUNT - 1) + [VM_TOP]
    out = []
    for tier, (share, *_) in enumerate(VM_TIERS):
        for _ in range(round(share * VM_COUNT) if tier < 2 else VM_COUNT - len(out)):
            out.append(vm_make(rnd, len(out), names[len(out)], tier))
    top = out[-1]  # VM_TOP: cao thu, CLB manh nhat, the +10 (+9..+12), Elo cao nhat moi che do
    best = max(vm_thirds()[2] or [top["teamid"]], key=hc.club_rating)
    top.update(teamid=best, grade=10, level=100, elo=[1900] * LADDER_COUNT,
               wins=[w * 2 for w in top["wins"]], forfeits=[0] * LADDER_COUNT)
    return out


def vm_is_top(vm):
    return vm.get("name") == VM_TOP


def vm_top_fix(crowd):
    """VM_TOP luon dung dau: Elo moi che do = cao nhat cua nguoi khac + 40..80."""
    top = next((m for m in crowd if vm_is_top(m)), None)
    if top is None:
        return crowd
    rnd = random.Random(f"{top['_id']}/top")
    for i in range(LADDER_COUNT):
        others = max((m["elo"][i] for m in crowd if m is not top), default=LADDER_START)
        top["elo"][i] = max(top["elo"][i], others + rnd.randint(40, 80))
    return crowd


@functools.lru_cache(maxsize=4)
def vm_newcomers(day):
    """[(ngay gia nhap, gio UTC, HLV)] cua nguoi gia nhap ngay 1..day (toi da VM_MAX ca dam dong)."""
    taken = {m["name"] for m in vm_base()}
    free = sorted(n for n in VM_NAMES if n not in taken)
    out = []
    for d in range(1, day + 1):
        rnd = random.Random(f"vmjoin/{d}")
        for _ in range(rnd.choice(VM_JOIN)):
            if VM_COUNT + len(out) >= VM_MAX or not free:
                return out
            tier = rnd.choices(range(len(VM_TIERS)), VM_JOIN_TIER)[0]
            name = free.pop(rnd.randrange(len(free)))
            out.append((d, rnd.choice(VM_HOURS), vm_make(rnd, VM_COUNT + len(out), name, tier, played=False)))
    return out


def vm_by_id(vid):
    return next((m for m in virtual_managers() if m["_id"] == vid), None)


@functools.lru_cache(maxsize=None)
def vm_card_pool():
    """{vai tro (POS_ROLE): [(chi so client, spid, playerid)]} moi the tren Thi truong, moi mua."""
    pool = {}
    with closing(hc._db()) as c:
        for spid, pid, pos, ovr in c.execute(f"SELECT p.spid, p.playerid, p.preferredposition1, {hc.POSOVR} FROM players p"):
            if 0 <= pos < hc.POS_SUB and spid >= 1000000 and hc.on_market(spid):  # bo the mua 0 cua engine (chi so 120+)
                pool.setdefault(hc.POS_ROLE[pos], []).append((ovr, spid, pid))
    return pool


def vm_style(vm):
    if vm_is_top(vm):  # VM_TOP: pressing tam cao, kiem soat tran dau
        return "press"
    return random.Random(f"{vm['_id']}/style").choice(sorted(VM_STYLES))


def style_tactic(name, i=0):
    v, mentality = VM_STYLES[name][:2]
    return {"id": i, "name": "", "teammentality": mentality,
            "bus": {"buildupspeed": v[0], "passing": v[1], "positioning": v[2]},
            "cc": {"passing": v[3], "crossing": v[4], "shooting": v[5], "positioning": v[6]},
            "def": {"mentality": v[7], "aggression": v[8], "teamwidth": v[9], "defenderline": v[10]}}


def style_slots(name):
    """So do cua phong cach thanh 11 o doi hinh [{pos, starting}], None neu bang so do khong co."""
    _, _, formation, part = VM_STYLES[name]
    form = next((f for f in hc.formation_table()["common"] if f["formationname"] == formation), None)
    line = lambda p: (0, 0) if p == 0 else part[0 if p <= 8 else 1 if p <= 19 else 2]
    return form and [{"pos": p, "starting": [*line(p), o["x"], o["y"]]} for p, o in zip(form["positions"], form["offsets"])]


def vm_origin(vid, day):
    return next(((jd, m) for jd, _, m in vm_newcomers(day) if m["_id"] == vid),
                next(((0, m) for m in vm_base() if m["_id"] == vid), None))


def vm_levels(vm):
    """(cap thap nhat, cao nhat) khi tao the: theo Elo, LV 1..5 (moi choi) toi 22..28 (Huyen thoai)."""
    if vm_is_top(vm):  # VM_TOP: cau thu LV 27..30
        return 27, hc.PLAYER_MAX_LEVEL
    top = min(CARD_LEVEL_TOP, 5 + (max(vm["elo"]) - LADDER_START) * (CARD_LEVEL_TOP - 5) // 780)
    return max(1, top - 6), top


def vm_team(vid, day=None):
    return vm_squad(vid, vm_day() if day is None else day)


@functools.lru_cache(maxsize=512)
def vm_squad(vid, day):
    """Doi hinh ngay `day`: so do theo phong cach (else CLB), moi vi tri 1 the ngau nhien moi mua dung
    vai tro, chi so quanh muc theo Elo (64 moi choi -> 82 Huyen thoai), + 7 du bi. Hang the = hang
    HLV +-1 (toi da +2 sau nang cap), cap do theo vm_levels. Moi ngay tu khi gia nhap: chuyen nhuong,
    thu nang cap (ENHANCE_RATE, rot hang tu +ENHANCE_DROP_FROM), len cap."""
    origin = vm_origin(vid, day)
    if origin is None:
        return None
    joined, vm = origin
    rnd, rc = random.Random(vid), random.Random(f"{vid}/cards")
    club = hc.roster_team(vm["teamid"])
    style = vm_style(vm)
    slots = style_slots(style)
    formation = next((f["formationid"] for f in hc.formation_table()["common"]
                      if slots and f["formationname"] == VM_STYLES[style][2]), club["squad"].get("formation"))
    centre = 64 + (max(vm["elo"]) - LADDER_START) * 18 // 780
    lo, top = vm_levels(vm)
    gmax, lmax = min(hc.MAX_GRADE, vm["grade"] + 2), min(hc.PLAYER_MAX_LEVEL, top + 3)
    pool = vm_card_pool()

    def card(slot, r, used):
        role = hc.POS_ROLE[slot["pos"]] if slot["pos"] < hc.POS_SUB else r.choice(("CB", "CM", "ST", "W", "FB"))
        cand = []
        for width in (4, 8, 16, 99):
            cand = [c for c in pool.get(role, ()) if abs(c[0] - centre) <= width and c[2] not in used]
            if cand:
                break
        if not cand:  # vai tro trong (khong the xay ra voi fifadb day du): bat ky the nao
            cand = [c for cs in pool.values() for c in cs if c[2] not in used]
        ovr, spid, pid = r.choice(cand)
        grade = max(1, min(gmax, vm["grade"] + rc.choice((-1, 0, 0, 1))))
        return [slot, ovr, spid, pid, grade, rc.randint(lo, top)]

    chosen = []
    for slot in (slots or [p for p in club["squad"]["positions"] if p.get("pos", hc.POS_SUB) < hc.POS_SUB]) + [{"pos": hc.POS_SUB}] * 7:
        chosen.append(card(slot, rnd, {c[3] for c in chosen}))
    for d in range(joined + 1, day + 1):
        r = random.Random(f"{vid}/life/{d}")
        if r.random() < VM_LIFE[0]:
            k = r.randrange(len(chosen))
            chosen[k] = card(chosen[k][0], r, {c[3] for c in chosen})
        if r.random() < VM_LIFE[1]:
            c = chosen[r.randrange(len(chosen))]
            if c[4] < gmax:
                c[4] = max(1, c[4] + (1 if r.random() < hc.ENHANCE_RATE[c[4]] else -(c[4] >= hc.ENHANCE_DROP_FROM)))
        for c in chosen:
            if c[0]["pos"] < hc.POS_SUB and r.random() < VM_LIFE[2]:
                c[5] = min(lmax, c[5] + 1)
    with closing(hc._db()) as c:
        players = [{"_id": f"{vid}p{n:02d}", "spid": spid, "expandme": "player", "jersey": n + 1, "grade": g,
                    "levelinfo": {"level": lv, "exp": 0}, "jerseyname": hc.gen_teams.jersey_name(c, pid)}
                   for n, (_, ovr, spid, pid, g, lv) in enumerate(chosen)]
    xi = [(slot, ovr, f"{vid}p{n:02d}", spid) for n, (slot, ovr, spid, *_) in enumerate(chosen) if slot["pos"] < hc.POS_SUB]
    bench = [{"pos": hc.POS_SUB, "id": f"{vid}p{n:02d}", "spid": spid}
             for n, (slot, _, spid, *_) in enumerate(chosen) if slot["pos"] >= hc.POS_SUB]
    best = sorted(xi, key=lambda x: -x[1])  # da phat: cau thu ngoai thu mon tot nhat
    outfield = [i for slot, _, i, _ in best if slot["pos"]]
    squad = {"positions": [{**slot, "id": i, "spid": spid} for slot, _, i, spid in xi] + bench,
             "captain": outfield[0], "kickers": (outfield * 5)[:5], "formation": formation}
    return {"teamid": vm["teamid"], "squad": squad, "players": players, "formations": club.get("formations", [])}


def bot_override(team, i):
    """GM Tool > Suc manh BOT (rank1v1 / rankmgr): -1 = giu hang the / cap rieng cua HLV ao."""
    import bot_settings
    bs = bot_settings.for_mode("rankmgr" if i == 3 else "rank1v1")
    g, lv = bs.get("grade", -1), bs.get("level", -1)
    if g < 0 and lv < 0:
        return team
    team["players"] = [{**p, **({"grade": g} if g >= 0 else {}),
                        **({"levelinfo": {"level": lv, "exp": 0}} if lv >= 0 else {})}
                       for p in team.get("players", [])]
    return team


def vm_side(vm, i=None):
    """match.side cua HLV ao: doi hinh (moi the giu hang/cap rieng), chien thuat theo phong cach.
    i = che do Rank (0 = 1vs1, 3 = Gia lap) -> ap dung Suc manh BOT cua GM Tool neu co dat."""
    team = copy.deepcopy(vm_team(vm["_id"]))
    if i is not None:
        team = bot_override(team, i)
    side = hc.match_side(team, vm["teamid"], tactics=[style_tactic(vm_style(vm))],
                         keep_grades=True)
    side["players"] = [{"id": vm["_id"], "name": vm["name"], "levelinfo": {"level": vm["level"], "exp": 0}}]
    return side


def vm_ovr(vid, day=None):
    return vm_ratings(vid, vm_day() if day is None else day)


@functools.lru_cache(maxsize=512)
def vm_ratings(vid, day):
    team = vm_squad(vid, day)
    if team is None:
        return {"FW": 0, "MF": 0, "DF": 0}
    ovr = hc.team_summary(hc.owned_team(copy.deepcopy(team)), team["teamid"]).get("ovr") or {}
    return {k: ovr.get(k, 0) for k in ("FW", "MF", "DF")}


def vm_friends():
    """Moi HLV thu 5 cua dam dong dau la ban (trai deu cac trinh): BXH ban be va Messenger."""
    return virtual_managers()[:VM_COUNT:VM_COUNT // VM_FRIENDS]


def vm_status(vm, hour=None):
    """Trang thai Messenger: "busy" trong gio co tran Rank hom nay, "online" 1 gio truoc/sau, else "offline"."""
    h = time.gmtime().tm_hour if hour is None else hour
    hours = set(vm.get("hours", ()))
    return "busy" if h in hours else "online" if {(h - 1) % 24, (h + 1) % 24} & hours else "offline"


# --------------------------------------------------------------------------- my ladder record
def ladder_season(t=None):
    """(sid, start, next): mua = thang duong lich (sid yyyymm)."""
    y, mo = time.gmtime(t or time.time())[:2]
    nxt = (y + mo // 12, mo % 12 + 1)
    return y * 100 + mo, hc.w3c(calendar.timegm((y, mo, 1, 0, 0, 0))), hc.w3c(calendar.timegm((*nxt, 1, 0, 0, 0)))


def ladder_index(room_type):
    return 3 if "mgr" in str(room_type) else 0


@functools.lru_cache(maxsize=None)
def ladder_divisions():
    with open(os.path.join(hc.RESPONSES, "ladder_division_info.json"), encoding="utf-8") as f:
        d = json.load(f)  # cao nhat truoc
    return d or [{"name": "AMATEUR_C", "rating": 0, "mingames": 10}]


def ladder_division(elo):
    return next((d["name"] for d in ladder_divisions() if elo >= d["rating"]), ladder_divisions()[-1]["name"])


# --------------------------------------------------------------------------- thuong Rank
# Port fo3-server-indo e341d54: /ladder/reward/info {divs [{name DIVISION_<hang>, items [chinh, phu]}],
# wins [{from, items}]} (khung giua man Rank). GM Tool > Xep hang HLV ao ghi gamedata/ladder_rewards.json
# de len responses/ladder_reward_info.json (mac dinh). Truoc chi de xem; nay tra vao Tui:
# - moc so tran thang trong tuan ISO: ladder_total.midrewardable -> client POST /ladder/reward ->
#   {result, rewards {wins [[o vat pham]]}} (LadderWeeklyRewardPopup hien wins[0]);
# - het mua: luc mua moi bat dau, thuong chinh theo hang cua che do diem cao nhat cuoi mua, thuong phu
#   cua moi che do khac da choi. Mua truoc van "rewarded" (popup thuong mua can ban ghi server khong giu).
LADDER_REWARDS_FILE = None  # gamedata/ladder_rewards.json (dat trong install)
DIVISION_VI = {"AMATEUR": "Nghiệp dư", "SEMIPRO": "Bán chuyên", "PROFESSIONAL": "Chuyên nghiệp",
               "WORLDCLASS": "Cấp thế giới", "LEGENDARY": "Huyền thoại"}


def division_vi(name):
    """"LEGENDARY_A" / "DIVISION_LEGENDARY_A" -> "Huyền thoại A"."""
    head, _, tail = str(name).replace("DIVISION_", "").partition("_")
    return (DIVISION_VI.get(head, head.title()) + (" " + tail if tail else "")).strip()


def ladder_rewards():
    for f in (LADDER_REWARDS_FILE, os.path.join(hc.RESPONSES, "ladder_reward_info.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict) and isinstance(d.get("divs"), list) and isinstance(d.get("wins"), list):
                return d
        except (OSError, TypeError, ValueError):
            continue
    return {"divs": [], "wins": []}


def ladder_week(st):
    """Tuan ISO nay: {week, wins, paid [from]} cua cac tran Rank cua toi (tren HLV: mua co the bat dau giua tuan)."""
    week, wk = time.strftime("%G%V", time.gmtime()), hc.my_coach(st).setdefault("ladderweek", {})
    if wk.get("week") != week:
        wk.clear()
        wk.update(week=week, wins=0)
    wk.setdefault("wins", 0)
    wk.setdefault("paid", [])
    return wk


def ladder_week_due(st):
    """Cac moc thang tuan da dat ma chua tra: [{from, items}]."""
    wk = ladder_week(st)
    return [w for w in ladder_rewards()["wins"] if w["from"] <= wk["wins"] and w["from"] not in wk["paid"]]


def ladder_season_pay(st, prev):
    """Thuong mua prev vua xong vao Tui (xem ladder_rewards); None neu khong co gi."""
    games, elo = prev.get("games") or [], prev.get("elo") or []
    played = [i for i, g in enumerate(games) if g and i < len(elo)]
    if not played:
        return None
    divs = {d["name"]: d["items"] for d in ladder_rewards()["divs"]}
    best = max(played, key=lambda i: elo[i])
    items = [it for i in played for it in divs.get(f"DIVISION_{ladder_division(elo[i])}", [])[0 if i == best else 1:][:1]]
    if not items:
        return None
    y, mo = divmod(int(prev.get("sid") or 0), 100)
    return hc.bag_add(st, f"Thưởng mùa Rank {mo}/{y}", items,
                      msg=f"Hạng cao nhất cuối mùa: {division_vi(ladder_division(elo[best]))}.")


def my_ladder(st):
    """Ban ghi mua nay cua HLV dang choi: {sid, elo [i], high [i], wins, losses, draws, games, elos [i] [[elo, ymd]]}."""
    sid = ladder_season()[0]
    coach = hc.my_coach(st)
    lad = coach.setdefault("ladder", {})
    if lad.get("sid") and lad.get("elos"):
        lad["elos"] = [[e if isinstance(e, list) else [e, f"{lad['sid']}01"] for e in h] for h in lad["elos"]]
    if lad.get("sid") != sid or len(lad.get("elo") or ()) != LADDER_COUNT:
        if any(lad.get("games") or ()):  # mua cu: man thanh tich mua truoc, diem cao nhat
            coach["ladderprev"] = copy.deepcopy(lad)
            if lad.get("sid") != sid:  # het mua (khong phai doi so che do cung mua): thuong vao Tui
                ladder_season_pay(st, lad)
            coach["ladderbest"] = [max(b, h) for b, h in zip(coach.get("ladderbest") or lad["high"], lad["high"])]
        lad.clear()
        lad.update(sid=sid, elo=[LADDER_START] * LADDER_COUNT, high=[LADDER_START] * LADDER_COUNT,
                   **{k: [0] * LADDER_COUNT for k in ("wins", "losses", "draws", "games")},
                   elos=[[] for _ in range(LADDER_COUNT)])
    return lad


def ladder_board(st, i):
    """BXH che do i: [(elo, ten, _id)] cao nhat truoc (HLV ao + toi neu da choi)."""
    me = hc.my_coach(st)
    lad = my_ladder(st)
    mine = [(lad["elo"][i], me["name"], me["_id"])] if lad["games"][i] else []
    vms = [(m["elo"][i], m["name"], m["_id"]) for m in virtual_managers()]
    if mine:  # VM_TOP van dung tren toi
        vms = [(max(e, mine[0][0] + 25), n, v) if n == VM_TOP else (e, n, v) for e, n, v in vms]
    return sorted(vms + mine, key=lambda e: -e[0])


def my_rank(st, i):
    me = hc.my_coach(st)["_id"]
    return next((r + 1 for r, e in enumerate(ladder_board(st, i)) if e[2] == me), 0)


def ladder_season_record(lad, rank):
    """1 mua trong statistics.ladder (sid la chuoi: LadderMatchController.setSeasonalRecord so sanh ===)."""
    return {"sid": str(lad["sid"]), "elos": [[{"elo": e, "dateymd": d} for e, d in h] for h in lad["elos"]],
            "wins": lad["wins"], "losses": lad["losses"], "forfeits": [0] * LADDER_COUNT, "rank": rank,
            "recentmatches": lad.get("recent") or [[] for _ in range(LADDER_COUNT)],
            "swins": lad.get("swins") or [0] * LADDER_COUNT, "slosses": lad.get("slosses") or [0] * LADDER_COUNT,
            "division": [{"name": f"DIVISION_{ladder_division(e)}", "rank": r} for e, r in zip(lad["elo"], rank)],
            "highrecord": [{"elo": h, "rank": r} for h, r in zip(lad["high"], rank)]}


def ladder_statistics(st):
    lad, coach = my_ladder(st), hc.my_coach(st)
    rank = [my_rank(st, i) if lad["games"][i] else 0 for i in range(LADDER_COUNT)]
    seasons = [ladder_season_record(lad, rank)]
    prev = coach.get("ladderprev")
    if prev and len(prev.get("elo") or ()) == LADDER_COUNT:
        # rewarded: thuong mua da vao Tui luc mua moi bat dau (ladder_season_pay); thieu thi LadderMatchFrontScreen.checkSeasonReward POST
        # /ladder/reward moi lan vao va mo popup thuong mua (TypeError)
        # mua truoc dat dau: Info Manager / popup ho so lay mua CUOI lam mua hien tai (man Rank tim theo sid)
        seasons.insert(0, {**ladder_season_record(prev, [1 + sum(m["elo"][i] > e for m in virtual_managers()) if g else 0
                                                         for i, (e, g) in enumerate(zip(prev["elo"], prev["games"]))]),
                           "rewarded": True})
    best = [max(b, h) for b, h in zip(coach.get("ladderbest") or lad["high"], lad["high"])]
    return {"seasons": seasons, "highrecords": {"elo": best, "division": [
        {"name": f"DIVISION_{ladder_division(b)}", "rank": r} for b, r in zip(best, rank)]}}, rank


def ladder_my_info(st):
    lad = my_ladder(st)
    sid, start, nxt = ladder_season()
    stats, rank = ladder_statistics(st)
    # LadderMatchController.getCurrentSeasonInfo doc _ladderMyInfo (= body nay) .year .month .sid
    # .title (seasonTitleLabel / Rank front seasonTitle) va .title2 (man gioi thieu tran phan hang:
    # "Mua giai " + title2; thieu -> "Mua giai undefined")
    y, mo = divmod(sid, 100)
    out = {"start": start, "next": nxt, "sid": str(sid), "year": y, "month": mo,
           "title": f"Mùa giải {mo}/{y}", "title2": f"{mo}/{y}"}
    for i, key in LADDER_MY_KEYS.items():
        if lad["games"][i]:
            # firstround: 5 tran xep hang (LadderMatchFrontRecordWindow.setRankedMatchRecord)
            done = (lad.get("results") or [[]] * LADDER_COUNT)[i]
            out[key] = {"elo": lad["elo"][i], "highelo": lad["high"][i], "rank": rank[i],
                        "division": {"name": ladder_division(lad["elo"][i]), "rank": rank[i]},
                        "wins": lad["wins"][i], "losses": lad["losses"][i], "forfeits": 0,
                        "firstround": {"ongoing": lad["games"][i] < LADDER_DEPLOY, "wins": [r == 1 for r in done],
                                       "beforeelo": LADDER_START,
                                       "afterelo": (lad.get("placed") or [None] * LADDER_COUNT)[i] or lad["elo"][i]}}
    played = [i for i in LADDER_MY_KEYS if lad["games"][i]]
    if played:
        best = max(played, key=lambda i: lad["high"][i])
        week = hc.my_coach(st).get("ladderweek") or {}
        wins = week.get("wins", 0) if week.get("week") == time.strftime("%G%V", time.gmtime()) else 0
        out["ladder_total"] = {"highelo": lad["high"][best], "hightype": LADDER_TYPES[best],
                               "playedgames": sum(lad["games"]), "totalwins": wins,
                               "midrewardable": bool(ladder_week_due(st))}  # checkMidReward: POST /ladder/reward
    else:
        # chua choi tran nao: ban ghi "chua co du lieu" (LadderMatchFrontRecordWindow doc
        # _rankedInfo.ladder_total; thieu -> TypeError #1009 - da gap truoc day)
        out["ladder_total"] = {"nodata": True, "highelo": LADDER_START, "hightype": LADDER_TYPES[0],
                               "playedgames": 0, "totalwins": 0, "midrewardable": False}
    return out


def ladder_record(st, i, vm, mine, theirs, sh=0, sa=0):
    """Elo che do i doi sau tran voi vm (K 32); ban ghi tinh tran nay. Luat luan luu (sh:sa) phan dinh hoa."""
    lad = my_ladder(st)
    score = 1 if (mine, sh) > (theirs, sa) else 0 if (mine, sh) < (theirs, sa) else 0.5
    old = lad["elo"][i]
    expect = 1 / (1 + 10 ** ((vm["elo"][i] - lad["elo"][i]) / 400))
    lad["elo"][i] = max(LADDER_START, round(lad["elo"][i] + LADDER_K * (score - expect)))
    lad["high"][i] = max(lad["high"][i], lad["elo"][i])
    lad["games"][i] += 1
    lad[{1: "wins", 0: "losses", 0.5: "draws"}[score]][i] += 1
    day, hist = time.strftime("%Y%m%d", time.gmtime()), lad["elos"][i]
    if hist and hist[-1][1] == day:
        hist[-1][0] = lad["elo"][i]
    else:
        hist.append([lad["elo"][i], day])
    results = lad.setdefault("results", [[] for _ in range(LADDER_COUNT)])
    results[i] = (results[i] + [score])[:LADDER_DEPLOY]
    if lad["games"][i] == LADDER_DEPLOY:
        lad.setdefault("placed", [None] * LADDER_COUNT)[i] = lad["elo"][i]
    now = hc.w3c(time.time())
    if score != 0.5:
        recent = lad.setdefault("recent", [[] for _ in range(LADDER_COUNT)])
        prev = recent[i][-1]["streak"] if recent[i] else 0
        streak = (prev + 1 if prev > 0 else 1) if score == 1 else (prev - 1 if prev < 0 else -1)
        recent[i] = (recent[i] + [{"date": now, "goals": mine, "goalsconceded": theirs, "shootout": sh,
                                   "shootoutconceded": sa, "streak": streak, "elodiff": lad["elo"][i] - old,
                                   "elo": lad["elo"][i], "forfeits": 0}])[-LADDER_RECENT:]
        if sh or sa:
            lad.setdefault("swins" if score == 1 else "slosses", [0] * LADDER_COUNT)[i] += 1
    me = hc.my_coach(st)
    me["multiplay"] = (me.get("multiplay", []) + [{
        "type": LADDER_TYPES[i] if i < len(LADDER_TYPES) else LADDER_TYPES[0], "date": now, "forfeits": 0,
        "team": {"teamid": me["teamid"], "score": mine, "shootout": sh, "coaches": [{"coachid": me["_id"], "name": me["name"]}]},
        "opponent": {"teamid": vm.get("teamid"), "score": theirs, "shootout": sa, "clubname": "",
                     "coaches": [{"coachid": vm.get("_id", ""), "name": vm.get("name", "")}]}}])[-MULTIPLAY_MAX:]
    # Info Manager: so tran Rank, hang cao nhat + chuoi thang dai nhat moi che do
    try:
        r = hc.coach_record(me, "ladder")
        r["count"] = r.get("count", 0) + 1
        run, best, top = (r.setdefault(k, [0] * LADDER_COUNT) for k in ("streak", "winningstreak", "rank"))
        run[i] = run[i] + 1 if score == 1 else 0
        best[i] = max(best[i], run[i])
        pos = next((k for k, e in enumerate(ladder_board(st, i), 1) if e[2] == me["_id"]), 0)
        if pos:
            top[i] = min(top[i] or pos, pos)
    except Exception as e:
        print(f"ladder record: {e!r}", flush=True)
    ladder_week(st)["wins"] += score == 1  # so tran thang tuan nay (ladder_total, moc thuong tuan)
    return score


# --------------------------------------------------------------------------- endpoints: info
def ep_ladder_my_info(query, body):
    return hc._json(ladder_my_info(hc._state()))


def ep_ladder_leaderboard(query, body):
    st = hc._state()
    return hc._json([[{"_id": i_, "name": n, "elo": e, "rank": r + 1} for r, (e, n, i_) in enumerate(ladder_board(st, i)[:10])]
                     for i in range(LADDER_COUNT)])


def ep_ladder_friends(query, body):
    return hc._json([{**{k: m[k] for k in ("_id", "name", "teamid", "elo", "wins", "losses", "forfeits")},
                      "ovr": vm_ovr(m["_id"])} for m in vm_friends()])


def ep_ladder_season_info(query, body):
    sid, start, nxt = ladder_season()
    prev = sid - 1 if sid % 100 > 1 else sid - 89  # 202601 -> 202512
    return hc._json({"sid": sid, "prevsid": prev, "start": start, "end": nxt})


def ep_ladder_reward(query, body):
    """POST /ladder/reward (LadderMatchFrontScreen.checkMidReward): cac moc thang tuan da dat -> Tui.
    Thuong mua tra luc mua moi bat dau (ladder_season_pay); mua truoc danh dau rewarded nen client khong
    goi de nhan thuong mua."""
    st = hc._state()
    due = ladder_week_due(st)
    if not due:
        return hc._json({"result": False, "reason": "LADDER_ALREADY_REWARDED"})
    items = [it for w in due for it in w["items"]]
    for w in due:
        hc.bag_add(st, f"Thưởng Rank tuần: {w['from']} trận thắng", w["items"],
                   msg=f"Đã thắng {w['from']} trận Rank trong tuần này.")
        ladder_week(st)["paid"].append(w["from"])
    hc._save(st)
    return hc._json({"result": True, "rewards": {"wins": [[{**it, "staticdata": hc.item_def(it["itemid"])} for it in items]]}})


def ep_ladder_reward_info(query, body):
    """GET /ladder/reward/info: thuong Rank dang dung (GM Tool sua duoc, xem ladder_rewards)."""
    return hc._json(ladder_rewards())


def ep_contact_list(query, body):
    """GET /contact/list (MessengerProxy): 20 ban va trang thai. newfriends phai la Array khi friends
    khong rong (MessengerProxy.checkFriends). Bo qua group/club/channelid co chu y."""
    if not settings()["enabled"]:
        return hc._json({"friends": [], "newfriends": [], "requesting": [], "requested": [], "groups": []})
    return hc._json({"friends": [{"_id": m["_id"], "coachname": m["name"], "status": vm_status(m), "level": m["level"],
                                  "currdivision": f"DIVISION_{ladder_division(m['elo'][0])}"} for m in vm_friends()],
                     "newfriends": [], "requesting": [], "requested": [], "groups": []})


def presence_loop():
    """Luong: push /presence/<id> cho ban doi trang thai (client ve lai danh sach tu push)."""
    last = {}
    while True:
        try:
            if settings()["enabled"]:
                with hc.STATE_LOCK:
                    now = {m["_id"]: vm_status(m) for m in vm_friends()}
                for vid, s in now.items():
                    if last.get(vid) not in (None, s):
                        hc.messenger_push(f"/presence/{vid}", {"status": s})
                last = now
        except Exception as e:  # giu luong chay
            print(f"presence loop: {e!r}", flush=True)
        time.sleep(60)


def ep_vm_summary(vid, query, body):
    """GET /coach/<vm>/summary (nut Thong tin -> CoachUserPopupScreen): doc mua cuoi va chi so 1on1."""
    vm = vm_by_id(vid)
    if not vm:
        return hc._json({"result": False, "reason": "invalid coach id"})
    now = time.time()
    day = time.strftime("%Y%m%d", time.gmtime(now))
    season = {"sid": str(ladder_season(now)[0]), "wins": vm["wins"], "losses": vm["losses"], "forfeits": vm["forfeits"],
              "elos": [[{"elo": e, "dateymd": day}] for e in vm["elo"]]}
    return hc._json({"coach": {"_id": vm["_id"], "id": vm["_id"], "name": vm["name"], "levelinfo": {"level": vm["level"], "exp": 0},
                               "currentbadge": 0, "playtime": 0, "profile": {"description": "", "views": 0},
                               "statistics": {"ladder": {"seasons": [season]}}}, "friends": VM_FRIENDS})


def ep_vm_ladder_info(vid, query, body):
    vm = vm_by_id(vid)
    if not vm:
        return hc._json({})
    rank = next((r + 1 for r, e in enumerate(ladder_board(hc._state(), 0)) if e[2] == vid), 0)
    return hc._json({"sid": str(ladder_season()[0]), "ladder_pvp_1v1": {"rank": rank, "elo": vm["elo"][0]}})


def ep_my_ladder_info(cid, query, body):
    """GET /ladder/<id cua toi>/info (popup ho so cua chinh minh): hang + Elo Rank 1vs1 mua nay."""
    st = hc._state()
    if not any(str(c.get("_id")) == cid for c in st.get("coaches", [])):
        return hc._json({})
    lad = my_ladder(st)
    return hc._json({"sid": str(ladder_season()[0]),
                     "ladder_pvp_1v1": {"rank": my_rank(st, 0) if lad["games"][0] else 0, "elo": lad["elo"][0]}})


def ep_my_coach(cid, query, body):
    """GET /coach/<id cua HLV dang choi> (kinh lup o o cua minh trong phong Giao huu / Exhibition / Rank ->
    popup Thong tin doi): truoc day khong co route -> {} -> popup trong + loi. Tra nhu ep_vm_coach."""
    st = hc._state()
    slot = next((c["slot"] for c in st.get("coaches", []) if str(c.get("_id")) == cid), None)
    if slot is None:
        return hc._json({})
    me = hc.coach_me(st, slot)
    team = me["team"]
    if "path=team" in query:
        return hc._json(team)
    return hc._json({"id": me["id"], "_id": me["id"], "name": me["name"], "teamid": me["teamid"],
                     "badges": me.get("badges") or [], "currentbadge": me.get("currentbadge") or 0,
                     "levelinfo": me["levelinfo"], "team": team, "kit": team.get("kit") or me.get("kit"),
                     "summary": team.get("summary") or me.get("summary"), "formations": team.get("formations", []),
                     "teamsquadlist": me.get("teamsquadlist") or hc.squad_presets(team)})


def ep_vm_coach(vid, query, body):
    """GET /coach/<vm>?path=team: doi; GET /coach/<vm>: HLV nhu /coach/me (popup doi hinh)."""
    vm = vm_by_id(vid)
    if not vm:
        return hc._json({})
    team = vm_side(vm)["team"]
    if "path=team" in query:
        return hc._json(team)
    # badges: tab Nhiem vu cua popup ho so (CoachAchievementBadgePopup) lap _myBadges = coach.badges;
    # thieu -> TypeError #1009 + popup "chua ho tro offline". HLV ao chua co huy hieu nao.
    return hc._json({"id": vm["_id"], "_id": vm["_id"], "name": vm["name"], "teamid": vm["teamid"],
                     "badges": [], "currentbadge": 0,
                     "levelinfo": {"level": vm["level"], "exp": 0}, "team": team, "kit": team.get("kit"),
                     "summary": team.get("summary"), "formations": team.get("formations", []),
                     "teamsquadlist": team.get("teamsquadlist") or hc.squad_presets(team)})


# --------------------------------------------------------------------------- room / match
def room_player(me):
    return {"id": me["id"], "_id": me["id"], "name": me["name"], "levelinfo": me["levelinfo"], "teamid": me["teamid"],
            "summary": me["summary"], "captain": True}


def ladder_room_reply(room):
    return hc._json({"result": True, "resources": [{"uri": f"/room/{room['roomid']}", "body": room}]})


def room_kind(room_type):
    """Loai phong (RoomController.createARoom: POST /room {type}): ladder (Rank), koth (esports2 =
    King of the Hill), exhibition (pvp_exhibition), vscom (pve*: dau voi may o phong tu tao),
    friendly (Giao huu: dau nhanh / ngau nhien pvp_random / phong tu tao pvp_custom, pvp_referee)."""
    t = str(room_type)
    return ("ladder" if "ladder" in t else "koth" if t == "esports2" else "exhibition" if t == "pvp_exhibition"
            else "vscom" if t.startswith("pve") else "friendly")


def room_slots(room_type, want):
    """So nguoi moi ben: Giao huu / dau voi may luon 1 (ban offline la phien 1 nguoi: minh dieu khien ca
    doi, 2vs2..5vs5 can nguoi that) -> dau tich o man phong luon o 1on1. Rank / KOTH / Exhibition giu nhu client gui."""
    if room_kind(room_type) in ("friendly", "vscom"):
        return 1
    try:
        return max(1, int(want or 1))
    except (TypeError, ValueError):
        return 1


def _room_slotsize(st, room, req):
    """PUT /room/<id>/slotsize {slotsize}: giu 1 (room_slots); phong tra lai lam client ve lai dau tich 1on1."""
    room["slotsize"] = room_slots(room["type"], req.get("slotsize"))


def room_ai_team(room):
    """CLB o o chon may cua phong (chi so vao danh sach SORTNATION / SORTLEAGUE / SORTTEAM cua client, bo 2111)."""
    idx = (room.get("AI") or {}).get("index") or {}

    def pick(xs, k):
        xs = [x for x in xs if int(x) != 2111]
        return xs[max(0, min(int(idx.get(k) or 0), len(xs) - 1))] if xs else None
    nations = hc.club_index()
    nation = pick([n for n, _ in nations], "nation")
    league = pick([lid for lid, _ in dict(nations).get(nation, [])], "league")
    teams = json.loads(hc.ep_sortteam("", b"")[1]).get(str(league)) or [hc.my_teamid(hc._state())]
    return int(pick(teams, "team"))


def room_vm(vm):
    """HLV ao trong phong (o phong tu tao doc summary.id va summary.ovr)."""
    return {"id": vm["_id"], "_id": vm["_id"], "name": vm["name"], "teamid": vm["teamid"],
            "levelinfo": {"level": vm["level"], "exp": 0}, "captain": True,
            "summary": {"id": vm["teamid"], "ovr": vm_ovr(vm["_id"])}}


def room_change(change):
    """Handler PUT /room/<id>/...: change(st, room, req, *groups) tren phong cua toi, tra lai phong."""
    def handler(*args):
        *groups, query, body = args
        st = hc._state(); room = st.get("room")
        if room is None:
            return hc._json({"result": False, "reason": "invalid room"})
        try:
            req = json.loads(body or b"{}")
        except ValueError:
            req = {}
        out = change(st, room, req if isinstance(req, dict) else {}, *groups)
        if st.get("room") is not None or out is not None:
            hc._save(st)
        return out or ladder_room_reply(room)
    return handler


def _room_type(st, room, req):
    """PUT /room/<id>/type {type}: vs Ban / vs May / vs Ngau nhien trong phong tu tao."""
    room["type"] = str(req.get("type") or room["type"])
    room["side"]["left"]["ready"] = room["side"]["right"]["ready"] = False
    if room_kind(room["type"]) == "vscom":
        room["side"]["right"]["players"] = []


def _room_ai(st, room, req):
    """PUT /room/<id>/AI {index}: quoc gia / giai / CLB / do kho (0..4) cua may."""
    room.setdefault("AI", {"index": {}})["index"].update(
        {k: int(v) for k, v in (req.get("index") or {}).items()
         if k in ("nation", "league", "team", "difficulty") and str(v).lstrip("-").isdigit()})


def _room_ready(st, room, req, side):
    """PUT /room/<id>/side/<side>/ready {ready}: San sang voi ban da moi, hoac Bat dau voi may -> 1 giay sau co tran."""
    if side not in room["side"]:
        return hc._json({"result": False, "reason": "invalid side"})
    room["side"][side]["ready"] = ready = bool(req.get("ready", True))
    kind = room_kind(room["type"])
    if ready and side == "left" and room["stage"] == "room" and (
            kind == "vscom" or kind in ("friendly", "exhibition") and room["side"]["right"]["players"]):
        room["search"] = os.urandom(4).hex()
        _later(1, ladder_found, room["search"])


def _room_leave(st, room, req):
    """PUT /room/<id>/leave: roi phong (RoomProxy thay minh khong con trong phong: ON_LEAVE)."""
    st.pop("room", None)
    hc._save(st)
    gone = dict(room, side={**room["side"], "left": {"players": [], "ready": False}})
    return hc._json({"result": True, "resources": [{"uri": f"/room/{room['roomid']}", "body": gone}]})


def _room_invite(st, room, req, vid):
    """POST /room/<id>/invite/<coachid>: ban (HLV ao) vao ben phai sau 2-4 giay, san sang."""
    if not vm_by_id(vid) or room_kind(room["type"]) not in ("friendly", "exhibition"):
        return hc._json({"result": False, "reason": "invalid coach"})
    _later(random.uniform(2, 4), room_join, room["roomid"], vid)
    return hc._json({"result": True})


def room_join(roomid, vid):
    try:
        with hc.STATE_LOCK:
            st = hc._state(); room = st.get("room"); vm = vm_by_id(vid)
            if room and room["roomid"] == roomid and vm and room["stage"] == "room" \
                    and room_kind(room["type"]) in ("friendly", "exhibition"):
                room["side"]["right"].update(players=[room_vm(vm)], ready=True)
                if room_kind(room["type"]) == "exhibition":  # HLV ao tu chon 1 CLB de cu
                    room["side"]["right"]["authTeamId"] = hc.multiplay.exhibition_pick(vid, room["side"]["left"].get("authTeamId"))
                hc._save(st)
                hc.messenger_push(f"/room/{roomid}", room)
    except Exception as e:
        print(f"room join: {e!r}", flush=True)


def ep_room(query, body):
    """POST /room {slotsize, type, properties}: phong cua toi, stage "room"."""
    if not settings()["enabled"]:
        return hc.ep_online_only(query, body)
    req = json.loads(body or b"{}")
    st = hc._state(); me = hc.coach_me(st, hc._default_slot(st))
    rtype = req.get("type", "pvp_ladder_mgr")
    i = ladder_index(rtype)
    room = {"roomid": int(time.time()) % 1000000, "type": rtype, "stage": "room",
            "slotsize": room_slots(rtype, req.get("slotsize")), "option": {}, "publicset": True, "state": None,
            **({"ladder": {"elo": my_ladder(st)["elo"][i]}} if room_kind(rtype) == "ladder" else {}),
            "AI": {"index": {"nation": 0, "league": 0, "team": 0, "difficulty": 2}}, "matchmaking": None,
            "side": {"left": {"players": [room_player(me)], "ready": False}, "center": {"players": []},
                     "right": {"players": [], "ready": False}, "referee": {"players": []}}}
    st["room"] = room
    hc._save(st)
    return ladder_room_reply(room)


def ladder_opponent(st, i, rng=random):
    """1 trong 5 HLV ao co Elo gan minh nhat o che do i."""
    elo = my_ladder(st)["elo"][i]
    return rng.choice(sorted(virtual_managers(), key=lambda m: abs(m["elo"][i] - elo))[:5])


def push_match(match, limit=None):
    """Tran de push (<= 64 KB/1 goi, hc.PUSH_MAX): moi ben chi giu the cua 18 nguoi (reply HTTP sau
    mang du CLB); van qua lon thi bo dan so do / HLV phu / preset (doc lai tu reply HTTP)."""
    limit = hc.PUSH_MAX - 2000 if limit is None else limit
    m = copy.deepcopy(match)
    for side in m["side"].values():
        team = side.get("team") or {}
        squad = {str(p.get("id")) for p in (team.get("squad") or {}).get("positions", [])}
        kept = [p for p in team.get("players", []) if str(p.get("id")) in squad]
        if len(kept) >= len(squad):
            team["players"] = kept
        team.pop("teamsquadlist", None)
    size = lambda: len(json.dumps(m, separators=(",", ":")))
    for key in ("formations", "staffs", "teamcolors"):
        if size() <= limit:
            break
        for side in m["side"].values():
            if key in (side.get("team") or {}):
                side["team"][key] = []
    return m


def ep_matchmaking(query, body):
    """POST /room/<id>/matchmaking: stage searching; vai giay sau gap doi thu (ladder_found push phong
    stage matched + tran trong matchmaking.resources). {_method: DELETE}: ve phong."""
    req = json.loads(body or b"{}")
    st = hc._state(); room = st.get("room")
    if room is None:
        return hc._json({"result": False})
    room["side"]["right"]["players"] = []
    if req.get("_method") == "DELETE" or not settings()["enabled"]:
        room.update(stage="room", matchmaking=None, search=None)
    else:
        room.update(stage="searching", matchmaking=None, search=os.urandom(4).hex())
        t = threading.Timer(random.uniform(*LADDER_SEARCH), ladder_found, (room["search"],))
        t.daemon = True
        t.start()
    hc._save(st)
    return ladder_room_reply(room)


def ep_room_get(rid, query, body):
    """GET /room/<id>: phong hien tai (client lay lai stage neu lo mat 1 push); {_method: DELETE}: bo phong."""
    st = hc._state(); room = st.get("room")
    try:
        req = json.loads(body or b"{}")
    except ValueError:
        req = {}
    if isinstance(req, dict) and req.get("_method") == "DELETE":
        if room and str(room.get("roomid")) == rid:
            st.pop("room", None)
            hc._save(st)
        return hc._json({"result": True})
    if not room or str(room.get("roomid")) != rid:
        return hc._json({})
    if body:
        return ladder_room_reply(room)
    return hc._json(room)


def ep_room_back(query, body):
    """PUT /room/<id>/back (ve man Rank sau tran): phong, /coach/me, /ladder/my/info."""
    st = hc._state(); room = st.get("room")
    res = hc.coach_me_resources(st, hc._default_slot(st)) + [{"uri": "/ladder/my/info", "body": ladder_my_info(st)}]
    if room:
        room.update(stage="room", matchmaking=None, search=None)
        room["side"]["left"]["ready"] = False
        if room_kind(room["type"]) in ("ladder", "koth") or room["type"] == "pvp_random":
            room["side"]["right"]["players"] = []  # ban da moi / may o phong tu tao thi giu lai
        hc._save(st)
        res.append({"uri": f"/room/{room['roomid']}", "body": room})
    vm_say_result()
    return hc._json({"result": True, "resources": res})


def ladder_found(search):
    """Da gap doi thu: phong -> matched (man doi dau), 2 giay sau tran -> stage squad."""
    try:
        with hc.STATE_LOCK:
            st = hc._state(); room = st.get("room")
            if room and room.get("search") == search and (  # chua huy; o phong: San sang chua bo
                    room["stage"] == "searching" or room["stage"] == "room" and room["side"]["left"].get("ready")):
                ladder_match(st, room)
                hc._save(st)
                mid = st["match"]["id"]
                hc.messenger_push(f"/room/{room['roomid']}", dict(room, matchmaking={"resources": [
                    {"uri": f"/match/{mid}", "body": push_match(st["match"])}]}))
                t = threading.Timer(2, ladder_squad, (mid,))
                t.daemon = True
                t.start()
    except Exception as e:
        print(f"ladder found: {e!r}", flush=True)


def ladder_squad(mid):
    try:
        with hc.STATE_LOCK:
            st = hc._state(); match = st.get("match")
            if match and match["id"] == mid and match["stage"] in ("created", "squad"):
                match["stage"] = "squad"
                vm = vm_by_id((st.get("ladder_pending") or {}).get("opponent"))
                for p in match["side"]["right"]["players"]:
                    p["name"] = vm["name"] if vm else "COM"
                match["side"]["right"]["ready"] = True
                hc._save(st)
                hc.messenger_push(f"/match/{mid}", push_match(match))
    except Exception as e:
        print(f"ladder squad: {e!r}", flush=True)


MATCH_TYPES = {"friendly": "Friendly_PvP_1v1", "vscom": "Friendly_PvE_1v1", "koth": "Regional_League",
               "exhibition": "Exhibition_PvP"}


def ladder_match(st, room):
    """Tran vao phong (stage matched): Rank voi HLV ao gan Elo minh nhat; Giao huu voi ban da moi
    hoac 1 HLV ao gan Elo; dau voi may = CLB o o chon may; KOTH = HLV ao cung vung gan diem KOTH;
    Exhibition = moi ben 1 CLB da chon (doi hinh cua CLB do)."""
    mp = hc.multiplay
    i, kind = ladder_index(room["type"]), room_kind(room["type"])
    slot = hc._default_slot(st)
    me = hc.coach_me(st, slot); teamid = me["teamid"]
    user = {"id": me["id"], "name": me["name"], "levelinfo": me["levelinfo"]}
    mine = hc.match_side(st["teams"][str(slot)], teamid, user, hc._tactics(st)[1])
    if kind == "vscom":
        vm, away = None, room_ai_team(room)
        match = hc.new_match("League_2_league", mine, hc.match_side(hc.roster_team(away), away))
    elif kind == "exhibition":
        guest = room["side"]["right"]["players"]
        vm = (vm_by_id(guest[0]["id"]) if guest else None) or ladder_opponent(st, i)
        lt = room["side"]["left"].get("authTeamId") or mp.exhibition_teams()[0]
        away = room["side"]["right"].get("authTeamId") or mp.exhibition_pick(vm["_id"], lt)
        mine = hc.match_side(hc.roster_team(lt), lt, user, [hc.default_tactic(0)])
        theirs = hc.match_side(hc.roster_team(away), away, tactics=[style_tactic(vm_style(vm))])
        theirs["players"] = [{"id": vm["_id"], "name": vm["name"], "levelinfo": {"level": vm["level"], "exp": 0}}]
        mine["team"]["teamcolors"] = []
        match = hc.new_match("League_2_league", mine, theirs)
    else:
        guest = room["side"]["right"]["players"] if kind == "friendly" else []
        vm = (vm_by_id(guest[0]["id"]) if guest else None) or (mp.koth_opponent(st) if kind == "koth" else ladder_opponent(st, i))
        if kind == "koth":
            i = mp.KOTH_LADDER
        match = hc.new_match("League_2_league_MGR" if i == 3 else "League_2_league", mine,
                             vm_side(vm, i if kind == "ladder" else None))
        away = vm["teamid"]
    hc.apply_prematch(st, match)
    if kind in ("ladder", "koth"):
        # hoa sau 90 phut -> 2 hiep phu roi luan luu (Enums.DRAWMODE_EXTRATIME = 1, native end rule 9; ban Indo
        # b2cd8e7). Ket qua luan luu ve matchStats home/away.shootout (ladder_result / koth da tinh)
        match["gamesettings"]["drawMode"] = 1
    # muc Giai dau 10..24 theo Elo doi thu; dau voi may: do kho o chon may 0..4 (Nghiep du..Huyen thoai)
    match["gamesettings"]["difficulty"] = (max(0, min(4, int(room.get("AI", {}).get("index", {}).get("difficulty") or 0)))
                                           if kind == "vscom" else 10 + min(14, (vm["elo"][i] - LADDER_START) // 55))
    # man cai dat Xep hang cho doi san/thoi diem/thoi tiet/thoi luong (MultiplayGameSettingScreen)
    match["gamesettings"].update(stadiumchange=True, timechange=True, weatherchange=True, matchtimechange=True)
    match["leagueparam"] = {"matchtype": "ladder" if kind == "ladder" else "friendly", "round": 0, "home": teamid,
                            "away": away, "leagueid": 0}
    if vm:
        mine_elo = mp.my_koth(st)["elo"] if kind == "koth" else my_ladder(st)["elo"][i]
        match["elo"] = {"left": {"rating": mine_elo}, "right": {"rating": vm["elo"][i]}}
    # loai PvP: MatchController.connectToTheSession mo session native; doc user "ip", "id", "name" la
    # chuoi, "privateip" la mang, va summary.ladderelo (5 Elo) cho loai ladder
    match["type"] = ("Ladder_MGR_PvP_1v1" if i == 3 else "Ladder_PvP_1v1") if kind == "ladder" else MATCH_TYPES[kind]
    match["id"] = str(int(time.time()) % 1000000000)  # relaySessionInfo.matchid = parseInt(resourceId)
    match["relayServer"] = {"address": "127.0.0.1:28010", "forceRelay": True}  # doc, khong ket noi
    for side in match["side"].values():
        # man cai dat Xep hang gui club ? club.clubname : "" vao native (asCString: thieu = crash)
        side["club"] = {"clubname": ""}
        for p in side["team"]["players"]:  # "Lua chon tot nhat" o man doi hinh Rank doc rankedcards.red
            p["rankedcards"] = None
    for p in match["side"]["left"]["players"]:
        p.update(ip="127.0.0.1", privateip=[], summary={"ladderelo": my_ladder(st)["elo"][:5]})
    if kind == "koth":  # diem KOTH cho native (matchstats Regional_League) + the lay the luc tu es2stamina
        for side, elo in (("left", mp.my_koth(st)["elo"]), ("right", vm["elo"][i])):
            for p in match["side"][side]["players"]:
                p["matchstats"] = [{"type": "Regional_League", "elorating": elo}]
            for card in match["side"][side]["team"]["players"]:
                card["es2stamina"] = card.get("stamina", 1)
    # doi thu khong ten khi "created": connectToTheSession bo user khong ten -> session 1 nguoi, AI
    # cua client da ben kia; ladder_squad tra ten + ready lai
    for p in match["side"]["right"]["players"]:
        p.pop("name", None)
    match["side"]["right"]["ready"] = False
    st["match"] = match
    st["ladder_pending"] = {"id": match["id"], "index": i, "opponent": vm["_id"] if vm else None, "kind": kind,
                            "teamid": away, "record": {"vscom": "vscom", "koth": "koth", "exhibition": "exhibition"}.get(
                                kind, "quick" if room["type"] in ("pvp_random", "pvp_quick") else "custom")}
    VM_CHAT_AFTER.clear()
    room["side"]["right"]["players"] = [room_vm(vm)] if vm else []
    room.update(stage="matched", search=None, matchmaking={"resources": [{"uri": f"/match/{match['id']}", "body": match}]})
    return match


def ladder_result(st, mid, req):
    """Tran Rank xong: Elo + ban ghi."""
    p = st.get("ladder_pending") or {}
    if p.get("id") != mid:
        return None
    kind = p.get("kind", "ladder")
    vm = vm_by_id(p.get("opponent")) or {"elo": [LADDER_START] * LADDER_COUNT, "teamid": p.get("teamid")}
    if kind in ("exhibition", "vscom"):  # doi ben kia da CLB da chon / CLB cua may
        vm = {**vm, "teamid": p.get("teamid")}
    h, a = req.get("home") or {}, req.get("away") or {}
    mine, theirs = hc._num(h.get("score")), hc._num(a.get("score"))
    sh, sa = hc._num(h.get("shootout")), hc._num(a.get("shootout"))
    if str(a.get("id")) == str(h.get("id")):  # cung CLB voi doi thu: id khong phan biet duoc -> myTeam
        if req.get("myTeam") == "away":
            mine, theirs, sh, sa = theirs, mine, sa, sh
    elif str(h.get("id")) == str(vm["teamid"]):
        mine, theirs, sh, sa = theirs, mine, sa, sh
    if kind == "ladder":
        score = ladder_record(st, p["index"], vm, mine, theirs, sh, sa)
    else:
        score = 1 if (mine, sh) > (theirs, sa) else 0 if (mine, sh) < (theirs, sa) else 0.5
        hc.multiplay.record_result(st, mid, p, vm, mine, theirs, sh, sa)
    st.pop("ladder_pending", None)
    VM_CHAT.pop(mid, None)
    if "_id" in vm:
        VM_CHAT_AFTER["last"] = {"vm": vm["_id"], "outcome": {1: "loss", 0: "win", 0.5: "draw"}[score]}
    if st.get("room"):
        st["room"].update(stage="room", matchmaking=None)
        if "ladder" in st["room"]:
            st["room"]["ladder"] = {"elo": my_ladder(st)["elo"][p["index"]]}
    return score


def pvp_side_ready(side):
    """Tran Rank: squad -> setting (bo ready cua toi: MatchController.sendReady bo qua ben da ready),
    setting -> playing."""
    def step(match, req):
        ready = bool(req.get("ready", True))
        if side in match["side"]:
            match["side"][side]["ready"] = ready
        if ready and match["stage"] in ("created", "squad"):
            match["stage"] = "setting"
            if side in match["side"]:
                match["side"][side]["ready"] = False
        elif ready and match["stage"] == "setting":
            match["stage"] = "playing"
    return step


# --------------------------------------------------------------------------- chat
@functools.lru_cache(maxsize=1)
def _chat_lines():
    with open(os.path.join(hc.RESPONSES, "vm_chat.json"), encoding="utf-8") as f:
        return json.load(f)["vi"]


def vm_personality(vm):
    return random.Random(vm["_id"]).choice(VM_PERSONALITIES)


def vm_chat_line(vm, trigger, used=()):
    lines = _chat_lines()[trigger][vm_personality(vm)]
    return random.choice([s for s in lines if s not in used] or lines)


def _later(delay, fn, *args):
    t = threading.Timer(delay, fn, args)
    t.daemon = True
    t.start()


def vm_say(mid, vm, trigger, delay):
    if not settings()["chat"]:
        return
    s = VM_CHAT.setdefault(mid, {"last": 0, "used": set(), "shots": {}})
    now = time.monotonic()
    if random.random() >= VM_CHAT_ODDS[trigger] or (trigger not in ("goal", "conceded") and now - s["last"] < VM_CHAT_GAP):
        return
    s["last"] = now
    line = vm_chat_line(vm, trigger, s["used"])
    s["used"].add(line)
    _later(delay, hc.messenger_push, f"/match/{mid}/messages/latest",
           [{"type": "chat", "target": "", "coachname": vm["name"], "body": line, "myside": False}])


def ladder_opponent_of(st, mid):
    p = st.get("ladder_pending") or {}
    return vm_by_id(p.get("opponent")) if p.get("id") == mid else None


def ep_gameplay_event(mid, n, query, body):
    """PUT /match/<id>/gameplay/event/<n> (game tu gui moi tran; chi doc "result")."""
    try:
        ev = json.loads(body or b"{}")
    except ValueError:
        ev = {}
    vm = ladder_opponent_of(hc._state(), mid) if isinstance(ev, dict) else None
    if vm:
        kind, team = str(ev.get("type", "")), ev.get("teamid")
        shots = VM_CHAT.setdefault(mid, {"last": 0, "used": set(), "shots": {}})["shots"]
        if kind == "STARTHALF" and ev.get("halftype") == "FIRST_HALF":
            vm_say(mid, vm, "hello", 3)
        elif kind.startswith("GOAL_TYPE"):
            side = 1 - team if kind == "GOAL_TYPE_OWN_GOAL" and team in (0, 1) else team
            shots.pop(side, None)
            vm_say(mid, vm, "goal" if side == 1 else "conceded", 4)
        elif kind == "SHOT":
            shots[team] = n
            _later(VM_CHAT_SHOT, vm_shot_missed, mid, vm, team, n)
    return hc._json({"result": True})


def vm_shot_missed(mid, vm, team, n):
    try:
        with hc.STATE_LOCK:
            if ladder_opponent_of(hc._state(), mid) and VM_CHAT.get(mid, {}).get("shots", {}).get(team) == n:
                vm_say(mid, vm, "miss" if team == 1 else "save", 0.5)
    except Exception as e:
        print(f"vm chat: {e!r}", flush=True)


def ep_match_messages(mid, query, body):
    """POST /match/<id>/messages {message, target}: dong chat quay lai minh (myside true); tran Rank
    thi HLV ao co the tra loi. Body co "type" la cua game (khong phai go tay)."""
    try:
        req = json.loads(body or b"{}")
    except ValueError:
        req = {}
    if not isinstance(req, dict):
        return hc._json({"result": True})
    text, target = str(req.get("message") or "")[:200], req.get("target") or ""
    st = hc._state()
    if text and "type" not in req:
        hc.messenger_push(f"/match/{mid}/messages/latest", [
            {"type": "chat", "target": target, "coachname": hc.my_coach(st)["name"], "body": text, "myside": True}])
        vm = ladder_opponent_of(st, mid)
        if vm and target != "team":
            vm_say(mid, vm, "reply", 2.5)
    return hc._json({"result": True})


def vm_say_result():
    """Ve man Rank: cau cua HLV ao ve ket qua (banner /alert/<key>/latest, HTML dam 10 giay)."""
    after = VM_CHAT_AFTER.pop("last", None)
    vm = vm_by_id(after["vm"]) if after else None
    if vm and settings()["chat"]:
        line = vm_chat_line(vm, after["outcome"])
        text = f"<FONT COLOR='#ffcc00'>{html.escape(vm['name'])}</FONT>: {html.escape(line, quote=False)}"
        _later(1.5, hc.messenger_push, "/alert/vm/latest", [text])


def warm():
    """Luong khoi dong: dung san dam dong, kho the HLV ao va Team Color (lan dau ~2-3 giay) de luot
    dang nhap / mo Xep hang dau tien khong bi treo."""
    try:
        hc.tc_colors()
        virtual_managers()
        vm_card_pool()
    except Exception as e:
        print(f"rank warm: {e!r}", flush=True)


# --------------------------------------------------------------------------- install
RANK_FEATURES = {"laddermatch": {"vs1": {}, "mgr1": {}}, "messenger": {}}
RANK_MENU = {"ladder1on1": {}, "manager": {}, "ladder": {}}


def install(module):
    global hc, SETTINGS_FILE, LADDER_REWARDS_FILE
    hc = module
    SETTINGS_FILE = os.path.join(hc.HERE, "gamedata", "rank_settings.json")
    LADDER_REWARDS_FILE = os.path.join(hc.HERE, "gamedata", "ladder_rewards.json")
    hc.rank = sys.modules[__name__]
    vid = r"(vm\d{4})"
    hc.ROUTES[:0] = [
        (re.compile(r"/match/(\d+)/gameplay/event/(\d+)"), ep_gameplay_event),
        (re.compile(r"/match/([0-9a-f]+)/messages"), ep_match_messages),
        (re.compile(rf"/coach/{vid}/summary"), ep_vm_summary),
        (re.compile(rf"/ladder/{vid}/info"), ep_vm_ladder_info),
        (re.compile(rf"/coach/{vid}"), ep_vm_coach),
        (re.compile(r"/coach/([0-9a-f]{24})"), ep_my_coach),
        (re.compile(r"/ladder/([0-9a-f]{24})/info"), ep_my_ladder_info),
        (re.compile(rf"/achievement/{vid}"), lambda v, q, b: hc._json({"summary": {"done": 0}})),
        (re.compile(r"/room/\d+/matchmaking"), ep_matchmaking),
        (re.compile(r"/room/\d+/back"), ep_room_back),
        # Giao huu / dau voi may / Exhibition: phong tu tao
        (re.compile(r"/room/\d+/type"), room_change(_room_type)),
        (re.compile(r"/room/\d+/AI"), room_change(_room_ai)),
        (re.compile(r"/room/\d+/side/(\w+)/ready"), room_change(_room_ready)),
        (re.compile(r"/room/\d+/leave"), room_change(_room_leave)),
        (re.compile(r"/room/\d+/invite/(\w+)"), room_change(_room_invite)),
        (re.compile(r"/room/\d+/slotsize"), room_change(_room_slotsize)),
        (re.compile(r"/room/(\d+)/?"), ep_room_get),
        (re.compile(r"/room/\d+/[\w/]+"), room_change(lambda st, room, req: None)),  # slotsize, drawmode...: giu nguyen
        # tran Rank co id so (relaySessionInfo.matchid = parseInt): GET /match/<id>
        (re.compile(r"/match/(\d{1,10})"), hc.ep_match_get),
    ]
    hc.ENDPOINTS.update({
        "/contact/list": ep_contact_list,
        "/ladder/leaderboard": ep_ladder_leaderboard, "/ladder/friends/info": ep_ladder_friends,
        "/ladder/my/info": ep_ladder_my_info, "/ladder/season/info": ep_ladder_season_info,
        "/ladder/reward": ep_ladder_reward, "/room": ep_room, "/matchmaking": ep_matchmaking,
        "/presence/me": lambda q, b: hc._json({"status": "online"}),
    })
    # responses/contact_list.json (danh sach rong cu) khong duoc che ham nay
    hc.DYNAMIC_PATHS.add("/contact/list")
    # thuong Rank: responses/ladder_reward_info.json chi la mac dinh, GM Tool ghi de bang gamedata/ladder_rewards.json
    hc.ENDPOINTS["/ladder/reward/info"] = ep_ladder_reward_info
    hc.DYNAMIC_PATHS.add("/ladder/reward/info")
    hc.BACKGROUND.append(presence_loop)
    hc.BACKGROUND.append(warm)
    # PresenceProxy (moi game) doc /presence/me {status}: "online" = nhan loi moi
    hc.MATCH_END_HOOKS.append(lambda st, mid, req: ladder_result(st, mid, req))

    base_ready, base_coach_me, base_account = hc.ep_match_ready, hc.coach_me, hc.ENDPOINTS["/account"]

    def ep_match_ready(mid, side, query, body):
        if (hc._state().get("ladder_pending") or {}).get("id") == mid:
            return hc.match_step(mid, body, pvp_side_ready(side))
        return base_ready(mid, side, query, body)

    def coach_me(st, slot):
        me = base_coach_me(st, slot)
        if slot == hc._default_slot(st):
            # statistics.ladder: tab thanh tich Rank (LadderMatchController.rebuildLadderRecordInfo doc
            # seasons [{sid ...}] va highrecords {elo [i], division [i]})
            try:
                me["statistics"]["ladder"] = ladder_statistics(st)[0]
            except Exception as e:  # khong de /coach/me hong vi Rank
                print(f"ladder statistics: {e!r}", flush=True)
            me["multiplay"] = hc.my_coach(st).get("multiplay", [])
        return me

    def ep_account(query, body):
        ctype, data = base_account(query, body)
        if not settings()["enabled"]:
            return ctype, data
        acc = json.loads(data)
        feats = acc.setdefault("features", {})
        for k, v in RANK_FEATURES.items():
            feats.setdefault(k, copy.deepcopy(v))
        mm = feats.setdefault("mainmenu", {}).setdefault("multimode", {})
        for k, v in RANK_MENU.items():
            mm.setdefault(k, copy.deepcopy(v))
        return hc._json(acc)

    for pat_fn in hc.ROUTES:  # ep_match_ready nam trong ROUTES (ham da gan san)
        if pat_fn[1] is base_ready:
            hc.ROUTES[hc.ROUTES.index(pat_fn)] = (pat_fn[0], ep_match_ready)
    hc.ep_match_ready, hc.coach_me = ep_match_ready, coach_me
    hc.ENDPOINTS["/account"] = ep_account
