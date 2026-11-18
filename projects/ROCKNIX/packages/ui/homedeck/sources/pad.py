# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present ROCKNIX (https://github.com/ROCKNIX)

import fcntl
import glob
import os
import select
import struct
import time
import xml.etree.ElementTree as ET

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03
KEY_F1 = 59
BTN_JOYSTICK = 0x120
KEY_MAX = 0x2FF
BTN_SOUTH = 0x130
BTN_EAST = 0x131
BTN_NORTH = 0x133
BTN_WEST = 0x134
BTN_TL = 0x136
BTN_TR = 0x137
BTN_SELECT = 0x13A
BTN_START = 0x13B
BTN_MODE = 0x13C
BTN_TOUCH = 0x14A
ABS_X = 0x00
ABS_Y = 0x01
ABS_HAT0X = 0x10
ABS_HAT0Y = 0x11

ES_INPUT = "/storage/.config/emulationstation/es_input.cfg"
ES_ACTIONS = {
    "a": "a", "b": "b", "x": "x", "y": "y",
    "leftshoulder": "l", "rightshoulder": "r", "select": "select", "start": "start",
}
DEFAULTS = {
    BTN_EAST: "a", BTN_SOUTH: "b", BTN_NORTH: "x", BTN_WEST: "y",
    BTN_TL: "l", BTN_TR: "r", BTN_SELECT: "select", BTN_START: "start",
}

EVENT = struct.Struct("llHHi")
ABSINFO = struct.Struct("6i")
REPEAT_DELAY = 0.35
REPEAT_RATE = 0.09


def _sysfs(node, name):
    try:
        with open("/sys/class/input/%s/device/%s" % (node, name)) as f:
            return f.read().strip()
    except OSError:
        return ""


def _bits(node, kind):
    words = _sysfs(node, "capabilities/" + kind).split()
    return int("".join(w.zfill(16) for w in words) or "0", 16)


def _es_buttons(name):
    try:
        root = ET.parse(ES_INPUT).getroot()
    except (OSError, ET.ParseError):
        return {}
    for cfg in root.iter("inputConfig"):
        if cfg.get("deviceName") != name:
            continue
        out = {}
        for inp in cfg.iter("input"):
            action = ES_ACTIONS.get(inp.get("name"))
            if action and inp.get("type") == "button":
                out[int(inp.get("id"))] = action
        return out
    return {}


def _button_map(name, keys):
    # Same button numbering as SDL's Linux joystick driver, which ES mappings refer to.
    order = [c for c in range(BTN_JOYSTICK, KEY_MAX) if keys >> c & 1]
    order += [c for c in range(BTN_JOYSTICK) if keys >> c & 1]
    es = _es_buttons(name)
    if not es:
        return dict(DEFAULTS)
    return {order[i]: action for i, action in es.items() if i < len(order)}


def _abs_range(fd, code):
    buf = bytearray(ABSINFO.size)
    req = (2 << 30) | (ABSINFO.size << 16) | (ord("E") << 8) | (0x40 + code)
    try:
        fcntl.ioctl(fd, req, buf)
    except OSError:
        return None
    _, lo, hi, _, _, _ = ABSINFO.unpack(buf)
    return (lo, hi) if hi > lo else None


class _Device:
    def __init__(self, path, fd, kind, buttons, ranges):
        self.path = path
        self.fd = fd
        self.kind = kind
        self.buttons = buttons
        self.ranges = ranges
        self.axes = {}
        self.hotkey = False
        self.touching = False
        self.reported = False
        self.pos = [0.0, 0.0]


class Pad:
    def __init__(self, emit, touch_name=None, emit_touch=None):
        self.emit = emit
        self.touch_name = touch_name
        self.emit_touch = emit_touch
        self.has_touch = False
        self.devices = {}
        self.direction = None
        self.repeat_at = None

    def run(self):
        last_scan = 0.0
        while True:
            now = time.monotonic()
            if now - last_scan > 3.0:
                last_scan = now
                self._scan()
            timeout = 1.0
            if self.repeat_at is not None:
                timeout = max(0.0, min(timeout, self.repeat_at - now))
            try:
                ready, _, _ = select.select(list(self.devices), [], [], timeout)
            except (OSError, ValueError):
                ready = []
            for fd in ready:
                self._read(self.devices.get(fd))
            if self.repeat_at is not None and time.monotonic() >= self.repeat_at:
                self.repeat_at = time.monotonic() + REPEAT_RATE
                self.emit(self.direction)

    def _scan(self):
        known = {d.path for d in self.devices.values()}
        for path in sorted(glob.glob("/dev/input/event*")):
            if path in known:
                continue
            node = os.path.basename(path)
            keys = _bits(node, "key")
            if self.touch_name and _sysfs(node, "name").replace(" ", "_") == self.touch_name:
                kind = "touch"
            elif keys >> BTN_SOUTH & 1:
                kind = "pad"
            elif keys >> KEY_F1 & 1:
                kind = "keys"
            else:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            buttons, ranges = {}, {}
            if kind == "pad":
                buttons = _button_map(_sysfs(node, "name"), keys)
            if kind in ("pad", "touch"):
                for code in (ABS_X, ABS_Y):
                    r = _abs_range(fd, code)
                    if r:
                        ranges[code] = r
            if kind == "touch":
                if len(ranges) < 2:
                    os.close(fd)
                    continue
                self.has_touch = True
            self.devices[fd] = _Device(path, fd, kind, buttons, ranges)

    def _drop(self, dev):
        self.devices.pop(dev.fd, None)
        try:
            os.close(dev.fd)
        except OSError:
            pass
        if dev.kind == "touch":
            self.has_touch = any(d.kind == "touch" for d in self.devices.values())
            if dev.reported:
                self.emit_touch("up", *dev.pos)
            return
        self._set_direction(None)

    def _read(self, dev):
        if dev is None:
            return
        try:
            data = os.read(dev.fd, EVENT.size * 64)
        except BlockingIOError:
            return
        except OSError:
            self._drop(dev)
            return
        if not data:
            self._drop(dev)
            return
        for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _, _, etype, code, value = EVENT.unpack_from(data, off)
            if dev.kind == "touch":
                self._touch(dev, etype, code, value)
            elif etype == EV_KEY:
                self._key(dev, code, value)
            elif etype == EV_ABS and dev.kind == "pad":
                self._axis(dev, code, value)

    def _touch(self, dev, etype, code, value):
        if etype == EV_KEY and code == BTN_TOUCH:
            dev.touching = value != 0
        elif etype == EV_ABS and code in dev.ranges:
            lo, hi = dev.ranges[code]
            dev.pos[code == ABS_Y] = (value - lo) / (hi - lo)
        elif etype == EV_SYN and code == 0:
            if dev.touching:
                self.emit_touch("move" if dev.reported else "down", *dev.pos)
                dev.reported = True
            elif dev.reported:
                self.emit_touch("up", *dev.pos)
                dev.reported = False

    def _key(self, dev, code, value):
        if dev.kind == "keys":
            if code == KEY_F1 and value == 1:
                self.emit("home")
            return
        if code == BTN_MODE:
            dev.hotkey = value != 0
        if value != 1:
            return
        action = dev.buttons.get(code)
        self.emit("any" if dev.hotkey or not action else action)

    def _axis(self, dev, code, value):
        if code in (ABS_HAT0X, ABS_HAT0Y):
            dev.axes[code] = (value > 0) - (value < 0)
        elif code in dev.ranges:
            lo, hi = dev.ranges[code]
            v = (value - (lo + hi) / 2) / ((hi - lo) / 2)
            if abs(v) > 0.6:
                dev.axes[code] = 1 if v > 0 else -1
            elif abs(v) < 0.35:
                dev.axes[code] = 0
        else:
            return
        x = dev.axes.get(ABS_HAT0X) or dev.axes.get(ABS_X, 0)
        y = dev.axes.get(ABS_HAT0Y) or dev.axes.get(ABS_Y, 0)
        direction = None
        if y:
            direction = "up" if y < 0 else "down"
        elif x:
            direction = "left" if x < 0 else "right"
        if dev.hotkey and direction:
            self.emit("any")
            direction = None
        self._set_direction(direction)

    def _set_direction(self, direction):
        if direction == self.direction:
            return
        self.direction = direction
        if direction:
            self.emit(direction)
            self.repeat_at = time.monotonic() + REPEAT_DELAY
        else:
            self.repeat_at = None
