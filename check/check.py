import glob
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

PROM = os.environ.get("PROM_URL", "http://prometheus:9090")
RULES_DIR = "/rules"
LOG = "/state/fault.log"
KST = timezone(timedelta(hours=9))
STEP = 5
SELF_SCRAPE_INTERVAL = 15  # prometheus/prometheus.yml


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
    import yaml

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


def metric_names(node):
    if isinstance(node, list):
        for child in node:
            yield from metric_names(child)
    elif isinstance(node, dict):
        if node.get("type") in {"vectorSelector", "matrixSelector"}:
            for matcher in node["matchers"]:
                if matcher["name"] == "__name__" and matcher["type"] == "=" and matcher["value"]:
                    yield matcher["value"]
        else:
            for child in node.values():
                yield from metric_names(child)


def check_rules():
    files = rule_files()
    if not files:
        print("룰 파일이 없음 (prometheus/rules/의 .yml 또는 .yaml)")
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

    expected = [(os.path.basename(f), g, a, e) for f in files for g, a, e in alert_rules(f)]
    if not expected:
        print("\n알람 룰이 없음. rules에 alert 항목을 추가한 뒤 다시 실행.")
        return 1
    reload_prometheus()

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

    print("\n3) 지표 존재 (룰이 이름으로 지정한 지표가 실제로 있나)")
    known = set(api("/api/v1/label/__name__/values"))
    metric_ok = True
    for _, _, a, expr in expected:
        parsed = api("/api/v1/parse_query", {"query": expr})
        names = set(metric_names(parsed))
        missing = sorted(n for n in names if n not in known)
        if missing:
            metric_ok = False
            print(f"   실패  {a}: 없는 지표 {', '.join(missing)}")
        else:
            print(f"   통과  {a}: {', '.join(sorted(names)) or '고정 지표명 없음'}")

    ok = load_ok and metric_ok
    print("\n결과:", "모두 통과" if ok else "실패 항목 있음")
    return 0 if ok else 1


def episodes(times):
    out = []
    for t in sorted(times):
        if out and round(t - out[-1][1], 3) <= STEP:
            out[-1][1] = t
        else:
            out.append([t, t])
    return [(a, b + STEP) for a, b in out]


def has_prometheus_coverage(start, end):
    # A range query can reuse the last sample after scraping has stopped.
    # Require a successful self-scrape within two configured scrape intervals.
    up = 'up{job="prometheus"}'
    query = f"{up} == 1 and (time() - timestamp({up}) <= {2 * SELF_SCRAPE_INTERVAL})"
    series = api("/api/v1/query_range", {
        "query": query, "start": start, "end": end, "step": STEP,
    })["result"]
    healthy = {
        round(float(ts) * 1000)
        for s in series for ts, value in s["values"] if float(value) == 1
    }
    expected = range(round(start * 1000), round(end * 1000), STEP * 1000)
    return all(ts in healthy for ts in expected)


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
    recent = events[armed_at[-1] + 1:]
    first_transition = next((e for e in recent if e["event"] in {"on", "stopped"}), None)
    if first_transition is None:
        print("장애가 아직 켜지지 않음")
        return 1
    if first_transition["event"] == "stopped":
        print("장애가 켜지기 전에 취소됨")
        return 1

    on = first_transition
    fault_on = on["ts"]
    ended = [e["ts"] for e in recent if e["event"] in {"off", "stopped"} and e["ts"] >= fault_on]
    fault_end = min(armed["end"], *ended) if ended else armed["end"]
    now = time.time()
    observed_end = min(now, fault_end)
    total = observed_end - fault_on
    if total <= 0:
        print("집계할 장애 시간이 없음")
        return 1

    print(f"장애 켜짐: {kst(fault_on)} (예약보다 {on.get('late_by_s', 0)}초 늦음)")
    if now < fault_end:
        print(f"중간 결과: {kst(observed_end)}까지 ({fmt(total)} 경과, 종료 예정 {kst(fault_end)})")
    else:
        print(f"장애 끝:   {kst(fault_end)} (총 {fmt(total)})")

    if not has_prometheus_coverage(fault_on, observed_end):
        print("\n경고: 집계 구간에 정상 Prometheus 자체 수집 기록이 빠져 있음.")
        print("      이 구간의 알람 발생 여부와 지속 시간을 판정할 수 없음.")
        return 1

    series = api("/api/v1/query_range", {
        "query": "ALERTS",
        "start": fault_on - 600,
        "end": observed_end,
        "step": STEP,
    })["result"]
    times = {}
    for s in series:
        key = (s["metric"]["alertname"], s["metric"].get("alertstate"))
        for ts, _ in s["values"]:
            timestamp = float(ts)
            if timestamp < observed_end:
                times.setdefault(key, set()).add(timestamp)

    rules = {r["name"]: r for r in loaded_rules().values()}
    for name in sorted(set(rules) | {k[0] for k in times}):
        fire = episodes(times.get((name, "firing"), ()))
        pend = episodes(times.get((name, "pending"), ()))
        pend_before = [e for e in pend if e[0] < fault_on]
        pend_during = [e for e in pend if fault_on <= e[0] < observed_end]
        before = [e for e in fire if e[0] < fault_on]
        continuing = any(b > fault_on for _, b in before)
        during = [e for e in fire if fault_on <= e[0] < observed_end]
        print(f"\n   {name}")
        if before:
            print(f"      장애 전에도 울림 관측 ({kst(before[0][0])})")
        if continuing:
            print("      장애 시작 전부터 이어진 울림 있음")
        if during:
            first = during[0][0]
            print(f"      첫 새 울림: 장애 시작 후 {fmt(first - fault_on)} ({kst(first)})")
        if during or continuing:
            rang = sum(min(b, observed_end) - max(a, fault_on)
                       for a, b in fire if b > fault_on and a < observed_end)
            print(f"      새 울림 {len(during)}회 · 장애 중 울린 시간 {fmt(rang)} / {fmt(total)} ({rang / total:.0%})")
        elif pend_during:
            print("      장애 중 울림 없음 · Pending 기록만 있음")
        else:
            print("      장애 중 울림 없음")
        if pend_during:
            longest = max(min(b, observed_end) - a for a, b in pend_during)
            hold = rules.get(name, {}).get("duration")
            setting = f" (설정 for {fmt(hold)})" if hold is not None else ""
            print(f"      장애 중 시작된 Pending {len(pend_during)}회 · 가장 길게 {fmt(longest)}{setting}")
        if pend_before:
            print(f"      장애 전 10분 동안 Pending {len(pend_before)}회 관측")
    print("\n시간은 5초 간격으로 조회한 근사값이며, 룰 평가 간격의 영향을 받음.")
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
