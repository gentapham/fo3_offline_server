"""Ghep manh cau thu (tab "Manh ghep cau thu", TeamManagementPlayerCraftTabPage) + sieu nguyen
lieu WORLD BEST (features.teammanagement.playermanagement.craft.material = {}).

  GET  /mixin/recipes -> {key: {recipeid, category:"player", result:{normal:{id, grade, price}},
        materials:[{itemid:<manh>, count, replace:{itemid:<sieu nguyen lieu>, max}},
                   {itemid:<hop dong>, count}], money:{ep}}}
  POST /mixin/<recipeid> {materials:[{type:"item", id:<_id trong kho>, count}, ...]}
        -> {result, results:[{spid, grade}], resources}
Client chi doc hop dong/sieu nguyen lieu tu cong thuc dau tien -> moi cong thuc dung chung 1 hop
dong va 1 sieu nguyen lieu. Moi cong thuc deu co replace (bat buoc khi co "material" bat).
Manh ghep / hop dong / sieu nguyen lieu ban mien phi trong Cua hang (tab Dung duoc).
"""
import os
import re
from contextlib import closing

import item_ext

hc = None
SEASONS = ((93, "WL", 6), (30, "UL", 3), (91, "WB", 3))  # (ma mua, nhan, so cong thuc)
# Huyen thoai them sau (CHI NOI THEM VAO CUOI - stt quyet dinh id manh ghep/cong thuc da co trong kho):
# spid = the tot nhat cua cau thu trong cac mua huyen thoai (WL/UL/WB/EL/LP...). Khong co trong DB:
# Zidane, Cruyff, Baggio, Puskas, Eusebio (Beckham chi co the 2010).
EXTRA = (30028130, 30001625, 91138449, 93005589, 23001238, 91007763, 93001075, 93013128, 93006235,
         91054050, 91190871, 91001179, 91010535, 93191189, 93226764, 93214100, 93004000, 93001040,
         93001183, 27045661, 27000488, 27004231, 27005003, 91041236, 91013743, 91005471, 91005479)
PIECE_BASE = 87_100_000  # manh ghep: PIECE_BASE + stt (stt < 100, CONTRACT = PIECE_BASE + 100)
# Hinh rieng tung manh: externalAssets/shop/<id manh>.png (anh cau thu, tao offline); thieu -> PIECE_ICON
CONTRACT = 87_100_100
SUPER = 87_100_200  # sieu nguyen lieu WORLD BEST
PIECE_ICON, CONTRACT_ICON, SUPER_ICON = 80711, 80712, 80713
PIECES_NEEDED, CONTRACTS_NEEDED, SUPER_MAX = 10, 1, 5
RESULT_GRADE = 5
FEE = 0  # mien phi (SHOP_FREE / ban offline)
NOUSE = "Vật phẩm này dùng trong Quản lý cầu thủ > Mảnh ghép cầu thủ."
RECIPES = {}  # recipeid: {"spid", "piece"}


def build():
    items = {
        CONTRACT: ("Hợp đồng ghép cầu thủ", CONTRACT_ICON, "CRAFT_CONTRACT", {"nouse": NOUSE},
                   f"Cần {CONTRACTS_NEEDED} hợp đồng cho mỗi lần ghép thẻ ở tab Mảnh ghép cầu thủ."),
        SUPER: ("Siêu nguyên liệu WORLD BEST", SUPER_ICON, "CRAFT_MATERIAL", {"nouse": NOUSE},
                f"Thay thế mảnh ghép còn thiếu khi ghép thẻ (tối đa {SUPER_MAX} mảnh mỗi lần)."),
    }
    labels = {code: lab for lab, codes, _ in getattr(hc, "FA_SEASONS", ()) for code in codes}
    shop = os.path.join(hc.ASSETS, "externalAssets", "shop")
    cards = []  # (spid, playerid, nhan mua) theo stt
    with closing(hc._db()) as c:
        import gen_teams
        for season, label, count in SEASONS:
            rows = c.execute(f"SELECT p.spid, p.playerid, {hc.POSOVR} AS o FROM players p "
                             f"WHERE p.spid / 1000000 = ? ORDER BY o DESC, p.spid LIMIT ?", (season, count)).fetchall()
            cards += [(spid, playerid, label) for spid, playerid, _ in rows]
        have = {spid for spid, _, _ in cards}
        for spid in EXTRA:
            if spid not in have and c.execute("SELECT 1 FROM players WHERE spid = ?", (spid,)).fetchone():
                have.add(spid)
                cards.append((spid, spid % 1000000, labels.get(spid // 1000000, f"S{spid // 1000000}")))
        for n, (spid, playerid, label) in enumerate(cards, 1):
            assert n < 100, "PIECE_BASE + n trung CONTRACT"
            name = (gen_teams.jersey_name(c, playerid) or str(spid)).title()
            piece = PIECE_BASE + n
            icon = piece if os.path.isfile(os.path.join(shop, f"{piece}.png")) else PIECE_ICON
            items[piece] = (f"Mảnh ghép {name} ({label})", icon, "PLAYER_PIECE", {"nouse": NOUSE},
                            f"Gom {PIECES_NEEDED} mảnh + {CONTRACTS_NEEDED} hợp đồng để ghép thẻ "
                            f"{name} {label} +{RESULT_GRADE} (Quản lý cầu thủ > Mảnh ghép cầu thủ).")
            RECIPES[1000 + n] = {"spid": int(spid), "piece": piece}
    return items


def recipe_json(rid):
    r = RECIPES[rid]
    return {"recipeid": rid, "category": "player",
            "result": {"normal": {"id": r["spid"], "grade": RESULT_GRADE,
                                  "price": hc.card_price(r["spid"], RESULT_GRADE)}},
            "materials": [{"itemid": r["piece"], "count": PIECES_NEEDED,
                           "replace": {"itemid": SUPER, "max": SUPER_MAX}},
                          {"itemid": CONTRACT, "count": CONTRACTS_NEEDED}],
            "money": {"ep": FEE}}


def ep_recipes(query, body):
    return hc._json({str(rid): recipe_json(rid) for rid in RECIPES})


def fail(reason):
    return hc._json({"result": False, "reason": reason})


def ep_mixin(rid, query, body):
    rid = int(rid)
    if rid not in RECIPES:
        return fail("Công thức không tồn tại.")
    r = RECIPES[rid]
    req = hc.json.loads(body or b"{}")
    st = hc._state(); slot = hc._default_slot(st); team = st["teams"][str(slot)]; coach = hc.my_coach(st)
    inv = {i["itemid"]: i for i in coach.get("items", []) if i.get("remains", 0) > 0}
    by_id = {i["_id"]: i for i in inv.values()}
    # so sieu nguyen lieu client xin dung (theo _id trong kho hoac itemid)
    want = 0
    for m in req.get("materials") or []:
        it = by_id.get(str(m.get("id"))) or (inv.get(m.get("id")) if isinstance(m.get("id"), int) else None)
        if it is not None and it["itemid"] == SUPER:
            want += max(0, int(m.get("count") or 0))
    piece, contract, sup = inv.get(r["piece"]), inv.get(CONTRACT), inv.get(SUPER)
    have = piece["remains"] if piece else 0
    use_super = min(want, SUPER_MAX, sup["remains"] if sup else 0, PIECES_NEEDED)
    use_piece = PIECES_NEEDED - use_super
    if have < use_piece:
        return fail(f"Không đủ mảnh ghép (cần {use_piece}, có {have}).")
    if (contract["remains"] if contract else 0) < CONTRACTS_NEEDED:
        return fail("Không đủ hợp đồng ghép cầu thủ.")
    if len(team["players"]) >= hc.MAX_PLAYERS:
        return fail("CLB đã đầy, hãy thanh lý bớt cầu thủ.")
    if hc.coach_me(st, slot)["money"]["ep"] < FEE:
        return fail("Không đủ EP.")
    for it, n in ((piece, use_piece), (contract, CONTRACTS_NEEDED), (sup, use_super)):
        if it is not None and n:
            it["remains"] -= n
            if it["remains"] <= 0:
                coach["items"].remove(it)
    hc.add_ep(st, -FEE)
    p = hc.add_player(team, r["spid"], RESULT_GRADE)
    hc._save(st)
    return hc._json({"result": True, "results": [{"_id": p["_id"], "spid": r["spid"], "grade": RESULT_GRADE}],
                     "resources": hc.coach_me_resources(st, slot)})


def install(module):
    global hc
    hc = module
    item_ext.install(hc)
    item_ext.add_items(build())
    hc.ENDPOINTS["/mixin/recipes"] = ep_recipes
    hc.ROUTES[:0] = [(re.compile(r"/mixin/(\d+)"), ep_mixin)]
