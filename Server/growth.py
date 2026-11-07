"""Dao tao cau thu (Quan ly cau thu > Dao tao, TeamManagementPlayerGrowthExpansionTabPage;
features.teammanagement.playermanagement.expansion = {}, them vao GET /account o day).

Client (Module PlayerListUtil / CoachProxy):
- Bang responses/static_db_PLAYER_EXTRA_GROWTH.json: config.point.{coach_limit, player_limit,
  conversion, match_limit, igr, vip}, statgroups {chi so: nhom}, growth {nhom: [{min, max,
  require}]}. Gia 1 diem chi so = require cua dong co min <= v <= max, voi
  v = chi so goc + da dao tao + (cap toi da - 1) + boost the +1 (PLAYERGRADE) + so diem dang cong
  (convertStatValueForPlayerGrowth). Khong dong nao khop -> mien phi, nen bang phai phu het.
- Danh sach chi gom cau thu LV = STATICVALUES.max_playerlevel (30) hoac da dao tao (sp.used);
  bo the thue (contracttype rental) / dang tap huan.
- Diem HLV: coach/me sp {total, used} (con lai = total - used); moi cau thu toi da player_limit.
- Cau thu: player.sp {used, stats: {chi so: +n}}. checkGrowthBoostPlayer cong sp.stats vao
  staticdata.stats va statusAttribute (chi so tran dau native doc statusAttribute) -> chi so
  dao tao co tac dung trong tran, khong can cong them o server.
- "Dao tao": PUT /player/<id>/extra/growth {stats: {chi so: +n}} -> {result, usedsp:{used, detail}}
  (detail -> hieu ung chi so vua tang) + resources /coach/me.
- "Tao lai": can vat pham type PLAYER_GROWTH_RESET trong kho (khong co -> popup mua trong Cua hang,
  tab Dung duoc, mien phi): POST /item/<_id>/use {id, targetid} -> xoa sp cua cau thu, tra diem.
Ban offline: quy diem HLV luon day (total = coach_limit), khong tich luy qua tran dau.
Man thuong sau tran (setCoachSP) can match.reward.sp {descriptions, prev, current: {total, exp}}.
"""
import json
import os
import re
from contextlib import closing

import item_ext

hc = None
RESET_ITEM = 914001
RESET_TYPE = "PLAYER_GROWTH_RESET"
SLACK = 2  # diem lech toi da chap nhan giua gia server va gia client
STATS = ("strength", "stamina", "acceleration", "sprintspeed", "jumping", "agility", "balance",
         "slidingtackle", "dribbling", "ballcontrol", "marking", "standingtackle", "crossing",
         "shortpassing", "finishing", "longpassing", "shotpower", "headingaccuracy", "longshots",
         "volleys", "curve", "freekickaccuracy", "penalties", "interceptions", "positioning", "vision",
         "reactions", "aggression", "gkkicking", "gkdiving", "gkhandling", "gkpositioning", "gkreflexes")
ITEMS = {
    RESET_ITEM: ("Phiếu tạo lại đào tạo", 300, RESET_TYPE, {},
                 "Xóa toàn bộ chỉ số đã đào tạo của một cầu thủ và hoàn lại điểm đào tạo. "
                 "Dùng bằng nút Tạo lại trong Quản lý cầu thủ > Đào tạo."),
}


def table():
    with open(os.path.join(hc.RESPONSES, "static_db_PLAYER_EXTRA_GROWTH.json"), encoding="utf-8") as f:
        return json.load(f)


def limits():
    point = table()["config"]["point"]
    return int(point["coach_limit"]), int(point["player_limit"])


def used_of(p):
    return int((p.get("sp") or {}).get("used") or 0)


def coach_sp(team):
    """coach/me sp: quy diem HLV (luon day ban offline), da dung = tong sp.used cua cau thu."""
    total = limits()[0]
    used = sum(used_of(p) for p in team.get("players", []))
    return {"total": total, "used": used, "point": max(0, total - used), "exp": 0}


def grade_boost(grade):
    with open(os.path.join(hc.RESPONSES, "static_db_PLAYERGRADE.json"), encoding="utf-8") as f:
        return int(json.load(f)[str(grade)]["boost"]["PLAYER_ALLATTRIBUTES_ALL"])


def cost(spid, stats, grown):
    """Diem can de cong stats {chi so: n} cho the spid da dao tao grown {chi so: n} (giong getUsePoint)."""
    tab = table()
    with closing(hc._db()) as c:
        row = c.execute(f"SELECT {', '.join(STATS)} FROM players WHERE spid = ?", (int(spid),)).fetchone()
    if row is None:
        return None
    base = dict(zip(STATS, row))
    offset = hc.PLAYER_MAX_LEVEL - 1 + grade_boost(1)  # PlayerListUtil.GROWTH_TARGET_GRADE = 1
    total = 0
    for key, n in stats.items():
        rows = tab["growth"][str(tab["statgroups"][key])]
        v0 = int(base[key] or 0) + int(grown.get(key, 0)) + offset
        for i in range(n):
            total += next((r["require"] for r in rows if r["min"] <= v0 + i <= r["max"]), 0)
    return total


def ep_growth(pid, query, body):
    """PUT /player/<_id>/extra/growth {stats: {chi so: +n}}."""
    st = hc._state(); slot = hc._default_slot(st); team = st["teams"][str(slot)]
    p = next((x for x in team["players"] if hc.iid(x) == pid), None)
    try:
        stats = (json.loads(body or b"{}").get("stats") or {})
    except ValueError:
        stats = {}
    stats = {k: v for k, v in stats.items() if k in STATS and type(v) is int and v > 0} if isinstance(stats, dict) else {}
    if p is None or not stats or hc.player_levelinfo(p)["level"] < hc.PLAYER_MAX_LEVEL:
        return hc._json({"result": False, "reason": "ITEM_USE_ERROR"})
    sp = p.get("sp") or {}
    grown = dict(sp.get("stats") or {})
    need = cost(p["spid"], stats, grown)
    coach_limit, player_limit = limits()
    free = min(player_limit - used_of(p), coach_limit - coach_sp(team)["used"])
    # Client da kiem tra gioi han voi cung bang gia; lech nho (chi so goc khac du lieu client mot
    # bac gia) thi van nhan va chan o gioi han thay vi bao loi.
    if need is None or free <= 0 or need > free + SLACK:
        return hc._json({"result": False, "reason": "ITEM_USE_ERROR"})
    for k, n in stats.items():
        grown[k] = int(grown.get(k, 0)) + n
    used = used_of(p) + min(need, free)
    p["sp"] = {"used": used, "stats": grown}
    hc._save(st)
    return hc._json({"result": True, "usedsp": {"used": used, "detail": stats},
                     "resources": hc.coach_me_resources(st, slot)})


def install(module):
    global hc
    hc = module
    item_ext.install(hc)
    item_ext.add_items(ITEMS)
    hc.ROUTES[:0] = [(re.compile(r"/player/(\w+)/extra/growth/?"), ep_growth)]
    base_coach_me, base_use, base_reward = hc.coach_me, hc.use_item, hc.match_reward

    def coach_me(st, slot):
        me = base_coach_me(st, slot)
        me["sp"] = coach_sp(st["teams"].get(str(slot)) or {})
        return me

    def use_item(st, slot, coach, it, req):
        if hc.ITEMS[it["itemid"]][2] != RESET_TYPE:
            return base_use(st, slot, coach, it, req)
        targetid = req.get("targetid")
        p = next((x for x in st["teams"][str(slot)]["players"]
                  if isinstance(targetid, str) and hc.iid(x) == targetid), None)
        if p is None or not used_of(p):
            return {"result": False, "reason": "Cầu thủ này chưa được đào tạo."}
        p.pop("sp", None)
        it["remains"] -= 1
        if not it["remains"]:
            coach["items"].remove(it)
        return {"result": True, "targetid": targetid}

    def match_reward(st, stats):
        # PostMatchRewardDefaultScene.setCoachSP (chi chay khi co "expansion"): co reward.sp thi doc
        # sp.descriptions/prev/current + config.point.conversion; thieu thi tra MATCHTYPES[match.type]
        # (bang chi co League_2_*) -> null -> TypeError #1009, man thuong ket. Ban offline khong cong
        # diem dao tao qua tran: prev = current = quy day.
        out = base_reward(st, stats)
        total = coach_sp(st["teams"].get(str(hc._default_slot(st))) or {})["total"]
        out["sp"] = {"descriptions": {}, "prev": {"total": total, "exp": 0}, "current": {"total": total, "exp": 0}}
        return out

    def ep_account(query, body):
        # Bat tab Dao tao o day (khong o responses/account_base.json): co hieu luc cung luc voi
        # reward.sp / PUT .../extra/growth, nen server cu chua khoi dong lai khong lam ket man
        # thuong sau tran.
        ctype, data = base_account(query, body)
        acc = json.loads(data)
        pm = acc.setdefault("features", {}).setdefault("teammanagement", {}).setdefault("playermanagement", {})
        pm.setdefault("expansion", {})
        return hc._json(acc)

    base_account = hc.ENDPOINTS["/account"]
    hc.ENDPOINTS["/account"] = ep_account
    hc.coach_me, hc.use_item, hc.match_reward = coach_me, use_item, match_reward
