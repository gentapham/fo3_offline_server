"""Thi truong song (port fo3-indo-server issue #30): the rao ban duoc HLV ao mua sau mot luc (thong bao
"da ban"), Huy / Dang lai, bieu do gia trong cua so the, nen kinh te dong tuy chon (gia troi theo ngay,
HLV ao ban re, dat gia toi 110%) voi mau De / Thuc te / Kho. Che do "ban ngay" (nhu truoc) van giu.

Client (TransferMarketProxy, TransferMyOfferListTabPage, PlayerDetailPopup):
- offers[] {_id, status waiting|sold|expired (SORTKEY), issuedat, completefee.base, spid, grade, price};
  loi offer: NO_SUCH_OFFER / OFFER_NOT_EXIST_ERROR / NO_SUCH_TRANSFERRED_OFFER_ERROR (client tai lai).
- Huy = DELETE /transfer/my/offer/<waiting|expired>/<id>; Dang lai = POST /transfer/offer/from/<id> {price}.
- Bieu do = GET /transfer/<spid>/<grade>/detail {history {basePrice, startat, endat, data [[ms, gia]]}}
  (features.transfermarket.playerpricegraph).
- Ban xong: push /transfer/my/offer/list + thong bao /inbox/my/notifications/latest (category
  transfermarket, toast TRANSFERMARKET_LIVE_ALARM in gia nhu chuoi).
Gia nen = hc.card_price (Cai dat gia EP trong GM Tool: he so, gia rieng, +0 %). Cai dat thi truong:
gamedata/market_settings.json (GM Tool > Tran dau > Thi truong).
"""
import copy
import json
import math
import os
import random
import re
import sys
import threading
import time

hc = None
SETTINGS_FILE = None

# mau kinh te: dynamic = gia troi theo ngay (+-swing), HLV ao rao ban tu bargain[0] den bargain[1] gia
# ngay, nguoi ban dat toi ask_max; sale = (giay toi thieu, toi da) de ban o 80%..100% gia; slow = dat cao
# hon gia moi 10% thi cho lau gap slow lan
PRESETS = {
    "easy": {"dynamic": False, "swing": 0.0, "ask_max": 1.0, "sale_min": 10, "sale_max": 120, "slow": 48,
             "bargain_lo": 0.96, "bargain_hi": 1.0},
    "realistic": {"dynamic": True, "swing": 0.12, "ask_max": 1.1, "sale_min": 20, "sale_max": 300, "slow": 48,
                  "bargain_lo": 0.86, "bargain_hi": 1.02},
    "hard": {"dynamic": True, "swing": 0.2, "ask_max": 1.1, "sale_min": 60, "sale_max": 900, "slow": 96,
             "bargain_lo": 0.92, "bargain_hi": 1.05},
}
# mac dinh: thi truong song + Thuc te (ban sau 20 giay - 5 phut, gia troi +-12%/ngay)
DEFAULT = {"mode": "living", "preset": "realistic", **PRESETS["realistic"]}
LIMITS = {"swing": (0.0, 0.3), "ask_max": (1.0, 1.5), "sale_min": (1, 3600), "sale_max": (1, 86400),
          "slow": (1, 1000), "bargain_lo": (0.5, 1.0), "bargain_hi": (0.8, 1.2)}

MARKET_STOCK = (3, 30)  # so luong ban san moi hang/gio (khong bao gio het)
VM_LIST_ROWS = (0, 0, 1, 1, 2, 3)  # so dong HLV ao rao 1 the 1 gio (boc 1)
VM_LIST_GRADES = (1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 7, 8, 9, 10)
MARKET_BOUGHT = {}  # (spid, grade, price, khoa gio) -> so the da mua tu dong do trong gio
OFFERS_MAX = 100  # the dang rao + da ban chua nhan (client: TRANSFERMARKET_MAX_REGISTERED); push < 64 KB
OFFER_PRIVATE = ("card", "buyat", "buyer", "slot")  # khong gui client
PRICE_DAYS = 31  # bieu do: endat va moi 5 ngay ve truoc toi 30 ngay
_SET_CACHE = {"mtime": None, "data": dict(DEFAULT)}
_LOCK = threading.RLock()


# --------------------------------------------------------------------------- settings
def clean(d):
    d = d if isinstance(d, dict) else {}
    preset = d.get("preset") if d.get("preset") in (*PRESETS, "custom") else DEFAULT["preset"]
    out = {"mode": d.get("mode") if d.get("mode") in ("living", "instant") else DEFAULT["mode"], "preset": preset}
    base = PRESETS.get(preset, PRESETS[DEFAULT["preset"]])
    if preset != "custom":
        out.update(base)
        return out
    out["dynamic"] = d["dynamic"] if isinstance(d.get("dynamic"), bool) else base["dynamic"]
    for k, (lo, hi) in LIMITS.items():
        v = d.get(k)
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi and math.isfinite(v)
        out[k] = (int(v) if k in ("sale_min", "sale_max") else float(v)) if ok else base[k]
    out["sale_max"] = max(out["sale_max"], out["sale_min"])
    out["bargain_hi"] = max(out["bargain_hi"], out["bargain_lo"])
    return out


def settings():
    with _LOCK:
        try:
            m = os.stat(SETTINGS_FILE).st_mtime
        except OSError:
            _SET_CACHE.update(mtime=None, data=clean(DEFAULT))
            return _SET_CACHE["data"]
        if m != _SET_CACHE["mtime"]:
            try:
                with open(SETTINGS_FILE, encoding="utf-8") as f:
                    _SET_CACHE.update(mtime=m, data=clean(json.load(f)))
            except (OSError, ValueError):
                _SET_CACHE.update(mtime=m, data=clean(DEFAULT))
        return _SET_CACHE["data"]


def save_settings(d):
    data = clean(d)
    with _LOCK:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SETTINGS_FILE)
        _SET_CACHE["mtime"] = None
    return settings()


def living():
    return settings()["mode"] == "living"


# --------------------------------------------------------------------------- prices
def nice_price(x):
    return hc.sig3(max(0, int(x)))


def vm_day(t=None):
    return hc.rank.vm_day(t)


def market_factor(spid, grade, day):
    """Gia HLV ao rao (thi truong co dinh) ngay `day` / gia tri the: song cham 0.955..0.995."""
    r = random.Random(f"price/{spid}/{grade}")
    p1, p2, a, b = r.uniform(9, 23), r.uniform(3, 7), r.uniform(0, 2 * math.pi), r.uniform(0, 2 * math.pi)
    noise = random.Random(f"price/{spid}/{grade}/{day}").uniform(-0.003, 0.003)
    return 0.975 + 0.012 * math.sin(2 * math.pi * day / p1 + a) + 0.006 * math.sin(2 * math.pi * day / p2 + b) + noise


def market_swing(spid, day, swing):
    """Gia thi truong dong ngay `day` / gia tri the: song cham +-swing (moi hang cua cau thu cung troi)."""
    r = random.Random(f"swing/{spid}")
    p1, p2, a, b = r.uniform(12, 30), r.uniform(4, 9), r.uniform(0, 2 * math.pi), r.uniform(0, 2 * math.pi)
    noise = random.Random(f"swing/{spid}/{day}").uniform(-0.01, 0.01)
    return 1 + swing * (0.7 * math.sin(2 * math.pi * day / p1 + a) + 0.3 * math.sin(2 * math.pi * day / p2 + b)) + noise


def market_value(spid, grade, t=None):
    """Gia thi truong cua the luc nay (hoac t): card_price, troi theo market_swing khi bat kinh te dong."""
    p = hc.card_price(spid, grade)
    cfg = settings()
    if not cfg["dynamic"] or not p or not cfg["swing"]:
        return p
    f = market_swing(int(spid), vm_day(time.time() if t is None else t), cfg["swing"])
    if int(grade) < 1:
        return max(1, nice_price(p * f))
    # cung he so cho moi cap nhung lam tron 3 chu so co the lam 2 cap lien nhau trung gia -> giu tang
    # dan nghiem ngat theo cap (+1 < +2 < ... < +20) nhu card_price
    v = 0
    for g in range(1, min(hc.MAX_GRADE, int(grade)) + 1):
        v = max(1, nice_price(hc.card_price(spid, g) * f), hc.next_price(v) if v else 1)
    return v


def sell_band(spid, grade):
    """(thap nhat, cao nhat, gia thi truong) ma 1 the rao ban duoc dat luc nay."""
    v = market_value(spid, grade)
    cfg = settings()
    ask = cfg["ask_max"]
    if hc.shop_paid():  # Shop tinh phi: 110% cho the +1..+3 (HLV ao ban mai) ban lai co lai chac -> toi da 105%
        ask = min(ask, 1.05)
    return v * 8 // 10, (nice_price(v * ask) if cfg["dynamic"] and ask > 1 else v), v


def hour_key(now):
    return f"{vm_day(now)}/{time.gmtime(now).tm_hour}"


def market_rows(spid, t=None):
    """Cac dong cua 1 the: hang +1..+MAX_GRADE ban san o gia thi truong (khong het), + vai dong HLV ao rao
    trong gio (thi truong dong: tu gia re toi hon gia ngay mot chut; co dinh: >= 96% gia)."""
    spid = int(spid)
    if not hc.market_listed(spid):
        return []
    now = time.time() if t is None else t
    key, day = hour_key(now), vm_day(now)
    cfg = settings()
    r = random.Random(f"market/{spid}/{key}")
    rows = {}
    mv = {g: market_value(spid, g, now) for g in range(1, hc.MAX_GRADE + 1)}
    for g in range(1, hc.MAX_GRADE + 1):
        rows[(g, mv[g])] = r.randint(*MARKET_STOCK)
    for _ in range(r.choice(VM_LIST_ROWS)):
        g = r.choice(VM_LIST_GRADES)
        p = hc.card_price(spid, g)
        if not p:
            continue
        if cfg["dynamic"]:
            price = nice_price(mv[g] * r.uniform(cfg["bargain_lo"], cfg["bargain_hi"]))
        else:
            price = min(p, max(nice_price(0.96 * p), nice_price(p * market_factor(spid, g, day) * r.uniform(0.99, 1.01))))
        # dong HLV ao rao re / dat khong bao gio vuot sang khoang gia cua cap ben canh (the +g re hon
        # dong +(g-1) nao do): khoang cua +g = giua trung binh nhan voi +(g-1) va +(g+1)
        lo = math.sqrt(mv[g - 1] * mv[g]) if g > 1 else 0
        hi = math.sqrt(mv[g] * mv[g + 1]) if g < hc.MAX_GRADE else math.inf
        if not lo < price < hi:
            price = mv[g]
        if price > 0:
            rows[(g, price)] = rows.get((g, price), 0) + r.choice((1, 1, 1, 2, 3))
    out = []
    for (g, price), n in sorted(rows.items()):
        if g > hc.card_cap():  # GM Tool: cap the toi da (giu nguyen chuoi ngau nhien cua cac dong khac)
            continue
        stock = price == mv[g]
        n -= MARKET_BOUGHT.get((spid, g, price, key), 0)
        if n > 0 or stock:
            out.append({"spid": spid, "expandme": "player", "grade": g, "price": price, "count": max(1, n)})
    return out


def market_buy(spid, grade, price, t=None, budget=None):
    """Gia phai tra khi mua dong (grade, price), None neu dong da het trong gio, False neu budget (EP) khong du. Dong ban san (gia thi
    truong) luon con; danh sach cu (client cache ~30 giay) o gia khac -> tra gia thi truong hien tai neu
    khong dat hon gia client thay."""
    now = time.time() if t is None else t
    key = hour_key(now)
    spid, grade, price = int(spid), int(grade), int(price)
    row = next((r for r in market_rows(spid, now) if r["grade"] == grade and r["price"] == price), None)
    if row is None:
        cur = market_value(spid, grade, now)
        if hc.market_listed(spid) and 1 <= grade <= hc.MAX_GRADE and cur and cur <= price:
            return cur if budget is None or budget >= cur else False  # gia vua troi xuong hoac dong cu cua gio truoc
        return None
    if budget is not None and budget < row["price"]:
        return False  # khong du EP: chua tru vao so luong cua dong
    for k in [k for k in MARKET_BOUGHT if k[3] != key]:
        del MARKET_BOUGHT[k]
    MARKET_BOUGHT[spid, grade, price, key] = MARKET_BOUGHT.get((spid, grade, price, key), 0) + 1
    return row["price"]


# --------------------------------------------------------------------------- offers
def slot_offers(st):
    return st.get("offers", [])


def offer_list(st):
    return {"offerexpireoffset": 86400000, "boost": hc.NO_BOOST,
            "offers": [{k: v for k, v in o.items() if k not in OFFER_PRIVATE} for o in slot_offers(st)]}


def offer_resources(st):
    return [{"uri": "/transfer/my/offer/list", "body": offer_list(st)}] + hc.coach_me_resources(st, hc._default_slot(st))


def vm_buyer(o, now):
    """Ai mua offer o va khi nao (buyat): o 80% gia thi truong ban sau sale_min giay, 100% sau sale_max;
    dat cao hon (kinh te dong) cho lau gap `slow` lan moi 10% vuot."""
    cfg = settings()
    r = random.Random(f"{o['_id']}/{o['price']}")
    share = o["price"] / max(1, market_value(o["spid"], o["grade"], now))
    lo, hi = cfg["sale_min"], cfg["sale_max"]
    if share <= 1:
        wait = lo + (hi - lo) * min(1, max(0, (share - 0.8) / 0.2)) ** 2
    else:
        wait = hi * cfg["slow"] ** min(5, (share - 1) / 0.1)
    o["buyat"] = now + wait * r.uniform(0.6, 1.4)
    crowd = hc.rank.virtual_managers()
    o["buyer"] = r.choice(crowd)["_id"] if crowd else ""


def settle_offers(st, now=None):
    """Offer dang cho co nguoi mua toi han -> da ban: [cac offer do]."""
    now = time.time() if now is None else now
    sold = [o for o in st.get("offers", []) if o.get("status") == "waiting" and o.get("buyat", 0) <= now]
    for o in sold:
        o["status"], o["soldat"] = "sold", hc.w3c(now)
        o.pop("card", None)
        seller = next((c for c in st.get("coaches") or () if c["slot"] == o.get("slot", hc._default_slot(st))), None)
        if seller:  # Info Manager: so the ban, the ban dat nhat
            hc.record_trade(seller, "sell", o["spid"], o.get("price", 0), now)
    return sold


def fmt_price(n):
    return f"{int(n):,}"


def announce_sales(st, sold):
    """Push danh sach moi + 1 thong bao moi the (stackable: giu trong danh sach thong bao)."""
    if not sold:
        return
    hc.messenger_push("/transfer/my/offer/list", offer_list(st))
    for o in sold:
        vm = hc.rank.vm_by_id(o.get("buyer")) or {"name": "HLV ảo"}
        name = card_name(o["spid"])
        hc.messenger_push("/inbox/my/notifications/latest", {**hc.note("transfermarket", "sold", {
            "spid": o["spid"], "expandme": "player", "grade": o["grade"], "price": fmt_price(o["price"]),
            "title": f"{name} +{o['grade']} đã bán cho {vm['name']} ({fmt_price(o['price'])} EP)"}), "stackable": True})


_NAMES = {}


def card_name(spid):
    if spid not in _NAMES:
        try:
            from contextlib import closing
            with closing(hc._db()) as c:
                r = c.execute("SELECT playerid FROM players WHERE spid = ?", (int(spid),)).fetchone()
                _NAMES[spid] = hc.gen_teams.jersey_name(c, r[0]) if r else str(spid)
        except Exception:
            _NAMES[spid] = str(spid)
    return _NAMES[spid] or str(spid)


def my_offers(st):
    """Offer cua HLV dang choi sau khi chot cac the da den gio ban (luu + push)."""
    sold = settle_offers(st)
    if sold:
        hc._save(st)
        announce_sales(st, sold)
    return slot_offers(st)


def next_due(st):
    """Thoi diem offer dang cho som nhat co nguoi mua (inf: khong co)."""
    return min((o.get("buyat", 0) for o in st.get("offers", []) if o.get("status") == "waiting"), default=math.inf)


def market_loop():
    """Luong: moi 5 giay ban cac the da co nguoi mua. Chi doc lai state.json khi file doi hoac da
    toi han 1 offer (state co the lon)."""
    seen = (None, math.inf)  # (mtime state.json, han som nhat)
    while True:
        time.sleep(5)
        try:
            with hc.STATE_LOCK:
                try:
                    mtime = os.stat(hc.STATE).st_mtime_ns
                except OSError:
                    continue
                if mtime == seen[0] and time.time() < seen[1]:
                    continue
                st = hc._state()
                if st.get("coaches"):
                    my_offers(st)
                try:
                    seen = (os.stat(hc.STATE).st_mtime_ns, next_due(st))
                except OSError:
                    seen = (None, math.inf)
        except Exception as e:
            print(f"market loop: {e!r}", flush=True)


# --------------------------------------------------------------------------- endpoints
def ep_transfer_top(position, query, body):
    """GET /transfer/top/50[/position/<GK|DF|MF|FW>]: 50 the dat nhat (dong +1)."""
    from contextlib import closing
    rng = hc.LINES.get(str(position or "").upper())
    where = f"AND p.preferredposition1 BETWEEN {rng.start} AND {rng.stop - 1}" if rng else ""
    with closing(hc._db()) as c:
        both = c.execute(f"SELECT p.spid, {hc.POSOVR}, p.overallrating FROM players p WHERE 1 {where}").fetchall()
    hc._OVR.update((s, (o, d)) for s, o, d in both)
    spids = [s for s, ovr in sorted(((s, o) for s, o, _ in both if hc.market_listed(s)), key=lambda r: (-r[1], r[0]))[:50]]
    out = []
    for s in spids:
        p = market_value(s, 1)
        out.append({"spid": s, "expandme": "player", "grade": 1, "price": p, "count": 1})
    return hc._json(out)


def ep_transfer_search(spid, query, body):
    return hc._json(market_rows(int(spid)))


def ep_transfer_purchase(spid, grade, price, query, body):
    """POST /transfer/purchase/<spid>/<grade>/<price>/purchase: mua tu dong duong dan neu (market_buy)."""
    spid, grade, price = int(spid), int(grade), int(price)
    st = hc._state(); slot = hc._default_slot(st); team = st["teams"][str(slot)]
    if not hc.market_listed(spid) or not 1 <= grade <= hc.MAX_GRADE:
        return hc._json({"result": False, "reason": "NO_SUCH_OFFER"})
    if grade > hc.card_cap():
        return hc._json({"result": False, "reason": f"Cấp thẻ tối đa hiện tại là +{hc.card_cap()}."})
    if len(team["players"]) >= hc.MAX_PLAYERS:
        return hc._json({"result": False, "reason": "CLB đã đầy, hãy thanh lý bớt cầu thủ."})
    cost = market_buy(spid, grade, price, budget=hc.coach_me(st, slot)["money"]["ep"])
    if cost is None:
        return hc._json({"result": False, "reason": "NO_SUCH_OFFER"})
    if cost is False:
        return hc._json({"result": False, "reason": "NOT_ENOUGH_EP"})
    hc.add_ep(st, -cost)
    hc.add_player(team, spid, grade)
    hc.record_trade(hc.my_coach(st), "buy", spid, cost)  # Info Manager: the mua dat nhat
    hc._save(st)
    return hc._json({"result": True, "resources": hc.coach_me_resources(st, slot)})


def ep_transfer_price(spid, grade, query, body):
    """GET /transfer/price/<spid>/<grade>: khoang gia + mac dinh; lowest = dong re nhat cung hang dang ban."""
    g = max(0, min(hc.MAX_GRADE, int(grade)))
    lower, upper, p = sell_band(int(spid), g)
    lowest = min((r["price"] for r in market_rows(int(spid)) if r["grade"] == g), default=0) if g else 0
    return hc._json({"lower": lower, "upper": upper, "default": p, "lowest": lowest or p, "registrationFee": 0})


def ep_offer_list(query, body):
    st = hc._state()
    my_offers(st)
    return hc._json(offer_list(st))


def ep_transfer_offer(query, body):
    """POST /transfer/offer {playerobjectid, price}: rao ban 1 cau thu (khong phai da chinh)."""
    req = json.loads(body or b"{}")
    st = hc._state(); slot = hc._default_slot(st); team = st["teams"][str(slot)]
    pid = str(req.get("playerobjectid"))
    p = next((p for p in team["players"] if hc.iid(p) == pid), None)
    try:
        price = int(req.get("price") or 0)
    except (TypeError, ValueError):
        price = 0
    if p is None or hc.is_starter(team, p):
        return hc._json({"result": False, "reason": "TRANSFERMARKET_SELECT_SELLABLE_PLAYER"})
    lower, upper, _ = sell_band(p["spid"], hc.grade_of(p))
    if not 0 < price <= upper:
        return hc._json({"result": False, "reason": "TRANSFERMARKET_LOWERLIMIT"})
    now = time.time()
    if living() and len(my_offers(st)) >= OFFERS_MAX:
        return hc._json({"result": False, "reason": "TRANSFERMARKET_MAX_REGISTERED"})
    card = copy.deepcopy(p)
    hc.remove_player(team, p)
    o = {"_id": f"{int(now):08x}{os.urandom(8).hex()}", "issuedat": hc.w3c(now),
         "spid": p["spid"], "expandme": "player", "grade": hc.grade_of(p), "price": price,
         "playerobjectid": pid, "completefee": {"base": int(price * hc.SALE_FEE)}}
    if living():
        o.update(status="waiting", card=card)
        vm_buyer(o, now)
    else:  # ban ngay (nhu truoc)
        o.update(status="sold", soldat=hc.w3c(now))
    st.setdefault("offers", []).append(o)
    hc._save(st)
    return hc._json({"result": True, "resources": offer_resources(st)})


def _waiting(st, offerid):
    return next((o for o in my_offers(st) if o["_id"] == offerid and o.get("status") in ("waiting", "expired")), None)


def ep_offer_cancel(status, offerid, query, body):
    """DELETE /transfer/my/offer/<waiting|expired>/<id> (Huy): the ve CLB (Doi du bi). Da ban -> OFFER_NOT_EXIST_ERROR."""
    st = hc._state()
    o = _waiting(st, offerid)
    if o is None:
        return hc._json({"result": False, "reason": "OFFER_NOT_EXIST_ERROR"})
    team = st["teams"][str(hc._default_slot(st))]
    if len(team["players"]) >= hc.MAX_PLAYERS:
        return hc._json({"result": False, "reason": "CLB đã đầy, hãy thanh lý bớt cầu thủ."})
    card = o.get("card")
    if card and not any(hc.iid(x) == hc.iid(card) for x in team["players"]):
        card = dict(card, team2=True)  # ve Doi du bi nhu the moi
        team["players"].append(card)
    else:
        hc.add_player(team, o["spid"], o["grade"], cap=False)  # the cua chinh minh tra ve: giu nguyen cap
    st["offers"].remove(o)
    hc._save(st)
    return hc._json({"result": True, "resources": offer_resources(st)})


def ep_offer_reoffer(offerid, query, body):
    """POST /transfer/offer/from/<id> {price} (Dang lai): the dang cho voi gia moi."""
    req = json.loads(body or b"{}")
    st = hc._state()
    o = _waiting(st, offerid)
    if o is None:
        return hc._json({"result": False, "reason": "OFFER_NOT_EXIST_ERROR"})
    try:
        price = int(req.get("price") or 0)
    except (TypeError, ValueError):
        price = 0
    if not 0 < price <= sell_band(o["spid"], o["grade"])[1]:
        return hc._json({"result": False, "reason": "TRANSFERMARKET_LOWERLIMIT"})
    now = time.time()
    o.update(issuedat=hc.w3c(now), price=price, completefee={"base": int(price * hc.SALE_FEE)})
    if living():
        o["status"] = "waiting"
        vm_buyer(o, now)
    else:
        o.update(status="sold", soldat=hc.w3c(now))
        o.pop("card", None)
    hc._save(st)
    return hc._json({"result": True, "resources": offer_resources(st)})


def ep_offer_collect(offerid, coupon, query, body):
    """DELETE /transfer/my/offer/completed/<id|all>[/<coupon>]: nhan EP the da ban."""
    st = hc._state()
    offers = my_offers(st)
    done = [o for o in offers if o.get("status") == "sold" and offerid in ("all", o["_id"])]
    if not done:
        return hc._json({"result": False, "reason": "NO_SUCH_TRANSFERRED_OFFER_ERROR"})
    ep = sum(hc.sale_net(o) for o in done)
    hc.add_ep(st, ep)
    st["offers"] = [o for o in st["offers"] if o not in done]
    hc._save(st)
    return hc._json({"result": True, "ep": ep, "resources": offer_resources(st)})


def ep_transfer_detail(spid, grade, query, body):
    """GET /transfer/<spid>/<grade>/detail: bieu do gia (1 diem/ngay, PRICE_DAYS ngay)."""
    spid, grade = int(spid), max(1, min(hc.MAX_GRADE, int(grade)))
    p, today = market_value(spid, grade), vm_day()
    days = range(today - PRICE_DAYS + 1, today + 1)
    at = lambda d: hc.rank.VM_EPOCH + d * 86400
    if settings()["dynamic"]:
        day_price = lambda d: market_value(spid, grade, at(d) + 43200)
    else:
        day_price = lambda d: nice_price(p * market_factor(spid, grade, d))
    data = [[at(d) * 1000, max(1, day_price(d))] for d in days]
    return hc._json({"history": {"basePrice": max(1, p), "startat": hc.w3c(at(days[0])), "endat": hc.w3c(at(today)),
                                 "data": data}})


# --------------------------------------------------------------------------- install
def install(module):
    global hc, SETTINGS_FILE
    hc = module
    SETTINGS_FILE = os.path.join(hc.HERE, "gamedata", "market_settings.json")
    hc.market = sys.modules[__name__]
    hc.market_value = market_value
    replace = {hc.ep_transfer_top: ep_transfer_top, hc.ep_transfer_search: ep_transfer_search,
               hc.ep_transfer_purchase: ep_transfer_purchase, hc.ep_transfer_price: ep_transfer_price,
               hc.ep_offer_collect: ep_offer_collect}
    hc.ROUTES[:] = [(pat, replace.get(fn, fn)) for pat, fn in hc.ROUTES]
    hc.ROUTES[:0] = [
        (re.compile(r"/transfer/my/offer/(waiting|expired)/(\w+)"), ep_offer_cancel),
        (re.compile(r"/transfer/offer/from/(\w+)"), ep_offer_reoffer),
        (re.compile(r"/transfer/(\d+)/(\d+)/detail"), ep_transfer_detail),
    ]
    hc.ENDPOINTS["/transfer/offer"] = ep_transfer_offer
    hc.ENDPOINTS["/transfer/my/offer/list"] = ep_offer_list
    hc.ep_transfer_top, hc.ep_transfer_search, hc.ep_transfer_purchase = ep_transfer_top, ep_transfer_search, ep_transfer_purchase
    hc.ep_transfer_price, hc.ep_offer_collect, hc.ep_transfer_offer = ep_transfer_price, ep_offer_collect, ep_transfer_offer
    hc.offer_list, hc.offer_resources = offer_list, offer_resources
    hc.BACKGROUND.append(market_loop)
