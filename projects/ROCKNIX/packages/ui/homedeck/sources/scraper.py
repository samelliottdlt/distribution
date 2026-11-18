# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present ROCKNIX (https://github.com/ROCKNIX)

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import esapi

SERVER = "https://thumbnails.libretro.com"

PLAYLISTS = {
    "3ds": ["Nintendo - Nintendo 3DS"],
    "nds": ["Nintendo - Nintendo DS"],
    "gb": ["Nintendo - Game Boy", "Nintendo - Game Boy Color"],
    "gbc": ["Nintendo - Game Boy Color"],
    "gba": ["Nintendo - Game Boy Advance"],
    "nes": ["Nintendo - Nintendo Entertainment System"],
    "famicom": ["Nintendo - Nintendo Entertainment System"],
    "fds": ["Nintendo - Family Computer Disk System"],
    "snes": ["Nintendo - Super Nintendo Entertainment System"],
    "sfc": ["Nintendo - Super Nintendo Entertainment System"],
    "n64": ["Nintendo - Nintendo 64"],
    "gamecube": ["Nintendo - GameCube"],
    "wii": ["Nintendo - Wii"],
    "wiiu": ["Nintendo - Wii U"],
    "virtualboy": ["Nintendo - Virtual Boy"],
    "pokemini": ["Nintendo - Pokemon Mini"],
    "psx": ["Sony - PlayStation"],
    "ps2": ["Sony - PlayStation 2"],
    "ps3": ["Sony - PlayStation 3"],
    "psp": ["Sony - PlayStation Portable"],
    "psvita": ["Sony - PlayStation Vita"],
    "dreamcast": ["Sega - Dreamcast"],
    "saturn": ["Sega - Saturn"],
    "genesis": ["Sega - Mega Drive - Genesis"],
    "megadrive": ["Sega - Mega Drive - Genesis"],
    "mastersystem": ["Sega - Master System - Mark III"],
    "gamegear": ["Sega - Game Gear"],
    "segacd": ["Sega - Mega-CD - Sega CD"],
    "megacd": ["Sega - Mega-CD - Sega CD"],
    "sega32x": ["Sega - 32X"],
    "sg-1000": ["Sega - SG-1000"],
    "pcengine": ["NEC - PC Engine - TurboGrafx 16"],
    "tg16": ["NEC - PC Engine - TurboGrafx 16"],
    "pcenginecd": ["NEC - PC Engine CD - TurboGrafx-CD"],
    "tg16cd": ["NEC - PC Engine CD - TurboGrafx-CD"],
    "supergrafx": ["NEC - PC Engine SuperGrafx"],
    "ngp": ["SNK - Neo Geo Pocket"],
    "ngpc": ["SNK - Neo Geo Pocket Color"],
    "neogeo": ["SNK - Neo Geo"],
    "atari2600": ["Atari - 2600"],
    "atari7800": ["Atari - 7800"],
    "atarilynx": ["Atari - Lynx"],
    "atarijaguar": ["Atari - Jaguar"],
    "wonderswan": ["Bandai - WonderSwan"],
    "wonderswancolor": ["Bandai - WonderSwan Color"],
    "colecovision": ["Coleco - ColecoVision"],
    "intellivision": ["Mattel - Intellivision"],
    "vectrex": ["GCE - Vectrex"],
    "3do": ["The 3DO Company - 3DO"],
    "msx": ["Microsoft - MSX"],
    "msx2": ["Microsoft - MSX2"],
    "xbox": ["Microsoft - Xbox"],
    "arcade": ["MAME", "FBNeo - Arcade Games"],
    "mame": ["MAME"],
    "fbn": ["FBNeo - Arcade Games"],
}

KINDS = (
    ("Named_Boxarts", "thumbnail"),
    ("Named_Titles", "titleshot"),
    ("Named_Snaps", "image"),
    ("Named_Logos", "marquee"),
)

RETRY_SECONDS = 7 * 24 * 3600


def _fetch(url, timeout=20.0):
    req = urllib.request.Request(url, headers={"User-Agent": "ROCKNIX homedeck"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _safe(name):
    return re.sub(r'[&*/:`<>?\\|"]', "_", name)


def _base(name):
    name = re.sub(r"\s*[\(\[].*?[\)\]]", "", name)
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _region_score(name):
    for i, region in enumerate(("(USA", "(World", "(Europe", "(Japan")):
        if region in name:
            return i
    return 9


class Scraper:
    playlists = PLAYLISTS

    def __init__(self, cache_dir, on_update):
        self.cache_dir = cache_dir
        self.on_update = on_update
        self.queue = []
        self.cond = threading.Condition()
        self.index = {}
        self.state_path = os.path.join(cache_dir, "scrape.json")
        try:
            with open(self.state_path) as f:
                self.state = json.load(f)
        except (OSError, ValueError):
            self.state = {}
        threading.Thread(target=self._run, daemon=True).start()

    def enqueue(self, system, games):
        if system not in PLAYLISTS:
            return
        with self.cond:
            self.queue.append((system, list(games)))
            self.cond.notify()

    def _listing(self, playlist, folder):
        key = playlist + "/" + folder
        if key not in self.index:
            names = {}
            try:
                html = _fetch("%s/%s/%s/" % (SERVER, urllib.parse.quote(playlist), folder)).decode("utf-8", "replace")
                for href in re.findall(r'href="([^"?/]+\.png)"', html):
                    stem = urllib.parse.unquote(href)[:-4]
                    names.setdefault(_base(stem), []).append(stem)
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    return None
            except OSError:
                return None
            self.index[key] = names
        return self.index[key]

    def _match(self, playlist, folder, stem):
        names = self._listing(playlist, folder)
        if names is None:
            return None
        candidates = names.get(_base(stem), [])
        if _safe(stem) in candidates:
            return _safe(stem)
        if candidates:
            return min(candidates, key=_region_score)
        return None

    def _scrape(self, system, game):
        stem = os.path.splitext(os.path.basename(game["path"]))[0]
        changed = False
        for folder, kind in KINDS:
            if game.get(kind):
                continue
            key = "%s/%s/%s" % (system, game["id"], kind)
            if time.time() - self.state.get(key, 0) < RETRY_SECONDS:
                continue
            data = None
            for playlist in PLAYLISTS[system]:
                name = self._match(playlist, folder, stem)
                if name is None:
                    continue
                try:
                    data = _fetch("%s/%s/%s/%s.png" % (SERVER, urllib.parse.quote(playlist), folder, urllib.parse.quote(name)))
                except OSError:
                    data = None
                if data:
                    break
            if data and esapi.upload_media(system, game["id"], kind, data, "image/png"):
                game[kind] = "/systems/%s/games/%s/media/%s" % (system, game["id"], kind)
                changed = True
            else:
                self.state[key] = time.time()
        return changed

    def _run(self):
        while True:
            with self.cond:
                while not self.queue:
                    self.cond.wait()
                system, games = self.queue.pop(0)
            for game in games:
                while esapi.running_game():
                    time.sleep(30)
                if self._scrape(system, game):
                    self.on_update(system, game)
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
                with open(self.state_path, "w") as f:
                    json.dump(self.state, f)
            except OSError:
                pass
