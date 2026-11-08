"""GM Tool > Database (port + toi uu tu fo3-server-indo issue #38): sua the, mat 3D, logo CLB, mua tu tao,
file mod, "Ap dung vao game".

- Sua the: chi so, vi tri, chan thuan / chan yeu, ky thuat, tinh than (work rate), trait, CLB (chuyen nhuong:
  giai lay theo CLB). Ghi thang vao gamedata/fifadb.sqlite (server thay ngay: gia, OVR, tim kiem); gia tri goc
  luu o gamedata/card_edits.json de khoi phuc. Trong tran / man the cua game doc DB cua client -> can "Ap dung
  vao game" (dung lai data_0.big tu chinh DB nay, nen sua khong bao gio bi mat khi build lai).
- Mat 3D: muon dau (mat, da, mat...) cua dong 3D khac (spid / player id), chinh toc, mau toc, rau, da, mat, dang
  nguoi. Luu "look" trong card_edits.json; tools/clientbuild/season.py ghi vao dong 3D rieng cua the (giu gioi han
  dong cua engine).
- Logo CLB: thay anh 256 + 128 px trong cache cua game (ban goc giu o gamedata/crest_backup de khoi phuc).
- Mua tu tao: ma con trong (< 100), ten, icon 30x24; the = ban sao the khac (+ cong chi so). Server them goi
  Top 200 + nhan mua tu lan bat sau; ten mua + bo loc Thi truong ghi ngay; icon + dong 3D khi Ap dung vao game.
- File mod: xuat / nhap ca mua tu tao, the da sua, mat 3D sang server khac.
- Ap dung vao game: chay tools/clientbuild/apply.py --install trong nen (game phai dang tat), xem nhat ky.
"""
import base64, binascii, copy, functools, json, os, re, sqlite3, subprocess, sys, threading, time
from contextlib import closing

import admin
from admin import api, AdminError, as_int, names, name_table, rel, POS_NAMES

HERE = admin.HERE
CARD_EDITS = os.path.join(HERE, "gamedata", "card_edits.json")
CREST_BACKUP = os.path.join(HERE, "gamedata", "crest_backup")
SEASON_ICONS = os.path.join(HERE, "gamedata", "season_icons")
CLIENTBUILD = os.path.join(HERE, "tools", "clientbuild")
STATIC_ROSTER = os.path.join(HERE, "responses", "static_roster.json")
# ma mua khong co the FO3 nao va khong co icon cua client (< 100: truong spid 27 bit cua engine)
SEASON_CODES = (18, 19, 25, 50, 72, 80, 1, 2, 3, 4, 5, 34, 36, 46, 52, 58, 60, 62, 64, 66, 68, 70, 74, 75)

STATS = ("acceleration sprintspeed agility balance reactions ballcontrol dribbling positioning finishing shotpower longshots "
         "volleys penalties crossing curve freekickaccuracy shortpassing longpassing vision headingaccuracy marking "
         "standingtackle slidingtackle interceptions aggression jumping stamina strength gkdiving gkhandling gkkicking "
         "gkreflexes gkpositioning").split()
FIELDS = {**dict.fromkeys(STATS, (1, 150)), "preferredposition1": (0, 27), "preferredposition2": (-1, 27),
          "preferredposition3": (-1, 27), "preferredposition4": (-1, 27), "preferredfoot": (1, 2),
          "weakfootabilitytypecode": (1, 5), "skillmoves": (0, 4), "attackingworkrate": (0, 2), "defensiveworkrate": (0, 2),
          "trait1": (0, 2 ** 31 - 1), "teamid": (1, 999999)}
TRAITS = ("inflexibility long-throw-in takes-powerful-driven-free-kicks diver injury-prone solid-player "
          "avoids-using-weaker-foot dives-into-tackles tries-to-beat-offside-trap selfish leadership "
          "argues-with-officials early-crosser finesse-shot flair long-passer long-shot-taker speed-dribbler "
          "play-maker pushes-up-for-corners puncher gk-long-thrower power-header gk-one-on-ones giant-throw-in "
          "outside-foot-shot crowd-favourite swerve second-wind acrobatic-clearance").split()
TRAIT_VI = ("Cứng nhắc", "Ném biên xa", "Sút phạt căng", "Hay ăn vạ", "Dễ chấn thương", "Chắc chắn", "Tránh chân yếu",
            "Xoạc bóng", "Bẫy việt vị", "Ích kỷ", "Thủ lĩnh", "Cãi trọng tài", "Tạt sớm", "Sút xoáy", "Hoa mỹ",
            "Chuyền dài", "Sút xa", "Rê bóng tốc độ", "Kiến thiết", "Lên phạt góc", "Đấm bóng (TM)", "Ném xa (TM)",
            "Đánh đầu mạnh", "Đối mặt (TM)", "Ném biên siêu xa", "Sút má ngoài", "Được yêu mến", "Cứa lòng",
            "Hồi sức", "Phá bóng trên không")
LOOK = ("hairtypecode", "haircolorcode", "facialhairtypecode", "facialhaircolorcode", "skintonecode", "eyecolorcode",
        "bodytypecode")
LOOK_VI = {"hairtypecode": "Kiểu tóc", "haircolorcode": "Màu tóc", "facialhairtypecode": "Kiểu râu",
           "facialhaircolorcode": "Màu râu", "skintonecode": "Màu da", "eyecolorcode": "Màu mắt", "bodytypecode": "Dáng người"}
PERM_TABLES = ("permkr", "permcn", "permth", "permvn", "permsg", "permid")
NG_ENTRY, NG_META = "data/db/fifa_ng_db.db", "data/db/fifa_ng_db-meta.xml"
hc = None


def _hc():
    return admin.hc


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def load_edits():
    d = _load(CARD_EDITS, {})
    return d if isinstance(d, dict) else {}


def caches_changed(spids=()):
    """Sau khi sua fifadb: bo cache chi so / danh sach the cua server va GM Tool."""
    h = _hc()
    for s in spids:
        h._OVR.pop(int(s), None)
    admin._INDEX = None
    for fn in (getattr(h, "_pack_rows", None), getattr(getattr(h, "rank", None), "vm_card_pool", None)):
        try:
            fn.cache_clear()
        except AttributeError:
            pass


def card_row(c, spid):
    c.row_factory = sqlite3.Row
    r = c.execute(f"SELECT p.*, {_hc().POSOVR} AS ovr FROM players p WHERE p.spid = ?", (spid,)).fetchone()
    if r is None:
        raise AdminError("Không có SPID này trong dữ liệu")
    return r


def write_card(c, r, values):
    """values vao dong the r; overallrating / potential doi theo muc OVR client thay doi."""
    c.execute(f"UPDATE players SET {', '.join(f'{k} = ?' for k in values)} WHERE spid = ?", [*values.values(), r["spid"]])
    moved = card_row(c, r["spid"])["ovr"] - r["ovr"]
    if moved:
        c.execute("UPDATE players SET overallrating = overallrating + ?1, potential = potential + ?1 WHERE spid = ?2",
                  (moved, r["spid"]))
    c.commit()


@functools.lru_cache(maxsize=1)
def clubs():
    """{teamid: (ten, leagueid)} cac CLB (khong doi tuyen) cua server."""
    teams = name_table("TEAM")
    return {t: (teams.get(t, f"CLB {t}"), lid) for _, ls in _hc().club_index() for lid, ts in ls if lid != 78 for t in ts}


# ---------------------------------------------------------------- dong 3D (doc tu data_0.big cua game)
def _cb():
    if CLIENTBUILD not in sys.path:
        sys.path.append(CLIENTBUILD)
    import bigdir, eadb  # noqa: E401 (ban trong tools/clientbuild)
    return bigdir, eadb


_ENGINE = {}


def engine_rows():
    """(Table, {truong: (shortname, rangelow)}, {khoa: dong}, {player id: [khoa]}) cua bang players trong
    fifa_ng_db cua game (cache theo thoi gian sua data_0.big); None neu khong doc duoc."""
    path = os.path.join(_hc().GAME, "data", "data_0.big")
    try:
        sig = os.path.getmtime(path)
    except OSError:
        return None
    if _ENGINE.get("sig") != sig:
        try:
            bigdir, eadb = _cb()
            db, meta = bigdir.read(path, NG_ENTRY), bigdir.read(path, NG_META).decode("utf-8", "replace")
            m = re.search(r'<table name="players" shortname="(\w+)".*?</table>', meta, re.S)
            fld = {n: (sn, int(lo)) for n, sn, lo in re.findall(r'<field name="(\w+)" shortname="(\w+)"[^>]*rangelow="(-?\d+)"', m.group())}
            base, tnames, raws = eadb.db_tables(db)
            t = eadb.Table(raws[tnames.index(m.group(1).encode())])
            pk = fld["playerid"][0]
            by = {t.get(r, pk): r for r in t.rows}
            mine = {}
            for k in sorted(by):
                mine.setdefault(k % 1000000, []).append(k)
            _ENGINE.update(sig=sig, data=(t, fld, by, mine))
        except Exception as e:
            print("database tab: engine rows:", e, flush=True)
            _ENGINE.update(sig=sig, data=None)
    return _ENGINE["data"]


def card_look(spid, pid):
    """Dong 3D the dang dung (rieng theo spid, else theo player id), gia tri LOOK + gioi han, cac dong cua cau thu."""
    e = engine_rows()
    if e is None:
        return None
    t, fld, by, mine = e
    key = spid if spid in by else pid if pid in by else None
    if key is None:
        return {"row": None, "fields": {}, "ranges": {}, "faces": []}
    row = by[key]
    val = lambda k: t.get(row, fld[k][0]) + fld[k][1]
    ranges = {k: (fld[k][1], fld[k][1] + (1 << t.fields[fld[k][0]][2]) - 1) for k in LOOK if k in fld}
    return {"row": key, "fields": {k: val(k) for k in ranges}, "ranges": ranges,
            "faces": [[k, t.get(by[k], fld["headassetid"][0]) + fld["headassetid"][1]] for k in mine.get(pid, [])][:30]}


# ---------------------------------------------------------------- the
@api("db_meta")
def db_meta(p):
    return {"traits": [[i, k, TRAIT_VI[i]] for i, k in enumerate(TRAITS)], "fields": FIELDS, "positions": POS_NAMES,
            "look": [[k, LOOK_VI[k]] for k in LOOK], "clubs": sorted([[t, n, lg] for t, (n, lg) in clubs().items()], key=lambda x: x[1]),
            "file": rel(CARD_EDITS)}


@api("db_card")
def db_card(p):
    spid = as_int(p.get("spid"), 1, 10 ** 10, "spid")
    with closing(_hc()._db()) as c:
        r = card_row(c, spid)
    e = load_edits().get(str(spid), {})
    own = str(spid // 1000000) in {str(k) for k in _hc().own_seasons()}
    club = clubs().get(r["teamid"])
    return {"spid": spid, "playerid": r["playerid"], "season": spid // 1000000, "name": names([spid]).get(spid, "?"),
            "ovr": r["ovr"], "teamid": r["teamid"], "club": club[0] if club else name_table("TEAM").get(r["teamid"], ""),
            "own": own, "fields": {k: r[k] for k in FIELDS}, "orig": e.get("orig", {}),
            "look": card_look(spid, r["playerid"]), "look_edit": e.get("look", {})}


@api("db_edits")
def db_edits(p):
    edits = load_edits()
    nm = names(edits)
    return {"rows": [{"spid": int(s), "name": nm.get(int(s), "?"), "season": int(s) // 1000000,
                      "changed": sorted(e.get("new", {})) + (["look"] if e.get("look") else [])}
                     for s, e in sorted(edits.items(), key=lambda kv: nm.get(int(kv[0]), ""))], "file": rel(CARD_EDITS)}


def set_look(spid, pid, want):
    """Chi giu phan khac voi mat hien co: {"face": khoa dong 3D, truong LOOK: gia tri}."""
    base = card_look(spid, pid)
    if base is None:
        raise AdminError("Không đọc được dữ liệu 3D của game (data_0.big)")
    look = {}
    face = want.get("face")
    if face not in (None, "", base["row"], str(base["row"])):
        face = as_int(face, 1, 10 ** 9, "Mặt (SPID / ID cầu thủ)")
        if face not in engine_rows()[2]:
            raise AdminError(f"Không có dòng 3D {face} (nhập SPID hoặc ID cầu thủ có mặt trong game)")
        look["face"] = face
    for k in LOOK:
        if k in want and k in base["ranges"] and str(want[k]) != str(base["fields"].get(k)):
            look[k] = as_int(want[k], *base["ranges"][k], LOOK_VI[k])
    return look


@api("db_card_set")
def db_card_set(p):
    spid = as_int(p.get("spid"), 1, 10 ** 10, "spid")
    want = p.get("fields") or {}
    if not isinstance(want, dict) or not isinstance(p.get("look", {}), dict):
        raise AdminError("Dữ liệu không hợp lệ")
    with closing(_hc()._db()) as c:
        r = card_row(c, spid)
        new = {}
        for k, v in want.items():
            if k not in FIELDS:
                raise AdminError(f"Trường không hợp lệ: {k}")
            if str(v) != str(r[k]):  # gia tri khong doi co the ngoai khoang (vd -3 cua thu mon)
                new[k] = as_int(v, *FIELDS[k], k)
        if "teamid" in new:
            if new["teamid"] not in clubs():
                raise AdminError(f"Không có CLB {new['teamid']}")
            new["leagueid"] = clubs()[new["teamid"]][1]
        look = set_look(spid, r["playerid"], p["look"]) if "look" in p else None
        if new:
            write_card(c, r, new)
    edits = load_edits()
    e = edits.setdefault(str(spid), {"orig": {}, "new": {}})
    for k, v in new.items():
        e["orig"].setdefault(k, r[k])
        e["new"][k] = v
    for k in [k for k in e["new"] if e["new"][k] == e["orig"].get(k)]:  # sua tay ve gia tri goc
        e["new"].pop(k)
        e["orig"].pop(k, None)
    if look is not None:
        e["look"] = look
    if not e["new"] and not e.get("look"):
        edits.pop(str(spid))
    _save(CARD_EDITS, edits)
    caches_changed([spid])
    return db_card(p)


@api("db_card_revert")
def db_card_revert(p):
    spid = as_int(p.get("spid"), 1, 10 ** 10, "spid")
    edits = load_edits()
    e = edits.pop(str(spid), None)
    if e is None:
        raise AdminError("Thẻ này chưa sửa")
    orig = {k: v for k, v in e.get("orig", {}).items() if k in FIELDS or k == "leagueid"}
    if orig:
        with closing(_hc()._db()) as c:
            write_card(c, card_row(c, spid), orig)
    _save(CARD_EDITS, edits)
    caches_changed([spid])
    return db_card(p)


# ---------------------------------------------------------------- logo CLB
def crest_file(size, teamid, root):
    folder = {256: "large", 128: "small"}[size]
    return os.path.join(root, "externalAssets", "crests", folder, f"s{teamid}.png")


def crest_roots():
    g = _hc().GAME
    return [r for r in (os.path.join(g, "_cache", "games", "fo3sg"), os.path.join(g, "_cache", "cdn")) if os.path.isdir(r)]


def _png(b64, what):
    try:
        raw = base64.b64decode(str(b64 or ""), validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    if raw[:8] != b"\x89PNG\r\n\x1a\n" or len(raw) > 2 << 20:
        raise AdminError(f"{what} phải là ảnh PNG (≤ 2 MB)")
    return raw


@api("db_crest_set")
def db_crest_set(p):
    """{teamid, large (PNG 256), small (PNG 128)} - trang da thu nho anh bang canvas."""
    teamid = as_int(p.get("teamid"), 1, 999999, "CLB")
    imgs = {256: _png(p.get("large"), "Ảnh 256 px"), 128: _png(p.get("small"), "Ảnh 128 px")}
    for root in crest_roots():
        tag = "cdn" if root.endswith("cdn") else "fo3sg"
        for size, raw in imgs.items():
            dst = crest_file(size, teamid, root)
            bak = os.path.join(CREST_BACKUP, tag, str(size), f"s{teamid}.png")
            if os.path.isfile(dst) and not os.path.isfile(bak):  # giu ban goc lan dau
                os.makedirs(os.path.dirname(bak), exist_ok=True)
                with open(dst, "rb") as fs, open(bak, "wb") as fd:
                    fd.write(fs.read())
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst + ".tmp", "wb") as f:
                f.write(raw)
            os.replace(dst + ".tmp", dst)
    return {"ok": True, "teamid": teamid}


@api("db_crest_revert")
def db_crest_revert(p):
    teamid = as_int(p.get("teamid"), 1, 999999, "CLB")
    done = 0
    for root in crest_roots():
        tag = "cdn" if root.endswith("cdn") else "fo3sg"
        for size in (256, 128):
            bak = os.path.join(CREST_BACKUP, tag, str(size), f"s{teamid}.png")
            if os.path.isfile(bak):
                os.replace(bak, crest_file(size, teamid, root))
                done += 1
    if not done:
        raise AdminError("Logo CLB này chưa bị thay (không có bản gốc lưu lại)")
    return {"ok": True, "teamid": teamid}


# ---------------------------------------------------------------- mua tu tao
def load_seasons():
    d = _load(_hc().OWN_SEASONS_FILE, {})
    return d if isinstance(d, dict) else {}


def save_seasons(s):
    _save(_hc().OWN_SEASONS_FILE, s)


def season_icon(code):
    return os.path.join(SEASON_ICONS, f"{code}.png")


def used_codes():
    with closing(_hc()._db()) as c:
        return {r[0] for r in c.execute("SELECT DISTINCT spid / 1000000 FROM players")}


def lang_file():
    return os.path.join(_hc().GAME, "data", "ui", "localization", "LANGUAGESTRING_ENG.json")


def season_loc(code, name, tag):
    """Ten mua trong LANGUAGESTRING_ENG.json cua game (file chi doc: bo tam) + bo loc Thi truong."""
    f = lang_file()
    try:
        with open(f, "rb") as fh:
            raw = fh.read()
        d = json.loads(raw.decode("utf-8-sig"))
        keys = {f"TRANSFERMARKET_SEASON_{code}": name, f"CHATTING_SEASON_HEADER_{code}": tag,
                f"LEAGUE_MISSION_SEASON_PREFIX_{code}": tag}
        if any(d.get(k) != v for k, v in keys.items()):
            d.update(keys)
            ro = not os.access(f, os.W_OK)
            if ro:
                os.chmod(f, 0o666)
            with open(f + ".tmp", "wb") as fh:
                fh.write((b"\xef\xbb\xbf" if raw[:3] == b"\xef\xbb\xbf" else b"") +
                         json.dumps(d, ensure_ascii=False, indent=1).encode("utf-8"))
            os.replace(f + ".tmp", f)
            if ro:
                os.chmod(f, 0o444)
    except (OSError, ValueError) as e:
        print("database tab: language file:", e, flush=True)
    try:
        r = _load(STATIC_ROSTER, None)
        if isinstance(r, dict) and not any(code in (x.get("value") or []) for x in r.get("seasons", [])):
            r["seasons"].insert(0, {"season": f"$TRANSFERMARKET_SEASON_{code}", "value": [code],
                                    "open": "2013-01-01T00:00:00.000Z", "close": "2099-12-31T00:00:00.000Z"})
            _save(STATIC_ROSTER, r)
    except (OSError, ValueError) as e:
        print("database tab: static roster:", e, flush=True)
    admin._SEASONS = None


@api("db_seasons")
def db_seasons(p):
    s = load_seasons()
    with closing(_hc()._db()) as c:
        count = dict(c.execute("SELECT spid / 1000000, COUNT(*) FROM players GROUP BY 1"))
    used = used_codes()
    return {"seasons": [{"code": int(k), "name": v.get("name"), "tag": v.get("tag"), "cards": count.get(int(k), 0),
                         "icon": os.path.isfile(season_icon(k))} for k, v in sorted(s.items(), key=lambda kv: int(kv[0]))],
            "free": [c for c in SEASON_CODES if str(c) not in s and c not in used], "file": rel(_hc().OWN_SEASONS_FILE)}


@api("db_season_set")
def db_season_set(p):
    """{code, name, tag, icon (PNG 30x24, base64; bat buoc khi tao)}."""
    code, s = as_int(p.get("code"), 1, 99, "Mã mùa"), load_seasons()
    if str(code) not in s and (code not in SEASON_CODES or code in used_codes()):
        raise AdminError(f"Mã mùa {code} đã có thẻ / không dùng được")
    name = str(p.get("name") or "").strip()[:24]
    tag = re.sub(r"[^A-Za-z0-9]", "", str(p.get("tag") or ""))[:5].upper() or f"S{code}"
    if not name:
        raise AdminError("Nhập tên mùa")
    raw = _png(p["icon"], "Icon mùa") if p.get("icon") else None
    if raw is None and not os.path.isfile(season_icon(code)):
        raise AdminError("Chọn icon cho mùa (ảnh PNG, trang tự thu về 30x24)")
    s.setdefault(str(code), {"cards": {}}).update(name=name, tag=tag)
    if raw:
        os.makedirs(SEASON_ICONS, exist_ok=True)
        with open(season_icon(code) + ".tmp", "wb") as f:
            f.write(raw)
        os.replace(season_icon(code) + ".tmp", season_icon(code))
    save_seasons(s)
    season_loc(code, name, tag)
    return db_seasons(p)


def _in_club(spids):
    st = _hc()._state()
    have = {int(x["spid"]) for t in (st.get("teams") or {}).values() for x in t.get("players", [])}
    return have & {int(s) for s in spids}


@api("db_season_delete")
def db_season_delete(p):
    code, s = as_int(p.get("code"), 1, 99, "Mã mùa"), load_seasons()
    if str(code) not in s:
        raise AdminError("Không có mùa này")
    cards = [int(k) for k in (s[str(code)].get("cards") or {})]
    if _in_club(cards):
        raise AdminError("Còn thẻ của mùa này trong CLB: bán / xoá thẻ đó trước")
    _delete_cards(cards)
    s.pop(str(code))
    save_seasons(s)
    try:
        os.remove(season_icon(code))
    except OSError:
        pass
    return db_seasons(p)


def _delete_cards(spids):
    if not spids:
        return
    with closing(_hc()._db()) as c:
        q = ",".join("?" * len(spids))
        c.execute(f"DELETE FROM players WHERE spid IN ({q})", spids)
        for tb in PERM_TABLES:
            try:
                c.execute(f"DELETE FROM {tb} WHERE spid IN ({q})", spids)
            except sqlite3.OperationalError:
                pass
        c.commit()
    edits = load_edits()
    if any(edits.pop(str(s), None) for s in spids):
        _save(CARD_EDITS, edits)
    caches_changed(spids)


@api("db_season_cards")
def db_season_cards(p):
    code = as_int(p.get("code"), 1, 99, "Mã mùa")
    cards = load_seasons().get(str(code), {}).get("cards") or {}
    nm = names([int(k) for k in cards] + [int(v.get("from", 0)) for v in cards.values()])
    ovr = _hc().card_ovrs([int(k) for k in cards]) if cards else {}
    return {"rows": [{"spid": int(k), "name": nm.get(int(k), "?"), "from": v.get("from"), "boost": v.get("boost", 0),
                      "ovr": ovr.get(int(k), 0)} for k, v in sorted(cards.items(), key=lambda kv: -ovr.get(int(kv[0]), 0))]}


def copy_card(c, src, code, boost):
    """The moi code*1e6 + player id = ban sao the src (chi so + boost, toi da 150), giu ban quyen (perm*)."""
    r = card_row(c, src)
    spid = code * 1000000 + r["playerid"]
    if c.execute("SELECT 1 FROM players WHERE spid = ?", (spid,)).fetchone():
        raise AdminError(f"Cầu thủ này đã có thẻ ở mùa {code}")
    row = dict(r)
    row.pop("ovr", None)
    is_gk = row["preferredposition1"] == 0
    for k in STATS:
        if k in row and isinstance(row[k], int) and (is_gk or not k.startswith("gk")):
            row[k] = max(1, min(150, row[k] + boost))
    row["spid"] = spid
    cols = list(row)
    c.execute(f"INSERT INTO players ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[k] for k in cols])
    moved = card_row(c, spid)["ovr"] - r["ovr"]
    c.execute("UPDATE players SET overallrating = overallrating + ?1, potential = potential + ?1 WHERE spid = ?2", (moved, spid))
    for tb in PERM_TABLES:
        try:
            pr = c.execute(f"SELECT * FROM {tb} WHERE spid = ?", (src,)).fetchone()
        except sqlite3.OperationalError:
            continue
        if pr:
            pr = dict(pr, spid=spid)
            c.execute(f"INSERT OR REPLACE INTO {tb} ({','.join(pr)}) VALUES ({','.join('?' * len(pr))})", list(pr.values()))
    return spid


@api("db_season_card_add")
def db_season_card_add(p):
    """{code, from: [spid...], boost 0..20}: sao the vao mua tu tao."""
    code, s = as_int(p.get("code"), 1, 99, "Mã mùa"), load_seasons()
    if str(code) not in s:
        raise AdminError("Tạo mùa trước")
    boost = as_int(p.get("boost") or 0, -20, 20, "Cộng chỉ số")
    src = p.get("from")
    src = src if isinstance(src, list) else [src]
    src = [as_int(x, 1, 10 ** 10, "SPID gốc") for x in src][:200]
    added = []
    with closing(_hc()._db()) as c:
        for x in src:
            if x // 1000000 == code:
                raise AdminError("Thẻ gốc không được là thẻ của chính mùa này")
            added.append((copy_card(c, x, code, boost), x))
        c.commit()
    for spid, x in added:
        s[str(code)].setdefault("cards", {})[str(spid)] = {"from": x, "boost": boost}
    save_seasons(s)
    caches_changed([a for a, _ in added])
    return {**db_season_cards({"code": code}), "added": len(added)}


@api("db_season_card_remove")
def db_season_card_remove(p):
    spid = as_int(p.get("spid"), 1, 10 ** 10, "spid")
    s = load_seasons()
    code = str(spid // 1000000)
    if str(spid) not in (s.get(code, {}).get("cards") or {}):
        raise AdminError("Thẻ này không thuộc mùa tự tạo")
    if _in_club([spid]):
        raise AdminError("Thẻ này đang có trong CLB: bán / xoá trước")
    _delete_cards([spid])
    s[code]["cards"].pop(str(spid))
    save_seasons(s)
    return db_season_cards({"code": int(code)})


# ---------------------------------------------------------------- file mod
@api("db_mod_export")
def db_mod_export(p):
    """Moi mua tu tao (ten, icon, the: goc + cong), the da sua (gia tri moi + mat) -> 1 JSON."""
    s = load_seasons()
    seasons = {}
    for k, v in s.items():
        icon = None
        if os.path.isfile(season_icon(k)):
            with open(season_icon(k), "rb") as f:
                icon = base64.b64encode(f.read()).decode()
        seasons[k] = {"name": v.get("name"), "tag": v.get("tag"), "icon": icon, "cards": v.get("cards") or {}}
    edits = {k: {"new": e.get("new", {}), "look": e.get("look", {})} for k, e in load_edits().items()}
    return {"fo3mod": 1, "made": time.strftime("%Y-%m-%d %H:%M"), "seasons": seasons, "edits": edits}


@api("db_mod_import")
def db_mod_import(p):
    mod = p.get("mod")
    if not isinstance(mod, dict) or mod.get("fo3mod") != 1:
        raise AdminError("File mod không hợp lệ")
    log = []
    s = load_seasons()
    used = used_codes()
    for k, v in (mod.get("seasons") or {}).items():
        code = as_int(k, 1, 99, "Mã mùa")
        if str(code) not in s and code in used:
            log.append(f"Bỏ mùa {code}: mã đã có thẻ trên server này")
            continue
        db_season_set({"code": code, "name": v.get("name") or f"Mùa {code}", "tag": v.get("tag"), "icon": v.get("icon")})
        s = load_seasons()
        n = 0
        for spid, card in (v.get("cards") or {}).items():
            if spid in (s[str(code)].get("cards") or {}):
                continue
            try:
                db_season_card_add({"code": code, "from": [card.get("from")], "boost": card.get("boost", 0)})
                n += 1
            except AdminError as e:
                log.append(f"Thẻ {spid}: {e}")
        s = load_seasons()
        log.append(f"Mùa {code} ({v.get('name')}): +{n} thẻ")
    n = 0
    for spid, e in (mod.get("edits") or {}).items():
        try:
            db_card_set({"spid": spid, "fields": e.get("new") or {}, **({"look": e["look"]} if e.get("look") else {})})
            n += 1
        except AdminError as err:
            log.append(f"Sửa thẻ {spid}: {err}")
    log.append(f"Đã áp {n} thẻ sửa")
    return {"log": log}


# ---------------------------------------------------------------- ap dung vao game (nen)
JOB = {"running": False, "log": [], "code": None, "started": None}


def _job_cmd():
    script = os.path.join(CLIENTBUILD, "apply.py")
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run", script, "--install"]
    return [sys.executable, "-I", script, "--install"]


def _job_run():
    try:
        p = subprocess.Popen(_job_cmd(), cwd=CLIENTBUILD, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for raw in iter(p.stdout.readline, b""):
            JOB["log"] = (JOB["log"] + [raw.decode("utf-8", "replace").rstrip()])[-300:]
        JOB["code"] = p.wait()
    except Exception as e:
        JOB["log"].append(f"Lỗi: {e}")
        JOB["code"] = -1
    finally:
        JOB["running"] = False
        _ENGINE.clear()


@api("db_apply")
def db_apply(p):
    if JOB["running"]:
        raise AdminError("Đang áp dụng, chờ xong đã")
    if not os.path.isfile(os.path.join(CLIENTBUILD, "orig", "data_0.big")):
        raise AdminError("Máy này không có bộ dựng client (tools/clientbuild): chỉ máy làm bản vá mới áp dụng được")
    JOB.update(running=True, log=["Bắt đầu dựng lại data_0.big / Label.big (khoảng 1 phút)…"], code=None, started=time.time())
    threading.Thread(target=_job_run, daemon=True, name="db-apply").start()
    return db_job(p)


@api("db_job")
def db_job(p):
    return {k: JOB[k] for k in ("running", "log", "code", "started")}


admin.READ_ONLY.update({"db_meta", "db_card", "db_edits", "db_seasons", "db_season_cards", "db_job", "db_mod_export"})
admin.NO_LOCK.update({"db_meta", "db_job", "db_seasons", "db_season_cards", "db_edits"})


# ---------------------------------------------------------------- Giao huu / Exhibition / KOTH (tab Xep hang)
@api("multiplay_info")
def multiplay_info(p):
    mp = _hc().multiplay
    return {**mp.settings(), "sets": [[k, mp.KOTH_SET_NAMES.get(k, k)] for k in mp.KOTH_SETS],
            "plays": mp.EXHIBITION_PLAYS, "file": rel(mp.SETTINGS_FILE)}


@api("multiplay_set")
def multiplay_set(p):
    mp = _hc().multiplay
    if "regions" in p and p["regions"] not in mp.KOTH_SETS:
        raise AdminError("Bộ vùng không hợp lệ")
    mp.save_settings({k: p[k] for k in ("friendly", "exhibition", "koth", "regions") if k in p})
    return multiplay_info(p)


admin.READ_ONLY.add("multiplay_info")
