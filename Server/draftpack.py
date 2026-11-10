"""Goi chon cau thu (draft pack, port fo3-indo-server issue #27): "chon N cau thu cua 1 mua, mo ra 1".

Client (TeamManagementDraftPackScreen, category DRAFTPACK): Vat pham > Cau thu > Dung mo man chon
cho 1 mua (staticdata.draft {season, grade, availables}).
- GET /item/draftpack/<itemid>/players/<n> = n the gia tri nhat cua goi [{spid, price}]: lan dau
  (n = availables) dien danh sach chon, lan 2 (n = 100) la danh sach tim kiem.
- The tim theo ten ngoai danh sach do: POST /transfer/price/default/<grade> {spids} -> {prices [{spid, price}]}.
- "Xac nhan" mo man mo the: POST /item/<_id>/use {id, spids} -> 1 trong cac the da chon (ngau nhien
  deu nhau), o hang cua goi.
Icon: externalAssets/shop/971000xx.png (ve rieng bang PIL, kieu icon goi cau thu cua client).
"""
import json
import random
import re

hc = None

DRAFT_PACKS = {  # itemid (= icon): (ten, {season, draft = so cau thu chon, grade})
    97100001: ("Gói chọn 5 cầu thủ - Mùa 2017", {"season": 88, "all": True, "draft": 5, "grade": 1, "icon": 97100001}),
    97100002: ("Gói chọn 3 cầu thủ - Mùa 06", {"season": 6, "all": True, "draft": 3, "grade": 1, "icon": 97100002}),
    97100003: ("Gói chọn 3 cầu thủ - World Legend", {"season": 93, "all": True, "draft": 3, "grade": 1, "icon": 97100003}),
    97100004: ("Gói chọn 3 cầu thủ - World Best", {"season": 91, "all": True, "draft": 3, "grade": 1, "icon": 97100004}),
    97100005: ("Gói chọn 3 cầu thủ - Ultimate Legend", {"season": 30, "all": True, "draft": 3, "grade": 1, "icon": 97100005}),
    97100006: ("Gói chọn 5 cầu thủ - Mùa 2017 +5", {"season": 88, "all": True, "draft": 5, "grade": 5, "icon": 97100006}),
}


def is_draft(itemid):
    return bool(hc.PACKS.get(itemid, ("", {}))[1].get("draft"))


def draft_grade(itemid):
    return max(1, min(hc.card_cap(), int(hc.PACKS[itemid][1].get("grade") or 1)))  # + gioi han GM Tool


def draft_top(itemid, n):
    """n the gia tri nhat cua goi (lua chon mac dinh va danh sach tim)."""
    return sorted((s for s, _ in hc.pack_pool(itemid)), key=lambda s: (-hc.card_price(s), s))[:max(0, n)]


def ep_draftpack_players(itemid, n, query, body):
    itemid = int(itemid)
    if not is_draft(itemid):
        return hc._json([])
    g = draft_grade(itemid)
    return hc._json([{"spid": s, "price": hc.card_price(s, g)} for s in draft_top(itemid, min(int(n), 500))])


def ep_transfer_price_default(grade, query, body):
    try:
        req = json.loads(body or b"{}")
    except ValueError:
        req = {}
    spids = []
    for s in (req.get("spids") if isinstance(req, dict) else None) or []:
        try:
            spids.append(int(s))
        except (TypeError, ValueError):
            pass
    g = max(0, min(hc.MAX_GRADE, int(grade)))
    return hc._json({"prices": [{"spid": s, "price": hc.card_price(s, g)} for s in spids[:500] if hc.on_market(s)]})


def install(module):
    global hc
    hc = module
    for itemid in DRAFT_PACKS:
        for table in (hc.PACKS, hc.ITEMS, hc.PACKAGES, hc.MONTHLY):
            assert itemid not in table or table is hc.PACKS and hc.PACKS[itemid] == DRAFT_PACKS[itemid], itemid
    hc.PACKS.update(DRAFT_PACKS)
    base_grades, base_price, base_def, base_use = hc.pack_grades, hc.pack_price, hc._item_def, hc.ep_item_use

    def pack_grades(itemid):
        if is_draft(itemid):  # the mo ra o dung hang cua goi (man chon hien gia o hang do)
            return [(draft_grade(itemid), 1)]
        return base_grades(itemid)

    prices = {}

    def pack_price(itemid):
        if is_draft(itemid):  # gia tri trung binh cua N the mac dinh o hang cua goi
            if itemid not in prices:
                top = draft_top(itemid, hc.PACKS[itemid][1]["draft"])
                g = draft_grade(itemid)
                prices[itemid] = hc.sig3(sum(hc.card_price(s, g) for s in top) / len(top)) if top else 0
            return prices[itemid]
        return base_price(itemid)

    def cache_clear():  # custom_packs_sync goi pack_price.cache_clear()
        prices.clear()
        base_price.cache_clear()
    pack_price.cache_clear = cache_clear

    def _item_def(itemid):
        d = base_def(itemid)
        if itemid in hc.PACKS and is_draft(itemid) and isinstance(d, dict):
            f = hc.PACKS[itemid][1]
            d = dict(d, category="DRAFTPACK", type="DRAFTPACK", draft={
                "season": (f.get("seasons") or (f.get("season"),))[0], "grade": draft_grade(itemid), "availables": f["draft"]},
                description=f"Chọn {f['draft']} cầu thủ của mùa này, mở ra ngẫu nhiên 1 trong số đó (thẻ +{draft_grade(itemid)}).")
        return d

    def ep_item_use(item_id, query, body):
        st = hc._state(); slot = hc._default_slot(st); coach = hc.my_coach(st); team = st["teams"][str(slot)]
        it = next((i for i in coach.get("items", []) if i["_id"] == item_id and i["remains"] > 0), None)
        if it is None or not is_draft(it["itemid"]):
            return base_use(item_id, query, body)
        try:
            req = json.loads(body or b"{}")
        except ValueError:
            req = {}
        if len(team["players"]) >= hc.MAX_PLAYERS:
            return hc._json({"result": False, "reason": "CLB đã đầy, hãy thanh lý bớt cầu thủ."})
        pool = {s for s, _ in hc.pack_pool(it["itemid"])}
        need = hc.PACKS[it["itemid"]][1]["draft"]
        try:
            picks = sorted({int(s) for s in (req.get("spids") if isinstance(req, dict) else None) or []})
        except (TypeError, ValueError):
            picks = []
        if len(picks) != need or not set(picks) <= pool:
            return hc._json({"result": False, "reason": f"Hãy chọn đủ {need} cầu thủ của mùa này."})
        spid, g = random.choice(picks), draft_grade(it["itemid"])
        p = hc.add_player(team, spid, g)
        it["remains"] -= 1
        if not it["remains"]:
            coach["items"].remove(it)
        hc._save(st)
        return hc._json({"result": True, "player": {"_id": p["_id"], "spid": spid, "grade": g, "price": hc.card_price(spid, g)},
                         "resources": hc.coach_me_resources(st, slot)})

    hc.ROUTES[:] = [(pat, ep_item_use if fn is base_use else fn) for pat, fn in hc.ROUTES]
    hc.ROUTES[:0] = [
        (re.compile(r"/item/draftpack/(\d+)/players/(\d+)"), ep_draftpack_players),
        (re.compile(r"/transfer/price/default/(\d+)"), ep_transfer_price_default),
    ]
    hc.pack_grades, hc.pack_price, hc._item_def, hc.ep_item_use = pack_grades, pack_price, _item_def, ep_item_use
