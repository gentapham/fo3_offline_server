"""GM Tool > Goi the & Tui do > Hop dong cau thu: danh sach the ghep manh + so manh, phi, cap the (craft.py, gamedata/craft.json)."""
import json
import os

import admin
import craft
from admin import api, AdminError

admin.READ_ONLY.add("craft_info")


def _row(idx, spid):
    r = idx.get(spid) or {"spid": spid, "name": str(spid), "season": spid // 1000000, "ovr": 0}
    return {"spid": spid, "name": r.get("name"), "season": r.get("season"), "ovr": r.get("ovr"), "pos": r.get("pos"),
            "piece": craft.PIECE_BASE + craft.PIECE_IDS[spid] if spid in craft.PIECE_IDS else None}


def _info():
    idx = {r["spid"]: r for r in admin.card_index()}
    st = craft.settings()
    cards = [r["spid"] for r in sorted(craft.RECIPES.values(), key=lambda r: r["piece"])]
    if st["cards"] is not None:  # giu dung thu tu GM da xep
        order = [int(x) for x in st["cards"] if str(x).isdigit()]
        cards = [s for s in order if s in cards]
    return {"cards": [_row(idx, s) for s in cards], "pieces": st["pieces"], "fee": st["fee"], "grade": st["grade"],
            "grade_now": craft.result_grade(), "super_max": st["super_max"], "custom": os.path.isfile(craft.CONF),
            "free_slots": 99 - len(craft.PIECE_IDS)}


@api("craft_info")
def craft_info(p):
    return _info()


@api("craft_save")
def craft_save(p):
    try:
        cards = [int(x) for x in p.get("cards") or []]
    except (TypeError, ValueError):
        raise AdminError("Danh sách thẻ không hợp lệ.") from None
    cards = list(dict.fromkeys(cards))
    if not cards:
        raise AdminError("Danh sách cần ít nhất 1 thẻ.")
    idx = {r["spid"]: r for r in admin.card_index()}
    for s in cards:
        if s < 1000000 or s not in idx:
            raise AdminError(f"Thẻ {s} không có trong game (không dùng thẻ mùa 0).")
        if craft.assign_id(s) is None:
            raise AdminError("Hết chỗ cho mảnh ghép mới (tối đa 99 cầu thủ từng được ghép).")
    cfg = {"cards": cards, "pieces": admin.as_int(p.get("pieces", 10), 1, 999, "số mảnh"),
           "fee": admin.as_int(p.get("fee", 0), 0, 10 ** 12, "phí EP"),
           "grade": admin.as_int(p.get("grade", 5), 1, 20, "cấp thẻ"),
           "super_max": admin.as_int(p.get("super_max", 5), 0, 999, "siêu nguyên liệu tối đa"),
           "ids": {str(k): v for k, v in craft.PIECE_IDS.items()}}
    os.makedirs(os.path.dirname(craft.CONF), exist_ok=True)
    with open(craft.CONF + ".tmp", "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)
    os.replace(craft.CONF + ".tmp", craft.CONF)
    craft.reload()
    admin.hc.shop_changed()
    return _info()


@api("craft_reset")
def craft_reset(p):
    """Ve mac dinh (giu stt manh cua the GM tung them de manh trong kho van dung cau thu)."""
    ids = {str(k): v for k, v in craft.PIECE_IDS.items()}
    with open(craft.CONF + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"ids": ids}, f, indent=1)
    os.replace(craft.CONF + ".tmp", craft.CONF)
    craft.reload()
    admin.hc.shop_changed()
    return _info()
