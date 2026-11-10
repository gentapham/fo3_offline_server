"""Chat kenh (global chat) voi HLV ao + tin bong da trong ngay (port fo3-server-indo a0b49dd).

* Cua so Chat o sanh: tu truoc toi gio khong bao gio gan vao (MenuScreen.onFreeRoamLoadingFinished cho chuong
  Huong dan nguoi moi "setcontroller", account.guides rong) -> /account them "setcontroller" vao guides va
  features.chatting (tab Kenh, Thi tham, Chan).
* Tab Kenh: ChattingController vao kenh (POST /chat/channel {channelid} | PUT /chat/channel/<id> -> {result,
  channelid, created, reason}; GET /chat/coach/<ten>/channel/detail {result, channelid, coachid} cho "den kenh
  cua ban"); 1 dong chat = POST /chat/my/channel/message {message, hint}; moi dong cua kenh (ca cua minh, client
  khong tu hien) den qua push /chat/my/channel/messages/latest {type "chat", coachid, coachname, message, time}.
  time (W3C) = gio cua dong (thieu -> moi dong hien 7:00 = epoch theo gio VN).
* Nguoi trong kenh = HLV ao dang online (rank.vm_status): 1-2 nguoi tra loi (responses/vm_channel_chat.json),
  thinh thoang tu noi (moi phut), phan lon ve tin bong da hom nay: Google Trends + Google News (tin toan cau
  luon co + cac nuoc tick o GM Tool, mac dinh Viet Nam), lay 1 lan / ngay trong luong nen, timeout ngan, cache
  gamedata/news_cache.json; bo tin toi pham / tai nan / qua doi. Moi HLV ao thich CLB cua minh, cau thu hay nhat
  doi minh, hoac 1 nuoc (responses/vm_news_chat.json). Khong gui du lieu ca nhan nao (chi doc RSS cong khai).
Cai dat: gamedata/chat_settings.json (GM Tool > Xep hang HLV ao > Chat kenh).
"""
import functools
import html
import json
import os
import random
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

hc = None
SETTINGS_FILE = None  # gamedata/chat_settings.json
NEWS_FILE = None      # gamedata/news_cache.json (cache, khong gui trong ban cap nhat)
SETTINGS_DEFAULT = {"vmchat": True, "news": True, "newsregions": ["VN"]}
CHANNEL_MAX = 99
CHANNEL_REPLY = (0.7, 0.25)  # xac suat HLV online thu 1 / thu 2 tra loi
CHANNEL_IDLE = 0.1           # moi phut
CHANNEL_NEWS = 0.7           # phan cac cau tu noi la ve tin trong ngay
TIMEOUT = 10
UA = {"User-Agent": "Mozilla/5.0"}
THANKS = ("cam on", "cảm ơn", "thanks", "thank", "tks", "thx", "camon")
HELLO = ("chao", "chào", "hello", "hi", "alo", "helo", "xin chao", "hey")

NEWS_GLOBAL = ("Toàn cầu", "en-GB", "GB", "GB:en", "football", ())
NEWS_REGIONS = {  # vung: (ten, hl, gl, ceid, tu khoa, tu chi doi tuyen)
    "VN": ("Việt Nam", "vi", "VN", "VN:vi", "bóng đá", ("tuyển việt nam", "việt nam", "dtvn")),
    "TH": ("Thái Lan", "th", "TH", "TH:th", "ฟุตบอล", ("ช้างศึก", "ทีมชาติไทย")),
    "ID": ("Indonesia", "id", "ID", "ID:id", "sepak bola", ("timnas", "indonesia")),
    "MY": ("Malaysia", "ms", "MY", "MY:ms", "bola sepak", ("harimau malaya", "malaysia")),
    "KR": ("Hàn Quốc", "ko", "KR", "KR:ko", "축구", ("대표팀", "한국")),
}
NEWS_COUNTRY = {"VN": "ĐT Việt Nam", "TH": "ĐT Thái Lan", "ID": "ĐT Indonesia", "MY": "ĐT Malaysia", "KR": "ĐT Hàn Quốc"}
FOOTBALL_WORDS = ("bóng đá", "sepak bola", "bola", "liga", "timnas", "gol ", "pelatih", "football", "soccer", "premier league",
                  "laliga", "la liga", "serie a", "bundesliga", "ligue 1", "champions league", "fifa", "uefa", "v-league",
                  "v.league", "đtvn", "hlv", "tiền đạo", "thủ môn", "ฟุตบอล", "축구", "transfer", "manchester", "madrid",
                  "barcelona", "arsenal", "liverpool", "chelsea", "juventus", "bayern", "psg", "inter ", "milan")
# khong bao gio dem ra tan gau: toi pham, qua doi, bao luc, lam dung, phan biet, ma tuy, co bac, tai nan
NEWS_SKIP = ("assault", "sexual", " rape", "murder", " kill", " dies ", " died", "death", " dead ", "abuse", "racis", "arrest",
             "court", "jail", "prison", "police", "crash", "tragedy", "funeral", "suicide", "cancer", "war ", "bomb",
             "shooting", "stabb", "drug", "gambl", "betting", "violence", "victim", "meninggal", "tewas", "wafat",
             "pelecehan", "perkosa", "pembunuhan", "kekerasan", "ditangkap", "polisi", "penjara", "narkoba", "rasis",
             "kecelakaan", "berduka", "judi", "korban", "qua doi", "tu vong", "cuong hiep", "bat giu", "ma tuy",
             "tai nan", "hiep dam", "danh bac", " ca do ", "khoi to", "tam giam", "cong an", "an mang", " giet", " chet ",
             "bao luc", "xam hai", "lua dao", "tu sat", "ung thu", "dot quy", "phan biet chung toc",
             "เสียชีวิต", "ข่มขืน", "ยาเสพติด", "사망", "성폭", "마약", "폭행")
NEWS = {"key": None, "at": 0, "items": [], "error": ""}  # tin hom nay: [{title, source, trend, region}]
_NEWS_LOCK = threading.Lock()
_FETCH_LOCK = threading.Lock()


# --------------------------------------------------------------------------- cai dat
def settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        d = d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        d = {}
    out = {k: d[k] if isinstance(d.get(k), bool) else v for k, v in SETTINGS_DEFAULT.items() if k != "newsregions"}
    regions = d.get("newsregions", SETTINGS_DEFAULT["newsregions"])
    out["newsregions"] = [r for r in NEWS_REGIONS if isinstance(regions, list) and r in regions]
    return out


def save_settings(d):
    cur = settings()
    for k in ("vmchat", "news"):
        if k in d:
            cur[k] = bool(d[k])
    if "newsregions" in d:
        if not isinstance(d["newsregions"], list) or any(r not in NEWS_REGIONS for r in d["newsregions"]):
            raise ValueError("Quốc gia không hợp lệ")
        cur["newsregions"] = [r for r in NEWS_REGIONS if r in d["newsregions"]]
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SETTINGS_FILE)
    return settings()


# --------------------------------------------------------------------------- dong chat
def chat_push(uri, who, text, delay=0, **extra):
    """1 dong {type, coachid, coachname, message, time} vao tab chat, ngay hoac sau delay giay."""
    def go():
        hc.messenger_push(uri, {"type": "chat", "coachid": who.get("_id", ""), "coachname": who.get("name", ""),
                                "message": text, **extra, "time": hc.w3c(time.time())})
    if delay:
        t = threading.Timer(delay, go)
        t.daemon = True
        t.start()
    else:
        go()


@functools.lru_cache(maxsize=4)
def _lines(name, mtime):
    with open(os.path.join(hc.RESPONSES, name), encoding="utf-8") as f:
        return json.load(f)["vi"]


def lines(name):
    path = os.path.join(hc.RESPONSES, name)
    return _lines(name, os.path.getmtime(path))


def personality(vm):
    return hc.rank.vm_personality(vm)


def channel_line(vm, trigger):
    return random.choice(lines("vm_channel_chat.json")[trigger][personality(vm)])


def channel_say(vm, text, delay):
    chat_push("/chat/my/channel/messages/latest", vm, text, delay or 0.01)


def channel_online():
    try:
        return [vm for vm in hc.rank.virtual_managers() if hc.rank.vm_status(vm) != "offline"]
    except Exception as e:  # dam dong chua dung duoc: kenh van chay (chi khong ai tra loi)
        print(f"chat online: {e!r}", flush=True)
        return []


def trigger_of(text):
    low = f" {text.lower()} "
    plain = f" {_plain(text)} "
    if any(w in low or _plain(w) in plain for w in THANKS):
        return "thanks"
    if any(re.search(rf"\b{re.escape(_plain(w))}\b", plain) for w in HELLO):
        return "hello"
    return "reply"


def ep_channel_message(query, body):
    """POST /chat/my/channel/message {message, hint}: dong cua toi tra lai cho toi, co the co nguoi tra loi."""
    try:
        req = json.loads(body or b"{}")
    except ValueError:
        req = {}
    text = str(req.get("message") or "")[:200]
    if not text.strip():
        return hc._json({"result": True})
    st = hc._state()
    coach = hc.my_coach(st)
    chat_push("/chat/my/channel/messages/latest", coach, text, hint=req.get("hint"))
    if settings()["vmchat"]:
        trig = trigger_of(text)
        online = channel_online()
        random.shuffle(online)
        for i, vm in enumerate(online[:2]):
            if random.random() < CHANNEL_REPLY[i]:
                channel_say(vm, channel_line(vm, trig), 2 + 3 * i + random.uniform(0, 3))
    return hc._json({"result": True})


def ep_channel_join(cid, query, body):
    """POST /chat/channel {channelid} | PUT /chat/channel/<id>: kenh 1..CHANNEL_MAX."""
    try:
        cid = int(cid or json.loads(body or b"{}").get("channelid") or 1)
    except (ValueError, TypeError, AttributeError):
        cid = 0
    if not 1 <= cid <= CHANNEL_MAX:
        return hc._json({"result": False, "reason": "channel does not exist", "channelid": cid})
    return hc._json({"result": True, "channelid": cid, "created": False, "reason": ""})


# --------------------------------------------------------------------------- tin bong da trong ngay
def _plain(s):
    return "".join(ch for ch in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(ch)).lower() \
        .replace("đ", "d")


def is_football(text):
    t = f" {_plain(text)} "
    return any(_plain(w) in t for w in FOOTBALL_WORDS)


def is_skipped(text):
    t = f" {_plain(text)} "
    return any(_plain(w) in t for w in NEWS_SKIP)


def _fetch_url(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=TIMEOUT) as r:
        return r.read()


def news_fetch(region, get=None):
    """Tin bong da hien tai cua 1 vung ("" = toan cau), trends truoc: [{title, source, trend}] (toi da 40)."""
    import xml.etree.ElementTree as ET
    get = get or _fetch_url
    _, hl, gl, ceid, q, _ = NEWS_REGIONS[region] if region else NEWS_GLOBAL
    ns = {"ht": "https://trends.google.com/trending/rss"}
    items = []
    try:  # khong bat buoc: vung khong co trends van co tin
        for it in ET.fromstring(get(f"https://trends.google.com/trending/rss?geo={gl}")).iter("item"):
            news = [(n.findtext("ht:news_item_title", "", ns), n.findtext("ht:news_item_source", "", ns))
                    for n in it.findall("ht:news_item", ns)]
            hit = next(((t, s) for t, s in news if is_football(t)), None)
            if hit or is_football(it.findtext("title", "")):
                t, s = hit or (news[0] if news else (it.findtext("title", ""), ""))
                items.append({"title": html.unescape(t).strip(), "source": s, "trend": it.findtext("title", "")})
    except Exception as e:
        print(f"news trends {region or 'global'}: {e!r}", flush=True)
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": hl, "gl": gl, "ceid": ceid})
    for it in ET.fromstring(get(url)).iter("item"):
        t, s = html.unescape(it.findtext("title", "")), it.findtext("source", "")
        items.append({"title": (t[:-len(s) - 3] if s and t.endswith(" - " + s) else t).strip(), "source": s, "trend": ""})
    seen, out = set(), []
    for i in items:
        if i["title"] and i["title"].lower() not in seen and not is_skipped(i["title"]):
            seen.add(i["title"].lower())
            out.append(i)
    return out[:40]


def _load_cache():
    if NEWS["key"] is not None:
        return
    try:
        with open(NEWS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        NEWS.update(key=(tuple(d["key"][0]), d["key"][1]), at=d["at"], items=list(d["items"]), error=d.get("error", ""))
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        NEWS["key"] = ("", "")  # khong co cache


def news_refresh(now=None, get=None, force=False):
    """Tin toan cau + cac nuoc da tick, 1 lan / ngay gio VN (lai khi doi nuoc, hoac 10 phut sau 1 lan khong lay
    duoc gi). Chi chay trong luong nen / GM Tool, khong bao gio trong yeu cau cua game; tai mang khong giu khoa
    nao khac (_NEWS_LOCK chi giu luc doc / ghi NEWS)."""
    now = time.time() if now is None else now
    cfg = settings()
    if not cfg["news"]:
        return NEWS
    with _NEWS_LOCK:
        _load_cache()
        key = (tuple(cfg["newsregions"]), hc.today_wib(now))
        if not force and NEWS["key"] == key and (NEWS["items"] or now - NEWS["at"] <= 600):
            return NEWS
    if not _FETCH_LOCK.acquire(blocking=False):  # dang tai o luong khac
        return NEWS
    try:
        items, errors = [], []
        for region in ("", *key[0]):
            try:
                items += [{**i, "region": region} for i in news_fetch(region, get)]
            except Exception as e:  # 1 nguon hong: cac nguon khac van noi
                errors.append(f"{region or 'global'}: {type(e).__name__}")
                print(f"news {region or 'global'}: {e!r}", flush=True)
        with _NEWS_LOCK:
            if not items and NEWS["items"] and NEWS["key"][0] == key[0]:
                items = NEWS["items"]  # mat mang: giu tin cu
            NEWS.update(key=key, at=now, items=items, error="; ".join(errors))
            try:
                os.makedirs(os.path.dirname(NEWS_FILE), exist_ok=True)
                tmp = NEWS_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"key": [list(key[0]), key[1]], "at": now, "items": items, "error": NEWS["error"]}, f,
                              ensure_ascii=False, indent=1)
                os.replace(tmp, NEWS_FILE)
            except OSError as e:
                print(f"news cache: {e!r}", flush=True)
    finally:
        _FETCH_LOCK.release()
    return NEWS


def news_background(force=False):
    threading.Thread(target=lambda: _safe(news_refresh, force=force), daemon=True, name="news-fetch").start()


def _safe(fn, **kw):
    try:
        fn(**kw)
    except Exception as e:
        print(f"chat: {e!r}", flush=True)


NAME_SKIP = {"real", "club", "city", "united", "sporting", "athletic", "atletico", "calcio", "olympique", "borussia", "fc"}


def vm_likes(vm, regions):
    """[(loai, ten, tu khoa)]: CLB cua anh ta, cau thu hay nhat doi, 1 nuoc (co dinh trong cac nuoc da tick)."""
    out = []
    club = (getattr(hc, "_CLUB_TEAMS", {}).get(vm.get("teamid")) or {}).get("teamname")
    if club:
        words = {_plain(club)} | {w for w in _plain(club).split() if len(w) >= 5 and w not in NAME_SKIP}
        out.append(("club", club, words))
    try:
        team = hc.rank.vm_team(vm["_id"])
        best = max(team["players"], key=lambda p: hc.card_ovr(int(p["spid"])))
        star = hc.market.card_name(int(best["spid"]))
        if not star.isdigit() and len(star.split()[-1]) >= 4:
            out.append(("player", star, {_plain(star.split()[-1])}))
    except Exception:
        pass
    if regions:
        region = random.Random("newsregion" + vm["_id"]).choice(sorted(regions))
        out.append(("country", NEWS_COUNTRY[region], {_plain(w) for w in NEWS_REGIONS[region][5]}))
    return out


def news_line(vm, kind, **words):
    """Cau loai kind (general, club, player, country, react) theo tinh cach."""
    L = lines("vm_news_chat.json")
    return random.choice(L[kind]).format(**words) + random.choice(L["mood"][personality(vm)])


def channel_news(online, rng=random):
    """1 tin trong ngay do 1 HLV online dang + 1-2 nguoi khac tra loi: [(vm, cau, delay)]."""
    items = NEWS["items"]
    if not items or not online:
        return []
    who = rng.sample(online, min(3, len(online)))
    likes = vm_likes(who[0], sorted({i["region"] for i in items} - {""}))
    mine = {k: [(name, i) for i in items if any(w in f" {_plain(i['title'])} " for w in words)] for k, name, words in likes}
    kind = rng.choice([k for k in mine if mine[k]] + ["general"])
    if kind == "general":  # tin dau cua 1 nguon (toan cau hoac 1 nuoc da tick)
        src = rng.choice(sorted({i["region"] for i in items}))
        name, item = "", rng.choice([i for i in items if i["region"] == src][:15])
    else:
        name, item = rng.choice(mine[kind])
    title = item["title"] if len(item["title"]) <= 110 else item["title"][:107].rstrip() + "..."
    out = [(who[0], news_line(who[0], kind, title=title, name=name), 0)]
    for i, vm in enumerate(who[1:]):
        if rng.random() < (0.85, 0.45)[i]:
            club = (getattr(hc, "_CLUB_TEAMS", {}).get(vm.get("teamid")) or {}).get("teamname") or "đội mình"
            for _ in range(5):  # khong trung cau nguoi truoc
                line = news_line(vm, "react", title=title, name=name, club=club)
                if not any(line[:20] == o[1][:20] for o in out[1:]):
                    break
            out.append((vm, line, 4 + 5 * i + rng.uniform(0, 4)))
    return out


def channel_idle(rng=random, online=None):
    """Moi phut (luong nen): thinh thoang 1 HLV online noi trong kenh, phan lon ve tin hom nay."""
    cfg = settings()
    if not cfg["vmchat"]:
        return []
    online = channel_online() if online is None else online
    if not online or rng.random() >= CHANNEL_IDLE:
        return []
    talk = channel_news(online, rng) if cfg["news"] and rng.random() < CHANNEL_NEWS else []
    if not talk:
        vm = rng.choice(online)
        talk = [(vm, channel_line(vm, "idle"), 0)]
    for vm, text, delay in talk:
        channel_say(vm, text, delay)
    return talk


def chat_loop():
    """Luong nen: tin trong ngay (moi 10 phut kiem tra, lay 1 lan / ngay) + HLV ao noi chuyen trong kenh."""
    time.sleep(30)
    n = 0
    while True:
        try:
            if n % 10 == 0:
                news_refresh()
            if settings()["vmchat"]:
                with hc.STATE_LOCK:  # dam dong HLV ao (nhu rank.presence_loop)
                    online = channel_online()
                channel_idle(online=online)
        except Exception as e:
            print(f"chat loop: {e!r}", flush=True)
        n += 1
        time.sleep(60)


def install(module):
    global hc, SETTINGS_FILE, NEWS_FILE
    hc = module
    hc.chat = sys.modules[__name__]
    SETTINGS_FILE = os.path.join(hc.HERE, "gamedata", "chat_settings.json")
    NEWS_FILE = os.path.join(hc.HERE, "gamedata", "news_cache.json")
    hc.ENDPOINTS.update({"/chat/my/channel/message": ep_channel_message,
                         "/chat/channel": lambda q, b: ep_channel_join(None, q, b)})
    hc.ROUTES[:0] = [(re.compile(r"/chat/channel/(\d+)"), ep_channel_join),
                     (re.compile(r"/chat/coach/[^/]+/channel/detail"),
                      lambda q, b: hc._json({"result": True, "channelid": 1, "coachid": ""}))]
    hc.DYNAMIC_PATHS.update({"/chat/my/channel/message", "/chat/channel"})
    hc.BACKGROUND.append(chat_loop)

    base_account = hc.ENDPOINTS["/account"]

    def ep_account(query, body):
        ctype, data = base_account(query, body)
        acc = json.loads(data)
        # guides = cac chuong Huong dan nguoi moi da xong (BeginnersGuideManager.isChapterCompleted): chua co
        # "setcontroller" -> MenuScreen chay huong dan do thay vi gan cua so Chat (nut Chat khong lam gi);
        # tay cam van chon trong Tuy chon
        acc["guides"] = sorted({*(acc.get("guides") or []), "setcontroller"})
        ch = acc.setdefault("features", {}).setdefault("chatting", {})
        ch.setdefault("board", {}).update({"channel": {}, "whisper": {}})
        ch.setdefault("list", {}).setdefault("blacklist", {})
        return hc._json(acc)

    hc.ENDPOINTS["/account"] = ep_account
