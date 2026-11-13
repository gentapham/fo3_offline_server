"""Manager > Info Manager, Xep hang HLV, Nhiem vu / Thanh tich / Quest (port tu fo3-server-indo #33).

* Info Manager (CoachTotalRecordTabPage) chi ve khi /coach/me co du createdat, playtime, career va
  statistics. coach["record"] luu: Giai dau, Cup, Rank (thang/hoa/thua, ban, chuoi thang), vo dich,
  tien thuong Cup, the mua/ban dat nhat tren thi truong; playtime = khoang cach giua cac yeu cau
  cua game (toi da 20 phut).
* Xep hang HLV (CoachRankingTabPage): gia tri doi 30 ngay + 20 ban (HLV ao) va thay doi tu hom qua.
* Nhiem vu: 8 thanh tich x 5 cap (thuong EP, goi, vat pham; xong ca hang -> huy hieu), nhiem vu
  hang ngay (lam moi 00:00 gio VN), nut Quest: 5 nhiem vu tuan (lam moi thu Hai) thuong GP de doi
  hop qua. Cai dat: gamedata/missions.json (GM Tool > Nhiem vu).
"""
import calendar
import copy
import functools
import json
import os
import random
import re
import sys
import time

hc = None
MISSIONS_FILE = None
MISSIONS_DEFAULT = {"enabled": True, "lang": "vi", "targets": 1.0, "rewards": 1.0}
MISSION_LANGS = {"vi": "Tiếng Việt", "en": "English", "id": "Bahasa Indonesia"}
CUP_LEVELS = 5
LADDER_COUNT = 4

# (id, nhom, bo dem | "level" / "grade" / "value", muc tieu 5 cap, sao)
ACHIEVEMENTS = ((101, "lv", "level", (10, 20, 40, 70, 100), 2), (102, "lv", "match", (10, 50, 150, 400, 1000), 2),
                (103, "lv", "win", (5, 25, 75, 200, 500), 3), (201, "upg", "upgrade", (1, 5, 20, 50, 100), 2),
                (202, "upg", "grade", (3, 4, 5, 6, 7), 4), (301, "tv", "value", (10 ** 7, 10 ** 8, 10 ** 9, 10 ** 10, 10 ** 11), 3),
                (401, "gol", "goal", (10, 50, 150, 400, 1000), 2), (402, "gol", "rankwin", (1, 10, 30, 75, 150), 3))
DAILY = ((901, "match", 3, 30_000), (902, "win", 2, 40_000), (903, "goal", 5, 30_000), (904, "rank", 1, 20_000))
GRADE_REWARDS = ((50_000, None), (100_000, 501), (250_000, 28007), (500_000, 8223601), (1_000_000, 50122))  # EP, vat pham
BADGE_GROUPS = {"lv": (900046, 1, 2), "upg": (900021, 1, 4), "tv": (900038, 1, 3), "gol": (900039, 1, 3)}  # huy hieu, hieu ung, sao
QUESTS = (("match", 10, 30), ("win", 6, 30), ("goal", 15, 25), ("upgrade", 3, 20), ("rank", 5, 25), ("rankwin", 3, 30),
          ("match", 20, 50), ("goal", 30, 45))  # (bo dem, muc tieu, GP): QUEST_COUNT / tuan
QUEST_COUNT = 5
GP_SHOP = ((50021, 40), (501, 20), (8223601, 50), (28007, 30), (50122, 60))  # (vat pham, GP): hop qua cua Quest
GP_SHOPID = 920_000_000
# Trinh sua nhiem vu tuan (GM Tool > Nhiem vu, port fo3-server-indo a0b49dd): danh sach nhiem vu tuan boc tu,
# so nhiem vu / tuan, hop qua GP. Nhiem vu da xong ghi theo bo dem + muc tieu -> sua danh sach khong tra thuong 2 lan.
QUEST_STATS = ("match", "win", "goal", "rank", "rankwin", "upgrade")  # bo dem mission_event giu theo tuan
QUEST_MAX, GP_SHOP_MAX = 6, 5  # cua so Quest hien 6 nhiem vu; 5 hop qua (REWARD_BOX_COUNT)
QUESTS_DEFAULT = [{"stat": s, "todo": t, "gp": g, "name": ""} for s, t, g in QUESTS]
GP_SHOP_DEFAULT = [{"itemid": i, "gp": g} for i, g in GP_SHOP]
MISSION_TEXT = {  # bo dem: {ngon ngu: (ten, muc tieu ({0}), mo ta)}
    "level": {"id": ("Level manajer", "Capai level {0}", "Naikkan level manajer sampai level {0}."),
              "en": ("Manager level", "Reach level {0}", "Raise your manager to level {0}."),
              "vi": ("Cấp HLV", "Đạt cấp {0}", "Nâng HLV lên cấp {0}.")},
    "match": {"id": ("Pertandingan", "Main {0} pertandingan", "Mainkan {0} pertandingan di Liga, Cup atau Rank."),
              "en": ("Matches", "Play {0} matches", "Play {0} matches in League, Cup or Rank."),
              "vi": ("Trận đấu", "Chơi {0} trận", "Chơi {0} trận ở Giải đấu, Cúp hoặc Rank.")},
    "win": {"id": ("Kemenangan", "Menangi {0} pertandingan", "Menangkan {0} pertandingan."),
            "en": ("Wins", "Win {0} matches", "Win {0} matches."), "vi": ("Chiến thắng", "Thắng {0} trận", "Thắng {0} trận.")},
    "goal": {"id": ("Gol", "Cetak {0} gol", "Cetak {0} gol di pertandingan yang kamu mainkan."),
             "en": ("Goals", "Score {0} goals", "Score {0} goals in the matches you play."),
             "vi": ("Bàn thắng", "Ghi {0} bàn", "Ghi {0} bàn trong các trận bạn chơi.")},
    "rank": {"id": ("Rank", "Main {0} pertandingan Rank", "Mainkan {0} pertandingan Rank."),
             "en": ("Rank", "Play {0} Rank matches", "Play {0} Rank matches."), "vi": ("Rank", "Chơi {0} trận Rank", "Chơi {0} trận Rank.")},
    "rankwin": {"id": ("Kemenangan Rank", "Menang {0} kali di Rank", "Menangkan {0} pertandingan Rank."),
                "en": ("Rank wins", "Win {0} Rank matches", "Win {0} Rank matches."),
                "vi": ("Thắng Rank", "Thắng {0} trận Rank", "Thắng {0} trận Rank.")},
    "upgrade": {"id": ("Upgrade berhasil", "Upgrade pemain {0} kali", "Berhasil meng-upgrade pemain {0} kali."),
                "en": ("Successful upgrades", "Upgrade players {0} times", "Upgrade players successfully {0} times."),
                "vi": ("Nâng cấp thành công", "Nâng cấp cầu thủ {0} lần", "Nâng cấp cầu thủ thành công {0} lần.")},
    "grade": {"id": ("Pemain berkelas", "Punya pemain +{0}", "Miliki pemain dengan grade +{0} atau lebih."),
              "en": ("Top grade", "Own a +{0} player", "Own a player of grade +{0} or higher."),
              "vi": ("Thẻ cấp cao", "Sở hữu cầu thủ +{0}", "Sở hữu cầu thủ cấp +{0} trở lên.")},
    "value": {"id": ("Nilai tim", "Nilai tim {0} EP", "Naikkan nilai Tim inti sampai {0} EP."),
              "en": ("Team value", "Team value {0} EP", "Raise your first team's value to {0} EP."),
              "vi": ("Giá trị đội", "Giá trị đội {0} EP", "Nâng giá trị đội hình chính lên {0} EP.")},
}
MISSION_WORDS = {  # ngon ngu: (ten nhom, "Hang ngay", "hom nay", ghi chu lam moi)
    "id": ({"lv": "Manajer", "upg": "Upgrade", "tv": "Nilai tim", "gol": "Gol", "misc": "Misi harian"},
           "Harian", "hari ini", "Direset tiap hari pukul 00:00 WIB."),
    "en": ({"lv": "Manager", "upg": "Upgrades", "tv": "Team value", "gol": "Goals", "misc": "Daily missions"},
           "Daily", "today", "Resets every day at 00:00 (UTC+7)."),
    "vi": ({"lv": "HLV", "upg": "Nâng cấp", "tv": "Giá trị đội", "gol": "Bàn thắng", "misc": "Nhiệm vụ hằng ngày"},
           "Hằng ngày", "hôm nay", "Làm mới mỗi ngày lúc 00:00 giờ Việt Nam."),
}


# --------------------------------------------------------------------------- thanh tich HLV
def coach_record(coach, part):
    return coach.setdefault("record", {}).setdefault(part, {})


def record_match(st, part, gf, ga):
    """1 tran Giai dau / Cup cua toi xong (ban toi gf, ban ho ga)."""
    r = coach_record(hc.my_coach(st), part)
    for k in ("wins", "draws", "losses", "goals", "goalsconceded", "streak", "maxstreak"):
        r.setdefault(k, 0)
    res = "wins" if gf > ga else "losses" if gf < ga else "draws"
    r[res] += 1
    r["goals"] += int(gf)
    r["goalsconceded"] += int(ga)
    r["streak"] = r["streak"] + 1 if res == "wins" else 0
    r["maxstreak"] = max(r["maxstreak"], r["streak"])


def record_trade(coach, kind, spid, amount, now=None):
    """The mua ("buy") / ban ("sell") tren thi truong: so luong + the dat nhat."""
    r = coach_record(coach, "market")
    r[kind] = r.get(kind, 0) + 1
    top = r.get("max" + kind)
    if not top or amount > top.get("amount", 0):
        try:
            name = hc.market.card_name(spid)
        except Exception:
            name = ""
        r["max" + kind] = {"amount": int(amount), "spid": int(spid), "name": name,
                           "date": hc.w3c(time.time() if now is None else now)}


def statistics(st, coach, me):
    """statistics cua /coach/me: Rank (+ so tran, hang cao nhat, chuoi thang), Giai dau, Cup."""
    rec = coach.get("record", {})
    lg, cup, lad = rec.get("league", {}), rec.get("cup", {}), rec.get("ladder", {})
    out = me.get("statistics") or {}
    ladder = out.get("ladder") or {}
    if isinstance(ladder.get("highrecords", {}).get("elo"), list):  # dang cua rank.ladder_statistics
        ladder["highrecords"].update(count=lad.get("count", 0), rank=lad.get("rank", [0] * LADDER_COUNT),
                                     winningstreak=lad.get("winningstreak", [0] * LADDER_COUNT))
    out["ladder"] = ladder
    out["league"] = dict(out.get("league") or {}, **{k: lg.get(k, 0) for k in (
        "wins", "draws", "losses", "goals", "goalsconceded", "normalwins", "noramlbest", "ultimatewins", "ultimatebest")})
    out["league"]["winningstreak"] = {"max": lg.get("maxstreak", 0), "current": lg.get("streak", 0)}
    out["league"].setdefault("leaguewins", [])
    if not out["league"]["leaguewins"]:
        out["league"]["leaguewins"] = [0]
    out["cup"] = {**{k: cup.get(k, 0) for k in ("wins", "draws", "losses", "abandonments")},
                  "cupwins": cup.get("cupwins", [0] * CUP_LEVELS), **({"highrecords": cup["best"]} if cup.get("best") else {})}
    return out


def career(coach, me):
    m = coach.get("record", {}).get("market", {})
    c = dict(me.get("career") or {})
    c.update(clubs=1, maxplayers=len(me["team"]["players"]), buyplayers=m.get("buy", 0), sellplayers=m.get("sell", 0))
    for k in ("maxbuy", "maxsell"):
        if k in m:
            c[k] = m[k]
        else:
            c.pop(k, None)  # khong co = "Chua co giao dich"
    return c


PLAYTIME_GAP = 1200
PLAYTIME = {"last": 0.0, "pending": 0.0}


def note_activity(now=None):
    now = time.time() if now is None else now
    if 0 < now - PLAYTIME["last"] <= PLAYTIME_GAP:
        PLAYTIME["pending"] += now - PLAYTIME["last"]
    PLAYTIME["last"] = now


def fold_playtime(st):
    if PLAYTIME["pending"] >= 1 and st.get("coaches"):
        try:
            coach = hc.my_coach(st)
        except Exception:
            return
        coach["playtime"] = coach.get("playtime", 0) + int(PLAYTIME["pending"])
        PLAYTIME["pending"] -= int(PLAYTIME["pending"])


# --------------------------------------------------------------------------- nhiem vu
def _quests_ok(quests, count, gpshop):
    """Loi (chuoi tieng Viet) neu danh sach nhiem vu tuan / hop qua khong hop le, else None."""
    if not isinstance(quests, list) or not 1 <= len(quests) <= 50:
        return "Danh sách nhiệm vụ tuần phải có 1-50 dòng."
    for q in quests:
        if not isinstance(q, dict) or q.get("stat") not in QUEST_STATS or type(q.get("todo")) is not int \
                or type(q.get("gp")) is not int or not 1 <= q["todo"] <= 10000 or not 1 <= q["gp"] <= 10000 \
                or not isinstance(q.get("name", ""), str) or len(q.get("name", "")) > 60:
            return "Nhiệm vụ không hợp lệ (mục tiêu 1-10.000, GP 1-10.000, tên tối đa 60 ký tự)."
    if type(count) is not int or not 1 <= count <= min(QUEST_MAX, len(quests)):
        return f"Số nhiệm vụ mỗi tuần phải từ 1 đến {min(QUEST_MAX, len(quests))}."
    if not isinstance(gpshop, list) or not 1 <= len(gpshop) <= GP_SHOP_MAX \
            or any(not isinstance(b, dict) or type(b.get("itemid")) is not int or type(b.get("gp")) is not int
                   or not 1 <= b["gp"] <= 100000 for b in gpshop) \
            or len({b["itemid"] for b in gpshop}) < len(gpshop):
        return f"Hộp quà GP phải có 1-{GP_SHOP_MAX} vật phẩm khác nhau, giá 1-100.000 GP."
    return None


def missions_cfg():
    try:
        with open(MISSIONS_FILE, encoding="utf-8") as f:
            cfg = {**MISSIONS_DEFAULT, **json.load(f)}
    except (OSError, ValueError, TypeError):
        cfg = dict(MISSIONS_DEFAULT)
    if cfg.get("lang") not in MISSION_LANGS:
        cfg["lang"] = MISSIONS_DEFAULT["lang"]
    if _quests_ok(cfg.get("quests", QUESTS_DEFAULT), cfg.get("questcount", QUEST_COUNT), cfg.get("gpshop", GP_SHOP_DEFAULT)):
        # file sua tay hong -> danh sach mac dinh
        cfg.update(quests=QUESTS_DEFAULT, questcount=QUEST_COUNT, gpshop=GP_SHOP_DEFAULT)
    cfg.setdefault("quests", QUESTS_DEFAULT)
    cfg.setdefault("questcount", QUEST_COUNT)
    cfg.setdefault("gpshop", GP_SHOP_DEFAULT)
    return copy.deepcopy(cfg)


def _write_missions(changes, drop=()):
    """gamedata/missions.json voi `changes`, giu cac cai dat khac (cai dat chung <-> trinh sua nhiem vu)."""
    try:
        with open(MISSIONS_FILE, encoding="utf-8") as f:
            cur = json.load(f)
        cur = cur if isinstance(cur, dict) else {}
    except (OSError, ValueError):
        cur = {}
    cur = {k: v for k, v in {**cur, **changes}.items() if k not in drop}
    os.makedirs(os.path.dirname(MISSIONS_FILE), exist_ok=True)
    tmp = MISSIONS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=1, ensure_ascii=False)
    os.replace(tmp, MISSIONS_FILE)
    return missions_cfg()


def set_missions(p):
    targets, rewards = float(p.get("targets")), float(p.get("rewards"))
    if p.get("lang") not in MISSION_LANGS or not (0.1 <= targets <= 10 and 0 <= rewards <= 10):
        raise ValueError("bad mission settings")
    cfg = {"enabled": bool(p.get("enabled")), "lang": p["lang"], "targets": targets, "rewards": rewards}
    _write_missions(cfg)  # giu danh sach nhiem vu tuan da sua
    return cfg


def set_quests(p):
    """Trinh sua nhiem vu tuan: {quests [{stat, todo, gp, name}], questcount, gpshop [{itemid, gp}]} | {reset}.
    ValueError(thong bao tieng Viet) khi khong hop le."""
    if p.get("reset"):
        return _write_missions({}, drop=("quests", "questcount", "gpshop"))
    try:
        quests = [{"stat": q["stat"], "todo": int(q["todo"]), "gp": int(q["gp"]), "name": str(q.get("name") or "").strip()}
                  for q in p["quests"]]
        gpshop = [{"itemid": int(b["itemid"]), "gp": int(b["gp"])} for b in p["gpshop"]]
        count = int(p["questcount"])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("Dữ liệu nhiệm vụ tuần không hợp lệ.")
    err = _quests_ok(quests, count, gpshop)
    if err:
        raise ValueError(err)
    bad = [b["itemid"] for b in gpshop if b["itemid"] not in hc.PACKS and b["itemid"] not in hc.ITEMS]
    if bad:
        raise ValueError(f"Vật phẩm {bad[0]} không tồn tại (hộp quà GP chỉ nhận gói / vật phẩm của server).")
    return _write_missions({"quests": quests, "questcount": count, "gpshop": gpshop})


def week_end(now=None):
    """Het tuan nhiem vu: thu Hai 00:00 gio VN (epoch s)."""
    t = time.gmtime((time.time() if now is None else now) + hc.WIB)
    return calendar.timegm((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0)) + (7 - t.tm_wday) * 86400 - hc.WIB


def nice(x):
    x = max(1, int(round(x)))
    try:
        return int(hc.market.nice_price(x))
    except Exception:
        return x


def mission_target(n, cfg):
    return max(1, round(n * cfg["targets"]))


def mission_ep(n, cfg):
    try:
        import price_settings
        mult = price_settings.get()["multiplier"]
    except Exception:
        mult = 1.0
    return nice(n * cfg["rewards"] * mult) if cfg["rewards"] > 0 else 0


def mission_defs(cfg):
    lang = cfg["lang"]
    groups, daily_word, today, reset = MISSION_WORDS[lang]
    out = []
    for aid, group, stat, todos, stars in ACHIEVEMENTS:
        name, short, long = MISSION_TEXT[stat][lang]
        todos = [mission_target(t, cfg) if stat not in ("level", "grade") else t for t in todos]
        rewards = [{"todo": t, "reward": [{"itemid": hc.EP_ITEM, "count": mission_ep(ep, cfg)}]
                    + ([{"itemid": item, "count": 1}] if item and cfg["rewards"] > 0 else [])}
                   for t, (ep, item) in zip(todos, GRADE_REWARDS)]
        out.append(({"id": aid, "name": name, "shortdesc": short, "longdesc": long, "difficulty": stars, "rewards": rewards},
                    group, stat, False))
    for aid, stat, todo, ep in DAILY:
        name, short, long = MISSION_TEXT[stat][lang]
        out.append(({"id": aid, "name": f"{daily_word}: {name}", "shortdesc": f"{short} {today}",
                     "longdesc": f"{short} {today}. {reset}", "difficulty": 1,
                     "rewards": [{"todo": mission_target(todo, cfg), "reward": [{"itemid": hc.EP_ITEM, "count": mission_ep(ep, cfg)}]}]},
                    "misc", stat, True))
    return out


def week_quests(cfg, week_end_ts):
    """Nhiem vu tuan: [(ten, bo dem, muc tieu, GP)], cung tuan + cung danh sach -> cung ket qua. Ten rieng
    (trinh sua) co the ghi muc tieu bang {0}. Danh sach mac dinh boc giong het ban truoc trinh sua."""
    out, pool = [], cfg["quests"]
    for q in random.Random(f"quests/{week_end_ts}").sample(pool, min(cfg["questcount"], len(pool))):
        todo = mission_target(q["todo"], cfg)
        name = (q.get("name") or MISSION_TEXT[q["stat"]][cfg["lang"]][1]).replace("{0}", f"{todo:,}")
        out.append((name, q["stat"], todo, max(1, round(q["gp"] * cfg["rewards"]))))
    return out


def quest_key(stat, todo):
    """Nhiem vu tuan da xong trong coach missions.quests: bo dem + muc tieu (sua danh sach van giu)."""
    return f"{stat}:{todo}"


def gp_shop_prices():
    """{vat pham: GP} cua hop qua cua so Quest (GM Tool sua duoc)."""
    return {b["itemid"]: b["gp"] for b in missions_cfg()["gpshop"]}


def mission_gp(coach):
    m = coach.get("missions", {})
    return m.get("gp", 0) if m.get("week") == week_end() else 0


def missions_state(st, now=None):
    now = time.time() if now is None else now
    m = hc.my_coach(st).setdefault("missions", {})
    day, week = hc.today_wib(now), week_end(now)
    if m.get("day") != day:
        m.update(day=day, daily={}, dailyclaimed=[])
    if m.get("week") != week:
        m.update(week=week, weekly={}, quests={}, gp=0)
    if any(k.isdigit() for k in m.get("quests", {})):  # ban cu: theo vi tri trong danh sach tuan
        qs = week_quests(missions_cfg(), week)
        m["quests"] = {quest_key(*qs[int(k)][1:3]) if k.isdigit() and int(k) < len(qs) else k: v
                       for k, v in m["quests"].items()}
    for k, v in (("total", {}), ("claimed", {}), ("groups", {}), ("badges", []), ("currentbadge", 0)):
        m.setdefault(k, v)
    return m


def mission_event(st, now=None, **counts):
    """Dem luot choi (match, win, goal, rank, rankwin, upgrade); xong nhiem vu tuan -> cong GP."""
    cfg = missions_cfg()
    if not cfg["enabled"]:
        return
    now = time.time() if now is None else now
    m = missions_state(st, now)
    for k, n in counts.items():
        for scope in ("daily", "weekly", "total"):
            m[scope][k] = m[scope].get(k, 0) + int(n)
    for _, stat, todo, gp in week_quests(cfg, m["week"]):
        if quest_key(stat, todo) not in m["quests"] and m["weekly"].get(stat, 0) >= todo:
            m["quests"][quest_key(stat, todo)] = hc.w3c(now)
            m["gp"] += gp


def first_team_value(team):
    return sum(int(p.get("price") or 0) for p in team["players"] if p.get("state") == "first")


def my_team_value(st):
    slot = hc._default_slot(st)
    raw = st["teams"].get(str(slot)) or {"players": [], "squad": {"positions": [], "kickers": []}}
    return first_team_value(hc.owned_team(raw))


def mission_value(st, m, stat, daily):
    if stat == "level":
        return hc.coach_levelinfo(hc.my_coach(st))["level"]
    if stat == "grade":
        team = st["teams"].get(str(hc._default_slot(st))) or {"players": []}
        return max((hc.grade_of(p) for p in team["players"]), default=0)
    if stat == "value":
        return my_team_value(st)
    return m["daily" if daily else "total"].get(stat, 0)


def achievement_me(st, now=None):
    cfg = missions_cfg()
    if not cfg["enabled"]:
        return {"summary": {"done": 0, "total": 0, "rewardable": 0}, "achievement": [], "quest": []}
    m = missions_state(st, now)
    rows, groups = [], {}
    for d, group, stat, daily in mission_defs(cfg):
        todos = [r["todo"] for r in d["rewards"]]
        paid = int(str(d["id"]) in m["dailyclaimed"]) if daily else m["claimed"].get(str(d["id"]), 0)
        grade, cur = min(paid + 1, len(todos)), mission_value(st, m, stat, daily)
        rows.append({"id": d["id"], "aon": True, "complete": paid >= len(todos),
                     "progress": {"grade": grade, "detail": {"current": min(cur, todos[grade - 1]), "total": todos[grade - 1]}},
                     "rewards": {"available": paid < len(todos) and cur >= todos[paid], "grade": paid}})
        groups.setdefault(group, []).append(paid >= len(todos))
    done = sum(r["complete"] for r in rows)
    # muc cua nhom chi khi da co huy hieu (client sang huy hieu + ghi ngay khi co muc nay)
    out = rows + [{"id": g, "complete": True, "completedat": m["groups"][g]} for g in groups if g in m["groups"]]
    quests = [{"id": i, "progress": {"detail": {"current": min(m["weekly"].get(stat, 0), todo)}},
               **({"completedat": m["quests"][quest_key(stat, todo)]} if quest_key(stat, todo) in m["quests"] else {})}
              for i, (_, stat, todo, _) in enumerate(week_quests(cfg, m["week"]))]
    return {"summary": {"done": done, "total": len(rows), "rewardable": sum(r["rewards"]["available"] for r in rows)},
            "achievement": out, "quest": quests}


def achievement_resources(st):
    try:
        return [{"uri": "/achievement/me", "body": achievement_me(st)}] if missions_cfg()["enabled"] else []
    except Exception as e:  # khong lam hong ket qua tran / nang cap
        print(f"achievement: {e!r}", flush=True)
        return []


def match_hook(st, mid, req):
    """Sau moi tran cua toi (Giai dau, Cup, Rank): dem cho nhiem vu."""
    match = st.get("match") or {}
    score = lambda side: hc._num((req.get(side) or {}).get("score"))
    gf = score(req.get("myTeam", "home"))
    rank, won = str(match.get("type", "")).startswith("Ladder"), hc.match_outcome(req) > 0  # thang luan luu = thang
    mission_event(st, match=1, win=int(won), goal=gf, rank=int(rank), rankwin=int(rank and won))


# --------------------------------------------------------------------------- endpoints
def ep_achievement_me(query, body):
    st = hc._state()
    out = achievement_me(st)
    hc._save(st)  # sang ngay / tuan moi
    return hc._json(out)


def ep_achievement_other(cid, query, body):
    """GET /achievement/<HLV khac | chinh toi> (tab Nhiem vu cua popup ho so, CoachAchievementBadgePopup):
    danh sach rong -> TypeError #1009 + popup "chua ho tro offline". Chinh toi: thanh tich that; HLV ao:
    du cac thanh tich, chua lam duoc gi."""
    st = hc._state()
    mine = achievement_me(st)
    me = next((c for c in st.get("coaches", []) if str(c.get("_id")) == cid), None)
    # makeBadgeList: moi huy hieu (/static/db/BADGES asset = nhom lv/upg/tv/gol) doc myAchievement co id = nhom
    # roi .completedat KHONG kiem tra null -> TypeError #1009 neu thieu. Them 1 dong cho moi nhom huy hieu.
    done = missions_state(st).get("groups", {}) if me is not None else {}
    group_rows = [{"id": g, "aon": True, "complete": g in done, "completedat": done.get(g),
                   "progress": {"grade": 1, "detail": {"current": int(g in done), "total": 1}},
                   "rewards": {"available": False, "grade": 0}} for g in BADGE_GROUPS]
    if me is not None:
        return hc._json({"summary": mine["summary"], "achievement": mine["achievement"] + group_rows})
    rows = [{"id": a["id"], "aon": a.get("aon", True), "complete": False,
             "progress": {"grade": 1, "detail": {"current": 0, "total": (a.get("progress") or {}).get("detail", {}).get("total", 1)}},
             "rewards": {"available": False, "grade": 0}} for a in mine.get("achievement", [])]
    return hc._json({"summary": {"done": 0, "total": len(rows), "rewardable": 0}, "achievement": rows + group_rows})


def ep_static_achievement(query, body):
    cfg = missions_cfg()
    if not cfg["enabled"]:
        return hc._json({"list": [], "mixed": {}})
    defs, groups = mission_defs(cfg), MISSION_WORDS[cfg["lang"]][0]
    mixed = {}
    for d, group, _, _ in defs:
        mixed.setdefault(group, []).append(d["id"])
    stars = {g: v[2] for g, v in BADGE_GROUPS.items()}
    return hc._json({"list": [d for d, _, _, _ in defs] + [{"id": g, "name": groups[g], "difficulty": stars.get(g, 1)} for g in mixed],
                     "mixed": mixed})


def ep_badges(query, body):
    return hc._json([{"itemid": b, "category": "BADGE", "itemicon": b, "itemback": "defaultblack", "asset": g, "image_effect": fx}
                     for g, (b, fx, _) in BADGE_GROUPS.items()])


def ep_itemnames(query, body):
    """GET /static/db/ITEMNAMES {itemid: {<ngon ngu>: ten}}: ten vat pham trong popup thuong."""
    ids = [hc.EP_ITEM, *hc.PACKS, *hc.ITEMS, *hc.PACKAGES, *hc.MONTHLY]
    out = {}
    for i in ids:
        try:
            n = hc.item_def(i)["name"]
        except Exception:
            continue
        out[str(i)] = {k: n for k in ("eng", "ind", "kor", "vie", "vn", "tha", "chi")}
    return hc._json(out)


def ep_quest_bank(query, body):
    cfg, end = missions_cfg(), week_end()
    expire = [hc.w3c(end), hc.w3c(end + 7 * 86400)]
    if not cfg["enabled"]:
        return hc._json({"expire": expire, "current": [], "next": []})
    bank = lambda e: [{"name": name, "todo": todo, "reward": {"count": gp}, "platform": 1}
                      for name, _, todo, gp in week_quests(cfg, e)]
    return hc._json({"expire": expire, "current": bank(end), "next": bank(end + 7 * 86400)})


def gp_shop_list():
    out = []
    for itemid, gp in gp_shop_prices().items():
        e = hc.shop_entry(itemid, "GPSHOP", GP_SHOPID + itemid)
        sd = e["product"]["staticdata"]
        sd["bunches"] = [{"bunch": 1, "price": gp}]
        if not os.path.isfile(os.path.join(hc.ASSETS, "externalAssets", "shop", f"Q{itemid}.png")):
            # vat pham GM them chua co anh Q<id> rieng: anh cua vat pham, dem vien cho dung khung cua so Nhiem vu
            # (hc.QUEST_ICON, ban Indo 6c8323d)
            icon = sd.get("bundleicon", sd.get("itemicon", itemid))
            sd["itemicon"] = sd["bundleicon"] = f"{hc.QUEST_ICON}{icon}"
            out.append(e)
            continue
        # anh rieng cho cua so Quest (shop/Q<id>.png): hinh thu nho sat tren, chua cho ten vat pham game in de
        # len 1/4 duoi o anh
        sd["itemicon"] = sd["bundleicon"] = f"Q{itemid}"
        for it in sd.get("items") or ():
            if it.get("itemid") == itemid and isinstance(it.get("staticdata"), dict):
                it["staticdata"] = dict(it["staticdata"], itemicon=f"Q{itemid}", bundleicon=f"Q{itemid}")
        out.append(e)
    return out


def ep_gp_shop(query, body):
    return hc._json(gp_shop_list() if missions_cfg()["enabled"] else [])


def ep_achievement_reward(aid, grade, query, body):
    st = hc._state(); cfg = missions_cfg(); slot = hc._default_slot(st)
    me = achievement_me(st)
    row = next((r for r in me["achievement"] if str(r["id"]) == aid), None)
    found = next(((d, group, daily) for d, group, _, daily in mission_defs(cfg) if str(d["id"]) == aid), None)
    if not cfg["enabled"] or row is None or found is None or "rewards" not in row \
            or int(grade) != row["rewards"]["grade"] + 1 or not row["rewards"]["available"]:
        return hc._json({"result": False, "reason": "ACHIEVEMENT_REWARDS_ERROR"})
    d, group, daily = found
    m, coach = missions_state(st), hc.my_coach(st)
    reward = d["rewards"][row["rewards"]["grade"]]["reward"]
    for r in reward:
        if r["itemid"] == hc.EP_ITEM:
            hc.add_ep(st, r["count"])
        else:
            hc.inventory_add(coach, r["itemid"], r["count"])
    if daily:
        m["dailyclaimed"].append(aid)
    else:
        m["claimed"][aid] = row["rewards"]["grade"] + 1
    after = achievement_me(st)
    ids = {d["id"] for d, g, _, _ in mission_defs(cfg) if g == group}
    if group in BADGE_GROUPS and BADGE_GROUPS[group][0] not in m["badges"] \
            and all(r["complete"] for r in after["achievement"] if r["id"] in ids):
        m["groups"][group] = hc.w3c(time.time())
        m["badges"].append(BADGE_GROUPS[group][0])
        after = achievement_me(st)
    hc._save(st)
    return hc._json({"result": True, "rewards": [{**r, "staticdata": hc.item_def(r["itemid"])} for r in reward],
                     "resources": [{"uri": "/achievement/me", "body": after}] + hc.coach_me_resources(st, slot)})


def ep_badge_use(itemid, query, body):
    st = hc._state()
    m = missions_state(st)
    if int(itemid) and int(itemid) not in m["badges"]:
        return hc._json({"result": False, "reason": "INVALID_PARAM"})
    m["currentbadge"] = int(itemid)
    hc._save(st)
    return hc._json({"result": True, "resources": hc.coach_me_resources(st, hc._default_slot(st))})


# --------------------------------------------------------------------------- xep hang HLV
TV_DAYS = 30


@functools.lru_cache(maxsize=256)
def vm_value(vid, day):
    try:
        return first_team_value(hc.owned_team(copy.deepcopy(hc.rank.vm_squad(vid, day))))
    except Exception:
        return 0


def tv_history(st):
    coach, today = hc.my_coach(st), hc.rank.vm_day()
    hist = {int(d): v for d, v in coach.get("tvhist", {}).items() if int(d) > today - TV_DAYS}
    hist[today] = my_team_value(st)
    coach["tvhist"] = {str(d): v for d, v in hist.items()}
    last, out = hist[min(hist)], []
    for d in range(today - TV_DAYS + 1, today + 1):
        last = hist.get(d, last)
        out.append(last)
    return out


def ep_tv_history(query, body):
    st = hc._state()
    values = tv_history(st)
    hc._save(st)
    return hc._json({"values": values})


def ep_rank_friends(query, body):
    st = hc._state()
    coach, day = hc.my_coach(st), hc.rank.vm_day()
    me, hist = hc.coach_me(st, coach["slot"]), tv_history(st)
    hc._save(st)
    rows = [(m["_id"], m["name"], m["level"], hc.rank.vm_ovr(m["_id"]), vm_value(m["_id"], day), vm_value(m["_id"], day - 1))
            for m in hc.rank.vm_friends()]
    ovr = (me.get("summary") or {}).get("ovr") or {}
    rows.append((coach["_id"], coach["name"], me["levelinfo"]["level"],
                 {k: ovr.get(k, 0) for k in ("FW", "MF", "DF")}, hist[-1], hist[-2]))
    before = {r[0]: k for k, r in enumerate(sorted(rows, key=lambda r: (-r[5], -r[2])), 1)}
    return hc._json([{"_id": i_, "name": n, "level": lv, "ovr": o, "teamvalue": {"val": v}, "rank": k, "fluc": before[i_] - k}
                     for k, (i_, n, lv, o, v, _) in enumerate(sorted(rows, key=lambda r: (-r[4], -r[2])), 1)])


def ep_hiderank(query, body):
    st = hc._state()
    hc.my_coach(st)["hiderank"] = bool(json.loads(body or b"{}").get("hiderank"))
    hc._save(st)
    return hc._json({"result": True, "resources": hc.coach_me_resources(st, hc._default_slot(st))})


def ep_my_summary(cid, query, body):
    """GET /coach/<id cua toi>/summary: ho so cua chinh toi (nut Thong tin o hang cua toi)."""
    st = hc._state()
    coach = hc.my_coach(st)
    if coach["_id"] != cid:
        return hc._json({"result": False, "reason": "invalid coach id"})
    me = hc.coach_me(st, coach["slot"])
    return hc._json({"coach": {"_id": cid, "id": cid, **{k: me.get(k) for k in ("name", "levelinfo", "currentbadge", "playtime", "profile")},
                               "statistics": {"ladder": me["statistics"].get("ladder", {})}}, "friends": hc.rank.VM_FRIENDS})


def install(module):
    global hc, MISSIONS_FILE
    hc = module
    hc.manager = sys.modules[__name__]
    MISSIONS_FILE = os.path.join(hc.HERE, "gamedata", "missions.json")
    hc.coach_record, hc.record_match, hc.record_trade = coach_record, record_match, record_trade
    hc.mission_event, hc.achievement_resources = mission_event, achievement_resources
    hc.MATCH_END_HOOKS.append(match_hook)

    base_save, base_coach_me, base_account = hc._save, hc.coach_me, hc.ENDPOINTS["/account"]
    base_profile, base_purchase = hc.ENDPOINTS.get("/coach/me/profile"), hc.ENDPOINTS["/shop/purchase"]

    def _save(st):
        fold_playtime(st)
        return base_save(st)

    def coach_me(st, slot):
        me = base_coach_me(st, slot)
        coach = hc.coach_of(st, slot)
        try:
            me["statistics"] = statistics(st, coach, me)
            me["career"] = career(coach, me)
            me["_id"] = coach["_id"]
            me["hiderank"] = coach.get("hiderank", False)
            me["createdat"] = (me.get("career") or {}).get("appat") or coach.get("createdat") or hc.w3c(time.time())
            me["playtime"] = int(coach.get("playtime", 0)) + (int(PLAYTIME["pending"]) if slot == hc._default_slot(st) else 0)
            prof = coach.get("profile") if isinstance(coach.get("profile"), dict) else {}
            me["profile"] = {"description": prof.get("description", ""), "views": 0, **(me.get("profile") or {})}
            m = coach.get("missions", {})
            me["gpdata"] = {"point": mission_gp(coach), "until": hc.w3c(week_end())}
            me["badges"], me["currentbadge"] = m.get("badges", []), m.get("currentbadge", 0)
        except Exception as e:  # khong de /coach/me hong vi thanh tich
            print(f"manager coach_me: {e!r}", flush=True)
        return me

    def ep_account(query, body):
        ctype, data = base_account(query, body)
        if not missions_cfg()["enabled"]:
            return ctype, data
        acc = json.loads(data)
        acc.setdefault("features", {}).setdefault("freeroambuttons", {})["quest"] = {}
        return hc._json(acc)

    def ep_coach_profile(query, body):
        """PUT /coach/me/profile {description} | {picture}: loi gioi thieu + anh HLV."""
        req = json.loads(body or b"{}")
        if isinstance(req.get("description"), str) and req.get("picture") in (None, ""):
            st = hc._state()
            coach = hc.my_coach(st)
            prof = coach.get("profile") if isinstance(coach.get("profile"), dict) else {}
            prof["description"] = req["description"][:200]
            coach["profile"] = prof
            hc._save(st)
            return hc._json({"result": True, "resources": hc.coach_me_resources(st, hc._default_slot(st))})
        return base_profile(query, body)

    def ep_shop_purchase(query, body):
        """Hop qua cua Quest (shopid >= GP_SHOPID): tra bang GP."""
        req = json.loads(body or b"{}")
        shopid = int(req.get("shopid") or 0)
        gp = shopid >= GP_SHOPID and gp_shop_prices().get(shopid - GP_SHOPID)
        if not gp:
            return base_purchase(query, body)
        st = hc._state(); slot = hc._default_slot(st)
        m = missions_state(st)
        if m["gp"] < gp:
            return hc._json({"result": False, "reason": "NOT_ENOUGH_GP"})
        m["gp"] -= gp
        itemid = shopid - GP_SHOPID
        if itemid == hc.EP_ITEM:
            hc.add_ep(st, 1)
        else:
            hc.inventory_add(hc.my_coach(st), itemid, 1)
        hc._save(st)
        return hc._json({"result": True, "resources": hc.coach_me_resources(st, slot)})

    hc._save, hc.coach_me = _save, coach_me
    hc.ENDPOINTS["/account"] = ep_account
    if base_profile:
        hc.ENDPOINTS["/coach/me/profile"] = ep_coach_profile
    hc.ENDPOINTS["/shop/purchase"] = ep_shop_purchase
    hc.ROUTES[:0] = [
        (re.compile(r"/coach/([0-9a-f]{24})/summary"), ep_my_summary),
        (re.compile(r"/achievement/(\d+)/reward/(\d+)"), ep_achievement_reward),
        (re.compile(r"/achievement/(vm\d{4}|[0-9a-f]{24})"), ep_achievement_other),
        (re.compile(r"/badge/(\d+)/use"), ep_badge_use),
    ]
    eps = {"/coach/me/teamvalue/history": ep_tv_history, "/rank/teamvalue/friend": ep_rank_friends,
           "/rank/teamvalue/club": lambda q, b: hc._json([]), "/coach/me/hiderank": ep_hiderank,
           "/achievement/me": ep_achievement_me, "/static/info/ACHIEVEMENT": ep_static_achievement,
           "/static/db/BADGES": ep_badges, "/static/db/ITEMNAMES": ep_itemnames, "/static/info/request": ep_quest_bank,
           "/shop/list/GPSHOP": ep_gp_shop}
    hc.ENDPOINTS.update(eps)
    hc.DYNAMIC_PATHS.update(eps)  # responses/achievement_me.json, static_info_request.json khong che
    hc.NOTE_ACTIVITY = note_activity
