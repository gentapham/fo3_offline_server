"""Thuong len cap HLV (LV2..100) - port tu fo3-server-indo (issue #35, bang thuong FO3 cua fifaaddict).

Moi cap HLV dat duoc -> EP + vat pham vao Tui do ("Thuong len cap HLV Lv.x"). Man ket qua sau tran
hien popup len cap (PostMatchRewardScreen: GET /coach/me/level/reward = cac vat pham vua nhan) va
bang thuong cac cap sau (nut i: GET /static/db/REWARDS/coachlevelup).
Moi lan gain_exp cua HLV (tran dau, cuoi mua giai, the EXP HLV) deu tinh. Khong tra bu cho cap da
co truoc khi cap nhat.
"""
import time

hc = None

# EP cap 2..100 (bang FO3)
LEVEL_EP = (50_000,) * 6 + (60_000, 70_000, 80_000, 90_000, 110_000, 120_000, 130_000, 140_000, 150_000, 160_000,
            170_000, 180_000, 200_000, 210_000, 220_000, 230_000, 240_000, 260_000, 270_000, 280_000, 300_000,
            310_000, 330_000, 340_000, 360_000, 380_000, 390_000, 410_000, 430_000, 450_000, 470_000, 500_000,
            520_000, 540_000, 560_000, 590_000, 610_000, 640_000, 670_000, 690_000, 720_000, 800_000, 900_000,
            1_000_000, 1_100_000, 1_200_000, 1_300_000, 1_400_000, 990_000, 1_030_000, 1_070_000, 1_120_000,
            *range(1_900_000, 4_000_000, 100_000), *range(4_900_000, 19_000_000, 1_000_000),
            *range(20_900_000, 29_000_000, 2_000_000))
assert len(LEVEL_EP) == 99

# Goi rieng cua bang thuong (id 98000001.. nhu ban Indo), hinh dung lai hinh goi co san
LEVEL_PACKS = {
    98000001: ("Top 100", {"top": 100, "icon": 82214007}), 98000002: ("Best 50", {"top": 50, "icon": 45007}),
    98000003: ("Best 20", {"top": 20, "icon": 45012}), 98000004: ("Best 10", {"top": 10, "icon": 45011}),
    98000005: ("Best 7", {"top": 7, "icon": 8334}), 98000006: ("DF 70+", {"pos": "DF", "ovr": 70, "icon": 8223510}),
    98000007: ("MF 70+", {"pos": "MF", "ovr": 70, "icon": 8223509}),
    98000008: ("FW 70+", {"pos": "FW", "ovr": 70, "icon": 8223508}),
    98000009: ("Best 30 +5", {"top": 30, "grade": 5, "icon": 45097}),
    98000010: ("Best 20 +5", {"top": 20, "grade": 5, "icon": 45012}),
    98000011: ("Best 7 +5", {"top": 7, "grade": 5, "icon": 8334}),
    98000012: ("'09 Best 10", {"top": 10, "season": 9, "icon": 45006}),
    98000013: ("'07 Best 10", {"top": 10, "season": 7, "icon": 82214356}),
    98000014: ("'06 Best 10", {"top": 10, "season": 6, "icon": 45004}),
}
_TOP300, _TOP100, _BEST50, _BEST30, _BEST20, _BEST10, _BEST7 = 82214007, 98000001, 98000002, 8223562, 98000003, 98000004, 98000005
_DF70, _MF70, _FW70, _B30P5, _B20P5, _B7P5 = 98000006, 98000007, 98000008, 98000009, 98000010, 98000011
_SIM, _TEAM, _ALL = 2001, 3001, 92000088  # ve gia lap, doi doi, '17 Top 200 (ban Indo: goi "tat ca cau thu")
_EXP1K, _EXP5K, _EXP2K, _EXP30K = 910001, 910002, 910004, 910003  # the EXP cau thu cua server nay
_PL, _BL, _LL, _SA, _GK65 = 28001, 28002, 28003, 28004, 8223603  # goi giai 65+, GK 65+
_0611 = 45004  # 06-11 Player Top 10
LEVEL_ITEMS = {  # cap: ((itemid, so luong), ...); cap khong co trong bang chi nhan EP
    2: ((_TOP300, 2), (_ALL, 5), (_SIM, 3)), 3: ((_TOP100, 2), (_ALL, 5)), 4: ((_TOP300, 2), (_SIM, 3)),
    5: ((_TOP300, 2), (_TEAM, 1)), 6: ((_TOP300, 2), (_SIM, 3)), 7: ((_TOP300, 2), (_EXP1K, 2)), 8: ((_TOP300, 2),),
    9: ((_TOP300, 2),), 10: ((_TOP100, 1), (_SIM, 20), (_TEAM, 1)), 11: ((_TOP100, 1), (_EXP5K, 2)),
    12: ((_TOP100, 1), (_SA, 1), (_EXP2K, 2)), 13: ((_TOP100, 1), (_BL, 1)), 14: ((_TOP100, 1), (_LL, 1)),
    15: ((_TOP100, 1), (_PL, 1)), 16: ((_TOP100, 1), (_GK65, 1)), 17: ((_TOP100, 1), (_GK65, 1)),
    18: ((_TOP100, 2), (_GK65, 2)), 41: ((_BEST30, 5), (_DF70, 3)), 43: ((_BEST30, 6), (_DF70, 3)),
    44: ((_BEST30, 6), (_MF70, 3)), 45: ((_BEST10, 1), (_MF70, 3)), 46: ((_BEST10, 1), (_MF70, 3)),
    47: ((_BEST10, 1), (_FW70, 3)), 48: ((_BEST10, 1), (_FW70, 3)), 49: ((_BEST10, 1), (_FW70, 3)),
    51: ((_BEST10, 1),), 52: ((_BEST10, 2),), 53: ((_BEST10, 2),), 54: ((_BEST10, 2),), 55: ((_BEST10, 2),),
    60: ((_BEST10, 2), (98000012, 3), (_BEST50, 5)), 61: ((_BEST10, 2), (98000013, 3), (_BEST50, 6)),
    62: ((_BEST10, 2), (_BEST50, 8)), 63: ((_BEST7, 2), (_0611, 2)), 64: ((_BEST7, 2), (_0611, 2)),
    65: ((_BEST7, 2), (_0611, 4)), 66: ((_BEST7, 2), (_0611, 4), (_SIM, 20)), 67: ((_BEST7, 2), (_0611, 5)),
    68: ((_BEST7, 2), (98000014, 1), (_0611, 5)), 69: ((_BEST7, 2), (_BEST50, 4)), 70: ((_BEST7, 2), (_BEST50, 4)),
    71: ((_BEST7, 2), (_BEST50, 4)), 72: ((_BEST7, 2), (_BEST50, 5)), 73: ((_BEST7, 2), (_BEST30, 5)),
    74: ((_BEST7, 2), (_BEST30, 5)), 75: ((_BEST7, 2), (_BEST30, 5)), 76: ((_BEST7, 2), (_BEST30, 6)),
    77: ((_BEST7, 2), (_BEST30, 6), (_SIM, 20)), 78: ((_BEST7, 2), (_BEST30, 7)), 79: ((_B30P5, 2), (_BEST50, 1)),
    80: ((_B30P5, 2), (_BEST50, 2)), **{lv: ((_B20P5, 2),) for lv in range(81, 100)},
    90: ((_B20P5, 2), (_EXP30K, 1)), 93: ((_B20P5, 2), (_EXP30K, 1)), 100: ((_B7P5, 1), (_EXP30K, 1)),
}


def level_reward_items(level):
    """[{itemid, count}] khi HLV dat cap `level`: EP roi vat pham (bo id server khong co)."""
    ep = LEVEL_EP[level - 2] if 2 <= level <= len(LEVEL_EP) + 1 else 0
    return ([{"itemid": hc.EP_ITEM, "count": ep}] if ep else []) + \
           [{"itemid": i, "count": n} for i, n in LEVEL_ITEMS.get(level, ()) if i in hc.PACKS or i in hc.ITEMS]


def grant(coach, prev_level, cur_level):
    """Thuong cac cap prev+1..cur vao Tui do cua HLV + danh sach cho popup len cap."""
    for level in range(prev_level + 1, cur_level + 1):
        items = level_reward_items(level)
        if not items:
            continue
        coach.setdefault("bag", []).append({
            "_id": hc.new_iid(), "title": f"Thưởng lên cấp HLV Lv.{level}",
            "msg": f"HLV đã lên cấp {level}.", "items": items, "players": [], "createdat": int(time.time())})
        coach.setdefault("levelreward", []).extend(items)


def ep_level_reward(query, body):
    """GET /coach/me/level/reward: vat pham cua cac lan len cap vua roi (cong theo id), 1 lan."""
    st = hc._state()
    got = hc.my_coach(st).pop("levelreward", [])
    if got:
        hc._save(st)
    total = {}
    for it in got:
        total[it["itemid"]] = total.get(it["itemid"], 0) + it["count"]
    return hc._json([{"itemid": i, "count": n, "staticdata": hc.item_def(i)} for i, n in total.items()])


def ep_level_reward_table(query, body):
    """GET /static/db/REWARDS/coachlevelup: thuong moi cap (popup cac cap sau)."""
    return hc._json({str(lv): {"coachlevel": lv, "items": [{**it, "staticdata": hc.item_def(it["itemid"])}
                                                           for it in level_reward_items(lv)]}
                     for lv in range(2, hc.COACH_MAX_LEVEL + 1)})


def install(module):
    global hc
    hc = module
    for pid, pack in LEVEL_PACKS.items():
        hc.PACKS.setdefault(pid, pack)
    base_gain = hc.gain_exp

    def gain_exp(owner, n, need, top):
        prev, cur = base_gain(owner, n, need, top)
        if need is hc.coach_need and cur["level"] > prev["level"]:
            try:
                grant(owner, prev["level"], cur["level"])
            except Exception as e:  # khong lam hong ket qua tran vi thuong
                print(f"level reward: {e!r}", flush=True)
        return prev, cur
    hc.gain_exp = gain_exp
    hc.ENDPOINTS.update({"/coach/me/level/reward": ep_level_reward,
                         "/static/db/REWARDS/coachlevelup": ep_level_reward_table})
    hc.DYNAMIC_PATHS.update({"/coach/me/level/reward", "/static/db/REWARDS/coachlevelup"})
