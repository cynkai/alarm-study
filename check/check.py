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
KEYWORDS = {"by", "without", "on", "ignoring", "group_left", "group_right",
            "bool", "offset", "and", "or", "unless", "inf", "nan"}


def kst(ts):
    return datetime.fromtimestamp(ts, KST).strftime("%H:%M:%S")


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
    series = api("/api/v1/query_range", {
        "query": 'ALERTS{alertstate="firing"}',
        "start": armed["start"] - 600,
        "end": min(time.time(), armed["end"] + 300),
        "step": 5,
    })["result"]

    fired = {}
    for s in series:
        name = s["metric"]["alertname"]
        first = float(s["values"][0][0])
        fired[name] = min(first, fired.get(name, first))

    print(f"장애 켜짐: {kst(fault_on)} (예약보다 {on.get('late_by_s', 0)}초 늦음)")
    print(f"장애 끝:   {kst(armed['end'])}\n")
    names = sorted({r["name"] for r in loaded_rules().values()} | set(fired))
    for name in names:
        if name not in fired:
            print(f"   {name}: 안 울림")
            continue
        t = fired[name]
        if t < fault_on:
            print(f"   {name}: 장애 전에 울림 {kst(t)} (오탐 의심)")
        else:
            m, s = divmod(int(round(t - fault_on)), 60)
            print(f"   {name}: 울림, 장애 시작 후 {m}분 {s}초 ({kst(t)})")
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
