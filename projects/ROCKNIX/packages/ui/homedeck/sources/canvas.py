# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present ROCKNIX (https://github.com/ROCKNIX)

import ctypes

import sdl2 as sdl

FONTS = {
    True: "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    False: "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
}

_fonts = {}


def font(size, bold=True):
    key = (max(10, int(size)), bold)
    if key not in _fonts:
        _fonts[key] = sdl.TTF_OpenFont(FONTS[bold].encode("utf-8"), key[0])
    return _fonts[key]


def _corner(radius, quadrant, inverse):
    px = bytearray(radius * radius * 4)
    for y in range(radius):
        for x in range(radius):
            cx = radius - x - 0.5 if quadrant in (0, 2) else x + 0.5
            cy = radius - y - 0.5 if quadrant in (0, 1) else y + 0.5
            d = (cx * cx + cy * cy) ** 0.5
            a = max(0.0, min(1.0, radius - d + 0.5))
            if inverse:
                a = 1.0 - a
            o = (y * radius + x) * 4
            px[o:o + 4] = bytes((255, 255, 255, int(a * 255)))
    return bytes(px)


def _rgba(surf):
    conv = sdl.ConvertSurfaceFormat(surf, sdl.PIXELFORMAT_RGBA32, 0)
    if not conv:
        return None
    s = conv.contents
    data = ctypes.string_at(s.pixels, s.pitch * s.h)
    w, h, pitch = s.w, s.h, s.pitch
    sdl.FreeSurface(conv)
    return data, w, h, pitch


def average_color(surf):
    px = _rgba(surf)
    if not px:
        return None
    data, w, h, pitch = px
    step = max(1, min(w, h) // 24)
    total = [0.0, 0.0, 0.0]
    weight = 0.0
    for y in range(0, h, step):
        row = y * pitch
        for x in range(0, w, step):
            o = row + x * 4
            r, g, b, a = data[o], data[o + 1], data[o + 2], data[o + 3]
            if a < 128:
                continue
            k = max(r, g, b) - min(r, g, b) + 16
            total[0] += r * k
            total[1] += g * k
            total[2] += b * k
            weight += k
    if not weight:
        return None
    return tuple(int(c / weight) for c in total)


def _box(px, start, stride, n, r):
    vals = px[start:start + stride * n:stride]
    acc = [0]
    for v in vals:
        acc.append(acc[-1] + v)
    for i in range(n):
        lo, hi = max(0, i - r), min(n, i + r + 1)
        px[start + i * stride] = (acc[hi] - acc[lo]) // (hi - lo)


def blurred(surf, w=96, h=54, radius=3, passes=2):
    conv = sdl.ConvertSurfaceFormat(surf, sdl.PIXELFORMAT_RGBA32, 0)
    if not conv:
        return None
    small = sdl.CreateRGBSurfaceWithFormat(0, w, h, 32, sdl.PIXELFORMAT_RGBA32)
    if not small:
        sdl.FreeSurface(conv)
        return None
    sw, sh = conv.contents.w, conv.contents.h
    if sw * h > sh * w:
        cw = sh * w // h
        src = sdl.Rect((sw - cw) // 2, 0, cw, sh)
    else:
        ch = sw * h // w
        src = sdl.Rect(0, (sh - ch) // 2, sw, ch)
    sdl.SetSurfaceBlendMode(conv, sdl.BLENDMODE_NONE)
    sdl.BlitScaled(conv, ctypes.byref(src), small, None)
    sdl.FreeSurface(conv)
    s = small.contents
    px = bytearray(ctypes.string_at(s.pixels, s.pitch * h))
    for _ in range(passes):
        for y in range(h):
            for c in range(4):
                _box(px, y * s.pitch + c, 4, w, radius)
        for x in range(w):
            for c in range(4):
                _box(px, x * 4 + c, s.pitch, h, radius)
    ctypes.memmove(s.pixels, bytes(px), len(px))
    return small


class Canvas:
    def __init__(self, title, flags, w=0, h=0):
        self.window = sdl.CreateWindow(title.encode("utf-8"), sdl.WINDOWPOS_UNDEFINED, sdl.WINDOWPOS_UNDEFINED,
                                       w, h, flags | sdl.WINDOW_BORDERLESS)
        if not self.window:
            raise RuntimeError("SDL_CreateWindow: " + sdl.error())
        self.id = sdl.GetWindowID(self.window)
        self.renderer = sdl.CreateRenderer(self.window, -1, sdl.RENDERER_ACCELERATED | sdl.RENDERER_PRESENTVSYNC)
        if not self.renderer:
            raise RuntimeError("SDL_CreateRenderer: " + sdl.error())
        sdl.SetRenderDrawBlendMode(self.renderer, sdl.BLENDMODE_BLEND)
        self.size = (0, 0)
        self.dirty = True
        self.textures = {}
        self.wanted = set()
        self.text_cache = {}
        self.wrap_cache = {}
        self.corner_r = 64
        self.corner_tex = []
        for inverse in (False, True):
            for q in range(4):
                tex = sdl.CreateTexture(self.renderer, sdl.PIXELFORMAT_RGBA32, sdl.TEXTUREACCESS_STATIC,
                                        self.corner_r, self.corner_r)
                sdl.UpdateTexture(tex, None, _corner(self.corner_r, q, inverse), self.corner_r * 4)
                sdl.SetTextureBlendMode(tex, sdl.BLENDMODE_BLEND)
                sdl.SetTextureScaleMode(tex, sdl.SCALEMODE_LINEAR)
                self.corner_tex.append(tex)
        self.update_size()

    def update_size(self):
        w, h = ctypes.c_int(0), ctypes.c_int(0)
        sdl.GetWindowSize(self.window, ctypes.byref(w), ctypes.byref(h))
        if (w.value, h.value) != self.size:
            self.size = (w.value, h.value)
            self.dirty = True

    def show(self):
        sdl.ShowWindow(self.window)
        self.dirty = True

    def hide(self):
        sdl.HideWindow(self.window)

    def adopt(self, key, surf):
        self.wanted.discard(key)
        tex = sdl.CreateTextureFromSurface(self.renderer, surf)
        if tex:
            sdl.SetTextureScaleMode(tex, sdl.SCALEMODE_LINEAR)
            self.textures[key] = tex

    def forget(self, key):
        tex = self.textures.pop(key, None)
        if tex:
            sdl.DestroyTexture(tex)

    def present(self):
        sdl.RenderPresent(self.renderer)
        self.dirty = False

    def clear(self, c):
        self.color(c)
        sdl.RenderClear(self.renderer)

    def color(self, c, a=255):
        sdl.SetRenderDrawColor(self.renderer, c[0], c[1], c[2], a)

    def fill(self, x, y, w, h, c, a=255):
        self.color(c, a)
        sdl.RenderFillRect(self.renderer, ctypes.byref(sdl.Rect(int(x), int(y), int(w), int(h))))

    def clip(self, rect):
        sdl.RenderSetClipRect(self.renderer, ctypes.byref(sdl.Rect(*map(int, rect))) if rect else None)

    def corners(self, x, y, w, h, r, c, a=255, inverse=False):
        r = int(min(r, w / 2, h / 2))
        x, y, w, h = int(x), int(y), int(w), int(h)
        spots = ((x, y), (x + w - r, y), (x, y + h - r), (x + w - r, y + h - r))
        for q, (cx, cy) in enumerate(spots):
            tex = self.corner_tex[q + (4 if inverse else 0)]
            sdl.SetTextureColorMod(tex, *c)
            sdl.SetTextureAlphaMod(tex, a)
            sdl.RenderCopy(self.renderer, tex, None, ctypes.byref(sdl.Rect(cx, cy, r, r)))
        return x, y, w, h, r

    def round(self, x, y, w, h, r, c, a=255):
        x, y, w, h, r = self.corners(x, y, w, h, r, c, a)
        self.fill(x + r, y, w - 2 * r, h, c, a)
        self.fill(x, y + r, r, h - 2 * r, c, a)
        self.fill(x + w - r, y + r, r, h - 2 * r, c, a)

    def fade(self, x, y, w, h, c, a0, a1, steps=16):
        for i in range(steps):
            y0 = int(y + h * i / steps)
            y1 = int(y + h * (i + 1) / steps)
            self.fill(x, y0, w, y1 - y0, c, int(a0 + (a1 - a0) * (i + 0.5) / steps))

    def measure(self, text, size, bold=True):
        w = ctypes.c_int(0)
        f = font(size, bold)
        if f:
            sdl.TTF_SizeUTF8(f, text.encode("utf-8"), ctypes.byref(w), None)
        return w.value

    def text(self, text, size, color, x, y, max_w=None, align="left", bold=True, a=255):
        if not text:
            return 0
        key = (text, int(size), color, max_w, bold)
        if key not in self.text_cache:
            f = font(size, bold)
            if not f:
                return 0
            s = text
            if max_w:
                while len(s) > 1 and self.measure(s, size, bold) > max_w:
                    s = s[:-2].rstrip() + "…"
            surf = sdl.TTF_RenderUTF8_Blended(f, s.encode("utf-8"), sdl.Color(*color, 255))
            if not surf:
                return 0
            tex = sdl.CreateTextureFromSurface(self.renderer, surf)
            dims = (surf.contents.w, surf.contents.h)
            sdl.FreeSurface(surf)
            if len(self.text_cache) > 400:
                for t, _, _ in self.text_cache.values():
                    sdl.DestroyTexture(t)
                self.text_cache.clear()
            self.text_cache[key] = (tex, dims[0], dims[1])
        tex, w, h = self.text_cache[key]
        if align == "center":
            x -= w / 2
        elif align == "right":
            x -= w
        sdl.SetTextureAlphaMod(tex, a)
        sdl.RenderCopy(self.renderer, tex, None, ctypes.byref(sdl.Rect(int(x), int(y - h / 2), w, h)))
        return w

    def wrap(self, text, size, max_w, max_lines, bold=True):
        key = (text, int(size), int(max_w), max_lines, bold)
        if key in self.wrap_cache:
            return self.wrap_cache[key]
        lines, cur = [], ""
        for word in text.split():
            cand = (cur + " " + word).strip()
            if cur and self.measure(cand, size, bold) > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = cand
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1].rstrip(".,;: ") + "…"
        if len(self.wrap_cache) > 200:
            self.wrap_cache.clear()
        self.wrap_cache[key] = lines
        return lines

    def tex_size(self, tex):
        tw, th = ctypes.c_int(0), ctypes.c_int(0)
        sdl.QueryTexture(tex, None, None, ctypes.byref(tw), ctypes.byref(th))
        return tw.value, th.value

    def blit(self, tex, x, y, w, h, cover):
        tw, th = self.tex_size(tex)
        if not tw or not th:
            return None
        if cover:
            if tw * h > th * w:
                cw = max(1, int(th * w / h))
                src = sdl.Rect((tw - cw) // 2, 0, cw, th)
            else:
                ch = max(1, int(tw * h / w))
                src = sdl.Rect(0, (th - ch) // 2 if th * w < tw * h * 1.2 else 0, tw, ch)
            sdl.RenderCopy(self.renderer, tex, ctypes.byref(src), ctypes.byref(sdl.Rect(int(x), int(y), int(w), int(h))))
            return x, y, w, h
        scale = min(w / tw, h / th)
        dw, dh = tw * scale, th * scale
        dst = sdl.Rect(int(x + (w - dw) / 2), int(y + (h - dh) / 2), int(dw), int(dh))
        sdl.RenderCopy(self.renderer, tex, None, ctypes.byref(dst))
        return dst.x, dst.y, dst.w, dst.h
