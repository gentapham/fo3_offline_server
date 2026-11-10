"""Club careers up to the running season for the player info's "Karir klub" (PR #39 follow-up;
user: "Lakukan riset dan implementasi untuk riwayat klub sampai musim berjalan tahun ini").

The client lists a card's club career from its own fifadb (players JOIN playerhistory where
historyopen = 1, once features.seasoncare is on) and names each club with Localization.TEAMNAME:
data/ui/localization/TEAMNAMES_<LANG>.json in <lang>.big (an unknown id shows "teamname(<id>)").
The shipped table has 1,040 players and ends around 2017. This reads Wikidata (CC0) instead:
  1. footballers (P106 Q937857) born on each FO3 player's birth date, matched by name
     (fo4_season._same_name over their English / multilingual labels and aliases);
  2. their P54 stints (member of sports team: start and end year, P1642 acquisition "loan"),
     without national teams (P31/P279* national sports team), youth sides and undated stints;
  3. each club's EA team id: the one FO3's own history gives it (same player, same start year),
     else the one whose TEAMNAMES name is the club's, else a new id (NEW_TEAM + the club's
     Q number) whose names fo4_season install adds to every TEAMNAMES file.
Players Wikidata does not know (or not by that birth date and name) keep FO3's rows.

    python tools/club_history.py fetch [--fresh]   Wikidata -> gamedata/club_history.json
    python tools/club_history.py show NAME          a player's rows, newest first, as the game lists them
    python tools/club_history.py selftest

Queries are cached in %TEMP%/club_history_cache (a rerun resumes; --fresh asks Wikidata again).
Then tools/clientbuild/apply.py (GM Tool > Database > Ap dung vao game) puts the rows in
the client's fifadb and the new clubs' names in its TEAMNAMES files.
"""
import argparse, collections, hashlib, json, os, re, sqlite3, sys, tempfile, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))  # Server/tools/clientbuild
ROOT = os.path.dirname(os.path.dirname(HERE))       # Server
GAME = os.path.dirname(ROOT)
sys.path.insert(0, HERE)
import season as fs  # _same_name, plain, REST_OF_WORLD, FIFADB (ban FO3 Offline: tools/clientbuild/season.py)

OUT = os.path.join(ROOT, "gamedata", "club_history.json")
CACHE = os.path.join(tempfile.gettempdir(), "club_history_cache")
SPARQL = "https://query.wikidata.org/sparql"
AGENT = "FO3-private-server-club-history/1.0 (https://github.com/soloplayer24/FIFA-ONLINE-3-Private-Server)"
NEW_TEAM = 200000000  # + a club's Q number: ids of clubs TEAMNAMES has no name for (EA's end at 14,112,486)
YOUTH = re.compile(r"youth|academy|under-?\d\d|\bU-?\d\d\b|juvenil|primavera|\bjunior", re.I)
NOISE = set("fc cf sc afc ac as ssc sv fk cd ca sd ud rc rcd club de del football futbol fussball calcio the "
            "sa sad sae plc ag gmbh spa srl ltd".split())  # words EA's names often leave out (and company forms)
LOC_ENTRY = "data/ui/localization/{0}.big", "data/ui/localization/TEAMNAMES_{1}.json"
CARD_YEAR = {88: 2017, 325: 2025, 300: 2026}  # seasons whose cards show his club in season (year - 1)/year


class Busy(Exception):
    """query.wikidata.org khong tra loi (504/502) sau vai lan thu."""


def sparql(q, fresh=False, tries=6):
    """Result rows of a Wikidata query (cached; one request a second, polite retries)."""
    os.makedirs(CACHE, exist_ok=True)
    f = os.path.join(CACHE, hashlib.sha1(q.encode()).hexdigest() + ".json")
    if os.path.exists(f) and not fresh:
        return json.load(open(f, encoding="utf-8"))
    for attempt in range(tries):
        time.sleep(1.0)
        req = urllib.request.Request(SPARQL, data=urllib.parse.urlencode({"query": q, "format": "json"}).encode(),
                                     headers={"User-Agent": AGENT, "Content-Type": "application/x-www-form-urlencoded"})
        try:
            rows = json.load(urllib.request.urlopen(req, timeout=120))["results"]["bindings"]
            break
        except (OSError, ValueError) as e:
            wait = int(getattr(e, "headers", {}).get("Retry-After", 0) or 0) or 20 * (attempt + 1)
            print(f"retry {attempt + 1} in {wait} s: {str(e)[:80]}", flush=True)
            time.sleep(wait)
    else:
        raise Busy("query.wikidata.org did not answer: run fetch again later (finished parts are cached)")
    rows = [{k: v["value"] for k, v in r.items()} for r in rows]
    json.dump(rows, open(f, "w", encoding="utf-8"))
    return rows


def sparql_split(make, items, fresh=False):
    """Hang cua truy van make(items); Wikidata qua tai (504) voi lo lon -> chia doi lo va hoi lai (2026-11-10:
    lo 60 ngay sinh het gio lien tuc). Lo 1 phan tu van hong thi bo qua lo do (in ra)."""
    try:
        return sparql(make(items), fresh, tries=3 if len(items) > 1 else 6)
    except Busy:
        if len(items) == 1:
            print(f"bo qua (Wikidata khong tra loi): {items[0]}", flush=True)
            return []
        h = len(items) // 2
        print(f"lo {len(items)} qua tai -> chia {h} + {len(items) - h}", flush=True)
        return sparql_split(make, items[:h], fresh) + sparql_split(make, items[h:], fresh)


def qid(uri):
    return uri.rsplit("/", 1)[-1]


def batches(xs, n):
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def norm(name):
    """A club name without accents, punctuation and the usual FC / CF / Club de words."""
    words = ["saint" if w == "st" else w for w in re.sub(r"[^a-z0-9 ]", " ", fs.plain(name).lower().replace(".", "")).split()]
    return " ".join(w for w in words if w not in NOISE) or " ".join(words)


def shown(info):
    """A new club's English name: Wikidata's short name when it is a name (Colo-Colo), not an
    abbreviation (CSD), else its label (Club Social y Deportivo Colo-Colo)."""
    s = info["short"]
    return s if len(s) >= 5 and not s.isupper() and len(s) < len(info["en"]) else info["en"]


def fo3_players(db):
    """{player id: (birth date 'YYYY-MM-DD', [names])} of every player with a card, and
    {player id: [(team, joined, end, loan)]} of FO3's own history."""
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = {}
    for pid, born in c.execute("SELECT playerid, MIN(birthdate) FROM players GROUP BY playerid"):
        m = re.match(r"(\d+)/(\d+)/(\d+)", born or "")
        if m:
            out[pid] = (f"{int(m[3]):04d}-{int(m[1]):02d}-{int(m[2]):02d}", [])
    for t in ("nameseng", "namesind"):
        for row in c.execute(f"SELECT playerid, full, short, full_eng, short_eng FROM {t}"):
            if row[0] in out:
                out[row[0]][1].extend(n for n in row[1:] if n and n not in out[row[0]][1])
    old = collections.defaultdict(list)
    for pid, team, joined, end, loan in c.execute("SELECT playerid, teamid, joinedyear, endyear, loan FROM playerhistory"):
        old[pid].append((team, joined, end, loan))
    # and the club of his 2017 / 25 LIVE / 26 LIVE card that year (loan None: a card, not a stint)
    for pid, season, team in c.execute("SELECT playerid, spid / 1000000, teamid FROM players WHERE spid / 1000000 IN "
                                       f"({','.join(map(str, CARD_YEAR))}) AND teamid != {fs.REST_OF_WORLD[0]}"):
        old[pid].append((team, CARD_YEAR[season], CARD_YEAR[season], None))
    c.close()
    return out, old


def match(players, people):
    """{player id: Q id}: the footballer born that day whose name is the player's (the best one,
    and clearly better than the next; of two with the very same name, a duplicate item, the older)."""
    born = collections.defaultdict(list)
    for p in people:
        born[p["dob"][:10]].append((qid(p["p"]), p.get("names", "").split("|")))
    out = {}
    for pid, (dob, names) in players.items():
        scored = sorted(((max((fs._same_name(a, b) for a in names for b in wd if b), default=0), q)
                         for q, wd in born.get(dob, [])), key=lambda sq: (-sq[0], int(sq[1][1:])))
        if scored and scored[0][0] >= 1.3 and (len(scored) == 1 or scored[1][0] < scored[0][0] - 0.2
                                               or scored[1][0] == scored[0][0]):
            out[pid] = scored[0][1]
    return out


def rows(stints, clubs, born=None):
    """[(club Q id, joined, end (-1: still there), loan)] of a player, oldest first, from his P54
    stints [(club, start, end, loan)]: national teams, youth sides (also any club before he was
    16: Wikidata lists youth clubs too, EA's careers start with the senior ones) and undated
    stints left out, one stint per club and start year, an open stint ended by the next one."""
    year = lambda d: int(d[:4]) if d and re.match(r"\d{4}-", d) else None  # not "unknown value" (a URI)
    keep = {}
    for club, start, end, loan in stints:
        info = clubs.get(club)
        s = year(start)
        if s is None or not info or info["national"] or YOUTH.search(info["en"]) or (born and s < born + 16):
            continue
        e = year(end) if year(end) is not None else (-1 if not end else -2)  # -2: ended, year unknown
        if e >= 0 and e < s:
            continue
        k = (club, s)
        if k not in keep or (keep[k][2] < 0 and e >= 0):
            keep[k] = (club, s, e, int(loan))
    out = sorted(keep.values(), key=lambda r: (r[1], r[2] < 0, r[2]))
    for i, (club, s, e, loan) in enumerate(out):
        if e < 0 and (i + 1 < len(out) or e == -2):  # an open stint before another, or one ended unknown when
            out[i] = (club, s, max(s, out[i + 1][1]) if i + 1 < len(out) else s, loan)
    return out


def club_ids(clubs, careers, matched, old, ea):
    """{club Q id: team id}: votes from FO3's data on the same player (an FO3 history row starting
    within a year of the stint, or his 2017 / LIVE card's club in a year the stint covers), then the
    club's names against TEAMNAMES (EA's English names), then a new id."""
    votes = collections.defaultdict(collections.Counter)
    for pid, q in matched.items():
        for club, s, e, loan in careers.get(q, []):
            for team, joined, end, l in old.get(pid, []):
                if (abs(joined - s) <= 1) if l is not None else (s < joined <= (e if e >= 0 else 9999)):
                    votes[club][team] += 1
    by_name = collections.defaultdict(set)
    for t, n in ea.items():
        by_name[norm(n)].add(int(t))
    labels = collections.Counter(norm(i["en"]) for i in clubs.values())
    out = {}
    for club, info in clubs.items():
        names = [info["en"], info["short"], *info["aliases"]]
        if votes[club]:
            team, n = votes[club].most_common(1)[0]
            import difflib  # chi luc tai tu Wikidata; Python cua launcher khong co difflib (Ap dung vao game)
            like = max(difflib.SequenceMatcher(None, norm(ea.get(str(team), "")), norm(x)).ratio() for x in names if x)
            if (n >= 2 and n >= 0.6 * sum(votes[club].values())) or like >= 0.6:
                out[club] = team
                continue
        # the club's own names first; an alias only when no other club is called that (Inter Miami
        # CF's alias "Miami FC" is another club's name)
        hits = {t for x in (info["en"], info["short"]) if x for t in by_name.get(norm(x), ())}
        if not hits:
            hits = {t for x in info["aliases"] if labels[norm(x)] == 0 for t in by_name.get(norm(x), ())}
        out[club] = hits.pop() if len(hits) == 1 else NEW_TEAM + int(club[1:])
    return out


def ea_teamnames(game=GAME, lang="eng"):
    """Ten CLB cua EA (TEAMNAMES_ENG.json trong data/ui/localization/eng.big cua game)."""
    import bigdir
    big, entry = LOC_ENTRY[0].format(lang), LOC_ENTRY[1].format(lang, lang.upper())
    return json.loads(bigdir.read(os.path.join(game, big), entry).decode("utf-8-sig"))

def cmd_fetch(a):
    players, old = fo3_players(fs.FIFADB)
    dates = sorted({d for d, _ in players.values()})
    print(f"{len(players)} players, {len(dates)} birth dates", flush=True)
    names = collections.defaultdict(set)  # Q id -> names; labels first (aliases make a query 6 times slower)
    born = {}
    q_dates = lambda part: """SELECT ?p ?dob ?n WHERE {
  VALUES ?dob { %s }
  ?p wdt:P569 ?dob ; wdt:P106 wd:Q937857 .
  OPTIONAL { ?p rdfs:label ?n FILTER(LANG(?n) IN ("en", "mul")) }
}""" % " ".join(f'"{d}T00:00:00Z"^^xsd:dateTime' for d in part)
    for i, part in enumerate(batches(dates, 60)):
        for r in sparql_split(q_dates, part, a.fresh):
            born[qid(r["p"])] = r["dob"]
            names[qid(r["p"])].add(r.get("n", ""))
        if i % 10 == 9:
            print(f"birth dates {min(len(dates), (i + 1) * 60)}/{len(dates)}: {len(born)} footballers", flush=True)
    people = lambda: [{"p": q, "dob": d, "names": "|".join(names[q])} for q, d in born.items()]
    matched = match(players, people())
    print(f"{len(matched)}/{len(players)} players found by label", flush=True)
    # then the aliases of footballers born the day a player not found yet was, with a name a bit like his
    open_days = collections.defaultdict(list)
    for pid, (d, ns) in players.items():
        if pid not in matched:
            open_days[d] += ns
    ask = sorted(q for q, d in born.items() if d[:10] in open_days and
                 max((fs._same_name(x, n) for x in open_days[d[:10]] for n in names[q] if n), default=0) >= 0.7)
    q_alias = lambda part: ("""SELECT ?p ?n WHERE { VALUES ?p { %s } ?p skos:altLabel ?n FILTER(LANG(?n) IN ("en", "mul")) }"""
                            % " ".join("wd:" + q for q in part))
    for part in batches(ask, 300):
        for r in sparql_split(q_alias, part, a.fresh):
            names[qid(r["p"])].add(r["n"])
    matched = match(players, people())
    print(f"{len(matched)}/{len(players)} players found on Wikidata (aliases of {len(ask)} footballers)", flush=True)
    careers = collections.defaultdict(list)
    qs = sorted(set(matched.values()))
    q_career = lambda part: """SELECT ?p ?club ?start ?end ?loan WHERE {
  VALUES ?p { %s }
  ?p p:P54 ?st . ?st ps:P54 ?club ; wikibase:rank ?rank . FILTER(?rank != wikibase:DeprecatedRank)
  OPTIONAL { ?st pq:P580 ?start } OPTIONAL { ?st pq:P582 ?end }
  BIND(EXISTS { ?st pq:P1642 ?how . ?how rdfs:label "loan"@en } AS ?loan)
}""" % " ".join("wd:" + q for q in part)
    for i, part in enumerate(batches(qs, 150)):
        for r in sparql_split(q_career, part, a.fresh):
            if re.fullmatch(r"Q\d+", qid(r["club"])):  # not an "unknown value" club
                careers[qid(r["p"])].append((qid(r["club"]), r.get("start"), r.get("end"), r["loan"] == "true"))
        if i % 20 == 19:
            print(f"careers {min(len(qs), (i + 1) * 150)}/{len(qs)}", flush=True)
    clubs = {}
    every = sorted({s[0] for st in careers.values() for s in st})
    q_club = lambda part: """SELECT ?c ?nat (SAMPLE(?l1) AS ?en) (SAMPLE(?l2) AS ?mul) (SAMPLE(?s) AS ?short) (SAMPLE(?k) AS ?ko)
  (SAMPLE(?z) AS ?zh) (GROUP_CONCAT(DISTINCT ?al; separator="|") AS ?aliases) WHERE {
  VALUES ?c { %s }
  BIND(EXISTS { ?c wdt:P31/wdt:P279* wd:Q1194951 } AS ?nat)
  OPTIONAL { ?c rdfs:label ?l1 FILTER(LANG(?l1) = "en") } OPTIONAL { ?c rdfs:label ?l2 FILTER(LANG(?l2) = "mul") }
  OPTIONAL { ?c wdt:P1813 ?s FILTER(LANG(?s) IN ("en", "mul")) }
  OPTIONAL { ?c rdfs:label ?k FILTER(LANG(?k) = "ko") } OPTIONAL { ?c rdfs:label ?z FILTER(LANG(?z) IN ("zh", "zh-hans", "zh-cn")) }
  OPTIONAL { ?c skos:altLabel ?al FILTER(LANG(?al) IN ("en", "mul")) }
} GROUP BY ?c ?nat""" % " ".join("wd:" + q for q in part)
    for i, part in enumerate(batches(every, 150)):
        for r in sparql_split(q_club, part, a.fresh):
            en = r.get("en") or r.get("mul") or qid(r["c"])
            clubs[qid(r["c"])] = {"en": en, "short": r.get("short", ""), "ko": r.get("ko", ""), "zh": r.get("zh", ""),
                                  "aliases": [x for x in r.get("aliases", "").split("|") if x], "national": r["nat"] == "true"}
        if i % 20 == 19:
            print(f"clubs {min(len(every), (i + 1) * 150)}/{len(every)}", flush=True)
    careers = {q: rows(st, clubs, int(born[q][:4]) if q in born else None) for q, st in careers.items()}
    ea = ea_teamnames()
    ids = club_ids(clubs, careers, matched, old, ea)
    out = {"source": "Wikidata (CC0), query.wikidata.org", "made": time.strftime("%Y-%m-%d"), "players": {}, "teams": {}}
    for pid, q in sorted(matched.items()):
        if careers.get(q):
            out["players"][str(pid)] = [[ids[c], s, e, loan] for c, s, e, loan in careers[q]]
    out["missing"] = len(players) - len(out["players"])  # the admin page's Aset tab: "not available"
    used = {t for r in out["players"].values() for t, *_ in r}
    for club, t in sorted(ids.items()):
        if t >= NEW_TEAM and t in used:
            info = clubs[club]
            out["teams"][str(t)] = {"eng": shown(info), "kor": info["ko"] or shown(info), "chi": info["zh"] or shown(info)}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    known = sum(1 for c, t in ids.items() if t < NEW_TEAM and t in used)
    print(f"done: {len(out['players'])} players, {sum(map(len, out['players'].values()))} rows, "
          f"{known} clubs with EA ids, {len(out['teams'])} new clubs -> {OUT}")


def load():
    return json.load(open(OUT, encoding="utf-8")) if os.path.isfile(OUT) else None


def apply(c):
    """Put gamedata/club_history.json's rows in a fifadb connection (fo4_season.client_db)."""
    h = load()
    for pid, rs in (h or {}).get("players", {}).items():
        c.execute("DELETE FROM playerhistory WHERE playerid = ?", (int(pid),))
        c.executemany("INSERT INTO playerhistory (playerid, ordernum, teamid, joinedyear, endyear, loan) VALUES (?,?,?,?,?,?)",
                      [(int(pid), i, t, s, e, loan) for i, (t, s, e, loan) in enumerate(rs)])


def teamnames(names, lang, teams):
    """TEAMNAMES_<LANG>.json bytes with the new clubs' names (the client reads it as a JSON object)."""
    key = {"kor": "kor", "chi": "chi"}.get(lang, "eng")
    names = {**names, **{t: n[key] for t, n in teams.items()}}
    return json.dumps(names, ensure_ascii=False, indent=1).replace("\n", "\r\n").encode("utf-8")


def cmd_show(a):
    h, ea = load() or {"players": {}, "teams": {}}, ea_teamnames()
    c = sqlite3.connect(f"file:{fs.FIFADB}?mode=ro", uri=True)
    for pid, full in c.execute("SELECT playerid, full FROM nameseng WHERE full LIKE ? LIMIT 20", (f"%{a.name}%",)):
        rs = h["players"].get(str(pid))
        print(f"{full} ({pid}): {'Wikidata' if rs else 'FO3'}")
        if not rs:
            rs = [list(r[1:]) for r in c.execute("SELECT ordernum, teamid, joinedyear, endyear, loan FROM playerhistory "
                                                  "WHERE playerid = ? ORDER BY ordernum", (pid,))]
        for t, s, e, loan in reversed(rs):
            name = ea.get(str(t)) or h["teams"].get(str(t), {}).get("eng", f"teamname({t})")
            print(f"  {s} ~ {'' if e == -1 else e}  {name}{'  (loan)' if loan else ''}")


def cmd_selftest(a=None):
    clubs = {"Q1": {"en": "AS Monaco FC", "national": False}, "Q2": {"en": "France national under-19", "national": True},
             "Q3": {"en": "Paris Saint-Germain FC", "national": False}, "Q4": {"en": "Real Madrid Club de Fútbol", "national": False},
             "Q5": {"en": "Juventus F.C. Youth Sector", "national": False}}
    st = [("Q1", "2015-01-01", "2018-01-01", False), ("Q2", "2016-01-01", "2016-01-01", False), ("Q3", None, None, False),
          ("Q3", "2017-01-01", "2018-01-01", True), ("Q3", "2018-01-01", "2024-01-01", False), ("Q3", "2018-01-01", None, False),
          ("Q4", "2024-01-01", None, False), ("Q5", "2012-01-01", "2014-01-01", False), ("Q1", "2010-01-01", None, False)]
    assert rows(st, clubs) == [("Q1", 2010, 2015, 0), ("Q1", 2015, 2018, 0), ("Q3", 2017, 2018, 1), ("Q3", 2018, 2024, 0),
                               ("Q4", 2024, -1, 0)], rows(st, clubs)
    unknown = "http://www.wikidata.org/.well-known/genid/1"  # "unknown value" dates
    assert rows([("Q1", "2010-01-01", unknown, False), ("Q4", "2012-01-01", unknown, False), ("Q3", unknown, None, False)],
                clubs) == [("Q1", 2010, 2012, 0), ("Q4", 2012, 2012, 0)]
    assert norm("Real Madrid Club de Fútbol") == norm("Real Madrid") == "real madrid"
    assert norm("K.R.C. Genk") == norm("KRC Genk") and norm("AS Monaco FC") == norm("AS Monaco Football Club SA") == "monaco"
    assert match({1: ("1998-12-20", ["Kylian Mbappe", "K. Mbappe"])},
                 [{"p": "x/Q1", "dob": "1998-12-20T00:00:00Z", "names": "Kylian Mbappé|Kylian Mbappé Lottin"},
                  {"p": "x/Q2", "dob": "1998-12-20T00:00:00Z", "names": "Kylian Smith"},
                  {"p": "x/Q123410299", "dob": "1998-12-20T00:00:00Z", "names": "Kylian Mbappé|Kylian Mbappé Lottin"}]) == {1: "Q1"}
    # Ronaldo (born 1985): his youth clubs Andorinha (1992) and Nacional (1995) stay out, Sporting (2002) in
    assert rows([("Q1", "1992-01-01", "1995-01-01", False), ("Q3", "2002-01-01", "2003-01-01", False)], clubs, 1985) == [("Q3", 2002, 2003, 0)]
    ea = {"18": "AS Monaco", "73": "Paris Saint-Germain", "112732": "Miami FC"}
    infos = {q: {**i, "short": "", "aliases": []} for q, i in clubs.items()}
    infos["Q6"] = {"en": "Inter Miami CF", "short": "", "aliases": ["Inter Miami", "Miami FC"], "national": False}
    infos["Q7"] = {"en": "Miami FC", "short": "", "aliases": [], "national": False}
    ids = club_ids(infos, {"Q9": [("Q3", 2017, 2018, 1)]}, {7: "Q9"}, {7: [(73, 2017, 2018, 1)]}, ea)
    assert ids["Q1"] == 18 and ids["Q3"] == 73 and ids["Q4"] == NEW_TEAM + 4, ids
    assert ids["Q7"] == 112732 and ids["Q6"] == NEW_TEAM + 6, ids
    # two players' 2017 cards at EA's "Legia Warszawa" while Wikidata's "Legia Warsaw" stints cover 2017
    infos["Q8"] = {"en": "Legia Warsaw", "short": "", "aliases": [], "national": False}
    ids = club_ids(infos, {"Q10": [("Q8", 2015, -1, 0)], "Q11": [("Q8", 2016, 2019, 0)]}, {1: "Q10", 2: "Q11"},
                   {1: [(1871, 2017, 2017, None)], 2: [(1871, 2017, 2017, None)]}, {**ea, "1871": "Legia Warszawa"})
    assert ids["Q8"] == 1871, ids
    got = json.loads(teamnames({"1": "Arsenal"}, "kor", {"200000004": {"eng": "Real", "kor": "레알", "chi": "皇马"}}))
    assert got == {"1": "Arsenal", "200000004": "레알"}
    print("club_history selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch").add_argument("--fresh", action="store_true", help="ask Wikidata again (ignore the cache)")
    sub.add_parser("show").add_argument("name")
    sub.add_parser("selftest")
    a = ap.parse_args()
    {"fetch": cmd_fetch, "show": cmd_show, "selftest": cmd_selftest}[a.cmd](a)


if __name__ == "__main__":
    main()
