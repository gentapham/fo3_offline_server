"""Nhac nen trong game (EA Trax) tu file .mp3 - GM Tool > Nhac nen.

Game phat bo EA Trax o menu sanh, menu tam dung, nghi giua hiep: 9 bai trong data_1.big,
audiodata/eatrax/eatrax_01.sbs (du lieu) + eatrax_01.sbr (bang muc luc, 9 offset LE u32 tai 0x258).
Codec goc: EALayer3 v2 "pcm_int" (EAAC codec 6), 44.1 kHz stereo, khoi 16 granule (9216 mau).

EALayer3 = MP3 layer III xep lai: moi khung EA chua MOT granule (ca 2 kenh), side info cua granule +
du lieu chinh cua no, KHONG co bit reservoir. Vi vay dong goi thang tu mp3 (khong giai ma, khong ma
hoa lai): doc tung khung mp3, lay du lieu chinh cua tung granule tu reservoir, ghi lai thanh khung EA.
Cau truc da doi chieu voi 9 bai goc (149.805 khung, khop tung byte - 2026-11-10).

Khung EA v2: ext(1) stereo(1) res(2) size(12) [ext: offset_mode(2) offset_samples(10) pcm_samples(10)
common_size(10)] | common: version(2) sr(2) chmode(2) modeext(2) granule(1) [MPEG1 & granule 1:
scfsi 4/kenh] {part2_3_length(12) + 47 bit (MPEG1) / 51 bit (MPEG2) side info}/kenh | du lieu chinh
tung kenh | bu 0 cho tron byte | [PCM pcm_samples*kenh*2 byte].
Hai granule dau moi bai la khung ext co 576 mau PCM (giong ban goc; o day la im lang).

Cai dat: phai thoat game (fifazf.exe dang doc data_1.big). Khi game dang mo, lenh duoc giu lai va tu
cai luc game tat. Lan dau sao luu bang muc luc + kich thuoc goc cua data_1.big (gamedata/music/), moi
lan cai: tra data_1.big ve goc (cat phan da them) roi noi du lieu moi vao cuoi -> file khong phinh dan.
"""
import json
import os
import struct
import subprocess
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "gamedata", "music")
CONF = os.path.join(DIR, "music.json")
ORIG_TABLE = os.path.join(DIR, "data_1.table.orig")
ORIG_META = os.path.join(DIR, "data_1.orig.json")
SBS, SBR = "audiodata/eatrax/eatrax_01.sbs", "audiodata/eatrax/eatrax_01.sbr"
ORIG_SBS = (248564864, 56828800)  # (offset, size) cua eatrax_01.sbs trong data_1.big goc
SLOTS = 9
SBR_OFFSETS = 0x258
ORIGINAL_TRACKS = ["All Time Low - Dear Maria, Count Me In", "Animals (instrumental)", "Fall Out Boy - The Phoenix",
                   "Glen Check - Vogue Boys and Girls", "Icona Pop - Emergency", "Jax Jones - Yeah Yeah Yeah",
                   "The Other Line - Chunk! No, Captain Chunk!", "The Sunshine Underground - Put You In Your Place",
                   "Tiggs - Run"]
MAX_MB = 30  # moi file mp3
GRANULES_PER_BLOCK = 16
LOCK = threading.Lock()
hc = None


class MusicError(Exception):
    pass


# ---------------------------------------------------------------- mp3 -> EALayer3
BITRATE = {1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
           2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]}
RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


class BitWriter:
    def __init__(self):
        self.v, self.n = 0, 0

    def put(self, value, bits):
        self.v = (self.v << bits) | (value & ((1 << bits) - 1))
        self.n += bits

    def bytes(self):
        pad = -self.n % 8
        return (self.v << pad).to_bytes((self.n + pad) // 8, "big")


def _skip_tags(d):
    """(start, end) of the audio frames: skip ID3v2 at the start, ID3v1 / APE at the end."""
    s, e = 0, len(d)
    while d[s:s + 3] == b"ID3" and s + 10 <= e:
        size = 0
        for b in d[s + 6:s + 10]:
            size = (size << 7) | (b & 0x7F)
        s += 10 + size + (10 if d[s + 5] & 0x10 else 0)
    if e - 128 >= s and d[e - 128:e - 125] == b"TAG":
        e -= 128
    if e - 32 >= s and d[e - 32:e - 24] == b"APETAGEX":
        size = struct.unpack("<I", d[e - 20:e - 16])[0]
        e -= size + (32 if d[e - 32 + 23] & 0x80 else 0)
    return s, max(s, e)


def _header(d, p):
    """Parsed MPEG audio layer III frame header at p, or None."""
    if p + 4 > len(d) or d[p] != 0xFF or (d[p + 1] & 0xE0) != 0xE0:
        return None
    h = int.from_bytes(d[p:p + 4], "big")
    vi, layer, prot = (h >> 19) & 3, (h >> 17) & 3, (h >> 16) & 1
    bri, sri, pad = (h >> 12) & 15, (h >> 10) & 3, (h >> 9) & 1
    cm, me = (h >> 6) & 3, (h >> 4) & 3
    if vi == 1 or layer != 1 or bri in (0, 15) or sri == 3:
        return None
    mpeg1 = vi == 3
    br = BITRATE[1 if mpeg1 else 2][bri] * 1000
    sr = RATES[vi][sri]
    size = (144 if mpeg1 else 72) * br // sr + pad
    ch = 1 if cm == 3 else 2
    side = (17 if ch == 1 else 32) if mpeg1 else (9 if ch == 1 else 17)
    return dict(vi=vi, sri=sri, sr=sr, cm=cm, me=me, ch=ch, mpeg1=mpeg1, crc=2 if prot == 0 else 0,
                size=size, side=side)


def mp3_granules(d):
    """Yield (header, granule index, scfsi list, [(part2_3_length, other side bits, main data int)] per
    channel) for every granule of an mp3 file. Raises MusicError when it is not a usable mp3."""
    p, end = _skip_tags(d)
    res = bytearray()  # bit reservoir (main data bytes of the recent frames)
    first, frames = None, 0
    while p < end:
        h = _header(d, p)
        if not h or p + h["size"] > end:
            # resync: tim khung tiep theo (rac giua cac khung)
            p += 1
            continue
        nxt = p + h["size"]
        if first is None:
            # khung dau phai co khung hop le ngay sau no (tranh nham byte 0xFF ngau nhien)
            if nxt < end and not _header(d, nxt):
                p += 1
                continue
            first = h
        elif (h["vi"], h["sri"], h["ch"]) != (first["vi"], first["sri"], first["ch"]):
            p += 1  # khung khac thong so (rac) -> bo qua
            continue
        si = p + 4 + h["crc"]
        side = int.from_bytes(d[si:si + h["side"]], "big")
        sbits = h["side"] * 8
        body = d[si + h["side"]:nxt]
        if body[:4] in (b"Xing", b"Info") or d[si + 32:si + 36] == b"VBRI" or (side == 0 and frames == 0):
            p = nxt  # khung thong tin VBR (khong co am thanh)
            continue
        pos = 0

        def take(n):
            nonlocal pos
            pos += n
            return (side >> (sbits - pos)) & ((1 << n) - 1)

        ch, mpeg1 = h["ch"], h["mpeg1"]
        mdb = take(9 if mpeg1 else 8)
        take((5 if ch == 1 else 3) if mpeg1 else (1 if ch == 1 else 2))
        scfsi = [take(4) for _ in range(ch)] if mpeg1 else [0] * ch
        grs = []
        for _g in range(2 if mpeg1 else 1):
            grs.append([(take(12), take(47 if mpeg1 else 51)) for _ in range(ch)])
        start = len(res) - mdb
        res += body
        if start < 0:  # reservoir chua du (khung dau sau cat ghep) -> bo qua
            p = nxt
            continue
        total = sum(l for g in grs for l, _ in g)
        nbytes = (total + 7) // 8
        if start + nbytes > len(res):
            p = nxt
            continue
        val = int.from_bytes(res[start:start + nbytes], "big")
        vbits = nbytes * 8
        bit = 0
        for gi, g in enumerate(grs):
            chans = []
            for l, other in g:
                main = (val >> (vbits - bit - l)) & ((1 << l) - 1) if l else 0
                bit += l
                chans.append((l, other, main))
            yield h, gi, scfsi, chans
        if len(res) > 4096:
            del res[:-1024]
        frames += 1
        p = nxt
    if not frames:
        raise MusicError("Không đọc được file mp3 (không tìm thấy khung MPEG Layer III).")


def _ea_frame(h, gi, scfsi, chans, pcm=0):
    w = BitWriter()
    w.put(h["vi"], 2); w.put(h["sri"], 2); w.put(h["cm"], 2); w.put(h["me"], 2)
    w.put(gi, 1)
    if h["mpeg1"] and gi == 1:
        for s in scfsi:
            w.put(s, 4)
    for l, other, _ in chans:
        w.put(l, 12); w.put(other, 47 if h["mpeg1"] else 51)
    for l, _, main in chans:
        if l:
            w.put(main, l)
    common = w.bytes()
    st = 1 if h["ch"] == 2 else 0
    if pcm:
        pcm_bytes = bytes(pcm * h["ch"] * 2)
        size = 6 + len(common) + len(pcm_bytes)
        head = BitWriter()
        head.put(1, 1); head.put(st, 1); head.put(0, 2); head.put(size, 12)
        head.put(0, 2); head.put(0, 10); head.put(pcm, 10); head.put(len(common), 10)
        return head.bytes() + common + pcm_bytes
    size = 2 + len(common)
    if size > 0xFFF:
        raise MusicError("Khung mp3 quá lớn.")
    return bytes([(st << 6) | (size >> 8), size & 0xFF]) + common


def mp3_to_stream(data):
    """mp3 bytes -> (EA stream bytes: header block + data blocks + end block, info)."""
    blocks, cur, n, h0 = [], [], 0, None
    for h, gi, scfsi, chans in mp3_granules(data):
        h0 = h0 or h
        if n < 2:  # 2 granule dau: khung ext + 576 mau PCM (im lang), moi khung 1 khoi - nhu ban goc
            blocks.append((576, _ea_frame(h, gi, scfsi, chans, pcm=576)))
        else:
            cur.append(_ea_frame(h, gi, scfsi, chans))
            if len(cur) == GRANULES_PER_BLOCK:
                blocks.append((576 * len(cur), b"".join(cur))); cur = []
        n += 1
    if cur:
        blocks.append((576 * len(cur), b"".join(cur)))
    if n < 8:
        raise MusicError("File mp3 quá ngắn.")
    samples = 576 * n
    out = bytearray()
    h1 = (1 << 28) | (6 << 24) | ((h0["ch"] - 1) << 18) | h0["sr"]
    out += b"\x48\x00\x00\x0c" + struct.pack(">II", h1, (1 << 30) | samples)
    for ns, body in blocks:
        out += bytes([0x44]) + (8 + len(body)).to_bytes(3, "big") + struct.pack(">I", ns) + body
    out += bytes.fromhex("450000060000")
    return bytes(out), {"sr": h0["sr"], "ch": h0["ch"], "samples": samples, "seconds": round(samples / h0["sr"], 1)}


# ---------------------------------------------------------------- data_1.big
def _bigdir():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bigdir_music", os.path.join(HERE, "tools", "clientbuild", "bigdir.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def game_dir():
    return getattr(hc, "GAME", None) or os.path.dirname(HERE)


def big_path():
    return os.path.join(game_dir(), "data", "data_1.big")


def game_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq fifazf.exe", "/NH"], capture_output=True, text=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return "fifazf.exe" in out.lower()
    except Exception:
        return False


def _entry(bd, table, name):
    for _p, n, off, size, _u in bd._walk(table):
        if n == name:
            return off, size
    raise MusicError(f"data_1.big không có {name}")


def ensure_backup():
    """Sao luu bang muc luc + kich thuoc cua data_1.big goc (chi khi file con nguyen ban)."""
    if os.path.isfile(ORIG_TABLE) and os.path.isfile(ORIG_META):
        return
    bd, big = _bigdir(), big_path()
    t = bd.table(big)
    if _entry(bd, t, SBS) != ORIG_SBS:
        raise MusicError("data_1.big không còn nguyên bản (nhạc đã bị thay bằng cách khác) và chưa có bản sao lưu. "
                         "Hãy chép lại data_1.big gốc rồi thử lại.")
    os.makedirs(DIR, exist_ok=True)
    with open(ORIG_TABLE + ".tmp", "wb") as f:
        f.write(t)
    os.replace(ORIG_TABLE + ".tmp", ORIG_TABLE)
    with open(ORIG_META, "w") as f:
        json.dump({"size": os.path.getsize(big), "table_len": len(t)}, f)


def _restore_big():
    """data_1.big ve dung ban goc: ghi lai bang muc luc goc, cat phan du lieu da them."""
    meta = json.load(open(ORIG_META))
    t = open(ORIG_TABLE, "rb").read()
    big = big_path()
    if os.path.getsize(big) < meta["size"]:
        raise MusicError("data_1.big ngắn hơn bản gốc - không khôi phục được.")
    with open(big, "r+b") as f:
        cur = f.read(len(t))
        if cur[:4] != b"BIg4":
            raise MusicError("data_1.big hỏng đầu file.")
        f.seek(0)
        f.write(t)
        f.truncate(meta["size"])


def build_bank(streams, slots):
    """(sbs bytes, sbr bytes): streams noi tiep (can 32 byte), slots[i] = chi so stream cho bai thu i."""
    bd = _bigdir()
    t = open(ORIG_TABLE, "rb").read()
    off, size = _entry(bd, t, SBR)
    with open(big_path(), "rb") as f:
        f.seek(off)
        sbr = bytearray(f.read(size))
    sbs = bytearray(b"data" + bytes(28))
    offs = []
    for s in streams:
        offs.append(len(sbs))
        sbs += s
        sbs += bytes(-len(sbs) % 32)
    for i in range(SLOTS):
        struct.pack_into("<I", sbr, SBR_OFFSETS + 4 * i, offs[slots[i]])
    return bytes(sbs), bytes(sbr)


# ---------------------------------------------------------------- cau hinh + cai dat
def load_conf():
    try:
        c = json.load(open(CONF, encoding="utf-8"))
    except Exception:
        c = {}
    c.setdefault("songs", [])
    c.setdefault("installed", "original")  # original | custom
    c.setdefault("pending", None)  # None | "install" | "restore"
    return c


def save_conf(c):
    os.makedirs(DIR, exist_ok=True)
    with open(CONF + ".tmp", "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)
    os.replace(CONF + ".tmp", CONF)


def slot_map(n):
    """9 vi tri bai cua game -> lap lai danh sach bai cua nguoi dung."""
    return [i % n for i in range(SLOTS)]


class _FileLock:
    """Khoa giua cac tien trinh (server + launcher deu co the cai lenh dang cho)."""
    def __init__(self):
        self.path = os.path.join(DIR, ".lock")

    def __enter__(self):
        os.makedirs(DIR, exist_ok=True)
        for _ in range(240):
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > 300:  # khoa mo coi (tien trinh bi tat giua chung)
                        os.remove(self.path)
                        continue
                except OSError:
                    pass
                time.sleep(0.5)
        raise MusicError("Đang có tiến trình khác cài nhạc, thử lại sau.")

    def __exit__(self, *a):
        try:
            os.remove(self.path)
        except OSError:
            pass


def _apply(action):
    with _FileLock():
        return _apply_locked(action)


def _apply_locked(action):
    """Cai (install) hoac tra goc (restore) ngay - goi khi game DA tat."""
    c = load_conf()
    ensure_backup()
    if action == "install":
        songs = [s for s in c["songs"] if os.path.isfile(os.path.join(DIR, s["file"]))]
        if not songs:
            raise MusicError("Chưa có bài nào.")
        streams = [mp3_to_stream(open(os.path.join(DIR, s["file"]), "rb").read())[0] for s in songs]
        sbs, sbr = build_bank(streams, slot_map(len(streams)))
        _restore_big()
        _bigdir().patch(big_path(), {SBS: sbs, SBR: sbr})
        c["installed"] = "custom"
        c["installed_songs"] = [s["name"] for s in songs]
    else:
        _restore_big()
        c["installed"] = "original"
        c["installed_songs"] = []
    c["pending"] = None
    c["applied_at"] = time.strftime("%H:%M %d/%m")
    c.pop("error", None)
    save_conf(c)
    return c


def request(action):
    """Cai ngay neu game dang tat; dang mo -> giu lenh, tu cai khi game tat."""
    with LOCK:
        if game_running():
            c = load_conf()
            c["pending"] = action
            save_conf(c)
            _start_watch()
            return c, False
        return _apply(action), True


_watch = None


def _start_watch():
    global _watch
    if _watch and _watch.is_alive():
        return
    _watch = threading.Thread(target=_watch_loop, daemon=True, name="music-watch")
    _watch.start()


def _watch_loop():
    while True:
        time.sleep(5)
        c = load_conf()
        if not c.get("pending"):
            return
        if game_running():
            continue
        time.sleep(3)  # game vua tat: cho no dong file
        with LOCK:
            c = load_conf()
            if not c.get("pending") or game_running():
                continue
            try:
                _apply(c["pending"])
                print("Nhạc nền: đã cài", flush=True)
            except Exception as e:
                c["pending"] = None
                c["error"] = str(e)
                save_conf(c)
                print("Nhạc nền: lỗi", e, flush=True)
        return


def apply_pending():
    """Cai lenh dang cho neu game dang tat. Goi luc server bat va luc launcher sap mo game (qua i18n.apply_game):
    launcher co tuy chon tu tat may chu khi thoat game -> luong theo doi trong server chet truoc khi kip cai.
    Tra ve hanh dong da lam ("install" / "restore") hoac None."""
    c = load_conf()
    act = c.get("pending")
    if not act or game_running():
        return None
    with LOCK:
        c = load_conf()
        act = c.get("pending")
        if not act:
            return None
        try:
            _apply(act)
            return act
        except Exception as e:
            c = load_conf()
            c["pending"] = None
            c["error"] = str(e)
            save_conf(c)
            raise


def start(module):
    """Goi khi server bat: cai lenh dang cho (game da tat) hoac theo doi tiep neu game con mo."""
    global hc
    hc = module
    if load_conf().get("pending"):
        try:
            act = apply_pending()
            if act:
                print("Nhạc nền: đã " + ("cài nhạc mới" if act == "install" else "trả nhạc gốc"), flush=True)
        except Exception as e:
            print("Nhạc nền: lỗi", e, flush=True)
        if load_conf().get("pending"):
            _start_watch()
