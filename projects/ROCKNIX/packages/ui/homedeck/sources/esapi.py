# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present ROCKNIX (https://github.com/ROCKNIX)

import glob
import json
import os
import socket
import struct
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:1234"


def _request(method, path, body=None, ctype=None, timeout=3.0):
    req = urllib.request.Request(BASE + path, data=body, method=method)
    if ctype:
        req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def get_json(path, timeout=3.0):
    try:
        status, body = _request("GET", path, timeout=timeout)
    except urllib.error.HTTPError as e:
        return e.code, None
    except (OSError, ValueError):
        return 0, None
    try:
        return status, json.loads(body)
    except ValueError:
        return status, None


def get_bytes(path, timeout=5.0):
    try:
        return _request("GET", path, timeout=timeout)[1]
    except (OSError, ValueError):
        return None


def post(path, body=b"", ctype="text/plain", timeout=3.0):
    try:
        return _request("POST", path, body, ctype, timeout)[0]
    except urllib.error.HTTPError as e:
        return e.code
    except (OSError, ValueError):
        return 0


def systems():
    _, data = get_json("/systems", timeout=10.0)
    out = []
    for s in data or []:
        if s.get("visible") == "false" or s.get("gamesystem") == "false":
            continue
        if s.get("collection") == "true" or s.get("groupsystem") == "true":
            continue
        if not s.get("visibleGames"):
            continue
        out.append(s)
    return out


def games(system):
    _, data = get_json("/systems/" + urllib.parse.quote(system) + "/games", timeout=15.0)
    data = [g for g in data or [] if g.get("hidden") != "true"]
    parents = {os.path.dirname(g.get("path", "")) for g in data}
    out = [g for g in data if g.get("path") not in parents]
    out.sort(key=lambda g: (g.get("favorite") != "true", g.get("sortname") or g.get("name", "").lower()))
    return out


def selection():
    return get_json("/selection", timeout=1.0)


def select(system, game_id=None, open_list=False):
    q = {"system": system}
    if game_id:
        q["id"] = game_id
    if open_list:
        q["open"] = "1"
    return post("/selection?" + urllib.parse.urlencode(q))


def launch(path):
    return post("/launch", path.encode("utf-8"))


def running_game():
    status, _ = get_json("/runningGame", timeout=1.0)
    return status == 200


def upload_media(system, game_id, kind, data, ctype):
    path = "/systems/%s/games/%s/media/%s" % (urllib.parse.quote(system), game_id, kind)
    return post(path, data, ctype, timeout=10.0) == 200


def _sway_ipc(msg_type, payload=b""):
    sock_path = os.environ.get("SWAYSOCK") or next(iter(glob.glob("/var/run/0-runtime-dir/sway-ipc.*.sock")), None)
    if not sock_path:
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(sock_path)
            s.sendall(b"i3-ipc" + struct.pack("<II", len(payload), msg_type) + payload)
            head = b""
            while len(head) < 14:
                chunk = s.recv(14 - len(head))
                if not chunk:
                    return None
                head += chunk
            size = struct.unpack_from("<I", head, 6)[0]
            body = b""
            while len(body) < size:
                chunk = s.recv(size - len(body))
                if not chunk:
                    return None
                body += chunk
            return json.loads(body)
    except (OSError, ValueError):
        return None


def sway(command):
    return _sway_ipc(0, command.encode("utf-8")) is not None


def sway_output(name):
    for out in _sway_ipc(3) or []:
        if out.get("name") == name:
            return out
    return None


def output_power(name):
    out = sway_output(name)
    return None if out is None else bool(out.get("power"))


def sway_tree():
    return _sway_ipc(4)


def sway_inputs():
    return _sway_ipc(100) or []
