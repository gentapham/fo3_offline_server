"""Su kien trong game: thong bao qua dang nhap hang ngay + thuong thoi gian choi (port fo3-server-indo a0b49dd).

* Qua dang nhap: server da tang qua vao Tui do o lan chon HLV dau tien moi ngay (http_config.daily_login,
  GM Tool > Qua hang ngay). InGameEventController con gui PUT /events/rewards/daily (0-2 phut sau khi vao
  sanh va khi doi ngay UTC): tra qua neu chua tang + day 1 thong bao su kien (EVENT_GET_MESSAGE) ma muc
  trong danh sach mo EventRewardPopup (details: type daily, opts.dailytype, rewardindex, messages.popup,
  items). Moi ngay 1 lan (coach daily.noted).
* Thuong thoi gian choi: su kien "dt" trong GET /events (InGameDTEventViewer cua sanh): condition.dt
  {time {beginningat (ms, so), prevsession (s)}, timeline [s moi moc], endline, rewarded}, limit.reward,
  reward[moc][0].items. Client tu dem prevsession + dong ho cua no tu beginningat, qua moi moc thi gui
  PUT /events/rewards/dt. Thoi gian = thoi gian choi server dem (manager.note_activity, khoang cach giua 2
  yeu cau <= 20 phut) tu 00:00 gio VN. Moc mac dinh 10/30/60 phut, sua o GM Tool > Qua hang ngay
  (gamedata/playtime_rewards.json).
Chua co HLV (cai moi / vua reset): /events tra danh sach rong, cac PUT tra result false - khong loi.
"""
import json
import os
import sys
import time

hc = None
PLAYTIME_FILE = None  # gamedata/playtime_rewards.json (dat trong install)
PLAYTIME_DEFAULT = [{"minutes": 10, "ep": 1_000_000}, {"minutes": 30, "ep": 3_000_000},
                    {"minutes": 60, "pack": 28007, "count": 1}]
DT_EVENTID = 1002
EVENT_TIMESET = {"startdate": "20140101", "enddate": "20351231", "starttime": "0000", "endtime": "2359"}
EVENT_TARGET = {"targetuser_igr": 255, "targetuser_vip": 255}
SLACK = 120  # s: client tu dem bang dong ho cua no


def _real_coach(st):
    """HLV dang chon, hoac None khi tai khoan chua co HLV (coach_of tra HLV tam khong luu)."""
    c = hc.my_coach(st)
    return None if c.get("temp") or not st.get("coaches") else c


def playtime_table():
    try:
        with open(PLAYTIME_FILE, encoding="utf-8") as f:
            t = json.load(f)
        if isinstance(t, list) and t and all(isinstance(r, dict) and int(r.get("minutes", 0)) > 0 for r in t):
            return t
    except (OSError, ValueError, TypeError):
        pass
    return [dict(r) for r in PLAYTIME_DEFAULT]


def save_playtime(table):
    os.makedirs(os.path.dirname(PLAYTIME_FILE), exist_ok=True)
    tmp = PLAYTIME_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(table, f, indent=1, ensure_ascii=False)
    os.replace(tmp, PLAYTIME_FILE)


def _items(reward):
    return [{**it, "staticdata": hc.item_def(it["itemid"])} for it in hc.reward_items(reward)]


def played_today(coach, now=None):
    """(ban ghi "dt" cua HLV, so giay da choi tu 00:00 gio VN): goc = thoi gian choi luc doi ngay."""
    pending = hc.manager.PLAYTIME["pending"] if getattr(hc, "manager", None) else 0
    total = coach.get("playtime", 0) + pending
    day = hc.today_wib(now)
    if (coach.get("dt") or {}).get("day") != day:
        coach["dt"] = {"day": day, "base": total, "rewarded": 0}
    return coach["dt"], max(0, int(total - coach["dt"]["base"]))


def dt_event(coach, now=None):
    now = time.time() if now is None else now
    d, secs = played_today(coach, now)
    table = playtime_table()
    timeline = [int(r["minutes"]) * 60 for r in table]
    return {"eventid": DT_EVENTID, "active": True, "reason": [],
            "condition": {"type": "dt", "timeset": EVENT_TIMESET, "targetuser": EVENT_TARGET,
                          # beginningat: so ms (chuoi W3C -> ngay khong hop le, client doc thanh nua dem)
                          "dt": {"time": {"beginningat": int(now * 1000), "prevsession": secs}, "timeline": timeline,
                                 "endline": timeline[-1], "rewarded": d["rewarded"]}},
            # reward[moc] = [{items: [...]}] (InGameDTEventViewer doc reward[moc][0].items[0]; mang vat pham
            # tran -> TypeError #1010 khi vao sanh, theo ban Indo)
            "limit": {"reward": 1}, "reward": [[{"items": _items(r)}] for r in table],
            "messages": {"main": {"name": "Thưởng thời gian chơi", "description": "Chơi đủ " + ", ".join(
                str(r["minutes"]) for r in table) + " phút trong hôm nay (tính từ 00:00 giờ Việt Nam): mỗi mốc tặng quà vào Túi đồ."}}}


def events_body(st, now=None):
    coach = _real_coach(st)
    return {"events": [dt_event(coach, now)] if coach else []}


def ep_events(query, body):
    st = hc._state()
    out = events_body(st)
    if _real_coach(st):
        hc._save(st)
    return hc._json(out)


def daily_note(st, now=None):
    """Thong bao qua dang nhap hom nay (1 lan / ngay) hoac None."""
    coach = _real_coach(st)
    d, today = (coach or {}).get("daily") or {}, hc.today_wib(now)
    if not coach or d.get("last") != today or d.get("noted") == today:
        return None
    d["noted"] = today
    table, n = hc.daily_table(), max(1, int(d.get("streak") or 1))
    items = _items(table[(n - 1) % len(table)])
    return {**hc.note("event", "reward", {
        "type": "daily", "opts": {"dailytype": "continuous"}, "rewardindex": n - 1,
        # anh cua so: externalAssets/events/popup/daily_login.png (tools/event_banners.py; thieu -> "NOT FOUND EVENT")
        "assets": {"popup": "daily_login"}, "items": items,
        "title": "Quà đăng nhập hằng ngày", "messages": {"popup": {"name": "Quà đăng nhập hằng ngày", "description":
            f"Đăng nhập {n} ngày liên tiếp. Quà đã vào Túi đồ."}}}, now), "stackable": True}


def ep_event_daily(query, body):
    """PUT /events/rewards/daily: qua hom nay neu chua tang + thong bao cua no."""
    st = hc._state()
    if not _real_coach(st):
        return hc._json({"result": False})
    hc.daily_login(st)
    n = daily_note(st)
    hc._save(st)
    if n:
        hc.messenger_push("/inbox/my/notifications/latest", n)
    return hc._json({"result": True})


def ep_event_dt(query, body):
    """PUT /events/rewards/dt: thuong moc ke tiep khi da choi du (du 2 phut), roi su kien hien tai."""
    st, now = hc._state(), time.time()
    coach = _real_coach(st)
    if not coach:
        return hc._json({"result": False, "resources": [{"uri": "/events", "body": events_body(st, now)}]})
    d, secs = played_today(coach, now)
    table = playtime_table()
    i = d["rewarded"]
    ok = i < len(table) and secs + SLACK >= int(table[i]["minutes"]) * 60
    n = None
    if ok:
        d["rewarded"] = i + 1
        m = table[i]["minutes"]
        items = hc.reward_items(table[i])
        hc.bag_add(st, f"Quà chơi {m} phút", items, msg=f"Bạn đã chơi {m} phút trong hôm nay.")
        n = {**hc.note("event", "reward", {
            "type": "dt", "opts": {}, "rewardindex": i, "assets": {"popup": "play_time"}, "title": "Thưởng thời gian chơi",
            "items": [{**it, "staticdata": hc.item_def(it["itemid"])} for it in items],
            "messages": {"popup": {"name": "Thưởng thời gian chơi", "description":
                f"Đã chơi {m} phút trong hôm nay. Quà đã vào Túi đồ."}}}, now), "stackable": True}
    out = events_body(st, now)
    hc._save(st)
    if n:
        hc.messenger_push("/inbox/my/notifications/latest", n)
    return hc._json({"result": ok, "resources": [{"uri": "/events", "body": out}]})


def install(module):
    global hc, PLAYTIME_FILE
    hc = module
    hc.events = sys.modules[__name__]
    PLAYTIME_FILE = os.path.join(hc.HERE, "gamedata", "playtime_rewards.json")
    eps = {"/events": ep_events, "/events/rewards/daily": ep_event_daily, "/events/rewards/dt": ep_event_dt}
    hc.ENDPOINTS.update(eps)
    hc.DYNAMIC_PATHS.update(eps)
