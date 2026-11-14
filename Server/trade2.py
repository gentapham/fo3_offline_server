"""Trao doi cau thu v2 (features.teammanagement.playermanagement.trade2 = {}).

Co nay chuyen tab "Trao doi cau thu" (TeamManagementPlayerRecycleTabPage) sang che do v2:
diem trao doi (TP) = tong gia the x he so the dac biet x playertrade2_bonus[so the] x VIP,
"Buoc trao doi" 1..15 theo bang TRADE.tp (cap toi da cua the nhan duoc) va so de nghi.
  GET  /static/db/TRADE                 bang tinh (bonusplayergroup, trade2_limit, tp, ...)
  GET  /static/cache/TRADE_SPECIALPLAYERS  {spid: 1} = the co huy hieu "dac biet"
  GET  /players/trade2/info             {maxTP: nhom} (khung "the top"; khong gui spid -> khung trong)
  GET  /account/trade2/usage            {remainsec, usedcount, maxcount}: maxcount -1 = khong gioi han
  POST /players/trade2/proposal {players:[_id]} -> {result, props} (giong v1)
  POST /players/trade2/contract -> ep_trade_contract (khi trade3 bat, popup goi /trade3/contract)
"""
import bisect
import random
from contextlib import closing

hc = None
SPECIAL_SEASONS = (93, 30, 27, 28, 91, 70, 46, 64, 52)  # WL, UL, EL, UB, WB + ICONS, 20/19 WL, USS (ban CN)
SPECIAL_MIN_OVR = 80
SPECIAL_BONUS, SPECIAL_GRADE = 1.2, 5
# 15 buoc: TP toi da (EP) -> cap cao nhat co the nhan
TP_STEPS = ((1_000_000, 2), (3_000_000, 3), (10_000_000, 3), (30_000_000, 4), (100_000_000, 4),
            (300_000_000, 5), (1_000_000_000, 5), (3_000_000_000, 6), (10_000_000_000, 6),
            (30_000_000_000, 7), (100_000_000_000, 8), (300_000_000_000, 8), (1_000_000_000_000, 9),
            (10_000_000_000_000, 10), (9_000_000_000_000_000, 10))
BONUS = {**{str(n): round(1 + 0.02 * (n - 2), 2) for n in range(2, 11)}, "IGR": 1.0, "VIP": 1.1}
OFFERS = {str(n): 1 + (n >= 4) + (n >= 7) for n in range(0, 11)}  # de nghi thuong (+ PC + VIP)
VALUE_RANGE = (0.9, 1.3)  # gia tri moi de nghi = TP x khoang nay
_SPECIAL = {}


def trade_static():
    return {"bonusplayergroup": {"bonus": SPECIAL_BONUS, "grade": SPECIAL_GRADE},
            "playertrade2_bonus": BONUS,
            "legendgroup": list(SPECIAL_SEASONS),
            "playertrade2_normal": OFFERS,
            "tp": {str(tp): {"value": list(range(1, g + 1))} for tp, g in TP_STEPS},
            "trade2_limit": {"max": 0, "max_trade": hc.NO_LIMIT},
            "trade_effect_price": [10_000_000_000, 1_000_000_000, 100_000_000, 10_000_000, 1_000_000]}


def special_players():
    if not _SPECIAL:
        with closing(hc._db()) as c:
            for (s,) in c.execute(f"SELECT p.spid FROM players p WHERE p.spid / 1000000 IN "
                                  f"({','.join('?' * len(SPECIAL_SEASONS))}) AND {hc.POSOVR} >= ?",
                                  (*SPECIAL_SEASONS, SPECIAL_MIN_OVR)):
                _SPECIAL[str(s)] = 1
    return _SPECIAL


def trade_points(give):
    sp = special_players()
    tp = 0.0
    for p in give:
        w = SPECIAL_BONUS if str(p["spid"]) in sp and hc.grade_of(p) >= SPECIAL_GRADE else 1.0
        tp += hc.card_price(p["spid"], hc.grade_of(p)) * w
    return tp * BONUS.get(str(len(give)), 1) * BONUS["IGR"] * BONUS["VIP"]


def max_grade(tp):
    return min(hc.card_cap(), next((g for top, g in TP_STEPS if tp < top), TP_STEPS[-1][1]))  # + gioi han GM Tool


def pick(pool, target, gmax, rng):
    """Mot the gia tri gan target, cap <= gmax (pool = trade3_pool: (gia, spid, teamid, cap))."""
    k = bisect.bisect_left(pool, (target,))
    near = []
    lo, hi = k - 1, k
    while len(near) < 20 and (lo >= 0 or hi < len(pool)) and (hi - lo) < 20000:
        for j in (hi, lo):
            if 0 <= j < len(pool) and pool[j][3] <= gmax:
                near.append(pool[j])
        lo, hi = lo - 1, hi + 1
    return rng.choice(near or pool[max(0, k - 10):k + 10] or pool)


def props_for(give, rng):
    tp = trade_points(give)
    gmax = max_grade(tp)
    pool = hc.trade3_pool()
    normal = OFFERS[str(len(give))]
    props = []
    for i, boost in enumerate([None] * normal + ["igr", "vip"]):
        target = tp * rng.uniform(*VALUE_RANGE)
        price, s, t, g = hc.prime_pick(target, gmax, rng) or pick(pool, target, gmax, rng)  # 30%: PRIME / INFINITY PRIME
        offer = {"spid": s, "grade": g, "teamid": t, "price": price}
        if i == normal - 1 and normal > 1:  # de nghi cuoi kem them EP
            offer["ep"] = hc.sig3(max(1000, tp * rng.uniform(0.05, 0.15)))
        if boost:
            offer["boost"] = boost
        props.append(offer)
    return props


def ep_proposal(query, body):
    req = hc.json.loads(body or b"{}")
    st = hc._state(); slot = hc._default_slot(st); team = st["teams"][str(slot)]
    ids = [str(i) for i in req.get("players") or []]
    give = [p for p in team["players"] if hc.iid(p) in ids]
    if not 2 <= len(give) <= 10 or len(give) != len(set(ids)) or any(hc.in_squad(team, p) for p in give):
        return hc._json({"result": False, "reason": "invalid playerid"})
    if sum(hc.card_price(p["spid"], hc.grade_of(p)) for p in give) > hc.NO_LIMIT:
        return hc._json({"result": False, "reason": "limit trade price"})
    props = hc.trade_cap_props(props_for(give, random.Random()), give, max_grade(trade_points(give)))
    st["trade"] = {"players": [hc.iid(p) for p in give], "props": props}
    hc._save(st)
    return hc._json({"result": True, "props": props})


def ep_info(query, body):
    # Khong gui spid: khung "the top" hien TRADE2_BASIC (staticdata the day du cho playerCardInfo
    # khong tai tao duoc o server, gui thieu co the gay loi giao dien).
    return hc._json({str(tp): {} for tp, _ in TP_STEPS})


def ep_usage(query, body):
    # Nut "Trao doi": maxcount < 0 = khong gioi han -> client ghi TRADE_BUTTON, khong dem luot
    # (maxcount >= 0 -> TRADE2_REMAINCOUNT "({max - used}/{max})"; 0 -> "(0/0)" kho hieu).
    # remainsec > 0 -> nut bi khoa, dem nguoc TRADE2_REMAINTIME.
    return hc._json({"remainsec": 0, "usedcount": 0, "maxcount": -1})


def install(module):
    global hc
    hc = module
    hc.ENDPOINTS.update({
        "/static/db/TRADE": lambda q, b: hc._json(trade_static()),
        "/static/cache/TRADE_SPECIALPLAYERS": lambda q, b: hc._json(special_players()),
        "/players/trade2/info": ep_info,
        "/account/trade2/usage": ep_usage,
        "/players/trade2/proposal": ep_proposal,
        "/players/trade2/contract": hc.ep_trade_contract,
    })
