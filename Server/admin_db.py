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
# 2026-11-13: cac ma trong cu da dung cho mua nhap tu ban CN (tools/cn_import.py) -> mua tu tao dung cac ma chua co the nao
# (client co san icon cho cac ma nay; labelicons.py doi ten khung icon cu de icon moi thay the)
SEASON_CODES = (12, 54, 56, 59, 79, 84, 86, 90, 92, 96, 98, 99)

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


# ---------------------------------------------------------------- Thuong Rank / King of the Hill (tab Xep hang)
# port fo3-server-indo e341d54: gamedata/ladder_rewards.json va gamedata/koth_rewards.json de len ban mac dinh
# trong responses/ (rank.ladder_rewards, multiplay.koth_rewards); reset = xoa file, ve mac dinh.
def _reward_choices():
    h = _hc()
    return ([{"itemid": h.EP_ITEM, "name": "EP", "kind": "EP"}]
            + [{"itemid": i, "name": h.item_def(i).get("name", str(i)), "kind": "Gói cầu thủ"} for i in h.PACKS]
            + [{"itemid": i, "name": h.item_def(i).get("name", str(i)), "kind": "Vật phẩm"} for i in h.ITEMS])


def _reward_item(it, err):
    """{itemid, count}: EP 1..10^10, goi / vat pham 1..99; khac -> AdminError(err)."""
    h = _hc()
    try:
        itemid, count = int(it["itemid"]), int(it["count"])
    except (KeyError, TypeError, ValueError):
        raise AdminError(err)
    top = 10 ** 10 if itemid == h.EP_ITEM else 99 if itemid in h.PACKS or itemid in h.ITEMS else 0
    if not 1 <= count <= top:
        raise AdminError(err)
    return {"itemid": itemid, "count": count}


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


E_LR = "Phần thưởng Rank không hợp lệ (EP 1-10.000.000.000, gói/vật phẩm 1-99; mốc tuần 1-500 trận thắng, tăng dần, 1-20 mốc)"
E_KR = ("Phần thưởng King of the Hill không hợp lệ (EP 1-10.000.000.000, gói/vật phẩm 1-99; mục tiêu 1-100 trận thắng "
        "hoặc 1-200 trận, số trận thắng cho thưởng hạng 1-100 mỗi tuần và 1-500 mỗi mùa; 1-20 dòng thứ hạng trong "
        "khoảng 1-1000, tăng dần và không chồng nhau)")


@api("ladder_rewards")
def ladder_rewards(p):
    """Thuong Rank: thuong chinh / phu cuoi mua moi hang, cac moc thang trong tuan."""
    rk = _hc().rank
    return {**rk.ladder_rewards(), "file": rel(rk.LADDER_REWARDS_FILE), "choices": _reward_choices()}


@api("ladder_rewards_set")
def ladder_rewards_set(p):
    """gamedata/ladder_rewards.json: dung cac hang cua ban mac dinh, [chinh, phu] moi hang; 1-20 moc tuan 1-500
    tran thang, tang dan; reset = ve mac dinh."""
    rk = _hc().rank
    if p.get("reset"):
        if os.path.isfile(rk.LADDER_REWARDS_FILE):
            os.remove(rk.LADDER_REWARDS_FILE)
        return ladder_rewards(p)
    names = ["DIVISION_" + d["name"] for d in rk.ladder_divisions()]
    try:
        divs = [{"name": d["name"], "items": [_reward_item(d["items"][0], E_LR), _reward_item(d["items"][1], E_LR)]}
                for d in p["divs"]]
        wins = [{"from": int(w["from"]), "items": [_reward_item(i, E_LR) for i in w["items"]]} for w in p["wins"]]
    except (KeyError, IndexError, TypeError, ValueError):
        raise AdminError(E_LR)
    steps = [w["from"] for w in wins]
    if sorted(d["name"] for d in divs) != sorted(names) or len(divs) != len(names) or steps != sorted(set(steps)) \
            or not 1 <= len(wins) <= 20 or not all(1 <= f <= 500 for f in steps) or not all(w["items"] for w in wins):
        raise AdminError(E_LR)
    _write_json(rk.LADDER_REWARDS_FILE, {"divs": divs, "wins": wins})
    return ladder_rewards(p)


@api("koth_rewards")
def koth_rewards(p):
    """Thuong King of the Hill: muc tieu tuan + thuong hang tuan, thuong hang mua + thu hang khu vuc."""
    mp = _hc().multiplay
    cfg = mp.koth_rewards()
    # du moi hang (thap nhat truoc) de trang luon co du dong, ke ca khi file thieu hang
    names = ["DIVISION_" + d["name"] for d in reversed(_hc().rank.ladder_divisions())]
    for part in (cfg["week"], cfg["season"]):
        by = {d.get("name"): d.get("items") or [] for d in part.get("divs") or []}
        part["divs"] = [{"name": n, "items": by.get(n) or [{"itemid": _hc().EP_ITEM, "count": 1000}]} for n in names]
    if not cfg["week"].get("items"):
        cfg["week"]["items"] = [{"itemid": _hc().EP_ITEM, "count": 100000}]
    return {**cfg, "file": rel(mp.KOTH_REWARDS_FILE), "choices": _reward_choices()}


@api("koth_rewards_set")
def koth_rewards_set(p):
    """gamedata/koth_rewards.json: 1 vat pham moi thuong; dung cac hang (thap nhat truoc); muc tieu tuan 1-100
    tran thang hoac 1-200 tran, so tran thang cho thuong hang 1-100 / tuan, 1-500 / mua; 1-20 dong thu hang
    1-1000, tang dan, khong chong nhau; reset = ve mac dinh."""
    mp = _hc().multiplay
    if p.get("reset"):
        if os.path.isfile(mp.KOTH_REWARDS_FILE):
            os.remove(mp.KOTH_REWARDS_FILE)
        return koth_rewards(p)
    names = ["DIVISION_" + d["name"] for d in reversed(_hc().rank.ladder_divisions())]
    one = lambda x: [_reward_item(x["items"][0], E_KR)]
    try:
        w, s = p["week"], p["season"]
        week = {"type": w["type"], "win": int(w["win"]), "match": int(w["match"]), "grade": int(w["grade"]), "items": one(w),
                "divs": [{"name": d["name"], "items": one(d)} for d in w["divs"]]}
        season = {"grade": int(s["grade"]), "divs": [{"name": d["name"], "items": one(d)} for d in s["divs"]],
                  "ranking": [{"from": int(r["from"]), "to": int(r["to"]), "items": one(r)} for r in s["ranking"]]}
    except (KeyError, IndexError, TypeError, ValueError):
        raise AdminError(E_KR)
    last = 0
    for r in season["ranking"]:
        if not last < r["from"] <= r["to"] <= 1000:
            raise AdminError(E_KR)
        last = r["to"]
    if week["type"] not in ("winning", "match") or not 1 <= week["win"] <= 100 or not 1 <= week["match"] <= 200 \
            or not 1 <= week["grade"] <= 100 or not 1 <= season["grade"] <= 500 or not 1 <= len(season["ranking"]) <= 20 \
            or [d["name"] for d in week["divs"]] != names or [d["name"] for d in season["divs"]] != names:
        raise AdminError(E_KR)
    _write_json(mp.KOTH_REWARDS_FILE, {"week": week, "season": season})
    return koth_rewards(p)


admin.READ_ONLY.update({"ladder_rewards", "koth_rewards"})



# ---------------------------------------------------------------- mat 3D tu anh (port fo3-server-indo b7f6983 + 334b131)
# tools/clientbuild/photo_head.py: anh chup can mat (hoac anh cau thu, diem tu YuNet) dan len dau 3D da quet cua
# mot cau thu "donor" (luoi + toc cua donor, mat moi), luu theo player id o gamedata/photo_heads (anh .png + mat
# .rx3 da ma hoa); "Ap dung vao game" dung data_7.big + dong engine (season.py photo_heads). Tao mat can numpy +
# cv2 trong tools/clientbuild/pylib (chi may lam ban va); viec nang chay ngoai khoa state (NO_LOCK), PH_LOCK giu
# heads.json khong bi 2 yeu cau ghi cung luc.
PHOTO_MAX = 1024
PH_LOCK = threading.Lock()
PH_MISSING = ("Máy này thiếu thư viện tạo mặt 3D ({0}: numpy / OpenCV trong tools/clientbuild/pylib), chỉ máy làm "
              "bản vá tạo được mặt 3D từ ảnh. Mặt đã tạo vẫn được cài bình thường khi Áp dụng vào game.")
_PH = {}


def photo_mod():
    """Module photo_head (tools/clientbuild), nap 1 lan."""
    if "mod" not in _PH:
        _cb()
        import photo_head  # noqa: E402 (ban trong tools/clientbuild)
        _PH["mod"] = photo_head
    return _PH["mod"]


def ph_ready():
    """AdminError tieng Viet neu may nay khong tao duoc mat (thieu numpy / cv2)."""
    miss = photo_mod().missing()
    if miss:
        raise AdminError(PH_MISSING.format(miss))


def ph_pid(p):
    pid = as_int(p.get("pid"), 1, 999999, "ID cầu thủ")
    with closing(_hc()._db()) as c:
        if not c.execute("SELECT 1 FROM players WHERE playerid = ? LIMIT 1", (pid,)).fetchone():
            raise AdminError(f"Không có cầu thủ {pid} trong dữ liệu")
    return pid


def ph_name(pid):
    """Ten cau thu theo player id (hoac dong engine / spid)."""
    with closing(_hc()._db()) as c:
        r = c.execute("SELECT COALESCE(full_eng, full, short) FROM nameseng WHERE playerid = ?", (int(pid) % 1000000,)).fetchone()
    return r[0] if r else str(pid)


def ph_picture(pid):
    """File anh cau thu cua game cho pid: p<pid>.png, else anh the moi nhat p<spid>.png; None neu khong co."""
    base = os.path.join(_hc().ASSETS, "externalAssets", "players")
    f = os.path.join(base, f"p{pid}.png")
    if os.path.isfile(f):
        return f
    with closing(_hc()._db()) as c:
        for (spid,) in c.execute("SELECT spid FROM players WHERE playerid = ? ORDER BY spid DESC", (pid,)):
            f = os.path.join(base, f"p{spid}.png")
            if os.path.isfile(f):
                return f
    return None


def ph_nation(pid):
    with closing(_hc()._db()) as c:
        r = c.execute("SELECT nationality FROM players WHERE playerid = ? ORDER BY spid DESC LIMIT 1", (pid,)).fetchone()
    return r[0] if r else None


def ph_donors():
    """{headassetid: [dong engine, *look, quoc tich]} cac dau quet lam donor duoc (lan dau doc ~2.000 texture + anh,
    vai phut; sau do gamedata/photo_heads/donors.json). Cache theo data_0.big cua game."""
    e = engine_rows()
    if e is None:
        raise AdminError("Không đọc được dữ liệu 3D của game (data_0.big)")
    t, fld, by, _ = e
    if "donors" in _PH and _PH.get("donors_sig") == _ENGINE.get("sig"):
        return _PH["donors"]
    ph = photo_mod()
    rows = {}
    for k in sorted(by, key=lambda k: (k >= 1000000, k)):  # dong player id truoc
        h = t.get(by[k], fld["headassetid"][0]) + fld["headassetid"][1]
        if t.get(by[k], fld["headclasscode"][0]) + fld["headclasscode"][1] == 0 and h < ph.ID_BASE:
            rows.setdefault(h, k)
    looks = ph.donor_looks(_hc().GAME, rows, ph_picture)
    nation = lambda row: t.get(by[row], fld["nationality"][0]) + fld["nationality"][1] if row in by else None
    out = {k: v[:7] + [nation(v[0])] for k, v in looks.items()}
    _PH.update(donors=out, donors_sig=_ENGINE.get("sig"))
    return out


def ph_icon(pid):
    """(anh cau thu RGB, 11 diem hoac None, dong donor goi y hoac None); AdminError khi khong co / hong anh."""
    ph = photo_mod()
    f = ph_picture(pid)
    if not f:
        raise AdminError(f"Chưa có ảnh cầu thủ {pid} trong kho ảnh của game (externalAssets/players)")
    try:
        photo = ph.picture(f)
    except Exception:
        raise AdminError(f"Ảnh cầu thủ {pid} bị hỏng / không phải PNG")
    pts = ph.landmarks(photo)
    if not pts:
        return photo, None, None
    donors = ph_donors()
    head = ph.suggest(ph.look(photo, pts), donors, ph_nation(pid)) if donors else None
    return photo, pts, donors[head][0] if head is not None else None


def ph_save(pid, photo, pts, row):
    """Tao mat cho pid tu anh (RGB) + diem tren dau quet cua dong engine row; thay mat cu cua anh ay. Tra ve id."""
    ph = photo_mod()
    e = engine_rows()
    if e is None:
        raise AdminError("Không đọc được dữ liệu 3D của game (data_0.big)")
    t, fld, by, _ = e
    if row not in by or t.get(by[row], fld["headclasscode"][0]) + fld["headclasscode"][1] != 0:
        raise AdminError(f"Dòng 3D {row} không phải đầu quét thật: chọn cầu thủ có mặt quét làm đầu mẫu")
    donor = t.get(by[row], fld["headassetid"][0]) + fld["headassetid"][1]
    if donor >= ph.ID_BASE:
        raise AdminError(f"Dòng 3D {row} đang mang mặt 3D từ ảnh: chọn cầu thủ có mặt quét gốc làm đầu mẫu")
    try:
        face = ph.donor_files(_hc().GAME, donor)[ph.FACE_FILE]
    except (KeyError, OSError):
        raise AdminError(f"Game thiếu file đầu {donor} (data_3/4/6.big): chọn đầu mẫu khác")
    img = ph.make(photo, pts, face)
    rx3 = ph.with_texture(ph.unchunk(face), img)
    with PH_LOCK:
        heads = ph.load()
        hid = next((k for k, v in heads.items() if v.get("pid") == pid), None) or ph.new_id(heads)
        ph.store(hid, img, rx3)
        heads[hid] = {"pid": pid, "row": row, "donor": donor, "made": time.strftime("%Y-%m-%d")}
        ph.save(heads)
    return hid


def _ph_png(path):
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except OSError:
        return None


@api("photo_head")
def photo_head(p):
    """Mat 3D tu anh cua cau thu pid (neu co): id, donor, ngay tao, texture (PNG base64); co tao duoc tren may nay."""
    pid, ph = ph_pid(p), photo_mod()
    hid, h = next(((k, v) for k, v in ph.load().items() if v.get("pid") == pid), (None, {}))
    miss = ph.missing()
    return {"pid": pid, "name": ph_name(pid), "id": hid, "row": h.get("row"), "donor": ph_name(h["row"]) if h.get("row") else None,
            "made": h.get("made"), "png": _ph_png(os.path.join(ph.DIR, f"{hid}.png")) if hid else None,
            "points": list(ph.POINTS), "picture": bool(ph_picture(pid)),
            "can_make": not miss, "why": PH_MISSING.format(miss) if miss else ""}


@api("photo_heads")
def photo_heads(p):
    """Moi mat 3D tu anh: [{id, pid, name, donor, made}] + may nay co tao duoc khong."""
    ph = photo_mod()
    miss = ph.missing()
    with closing(_hc()._db()) as c:  # the moi nhat cua cau thu: nut Mo (Sua the)
        spid = lambda pid: (c.execute("SELECT MAX(spid) FROM players WHERE playerid = ?", (pid,)).fetchone() or [None])[0]
        rows = [{"id": k, "pid": v.get("pid"), "spid": spid(v.get("pid")), "name": ph_name(v.get("pid", 0)), "row": v.get("row"),
                 "donor": ph_name(v["row"]) if v.get("row") else "", "made": v.get("made", "")}
                for k, v in sorted(ph.load().items())]
    return {"rows": rows, "can_make": not miss, "why": PH_MISSING.format(miss) if miss else "", "file": rel(ph.DIR)}


@api("photo_head_donors")
def photo_head_donors(p):
    """[[dong engine, ten, mua]] cac dau quet (headclasscode 0) cua cau thu tim theo ten: o Dau mau."""
    e = engine_rows()
    q = str(p.get("q") or "").strip()
    if e is None or len(q) < 2:
        return {"rows": []}
    t, fld, by, mine = e
    top = photo_mod().ID_BASE
    out = {}
    for r in admin.search({"q": q, "size": 40})["rows"]:
        for k in mine.get(r["spid"] % 1000000, ()):
            if t.get(by[k], fld["headclasscode"][0]) + fld["headclasscode"][1] == 0 \
                    and t.get(by[k], fld["headassetid"][0]) + fld["headassetid"][1] < top:
                out.setdefault(k, [k, r["name"], k // 1000000])
        if len(out) >= 30:
            break
    return {"rows": list(out.values())}


@api("photo_head_make")
def photo_head_make(p):
    """Tao mat tu anh tai len (PNG <= PHOTO_MAX px) + 11 diem; row = dong engine donor (bo trong: tu goi y)."""
    pid = ph_pid(p)
    ph_ready()
    ph = photo_mod()
    import io
    import numpy as np
    from PIL import Image
    try:
        raw = base64.b64decode(str(p.get("png") or ""), validate=True)
        img = Image.open(io.BytesIO(raw))
        if img.format != "PNG" or max(img.size) > PHOTO_MAX or min(img.size) < 32 or len(raw) > 8 << 20:
            raise ValueError
        photo = np.asarray(img.convert("RGB"))
    except Exception:
        raise AdminError(f"Ảnh phải là PNG, cạnh dài tối đa {PHOTO_MAX} px (chọn lại ảnh trên trang này)")
    pts = p.get("points")
    n = len(ph.POINTS)
    if (not isinstance(pts, list) or len(pts) != n
            or not all(isinstance(q, list) and len(q) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in q)
                       and 0 <= q[0] <= photo.shape[1] and 0 <= q[1] <= photo.shape[0] for q in pts)):
        raise AdminError(f"Hãy đánh dấu đủ {n} điểm trên ảnh trước")
    if len({(round(q[0]), round(q[1])) for q in pts}) != n or abs(pts[1][0] - pts[0][0]) + abs(pts[1][1] - pts[0][1]) < 4:
        raise AdminError("Các điểm trùng nhau hoặc hai mắt quá sát: đánh dấu lại")
    row = p.get("row")
    if row in (None, "", 0, "0"):
        donors = ph_donors()
        head = ph.suggest(ph.look(photo, pts), donors, ph_nation(pid)) if donors else None
        if head is None:
            raise AdminError("Không tìm được đầu mẫu: nhập tên cầu thủ có mặt quét ở ô Đầu mẫu")
        row = donors[head][0]
    ph_save(pid, photo, pts, as_int(row, 1, 10 ** 9, "Đầu mẫu (dòng 3D)"))
    return photo_head({"pid": pid})


@api("photo_head_icon")
def photo_head_icon(p):
    """Anh cau thu cua game cho trang: anh (PNG base64), 11 diem YuNet tim (None neu khong thay mat), donor goi y."""
    pid = ph_pid(p)
    ph_ready()
    import io
    from PIL import Image
    photo, pts, row = ph_icon(pid)
    b = io.BytesIO()
    Image.fromarray(photo).save(b, "PNG")
    return {"png": base64.b64encode(b.getvalue()).decode(), "points": pts, "row": row,
            "donor": ph_name(row) if row else None}


@api("photo_head_club")
def photo_head_club(p):
    """Mat tu anh cau thu cho moi cau thu trong CLB (cac doi cua HLV) dang mang dau chung va chua co mat;
    redo: tao lai ca moi mat da co tu anh cau thu (donor chon lai; mat tu anh tai len cung bi thay)."""
    ph_ready()
    ph = photo_mod()
    redo = bool(p.get("redo"))
    with _hc().STATE_LOCK:  # chi doc danh sach the roi tha khoa ngay (viec nang o ngoai)
        st = _hc()._state()
        cards = {}
        for club in (st.get("teams") or {}).values():
            for c in club.get("players", []):
                spid = int(c.get("spid") or 0)
                if spid:
                    cards.setdefault(spid % 1000000, spid)
    have = {v.get("pid") for v in ph.load().values()}
    for pid in (have if redo else ()):
        cards.setdefault(pid, pid)
    made, skipped = [], []
    for pid, spid in sorted(cards.items()):
        if pid in have and not redo:
            continue
        if pid not in have:
            look, e = card_look(spid, pid), engine_rows()
            if not look or look["row"] is None or e is None:
                continue
            t, fld, by, _ = e
            if t.get(by[look["row"]], fld["headclasscode"][0]) + fld["headclasscode"][1] != 1:
                continue
        name = ph_name(pid)
        try:
            photo, pts, row = ph_icon(pid)
            if pts is None or row is None:
                skipped.append([pid, name, "không thấy khuôn mặt trong ảnh"])
                continue
            ph_save(pid, photo, pts, row)
        except AdminError as err:
            skipped.append([pid, name, str(err)])
            continue
        made.append([pid, name, ph_name(row)])
    return {"made": made, "skipped": skipped}


@api("photo_head_delete")
def photo_head_delete(p):
    pid, ph = ph_pid(p), photo_mod()
    with PH_LOCK:
        heads = ph.load()
        gone = [k for k, v in heads.items() if v.get("pid") == pid]
        if not gone:
            raise AdminError("Cầu thủ này chưa có mặt 3D từ ảnh")
        for hid in gone:
            del heads[hid]
            ph.remove(hid)
        ph.save(heads)
    return photo_head({"pid": pid})


admin.READ_ONLY.update({"photo_head", "photo_heads", "photo_head_donors"})
admin.NO_LOCK.update({"photo_head", "photo_heads", "photo_head_donors", "photo_head_make", "photo_head_icon",
                      "photo_head_club", "photo_head_delete"})
