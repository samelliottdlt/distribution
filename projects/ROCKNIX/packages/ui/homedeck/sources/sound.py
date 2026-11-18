# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present ROCKNIX (https://github.com/ROCKNIX)

import ctypes
import os
import sys
from ctypes import c_char_p, c_int, c_uint16, c_void_p

import sdl2 as sdl

EFFECTS = ("move", "open", "back", "launch")
EXTENSIONS = (".wav", ".ogg", ".mp3", ".flac")
AUDIO_S16SYS = 0x8010 if sys.byteorder == "little" else 0x9010
MIX_INIT = 0x01 | 0x08 | 0x10


class Sounds:
    def __init__(self, folder, volume=100):
        self.mix = None
        self.chunks = {}
        self.music = None
        self.playing = False
        try:
            entries = os.listdir(folder)
        except OSError:
            return
        files = {}
        for name in sorted(entries):
            stem, ext = os.path.splitext(name)
            if ext.lower() in EXTENSIONS and (stem in EFFECTS or stem == "bgm"):
                files.setdefault(stem, os.path.join(folder, name))
        if not files:
            return
        try:
            mix = ctypes.CDLL("libSDL2_mixer-2.0.so.0")
        except OSError:
            return
        for fn, res, args in (
            ("Mix_Init", c_int, (c_int,)),
            ("Mix_OpenAudio", c_int, (c_int, c_uint16, c_int, c_int)),
            ("Mix_LoadWAV_RW", c_void_p, (c_void_p, c_int)),
            ("Mix_LoadMUS", c_void_p, (c_char_p,)),
            ("Mix_PlayChannelTimed", c_int, (c_int, c_void_p, c_int, c_int)),
            ("Mix_FadeInMusic", c_int, (c_void_p, c_int, c_int)),
            ("Mix_PlayingMusic", c_int, ()),
            ("Mix_PauseMusic", None, ()),
            ("Mix_ResumeMusic", None, ()),
            ("Mix_Volume", c_int, (c_int, c_int)),
            ("Mix_VolumeMusic", c_int, (c_int,)),
        ):
            getattr(mix, fn).restype = res
            getattr(mix, fn).argtypes = args
        if sdl.InitSubSystem(sdl.INIT_AUDIO) != 0:
            return
        mix.Mix_Init(MIX_INIT)
        if mix.Mix_OpenAudio(44100, AUDIO_S16SYS, 2, 1024) != 0:
            print("Mix_OpenAudio: " + sdl.error(), file=sys.stderr)
            return
        self.mix = mix
        level = max(0, min(128, round(128 * volume / 100)))
        mix.Mix_Volume(-1, level)
        mix.Mix_VolumeMusic(level)
        for stem, path in files.items():
            if stem == "bgm":
                self.music = mix.Mix_LoadMUS(path.encode("utf-8"))
            else:
                chunk = mix.Mix_LoadWAV_RW(sdl.RWFromFile(path.encode("utf-8"), b"rb"), 1)
                if chunk:
                    self.chunks[stem] = chunk
            if not (self.music if stem == "bgm" else self.chunks.get(stem)):
                print("sound %s: %s" % (path, sdl.error()), file=sys.stderr)

    def play(self, name):
        chunk = self.chunks.get(name)
        if chunk:
            self.mix.Mix_PlayChannelTimed(-1, chunk, 0, -1)

    def bgm(self, on):
        if not self.music or on == self.playing:
            return
        self.playing = on
        if not on:
            self.mix.Mix_PauseMusic()
        elif self.mix.Mix_PlayingMusic():
            self.mix.Mix_ResumeMusic()
        else:
            self.mix.Mix_FadeInMusic(self.music, -1, 500)
