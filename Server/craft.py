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
         93001183, 27045661, 27000488, 27004231, 27005003, 91041236, 91013743, 91005471, 91005479,
         93001397, 30001397, 91001397)  # Zidane WL / UL / WB (them 2026-11-08)
# 12 manh dau = top the cac mua WL/UL/WB luc tao (CO DINH: truoc day tinh lai tu DB moi lan -> them Zidane
# vao DB lam ca danh sach lech 1 bac, ten manh khong khop anh / mang ghep trong kho doi sang cau thu khac)
BASE = (93190043, 93001109, 93168473, 93192181, 93166906, 93190053,  # WL: Pele, Maldini, Beckenbauer, Van Basten, Baresi, ...
        30037576, 30190042, 30238380,  # UL: Ronaldo, Maradona, Yashin
        91020801, 91158023, 91000041)  # WB: C. Ronaldo, Messi, Iniesta
PIECE_BASE = 87_100_000  # manh ghep: PIECE_BASE + stt (stt < 100, CONTRACT = PIECE_BASE + 100)
# Hinh rieng tung manh: externalAssets/shop/<id manh>.png (anh cau thu, tao offline); thieu -> PIECE_ICON
CONTRACT = 87_100_100
SUPER = 87_100_200  # sieu nguyen lieu WORLD BEST
PIECE_ICON, CONTRACT_ICON, SUPER_ICON = 80711, 80712, 80713
PIECES_NEEDED, CONTRACTS_NEEDED, SUPER_MAX = 10, 1, 5
RESULT_GRADE = 5
FEE = 0  # mien phi (SHOP_FREE / ban offline)
# ten ao trung nhau: Cristiano Ronaldo (20801) va Ronaldo Nazario (37576) deu la "Ronaldo"
NAME_FIX = {20801: "C. Ronaldo"}
NOUSE = "Vật phẩm này dùng trong Quản lý cầu thủ > Mảnh ghép cầu thủ."
RECIPES = {}  # recipeid: {"spid", "piece"}
# GM Tool > Goi the & Tui do > Hop dong cau thu (ban Indo e19f77e, issue #44): gamedata/craft.json
#   {"cards": [spid...] (danh sach ghep, theo thu tu; null = mac dinh BASE + EXTRA), "pieces", "fee", "grade",
#    "super_max", "ids": {spid: stt manh}}. Stt manh cua moi the co dinh mai mai (manh trong kho giu dung cau thu):
#   the mac dinh giu stt cu, the GM them nhan stt trong tiep theo (< 100). Bo the khoi danh sach: manh van con.
CONF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gamedata", "craft.json")
PIECE_IDS = {}  # spid: stt manh (moi the tung co cong thuc)


def load_conf():
    try:
        c = hc.json.load(open(CONF, encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        c = {}
    return c if isinstance(c, dict) else {}


def settings():
    c = load_conf()
    num = lambda k, d, lo, hi: max(lo, min(hi, int(c.get(k, d)))) if str(c.get(k, d)).lstrip("-").isdigit() else d
    return {"pieces": num("pieces", PIECES_NEEDED, 1, 999), "fee": num("fee", FEE, 0, 10 ** 12),
            "grade": num("grade", RESULT_GRADE, 1, 20), "super_max": num("super_max", SUPER_MAX, 0, 999),
            "cards": c.get("cards") if isinstance(c.get("cards"), list) else None}


def result_grade():
    cap = getattr(hc, "card_cap", None)
    return min(settings()["grade"], cap() if cap else 20)


def build():
    st = settings()
    pieces_needed = st["pieces"]
    items = {
        CONTRACT: ("Hợp đồng ghép cầu thủ", CONTRACT_ICON, "CRAFT_CONTRACT", {"nouse": NOUSE},
                   f"Cần {CONTRACTS_NEEDED} hợp đồng cho mỗi lần ghép thẻ ở tab Mảnh ghép cầu thủ."),
        SUPER: ("Siêu nguyên liệu WORLD BEST", SUPER_ICON, "CRAFT_MATERIAL", {"nouse": NOUSE},
                f"Thay thế mảnh ghép còn thiếu khi ghép thẻ (tối đa {st['super_max']} mảnh mỗi lần)."),
    }
    labels = {code: lab for lab, codes, _ in getattr(hc, "FA_SEASONS", ()) for code in codes}
    shop = os.path.join(hc.ASSETS, "externalAssets", "shop")
    cards = []  # (spid, playerid, nhan mua) theo stt
    with closing(hc._db()) as c:
        import gen_teams
        season_label = {season: label for season, label, _ in SEASONS}
        for spid in BASE:
            row = c.execute("SELECT playerid FROM players WHERE spid = ?", (spid,)).fetchone()
            cards.append((spid, row[0] if row else spid % 1000000, season_label.get(spid // 1000000, "")))
        have = {spid for spid, _, _ in cards}
        for spid in EXTRA:
            if spid not in have and c.execute("SELECT 1 FROM players WHERE spid = ?", (spid,)).fetchone():
                have.add(spid)
                cards.append((spid, spid % 1000000, labels.get(spid // 1000000, f"S{spid // 1000000}")))
        ids = {int(spid): n for n, (spid, _, _) in enumerate(cards, 1)}  # stt cu (giu nguyen thu tu xua)
        for k, v in (load_conf().get("ids") or {}).items():  # the GM Tool da them (stt luu trong craft.json)
            if str(k).isdigit() and isinstance(v, int) and 0 < v < 100 and v not in ids.values():
                ids.setdefault(int(k), v)
        PIECE_IDS.clear(); PIECE_IDS.update(ids)
        enabled = [int(x) for x in st["cards"] if str(x).isdigit()] if st["cards"] is not None else [s for s, _, _ in cards]
        RECIPES.clear()
        grade = result_grade()
        for spid, n in sorted(ids.items(), key=lambda kv: kv[1]):
            row = c.execute("SELECT playerid FROM players WHERE spid = ?", (spid,)).fetchone()
            if row is None:
                continue
            playerid = row[0]
            label = next((lab for s_, _, lab in cards if s_ == spid), None) or labels.get(spid // 1000000, f"S{spid // 1000000}")
            name = NAME_FIX.get(playerid) or (gen_teams.jersey_name(c, playerid) or str(spid)).title()
            piece = PIECE_BASE + n
            icon = piece if os.path.isfile(os.path.join(shop, f"{piece}.png")) else PIECE_ICON
            items[piece] = (f"Mảnh ghép {name} ({label})", icon, "PLAYER_PIECE", {"nouse": NOUSE},
                            f"Gom {pieces_needed} mảnh + {CONTRACTS_NEEDED} hợp đồng để ghép thẻ "
                            f"{name} {label} +{grade} (Quản lý cầu thủ > Mảnh ghép cầu thủ).")
        for spid in enabled:
            if spid in ids and (PIECE_BASE + ids[spid]) in items:
                RECIPES[1000 + ids[spid]] = {"spid": spid, "piece": PIECE_BASE + ids[spid]}
    return items


def assign_id(spid):
    """Stt manh cho the moi them (trong tiep theo, < 100); None khi da het cho."""
    if spid in PIECE_IDS:
        return PIECE_IDS[spid]
    used = set(PIECE_IDS.values())
    n = next((i for i in range(1, 100) if i not in used), None)
    if n is not None:
        PIECE_IDS[spid] = n
    return n


def reload():
    """Ap craft.json ngay (GM Tool luu): dung lai vat pham manh + cong thuc."""
    old = [i for i in hc.ITEMS if PIECE_BASE < i < PIECE_BASE + 100 or i in (CONTRACT, SUPER)]
    new = build()
    for i in old:
        if i not in new:
            hc.ITEMS.pop(i, None)
    hc.ITEMS.update(new)


def recipe_json(rid):
    r, st, grade = RECIPES[rid], settings(), result_grade()
    return {"recipeid": rid, "category": "player",
            "result": {"normal": {"id": r["spid"], "grade": grade,
                                  "price": hc.card_price(r["spid"], grade)}},
            "materials": [{"itemid": r["piece"], "count": st["pieces"],
                           "replace": {"itemid": SUPER, "max": st["super_max"]}},
                          {"itemid": CONTRACT, "count": CONTRACTS_NEEDED}],
            "money": {"ep": st["fee"]}}


def ep_recipes(query, body):
    return hc._json({str(rid): recipe_json(rid) for rid in RECIPES})


def fail(reason):
    return hc._json({"result": False, "reason": reason})


def ep_mixin(rid, query, body):
    rid = int(rid)
    if rid not in RECIPES:
        return fail("Công thức không tồn tại.")
    r, cfg = RECIPES[rid], settings()
    PIECES_NEEDED, SUPER_MAX, FEE, RESULT_GRADE = cfg["pieces"], cfg["super_max"], cfg["fee"], result_grade()
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
