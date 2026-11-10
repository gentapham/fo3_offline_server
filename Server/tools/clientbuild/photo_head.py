"""Photo heads (user 2026-11-09: "di admin panel buat 3d model berdasarkan foto close up pemain lalu
simpan di folder client untuk pemain2 yang masih generic face", then "Coba level 1 saja"): a
player's face from a close-up photo, painted on the 3D head of a scanned "donor" player.

A scanned head (engine DB headclasscode 0) is a set of files named by its headassetid:
  data_3.big faces/face_<id>_0_0_0_0_0_0_0_0_textures.rx3   the face: one 512x512 DXT1 texture
             (10 mips), the head unwrapped from the front, ears / teeth / tongue along the bottom
  data_4.big heads/head_<id>_0.rx3, hair/hair_<id>_0_0.rx3, hair/hair_<id>_0_textures.rx3
  data_6.big hairlod/hairlod_<id>_0_0.rx3
Each is a plain "RX3l" file or a "chunkzip" of one (raw deflate chunks); the client reads both,
and no file carries its own id. So a photo head is the donor's mesh and hair under a new id
(from ID_BASE: headassetid goes to 99,999,999, the highest used is 97,999,916) with a face
texture made from the photo: warped onto the donor's texture over the points clicked on the
photo (POINTS, matched to UV), its colours blended into the donor's skin, the eyes kept from the
donor (they sit on its eye sockets).

The heads live in gamedata/photo_heads (heads.json: {id: {"pid": player id, "row": donor engine row,
"donor": its headassetid, "made": date}}, <id>.png the face texture, <id>.rx3 the donor's face file
with that texture already DXT1-encoded); season.py client puts their files in build/data_7.big,
the client's smallest archive (5 KB: the locale ini files), rebuilt from orig/data_7.big each
time, and the player's engine rows wear it (season.photo_heads); apply.py --install copies it.

Port (2026-11-10) of fo3-server-indo tools/photo_head.py (b7f6983 + 334b131) to this install:
bigdir from this folder, ROOT = Server, GAME = the game folder above Server (FO3_GAME overrides),
and <id>.rx3 kept beside <id>.png so that installing (files / archive) needs no numpy / cv2:
only making a head does (pylib/cv2 + pylib/numpy, on the machine that makes the patches).

python -I photo_head.py selftest | uv HEADASSETID OUT.png [--game DIR]
"""
import json, os, struct, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))  # Server/tools/clientbuild
ROOT = os.path.dirname(os.path.dirname(HERE))  # Server
if HERE not in sys.path:
    sys.path.append(HERE)  # bigdir canh ben (append: khong che module cua server khi GM Tool nap file nay)
if os.path.join(HERE, "pylib") not in sys.path:
    sys.path.append(os.path.join(HERE, "pylib"))  # PIL (+ numpy, cv2 tren may lam ban va)
import bigdir

DIR = os.environ.get("FO3_PHOTO_HEADS") or os.path.join(ROOT, "gamedata", "photo_heads")  # FO3_*: chi de test
GAME = os.environ.get("FO3_GAME") or os.path.dirname(ROOT)
ID_BASE = 99000000
ARCHIVE = os.path.join("data", "data_7.big")
TEX = 0x7A0B60DA  # an RX3 texture section
SIZE = 512
FILES = {  # file name pattern -> the archive the client ships it in
    "data/sceneassets/faces/face_{}_0_0_0_0_0_0_0_0_textures.rx3": "data_3.big",
    "data/sceneassets/heads/head_{}_0.rx3": "data_4.big",
    "data/sceneassets/hair/hair_{}_0_0.rx3": "data_4.big",
    "data/sceneassets/hair/hair_{}_0_textures.rx3": "data_4.big",
    "data/sceneassets/hairlod/hairlod_{}_0_0.rx3": "data_6.big",
}
FACE_FILE = next(iter(FILES))
FLAT = 24  # the blur (texture pixels) of the photo's shading that make() swaps for the donor's
# The points clicked on the photo, in order (the subject's right = the photo's left), and where
# they sit on a face texture (UV pixels).
POINTS = ("eye_r", "eye_l", "nose", "mouth_r", "mouth_l", "chin", "forehead", "temple_r", "temple_l", "jaw_r", "jaw_l")
UV = {"eye_r": (205, 168), "eye_l": (307, 168), "nose": (256, 216), "mouth_r": (220, 265), "mouth_l": (292, 265),
      "chin": (256, 340), "forehead": (256, 100), "temple_r": (150, 145), "temple_l": (362, 145),
      "jaw_r": (160, 290), "jaw_l": (352, 290)}
# The points found by themselves (user: "Ya, pakai YuNet"): OpenCV Zoo's YuNet face detector (MIT)
# gives the eyes, the nose tip and the mouth corners; the rest follow from the face's proportions,
# in eye distances (across) and eye-to-mouth distances (down) from between the eyes, as measured on
# FO3's player pictures (the edges a little inside the face, off the background).
MODEL_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
MODEL = os.path.join(ROOT, "gamedata", "yunet", "face_detection_yunet_2023mar.onnx")
SHAPE = {"chin": (0, 1.7), "forehead": (0, -.85), "temple_r": (-.85, -.33), "temple_l": (.85, -.33),
         "jaw_r": (-.72, 1.22), "jaw_l": (.72, 1.22)}
# donors.json in DIR: the scanned heads' looks (donor_looks), made once
# nations whose players' eyes stand apart from the rest's (a donor's eyes stay on the face): China PR,
# Indonesia, Japan, Korea DPR, Korea Republic, Malaysia, Thailand, Vietnam (the scanned heads' East
# and Southeast Asian nations); a donor comes from the same side as the player
ASIAN = frozenset((155, 160, 163, 166, 167, 173, 188, 192))


def unchunk(b):
    """An RX3 file from a big entry ("chunkzip": BE version, size, chunk size, count; each chunk
    u32 BE length + u32 deflated, its data 16-aligned)."""
    if b[:8] != b"chunkzip":
        return b
    size, _, count = struct.unpack(">III", b[12:24])
    out, pos = bytearray(), 0x28
    for _ in range(count):
        pos = ((pos + 8 + 15) & ~15) - 8
        n, deflated = struct.unpack(">II", b[pos:pos + 8])
        pos += 8
        out += zlib.decompress(b[pos:pos + n], -15) if deflated else b[pos:pos + n]
        pos += n
    assert len(out) == size, (len(out), size)
    return bytes(out)


def _texture(rx3):
    """[(data offset, width, height, size)] of every mip of an RX3's DXT1 texture."""
    n = struct.unpack("<I", rx3[12:16])[0]
    off = next(o for h, o, _, _ in (struct.unpack("<4I", rx3[16 + 16 * i:32 + 16 * i]) for i in range(n)) if h == TEX)
    _, _, _, w, h, fmt, mips = struct.unpack("<IHHHHHH", rx3[off:off + 16])
    assert fmt == 1, f"texture format {fmt}, not DXT1"
    out, pos = [], off + 16
    for _ in range(mips):
        size = struct.unpack("<4I", rx3[pos:pos + 16])[2]
        out.append((pos + 16, w, h, size))
        pos += 16 + size
        w, h = max(1, w // 2), max(1, h // 2)
    return out


def _rgb565(c):
    import numpy as np
    return np.stack([(c >> 11 & 31) * 255 // 31, (c >> 5 & 63) * 255 // 63, (c & 31) * 255 // 31], -1).astype(np.int32)


def dxt1_decode(data, w, h):
    """RGB uint8 array of a DXT1 image."""
    import numpy as np
    bw, bh = max(1, w // 4), max(1, h // 4)
    d = np.frombuffer(data, np.uint8)[:bw * bh * 8].reshape(bh, bw, 8).astype(np.uint32)
    c0, c1 = d[..., 0] | d[..., 1] << 8, d[..., 2] | d[..., 3] << 8
    a, b = _rgb565(c0), _rgb565(c1)
    four = (c0 > c1)[..., None]
    pal = np.stack([a, b, np.where(four, (2 * a + b) // 3, (a + b) // 2), np.where(four, (a + 2 * b) // 3, 0)], 2)
    bits = d[..., 4] | d[..., 5] << 8 | d[..., 6] << 16 | d[..., 7] << 24
    img = np.zeros((bh * 4, bw * 4, 3), np.uint8)
    for i in range(16):
        idx = (bits >> 2 * i & 3)[..., None, None].repeat(3, -1)
        img[i // 4::4, i % 4::4] = np.take_along_axis(pal, idx, 2)[:, :, 0]
    return img[:h, :w]


def dxt1_encode(img):
    """DXT1 bytes of an RGB uint8 array (sides a multiple of 4, or under 4): per block, the ends
    of the colours' spread along their main axis, then the nearest of the 4 palette colours."""
    import numpy as np
    h, w = img.shape[:2]
    if h < 4 or w < 4:
        img = np.pad(img, ((0, max(0, 4 - h)), (0, max(0, 4 - w)), (0, 0)), mode="edge")
        h, w = img.shape[:2]
    px = img.reshape(h // 4, 4, w // 4, 4, 3).swapaxes(1, 2).reshape(h // 4, w // 4, 16, 3).astype(np.float64)
    mean = px.mean(2, keepdims=True)
    cov = np.einsum("...ki,...kj->...ij", px - mean, px - mean)
    axis = np.ones(cov.shape[:-1])
    for _ in range(8):  # power iteration: the main axis
        axis = np.einsum("...ij,...j->...i", cov, axis)
        axis /= np.maximum(np.linalg.norm(axis, axis=-1, keepdims=True), 1e-9)
    proj = np.einsum("...ki,...i->...k", px - mean, axis)
    hi = mean[..., 0, :] + axis * proj.max(-1, keepdims=True)
    lo = mean[..., 0, :] + axis * proj.min(-1, keepdims=True)

    def q(c):
        c = np.clip(np.rint(c), 0, 255).astype(np.uint32)
        return (c[..., 0] * 31 + 127) // 255 << 11 | (c[..., 1] * 63 + 127) // 255 << 5 | (c[..., 2] * 31 + 127) // 255
    c0, c1 = q(hi), q(lo)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    a, b = _rgb565(c0), _rgb565(c1)
    pal = np.stack([a, b, (2 * a + b) // 3, (a + 2 * b) // 3], 2).astype(np.float64)  # 4 colours (c0 > c1)
    idx = ((px[:, :, :, None, :] - pal[:, :, None, :, :]) ** 2).sum(-1).argmin(-1).astype(np.uint32)
    idx[c0 == c1] = 0  # one colour: the 3-colour mode, index 0
    bits = np.zeros(idx.shape[:2], np.uint32)
    for i in range(16):
        bits |= idx[..., i] << 2 * i
    out = np.zeros(idx.shape[:2] + (8,), np.uint8)
    for start, v, n in ((0, c0, 2), (2, c1, 2), (4, bits, 4)):
        for k in range(n):
            out[..., start + k] = v >> 8 * k & 0xFF
    return out.tobytes()


def texture_image(rx3):
    """The texture's first mip, as an RGB array."""
    off, w, h, size = _texture(rx3)[0]
    return dxt1_decode(rx3[off:off + size], w, h)


def with_texture(rx3, img):
    """The RX3 with its texture (every mip) made from an RGB array of its size."""
    import numpy as np
    from PIL import Image
    out = bytearray(rx3)
    for off, w, h, size in _texture(rx3):
        mip = img if (w, h) == img.shape[1::-1] else np.asarray(Image.fromarray(img).resize((w, h), Image.LANCZOS))
        data = dxt1_encode(mip)
        assert len(data) == size, (w, h, len(data), size)
        out[off:off + size] = data
    return bytes(out)


def donor_files(game, head):
    """{file name pattern: the donor head's file as shipped}; KeyError when one is missing."""
    data = os.path.join(game, "data")
    return {p: bigdir.read(os.path.join(data, big), p.format(head)) for p, big in FILES.items()}


def make(photo, points, donor_face, uv=None):
    """The face texture (RGB array) from a photo (RGB array) whose POINTS are `points` [[x, y]...]
    on the donor's face file: the photo inside the points' outline (but around the eyes) warped
    triangle by triangle onto UV, blended in by Poisson cloning (its edge takes the donor's skin)."""
    import cv2
    import numpy as np
    uv = uv or UV
    base = np.ascontiguousarray(texture_image(unchunk(donor_face)))
    src = np.float32(points)
    dst = np.float32([uv[k] for k in POINTS])
    assert src.shape == dst.shape, (src.shape, dst.shape)
    photo = np.ascontiguousarray(photo)
    d = float(np.linalg.norm(src[1] - src[0]))  # the photo's eyes, filled with the skin around them
    holes = np.zeros(photo.shape[:2], np.uint8)  # (their whites would show around the donor's eyelids)
    for p in src[:2]:
        cv2.ellipse(holes, (int(p[0]), int(p[1])), (max(1, int(.3 * d)), max(1, int(.15 * d))), 0, 0, 360, 255, -1)
    # and a smile's teeth, gums and tongue (the donor's lips are closed): in the mouth, much paler
    # than the face's skin and not in shadow (a light face's teeth are no brighter than it), or much brighter
    lips = np.zeros_like(holes)
    mouth, half = (src[3] + src[4]) / 2, float(np.linalg.norm(src[4] - src[3])) / 2
    cv2.ellipse(lips, (int(mouth[0]), int(mouth[1] + .05 * d)), (max(1, int(half)), max(1, int(.3 * d))), 0, 0, 360, 255, -1)
    hsv = cv2.cvtColor(photo, cv2.COLOR_RGB2HSV)
    fm = face_mask(photo.shape, src) > 0
    skin = np.median(hsv[fm], 0) if fm.any() else (0, 90, 170)
    teeth = (hsv[..., 1] < .6 * skin[1]) & (hsv[..., 2] > .8 * skin[2])
    open_ = lips & ((teeth | (hsv[..., 2] > skin[2] + 45)) * np.uint8(255))
    holes |= open_
    # an open mouth (4% of it: a grin 6%, closed lips under 2%) takes the donor's closed one (below)
    smile = np.count_nonzero(open_) > .04 * np.count_nonzero(lips)
    photo = cv2.inpaint(photo, cv2.dilate(holes, np.ones((3, 3), np.uint8)), max(1, int(.1 * d)), cv2.INPAINT_TELEA)
    warped = np.zeros_like(base)
    mask = np.zeros(base.shape[:2], np.uint8)
    sub = cv2.Subdiv2D((0, 0, SIZE, SIZE))
    for p in dst:
        sub.insert((float(p[0]), float(p[1])))
    where = {(round(float(x)), round(float(y))): i for i, (x, y) in enumerate(dst)}
    for t in sub.getTriangleList():
        tri = [where.get((round(t[i]), round(t[i + 1]))) for i in (0, 2, 4)]
        if None in tri:
            continue
        m = cv2.getAffineTransform(src[tri], dst[tri])
        part = cv2.warpAffine(np.ascontiguousarray(photo), m, (SIZE, SIZE), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REFLECT)
        tm = np.zeros_like(mask)
        cv2.fillConvexPoly(tm, np.int32(np.rint(dst[tri])), 255)
        warped[tm > 0] = part[tm > 0]
        mask |= tm
    mask = cv2.erode(mask, np.ones((5, 5), np.uint8))
    x, y, w, h = cv2.boundingRect(mask)
    if not w:
        return base
    # the photo's details over the donor's light: its broad shading (a lamp from one side) swapped
    # for the donor's, both smoothed inside the face only
    inside = (mask > 0).astype(np.float32)[..., None]
    smooth = lambda img: cv2.GaussianBlur(img * inside, (0, 0), FLAT) / np.maximum(cv2.GaussianBlur(inside, (0, 0), FLAT)[..., None], 1e-3)
    flat = warped.astype(np.float32) - smooth(warped.astype(np.float32)) + smooth(base.astype(np.float32))
    warped = np.uint8(np.clip(np.rint(flat), 0, 255))
    out = cv2.seamlessClone(warped, base, mask, (x + w // 2, y + h // 2), cv2.NORMAL_CLONE)
    eyes = np.zeros(mask.shape, np.float32)  # the donor's eyelids stay on its eye sockets, faded in
    for k in ("eye_r", "eye_l"):
        cv2.ellipse(eyes, tuple(int(v) for v in uv[k]), (35, 19), 0, 0, 360, 1.0, -1)
    if smile:  # and its lips under a smile's (a patched open mouth read as a pink tongue)
        m = (np.float32(uv["mouth_r"]) + np.float32(uv["mouth_l"])) / 2
        cv2.ellipse(eyes, (int(m[0]), int(m[1]) + 8), (int(abs(uv["mouth_l"][0] - uv["mouth_r"][0]) * .75), 30), 0, 0, 360, 1.0, -1)
    eyes = cv2.GaussianBlur(eyes, (0, 0), 6)[..., None]
    return np.uint8(np.rint(out * (1 - eyes) + base * eyes))


_MODEL_OK = {}


def model():
    """YuNet's file, fetched the first time into gamedata/yunet; its pinned SHA-256 is checked
    before every use (cached by size + mtime): a file that is not the pinned one is refused
    (ValueError) and never loaded."""
    import hashlib, urllib.request
    if not os.path.isfile(MODEL):
        os.makedirs(os.path.dirname(MODEL), exist_ok=True)
        req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "FO3-private-server photo heads"})
        data = urllib.request.urlopen(req, timeout=60).read()
        if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
            raise ValueError(f"{MODEL_URL}: not the expected file")
        with open(MODEL + ".tmp", "wb") as f:
            f.write(data)
        os.replace(MODEL + ".tmp", MODEL)
    st = os.stat(MODEL)
    sig = (st.st_size, st.st_mtime_ns)
    if _MODEL_OK.get(MODEL) != sig:
        with open(MODEL, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() != MODEL_SHA256:
                raise ValueError(f"{MODEL}: SHA-256 is not the pinned one (delete it to fetch it again)")
        _MODEL_OK[MODEL] = sig
    return MODEL


def landmarks(photo):
    """The POINTS of the largest face on a photo (RGB array), or None without one."""
    import cv2
    import numpy as np
    k = 512 / max(photo.shape[:2])  # YuNet finds the small faces of 128 px pictures once enlarged
    img = cv2.resize(cv2.cvtColor(np.ascontiguousarray(photo), cv2.COLOR_RGB2BGR), None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
    det = cv2.FaceDetectorYN.create(model(), "", (img.shape[1], img.shape[0]), 0.6, 0.3, 50)
    faces = det.detect(img)[1]
    if faces is None or not len(faces):
        return None
    f = max(faces, key=lambda r: r[2] * r[3])
    er, el, nose, mr, ml = (np.float64(f[i:i + 2]) / k for i in range(4, 14, 2))
    mid, d = (er + el) / 2, np.linalg.norm(el - er)
    ux = (el - er) / d
    uy = np.array([-ux[1], ux[0]])
    m = float(np.dot((mr + ml) / 2 - mid, uy))
    pts = {"eye_r": er, "eye_l": el, "nose": nose, "mouth_r": mr, "mouth_l": ml,
           **{n: mid + ux * a * d + uy * b * m for n, (a, b) in SHAPE.items()}}
    h, w = photo.shape[:2]
    return [[round(float(np.clip(pts[n][0], 0, w)), 1), round(float(np.clip(pts[n][1], 0, h)), 1)] for n in POINTS]


def face_mask(shape, points):
    """1 on the skin of a face with its POINTS: inside its outline (temples, forehead, jaw, chin),
    but the eyes and the mouth."""
    import cv2
    import numpy as np
    p = {n: np.float64(v) for n, v in zip(POINTS, points)}
    d = float(np.linalg.norm(p["eye_l"] - p["eye_r"]))
    m = np.zeros(shape[:2], np.uint8)
    cv2.fillConvexPoly(m, np.int32([p[k] for k in ("temple_r", "forehead", "temple_l", "jaw_l", "chin", "jaw_r")]), 1)
    for c, ax in ((p["eye_r"], (.45, .3)), (p["eye_l"], (.45, .3)), ((p["mouth_r"] + p["mouth_l"]) / 2, (.6, .3))):
        cv2.ellipse(m, (int(c[0]), int(c[1])), (max(1, int(ax[0] * d)), max(1, int(ax[1] * d))), 0, 0, 360, 0, -1)
    return m


def look(img, points):
    """[skin L, a, b, beard, hair] of a face (RGB array) with its POINTS: the skin's colour (median
    Lab of face_mask), how much darker the chin and jaw are, and the hair: [L, a, b] above
    the hairline, how high its outline stands over the eyes and how much of the sides beside the
    jaw it covers (long hair), from a picture's cut-out outline (None without hair to see). A
    photo and a face texture (at UV) measure the same places."""
    import cv2
    import numpy as np
    p = {n: np.float64(v) for n, v in zip(POINTS, points)}
    eyes = (p["eye_r"] + p["eye_l"]) / 2
    r = max(1, int(0.08 * np.linalg.norm(p["eye_l"] - p["eye_r"])))
    img = np.ascontiguousarray(img)
    lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB).astype(np.float64)
    h, w = img.shape[:2]
    fg = np.any(img != 150, axis=-1)  # a picture's background (picture()) does not count

    def at(*xy):
        m = np.zeros((h, w), np.uint8)
        for x, y in xy:
            cv2.circle(m, (int(np.clip(x, 0, w - 1)), int(np.clip(y, 0, h - 1))), r, 1, -1)
        m = (m > 0) & fg
        return lab[m].mean(0) if m.any() else None
    mouth = (p["mouth_r"] + p["mouth_l"]) / 2
    # the skin: the median of the face (a lamp's shine on a forehead or a cheek made a brown face
    # read light at three spots)
    face = (face_mask((h, w), points) > 0) & fg
    skin = np.median(lab[face], 0) if face.sum() >= 20 else at((p["eye_r"] + p["jaw_r"]) / 2, (p["eye_l"] + p["jaw_l"]) / 2,
                                                                 (p["forehead"] + eyes) / 2)
    chin = at((mouth + p["chin"]) / 2, (p["mouth_r"] + p["jaw_r"]) / 2, (p["mouth_l"] + p["jaw_l"]) / 2)
    hair = at(*(eyes + (p["forehead"] - eyes) * f for f in (1.5, 1.8, 2.1)))  # above the hairline
    if skin is None:
        skin = lab.reshape(-1, 3).mean(0)
    beard = skin[0] - chin[0] if chin is not None else 0
    if hair is not None:
        d = np.linalg.norm(p["eye_l"] - p["eye_r"])
        m = max(1.0, float(np.linalg.norm(mouth - eyes)))
        x0, x1 = (int(np.clip(v, 0, w - 1)) for v in (eyes[0] - .1 * d, eyes[0] + .1 * d))
        rows = np.flatnonzero(fg[:, x0:x1 + 1].any(1)[:int(np.clip(eyes[1], 1, h))])
        top = (eyes[1] - rows[0]) / m if len(rows) else 0  # the outline's top over the eyes
        y0, y1 = (int(np.clip(v, 0, h - 1)) for v in (mouth[1], p["chin"][1]))
        sides = [fg[y0:y1 + 1, int(np.clip(a, 0, w - 1)):int(np.clip(b, 0, w - 1)) + 1]
                 for a, b in ((p["jaw_r"][0] - .5 * d, p["jaw_r"][0] - .15 * d), (p["jaw_l"][0] + .15 * d, p["jaw_l"][0] + .5 * d))]
        side = float(np.mean([s.mean() if s.size else 0 for s in sides]))
        hair = [*hair, top, side]
    return [round(float(v), 1) for v in (*skin, beard)] + [None if hair is None else [round(float(v), 2) for v in hair]]


def donor_looks(game, rows, pictures=lambda pid: None):
    """{headassetid: [engine row, *look, picture skin]} of the scanned heads whose files are all in
    the client; rows = {headassetid: an engine row wearing it}. The skin and beard are its face
    texture's (what shows around the photo); the hair and the picture skin ([L, a, b]) its player's
    picture's (pictures(player id) = its file, or None), measured the same way as the player's photo
    (None without one): the hair's colour is in its hair files as masks only, and a lit picture reads
    lighter than a face texture. Kept in DIR/donors.json (made once: ~2,000 textures and pictures)."""
    import numpy as np
    try:
        with open(_path("donors.json")) as f:
            kept = json.load(f)
        if kept.get("version") == 6:
            return {int(k): v for k, v in kept["heads"].items()}
    except (OSError, ValueError, AttributeError):
        pass
    have = {p: {n for n, *_ in bigdir.entries(os.path.join(game, "data", big))} for p, big in FILES.items()}
    out = {}
    path = os.path.join(game, "data", FILES[FACE_FILE])
    for head, row in sorted(rows.items()):
        if not all(p.format(head) in have[p] for p in FILES):
            continue
        rx3 = unchunk(bigdir.read(path, FACE_FILE.format(head)))
        off, w, h, size = _texture(rx3)[2]  # the 128 x 128 mip is enough
        img = dxt1_decode(rx3[off:off + size], w, h)
        out[head] = [row, *look(img, [np.float64(UV[n]) * w / SIZE for n in POINTS])]
        pic, pts = pictures(row % 1000000), None
        if pic:
            try:
                pic = picture(pic)
                pts = landmarks(pic)
            except Exception:  # not a picture
                pass
        seen = look(pic, pts) if pts else None
        out[head][-1:] = [seen[4], seen[:3]] if seen else [None, None]
    os.makedirs(DIR, exist_ok=True)
    with open(_path("donors.json"), "w") as f:
        json.dump({"version": 6, "heads": {str(k): v for k, v in out.items()}}, f)
    return out


def suggest(photo_look, donors, nation=None):
    """The donor head (headassetid) whose look is closest to a photo's: skin colour first, then
    hair, and a clean-shaven one (a photo's own beard comes with it; a donor's would show around it,
    and a small picture's lighting hides how much beard it has). The skin is half the donor's
    picture's against the photo (both lit pictures) and half its face texture's (what the face
    gets) against the texture skin a picture like the photo's comes with (fit over the donors with
    both): a dark-skinned player got a light texture when photo and texture were compared, and a
    brightly lit brown face reads as light as a tanned white one on its picture alone. With the
    player's nation, a donor (its nation at [7]) on the other side of ASIAN comes last: its eyes stay."""
    import numpy as np
    want = photo_look[4]
    pic = lambda v: v[6] if len(v) > 6 else None
    other = lambda v: nation is not None and len(v) > 7 and v[7] is not None and (nation in ASIAN) != (v[7] in ASIAN)
    both = [(pic(v), v[1:4]) for v in donors.values() if pic(v)]
    tex = photo_look[:3]
    if len(both) >= 8:
        fit = np.linalg.lstsq(np.c_[[p for p, _ in both], np.ones(len(both))], np.float64([t for _, t in both]), rcond=None)[0]
        tex = list(np.r_[photo_look[:3], 1] @ fit)
    dist = lambda x, y: ((x[0] - y[0]) ** 2 + 2 * (x[1] - y[1]) ** 2 + 2 * (x[2] - y[2]) ** 2) ** .5

    def far(v):
        beard, hair = v[4:6]
        return (dist(pic(v), photo_look) if pic(v) else dist(v[1:4], tex)) / 2 + dist(v[1:4], tex) / 2 \
            + (0 if want is None else 40 if hair is None  # the hair: lightness and colour (red, blond)
               else 0.6 * abs(hair[0] - want[0]) + 1.5 * (abs(hair[1] - want[1]) + abs(hair[2] - want[2]))
               + 15 * abs(hair[3] - want[3]) + 30 * abs(hair[4] - want[4])) \
            + 1.5 * max(0, beard - 8) + 1000 * other(v)  # 8: the donors' median (a stubbled one showed on a clean face)
    return min(donors, key=lambda k: far(donors[k]))


def picture(path):
    """A player's picture (a cut-out PNG) as an RGB array on grey."""
    import numpy as np
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", im.size, (150, 150, 150, 255))
    bg.alpha_composite(im)
    return np.asarray(bg.convert("RGB"))


def _path(name):
    return os.path.join(DIR, name)


def missing():
    """None when this machine can make photo heads (numpy, PIL, cv2 with FaceDetectorYN), else the
    name of what is missing (installing the made ones needs none of them)."""
    for mod in ("numpy", "PIL.Image", "cv2"):
        try:
            m = __import__(mod)
        except Exception:
            return mod.split(".")[0]
        if mod == "cv2" and not hasattr(m, "FaceDetectorYN"):
            return "cv2.FaceDetectorYN"
    return None


def load():
    try:
        with open(_path("heads.json"), encoding="utf-8") as f:
            return {int(k): v for k, v in json.load(f).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def save(heads):
    os.makedirs(DIR, exist_ok=True)
    with open(_path("heads.json.tmp"), "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in sorted(heads.items())}, f, indent=1)
    os.replace(_path("heads.json.tmp"), _path("heads.json"))


def new_id(heads):
    return max(heads, default=ID_BASE - 1) + 1


def face_rx3(game, donor, img):
    """The donor head's face file (unchunked RX3) with its texture made from img (RGB 512x512):
    what <id>.rx3 keeps, so that installing needs no numpy."""
    return with_texture(unchunk(donor_files(game, donor)[FACE_FILE]), img)


def store(hid, img, rx3):
    """Keep a head's face texture: <id>.png (the GM Tool's preview) and <id>.rx3 (what goes in
    the client)."""
    from PIL import Image
    os.makedirs(DIR, exist_ok=True)
    f = _path(f"{hid}.png")
    Image.fromarray(img).save(f + ".tmp", "PNG")
    os.replace(f + ".tmp", f)
    with open(_path(f"{hid}.rx3.tmp"), "wb") as fh:
        fh.write(rx3)
    os.replace(_path(f"{hid}.rx3.tmp"), _path(f"{hid}.rx3"))


def remove(hid):
    for ext in ("png", "rx3"):
        try:
            os.remove(_path(f"{hid}.{ext}"))
        except OSError:
            pass


def _same_layout(new, old):
    """new is old (the donor's unchunked face file) with only its texture's mip data changed."""
    if len(new) != len(old) or new[:4] != b"RX3l":
        return False
    pos = 0
    for off, w, h, size in _texture(old):
        if new[pos:off] != old[pos:off]:
            return False
        pos = off + size
    return new[pos:] == old[pos:]


def files(game, heads=None):
    """{entry name: bytes} of every photo head: the donor's head and hair under its id and its own
    face file (<id>.rx3; made from <id>.png only when the rx3 is missing and numpy is here).
    A head whose donor files, face file or picture are gone or do not fit is left out (its engine
    rows must stay as they are then: season.py uses heads_in())."""
    out = {}
    for hid, h in sorted((load() if heads is None else heads).items()):
        try:
            got = donor_files(game, int(h["donor"]))
            old = unchunk(got[FACE_FILE])
            try:
                with open(_path(f"{hid}.rx3"), "rb") as f:
                    face = f.read()
            except OSError:
                import numpy as np
                from PIL import Image
                face = with_texture(old, np.asarray(Image.open(_path(f"{hid}.png")).convert("RGB")))
            if not _same_layout(face, old):
                continue
        except (KeyError, OSError, ValueError, TypeError, ImportError, AssertionError, struct.error, zlib.error, StopIteration):
            continue
        got[FACE_FILE] = face
        out.update({p.format(hid): b for p, b in got.items()})
    return out


def heads_in(entries):
    """The head ids whose every file is among entries (an archive's names)."""
    have = set(entries)
    ids = {int(n.split("_")[1]) for n in have if n.startswith("data/sceneassets/heads/head_")}
    return sorted(i for i in ids if i >= ID_BASE and all(p.format(i) in have for p in FILES))


def archive(game, original, heads=None):
    """(data_7.big from its original `original` plus every photo head's files, [head ids in it])."""
    add = files(game, heads)
    old = [(n, bigdir.raw(original, n)) for n, *_ in bigdir.entries(original)]
    return bigdir.build(old + sorted(add.items()), bigdir.tail(original)), heads_in(add)


def _blank_texture(size, mips):
    """An RX3 with one blank DXT1 texture (the self-test's donor)."""
    sec, w = b"", size
    for _ in range(mips):
        n = max(1, w // 4) ** 2 * 8
        sec += struct.pack("<4I", max(1, w // 4) * 8, max(1, w // 4), n, 0) + bytes(n)
        w = max(1, w // 2)
    sec = struct.pack("<IHHHHHH", 16 + len(sec), 1, 1, size, size, 1, mips) + sec
    return b"RX3l" + struct.pack("<III", 4, 0, 1) + struct.pack("<4I", TEX, 32, len(sec), 0) + sec


def cmd_selftest():
    import numpy as np
    rng = np.random.default_rng(1)
    img = (rng.random((16, 16, 3)) * 255).astype(np.uint8)
    img[:4, :4] = 77  # a one-colour block
    back = dxt1_decode(dxt1_encode(img), 16, 16)
    assert np.abs(back.astype(int) - img).mean() < 40 and (back[:4, :4] == back[0, 0]).all()
    smooth = np.dstack([np.tile(np.linspace(0, 255, 64), (64, 1))] * 3).astype(np.uint8)
    assert np.abs(dxt1_decode(dxt1_encode(smooth), 64, 64).astype(int) - smooth).max() < 12
    assert len(dxt1_encode(img[:2, :2])) == 8
    small = _blank_texture(8, 4)
    new = with_texture(small, np.full((8, 8, 3), 200, np.uint8))
    assert len(new) == len(small) and np.abs(texture_image(new).astype(int) - 200).max() <= 4
    deflate = zlib.compressobj(9, zlib.DEFLATED, -15)
    body = deflate.compress(small) + deflate.flush()
    cz = b"chunkzip" + struct.pack(">IIIII", 2, len(small), 0x2D000, 1, 16) + bytes(12) + struct.pack(">II", len(body), 1) + body
    assert unchunk(cz) == small and unchunk(small) == small
    # a photo whose points are UV's at half size: its dark mole on the nose lands on the nose, the
    # skin takes the donor's colour (Poisson), the eyes and the rest stay the donor's
    import cv2
    donor = with_texture(_blank_texture(SIZE, 10), np.full((SIZE, SIZE, 3), (180, 120, 100), np.uint8))
    skin = texture_image(donor)[5, 5].astype(int)
    photo = np.full((256, 256, 3), (230, 200, 190), np.uint8)
    cv2.circle(photo, (UV["nose"][0] // 2, UV["nose"][1] // 2), 4, (20, 20, 20), -1)
    out = make(photo, [[UV[k][0] / 2, UV[k][1] / 2] for k in POINTS], donor).astype(int)
    at = lambda k, dy=0: out[UV[k][1] + dy, UV[k][0]]
    assert at("nose").sum() < skin.sum() - 150 and abs(at("nose", 30) - skin).max() < 20, (at("nose"), at("nose", 30))
    assert abs(at("eye_r") - skin).max() < 8 and (out[5, 5] == skin).all(), (at("eye_r"), out[5, 5])
    assert new_id({}) == ID_BASE and new_id({ID_BASE: {}}) == ID_BASE + 1
    # (port) storing and installing: a fake game whose donor 7 has every file (its face file a
    # chunkzip), a head kept as <id>.png + <id>.rx3; data_7.big is the original plus the head's
    # files, the same bytes each time; a head whose rx3 does not fit its donor is left out
    import shutil, tempfile
    global DIR
    keep, tmp = DIR, tempfile.mkdtemp(prefix="photo_head_selftest_")
    try:
        DIR = os.path.join(tmp, "photo_heads")
        os.makedirs(os.path.join(tmp, "game", "data"))
        small_face = with_texture(_blank_texture(SIZE, 10), np.full((SIZE, SIZE, 3), 90, np.uint8))
        body = zlib.compressobj(9, zlib.DEFLATED, -15)
        body = body.compress(small_face) + body.flush()
        cz = b"chunkzip" + struct.pack(">IIIII", 2, len(small_face), 0x2D000, 1, 16) + bytes(12) + struct.pack(">II", len(body), 1) + body
        assert unchunk(cz) == small_face
        bigs = {}
        for pat, big in FILES.items():
            bigs.setdefault(big, []).append((pat.format(7), cz if pat == FACE_FILE else pat.encode() * 3))
        for big, ents in bigs.items():
            with open(os.path.join(tmp, "game", "data", big), "wb") as f:
                f.write(bigdir.build(ents))
        orig = os.path.join(tmp, "data_7.big")
        with open(orig, "wb") as f:
            f.write(bigdir.build([("locale@vn.ini", b"[x]\n" * 9)], b"\0\0L286"))
        game = os.path.join(tmp, "game")
        face_img = out.astype(np.uint8)
        rx3 = face_rx3(game, 7, face_img)
        assert _same_layout(rx3, small_face) and not _same_layout(rx3[:20] + b"\xff" + rx3[21:], small_face)
        assert texture_image(rx3).astype(int).sum() != texture_image(small_face).astype(int).sum()
        store(ID_BASE, face_img, rx3)
        save({ID_BASE: {"pid": 1, "row": 7, "donor": 7, "made": "x"}})
        data, ids = archive(game, orig)
        assert ids == [ID_BASE] and archive(game, orig)[0] == data
        a7 = os.path.join(tmp, "a7.big")
        with open(a7, "wb") as f:
            f.write(data)
        names = [n for n, *_ in bigdir.entries(a7)]
        assert names[0] == "locale@vn.ini" and bigdir.read(a7, "locale@vn.ini") == b"[x]\n" * 9
        assert sorted(names[1:]) == names[1:] and set(names[1:]) == {p.format(ID_BASE) for p in FILES}
        assert bigdir.read(a7, FACE_FILE.format(ID_BASE)) == rx3 and bigdir.tail(a7) == b"\0\0L286"
        assert bigdir.read(a7, "data/sceneassets/heads/head_{}_0.rx3".format(ID_BASE)) == b"data/sceneassets/heads/head_{}_0.rx3" * 3
        with open(os.path.join(DIR, f"{ID_BASE}.rx3"), "wb") as f:
            f.write(rx3[:-16])  # not the donor's layout
        assert archive(game, orig)[1] == [] and archive(game, orig)[0] == bigdir.build([("locale@vn.ini", b"[x]\n" * 9)], b"\0\0L286")
        save({})
        assert load() == {}
    finally:
        DIR = keep
        shutil.rmtree(tmp, ignore_errors=True)
    print("photo_head selftest ok")


if __name__ == "__main__":
    args = sys.argv[1:]
    game = args[args.index("--game") + 1] if "--game" in args else GAME
    if args[:1] == ["selftest"]:
        cmd_selftest()
    elif args[:1] == ["uv"] and len(args) >= 3:
        from PIL import Image
        face = bigdir.read(os.path.join(game, "data", FILES[FACE_FILE]), FACE_FILE.format(int(args[1])))
        Image.fromarray(texture_image(unchunk(face))).save(args[2])
        print(args[2])
    else:
        print(__doc__)
