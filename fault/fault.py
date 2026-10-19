import base64
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

STATE = "/state/active.json"
LOG = "/state/fault.log"
KST = timezone(timedelta(hours=9))
TYPES = {"error", "latency"}


def kst(ts):
    return datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d %H:%M:%S KST")


def fail(msg):
    print(f"오류: {msg}", file=sys.stderr)
    sys.exit(1)


def log_event(event, **fields):
    rec = {"ts": round(time.time(), 3), "event": event, **fields}
    with open(LOG, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def validate(s):
    parts = s.get("parts")
    if not isinstance(parts, list) or not parts:
        fail("parts가 비어 있음")
    for p in parts:
        if p.get("type") not in TYPES:
            fail(f"알 수 없는 type: {p.get('type')}")
        if not str(p.get("path", "")).startswith("/"):
            fail("path는 /로 시작해야 함")
        if not 0 < float(p.get("rate", 1.0)) <= 1:
            fail("rate는 0보다 크고 1 이하")
        if p["type"] == "latency" and not p.get("ms"):
            fail("latency에는 ms가 필요")
    if int(s.get("duration_s", 0)) <= 0:
        fail("duration_s가 필요")


def schedule(s):
    at = s.get("at", "now")
    if at == "now":
        return time.time()
    dt = datetime.fromisoformat(at)
    if dt.tzinfo is None:
        fail("at에는 +09:00 같은 시간대가 필요")
    return dt.timestamp()


def encode():
    s = json.load(sys.stdin)
    validate(s)
    at = s.get("at", "now")
    if isinstance(at, str) and at.startswith("+"):
        target = time.time() + int(at[1:])
        s["at"] = datetime.fromtimestamp(target, KST).replace(microsecond=0).isoformat()
    raw = json.dumps(s, separators=(",", ":")).encode()
    code = base64.urlsafe_b64encode(raw).decode()
    digest = hashlib.sha256(code.encode()).hexdigest()
    if s.get("at", "now") != "now":
        print(f"예약 시각: {kst(schedule(s))}")
    print(f"봉인(sha256): {digest}")
    print()
    print("아래 한 줄을 그대로 복사해서 실행:")
    print(f"docker compose run --rm fault start {code}")


def start(code):
    try:
        s = json.loads(base64.urlsafe_b64decode(code.encode()))
    except Exception:
        fail("시나리오 코드를 읽을 수 없음")
    validate(s)
    sched = schedule(s)
    end = sched + int(s["duration_s"])
    now = time.time()
    if end <= now:
        fail("이미 끝난 시나리오")
    late = max(0.0, now - sched)
    digest = hashlib.sha256(code.encode()).hexdigest()
    state = {"parts": s["parts"], "start": sched, "end": end, "sha256": digest}
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)
    log_event("armed", start=sched, end=end, late_by_s=round(late, 1), sha256=digest[:12])
    print(f"장애 예약: {kst(sched)} ~ {kst(end)}")
    if late > 1:
        print(f"예약 시각보다 {late:.0f}초 늦게 실행됨 (기록됨)")


def stop():
    if os.path.exists(STATE):
        os.remove(STATE)
        log_event("stopped")
        print("장애 해제")
    else:
        print("켜진 장애 없음")


def status():
    if not os.path.exists(STATE):
        print("켜진 장애 없음")
        return
    with open(STATE) as f:
        s = json.load(f)
    now = time.time()
    if now < s["start"]:
        state = "대기 중"
    elif now < s["end"]:
        state = "진행 중"
    else:
        state = "끝남"
    print(f"{state}: {kst(s['start'])} ~ {kst(s['end'])}")


def show_log():
    if not os.path.exists(LOG):
        print("기록 없음")
        return
    with open(LOG) as f:
        for line in f:
            rec = json.loads(line)
            extra = {k: v for k, v in rec.items() if k not in ("ts", "event")}
            print(kst(rec["ts"]), rec["event"], json.dumps(extra, ensure_ascii=False))


def main():
    if len(sys.argv) < 2:
        fail("사용법: fault encode | start <코드> | stop | status | log")
    cmd = sys.argv[1]
    if cmd == "encode":
        encode()
    elif cmd == "start" and len(sys.argv) == 3:
        start(sys.argv[2])
    elif cmd == "stop":
        stop()
    elif cmd == "status":
        status()
    elif cmd == "log":
        show_log()
    else:
        fail("사용법: fault encode | start <코드> | stop | status | log")


if __name__ == "__main__":
    main()
