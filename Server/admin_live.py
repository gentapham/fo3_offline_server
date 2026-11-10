"""GM Tool: thuong thoi gian choi, trinh sua nhiem vu tuan, Live Boost, chat kenh + tin bong da (port
fo3-server-indo a0b49dd). Nap boi admin.py (dang ky API khi nap admin), giong admin_db.py."""
import os
import time

import admin
from admin import api, AdminError, rel


def _hc():
    return admin.hc


# ---------------------------------------------------------------- Thuong thoi gian choi (tab Qua hang ngay)
@api("playtime")
def playtime(p):
    """Cac moc + thoi gian choi hom nay va so moc da nhan cua HLV dang chon."""
    h = _hc()
    ev = h.events
    st = h._state()
    c = h.my_coach(st)
    minutes = rewarded = 0
    if st.get("coaches") and not c.get("temp"):
        d, secs = ev.played_today(dict(c))  # ban sao: trang doc khong ghi gi vao state
        minutes, rewarded = secs // 60, d["rewarded"]
    return {"table": ev.playtime_table(), "minutes": minutes, "rewarded": rewarded, "file": rel(ev.PLAYTIME_FILE),
            "packs": [[i, h.item_def(i).get("name", str(i))] for i in h.PACKS]}


E_PT = "Bảng thưởng thời gian chơi không hợp lệ (1-10 mốc, phút 1-1440 tăng dần, mỗi mốc có EP hoặc gói, EP tối đa 10.000.000.000, 1-99 gói)."


@api("playtime_set")
def playtime_set(p):
    """gamedata/playtime_rewards.json: [{minutes, ep, pack, count}], phut tang dan; reset = mac dinh."""
    h = _hc()
    ev = h.events
    if p.get("reset"):
        if os.path.isfile(ev.PLAYTIME_FILE):
            os.remove(ev.PLAYTIME_FILE)
        return playtime(p)
    table = []
    for r in p.get("table") or []:
        try:
            minutes, ep = int(r.get("minutes")), int(r.get("ep") or 0)
            pack = int(r["pack"]) if r.get("pack") else None
            count = int(r.get("count") or 1)
        except (TypeError, ValueError, AttributeError):
            raise AdminError(E_PT)
        if not 1 <= minutes <= 1440 or not 0 <= ep <= 10 ** 10 or (pack is not None and pack not in h.PACKS) \
                or not 1 <= count <= 99 or (not ep and pack is None) or (table and minutes <= table[-1]["minutes"]):
            raise AdminError(E_PT)
        table.append({"minutes": minutes, **({"ep": ep} if ep else {}), **({"pack": pack, "count": count} if pack else {})})
    if not 1 <= len(table) <= 10:
        raise AdminError(E_PT)
    ev.save_playtime(table)
    return playtime(p)


# ---------------------------------------------------------------- Trinh sua nhiem vu tuan (tab Nhiem vu)
@api("quests")
def quests(p):
    h = _hc()
    m = h.manager
    cfg, end = m.missions_cfg(), m.week_end()
    week = lambda e: [{"name": n, "stat": s, "todo": t, "gp": g} for n, s, t, g in m.week_quests(cfg, e)]
    return {"quests": cfg["quests"], "questcount": cfg["questcount"], "gpshop": cfg["gpshop"],
            "stats": {s: m.MISSION_TEXT[s]["vi"][1] for s in m.QUEST_STATS},
            "items": [[i, h.item_def(i).get("name", str(i))] for i in (*h.PACKS, *h.ITEMS)],
            "questmax": m.QUEST_MAX, "gpshopmax": m.GP_SHOP_MAX, "enabled": cfg["enabled"],
            "thisweek": week(end), "nextweek": week(end + 7 * 86400), "weekend": h.w3c(end), "file": rel(m.MISSIONS_FILE)}


@api("quests_set")
def quests_set(p):
    try:
        _hc().manager.set_quests(p)
    except ValueError as e:
        raise AdminError(str(e))
    return quests(p)


# ---------------------------------------------------------------- Live Boost (tab Thi truong)
@api("liveboost")
def liveboost(p):
    h = _hc()
    lb = h.liveboost
    data, cfg = lb.stored(), lb.settings()
    out = {**cfg, "file": rel(lb.DATA_FILE), "status": dict(lb.STATUS), "now": time.time(), "group": lb.LIVEBOOST_GROUP,
           "rewardep": lb.reward_ep(), "own": lb.LIVEBOOST_OWN, "data": None}
    if data:
        st = h._state()
        owned = lb.owned_count(st, data) if st.get("coaches") else 0
        out["data"] = {"game": data["game"], "week": data["week"], "weekid": lb.weekid(data), "released": data["released"],
                       "fetched": data.get("fetched", 0), "expires": data["released"] + lb.WEEK, "owned": owned,
                       "missing": data.get("missing", []),
                       "players": [{"pid": x["pid"], "name": x.get("name") or str(x["pid"]),
                                    "overall": x.get("overall", 0), "position": x["position"], "best11": x["best11"]}
                                   for x in data["players"]]}
    return out


@api("liveboost_set")
def liveboost_set(p):
    lb = _hc().liveboost
    d = {}
    if "enabled" in p:
        d["enabled"] = bool(p["enabled"])
    try:
        if "boost" in p:
            d["boost"] = int(p["boost"])
        if "reward_ep" in p:
            d["reward_ep"] = int(p["reward_ep"])
    except (TypeError, ValueError):
        raise AdminError("Giá trị không hợp lệ")
    if not 1 <= d.get("boost", 5) <= 10:
        raise AdminError("Điểm cộng Live Boost phải từ 1 đến 10")
    if not 0 <= d.get("reward_ep", 0) <= 10 ** 10:
        raise AdminError("EP thưởng phải từ 0 đến 10.000.000.000")
    lb.save_settings(d)
    return liveboost(p)


@api("liveboost_refresh")
def liveboost_refresh(p):
    """Tai lai Team of the Week tu fut.gg trong nen (khong giu khoa, khong chan game)."""
    _hc().liveboost.fetch_background(force=True)
    return {"started": True}


# ---------------------------------------------------------------- Chat kenh + tin bong da (tab Xep hang HLV ao)
@api("chat_settings")
def chat_settings(p):
    ch = _hc().chat
    with ch._NEWS_LOCK:
        ch._load_cache()  # tin da luu (luong nen chua chay luc vua bat server)
    n = ch.NEWS
    regions = dict.fromkeys(i["region"] for i in n["items"])
    return {**ch.settings(), "file": rel(ch.SETTINGS_FILE),
            "regions": {k: v[0] for k, v in ch.NEWS_REGIONS.items()}, "globalname": ch.NEWS_GLOBAL[0],
            "topics": [[(ch.NEWS_REGIONS[r][0] if r else ch.NEWS_GLOBAL[0]),
                        [i["title"] for i in n["items"] if i["region"] == r][:8]] for r in regions],
            "topicsat": n["at"], "error": n.get("error", ""), "count": len(n["items"])}


@api("chat_settings_set")
def chat_settings_set(p):
    ch = _hc().chat
    before = ch.settings()
    try:
        after = ch.save_settings({k: p[k] for k in ("vmchat", "news", "newsregions") if k in p})
    except ValueError as e:
        raise AdminError(str(e))
    if after["news"] and (after["newsregions"] != before["newsregions"] or not before["news"]):
        ch.news_background()  # tin cua cac nuoc vua tick: lay ngay trong nen
    return chat_settings(p)


@api("news_refresh")
def news_refresh(p):
    _hc().chat.news_background(force=True)
    return {"started": True}


admin.READ_ONLY.update({"playtime", "quests", "liveboost", "chat_settings"})
admin.NO_LOCK.update({"liveboost_refresh", "news_refresh", "chat_settings", "chat_settings_set"})
