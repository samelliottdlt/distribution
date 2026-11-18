# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present ROCKNIX (https://github.com/ROCKNIX)

import argparse
import ctypes
import glob
import json
import math
import os
import queue
import re
import signal
import sys
import threading
import time

import esapi
import icons
import pad
import sdl2 as sdl
import sound
from canvas import Canvas, average_color, blurred

CACHE = "/storage/.cache/homedeck"
SYSTEM_CFG = "/storage/.config/system/configs/system.cfg"
LEVELS = ("audio.volume", "display.brightness")
OSD_HOLD, OSD_FADE = 1.4, 0.3

BG = (236, 237, 241)
STRIPE = (228, 229, 234)
BAR = (250, 250, 252)
INK = (60, 64, 72)
MUTED = (140, 144, 152)
ACCENT = (0, 160, 240)
TILE = (255, 255, 255)
EDGE = (205, 208, 214)
WHITE = (255, 255, 255)
RING = (232, 234, 239)
MEDIA = ("thumbnail", "titleshot", "image", "boxart", "mix", "marquee")

FRAMES = {
    "nes": ((190, 40, 45), "NES"), "famicom": ((190, 40, 45), "FC"), "fds": ((190, 40, 45), "FDS"),
    "snes": ((95, 80, 160), "SNES"), "sfc": ((95, 80, 160), "SFC"),
    "gb": ((120, 128, 48), "GB"), "gbc": ((60, 150, 190), "GBC"), "gba": ((80, 60, 170), "GBA"),
    "n64": ((35, 120, 70), "N64"), "gamecube": ((100, 80, 165), "GC"),
    "wii": ((140, 150, 160), "Wii"), "wiiu": ((40, 150, 200), "Wii U"), "switch": ((225, 50, 50), "Switch"),
    "3ds": ((200, 40, 50), "3DS"), "nds": ((120, 125, 135), "DS"),
    "psx": ((105, 105, 115), "PS"), "ps2": ((30, 50, 120), "PS2"), "ps3": ((25, 25, 30), "PS3"),
    "psp": ((45, 45, 50), "PSP"), "psvita": ((30, 60, 140), "Vita"),
    "dreamcast": ((235, 120, 30), "DC"), "saturn": ((60, 60, 70), "SAT"),
    "genesis": ((30, 30, 30), "MD"), "megadrive": ((30, 30, 30), "MD"),
    "mastersystem": ((40, 40, 45), "SMS"), "gamegear": ((40, 40, 45), "GG"),
    "pcengine": ((220, 120, 40), "PCE"), "tg16": ((40, 40, 45), "TG16"),
    "arcade": ((210, 150, 30), "Arcade"), "mame": ((210, 150, 30), "MAME"), "fbneo": ((210, 150, 30), "FBNeo"),
    "neogeo": ((180, 150, 40), "NEOGEO"), "atari2600": ((150, 90, 50), "2600"),
    "pico-8": ((255, 0, 77), "PICO-8"), "steam": ((25, 60, 100), "Steam"), "ports": ((70, 130, 110), "Ports"),
    "scummvm": ((70, 140, 60), "Scumm"), "dos": ((50, 50, 60), "DOS"),
    "music": ((240, 90, 130), "Music"), "tools": ((110, 115, 125), "Tools"),
}
PALETTE = ((0, 150, 200), (230, 120, 30), (60, 160, 90), (150, 80, 180), (210, 60, 80), (90, 110, 130))

ZOOMS = (1, 2, 3, 4, 5)
PINNED = ("3ds", "nds")
DRAG_SLOP = 14
# Same as wlroots' touch mapping; sway's "90" is wl_output transform 270.
TOUCH_TRANSFORMS = {
    "normal": lambda x, y: (x, y),
    "90": lambda x, y: (y, 1 - x),
    "180": lambda x, y: (1 - x, 1 - y),
    "270": lambda x, y: (1 - y, x),
    "flipped": lambda x, y: (1 - x, y),
    "flipped-90": lambda x, y: (y, x),
    "flipped-180": lambda x, y: (x, 1 - y),
    "flipped-270": lambda x, y: (1 - y, 1 - x),
}
DIM_ALPHA = 190
BOTTOM_WIN = '[app_id="homedeck" title="^homedeck$"]'
TOP_WIN = '[app_id="homedeck" title="^homedeck-top$"]'
WINDOWEVENT_REDRAW = (1, 3, 5, 6)

_TAGS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_ARTICLE = re.compile(r"^(.*?), (The|A|An)\b(.*)$")


def frame_for(system):
    if system in FRAMES:
        return FRAMES[system]
    return PALETTE[sum(map(ord, system)) % len(PALETTE)], system.upper()[:6]


def pastel(color, amount=0.7):
    return tuple(int(c * (1 - amount) + 255 * amount) for c in color)


def title_of(game):
    if "_title" not in game:
        name = game.get("name") or os.path.splitext(os.path.basename(game.get("path", "")))[0]
        clean = _TAGS.sub("", name).strip() or name
        m = _ARTICLE.match(clean)
        if m:
            clean = "%s %s%s" % (m.group(2), m.group(1), m.group(3))
        game["_title"] = clean
    return game["_title"]


def es_date(value):
    try:
        return time.strptime((value or "")[:8], "%Y%m%d")
    except ValueError:
        return None


def es_time(value):
    try:
        return time.mktime(time.strptime((value or "")[:15], "%Y%m%dT%H%M%S"))
    except (ValueError, OverflowError):
        return None


def ago(ts):
    d = time.time() - ts
    if d < 3600:
        return "%dm ago" % max(1, d // 60)
    if d < 86400:
        return "%dh ago" % (d // 3600)
    if d < 172800:
        return "Yesterday"
    if d < 86400 * 7:
        return "%d days ago" % (d // 86400)
    return time.strftime("%-d %b %Y", time.localtime(ts))


def playtime(seconds):
    if seconds < 60:
        return "Under a minute"
    if seconds < 3600:
        n, unit = seconds // 60, "minute"
    else:
        n, unit = seconds // 3600, "hour"
    return "%d %s%s" % (n, unit, "" if n == 1 else "s")


def battery():
    for d in glob.glob("/sys/class/power_supply/*"):
        try:
            with open(d + "/type") as f:
                if f.read().strip() != "Battery":
                    continue
            with open(d + "/capacity") as f:
                cap = int(f.read().strip())
            with open(d + "/status") as f:
                charging = f.read().strip() == "Charging"
            return cap, charging
        except (OSError, ValueError):
            continue
    return None, False


class App:
    def __init__(self, args):
        self.args = args
        self.only = [s for s in args.systems.split(",") if s]
        self.exclude = [s for s in args.exclude.split(",") if s]
        self.inbox = queue.Queue()
        self.loads = queue.LifoQueue()
        self.pending = set()
        self.failed = set()
        self.canvases = ()
        self.ready = False
        self.systems = {}
        self.root = []
        self.games = {}
        self.tints = {}
        self.resume = None
        self.layout_key = None
        self.slots, self.cells, self.cols = [], {}, 0
        self.folder = None
        self.cursor = {}
        self.scrolls = {}
        self.scroll = 0.0
        self.velocity = 0.0
        self.zoom = {"root": 2, "games": 3}
        self.touch = None
        self.touch_matrix = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
        self.touch_transform = "normal"
        self.mode = "home"
        self.playing = False
        self.launch_deadline = 0.0
        self.visible = False
        self.has_selection_api = True
        self.last_input = time.monotonic()
        self.screen_on = True
        self.dim = 0.0
        self.dim_target = 0.0
        self.burst_until = 0.0
        self.status = ("", "", None, False)
        self.status_at = 0.0
        self.levels = None
        self.cfg_mtime = None
        self.osd = None
        self.running = True
        self.scraper = None
        self.clock12 = self._es_bool("ClockMode12")
        self._load_state()

    def _es_bool(self, name):
        try:
            with open("/storage/.config/emulationstation/es_settings.cfg") as f:
                return '<bool name="%s" value="true"' % name in f.read()
        except OSError:
            return False

    def _load_state(self):
        try:
            with open(os.path.join(CACHE, "state.json")) as f:
                state = json.load(f)
        except (OSError, ValueError):
            return
        zoom = state.get("zoom")
        if isinstance(zoom, int):
            zoom = {"games": zoom}
        if isinstance(zoom, dict):
            self.zoom.update({k: v for k, v in zoom.items() if k in self.zoom and v in ZOOMS})
        if isinstance(state.get("cursor"), dict):
            self.cursor = state["cursor"]
        if isinstance(state.get("folder"), str):
            self.folder = state["folder"]

    def _save_state(self):
        try:
            os.makedirs(CACHE, exist_ok=True)
            with open(os.path.join(CACHE, "state.json"), "w") as f:
                json.dump({"zoom": self.zoom, "cursor": self.cursor, "folder": self.folder}, f)
        except OSError:
            pass

    # ---------- setup ----------

    def run(self):
        os.environ["SDL_VIDEO_WAYLAND_WMCLASS"] = "homedeck"
        os.environ["SDL_APP_ID"] = "homedeck"
        sdl.SetHint(b"SDL_TOUCH_MOUSE_EVENTS", b"0")
        sdl.SetHint(b"SDL_RENDER_SCALE_QUALITY", b"1")
        if sdl.Init(sdl.INIT_VIDEO | sdl.INIT_EVENTS) != 0:
            print("SDL_Init: " + sdl.error(), file=sys.stderr)
            return 1
        sdl.IMG_Init(sdl.IMG_INIT_PNG | sdl.IMG_INIT_JPG)
        sdl.TTF_Init()
        self.sounds = sound.Sounds(self.args.sounds, self.args.volume)
        try:
            self.bottom = Canvas("homedeck", sdl.WINDOW_FULLSCREEN_DESKTOP)
            self.top = Canvas("homedeck-top", sdl.WINDOW_RESIZABLE, 1920, 1080)
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 1
        self.canvases = (self.bottom, self.top)
        self.visible = True

        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))

        if self.args.scrape:
            import scraper
            self.scraper = scraper.Scraper(CACHE, lambda s, g: self.inbox.put(("art", s, g)))
        touch_name = self.args.touch.split(":", 2)[-1] if self.args.touch else None
        self.pad = pad.Pad(lambda action: self._post("pad", action), touch_name,
                           lambda *touch: self._post("touch", *touch))
        for target in (self._poll, self._reconcile_loop, self._fetch_systems, self._loader, self._loader,
                       self.pad.run):
            threading.Thread(target=target, daemon=True).start()
        self._place()

        ev = sdl.Event()
        while self.running:
            if sdl.WaitEventTimeout(ctypes.byref(ev), 16 if self._animating() else 100):
                self._event(ev)
                while sdl.PollEvent(ctypes.byref(ev)):
                    self._event(ev)
            self._drain()
            self._animate()
            self._tick()
            self.sounds.bgm(self.visible and self.screen_on)
            if not self.visible:
                continue
            if time.monotonic() < self.burst_until:
                self._dirty()
            for cv in self.canvases:
                cv.update_size()
            if self.bottom.dirty and self.screen_on:
                self._draw_bottom()
            if self.top.dirty:
                self._draw_top()
        self._save_state()
        sdl.Quit()
        return 0

    def _post(self, *msg):
        self.inbox.put(msg)
        ev = sdl.Event()
        ctypes.c_uint32.from_buffer(ev.data).value = sdl.EVENT_USEREVENT
        sdl.PushEvent(ctypes.byref(ev))

    def _dirty(self, top=True):
        self.bottom.dirty = True
        if top:
            self.top.dirty = True

    def _burst(self, seconds=3.0):
        self.burst_until = time.monotonic() + seconds

    # ---------- sway ----------

    def _place(self):
        def work():
            esapi.sway("output %s power on" % self.args.output)
            if self.args.touch:
                esapi.sway('input "%s" events enabled' % self.args.touch)
            time.sleep(0.3)
            esapi.sway(BOTTOM_WIN + " move container to output %s, fullscreen enable" % self.args.output)
            esapi.sway(self._top_placement() + ", focus")
        threading.Thread(target=work, daemon=True).start()

    def _top_placement(self):
        return (TOP_WIN + " floating enable, border none, move container to output %s, "
                "resize set width 100 ppt height 100 ppt, move position 0 0" % self.args.top)

    def _stock_screens(self):
        esapi.sway('[app_id="emulationstation"] focus')
        esapi.sway("output %s power off" % self.args.output)
        if self.args.touch:
            esapi.sway('input "%s" events disabled' % self.args.touch)

    def _reconcile_loop(self):
        while self.running:
            time.sleep(2.0)
            if self.visible and not self.playing and self._reconcile():
                self._post("burst")

    def _reconcile(self):
        tree = esapi.sway_tree()
        if not tree:
            return False
        outputs, wins, focus = {}, {}, []

        def walk(node, output):
            if node.get("type") == "output":
                output = node.get("name")
                outputs[output] = node.get("rect", {})
            if node.get("app_id") == "homedeck":
                wins[node.get("name")] = (node, output)
            if node.get("focused"):
                focus.append(node)
            for child in node.get("nodes", []) + node.get("floating_nodes", []):
                walk(child, output)

        walk(tree, None)
        fixes = []
        if "homedeck-top" in wins:
            node, output = wins["homedeck-top"]
            rect, want = node.get("rect", {}), outputs.get(self.args.top, {})
            placed = all(abs(rect.get(k, 0) - want.get(k, 0)) <= 2 for k in ("x", "y", "width", "height"))
            if output != self.args.top or node.get("type") != "floating_con" or not placed:
                fixes.append(self._top_placement())
        if "homedeck" in wins:
            node, output = wins["homedeck"]
            if output != self.args.output or node.get("fullscreen_mode") != 1:
                fixes.append(BOTTOM_WIN + " move container to output %s, fullscreen enable" % self.args.output)
        if not focus or focus[0].get("app_id") != "homedeck":
            fixes.append(TOP_WIN + " focus")
        if self.screen_on:
            out = esapi.sway_output(self.args.output)
            if out:
                self.touch_transform = out.get("transform", "normal")
                if not out.get("power"):
                    fixes.append("output %s power on" % self.args.output)
            for dev in esapi.sway_inputs() if self.args.touch else []:
                if dev.get("identifier") != self.args.touch:
                    continue
                libinput = dev.get("libinput", {})
                matrix = libinput.get("calibration_matrix")
                if matrix and len(matrix) == 6:
                    self.touch_matrix = tuple(matrix)
                if libinput.get("send_events") == "disabled":
                    fixes.append('input "%s" events enabled' % self.args.touch)
        if not self.visible or self.playing:
            return False
        for cmd in fixes:
            esapi.sway(cmd)
        return bool(fixes)

    # ---------- modes ----------

    def _apply_visibility(self):
        want = self.mode == "home" and not self.playing
        if want == self.visible:
            return
        self.visible = want
        if want:
            for cv in self.canvases:
                cv.show()
            self.last_input = time.monotonic()
            self.screen_on = True
            self.dim = self.dim_target = 0.0
            self._burst(4.0)
            self._place()
        else:
            self.touch = None
            for cv in self.canvases:
                cv.hide()

    def _set_mode(self, mode):
        if mode == self.mode:
            return
        self.mode = mode
        if mode == "es":
            item = self._selected_item()
            threading.Thread(target=self._to_es, args=(self.folder, item), daemon=True).start()
        elif self.has_selection_api:
            threading.Thread(target=self._fetch_selection, daemon=True).start()
        self._apply_visibility()
        self._save_state()

    def _to_es(self, folder, item):
        self._stock_screens()
        if not self.has_selection_api or not item:
            return
        if item.get("kind") == "folder":
            esapi.select(item["system"])
        elif item.get("kind") == "resume":
            esapi.select(item["game"]["_system"], item["game"]["id"], open_list=True)
        else:
            esapi.select(folder, item["id"], open_list=True)

    def _fetch_selection(self):
        status, data = esapi.selection()
        if status == 404:
            self.has_selection_api = False
        elif data:
            self._post("selection", data)

    def _from_es(self, data):
        system, game = data.get("system"), data.get("game") or {}
        if system not in self.systems:
            return
        if data.get("view") == "gamelist" and game.get("id"):
            self._enter(system, quiet=True)
            self.cursor[system] = game["id"]
        else:
            self._leave(quiet=True)
            self.cursor[""] = system
        self._ensure_visible()
        self._dirty()

    def _launch(self, game):
        self.sounds.play("launch")
        self.playing = True
        self.launch_deadline = time.monotonic() + 15.0
        self._apply_visibility()

        def work():
            self._stock_screens()
            esapi.launch(game["path"])
        threading.Thread(target=work, daemon=True).start()

    def _set_running(self, running):
        if running:
            self.launch_deadline = 0.0
            if not self.playing:
                self.playing = True
                self._apply_visibility()
        elif self.playing and time.monotonic() > self.launch_deadline:
            self.playing = False
            self._apply_visibility()
            threading.Thread(target=self._fetch_all, daemon=True).start()

    # ---------- background work ----------

    def _fetch_systems(self):
        while self.running:
            systems = esapi.systems()
            if systems:
                self.inbox.put(("systems", systems))
                self._fetch_all(systems)
                return
            time.sleep(2)

    def _fetch_all(self, systems=None):
        for s in systems or list(self.systems.values()):
            self.inbox.put(("games", s["name"], esapi.games(s["name"])))

    def _poll(self):
        while self.running:
            self.inbox.put(("running", esapi.running_game()))
            time.sleep(1.0)

    def _loader(self):
        while self.running:
            key, src = self.loads.get()
            surf, tint = None, None
            try:
                surf = self._load_surface(src)
                if surf and src[0] in ("icon", "logo"):
                    tint = average_color(surf)
            except Exception as e:
                print("load %s: %s" % (key, e), file=sys.stderr)
            self._post("image", key, surf, tint)

    def _load_surface(self, src):
        kind = src[0]
        if kind == "icon":
            _, system, game = src
            path = os.path.join(CACHE, "icons", system, game["id"] + ".png")
            if not os.path.exists(path) and not icons.extract(system, game["path"], path):
                return None
            surf = sdl.IMG_Load(path.encode("utf-8"))
            return surf if surf else None
        _, urls = src
        for i, url in enumerate(urls):
            data = esapi.get_bytes(url)
            if not data:
                continue
            surf = sdl.IMG_Load_RW(sdl.RWFromConstMem(data, len(data)), 1)
            if not surf:
                continue
            if kind == "blur":
                small = blurred(surf)
                sdl.FreeSurface(surf)
                return small
            ratio = surf.contents.w / max(1, surf.contents.h)
            if kind != "art" or 0.7 < ratio < 1.45 or i == len(urls) - 1:
                return surf
            sdl.FreeSurface(surf)
        return None

    def _drain(self):
        while True:
            try:
                msg = self.inbox.get_nowait()
            except queue.Empty:
                return
            kind = msg[0]
            if kind == "systems":
                systems = [s for s in msg[1] if (not self.only or s["name"] in self.only)
                           and s["name"] not in self.exclude]
                systems.sort(key=lambda s: PINNED.index(s["name"]) if s["name"] in PINNED else len(PINNED))
                self.systems = {s["name"]: s for s in systems}
                self.root = [{"kind": "folder", "id": s["name"], "system": s["name"]} for s in systems]
                self.resume = None
                if self.folder not in self.systems:
                    self.folder = None
                self.ready = True
                self._ensure_visible()
                self._dirty()
            elif kind == "games":
                self._games(msg[1], msg[2])
            elif kind == "image":
                _, key, surf, tint = msg
                self.pending.discard(key)
                if tint:
                    self.tints[key] = tint
                if surf:
                    for cv in self.canvases:
                        if key in cv.wanted:
                            cv.adopt(key, surf)
                            cv.dirty = True
                    sdl.FreeSurface(surf)
                else:
                    self.failed.add(key)
            elif kind == "art":
                _, system, game = msg
                for g in self.games.get(system, []):
                    if g["id"] == game["id"]:
                        g.update({k: v for k, v in game.items() if not k.startswith("_")})
                self._forget(system, game["id"])
                self._dirty()
            elif kind == "running":
                self._set_running(msg[1])
            elif kind == "pad":
                self._pad(msg[1])
            elif kind == "touch":
                _, phase, nx, ny = msg
                m = self.touch_matrix
                x, y = TOUCH_TRANSFORMS.get(self.touch_transform, TOUCH_TRANSFORMS["normal"])(
                    m[0] * nx + m[1] * ny + m[2], m[3] * nx + m[4] * ny + m[5])
                {"down": self._down, "move": self._move, "up": self._up}[phase](
                    x * self.bottom.size[0], y * self.bottom.size[1])
            elif kind == "selection":
                self._from_es(msg[1])
            elif kind == "burst":
                self._burst()

    def _games(self, system, games):
        old = {g["id"]: g for g in self.games.get(system, [])}
        for g in games:
            g["_system"] = system
            prev = old.get(g["id"])
            if prev and any(prev.get(k) != g.get(k) for k in MEDIA):
                self._forget(system, g["id"])
        self.games[system] = games
        self.resume = None
        if self.scraper:
            self.scraper.enqueue(system, games)
        self._ensure_visible()
        self._dirty()

    def _forget(self, system, game_id):
        for kind in ("art", "big", "shot", "blur", "logo"):
            key = "%s:%s:%s" % (kind, system, game_id)
            for cv in self.canvases:
                cv.forget(key)
            self.failed.discard(key)

    def _tex(self, cv, key, src):
        tex = cv.textures.get(key)
        if tex:
            return tex
        cv.wanted.add(key)
        if key not in self.pending and key not in self.failed:
            self.pending.add(key)
            self.loads.put((key, src))
        return None

    # ---------- model ----------

    def _view(self):
        return self.folder or ""

    def _items(self):
        if self.folder is not None:
            return self.games.get(self.folder, [])
        if self.resume is None:
            best = None
            for system in self.systems:
                for g in self.games.get(system, []):
                    if es_time(g.get("lastplayed")) and (best is None or g["lastplayed"] > best["lastplayed"]):
                        best = g
            self.resume = [{"kind": "resume", "id": "@resume", "game": best}] if best else []
        return self.resume + self.root

    def _layout(self):
        items = self._items()
        rows = self._rows()
        key = (self._view(), len(items), rows, items[0]["id"] if items else None)
        if key == self.layout_key:
            return self.slots
        slots, cells, taken = [], {}, set()
        col = row = 0
        for i, item in enumerate(items):
            span = 2 if item.get("kind") == "resume" else 1
            while (col, row) in taken or (col + span - 1, row) in taken:
                row += 1
                if row >= rows:
                    row, col = 0, col + 1
            slots.append((col, row, span))
            for k in range(span):
                taken.add((col + k, row))
                cells[(col + k, row)] = i
            row += 1
            if row >= rows:
                row, col = 0, col + 1
        self.layout_key = key
        self.slots, self.cells = slots, cells
        self.cols = max((c + s for c, _, s in slots), default=0)
        return slots

    def _neighbour(self, i, dc, dr):
        col, row, span = self._layout()[i]
        if dr:
            return self.cells.get((col, row + dr), i)
        target = max(0, min(self.cols - 1, col + span - 1 + dc if dc > 0 else col + dc))
        for r in range(row, -1, -1):
            if (target, r) in self.cells:
                return self.cells[(target, r)]
        return i

    def _sel(self):
        items = self._items()
        key = self.cursor.get(self._view())
        for i, item in enumerate(items):
            if item["id"] == key:
                return i
        return 0 if items else -1

    def _selected_item(self):
        i = self._sel()
        return self._items()[i] if i >= 0 else None

    def _select(self, i):
        items = self._items()
        if not items:
            return
        i = max(0, min(len(items) - 1, i))
        if self.cursor.get(self._view()) != items[i]["id"]:
            self.sounds.play("move")
        self.cursor[self._view()] = items[i]["id"]
        self._ensure_visible()
        self._dirty()

    def _enter(self, system, quiet=False):
        if self.folder == system:
            return
        if not quiet:
            self.sounds.play("open")
        self.scrolls[self._view()] = self.scroll
        self.folder = system
        self.scroll = self.scrolls.get(system, 0.0)
        self.velocity = 0.0
        self._ensure_visible()
        self._dirty()

    def _leave(self, quiet=False):
        if self.folder is None:
            return
        if not quiet:
            self.sounds.play("back")
        self.scrolls[self.folder] = self.scroll
        self.cursor[""] = self.folder
        self.folder = None
        self.scroll = self.scrolls.get("", 0.0)
        self.velocity = 0.0
        self._ensure_visible()
        self._dirty()

    def _open(self, item):
        if item.get("kind") == "folder":
            self._enter(item["system"])
        elif item.get("kind") == "resume":
            self._launch(item["game"])
        else:
            self._launch(item)

    def _activity(self):
        self.last_input = time.monotonic()
        if not self.screen_on:
            self._place()
            self.screen_on = True
            self._burst()
        if self.dim_target:
            self.dim_target = 0.0

    def _rows(self):
        return self.zoom["root" if self.folder is None else "games"]

    def _set_zoom(self, step):
        key = "root" if self.folder is None else "games"
        cur = ZOOMS.index(self.zoom[key]) if self.zoom[key] in ZOOMS else 2
        self.zoom[key] = ZOOMS[max(0, min(len(ZOOMS) - 1, cur + step))]
        self._ensure_visible()
        self._save_state()
        self._dirty()

    # ---------- input ----------

    def _pad(self, action):
        if action == "home":
            if not self.playing:
                self._set_mode("es" if self.mode == "home" else "home")
            return
        if not self.visible:
            return
        asleep = not self.screen_on or self.dim_target
        self._activity()
        if asleep:
            return
        items = self._items()
        i = self._sel()
        if action in ("up", "down", "left", "right") and items:
            dc = {"left": -1, "right": 1}.get(action, 0)
            dr = {"up": -1, "down": 1}.get(action, 0)
            self._select(self._neighbour(i, dc, dr))
        elif action in ("l", "r") and items:
            _, _, _, _, _, _, _, cell, _ = self._metrics()
            cols = max(1, int(self.bottom.size[0] / cell) - 1)
            self._select(self._neighbour(i, cols * (1 if action == "r" else -1), 0))
        elif action == "a" and i >= 0:
            self._open(items[i])
        elif action == "b":
            self._leave()
        elif action == "x":
            self._set_zoom(-1)
        elif action == "y":
            self._set_zoom(1)
        elif action == "start":
            self._set_mode("es")

    def _event(self, ev):
        t = ev.type
        if t == sdl.EVENT_QUIT:
            self.running = False
        elif t == sdl.EVENT_WINDOWEVENT:
            we = ctypes.cast(ctypes.pointer(ev), ctypes.POINTER(sdl.WindowEvent)).contents
            for cv in self.canvases:
                if cv.id == we.windowID:
                    cv.dirty = True
            if we.event in WINDOWEVENT_REDRAW:
                self._burst()
        elif t in (sdl.EVENT_FINGERDOWN, sdl.EVENT_FINGERUP, sdl.EVENT_FINGERMOTION):
            # SDL2 tracks one Wayland seat and drops touch when another seat changes.
            if self.pad.has_touch:
                return
            tf = ctypes.cast(ctypes.pointer(ev), ctypes.POINTER(sdl.TouchFingerEvent)).contents
            if tf.windowID not in (0, self.bottom.id):
                return
            x, y = tf.x * self.bottom.size[0], tf.y * self.bottom.size[1]
            {sdl.EVENT_FINGERDOWN: self._down, sdl.EVENT_FINGERUP: self._up,
             sdl.EVENT_FINGERMOTION: self._move}[t](x, y)
        elif t in (sdl.EVENT_MOUSEBUTTONDOWN, sdl.EVENT_MOUSEBUTTONUP):
            mb = ctypes.cast(ctypes.pointer(ev), ctypes.POINTER(sdl.MouseButtonEvent)).contents
            if mb.windowID == self.bottom.id and mb.which != sdl.TOUCH_MOUSEID:
                (self._down if t == sdl.EVENT_MOUSEBUTTONDOWN else self._up)(mb.x, mb.y)
        elif t == sdl.EVENT_MOUSEMOTION:
            mm = ctypes.cast(ctypes.pointer(ev), ctypes.POINTER(sdl.MouseMotionEvent)).contents
            if mm.state and mm.windowID == self.bottom.id and mm.which != sdl.TOUCH_MOUSEID:
                self._move(mm.x, mm.y)

    def _down(self, x, y):
        if not self.visible:
            return
        asleep = not self.screen_on or self.dim_target
        self._activity()
        if asleep:
            self.touch = None
            return
        self.velocity = 0.0
        self.touch = {"x": x, "y": y, "scroll": self.scroll, "drag": False, "t": time.monotonic(), "lx": x}

    def _move(self, x, y):
        if not self.touch:
            return
        self.last_input = time.monotonic()
        if not self.touch["drag"] and abs(x - self.touch["x"]) > DRAG_SLOP:
            self.touch["drag"] = True
        if self.touch["drag"]:
            now = time.monotonic()
            dt = max(1e-3, now - self.touch["t"])
            self.velocity = 0.7 * self.velocity + 0.3 * (-(x - self.touch["lx"]) / dt)
            self.touch["t"], self.touch["lx"] = now, x
            self.scroll = max(0.0, min(self._max_scroll(), self.touch["scroll"] - (x - self.touch["x"])))
            self._dirty(top=False)

    def _up(self, x, y):
        touch, self.touch = self.touch, None
        if not touch:
            return
        if touch["drag"]:
            if time.monotonic() - touch["t"] > 0.08:
                self.velocity = 0.0
            return
        self.velocity = 0.0
        self._tap(x, y)

    def _tap(self, x, y):
        for name, (bx, by, bw, bh) in self._buttons().items():
            if bx <= x < bx + bw and by <= y < by + bh:
                self._button(name)
                return
        _, _, _, _, _, grid, _, _, _ = self._metrics()
        if not grid[0] <= y < grid[1]:
            return
        items = self._items()
        sel = self._sel()
        for i in range(len(items)):
            tx, ty, tw, th = self._tile_rect(i)
            if tx <= x < tx + tw and ty <= y < ty + th:
                if i == sel:
                    self._open(items[i])
                else:
                    self._select(i)
                return

    def _button(self, name):
        if name == "open":
            item = self._selected_item()
            if item:
                self._open(item)
        elif name == "back":
            self._leave()
        elif name == "classic":
            self._set_mode("es")
        elif name in ("zoom_in", "zoom_out"):
            self._set_zoom(-1 if name == "zoom_in" else 1)

    # ---------- animation / timers ----------

    def _animating(self):
        return (abs(self.velocity) > 5 and self.touch is None) or self.dim != self.dim_target or bool(self.osd)

    def _animate(self):
        if self.dim != self.dim_target:
            step = 1 / 40.0 if self.dim_target > self.dim else 1 / 8.0
            if self.dim_target > self.dim:
                self.dim = min(self.dim + step, self.dim_target)
            else:
                self.dim = max(self.dim - step, self.dim_target)
            self._dirty()
        if not (abs(self.velocity) > 5 and self.touch is None):
            return
        self.scroll += self.velocity / 60.0
        self.velocity *= 0.94
        if self.scroll < 0 or self.scroll > self._max_scroll():
            self.scroll = max(0.0, min(self._max_scroll(), self.scroll))
            self.velocity = 0.0
        self._dirty(top=False)

    def _watch_levels(self, now):
        try:
            mtime = os.stat(SYSTEM_CFG).st_mtime_ns
        except OSError:
            return
        if mtime == self.cfg_mtime:
            return
        self.cfg_mtime = mtime
        levels = {}
        try:
            with open(SYSTEM_CFG) as f:
                for line in f:
                    key, _, value = line.strip().partition("=")
                    if key in LEVELS and value.isdigit():
                        levels[key] = int(value)
        except OSError:
            return
        old, self.levels = self.levels, levels
        if old is None:
            return
        for key in LEVELS:
            if key in levels and levels[key] != old.get(key):
                at = self.osd[2] if self.osd and self.osd[0] == key and now - self.osd[2] < OSD_HOLD else now
                self.osd = (key, levels[key], at, now)
                self.top.dirty = True

    def _tick(self):
        now = time.monotonic()
        self._watch_levels(now)
        if self.osd:
            self.top.dirty = True
        if now - self.status_at > 20:
            self.status_at = now
            cap, charging = battery()
            status = (time.strftime("%I:%M %p" if self.clock12 else "%H:%M"), time.strftime("%a %-d %b"),
                      cap, charging)
            if status != self.status:
                self.status = status
                self.top.dirty = True
        if not (self.screen_on and self.visible) or self.touch:
            return
        idle = now - self.last_input
        if self.args.idle and idle > self.args.idle:
            threading.Thread(target=esapi.sway, args=("output %s power off" % self.args.output,),
                             daemon=True).start()
            self.screen_on = False
        elif self.args.dim and idle > self.args.dim and not self.dim_target:
            self.dim_target = 1.0

    # ---------- layout ----------

    def _metrics(self):
        w, h = self.bottom.size
        top = int(h * 0.09)
        bottom = int(h * 0.12)
        bar = int(h * 0.012)
        grid = (top, h - bottom - bar * 3)
        rows = self._rows()
        cell = (grid[1] - grid[0]) / rows
        margin = cell * 0.35
        return w, h, top, bottom, bar, grid, rows, cell, margin

    def _content_width(self):
        _, _, _, _, _, _, _, cell, margin = self._metrics()
        self._layout()
        return self.cols * cell + margin * 2

    def _max_scroll(self):
        return max(0.0, self._content_width() - self.bottom.size[0])

    def _tile_rect(self, i):
        _, _, _, _, _, grid, _, cell, margin = self._metrics()
        col, row, span = self._layout()[i]
        tile = cell * 0.84
        x = margin + col * cell + (cell - tile) / 2 - self.scroll
        y = grid[0] + row * cell + (cell - tile) / 2
        return x, y, tile + (span - 1) * cell, tile

    def _ensure_visible(self):
        i = self._sel()
        w = self.bottom.size[0] if self.canvases else 0
        if i < 0 or not w:
            return
        _, _, _, _, _, _, _, cell, margin = self._metrics()
        col, _, span = self._layout()[i]
        left = margin + col * cell
        if left - cell * 0.3 < self.scroll:
            self.scroll = left - cell * 0.3
        elif left + cell * (span + 0.3) > self.scroll + w:
            self.scroll = left + cell * (span + 0.3) - w
        self.scroll = max(0.0, min(self.scroll, self._max_scroll()))
        self.velocity = 0.0

    def _buttons(self):
        w, h, top, bottom, _, _, _, _, _ = self._metrics()
        pad = int(bottom * 0.16)
        bh = bottom - pad * 2
        tpad = int(top * 0.16)
        th = top - tpad * 2
        btns = {
            "zoom_out": (pad, h - bottom + pad, bh, bh),
            "zoom_in": (pad * 2 + bh, h - bottom + pad, bh, bh),
            "classic": (w - tpad - th * 3.6, tpad, th * 3.6, th),
        }
        if self.folder is not None:
            btns["back"] = (tpad, tpad, th * 2.6, th)
        if self._sel() >= 0:
            btns["open"] = (w - pad - bh * 3, h - bottom + pad, bh * 3, bh)
        return btns

    # ---------- drawing helpers ----------

    def _art_urls(self, game):
        return [game[k] for k in ("thumbnail", "titleshot", "image", "boxart", "mix") if game.get(k)]

    def _icon(self, cv, game):
        system = game["_system"]
        key = "icon:%s:%s" % (system, game["id"])
        if system not in icons.EXTRACTORS or key in self.failed:
            return None, False
        return self._tex(cv, key, ("icon", system, game)), True

    def _art(self, cv, game):
        urls = self._art_urls(game)
        return self._tex(cv, "art:%s:%s" % (game["_system"], game["id"]), ("art", urls)) if urls else None

    def _pill(self, cv, rect, label, fg, bg, edge=None):
        bx, by, bw, bh = rect
        if edge:
            cv.round(bx, by, bw, bh, bh / 2, edge)
            cv.round(bx + 2, by + 2, bw - 4, bh - 4, bh / 2 - 2, bg)
        else:
            cv.round(bx, by, bw, bh, bh / 2, bg)
        cv.text(label, bh * 0.45, fg, bx + bw / 2, by + bh / 2, align="center")

    def _draw_game(self, cv, game, x, y, s):
        system = game["_system"]
        tex, is_icon = self._icon(cv, game)
        if not is_icon:
            tex = self._art(cv, game)

        cv.round(x, y + s * 0.025, s, s, s * 0.14, (0, 0, 0), 40)
        if is_icon:
            ring = s * 0.055
            tint = self.tints.get("icon:%s:%s" % (system, game["id"]))
            cv.round(x, y, s, s, s * 0.14, pastel(tint, 0.58) if tint else RING)
            cv.round(x + ring, y + ring, s - ring * 2, s - ring * 2, s * 0.1, TILE)
            if tex:
                inset = s * 0.1
                sdl.SetTextureScaleMode(tex, sdl.SCALEMODE_NEAREST if system == "nds" else sdl.SCALEMODE_LINEAR)
                rect = cv.blit(tex, x + inset, y + inset, s - inset * 2, s - inset * 2, False)
                if rect:
                    cv.corners(*rect, s * 0.06, TILE, inverse=True)
            else:
                self._title_tile(cv, game, x, y, s, INK)
            return

        color, label = frame_for(system)
        cv.round(x, y, s, s, s * 0.14, color)
        border = s * 0.07
        art = s - border * 2
        strip = s * 0.17
        if tex:
            cv.blit(tex, x + border, y + border, art, art - strip + border, True)
            cv.corners(x + border, y + border, art, art - strip + border, s * 0.07, color, inverse=True)
        else:
            cv.fill(x + border, y + border, art, art - strip + border, WHITE, 30)
            self._title_tile(cv, game, x, y, s * 0.9, WHITE)
        cv.text(label, strip * 0.62, WHITE, x + s / 2, y + s - strip / 2 - border * 0.15, align="center")

    def _title_tile(self, cv, game, x, y, s, color):
        fs = s * 0.11
        lines = cv.wrap(title_of(game), fs, s * 0.8, 4)
        y0 = y + s / 2 - (len(lines) - 1) * fs * 0.6
        for i, line in enumerate(lines):
            cv.text(line, fs, color, x + s / 2, y0 + i * fs * 1.2, align="center")

    def _mini(self, cv, game, x, y, s):
        cv.round(x, y + s * 0.03, s, s, s * 0.16, (0, 0, 0), 50)
        cv.round(x, y, s, s, s * 0.16, EDGE)
        cv.round(x + 1, y + 1, s - 2, s - 2, s * 0.15, TILE)
        tex, is_icon = self._icon(cv, game)
        inset = s * 0.09
        if is_icon:
            if tex:
                sdl.SetTextureScaleMode(tex, sdl.SCALEMODE_NEAREST if game["_system"] == "nds" else sdl.SCALEMODE_LINEAR)
                cv.blit(tex, x + inset, y + inset, s - inset * 2, s - inset * 2, False)
            return
        tex = self._art(cv, game)
        if tex:
            cv.blit(tex, x + inset, y + inset, s - inset * 2, s - inset * 2, True)
            cv.corners(x + inset, y + inset, s - inset * 2, s - inset * 2, s * 0.08, TILE, inverse=True)
        else:
            cv.round(x + inset, y + inset, s - inset * 2, s - inset * 2, s * 0.08, frame_for(game["_system"])[0])

    def _draw_folder(self, cv, system, x, y, s):
        color, label = frame_for(system)
        dark = tuple(int(c * 0.72) for c in color)
        ring = s * 0.055
        cv.round(x, y + s * 0.025, s, s, s * 0.14, (0, 0, 0), 40)
        cv.round(x, y, s, s, s * 0.14, pastel(color))
        cv.round(x + ring, y + ring, s - ring * 2, s - ring * 2, s * 0.1, TILE)
        fx, fw = x + s * 0.1, s * 0.8
        body_y, body_b = y + s * 0.24, y + s * 0.86
        cv.round(fx, y + s * 0.17, fw * 0.4, s * 0.14, s * 0.04, dark)
        cv.round(fx, body_y, fw, body_b - body_y, s * 0.05, dark)
        games = self.games.get(system, [])[:3]
        ms = s * 0.3
        for i, g in enumerate(games):
            mx = fx + fw * 0.06 + i * (fw * 0.88 - ms) / 2
            my = y + s * (0.21 if i == 1 else 0.25)
            self._mini(cv, g, mx, my, ms)
        front_y = y + s * 0.5
        cv.round(fx - s * 0.015, front_y, fw + s * 0.03, body_b - front_y, s * 0.05, color)
        cv.fill(fx + s * 0.03, front_y + s * 0.012, fw - s * 0.06, max(2, s * 0.01), WHITE, 70)
        cv.text(label, s * 0.13, WHITE, x + s / 2, (front_y + body_b) / 2, max_w=fw * 0.9, align="center")

    def _draw_item(self, cv, item, x, y, s):
        if item.get("kind") == "folder":
            self._draw_folder(cv, item["system"], x, y, s)
        else:
            self._draw_game(cv, item, x, y, s)

    def _shot(self, cv, game):
        urls = [game[k] for k in ("image", "titleshot") if game.get(k)]
        return self._tex(cv, "shot:%s:%s" % (game["_system"], game["id"]), ("shot", urls)) if urls else None

    def _draw_resume(self, cv, game, x, y, w, h):
        r = h * 0.14
        b = h * 0.05
        cv.round(x, y + h * 0.025, w, h, r, (0, 0, 0), 40)
        cv.round(x, y, w, h, r, WHITE)
        ix, iy, iw, ih = x + b, y + b, w - b * 2, h - b * 2
        tex = self._shot(cv, game) or self._art(cv, game)
        if tex:
            cv.blit(tex, ix, iy, iw, ih, True)
        else:
            cv.fill(ix, iy, iw, ih, frame_for(game["_system"])[0])
        cv.fade(ix, iy + ih * 0.35, iw, ih * 0.65, (0, 0, 0), 0, 190)
        cv.corners(ix, iy, iw, ih, r - b, WHITE, inverse=True)

        fs = ih * 0.1
        label = "Jump back in"
        pw = cv.measure(label, fs) + fs * 1.6
        cv.round(ix + b, iy + b, pw, fs * 1.8, fs * 0.9, (0, 0, 0), 140)
        cv.text(label, fs, WHITE, ix + b + pw / 2, iy + b + fs * 0.9, align="center")

        ms = ih * 0.4
        mx, my = ix + b * 1.2, iy + ih - ms - b * 1.2
        self._mini(cv, game, mx, my, ms)
        tx = mx + ms + b
        tw = ix + iw - b - tx
        cv.text(title_of(game), ih * 0.12, WHITE, tx, my + ms * 0.35, max_w=tw)
        when = es_time(game.get("lastplayed"))
        if when:
            cv.text(ago(when), ih * 0.095, (225, 228, 235), tx, my + ms * 0.78, max_w=tw, bold=False)

    def _overlay(self, cv):
        if self.dim > 0:
            w, h = cv.size
            cv.fill(0, 0, w, h, (0, 0, 0), int(DIM_ALPHA * self.dim))

    # ---------- bottom screen ----------

    def _draw_bottom(self):
        cv = self.bottom
        w, h, top, bottom, bar, grid, rows, cell, margin = self._metrics()
        if not w:
            return
        cv.clear(BG)
        stripe = max(4, int(h / 135))
        for y in range(top, h - bottom, stripe * 2):
            cv.fill(0, y, w, stripe, STRIPE)

        items = self._items()
        sel = self._sel()
        first = max(0, (int((self.scroll - margin) / cell) - 2) * rows)
        last = min(len(items), first + (int(w / cell) + 5) * rows)
        cv.clip((0, grid[0], w, grid[1] - grid[0]))
        for i in range(first, last):
            x, y, tw, s = self._tile_rect(i)
            if x + tw < 0 or x > w:
                continue
            if i == sel:
                g = s * 0.06
                cv.round(x - g, y - g, tw + g * 2, s + g * 2, s * 0.16, ACCENT)
            if items[i].get("kind") == "resume":
                self._draw_resume(cv, items[i]["game"], x, y, tw, s)
            else:
                self._draw_item(cv, items[i], x, y, s)
        cv.clip(None)

        if not items:
            if not self.ready:
                msg = "Waiting for EmulationStation…"
            elif self.folder is not None and self.folder not in self.games:
                msg = "Loading…"
            else:
                msg = "No games found"
            cv.text(msg, h * 0.04, MUTED, w / 2, (grid[0] + grid[1]) / 2, align="center")

        content = self._content_width()
        if content > w:
            track_y = h - bottom - bar * 2
            cv.round(margin, track_y, w - margin * 2, bar, bar / 2, EDGE)
            tw = max(bar * 4, (w - margin * 2) * w / content)
            tx = margin + (w - margin * 2 - tw) * (self.scroll / max(1.0, content - w))
            cv.round(tx, track_y, tw, bar, bar / 2, ACCENT)

        self._draw_bottom_bars(cv, w, h, top, bottom)
        self._overlay(cv)
        cv.present()

    def _draw_bottom_bars(self, cv, w, h, top, bottom):
        cv.fill(0, 0, w, top, BAR)
        cv.fill(0, top - 2, w, 2, EDGE)
        cv.fill(0, h - bottom, w, bottom, BAR)
        cv.fill(0, h - bottom, w, 2, EDGE)
        btns = self._buttons()
        fs = top * 0.34
        if "back" in btns:
            bx, by, bw, bh = btns["back"]
            self._pill(cv, btns["back"], "‹ Back", INK, TILE, EDGE)
            system = self.systems.get(self.folder, {})
            right = btns["classic"][0] - top * 0.3
            cv.text(system.get("fullname", self.folder), fs, INK, bx + bw + top * 0.3, top / 2,
                    max_w=right - bx - bw - top * 0.3)
        elif self.root:
            n = len(self.root)
            cv.text("%d %s" % (n, "system" if n == 1 else "systems"), fs * 0.9, MUTED, top * 0.3, top / 2)
        self._pill(cv, btns["classic"], "Classic", INK, TILE, EDGE)

        for name, label in (("zoom_out", "−"), ("zoom_in", "+")):
            bx, by, bw, bh = btns[name]
            cv.round(bx, by, bw, bh, bh * 0.3, EDGE)
            cv.round(bx + 2, by + 2, bw - 4, bh - 4, bh * 0.28, TILE)
            cv.text(label, bh * 0.6, INK, bx + bw / 2, by + bh / 2, align="center")
        item = self._selected_item()
        if not item:
            return
        if item.get("kind") == "resume":
            item = item["game"]
        is_folder = item.get("kind") == "folder"
        self._pill(cv, btns["open"], "Open" if is_folder else "Play", WHITE, ACCENT)
        if is_folder:
            system = self.systems.get(item["system"], {})
            name = system.get("fullname", item["system"])
            n = len(self.games.get(item["system"], []))
            sub = "%d %s" % (n, "game" if n == 1 else "games") if item["system"] in self.games else ""
        else:
            name = title_of(item)
            sub = self.systems.get(item["_system"], {}).get("fullname", item["_system"])
        left = btns["zoom_in"][0] + btns["zoom_in"][2] + bottom * 0.3
        right = btns["open"][0] - bottom * 0.3
        cv.text(name, bottom * 0.26, INK, (left + right) / 2, h - bottom * 0.6, max_w=right - left, align="center")
        cv.text(sub, bottom * 0.17, MUTED, (left + right) / 2, h - bottom * 0.27, max_w=right - left, align="center")

    # ---------- top screen ----------

    def _draw_top(self):
        cv = self.top
        w, h = cv.size
        if not w:
            return
        cv.clear(BG)
        sb = int(h * 0.07)
        hb = int(h * 0.075)
        self._draw_status(cv, w, sb)
        item = self._selected_item()
        if item and item.get("kind") == "resume":
            item = item["game"]
        area = (sb, h - hb)
        if item and item.get("kind") == "folder":
            self._top_folder(cv, item, area)
        elif item:
            self._top_game(cv, item, area)
        else:
            msg = "Waiting for EmulationStation…" if not self.ready else "No games found"
            cv.text(msg, h * 0.045, MUTED, w / 2, (area[0] + area[1]) / 2, align="center")
        self._draw_hints(cv, w, h, hb, item)
        self._overlay(cv)
        self._draw_osd(cv, w, h)
        cv.present()

    def _draw_osd(self, cv, w, h):
        if not self.osd:
            return
        key, value, shown, changed = self.osd
        now = time.monotonic()
        t = now - changed
        if t > OSD_HOLD + OSD_FADE:
            self.osd = None
            return
        ease = min(1.0, (now - shown) / 0.15)
        a = int(255 * ease * min(1.0, (OSD_HOLD + OSD_FADE - t) / OSD_FADE))
        ch, cw = h * 0.09, w * 0.32
        x = (w - cw) / 2
        y = h * 0.07 + ch * (0.15 + 0.2 * ease)
        cv.round(x, y + 5, cw, ch, ch / 2, EDGE, a)
        cv.round(x, y, cw, ch, ch / 2, WHITE, a)
        s, cx, cy = ch * 0.5, x + ch * 0.6, y + ch / 2
        if key == "audio.volume":
            cv.fill(cx - s * 0.5, cy - s * 0.17, s * 0.22, s * 0.34, INK, a)
            for i in range(6):
                bh = s * (0.34 + 0.5 * (i + 1) / 6)
                cv.fill(cx - s * 0.28 + i * s * 0.05, cy - bh / 2, s * 0.05 + 1, bh, INK, a)
            for i, need in enumerate((1, 34, 67)):
                bh = s * (0.28 + 0.24 * i)
                cv.round(cx + s * (0.16 + 0.19 * i), cy - bh / 2, s * 0.1, bh, s * 0.05,
                         INK if value >= need else RING, a)
        else:
            r = s * 0.2
            cv.round(cx - r, cy - r, r * 2, r * 2, r, INK, a)
            for i in range(8):
                ang = math.pi * i / 4
                d = s * 0.1
                px, py = cx + math.cos(ang) * s * 0.4, cy + math.sin(ang) * s * 0.4
                cv.round(px - d / 2, py - d / 2, d, d, d / 2, INK, a)
        tx = x + ch * 1.15
        tw = cw - ch * 1.15 - ch * 1.25
        th = ch * 0.16
        cv.round(tx, cy - th / 2, tw, th, th / 2, RING, a)
        if value > 0:
            cv.round(tx, cy - th / 2, max(th, tw * value / 100), th, th / 2, ACCENT, a)
        cv.text("%d" % value, ch * 0.36, INK, x + cw - ch * 0.45, cy, align="right", a=a)

    def _draw_status(self, cv, w, sb):
        cv.fill(0, 0, w, sb, BAR)
        cv.fill(0, sb - 2, w, 2, EDGE)
        clock, date, cap, charging = self.status
        pad = sb * 0.45
        cv.text(date, sb * 0.36, MUTED, pad, sb / 2)
        x = w - pad
        if cap is not None:
            size = sb * 0.36
            bw, bh = size * 1.9, size
            bx, by = x - bw - size * 0.15, sb / 2 - bh / 2
            cv.fill(bx + bw, by + bh * 0.3, size * 0.15, bh * 0.4, MUTED)
            cv.round(bx, by, bw, bh, bh * 0.22, MUTED)
            cv.round(bx + 3, by + 3, bw - 6, bh - 6, bh * 0.16, BAR)
            fill = (80, 180, 90) if charging or cap > 20 else (220, 70, 60)
            cv.round(bx + 5, by + 5, max(2, (bw - 10) * cap / 100), bh - 10, bh * 0.1, fill)
            x = bx - size * 0.4
            x -= cv.text("%d%%" % cap, size * 0.95, MUTED, x, sb / 2, align="right") + pad * 0.8
        cv.text(clock, sb * 0.42, INK, x, sb / 2, align="right")

    def _top_layout(self, cv, area):
        w = cv.size[0]
        y0, y1 = area
        pad = (y1 - y0) * 0.08
        side = (y1 - y0) - pad * 2
        ax, ay = pad * 1.4, y0 + pad
        ix = ax + side + pad * 1.2
        return ax, ay, side, ix, w - ix - pad * 1.4, y1 - pad

    def _top_title(self, cv, text, x, y, width):
        size = 64
        for line in cv.wrap(text, size, width, 2):
            cv.text(line, size, INK, x, y + size * 0.55)
            y += size * 1.18
        return y + size * 0.2

    def _chip(self, cv, label, x, y, color, size=30):
        tw = cv.measure(label, size)
        h = size * 1.6
        cv.round(x, y, tw + h, h, h / 2, color)
        cv.text(label, size, WHITE, x + h / 2, y + h / 2)
        return tw + h

    def _backdrop(self, cv, game, area):
        urls = [game[k] for k in ("image", "titleshot", "thumbnail") if game.get(k)]
        tex = self._tex(cv, "blur:%s:%s" % (game["_system"], game["id"]), ("blur", urls)) if urls else None
        if tex:
            y0, y1 = area
            cv.blit(tex, 0, y0, cv.size[0], y1 - y0, True)
            cv.fill(0, y0, cv.size[0], y1 - y0, BG, 200)

    def _card(self, cv, tex, x, y, w, h, border):
        r = border * 2.4
        cv.round(x, y + border, w, h, r, (0, 0, 0), 50)
        cv.round(x, y, w, h, r, WHITE)
        cv.blit(tex, x + border, y + border, w - border * 2, h - border * 2, True)
        cv.corners(x + border, y + border, w - border * 2, h - border * 2, r - border, WHITE, inverse=True)

    def _fit(self, tex, w, h):
        tw, th = self.top.tex_size(tex)
        scale = min(w / max(1, tw), h / max(1, th))
        return tw * scale, th * scale

    def _clock(self, cv, x, y, size):
        r = size / 2
        cv.round(x, y - r, size, size, r, MUTED)
        cv.round(x + 3, y - r + 3, size - 6, size - 6, r - 3, WHITE)
        cv.fill(x + r - 1.5, y - r * 0.6, 3, r * 0.6, INK)
        cv.fill(x + r - 1.5, y - 1.5, r * 0.5, 3, INK)
        return size

    def _top_game(self, cv, game, area):
        w = cv.size[0]
        y0, y1 = area
        H = y1 - y0
        pad = H * 0.07
        system = game["_system"]
        key = "%s:%s" % (system, game["id"])
        self._backdrop(cv, game, area)

        right = w - pad * 1.4
        shot = self._shot(cv, game)
        urls = [game[k] for k in ("thumbnail", "boxart") if game.get(k)]
        box = self._tex(cv, "big:" + key, ("big", urls)) if urls else None
        card_x = right
        if shot:
            cw, ch = self._fit(shot, w * 0.36, H * 0.6)
            card_x, cy = right - cw, y0 + (H - ch) / 2
            self._card(cv, shot, card_x, cy, cw, ch, 10)
            if box:
                bw, bh = self._fit(box, ch * 0.5, ch * 0.55)
                self._card(cv, box, card_x - bw * 0.3, cy + ch - bh * 0.7, bw, bh, 6)
                card_x -= bw * 0.3
        elif box:
            bw, bh = self._fit(box, w * 0.3, H * 0.75)
            card_x = right - bw
            self._card(cv, box, card_x, y0 + (H - bh) / 2, bw, bh, 10)

        lx = pad * 1.4
        lw = card_x - lx - pad
        y = y0 + pad
        logo = self._tex(cv, "logo:" + key, ("logo", [game["marquee"]])) if game.get("marquee") else None
        if logo:
            dw, dh = self._fit(logo, lw * 0.85, H * 0.34)
            ly = y + (H * 0.34 - dh) / 2
            if sum(self.tints.get("logo:" + key, (0, 0, 0))) > 560:
                sdl.SetTextureColorMod(logo, 30, 32, 40)
                for dx, dy, a in ((0, 6, 90), (-3, 0, 70), (3, 0, 70), (0, -3, 70), (0, 3, 70)):
                    sdl.SetTextureAlphaMod(logo, a)
                    cv.blit(logo, lx + dx, ly + dy, dw, dh, False)
                sdl.SetTextureColorMod(logo, 255, 255, 255)
                sdl.SetTextureAlphaMod(logo, 255)
            cv.blit(logo, lx, ly, dw, dh, False)
            y += H * 0.34 + pad * 0.4
        elif system in icons.EXTRACTORS:
            s = H * 0.24
            self._draw_game(cv, game, lx, y, s)
            size = 60
            lines = cv.wrap(title_of(game), size, lw - s - pad * 0.5, 3)
            ty = y + s / 2 - (len(lines) - 1) * size * 0.59
            for line in lines:
                cv.text(line, size, INK, lx + s + pad * 0.5, ty, bold=True)
                ty += size * 1.18
            y += s + pad * 0.5
        else:
            y = self._top_title(cv, title_of(game), lx, y, lw)

        color, _ = frame_for(system)
        x = lx
        x += self._chip(cv, self.systems.get(system, {}).get("fullname", system), x, y, color) + 16
        if game.get("favorite") == "true":
            self._chip(cv, "Favourite", x, y, (235, 170, 20))
        y += 80

        meta = [game.get(k) for k in ("developer", "publisher", "genre") if game.get(k)]
        released = es_date(game.get("releasedate"))
        if released and released.tm_year > 1970:
            meta.append(str(released.tm_year))
        if game.get("players") and game["players"] not in ("1", "1-1"):
            meta.append("%s players" % game["players"])
        if meta:
            cv.text("  ·  ".join(dict.fromkeys(meta)), 32, MUTED, lx, y + 18, max_w=lw, bold=False)
            y += 56

        stats_y = y1 - pad * 0.9
        desc = " ".join((game.get("desc") or "").split())
        if desc:
            size = 32
            lines = cv.wrap(desc, size, lw, max(0, int((stats_y - 40 - y) / (size * 1.35))), bold=False)
            for line in lines:
                cv.text(line, size, INK, lx, y + size * 0.6, bold=False)
                y += size * 1.35

        when = es_time(game.get("lastplayed"))
        seconds = int(game.get("gametime") or 0)
        if when:
            x = lx + self._clock(cv, lx, stats_y, 36) + 14
            stats = [playtime(seconds)] if seconds else []
            stats.append("Last played " + ago(when))
            cv.text("  ·  ".join(stats), 32, INK, x, stats_y, max_w=lw - (x - lx))
        else:
            cv.text("Not played yet", 32, MUTED, lx, stats_y)

    def _top_folder(self, cv, item, area):
        ax, ay, side, ix, iw, bottom = self._top_layout(cv, area)
        system = item["system"]
        games = self.games.get(system, [])
        recent = sorted((g for g in games if es_time(g.get("lastplayed"))), key=lambda g: g["lastplayed"],
                        reverse=True)
        if recent or games:
            self._backdrop(cv, (recent or games)[0], area)
        self._draw_folder(cv, system, ax, ay, side)
        y = self._top_title(cv, self.systems.get(system, {}).get("fullname", system), ix, ay, iw)
        color, _ = frame_for(system)
        if system in self.games:
            self._chip(cv, "%d %s" % (len(games), "game" if len(games) == 1 else "games"), ix, y, color)
        y += 110
        shown = recent[:4] or games[:4]
        if not shown:
            return
        cv.text("Recently played" if recent else "Games", 34, MUTED, ix, y + 17)
        y += 60
        gap = 36
        ts = min((iw - gap * 3) / 4, bottom - y - 70)
        for i, g in enumerate(shown):
            gx = ix + i * (ts + gap)
            self._draw_game(cv, g, gx, y, ts)
            cv.text(title_of(g), 26, INK, gx + ts / 2, y + ts + 40, max_w=ts, align="center", bold=False)

    def _draw_hints(self, cv, w, h, hb, item):
        cv.fill(0, h - hb, w, hb, BAR)
        cv.fill(0, h - hb, w, 2, EDGE)
        hints = []
        if item:
            hints.append(("A", "Open" if item.get("kind") == "folder" else "Play"))
        if self.folder is not None:
            hints.append(("B", "Back"))
        hints.append(("X", "Bigger"))
        hints.append(("Y", "Smaller"))
        hints.append(("HOME", "Classic view"))
        y = h - hb / 2
        r = hb * 0.27
        x = hb * 0.45
        for glyph, label in hints:
            gw = max(r * 2, cv.measure(glyph, r * 1.05) + r * 1.2)
            cv.round(x, y - r, gw, r * 2, r, INK)
            cv.text(glyph, r * 1.05, WHITE, x + gw / 2, y, align="center")
            x += gw + r * 0.5
            x += cv.text(label, r * 1.15, INK, x, y, bold=False) + r * 1.6


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="DSI-1")
    p.add_argument("--top", default="DSI-2")
    p.add_argument("--touch", default="")
    p.add_argument("--systems", default="")
    p.add_argument("--exclude", default="pico-8,steam,music,tools")
    p.add_argument("--scrape", action="store_true")
    p.add_argument("--sounds", default="/storage/.config/homedeck/sounds")
    p.add_argument("--volume", type=int, default=30)
    p.add_argument("--dim", type=int, default=60)
    p.add_argument("--idle", type=int, default=300)
    return App(p.parse_args()).run()


if __name__ == "__main__":
    sys.exit(main())
