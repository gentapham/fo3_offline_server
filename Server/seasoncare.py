"""Nang cap bang vat pham (features.seasoncare = {}) - nut "Nang cap VP" trong tab Nang cap v2.5.

EnhancePlayerPanel (bat bang featuresAt("/").checkOne("seasoncare")) them danh sach vat pham
staticdata.type "ITEM_ENHANCEMENT" thay cho the nguyen lieu thu hai:
  GET /player/<t>/enhanceseasoncare/<itemid>/preview/<diem 0|1>/<bua|-1>/<su kien|-1>
  PUT /player/<t>/enhanceseasoncare/<itemid>/<diem 0|1>/<bua|-1>/<su kien|-1>
<itemid> = id vat pham tinh (khong phai _id trong kho). Giong enhance2.5 (cung bang
PE2STATICVALUES, diem nang cap, phuc hoi /player/enhance2.5/restore) nhung tieu hao 1 vat pham
thay vi gop the. Client loc: season (danh sach ma mua, None = moi mua), target.grade >= mingrade,
target.price <= pricemax. Server kiem tra them maxgrade.
"""
import random
import re

import item_ext

hc = None
LEGEND_SEASONS = "93,30,27,28,91,23,22,24,94,76"  # WL, UL, EL, UB, WB, LP, CP, CC, PRIME, INFINITY


def _sd(season, mingrade, maxgrade):
    return {"type": "ITEM_ENHANCEMENT", "season": season, "mingrade": mingrade, "maxgrade": maxgrade,
            "pricemin": 0, "pricemax": 9_000_000_000_000_000}


NOUSE = "Thẻ nguyên liệu chỉ dùng trong màn Nâng cấp cầu thủ (nút Nâng cấp VP)."
ITEMS = {  # itemid: (ten, icon, type, effect, mo ta)
    80601: ("Thẻ nguyên liệu nâng cấp", 80611, "ITEM_ENHANCEMENT",
            {"mult": 1.0, "staticdata": _sd(None, 1, 19), "nouse": NOUSE},
            "Dùng thay thẻ nguyên liệu khi nâng cấp (nút Nâng cấp VP). Mọi mùa, thẻ +1 đến +19; "
            "tỉ lệ như dùng thẻ cùng cấp."),
    80602: ("Thẻ nguyên liệu Huyền thoại", 80612, "ITEM_ENHANCEMENT",
            {"mult": 1.5, "staticdata": _sd(LEGEND_SEASONS, 1, 19), "nouse": NOUSE},
            "Dùng thay thẻ nguyên liệu khi nâng cấp (nút Nâng cấp VP). Chỉ thẻ mùa huyền thoại "
            "(WL, UL, EL, UB, WB, LP, CP, CC), +1 đến +19; tỉ lệ x1,5."),
    80603: ("Thẻ nâng cấp chắc chắn", 80613, "ITEM_ENHANCEMENT",
            {"sure": True, "staticdata": _sd(None, 1, 19), "nouse": NOUSE},
            "Dùng thay thẻ nguyên liệu khi nâng cấp (nút Nâng cấp VP). Mọi mùa, thẻ +1 đến +19; "
            "chắc chắn thành công."),
}
EMPTY_PREVIEW = {"grade": 0, "price": 0, "levelinfo": {"level": 1, "exp": 0}, "exp": 0,
                 "enhancepay": 0, "needes": 0, "probability": 0}


def season_ok(season, spid):
    return season is None or str(int(spid) // 1_000_000) in str(season).split(",")


def find_target(team, a):
    p = next((x for x in team["players"] if hc.iid(x) == str(a)), None)
    return p if p is not None and hc.grade_of(p) < hc.card_cap() else None  # GM Tool: cap the toi da


def find_item(coach, target, itemid):
    """Vat pham ITEM_ENHANCEMENT trong kho, hop le voi the muc tieu, hoac None."""
    try:
        itemid = int(itemid)
    except (TypeError, ValueError):
        return None
    if itemid not in ITEMS:
        return None
    it = next((i for i in coach.get("items", []) if i["itemid"] == itemid and i.get("remains", 0) > 0), None)
    sd = ITEMS[itemid][3]["staticdata"]
    g = hc.grade_of(target)
    if it is None or not season_ok(sd["season"], target["spid"]) or not sd["mingrade"] <= g <= sd["maxgrade"]:
        return None
    if hc.card_price(target["spid"], g) > sd["pricemax"]:
        return None
    return it


def rate(target, it, use_point, boost_eff):
    fake = {"spid": target["spid"], "grade": hc.grade_of(target)}  # vat pham = the cung cap
    r = hc.enh25_rate(target, fake, use_point, boost_eff)
    eff = ITEMS[it["itemid"]][3]
    return 1.0 if eff.get("sure") else min(1.0, r * eff.get("mult", 1))


def boost_of(coach, target, boost):
    if boost == "-1":
        return None, None
    b = hc.enh25_item(coach, target, boost)
    return b, (hc.ITEMS[b["itemid"]][3] if b else None)


def ep_preview(a, itemid, use_point, boost, event, query, body):
    st = hc._state(); team = st["teams"][str(hc._default_slot(st))]; coach = hc.my_coach(st)
    pa = find_target(team, a)
    it = find_item(coach, pa, itemid) if pa is not None else None
    if it is None:
        return hc._json(EMPTY_PREVIEW)
    _, beff = boost_of(coach, pa, boost)
    g = hc.grade_of(pa) + 1
    lv = hc.player_levelinfo(pa)
    return hc._json({"result": True, "grade": g, "price": hc.card_price(pa["spid"], g), "levelinfo": lv,
                     "exp": lv.get("exp", 0), "needes": hc.enh25_need_es(pa),
                     "enhancepay": hc.enhance_cost(pa, None, rate(pa, it, use_point == "1", beff), True,
                                                   hc.shop_worth(it["itemid"])),
                     "probability": round(rate(pa, it, use_point == "1", beff) * 100)})


def consume(coach, it):
    it["remains"] -= 1
    if it["remains"] <= 0:
        coach["items"].remove(it)


def ep_enhance(a, itemid, use_point, boost, event, query, body):
    st = hc._state(); slot = hc._default_slot(st); team = st["teams"][str(slot)]; coach = hc.my_coach(st)
    pa = find_target(team, a)
    if pa is None:
        return hc._json({"result": False, "reason": hc.cap_reason(team, a)})
    it = find_item(coach, pa, itemid)
    if it is None:
        return hc._json({"result": False, "reason": "expired item"})
    need = hc.enh25_need_es(pa)
    b, beff = boost_of(coach, pa, boost)
    if boost != "-1" and b is None:
        return hc._json({"result": False, "reason": "expired item"})
    use = use_point == "1" and int(coach.get("es", 0)) >= need
    chance = rate(pa, it, use, beff)
    cost = hc.enhance_cost(pa, None, chance, True, hc.shop_worth(it["itemid"]))
    if hc.coach_me(st, slot)["money"]["ep"] < cost:
        return hc._json({"result": False, "reason": "Invalid target ep price"})
    hc.add_ep(st, -cost)
    if use:
        coach["es"] = int(coach.get("es", 0)) - need
    consume(coach, it)
    if b is not None:
        consume(coach, b)
    before = hc.grade_of(pa)
    success = random.random() < chance
    out = {"result": True, "success": success}
    if success:
        pa["grade"] = before + 1
        out.update(grade=before + 1, rgrade=before + 1, rprice=hc.card_price(pa["spid"], before + 1),
                   attribPlus=hc.GRADE_BOOST[before + 1] - hc.GRADE_BOOST[before])
        coach.pop("enh25", None)
    else:  # nhu enhance2.5: phuc hoi lan dau mien phi, 1 lan phuc hoi lai ton diem
        rg = hc.enh25_roll_restore(before)
        grant = 20 + 10 * before
        pa["grade"] = rg
        coach["es"] = int(coach.get("es", 0)) + grant
        usepoint = hc.restore_cost(pa, before, rg, need)
        coach["enh25"] = {"id": hc.iid(pa), "before": before, "price": usepoint["price"], "currency": usepoint["currency"],
                          "used": rg == before}
        out.update(grade=rg, rgrade=rg, rprice=hc.card_price(pa["spid"], rg), grantes=grant,
                   usepoint=usepoint, attribPlus=0)
    hc.mission_event(st, upgrade=int(success))
    hc._save(st)
    out["resources"] = hc.coach_me_resources(st, slot) + hc.achievement_resources(st)
    return hc._json(out)


def install(module):
    global hc
    hc = module
    item_ext.install(hc)
    item_ext.add_items(ITEMS)
    hc.ROUTES[:0] = [
        (re.compile(r"/player/(\w+)/enhanceseasoncare/(\d+)/preview/([01])/(-?\w+)/(-?\w+)/?"), ep_preview),
        (re.compile(r"/player/(\w+)/enhanceseasoncare/(\d+)/([01])/(-?\w+)/(-?\w+)/?"), ep_enhance),
    ]
