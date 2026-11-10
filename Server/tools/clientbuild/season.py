"""FO4 26 LIVE (300) / 25 LIVE (325) -> FO3 offline. Reimplementation of the reviewed reference
tools/fo4_season.py (fo3-indo-server repo), adapted to this install; works only on COPIES.

    python -I season.py build      fo4_cards.json + orig/fifadb.sqlite -> build/fifadb.sqlite
    python -I season.py client     build/fifadb.sqlite + orig/data_0.big, orig/Label.big -> build/data_0.big, build/Label.big
    python -I season.py verify     checks build/* (decrypt back, integrity, sizes)
"""
import collections, hashlib, io, json, os, re, shutil, sqlite3, struct, sys, unicodedata, zlib
from contextlib import closing

HERE = os.path.dirname(os.path.abspath(__file__))
W = HERE  # Server/tools/clientbuild: orig/ (data_0.big, Label.big goc), build/, icons/, pylib/
sys.path.insert(0, HERE)
sys.path.append(os.path.join(W, "pylib"))  # PIL

SERVER = os.path.dirname(os.path.dirname(HERE))
GAME_DATA = os.path.join(os.path.dirname(SERVER), "data")  # data_3/4 cua game (mat that, giay)
ORIG = os.path.join(W, "orig")
BUILD = os.environ.get("FO3_BUILD") or os.path.join(W, "build")  # FO3_*: chi de test tren ban sao
CARDS = os.path.join(W, "fo4_cards.json")
FIFADB = os.environ.get("FO3_FIFADB") or os.path.join(SERVER, "gamedata", "fifadb.sqlite")  # DB cua server (chi doc)
CARD_EDITS = os.environ.get("FO3_CARD_EDITS") or os.path.join(SERVER, "gamedata", "card_edits.json")  # mat 3D muon
OWN_SEASONS = os.environ.get("FO3_OWN_SEASONS") or os.path.join(SERVER, "gamedata", "seasons.json")  # mua rieng
OWN_ICONS = os.environ.get("FO3_OWN_ICONS") or os.path.join(SERVER, "gamedata", "season_icons")
PIDS = os.path.join(W, "build", "fo4_pids.json")
HEADS = os.path.join(W, "build", "fo4_heads.json")
SEASON_NAMES = {300: "26 LIVE", 325: "25 LIVE"}
SEASONS = tuple(SEASON_NAMES)
ICON_ONLY = (94, 76, 18, 19)  # PRIME, INFINITY (prime_infinity.py), 26 TOTY 18, 26 TOTS 19 (add_toty.py): chi them icon (icon goc FO4 30x24)
NEW_PID = 700000
NAME_FIXES = (239085, 215441, 1397, 20801, 239, 34079, 999906, 999908)  # + Zidane (the WL moi), C. Ronaldo (ten ngan)
REST_OF_WORLD = (999998, 9998)
# FO4's renamed / re-id'd clubs FO3 has (reference): Lombardia FC = Inter, Bergamo Calcio = Atalanta, SS Lazio = Lazio
FO3_CLUB = {131682: 44, 115845: 39, 115841: 46}
with open(os.path.join(SERVER, "responses", "static_db_PLAYERGRADE.json"), encoding="utf-8") as _f:
    PLUS1 = json.load(_f)["1"]["boost"]["PLAYER_ALLATTRIBUTES_ALL"]

# POSOVR exactly as http_config.py builds it (copied source, the server file is not imported)
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
POSOVR = "(CASE p.preferredposition1 {} END)".format(" ".join(
    f"WHEN {i} THEN ({' + '.join(f'{w} * p.{k}' for k, w in ROLE_WEIGHTS[r].items())}) / 100" for i, r in enumerate(POS_ROLE)))

POS = {"GK": 0, "SW": 1, "RWB": 2, "RB": 3, "CB": 5, "LB": 7, "LWB": 8, "CDM": 10, "RM": 12, "CM": 14,
       "LM": 16, "CAM": 18, "RF": 20, "CF": 21, "LF": 22, "RW": 23, "ST": 25, "LW": 27}
WORKRATE = {"mid": 0, "medium": 0, "low": 1, "high": 2}
REPUTATION = {"regular player": 1, "famous player": 2, "top class": 3, "world class": 4, "legendary": 5}
STATS = ("acceleration aggression agility balance ballcontrol crossing curve dribbling finishing freekickaccuracy "
         "gkdiving gkhandling gkkicking gkpositioning gkreflexes headingaccuracy jumping longpassing longshots "
         "marking penalties positioning reactions shortpassing shotpower slidingtackle sprintspeed stamina "
         "standingtackle strength interceptions vision volleys").split()
NAME_TABLES = ("nameskor", "nameschi", "namestha", "namesvie", "nameseng", "namesind")
PERM_TABLES = ("permkr", "permcn", "permth", "permvn", "permsg", "permid")
FO3_TRAITS = ("inflexibility long-throw-in takes-powerful-driven-free-kicks diver injury-prone solid-player "
              "avoids-using-weaker-foot dives-into-tackles tries-to-beat-offside-trap selfish leadership "
              "argues-with-officials early-crosser finesse-shot flair long-passer long-shot-taker speed-dribbler "
              "play-maker pushes-up-for-corners puncher gk-long-thrower power-header gk-one-on-ones giant-throw-in "
              "outside-foot-shot crowd-favourite swerve second-wind acrobatic-clearance").split()
# FO4 trait slug -> FO3 trait (filled from the collected slugs, see TRAIT_MAP in build)
FO3_BADGES = ("poacher speedster aerial-threat dribbler playmaker box-to-box distance-shooter crosser "
              "fk-specialist tackling tactician agile strength clinical-finisher complete-defender "
              "complete-midfielder complete-forward").split()


def badges(r):
    at = lambda **kv: all(r[k] >= v for k, v in kv.items())
    b = {"poacher": at(finishing=77, positioning=73, headingaccuracy=81),
         "speedster": r["acceleration"] + r["sprintspeed"] >= 154,
         "aerial-threat": at(headingaccuracy=77, strength=69),
         "dribbler": at(dribbling=73, balance=63),
         "playmaker": at(vision=73, shortpassing=74, longpassing=71),
         "box-to-box": r["stamina"] >= 73 and r["attackingworkrate"] == 2 == r["defensiveworkrate"],
         "distance-shooter": at(longshots=72, shotpower=74, ballcontrol=57),
         "crosser": at(crossing=73, curve=68),
         "fk-specialist": at(freekickaccuracy=73, ballcontrol=64, crossing=58),
         "tackling": at(slidingtackle=73, standingtackle=72),
         "tactician": at(reactions=68, interceptions=73),
         "agile": r["agility"] >= 76,
         "strength": at(strength=73, weight=83),
         "clinical-finisher": at(finishing=73, longshots=68)}
    b["complete-defender"] = b["tackling"] and b["tactician"] and (b["aerial-threat"] or b["agile"] or b["strength"])
    b["complete-midfielder"] = b["playmaker"] and (b["crosser"] or b["tackling"] or b["clinical-finisher"])
    b["complete-forward"] = b["clinical-finisher"] and b["speedster"] + b["aerial-threat"] + b["dribbler"] + b["strength"] >= 2
    return sum(1 << i for i, k in enumerate(FO3_BADGES) if b[k])


def plain(s):
    return "".join(ch for ch in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(ch)) \
        .replace("ø", "o").replace("Ø", "O").replace("ł", "l").replace("Ł", "L").replace("ß", "ss") \
        .replace("æ", "ae").replace("Æ", "AE").replace("đ", "d").replace("Đ", "D").replace("ı", "i")


def _key(s):
    return re.sub(r"[^a-z ]", "", plain(s).lower()).strip()


def _date(fo4):
    d, m, y = (int(x) for x in fo4.split("."))
    return y, m, d


def _same_name(a, b):
    import difflib  # chi che do build (ghep the); launcher dong goi khong co difflib (che do client)
    a, b = _key(a), _key(b)
    if not a or not b:
        return 0.0
    ta, tb = a.split(), b.split()
    it = iter(tb)
    inside = all(any(w.startswith(x) for w in it) for x in ta)
    return (ta[-1] == tb[-1] or inside) + difflib.SequenceMatcher(None, a, b).ratio()


def trait_map(cards):
    """FO4 trait slug -> FO3 trait name. FO4's slugs are '<group>-<name>' (e.g. shooting-finesse-shot);
    a slug maps when its name part (or the whole slug) is one of FO3's 30 trait slugs."""
    out = {}
    alias = {"long-shot": "long-shot-taker", "playmaker": "play-maker", "gk-long-throw": "gk-long-thrower",
             "long-throw": "long-throw-in", "power-free-kick": "takes-powerful-driven-free-kicks",
             "powerful-free-kick": "takes-powerful-driven-free-kicks", "speed-dribbler-ai": "speed-dribbler",
             "early-crosser-ai": "early-crosser", "flair-passes": "flair", "giant-throw": "giant-throw-in",
             "one-club-player": None, "chip-shot": None}
    for c in cards.values():
        for slug in c.get("traits", []):
            if slug in out:
                continue
            hit = None
            parts = slug.split("-")
            for i in range(len(parts)):
                tail = "-".join(parts[i:])
                tail_noai = re.sub(r"-ai$", "", tail)
                for t in (tail, tail_noai):
                    if t in FO3_TRAITS:
                        hit = t
                        break
                    if t in alias:
                        hit = alias[t]
                        break
                if hit or tail in alias:
                    break
            out[slug] = hit
    return out


def cmd_build(a):
    cards = json.load(open(CARDS, encoding="utf-8"))
    os.makedirs(BUILD, exist_ok=True)
    shutil.copyfile(os.path.join(ORIG, "fifadb.sqlite"), FIFADB)
    pids = {}
    c = sqlite3.connect(FIFADB); c.row_factory = sqlite3.Row
    cols = [r[1] for r in c.execute("PRAGMA table_info(players)")]
    assert not c.execute(f"SELECT COUNT(*) FROM players WHERE spid / 1000000 IN (300, 325)").fetchone()[0]
    assert not c.execute("SELECT COUNT(*) FROM nameseng WHERE playerid BETWEEN ? AND ?", (NEW_PID, NEW_PID + 99999)).fetchone()[0]
    eng = sqlite3.connect(os.path.join(BUILD, "eng_orig.sqlite"))
    clubs = {r[0] for r in eng.execute("SELECT teamid FROM team")}
    leagues = {r[0] for r in eng.execute("SELECT leagueid FROM league")}
    team_names = {r[0]: r[1] for r in eng.execute("SELECT teamid, teamname FROM team")}
    eng.close()
    team_league = {}
    for t, l, n in c.execute("SELECT teamid, leagueid, COUNT(*) FROM players GROUP BY teamid, leagueid ORDER BY 3"):
        team_league[t] = l  # most common last
    nations = {r[0] for r in c.execute("SELECT DISTINCT nationality FROM players")}
    natteams = {r[0] for r in c.execute("SELECT DISTINCT nationalteamid FROM players")}
    born, best, bdate = collections.defaultdict(list), {}, {}
    names = {r[0]: (r[1], r[2]) for r in c.execute("SELECT playerid, full_eng, short_eng FROM nameseng")}
    by_full, by_short = collections.defaultdict(set), collections.defaultdict(set)
    for r in c.execute("SELECT * FROM players ORDER BY spid"):
        pid, season = r["playerid"], r["spid"] // 1000000
        m = re.match(r"(\d+)/(\d+)/(\d+)", r["birthdate"] or "")
        if m and pid not in best:
            born[(int(m[3]), int(m[1]), int(m[2]))].append(pid)
            bdate[pid] = (int(m[3]), int(m[1]), int(m[2]))
        rank = (season == 88, season == 0, season)
        if pid not in best or rank > best[pid][0]:
            best[pid] = (rank, r)
    for p in bdate:
        if p in names:
            by_full[_key(names[p][0])].add(p)
            by_short[_key(names[p][1])].add(p)
    generic = collections.defaultdict(list)
    for r in c.execute("SELECT * FROM players WHERE spid / 1000000 = 88 AND headclasscode = 1 ORDER BY spid"):
        generic[r["skintonecode"]].append(r)
    tmap = trait_map(cards)
    found = new = 0
    heads, report = {}, {"matched": [], "new": [], "rest_of_world": collections.Counter(), "unmapped_traits": [],
                         "nation_unknown": collections.Counter(), "league_fixed": collections.Counter()}
    report["unmapped_traits"] = sorted(s for s, t in tmap.items() if not t)
    for uid, card in sorted(cards.items(), key=lambda kv: (kv[1]["season"], kv[0])):
        db, attr, season = card["db"], card["attr"], card["season"]
        when = _date(db["birthdate"])
        cands = [(max(_same_name(db["name"], names.get(p, ("", ""))[0]), _same_name(db["name_short"], names.get(p, ("", ""))[1])), p)
                 for p in born.get(when, []) if p < NEW_PID]
        score, pid = max(cands, default=(0, None))
        how = "birthdate+name"
        if score < 0.85:
            # FIFA 17 data often had a placeholder / wrong birthdate (Konsa, Guehi, Raphinha): same full name
            # within a year, or same short name in the same year with the same month or day; unique only
            kf, ks = _key(db["name"]), _key(db.get("name_short") or "")
            alt = {p for p in by_full.get(kf, ()) if abs(bdate[p][0] - when[0]) <= 1}
            alt |= {p for p in by_short.get(ks, ()) if bdate[p][0] == when[0] and (bdate[p][1] == when[1] or bdate[p][2] == when[2])}
            if len(alt) == 1:
                pid, score, how = alt.pop(), 1.0, "name+year"
        person = f"{'-'.join(map(str, when))}|{_key(db['name'])}"
        if score >= 0.85:
            base = dict(best[pid][1])
            found += 1
            report["matched"].append((uid, db["name"], names[pid][0], round(score, 2), how, "-".join(map(str, when)),
                                      "-".join(map(str, bdate[pid]))))
        else:
            pid = pids.get(person) or max([NEW_PID - 1, *pids.values()]) + 1
            pids[person] = pid
            tone = int(db.get("skintonecode") or 2)
            near = min(generic, key=lambda t: (abs(t - tone), t))
            pool = generic[near]
            base = dict(pool[int(hashlib.md5(person.encode()).hexdigest(), 16) % len(pool)])
            heads[pid] = base["playerid"]
            base.update(trait1=0, trait2=0)
            new += 1
            report["new"].append((uid, db["name"], pid))
        spid = season * 1000000 + pid
        p1 = db.get("pos1")
        poss = [POS[p1]] if p1 in POS else []
        assert poss, (uid, p1)
        fo4_tid = int(db.get("team_id") or 0)
        tid = FO3_CLUB.get(fo4_tid, fo4_tid)
        if tid in clubs and tid != REST_OF_WORLD[0]:
            team = tid
            lid = int(db.get("league_id") or 0)
            if lid not in leagues:
                report["league_fixed"][(lid, team_names.get(team))] += 1
                lid = team_league.get(team, lid)
            league = lid
        else:
            team, league = REST_OF_WORLD
            report["rest_of_world"][f"{db.get('team_name')} ({fo4_tid})"] += 1
        nat = int(db["nation_id"])
        if nat not in nations:
            report["nation_unknown"][(nat, db.get("nation_name"))] += 1
        nsq = int(db.get("nation_squad_id") or 0)
        base.update({k: max(1, min(99, int(attr[k]))) for k in STATS})
        traits = sorted({tmap[t] for t in card.get("traits", []) if tmap.get(t)})
        base.update(spid=spid, playerid=pid, birthdate=f"{when[1]}/{when[2]}/{when[0]} 12:00:00 AM",
                    height=max(150, min(213, int(db["height"]))), weight=max(50, min(115, int(db["weight"]))),
                    preferredfoot=2 if db.get("foot_pref") == "left" else 1,
                    weakfootabilitytypecode=max(1, min(5, int(db.get("foot_weak") or 3))),
                    skillmoves=max(0, min(4, int(db.get("skill_level") or 1) - 1)),
                    attackingworkrate=WORKRATE.get(db.get("workrate_att"), 0), defensiveworkrate=WORKRATE.get(db.get("workrate_def"), 0),
                    internationalrep=REPUTATION.get((db.get("reputation") or "").lower(), 1),
                    nationality=nat, leagueid=league, teamid=team,
                    nationalteamid=nsq if nsq in natteams else -1,
                    trait1=sum(1 << FO3_TRAITS.index(t) for t in traits),
                    contractvaliduntil=2027, playerjointeamdate="Tue, 01 Jul 2025 00:00:00 GMT", historyopen=0,
                    **{f"preferredposition{i + 1}": (poss[i] if i < len(poss) else -1) for i in range(4)})
        base["overallrating"] = base["potential"] = 0  # set below from POSOVR
        base["badges"] = badges(base)
        c.execute(f"INSERT INTO players ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [base[k] for k in cols])
        for t in PERM_TABLES:
            c.execute(f"INSERT INTO {t} (spid, licensed, transferable) VALUES (?, 1, 1)", (spid,))
        if pid >= NEW_PID:
            full, short = plain(db["name"]), plain(db.get("name_short") or db["name"])
            jersey = short.split(". ", 1)[-1]
            for t in NAME_TABLES:
                c.execute(f"INSERT OR REPLACE INTO {t} (playerid, full, short, full_eng, short_eng, jersey) VALUES (?,?,?,?,?,?)",
                          (pid, full, short, full, short, jersey))
    c.execute(f"UPDATE players AS p SET overallrating = {POSOVR} + ?1, potential = {POSOVR} + ?1 "
              f"WHERE spid / 1000000 IN ({','.join(str(s) for s in SEASONS)})", (PLUS1,))
    c.commit()
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.close()
    json.dump(pids, open(PIDS, "w"), indent=0)
    json.dump(heads, open(HEADS, "w"))
    report = {k: (dict((str(kk), vv) for kk, vv in v.most_common()) if isinstance(v, collections.Counter) else v) for k, v in report.items()}
    report["trait_map"] = tmap
    json.dump(report, open(os.path.join(BUILD, "build_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    per = collections.Counter(card["season"] for card in cards.values())
    print(f"{len(cards)} cards ({', '.join(f'{SEASON_NAMES[s]} {n}' for s, n in sorted(per.items()))}): "
          f"{found} matched FO3 players, {new} new -> {FIFADB}")


# --------------------------------------------------------------------------- client
FIFADB_ENTRY = "data/db2/fifadb.db.db2"
LOCALES = ("chi", "eng", "ind", "kor", "tha", "vie")
NG_ENTRY, NG_META = "data/db/fifa_ng_db.db", "data/db/fifa_ng_db-meta.xml"
ENGINE_COPY = ("height", "weight", "nationality", "preferredfoot")
LABEL_GFX, LABEL_DDS = "data/ui/components/Label.gfx", "data/ui/components/Label_I1.dds"
TMP = os.path.join(BUILD, "tmp_edit.sqlite")


def _sqlite(plain_db, edit):
    open(TMP, "wb").write(plain_db)
    c = sqlite3.connect(TMP)
    edit(c)
    c.commit()
    c.execute("VACUUM")
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.close()
    data = open(TMP, "rb").read()
    os.remove(TMP)
    assert len(data) % 8 == 0
    return data


def _plain(shipped):
    from extract_fifadb import decrypt
    db = decrypt(bytes(shipped))
    db[:16] = b"SQLite format 3\x00"
    return bytes(db)


def _seal(plain_db, shipped):
    from refpack import refpack_blocks
    from extract_fifadb import swap32, KEY
    from cryptography.hazmat.primitives.ciphers import Cipher, modes
    try:
        from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish
    except ImportError:
        from cryptography.hazmat.primitives.ciphers.algorithms import Blowfish
    e = Cipher(Blowfish(KEY), modes.ECB()).encryptor()
    enc = bytearray(swap32(e.update(swap32(plain_db)) + e.finalize()))
    enc[:16] = shipped[:16]
    return refpack_blocks(bytes(enc)), len(enc)


CLIENT_REGION_PERM = "permid"  # client chay voi region ID (launch args): chi doc perm<REGION> nay


def client_db(trim):
    def edit(c):
        if trim:
            for t in NAME_TABLES:
                c.execute(f"DELETE FROM {t} WHERE playerid NOT IN (SELECT playerid FROM players)")
        # Su nghiep CLB toi mua hien tai (gamedata/club_history.json tu Wikidata, tools/clientbuild/club_history.py;
        # theo ban Indo 836c8ce / 80ff39b): tab Dac diem cua the liet ke "Su nghiep CLB" tu playerhistory, chi
        # voi the historyopen = 1 -> mo cho moi the co dong lich su. Bang perm cua 5 vung khac client khong
        # doc: xoa de fifadb van vua cho cua no trong data_0.big.
        import club_history
        if club_history.load():
            club_history.apply(c)
            c.execute("UPDATE players SET historyopen = 1 WHERE playerid IN (SELECT DISTINCT playerid FROM playerhistory)")
            for t in PERM_TABLES:
                if t != CLIENT_REGION_PERM:
                    c.execute(f"DELETE FROM {t}")
    return _sqlite(open(FIFADB, "rb").read(), edit)


TEAMNAMES_ENTRY = "data/ui/localization/TEAMNAMES_ENG.json"


# ten them rieng tung goi ngon ngu (khong co trong club_history): doi tuyen Viet Nam (113666, goc "VN STAR")
EXTRA_TEAMNAMES = {"tha": {"113666": "เวียดนาม"}}


def build_teamnames():
    """build/eng.big + build/tha.big = goi ngon ngu cua game + ten cac CLB moi cua club_history.json (id 200000000 +
    so Q) va EXTRA_TEAMNAMES: client goi ten CLB bang Localization.TEAMNAME (TEAMNAMES_<LANG>.json trong <lang>.big;
    id la -> "teamname(<id>)"). eng: tieng Viet / tieng Anh (ca hai la language "eng"), tha: tieng Thai (i18n).
    Chi them / sua cac id moi nen chay lai nhieu lan van giu nguyen ten khac (vd Viet Nam cua eng.big).
    Tra ve so CLB moi (None neu khong dung gi)."""
    import bigdir, club_history
    from refpack import refpack
    teams = (club_history.load() or {}).get("teams") or {}
    made = None
    for lang in ("eng", "tha"):
        src = os.path.join(GAME_DATA, "ui", "localization", f"{lang}.big")
        out = os.path.join(BUILD, f"{lang}.big")
        extra = EXTRA_TEAMNAMES.get(lang, {})
        entry = f"data/ui/localization/TEAMNAMES_{lang.upper()}.json"
        if not (teams or extra) or not os.path.isfile(src):
            if os.path.exists(out):
                os.remove(out)
            continue
        raw = bigdir.read(src, entry)
        names = json.loads(raw.decode("utf-8-sig"))
        want = {**{t: n["eng"] for t, n in teams.items()}, **extra}
        if all(names.get(t) == v for t, v in want.items()):
            new = raw
        else:
            keep = {k: v for k, v in names.items() if int(k) < club_history.NEW_TEAM}
            new = club_history.teamnames({**keep, **extra}, "eng", teams)
        shutil.copyfile(src, out)
        if new != raw:
            bigdir.patch(out, {entry: (refpack(new), len(new))})
            assert json.loads(bigdir.read(out, entry).decode("utf-8-sig")) == json.loads(new.decode("utf-8-sig"))
        made = len(teams)
    return made

def locale_additions():
    with closing(sqlite3.connect(FIFADB)) as c:
        rows = c.execute("SELECT playerid, full, short, full_eng, short_eng, jersey FROM nameseng "
                         "WHERE playerid BETWEEN ? AND ?", (NEW_PID, NEW_PID + 99999)).fetchall()
        # ten FIFA 17 viet sai, sua theo ten that (FO4): Haland -> Haaland, Sehrou -> Serhou Guirassy
        return rows + c.execute("SELECT playerid, full, short, full_eng, short_eng, jersey FROM nameseng "
                                "WHERE playerid IN (%s)" % ",".join(map(str, NAME_FIXES))).fetchall()


def locale_db(plain_db, names, trim):
    cards = None
    if trim:
        with closing(sqlite3.connect(FIFADB)) as c:
            cards = [r[0] for r in c.execute("SELECT DISTINCT playerid FROM players")]

    def edit(c):
        c.executemany("INSERT OR REPLACE INTO names (playerid, full, short, full_eng, short_eng, jersey) VALUES (?,?,?,?,?,?)", names)
        if cards:
            c.execute("CREATE TEMP TABLE keep (playerid INTEGER PRIMARY KEY)")
            c.executemany("INSERT INTO keep VALUES (?)", [(p,) for p in cards])
            c.execute("DELETE FROM names WHERE playerid NOT IN (SELECT playerid FROM keep)")
    return _sqlite(plain_db, edit)


ENGINE_MAX_ROWS = 64300  # dong 3D + 560 (maxinsert) <= 65535, else game treo luc tai (2026-11-08)
NG_MAX = 99999999  # truong spid/playerid 27 bit
FACE = ("headassetid", "headtypecode", "headclasscode", "hashighqualityhead", "headscalecode", "skintonecode",
        "skintypecode", "eyecolorcode", "eyebrowcode")
LOOK = ("hairtypecode", "haircolorcode", "facialhairtypecode", "facialhaircolorcode", "skintonecode", "eyecolorcode",
        "bodytypecode")


def _json_or(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def card_looks(t, fld, by, pk):
    """Dong 3D tu GM Tool > Database: the cua mua rieng = ban sao dong cua the goc (trong y het),
    the sua mat ("look": {"face": dong khac, truong LOOK: gia tri}) = dong rieng theo spid (tao tu dong
    cua cau thu neu chua co). Giu ENGINE_MAX_ROWS. Tra ve so dong them / sua."""
    sp = fld["spid"]

    def put(row, name, v):
        sn, lo = fld[name]
        return t.set(row, sn, v - lo) if 0 <= v - lo < 1 << t.fields[sn][2] else row

    def own(key, src):
        if key in by:
            return True
        base = by.get(src, by.get(src % 1000000))
        if base is None or key > NG_MAX or len(by) >= ENGINE_MAX_ROWS:
            return False
        by[key] = t.set(t.set(base, pk, key), sp[0], key - sp[1])
        return True
    n = 0
    for s in _json_or(OWN_SEASONS, {}).values():
        for spid, src in (s.get("cards") or {}).items():
            spid = int(spid)
            if spid not in by:
                n += own(spid, int(src.get("from", spid % 1000000)))
    for spid, e in _json_or(CARD_EDITS, {}).items():
        look, spid = e.get("look"), int(spid)
        if not look or not own(spid if spid <= NG_MAX else spid % 1000000, spid % 1000000):
            continue
        key = spid if spid in by else spid % 1000000
        row, face = by[key], by.get(look.get("face"))
        for name in FACE if face is not None else ():
            sn = fld[name][0]
            ty, bo, dep = t.fields[sn]
            row = t.set(row, sn, int.from_bytes(face, "little") >> bo & (1 << dep) - 1)  # bit tho (ca so thuc)
        for name in LOOK:
            if name in look:
                row = put(row, name, int(look[name]))
        by[key] = row
        n += 1
    return n


PHOTO_ORIG = os.path.join(ORIG, "data_7.big")  # data_7.big goc cua game (5 KB, file locale ini)


def build_photo_heads(log):
    """Mat 3D tu anh (GM Tool > Database, photo_head.py): build/data_7.big = orig/data_7.big + file
    cua tung mat (dau + toc cua donor doc tu data_3/4/6.big cua game, mat <id>.rx3 da ma hoa san ->
    khong can numpy). Khong co mat nao -> xoa build/data_7.big (apply.py tra lai ban goc cho game).
    Tra ve {player id: (id dau moi, dong engine donor, headassetid donor)} cua cac mat da vao data_7."""
    import photo_head
    out = os.path.join(BUILD, "data_7.big")
    heads = photo_head.load()
    if heads and not os.path.isfile(PHOTO_ORIG):
        log.append(f"photo heads: {len(heads)} bo qua (thieu {os.path.relpath(PHOTO_ORIG, W)})")
        heads = {}
    data, ids = photo_head.archive(os.path.dirname(GAME_DATA), PHOTO_ORIG, heads) if heads else (None, [])
    if not ids:
        if os.path.exists(out):
            os.remove(out)
        if heads:
            log.append(f"photo heads: 0/{len(heads)} vao data_7.big (thieu file donor / mat)")
        return {}
    with open(out + ".tmp", "wb") as f:
        f.write(data)
    os.replace(out + ".tmp", out)
    got = {int(heads[i]["pid"]): (i, int(heads[i]["row"]), int(heads[i]["donor"])) for i in ids}
    log.append(f"data_7.big: {len(ids)}/{len(heads)} photo heads, {os.path.getsize(PHOTO_ORIG)} -> {len(data)} B")
    return got


def photo_heads(t, fld, by, heads):
    """Dong engine cua mat 3D tu anh (nhu photo_heads cua fo4_season): dong player id cua cau thu va
    cac dong khac cua anh ay dang mang dau chung (headclasscode 1) mac dau cua donor (truong FACE)
    voi headassetid moi. Khong them dong (ENGINE_MAX_ROWS). heads = build_photo_heads(). Tra ve so dong."""
    if not heads:
        return 0
    ha, hc = fld["headassetid"], fld["headclasscode"]
    real = {}  # headassetid -> mot dong dau scan (donor chon o dong khong con / chua co)
    for k in sorted(by, key=lambda k: (k >= 1000000, k)):
        if t.get(by[k], hc[0]) + hc[1] == 0:
            real.setdefault(t.get(by[k], ha[0]) + ha[1], by[k])
    faces = {}
    for pid, (hid, row, donor) in heads.items():
        face = by.get(row)
        if face is None or t.get(face, ha[0]) + ha[1] != donor or t.get(face, hc[0]) + hc[1] != 0:
            face = real.get(donor)
        if face is not None:
            faces[pid] = (hid, face)
    n = 0
    for k in [k for k in by if k % 1000000 in faces]:
        hid, face = faces[k % 1000000]
        if k < 1000000 or t.get(by[k], hc[0]) + hc[1] == 1:
            row = by[k]
            for name in FACE:
                sn = fld[name][0]
                ty, bo, dep = t.fields[sn]
                row = t.set(row, sn, int.from_bytes(face, "little") >> bo & (1 << dep) - 1)  # bit tho (ca so thuc)
            by[k] = t.set(row, ha[0], hid - ha[1])
            n += 1
    return n


def engine_db(db, meta, photo=None):
    import eadb
    heads = {int(k): v for k, v in json.load(open(HEADS)).items()}
    m = re.search(r'<table name="players" shortname="(\w+)".*?</table>', meta, re.S)
    fld = {n: (sn, int(lo)) for n, sn, lo in re.findall(r'<field name="(\w+)" shortname="(\w+)"[^>]*rangelow="(-?\d+)"', m.group())}
    base, names, raws = eadb.db_tables(db)
    i = names.index(m.group(1).encode())
    t = eadb.Table(raws[i])
    pk = fld["playerid"][0]
    by = {t.get(r, pk): r for r in t.rows}
    generic = next(r for r in t.rows if t.get(r, fld["headclasscode"][0]) == 1)
    with closing(sqlite3.connect(FIFADB)) as c:
        c.row_factory = sqlite3.Row
        mine = {r["playerid"]: r for r in c.execute("SELECT * FROM players WHERE playerid BETWEEN ? AND ? ORDER BY spid",
                                                    (NEW_PID, NEW_PID + 99999))}
    missing_head = 0
    for pid, r in mine.items():
        assert pid not in by, pid
        src = by.get(heads.get(pid))
        if src is None:
            missing_head += 1
            src = generic
        row = t.set(src, pk, pid)
        for name in ENGINE_COPY:
            sn, lo = fld[name]
            v = r[name] - lo
            if 0 <= v < 1 << t.fields[sn][2]:
                row = t.set(row, sn, v)
        by[pid] = row
    # The cua game goc thieu dong 3D (Beckham 10000250, Carragher...: chon la crash o changeRight3DPlayerOnTheFly):
    # khoa = spid (the theo mua, nhu cac dong mua 10 khac) hoac playerid (the mua 0). Co file mat
    # data/sceneassets/heads/head_<id>_0.rx3 trong data_4.big -> mat that (headclasscode 0), khong -> mat chung.
    heads4 = {n for n, o, s, u in __import__("bigdir").entries(os.path.join(GAME_DATA, "data_4.big"))
              if n.startswith("data/sceneassets/heads/head_")}
    fill = {fn: fld[fn] for fn in fld if fn not in ("playerid", "spid", "headassetid")}
    added_old = real = fixed_shoes = 0
    shoes = set()
    for _b in ("data_2.big", "data_3.big", "data_4.big", "data_5.big", "data_6.big", "data_1.big"):
        shoes |= {n for n, o, s_, u in __import__("bigdir").entries(os.path.join(GAME_DATA, _b)) if n.startswith("data/sceneassets/shoe/")}
    assert "data/sceneassets/shoe/shoe_29.rx3" in shoes
    with closing(sqlite3.connect(FIFADB)) as c:
        c.row_factory = sqlite3.Row
        cols = {d[1] for d in c.execute("PRAGMA table_info(players)")}
        old = [r for r in c.execute("SELECT * FROM players WHERE playerid < ? ORDER BY spid", (NEW_PID,))
               if r["spid"] not in by and r["playerid"] not in by]
    tmpl = next(r for r in t.rows if 10000000 <= t.get(r, pk) < 11000000)
    with closing(sqlite3.connect(FIFADB)) as c2:
        spids_of = {}
        for sp, pl in c2.execute("SELECT spid, playerid FROM players"):
            spids_of.setdefault(pl, set()).add(sp)
    for r in old:
        key = r["spid"] if r["spid"] >= 1000000 else r["playerid"]
        row = t.set(tmpl, pk, key)
        for name, (sn, lo) in fill.items():
            if name in cols and isinstance(r[name], int) and t.fields[sn][0] not in (0, 4):  # bo cot chu / so thuc
                v = r[name] - lo
                if 0 <= v < 1 << t.fields[sn][2]:
                    row = t.set(row, sn, v)
        sp = fld["spid"]
        row = t.set(row, sp[0], key - sp[1])
        # ma giay khong co file data/sceneassets/shoe/shoe_<ma>.rx3 (vd. 14174 cua Beckham) -> chan trong:
        # dung mau giay pho bien nhat cua game (29), thiet ke mac dinh
        st, sd = fld["shoetypecode"], fld["shoedesigncode"]
        if f"data/sceneassets/shoe/shoe_{t.get(row, st[0]) + st[1]}.rx3" not in shoes:
            row = t.set(t.set(row, st[0], 29 - st[1]), sd[0], 0 - sd[1])
            fixed_shoes += 1
        asset = next((h for h in (r["spid"], r["playerid"]) if f"data/sceneassets/heads/head_{h}_0.rx3" in heads4), None)
        if asset is None:  # the clone (vd Beckham huyen thoai): mat that cua mot the khac cung cau thu (head_10000250)
            asset = next((h for h in sorted(spids_of.get(r["playerid"], ())) if f"data/sceneassets/heads/head_{h}_0.rx3" in heads4), None)
        if os.environ.get("NO_REAL_HEAD") and str(r["playerid"]) in os.environ["NO_REAL_HEAD"].split(","):
            asset = None  # phep thu: ep mat chung
        ha, hc = fld["headassetid"], fld["headclasscode"]
        if asset is not None:
            row = t.set(t.set(row, ha[0], asset - ha[1]), hc[0], 0 - hc[1])
            real += 1
        else:
            row = t.set(t.set(row, ha[0], key - ha[1]), hc[0], 1 - hc[1])
        by[key] = row
        added_old += 1
    print(f"engine: +{added_old} shipped cards that had no 3D row ({real} with their own head, {fixed_shoes} shoe codes without a model -> 29)", flush=True)
    # The huyen thoai (WL, UL, EL, UB, CEL, ENL, WB, PRIME 94, INFINITY 76): dong 3D rieng theo spid (ban sao dong theo
    # playerid, truong spid = the do) -> bang ten trong tran hien dung mua thay vi mua cua dong chung (vd Buffon "17").
    # Chi spid < 1e8 (truong spid/playerid cua engine 27 bit, rangehigh 99.999.999).
    # GIOI HAN: engine dem dong bang u16 va giu them maxinsert=560 dong -> tong dong + 560 phai <= 65535.
    # 65.234 dong (them ca 11 mua) lam game sap luc tai (2026-11-08). Chot ENGINE_MAX_ROWS, uu tien mua quan trong.
    SEASON_ROWS = (18, 19, 94, 76, 30, 93, 28, 27)  # 26 TOTY, 26 TOTS, PRIME, INFINITY PRIME, UL, WL, UB, EL
    sp = fld["spid"]
    n_photo = photo_heads(t, fld, by, photo)  # GM Tool: mat 3D tu anh (truoc card_looks: mat muon cua the van thang)
    print(f"engine: {n_photo} rows wear a photo head ({len(photo or {})} heads)", flush=True)
    n_look = card_looks(t, fld, by, pk)  # GM Tool: mua rieng + mat muon (truoc mua huyen thoai: co dong truoc)
    print(f"engine: +{n_look} rows from the GM Tool (own seasons, borrowed faces)", flush=True)
    per_season = 0
    with closing(sqlite3.connect(FIFADB)) as c3:
        order = {s: i for i, s in enumerate(SEASON_ROWS)}
        cards = sorted(c3.execute(f"SELECT spid, playerid FROM players WHERE spid / 1000000 IN ({','.join(map(str, SEASON_ROWS))})"),
                       key=lambda r: (order[r[0] // 1000000], r[0]))
        for spid, pid in cards:
            if len(by) >= ENGINE_MAX_ROWS:
                break
            if spid in by or pid not in by or spid >= 100000000:
                continue
            by[spid] = t.set(t.set(by[pid], pk, spid), sp[0], spid - sp[1])
            per_season += 1
    print(f"engine: +{per_season} legend-season rows keyed by spid (in-match season badge)", flush=True)
    t.rows = [by[k] for k in sorted(by)]
    assert len(t.rows) + 560 <= 65535, f"bang 3D {len(t.rows)} dong: vuot gioi han engine (+560 maxinsert)"
    raws[i] = t.raw()
    out, pos = bytearray(db[:base]), 0
    for j, raw in enumerate(raws):
        struct.pack_into("<I", out, 0x1C + 8 * j, pos)
        pos += len(raw)
    struct.pack_into("<I", out, base - 4, eadb.crc(out[0x18:base - 4]))
    struct.pack_into("<I", out, 8, base + pos)
    struct.pack_into("<I", out, 0x14, eadb.crc(out[:0x14]))
    return bytes(out) + b"".join(raws), len(mine), missing_head


def cmd_client(a):
    import bigdir
    from refpack import refpack
    from extract_fifadb import decrypt
    data0 = os.path.join(BUILD, "data_0.big")
    label = os.path.join(BUILD, "Label.big")
    os.makedirs(BUILD, exist_ok=True)  # may ban be (nhan bo dung qua cap nhat) chua co build/
    shutil.copyfile(os.path.join(ORIG, "data_0.big"), data0)
    shutil.copyfile(os.path.join(ORIG, "Label.big"), label)
    ents = {n: (off, size) for n, off, size, usize in bigdir.entries(data0)}
    room = {n: s for n, (o, s) in ents.items()}
    names = locale_additions()
    files, want, log = {}, {}, []
    # fifadb: its names tables keep only players with a card (the client reads names from <lang>.db),
    # exactly as the reference did, so the packed DB fits its own slot at its shipped offset
    shipped = bigdir.read(data0, FIFADB_ENTRY)
    plain_db = client_db(trim=True)
    packed, usize = _seal(plain_db, shipped)
    assert len(packed) <= room[FIFADB_ENTRY], (len(packed), room[FIFADB_ENTRY])
    files[FIFADB_ENTRY], want[FIFADB_ENTRY] = (packed, usize), plain_db
    log.append(f"{FIFADB_ENTRY}: {usize} B -> {len(packed)} B packed, slot {room[FIFADB_ENTRY]} (cardless names left out)")
    print(log[-1], flush=True)
    # locale DBs: every name kept (no trimming); one that outgrows its slot is moved into free space
    for loc in LOCALES:
        entry = f"data/db2/{loc}.db.db2"
        shipped = bigdir.read(data0, entry)
        plain_db = locale_db(_plain(shipped), names, trim=False)
        packed, usize = _seal(plain_db, shipped)
        files[entry], want[entry] = (packed, usize), plain_db
        log.append(f"{entry}: {usize} B -> {len(packed)} B packed, slot {room[entry]}")
        print(log[-1], flush=True)
    photo = build_photo_heads(log)  # build/data_7.big (mat 3D tu anh) truoc: dong engine chi tro vao mat co file
    if log and log[-1].startswith(("data_7.big", "photo heads")):
        print(log[-1], flush=True)
    ng, n_new, miss = engine_db(bigdir.read(data0, NG_ENTRY), bigdir.read(data0, NG_META).decode("utf-8", "replace"), photo)
    packed = refpack(ng)
    assert len(packed) < len(ng)
    files[NG_ENTRY] = (packed, len(ng))
    log.append(f"{NG_ENTRY}: {n_new} new players ({miss} without head source), {len(ng)} B -> {len(packed)} B, slot {room[NG_ENTRY]}")
    print(log[-1], flush=True)
    # layout: all rewritten entries live in the shipped slots of these same entries (a contiguous
    # region of data_0.big); fifadb stays at its offset, every other entry keeps its own offset when
    # it fits, else goes first-fit into the space the rewritten entries left free. Archive size unchanged.
    ALIGN = 0x100
    free = [[o, o + s] for n, (o, s) in ents.items() if n in files]
    used = []

    def take(lo, hi):
        for f in list(free):
            if f[0] <= lo and hi <= f[1]:
                free.remove(f)
                if f[0] < lo:
                    free.append([f[0], lo])
                if hi < f[1]:
                    free.append([hi, f[1]])
                used.append((lo, hi))
                return True
        return False
    at = {}
    # the shipped slots of these 8 entries form one contiguous region with no other entry inside it,
    # so the region is treated as one free block (fifadb pinned at its shipped offset)
    rs, re_ = min(ents[n][0] for n in files), max(ents[n][0] + ents[n][1] for n in files)
    assert not any(rs < o + s and o < re_ for n, (o, s) in ents.items() if n not in files and s), "foreign entry in region"
    free[:] = [[rs, re_]]
    fo = ents[FIFADB_ENTRY][0]
    assert take(fo, fo + len(files[FIFADB_ENTRY][0]))
    at[FIFADB_ENTRY] = fo
    pending = sorted((n for n in files if n != FIFADB_ENTRY), key=lambda n: -len(files[n][0]))  # first-fit decreasing
    for n in pending:
        ln = len(files[n][0])
        for f in sorted(free):
            lo = -(-f[0] // ALIGN) * ALIGN
            if lo + ln <= f[1] and take(lo, lo + ln):
                at[n] = lo
                break
        else:
            raise SystemExit(f"{n}: {ln} bytes: no free space left in the DB region")
        log.append(f"  {n} moved to {at[n]:#x} (was {ents[n][0]:#x})")
        print(log[-1], flush=True)
    used.sort()
    assert all(a[1] <= b[0] for a, b in zip(used, used[1:])), "overlap"
    size0 = os.path.getsize(data0)
    bigdir.patch(data0, files, in_place=True, at=at)
    assert os.path.getsize(data0) == size0
    after = {n: (o, s) for n, o, s, u in bigdir.entries(data0)}
    for n, (o, s) in ents.items():  # entries we did not touch: unchanged
        if n not in files:
            assert after[n] == (o, s), n
    region = (min(ents[n][0] for n in files), max(ents[n][0] + ents[n][1] for n in files))
    for n in files:
        assert region[0] <= after[n][0] and after[n][0] + after[n][1] <= region[1], n
    open(os.path.join(BUILD, "layout.json"), "w").write(json.dumps({n: [after[n][0], after[n][1], ents[n][0], ents[n][1]] for n in files}, indent=1))
    assert bigdir.read(data0, NG_ENTRY) == ng
    for entry, plain_db in want.items():
        assert bytes(decrypt(bigdir.read(data0, entry))[16:]) == plain_db[16:], entry
    # season icons
    import labelicons
    from PIL import Image
    icons = {}
    own = [int(k) for k in _json_or(OWN_SEASONS, {}) if os.path.isfile(os.path.join(OWN_ICONS, f"{k}.png"))]
    for s in SEASONS + ICON_ONLY + tuple(c for c in own if c not in SEASONS + ICON_ONLY):
        # FO4 badge 30x24: 26/25 LIVE phong to de lap day o; PRIME/IPRM + mua rieng giu nguyen pixel
        path = os.path.join(OWN_ICONS if s in own else os.path.join(W, "icons"), f"{s}.png")
        im = Image.open(path).convert("RGBA")
        if s in own:
            im.thumbnail((30, 24), Image.LANCZOS)
            icons[s] = im
            continue
        # PRIME / INFINITY: dung nguyen pixel icon 30x24 cua FO4 (khong phong to -> khong mo); o lon 35x34 dat giua
        icons[s] = im if s in ICON_ONLY else im.resize((im.width * 4, im.height * 4), Image.LANCZOS)
    # KHONG dung lossless (DefineBitsLossless2): game treo khi mo Quan ly doi (2026-11-08)
    gfx, dds = labelicons.label_with_icons(bigdir.read(label, LABEL_GFX), bigdir.read(label, LABEL_DDS), icons)
    bigdir.patch(label, {LABEL_GFX: gfx, LABEL_DDS: (refpack(dds), len(dds))})
    assert bigdir.read(label, LABEL_GFX) == gfx and bigdir.read(label, LABEL_DDS) == dds
    log.append(f"Label.big: {os.path.getsize(os.path.join(ORIG, 'Label.big'))} -> {os.path.getsize(label)} B")
    print(log[-1])
    n_teams = build_teamnames()
    if n_teams:
        log.append(f"eng.big / tha.big: TEAMNAMES + {n_teams} CLB moi (su nghiep CLB)")
        print(log[-1], flush=True)
    open(os.path.join(BUILD, "client_log.txt"), "w").write("\n".join(log) + "\n")


def main():
    {"build": cmd_build, "client": cmd_client}[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
