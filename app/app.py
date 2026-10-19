import json
import logging
import os
import random
import sys
import time

import requests
from flask import Flask, g, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

PAYMENTS_URL = os.environ.get("PAYMENTS_URL", "http://payments:9000")
BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5)

app = Flask(__name__)

REQUESTS = Counter(
    "http_requests_total", "HTTP 요청 수", ["endpoint", "method", "status"]
)
LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP 요청 처리 시간(초)", ["endpoint"], buckets=BUCKETS
)
UPSTREAM_REQUESTS = Counter(
    "upstream_requests_total", "외부 서비스 호출 수", ["target", "result"]
)
UPSTREAM_LATENCY = Histogram(
    "upstream_request_duration_seconds", "외부 서비스 응답 시간(초)", ["target"], buckets=BUCKETS
)

log = logging.getLogger("app")
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
log.addHandler(_handler)
log.setLevel(logging.INFO)
logging.getLogger("werkzeug").setLevel(logging.WARNING)


def call_upstream(target, method, path):
    start = time.perf_counter()
    result = "ok"
    try:
        resp = requests.request(method, PAYMENTS_URL + path, timeout=2)
        if resp.status_code >= 500:
            result = "error"
        return resp
    except requests.Timeout:
        result = "timeout"
        return None
    except requests.RequestException:
        result = "error"
        return None
    finally:
        UPSTREAM_REQUESTS.labels(target, result).inc()
        UPSTREAM_LATENCY.labels(target).observe(time.perf_counter() - start)


def upstream_failed(resp):
    return resp is None or resp.status_code >= 500


@app.before_request
def start_timer():
    g.start = time.perf_counter()


@app.after_request
def record(response):
    if request.path == "/metrics":
        return response
    endpoint = request.url_rule.rule if request.url_rule else "unmatched"
    elapsed = time.perf_counter() - g.start
    REQUESTS.labels(endpoint, request.method, str(response.status_code)).inc()
    LATENCY.labels(endpoint).observe(elapsed)
    log.info(json.dumps({
        "ts": round(time.time(), 3),
        "endpoint": endpoint,
        "method": request.method,
        "status": response.status_code,
        "duration_ms": round(elapsed * 1000, 1),
    }))
    return response


def work(lo, hi):
    time.sleep(random.uniform(lo, hi))


@app.get("/")
def home():
    work(0.005, 0.02)
    return jsonify(page="home")


@app.get("/products")
def products():
    work(0.005, 0.02)
    resp = call_upstream("stock", "GET", "/stock")
    if upstream_failed(resp):
        return jsonify(error="stock unavailable"), 502
    return jsonify(items=["a", "b", "c"], stock=resp.json().get("stock"))


@app.post("/checkout")
def checkout():
    work(0.01, 0.03)
    resp = call_upstream("payment", "POST", "/pay")
    if upstream_failed(resp):
        return jsonify(error="payment failed"), 502
    return jsonify(order="ok")


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
