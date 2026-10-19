import json
import logging
import os
import random
import threading
import time

from flask import Flask, jsonify, request

STATE = "/state/active.json"
LOG = "/state/fault.log"

app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

_lock = threading.Lock()
_cache = {"mtime": None, "state": None}
_active = False


def load_state():
    try:
        mtime = os.path.getmtime(STATE)
    except OSError:
        _cache.update(mtime=None, state=None)
        return None
    if mtime != _cache["mtime"]:
        try:
            with open(STATE) as f:
                _cache.update(mtime=mtime, state=json.load(f))
        except (OSError, ValueError):
            pass
    return _cache["state"]


def active_parts():
    global _active
    s = load_state()
    now = time.time()
    on = bool(s) and s["start"] <= now < s["end"]
    with _lock:
        if on != _active:
            _active = on
            rec = {"ts": round(now, 3), "event": "on" if on else "off"}
            if on:
                rec["late_by_s"] = round(now - s["start"], 1)
            with open(LOG, "a") as f:
                f.write(json.dumps(rec) + "\n")
    return s["parts"] if on else []


def injected_error(path):
    for p in active_parts():
        if p["path"] != path or random.random() >= float(p.get("rate", 1.0)):
            continue
        if p["type"] == "latency":
            time.sleep(p["ms"] / 1000)
        elif p["type"] == "error":
            return True
    return False


@app.get("/stock")
def stock():
    time.sleep(random.uniform(0.01, 0.04))
    if injected_error(request.path):
        return jsonify(error="internal error"), 500
    return jsonify(stock=42)


@app.post("/pay")
def pay():
    time.sleep(random.uniform(0.05, 0.12))
    if injected_error(request.path):
        return jsonify(error="internal error"), 500
    return jsonify(paid=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000, threaded=True)
