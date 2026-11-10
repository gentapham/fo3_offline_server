"""Nen / giai nen dinh dang chunkzip cua FO3 (FIFA 14 engine).
Header 40 B: b"chunkzip" | BE u32: version 2, unpacked size, max chunk 0x2d000, chunk count, align 0x10, 0, 0, 0
Moi khoi: BE u32 packed size, BE u32 flag (1 = deflate raw) + du lieu; du lieu moi khoi bat dau o vi tri chia het 0x10
(header khoi dat ngay truoc, sau cac byte 0 dem)."""
import struct, zlib
CHUNK = 0x2d000


def unpack(d):
    assert d[:8] == b"chunkzip"
    ver, usize, mx, n, al = struct.unpack_from(">5I", d, 8)
    p, out = 40, b""
    for _ in range(n):
        while (p + 8) % al:
            p += 1
        cs, fl = struct.unpack_from(">II", d, p); p += 8
        out += zlib.decompress(d[p:p + cs], -15) if fl == 1 else d[p:p + cs]
        p += cs
    assert len(out) == usize, (len(out), usize)
    return out


def pack(raw, level=9):
    chunks = [raw[i:i + CHUNK] for i in range(0, len(raw), CHUNK)]
    out = bytearray(b"chunkzip" + struct.pack(">8I", 2, len(raw), CHUNK, len(chunks), 0x10, 0, 0, 0))
    for c in chunks:
        while (len(out) + 8) % 0x10:
            out += b"\0"
        o = zlib.compressobj(level, zlib.DEFLATED, -15)
        z = o.compress(c) + o.flush()
        out += struct.pack(">II", len(z), 1) + z
    assert unpack(bytes(out)) == raw
    return bytes(out)
