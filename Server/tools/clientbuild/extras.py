"""Them AC Milan (teamid 47) vao fifa_ng_db cua client (data/data_0.big).
Ban sao Inter (44) cho: teams, leagueteamlinks (Serie A 31), teamstadiumlinks (San Siro 5),
teamkits (3 bo: kit_47_0/1/2_0.rx3 co san trong data_5.big), formations.
DB moi dat vao khoang trong sau data/db2/vie.db.db2 (kich thuoc archive giu nguyen).
Usage: python -I extras.py <data_0.big> [--write]   (apply.py goi sau season.py client)"""
import re, struct, sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # bigdir, eadb, refpack ben canh
import bigdir, eadb
from refpack import refpack

BIG = sys.argv[1]
NG, META = "data/db/fifa_ng_db.db", "data/db/fifa_ng_db-meta.xml"
TID, SRC = 47, 44
RED, BLACK, WHITE = (200, 16, 46), (20, 20, 20), (255, 255, 255)

db = bigdir.read(BIG, NG)
meta = bigdir.read(BIG, META).decode("utf-8", "replace")
base, names, raws = eadb.db_tables(db)


class T:
    def __init__(self, name):
        m = re.search(r'<table name="%s" shortname="(\w+)".*?</table>' % name, meta, re.S)
        self.i = names.index(m.group(1).encode())
        self.t = eadb.Table(raws[self.i])
        self.f = {}
        for f in re.findall(r"<field [^>]*>", m.group()):
            n = re.search(r'name="(\w+)"', f).group(1)
            sn = re.search(r'shortname="(\w+)"', f).group(1)
            lo = re.search(r'rangelow="(-?\d+)"', f)
            self.f[n] = (sn, int(lo.group(1)) if lo and "INTEGER" in f else 0)

    def get(self, row, n):
        sn, lo = self.f[n]
        v = self.t.get(row, sn)
        return v + lo if isinstance(v, int) else v

    def set(self, row, n, v):
        sn, lo = self.f[n]
        if isinstance(v, str):
            return self.t.set(row, sn, v)
        s = v - lo
        assert 0 <= s < 1 << self.t.fields[sn][2], (n, v)
        return self.t.set(row, sn, s)

    def where(self, n, v):
        return [r for r in self.t.rows if self.get(r, n) == v]

    def add(self, row, **kv):
        for k, v in kv.items():
            row = self.set(row, k, v)
        self.t.rows.append(row)
        return row

    def save(self):
        raws[self.i] = self.t.raw()


def rgb(prefix, c, keys=("r", "g", "b")):
    return {f"{prefix}{k}": v for k, v in zip(keys, c)}


teams = T("teams")
assert not teams.where("teamid", TID), "Milan da co"
src = teams.where("teamid", SRC)[0]
teams.add(src, teamid=TID, assetid=TID, teamname="AC Milan", rivalteam=SRC,
          captainid=184344, penaltytakerid=208128, freekicktakerid=208128, longkicktakerid=208128,
          leftcornerkicktakerid=202651, rightcornerkicktakerid=208128,
          domesticprestige=9, internationalprestige=8,
          **rgb("teamcolor1", RED), **rgb("teamcolor2", BLACK), **rgb("teamcolor3", WHITE))
# hang trong bang teams sap theo teamid
teams.t.rows.sort(key=lambda r: teams.get(r, "teamid"))
teams.save()

ltl = T("leagueteamlinks")
s = ltl.where("teamid", SRC)[0]
key = max(ltl.get(r, "artificialkey") for r in ltl.t.rows) + 1
ltl.add(s, teamid=TID, artificialkey=key, tableposition=1, champion=0)
ltl.save()

tsl = T("teamstadiumlinks")
tsl.add(tsl.where("teamid", SRC)[0], teamid=TID, stadiumid=5)
tsl.t.rows.sort(key=lambda r: tsl.get(r, "teamid"))
tsl.save()

kits = T("teamkits")
kid = max(kits.get(r, "teamkitid") for r in kits.t.rows)
cols = {0: (RED, BLACK, WHITE), 1: (WHITE, RED, BLACK), 2: ((40, 40, 40), (90, 90, 90), WHITE)}
for r in sorted(kits.where("teamtechid", SRC), key=lambda r: kits.get(r, "teamkittypetechid")):
    typ = kits.get(r, "teamkittypetechid")
    p, s2, t3 = cols.get(typ, cols[0])
    kid += 1
    kits.add(r, teamtechid=TID, teamkitid=kid, year=0,
             **rgb("teamcolorprim", p), **rgb("teamcolorsec", s2), **rgb("teamcolortert", t3))
kits.t.rows.sort(key=lambda r: (kits.get(r, "teamtechid"), kits.get(r, "teamkittypetechid"), kits.get(r, "year")))
kits.save()

fm = T("formations")
fid = max(fm.get(r, "formationid") for r in fm.t.rows) + 1
fm.add(fm.where("teamid", SRC)[0], teamid=TID, formationid=fid)
fm.save()

# Doi rieng cho the PRIME (94) va INFINITY PRIME (76): ban sao doi Ultimate Legend (113709), giai 2028
LEGEND_TEAMS = ((113710, "PRIME"), (113711, "INFINITY PRIME"))
for ltid, lname in LEGEND_TEAMS:
    tt = T("teams")
    if tt.where("teamid", ltid):
        continue
    tt.add(tt.where("teamid", 113709)[0], teamid=ltid, assetid=ltid, teamname=lname)
    tt.t.rows.sort(key=lambda r: tt.get(r, "teamid")); tt.save()
    lt = T("leagueteamlinks")
    lt.add(lt.where("teamid", 113709)[0], teamid=ltid, artificialkey=max(lt.get(r, "artificialkey") for r in lt.t.rows) + 1,
           tableposition=1, champion=0)
    lt.save()
    kt = T("teamkits")
    kid2 = max(kt.get(r, "teamkitid") for r in kt.t.rows)
    for r in sorted(kt.where("teamtechid", 113709), key=lambda r: kt.get(r, "teamkittypetechid")):
        kid2 += 1
        kt.add(r, teamtechid=ltid, teamkitid=kid2)
    kt.t.rows.sort(key=lambda r: (kt.get(r, "teamtechid"), kt.get(r, "teamkittypetechid"), kt.get(r, "year")))
    kt.save()
    ft = T("formations")
    srcf = ft.where("teamid", 113709)
    if srcf:
        ft.add(srcf[0], teamid=ltid, formationid=max(ft.get(r, "formationid") for r in ft.t.rows) + 1)
        ft.save()

# Doi tuyen chau A cho World Cup (server http_config.WC_NATION_SQUADS): client chi co ten (TEAMNAMES), khong co dong
# teams/teamkits -> engine khong tai ao (o vuong). Ban sao dong cua China PR (1413) o moi bang co ma doi, tru
# teamplayerlinks (cau thu do server gui). Ao 3D: file roi data/sceneassets/kit/kit_<doi>_*.rx3 (tools/national_kits.py).
NATIONAL_TEAMS = {1411: ("Japan", (0, 40, 130), (255, 255, 255)), 974: ("Korea Republic", (200, 16, 46), (20, 20, 20)),
                  111114: ("Saudi Arabia", (255, 255, 255), (0, 108, 53)), 111115: ("Iran", (255, 255, 255), (218, 0, 0)),
                  111512: ("Iraq", (0, 122, 61), (255, 255, 255)), 111533: ("Thailand", (36, 60, 140), (237, 28, 36))}
NAT_SRC = 1413
for ntid, (nname, c1, c2) in NATIONAL_TEAMS.items():
    tt = T("teams")
    if tt.where("teamid", ntid):
        continue
    tt.add(tt.where("teamid", NAT_SRC)[0], teamid=ntid, assetid=ntid, teamname=nname)
    tt.t.rows.sort(key=lambda r: tt.get(r, "teamid")); tt.save()
    lt = T("leagueteamlinks")
    lt.add(lt.where("teamid", NAT_SRC)[0], teamid=ntid,
           artificialkey=max(lt.get(r, "artificialkey") for r in lt.t.rows) + 1)
    lt.save()
    sl = T("teamstadiumlinks")
    if sl.where("teamid", NAT_SRC):
        sl.add(sl.where("teamid", NAT_SRC)[0], teamid=ntid)
        sl.t.rows.sort(key=lambda r: sl.get(r, "teamid")); sl.save()
    kt = T("teamkits")
    kid3 = max(kt.get(r, "teamkitid") for r in kt.t.rows)
    kcols = {0: (c1, c2, c2), 1: (c2, c1, c1), 2: ((40, 40, 40), (90, 90, 90), (255, 255, 255))}
    for r in sorted(kt.where("teamtechid", NAT_SRC), key=lambda r: kt.get(r, "teamkittypetechid")):
        p, s2, t3 = kcols.get(kt.get(r, "teamkittypetechid"), kcols[0])
        kid3 += 1
        kt.add(r, teamtechid=ntid, teamkitid=kid3,
               **rgb("teamcolorprim", p), **rgb("teamcolorsec", s2), **rgb("teamcolortert", t3))
    kt.t.rows.sort(key=lambda r: (kt.get(r, "teamtechid"), kt.get(r, "teamkittypetechid"), kt.get(r, "year")))
    kt.save()
    ft = T("formations")
    ft.add(ft.where("teamid", NAT_SRC)[0], teamid=ntid, formationid=max(ft.get(r, "formationid") for r in ft.t.rows) + 1)
    ft.save()

# Luat thay nguoi (user 2026-11-09: "van du bi 7 nguoi va thay toi da 3"): so luot thay trong tran lay tu
# fifaGameSettings.NumOfSubs (2 bit, chi so: 0 = 3 luot, +1 moi bac -> 2 = 5 luot), khong phai gamesettings
# availableSubs cua server. SubsOnBench 2 = 7 du bi (giu). Ap cho ca 4 dong (moi che do / profile).
NUM_OF_SUBS = 2
gs = T("fifaGameSettings")
gs.t.rows = [gs.set(r, "NumOfSubs", NUM_OF_SUBS) for r in gs.t.rows]
gs.save()

out = bytearray(db[:base]); pos = 0
for j, raw in enumerate(raws):
    struct.pack_into("<I", out, 0x1C + 8 * j, pos)
    pos += len(raw)
struct.pack_into("<I", out, base - 4, eadb.crc(out[0x18:base - 4]))
struct.pack_into("<I", out, 8, base + pos)
struct.pack_into("<I", out, 0x14, eadb.crc(out[:0x14]))
ng = bytes(out) + b"".join(raws)
print("db", len(db), "->", len(ng))

# kiem tra doc lai
chk = T.__new__(T)
for name, want in (("teams", 1), ("leagueteamlinks", 1), ("teamstadiumlinks", 1), ("teamkits", 3), ("formations", 1)):
    rows = eadb.rows(ng, meta, name)
    k = "teamtechid" if name == "teamkits" else "teamid"
    lo = 1 if name in ("teams",) else 0
    print(name, len(rows))

if "--write" in sys.argv:
    packed = refpack(ng)
    E = sorted(bigdir.entries(BIG), key=lambda e: e[1])
    i = next(k for k, e in enumerate(E) if e[0] == "data/db2/vie.db.db2")
    n, o, sz, u = E[i]
    lo = -(-(o + sz) // 0x100) * 0x100
    hi = E[i + 1][1]
    assert lo + len(packed) <= hi, (len(packed), hi - lo)
    size0 = __import__("os").path.getsize(BIG)
    bigdir.patch(BIG, {NG: (packed, len(ng))}, in_place=True, at={NG: lo})
    assert __import__("os").path.getsize(BIG) == size0
    assert bigdir.read(BIG, NG) == ng
    print("written", len(packed), "B at", hex(lo), "room", hi - lo)
