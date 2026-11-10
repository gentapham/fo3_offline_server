"""Da ngon ngu cho game / GM Tool / launcher (2026-11-10): Tieng Viet (mac dinh), English, ภาษาไทย.

Mot cai dat duy nhat: gamedata/server_options.json "language" = "vi" | "en" | "th" (GM Tool doi; launcher va game
theo do).
- Trong game:
  * Bo chu cua client: client doc LANGUAGESTRING_<LANG>.json theo `language` cua GET /info/server/locale.
    vi / en: language "eng", file data/ui/localization/LANGUAGESTRING_ENG.json = lang/game/LANGUAGESTRING_VI.json
    hoac _EN.json; th: language "tha" + LANGUAGESTRING_THA.json (font Thai cua client). apply_game() chep file
    dung ngon ngu vao cho (launcher goi truoc khi mo game; GM Tool goi khi doi ngon ngu).
  * Chu do server sinh (ten goi, vat pham, thong bao, nhiem vu, chat HLV ao...): translate_obj() dich cac chuoi
    tieng Viet trong JSON tra ve theo tu dien lang/<en|th>.json {chu Viet: ban dich} - khop nguyen chuoi truoc,
    roi thay tung doan (doan dai truoc) cho chuoi ghep tu nhieu manh (f-string).
- GM Tool: admin/i18n.js dich chu tren trang (va chu do JS ve) bang cung tu dien; loi API dich o admin.py.
- Launcher: doc language + lang/<lang>.json cua thu muc Server canh no.
"""
import json
import os
import re
import shutil
import stat
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.path.dirname(HERE)
LANG_DIR = os.path.join(HERE, "lang")
LANGS = ("vi", "en", "th")
DEFAULT = "vi"
NAMES = {"vi": "Tiếng Việt", "en": "English", "th": "ภาษาไทย"}
CLIENT_LANG = {"vi": "eng", "en": "eng", "th": "tha"}  # language cua /info/server/locale
VIET = re.compile("[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]", re.I)
KEEP_KEYS = frozenset(("teamid", "id", "_id", "uid", "key", "code", "uri", "type", "kind", "spid", "vie", "vn",
                       "teamColorId", "tcid", "groupid", "stid"))
MIN_PIECE = 5  # doan ngan hon khong thay giua chuoi (tranh dich nham ten nguoi, vd "Hải")

# mau ngan kem so ma doan van ban qua ngan de thay giua chuoi
PATTERNS = []  # (regex, {lang: thay}) doc tu lang/patterns.json (load_patterns): mau ngan kem so


def load_patterns():
    """lang/patterns.json [{re, en, th}] (dung chung voi GM Tool: thay the dang JS "$1")."""
    try:
        with open(os.path.join(LANG_DIR, "patterns.json"), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = []
    out = []
    for r in raw:
        try:
            out.append((re.compile(r["re"]), {k: re.sub(r"[$](\d)", r"\\g<\1>", v)
                                               for k, v in r.items() if k in LANGS}))
        except (re.error, KeyError, TypeError):
            continue
    PATTERNS[:] = out
    return raw


load_patterns()
hc = None  # http_config (install)
_lock = threading.Lock()
_cache = {}  # lang -> (sig, dict, regex)
_options_reader = None


def options_file():
    return os.path.join(HERE, "gamedata", "server_options.json")


def current():
    """Ngon ngu dang chon (vi neu chua chon / file hong)."""
    try:
        if hc is not None:
            lang = hc.server_options().get("language")
        else:
            with open(options_file(), encoding="utf-8") as f:
                lang = json.load(f).get("language")
    except (OSError, ValueError, AttributeError):
        lang = None
    return lang if lang in LANGS else DEFAULT


def client_lang(lang=None):
    return CLIENT_LANG[lang or current()]


def _load(lang):
    path = os.path.join(LANG_DIR, f"{lang}.json")
    try:
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}, None
    with _lock:
        c = _cache.get(lang)
        if c and c[0] == sig:
            return c[1], c[2]
        try:
            with open(path, encoding="utf-8") as f:
                d = {k: v for k, v in json.load(f).items() if isinstance(k, str) and isinstance(v, str) and k and v}
        except (OSError, ValueError):
            d = {}
        for k, v in list(d.items()):  # chu trong HTML viet &amp; -> trang / JSON hien "&"
            if "&amp;" in k:
                d.setdefault(k.replace("&amp;", "&"), v.replace("&amp;", "&"))
        # doan thay giua chuoi: tu 2 tu tro len (hoac >= 8 ky tu) - tu don ("Thắng", "Hải") chi dich khi khop nguyen
        # chuoi, khong thi ten nguoi bi dich nham ("Nguyễn Hữu Thắng" -> "Nguyễn Hữu Win")
        pieces = sorted((k for k in d if VIET.search(k) and ((" " in k.strip() and len(k.strip()) >= 4)
                                                          or len(k.strip()) >= 8)), key=len, reverse=True)
        rx = re.compile("|".join(map(re.escape, pieces))) if pieces else None
        _cache[lang] = (sig, d, rx)
        _templates[lang] = _build_templates(d)
        return d, rx


PH = re.compile(r"\$\{[^{}]*\}|\{[^{}]*\}")  # bien cua f-string / format / template JS trong khoa tu dien
_templates = {}  # lang -> [(regex, [placeholder], translation)]


def _build_templates(d):
    """Khoa co bien ("Quà đăng nhập - ngày {n}") -> regex: phan chu giu nguyen, bien = (.+?). Ban dich dat lai gia tri
    vao cho bien cung ten. Phan chu co dinh phai >= 6 ky tu (tranh khop qua long)."""
    out = []
    for k, v in d.items():
        phs = PH.findall(k)
        if not phs or not VIET.search(k):
            continue
        lits = PH.split(k)
        if sum(len(x.strip()) for x in lits) < 6:
            continue
        body = "(.+?)".join(re.escape(x) for x in lits)
        try:
            out.append((re.compile(body + ("$" if k.rstrip() == k else "")), phs, v))
        except re.error:
            continue
    out.sort(key=lambda t: -len(t[0].pattern))
    return out


def _apply_templates(s, lang):
    for rx, phs, v in _templates.get(lang, ()):
        m = rx.search(s)
        if not m:
            continue
        rep = v
        for ph, val in zip(phs, m.groups()):
            rep = rep.replace(ph, val, 1)
        if PH.search(rep):  # ban dich thieu / doi ten bien: bo qua
            continue
        s = s[:m.start()] + rep + s[m.end():]
        if not VIET.search(s):
            break
    return s


def dictionary(lang=None):
    lang = lang or current()
    return {} if lang == DEFAULT else _load(lang)[0]


def tr(s, lang=None):
    """Chuoi s theo ngon ngu dang chon (giu nguyen neu la tieng Viet chua co ban dich / khong phai tieng Viet)."""
    lang = lang or current()
    if lang == DEFAULT or not isinstance(s, str) or not VIET.search(s):
        return s
    d, rx = _load(lang)
    if s in d:
        return d[s]
    key = (lang, id(d), s)
    hit = _memo.get(key)
    if hit is not None:
        return hit
    out = _tr_slow(s, lang, d, rx)
    if len(_memo) > 50000:
        _memo.clear()
    _memo[key] = out
    return out


_memo = {}


def _tr_slow(s, lang, d, rx):
    t = s.strip()
    if t in d:
        return s.replace(t, d[t], 1)
    s = _apply_templates(s, lang)  # chuoi f-string ghep bien ("Quà đăng nhập - ngày 4")
    for pat, rep in PATTERNS:  # mau kem so (lang/patterns.json) truoc khi thay tung cum
        if lang in rep:
            s = pat.sub(rep[lang], s)
    if rx is not None and VIET.search(s):
        s = rx.sub(lambda m: d[m.group(0)], s)
    return s


def translate_obj(obj, lang=None):
    """Ban sao cua obj (dict/list/str) voi moi chuoi tieng Viet da dich (khoa dict giu nguyen)."""
    lang = lang or current()
    if lang == DEFAULT:
        return obj

    def walk(x):
        if isinstance(x, str):
            return tr(x, lang) if VIET.search(x) else x
        if isinstance(x, dict):
            # truong la MA DINH DANH: giu nguyen (team.teamcolors[].teamid = "Cầu thủ đặc biệt" bi dich -> client
            # khong tra duoc Team Color -> danh sach cau thu trong, tieng Anh "khong vao duoc", 2026-11-10)
            return {k: (v if k in KEEP_KEYS else walk(v)) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x
    return walk(obj)


# --------------------------------------------------------------------------- bo chu cua client
GAME_DIR = os.path.join(LANG_DIR, "game")
LOC = os.path.join(GAME, "data", "ui", "localization")
EXT = os.path.join(GAME, "data", "ui", "external")


def _write(dst, data):
    if os.path.isfile(dst):
        try:
            with open(dst, "rb") as f:
                if f.read() == data:
                    return False
        except OSError:
            pass
        os.chmod(dst, stat.S_IREAD | stat.S_IWRITE)
    tmp = dst + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dst)
    return True


def apply_game(lang=None):
    """Chep bo chu cua ngon ngu vao cho client doc. Tra ve danh sach file da doi ([] = dung san)."""
    lang = lang or current()
    changed = []
    src = {"vi": "LANGUAGESTRING_VI.json", "en": "LANGUAGESTRING_EN.json", "th": "LANGUAGESTRING_TH.json"}[lang]
    path = os.path.join(GAME_DIR, src)
    if not os.path.isfile(path):
        return changed
    with open(path, "rb") as f:
        data = f.read()
    if lang in ("vi", "en"):
        targets = [os.path.join(LOC, "LANGUAGESTRING_ENG.json")]
    else:
        targets = [os.path.join(EXT, "LANGUAGESTRING_THA.json"), os.path.join(LOC, "LANGUAGESTRING_THA.json")]
    for dst in targets:
        if os.path.isdir(os.path.dirname(dst)) and _write(dst, data):
            changed.append(os.path.relpath(dst, GAME))
    _music_pending()
    return changed


def _music_pending():
    """Launcher goi apply_game() ngay truoc khi mo game -> tien cai nhac nen dang cho (music.py), vi may chu co the
    da tu tat theo game truoc khi kip cai."""
    try:
        import importlib.util
        import sys
        mod = sys.modules.get("music")
        if mod is None:
            spec = importlib.util.spec_from_file_location("music", os.path.join(HERE, "music.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.modules["music"] = mod
        mod.apply_pending()
    except Exception as e:
        print("Nhạc nền:", e, flush=True)


def set_language(lang):
    """Luu ngon ngu vao server_options.json (giu cac tuy chon khac) va chep bo chu cua client."""
    if lang not in LANGS:
        raise ValueError(lang)
    f = options_file()
    try:
        with open(f, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        cfg = {}
    cfg["language"] = lang
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=1)
    return apply_game(lang)


def install(module):
    """Moc vao http_config: /info/server/locale theo ngon ngu, moi JSON tra ve cho game duoc dich."""
    global hc
    hc = module
    base_json = hc._json

    def _json(obj):
        return base_json(translate_obj(obj))
    hc._json = _json
    base_file = hc.from_file

    def from_file(path):  # responses/*.json phuc vu nguyen file: dich luon chu tieng Viet trong do
        hit = base_file(path)
        if hit and current() != DEFAULT:
            try:
                text = hit[1].decode("utf-8-sig")
                if VIET.search(text) or "\\u" in text:  # file luu dang \uXXXX: chu Viet chi thay sau khi giai ma
                    obj = json.loads(text)
                    out = translate_obj(obj)
                    if out != obj:
                        return (hit[0], json.dumps(out).encode())
            except (UnicodeDecodeError, ValueError):
                pass
        return hit
    hc.from_file = from_file
    hc.LANGUAGE_OF = lambda: client_lang()
    hc.LANGUAGE = client_lang()  # client_localization / ten bong, san... doc goi ngon ngu cua client dang dung
    try:  # may ban be dung launcher cu (chua goi apply_game): server chep bo chu dung ngon ngu luc khoi dong
        apply_game()
    except OSError as e:
        print(f"i18n apply_game: {e!r}", flush=True)
