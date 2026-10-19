import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import yaml

PROM = os.environ.get("PROM_URL", "http://prometheus:9090")
RULES_DIR = "/rules"
LOG = "/state/fault.log"
KST = timezone(timedelta(hours=9))
STEP = 5
KEYWORDS = {"by", "without", "on", "ignoring", "group_left", "group_right",
            "bool", "offset", "and", "or", "unless", "inf", "nan"}


def kst(ts):
    return datetime.fromtimestamp(ts, KST).strftime("%H:%M:%S")


def fmt(sec):
    m, s = divmod(int(round(sec)), 60)
    return f"{m}분 {s}초"


def api(path, params=None):
    url = PROM + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())["data"]


def reload_prometheus():
    req = urllib.request.Request(PROM + "/-/reload", method="POST")
    urllib.request.urlopen(req, timeout=10).read()


def rule_files():
    files = glob.glob(os.path.join(RULES_DIR, "*.yml"))
    files += glob.glob(os.path.join(RULES_DIR, "*.yaml"))
    return sorted(files)


def alert_rules(path):
    with open(path) as f:
        doc = yaml.safe_load(f) or {}
    out = []
    for g in doc.get("groups", []):
        for r in g.get("rules", []):
            if "alert" in r:
                out.append((g["name"], r["alert"], str(r["expr"])))
    return out


def loaded_rules():
    found = {}
    for g in api("/api/v1/rules")["groups"]:
        base = os.path.basename(g["file"])
        for r in g["rules"]:
            if r.get("type") == "alerting":
                found[(base, g["name"], r["name"])] = r
    return found


def metric_names(expr):
    e = re.sub(r'"(?:[^"\\]|\\.)*"', " ", expr)
    e = re.sub(r"'(?:[^'\\]|\\.)*'", " ", e)
    e = re.sub(r"\{[^}]*\}", " ", e)
    e = re.sub(r"\[[^\]]*\]", " ", e)
    e = re.sub(r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", " ", e)
    names = set()
    for m in re.finditer(r"[A-Za-z_:][A-Za-z0-9_:]*", e):
        if e[m.end():].lstrip().startswith("("):
            continue
        if m.group(0).lower() in KEYWORDS:
            continue
        names.add(m.group(0))
    return names


def check_rules():
    files = rule_files()
    if not files:
        print("룰 파일이 없음 (prometheus/rules/*.yml)")
        return 1

    print("1) 문법")
    syntax_ok = True
    for f in files:
        r = subprocess.run(["promtool", "check", "rules", f], capture_output=True, text=True)
        if r.returncode == 0:
            print(f"   통과  {os.path.basename(f)}")
        else:
            syntax_ok = False
            print(f"   실패  {os.path.basename(f)}")
            for line in (r.stdout + r.stderr).strip().splitlines():
                print(f"         {line}")
    if not syntax_ok:
        print("\n문법 오류가 있어서 여기서 멈춤. 고친 뒤 다시 실행.")
        return 1

    reload_prometheus()
    expected = [(os.path.basename(f), g, a, e) for f in files for g, a, e in alert_rules(f)]

    print("\n2) 로드 (Prometheus가 실제로 읽었나)")
    deadline = time.time() + 45
    while True:
        found = loaded_rules()
        waiting = [x for x in expected
                   if found.get(x[:3], {}).get("health", "unknown") == "unknown"]
        if not waiting or time.time() > deadline:
            break
        time.sleep(3)
    load_ok = True
    for base, g, a, _ in expected:
        r = found.get((base, g, a))
        if r is None:
            load_ok = False
            print(f"   실패  {a} ({base}): Prometheus에 없음")
        elif r.get("health") != "ok":
            load_ok = False
            print(f"   실패  {a}: {r.get('health')} {r.get('lastError', '')}")
        else:
            print(f"   통과  {a}")

    print("\n3) 지표 존재 (룰이 참조하는 지표가 실제로 있나)")
    known = set(api("/api/v1/label/__name__/values"))
    metric_ok = True
    for _, _, a, expr in expected:
        names = metric_names(expr)
        missing = sorted(n for n in names if n not in known)
        if missing:
            metric_ok = False
            print(f"   실패  {a}: 없는 지표 {', '.join(missing)}")
        else:
            print(f"   통과  {a}: {', '.join(sorted(names))}")

    ok = load_ok and metric_ok
    print("\n결과:", "모두 통과" if ok else "실패 항목 있음")
    return 0 if ok else 1


def episodes(times):
    out = []
    for t in sorted(times):
        if out and t - out[-1][1] <= STEP * 2:
            out[-1][1] = t
        else:
            out.append([t, t])
    return [(a, b + STEP) for a, b in out]


def result():
    if not os.path.exists(LOG):
        print("장애 기록이 없음. fault start를 먼저 실행.")
        return 1
    with open(LOG) as f:
        events = [json.loads(line) for line in f if line.strip()]
    armed_at = [i for i, e in enumerate(events) if e["event"] == "armed"]
    if not armed_at:
        print("예약된 장애가 없음")
        return 1
    armed = events[armed_at[-1]]
    on = next((e for e in events[armed_at[-1] + 1:] if e["event"] == "on"), None)
    if on is None:
        print("장애가 아직 켜지지 않음")
        return 1

    fault_on = on["ts"]
    fault_end = armed["end"]
    total = fault_end - fault_on

    print(f"장애 켜짐: {kst(fault_on)} (예약보다 {on.get('late_by_s', 0)}초 늦음)")
    print(f"장애 끝:   {kst(fault_end)} (총 {fmt(total)})")

    covered = api("/api/v1/query_range", {
        "query": 'up{job="prometheus"}',
        "start": fault_on, "end": fault_on + 60, "step": STEP,
    })["result"]
    if not covered:
        print("\n경고: 장애가 켜진 시각의 Prometheus 기록이 없음.")
        print("      기록이 지워졌거나 그때 Prometheus가 꺼져 있었음. 판정할 수 없음.")
        return 1
    if time.time() < fault_end:
        print("\n참고: 장애가 아직 진행 중이라 중간 결과임.")

    series = api("/api/v1/query_range", {
        "query": "ALERTS",
        "start": armed["start"] - 600,
        "end": min(time.time(), fault_end + 300),
        "step": STEP,
    })["result"]
    times = {}
    for s in series:
        key = (s["metric"]["alertname"], s["metric"].get("alertstate"))
        times.setdefault(key, set()).update(float(v[0]) for v in s["values"])

    rules = {r["name"]: r for r in loaded_rules().values()}
    for name in sorted(set(rules) | {k[0] for k in times}):
        fire = episodes(times.get((name, "firing"), ()))
        pend = episodes(times.get((name, "pending"), ()))
        pend_before = [e for e in pend if e[0] < fault_on]
        pend_during = [e for e in pend if fault_on <= e[0] <= fault_end]
        before = [e for e in fire if e[0] < fault_on]
        during = [e for e in fire if fault_on <= e[0] <= fault_end]
        print(f"\n   {name}")
        if before:
            print(f"      장애 전에 울림 {kst(before[0][0])} (오탐 의심)")
        if during:
            first = during[0][0]
            rang = sum(min(b, fault_end) - max(a, fault_on)
                       for a, b in fire if b > fault_on and a < fault_end)
            print(f"      첫 울림: 장애 시작 후 {fmt(first - fault_on)} ({kst(first)})")
            print(f"      울림 {len(during)}회 · 장애 중 울린 시간 {fmt(rang)} / {fmt(total)} ({rang / total:.0%})")
            print(f"      Pending {len(pend_during)}회")
        elif not before:
            hold = rules.get(name, {}).get("duration", 0)
            if pend_during:
                longest = max(b - a for a, b in pend_during)
                print(f"      안 울림 · 장애 중 Pending {len(pend_during)}회, 가장 길게 {fmt(longest)} (for {fmt(hold)}를 못 채움)")
            else:
                print("      안 울림 · 장애 중 Pending 없음 (장애 중 조건이 한 번도 참이 안 됨)")
        if pend_before:
            print(f"      (장애 전 10분: Pending {len(pend_before)}회 — 이전 장애 영향 의심)")
    print("\n오차: 룰 평가 간격(15초) 이내")
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "rules"
    try:
        if cmd == "rules":
            sys.exit(check_rules())
        if cmd == "result":
            sys.exit(result())
        print("사용법: check [rules | result]")
        sys.exit(1)
    except OSError as e:
        print(f"Prometheus에 연결하지 못함: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
