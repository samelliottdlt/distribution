# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present ROCKNIX (https://github.com/ROCKNIX)

import os
import struct
import zlib


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _read(f, off, size):
    f.seek(off)
    return f.read(size)


def _align64(n):
    return (n + 63) & ~63


def _smdh_from_ncch(f, base):
    hdr = _read(f, base, 0x200)
    if len(hdr) < 0x200 or hdr[0x100:0x104] != b"NCCH":
        return None
    if not hdr[0x188 + 7] & 0x4:
        return None
    exefs = base + _u32(hdr, 0x1A0) * 0x200
    if exefs == base:
        return None
    table = _read(f, exefs, 0xA0)
    for i in range(10):
        name = table[i * 16:i * 16 + 8].rstrip(b"\0")
        if name == b"icon":
            off, size = struct.unpack_from("<II", table, i * 16 + 8)
            return _read(f, exefs + 0x200 + off, size)
    return None


def _smdh_from_cia(f):
    hdr = _read(f, 0, 0x20)
    hdr_size, _, _, cert, ticket, tmd, meta = struct.unpack_from("<IHHIIII", hdr, 0)
    content = struct.unpack_from("<Q", hdr, 0x18)[0]
    if meta < 0x400 + 0x36C0:
        return None
    off = _align64(hdr_size) + _align64(cert) + _align64(ticket) + _align64(tmd) + _align64(content)
    return _read(f, off + 0x400, 0x36C0)


def _smdh_from_3dsx(f):
    hdr = _read(f, 0, 0x2C)
    if hdr[:4] != b"3DSX" or struct.unpack_from("<H", hdr, 4)[0] < 0x2C:
        return None
    off, size = struct.unpack_from("<II", hdr, 0x20)
    return _read(f, off, size) if size else None


def _find_smdh(path):
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        if ext == ".cia":
            return _smdh_from_cia(f)
        if ext == ".3dsx":
            return _smdh_from_3dsx(f)
        head = _read(f, 0, 0x200)
        if head[0x100:0x104] == b"NCSD":
            return _smdh_from_ncch(f, _u32(head, 0x120) * 0x200)
        return _smdh_from_ncch(f, 0)


def _morton(i):
    x = (i & 1) | ((i >> 1) & 2) | ((i >> 2) & 4)
    y = ((i >> 1) & 1) | ((i >> 2) & 2) | ((i >> 3) & 4)
    return x, y


_MORTON = [_morton(i) for i in range(64)]


def icon_3ds(path):
    smdh = _find_smdh(path)
    if not smdh or len(smdh) < 0x36C0 or smdh[:4] != b"SMDH":
        return None
    data = smdh[0x24C0:0x24C0 + 48 * 48 * 2]
    out = bytearray(48 * 48 * 4)
    for tile in range(36):
        tx, ty = (tile % 6) * 8, (tile // 6) * 8
        for i in range(64):
            px = struct.unpack_from("<H", data, (tile * 64 + i) * 2)[0]
            mx, my = _MORTON[i]
            o = ((ty + my) * 48 + tx + mx) * 4
            r, g, b = (px >> 11) & 0x1F, (px >> 5) & 0x3F, px & 0x1F
            out[o:o + 4] = bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2), 255))
    return 48, 48, bytes(out)


def icon_nds(path):
    with open(path, "rb") as f:
        hdr = _read(f, 0, 0x6C)
        if len(hdr) < 0x6C:
            return None
        banner = _u32(hdr, 0x68)
        if not banner:
            return None
        data = _read(f, banner, 0x240)
    if len(data) < 0x240:
        return None
    pal = []
    for i in range(16):
        c = struct.unpack_from("<H", data, 0x220 + i * 2)[0]
        r, g, b = c & 0x1F, (c >> 5) & 0x1F, (c >> 10) & 0x1F
        pal.append(bytes(((r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2), 0 if i == 0 else 255)))
    out = bytearray(32 * 32 * 4)
    for tile in range(16):
        tx, ty = (tile % 4) * 8, (tile // 4) * 8
        for i in range(32):
            byte = data[0x20 + tile * 32 + i]
            y, x = i // 4, (i % 4) * 2
            for n, idx in enumerate((byte & 0xF, byte >> 4)):
                o = ((ty + y) * 32 + tx + x + n) * 4
                out[o:o + 4] = pal[idx]
    return 32, 32, bytes(out)


def _iso_find(f, path):
    pvd = _read(f, 16 * 2048, 2048)
    if pvd[1:6] != b"CD001":
        return None
    lba, size = _u32(pvd, 156 + 2), _u32(pvd, 156 + 10)
    for part in path:
        found = None
        data = _read(f, lba * 2048, size)
        off = 0
        while off < len(data) and not found:
            n = data[off]
            if n == 0:
                off = (off // 2048 + 1) * 2048
                continue
            name = data[off + 33:off + 33 + data[off + 32]].split(b";")[0].decode("latin-1")
            if name.upper() == part:
                found = (_u32(data, off + 2), _u32(data, off + 10))
            off += n
        if not found:
            return None
        lba, size = found
    return _read(f, lba * 2048, size)


def icon_psp(path):
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        if ext == ".iso":
            png = _iso_find(f, ("PSP_GAME", "ICON0.PNG"))
        elif ext == ".pbp":
            hdr = _read(f, 0, 0x28)
            if hdr[:4] != b"\0PBP":
                return None
            start, end = struct.unpack_from("<II", hdr, 0x0C)
            png = _read(f, start, end - start)
        else:
            return None
    return png if png and png[:8] == b"\x89PNG\r\n\x1a\n" else None


def _tga(data):
    idlen, cmap, kind = data[0], data[1], data[2]
    w, h, bpp, desc = struct.unpack_from("<HHBB", data, 12)
    if cmap or kind not in (2, 10) or bpp not in (24, 32):
        return None
    step, pos, px = bpp // 8, 18 + idlen, bytearray()
    while len(px) < w * h * step:
        if kind == 2:
            px += data[pos:pos + w * h * step]
            break
        head = data[pos]
        pos += 1
        count = (head & 0x7F) + 1
        if head & 0x80:
            px += data[pos:pos + step] * count
            pos += step
        else:
            px += data[pos:pos + step * count]
            pos += step * count
    if len(px) < w * h * step:
        return None
    out = bytearray(w * h * 4)
    for y in range(h):
        row = y if desc & 0x20 else h - 1 - y
        for x in range(w):
            i = (row * w + x) * step
            b, g, r = px[i:i + 3]
            out[(y * w + x) * 4:(y * w + x) * 4 + 4] = bytes((r, g, b, px[i + 3] if step == 4 else 255))
    return w, h, bytes(out)


def _wua_file(path, want):
    from compression import zstd
    with open(path, "rb") as f:
        f.seek(0, 2)
        total = f.tell()
        footer = _read(f, total - 144, 144)
        if struct.unpack_from(">I", footer, 140)[0] != 0x169F52D6:
            return None
        sec = [struct.unpack_from(">QQ", footer, i * 16) for i in range(6)]
        records = _read(f, sec[1][0], sec[1][1])
        names = _read(f, sec[2][0], sec[2][1])
        tree = _read(f, sec[3][0], sec[3][1])

        def node(i):
            flag, a, b, c = struct.unpack_from(">IIII", tree, i * 16)
            o = flag & 0x7FFFFFFF
            if o == 0x7FFFFFFF:
                return "", bool(flag >> 31), a, b, c
            n = names[o] & 0x7F
            if names[o] & 0x80:
                n |= names[o + 1] << 7
                o += 1
            return names[o + 1:o + 1 + n].decode("utf-8", "replace").lower(), bool(flag >> 31), a, b, c

        def children(i):
            _, is_file, start, count, _ = node(i)
            return [] if is_file else [(j,) + node(j) for j in range(start, start + count)]

        titles = sorted(children(0), key=lambda e: not e[1].startswith("00050000"))
        for title in titles:
            cur = title[0]
            for part in want:
                cur = next((c[0] for c in children(cur) if c[1] == part), None)
                if cur is None:
                    break
            if cur is None:
                continue
            _, is_file, lo, size_lo, hi = node(cur)
            if not is_file:
                continue
            offset, size = lo | (hi & 0xFFFF) << 32, size_lo | (hi >> 16) << 32
            out = bytearray()
            block = offset // 65536
            while len(out) < offset % 65536 + size:
                rec = records[block // 16 * 40:block // 16 * 40 + 40]
                pos = struct.unpack_from(">Q", rec, 0)[0]
                sizes = struct.unpack_from(">16H", rec, 8)
                pos += sum(sizes[:block % 16]) + block % 16
                raw = _read(f, sec[0][0] + pos, sizes[block % 16] + 1)
                try:
                    out += raw if len(raw) == 65536 else zstd.decompress(raw)
                except zstd.ZstdError:
                    return None
                block += 1
            return bytes(out[offset % 65536:offset % 65536 + size])
    return None


def icon_wiiu(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".wua":
        data = _wua_file(path, ("meta", "icontex.tga"))
    elif ext == ".rpx":
        with open(os.path.join(os.path.dirname(path), "..", "meta", "iconTex.tga"), "rb") as f:
            data = f.read()
    else:
        return None
    return _tga(data) if data and len(data) > 18 else None


EXTRACTORS = {
    "3ds": icon_3ds,
    "nds": icon_nds,
    "psp": icon_psp,
    "wiiu": icon_wiiu,
}


def write_png(path, w, h, rgba):
    raw = b"".join(b"\0" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(png)
    os.replace(tmp, path)


def extract(system, rom, dest):
    fn = EXTRACTORS.get(system)
    if fn is None or os.path.isdir(rom):
        return False
    try:
        icon = fn(rom)
    except (OSError, struct.error, ValueError, IndexError, ImportError):
        icon = None
    if icon is None:
        return False
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if isinstance(icon, bytes):
        with open(dest + ".tmp", "wb") as f:
            f.write(icon)
        os.replace(dest + ".tmp", dest)
    else:
        write_png(dest, *icon)
    return True
