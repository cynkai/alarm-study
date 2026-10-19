import json
import logging
import random
import sys
import time

from flask import Flask, g, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

app = Flask(__name__)

REQUESTS = Counter(
    "http_requests_total", "HTTP 요청 수", ["endpoint", "method", "status"]
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP 요청 처리 시간(초)",
    ["endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

log = logging.getLogger("app")
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
log.addHandler(_handler)
log.setLevel(logging.INFO)
logging.getLogger("werkzeug").setLevel(logging.WARNING)


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
    work(0.01, 0.05)
    return jsonify(items=["a", "b", "c"])


@app.post("/checkout")
def checkout():
    work(0.05, 0.15)
    return jsonify(order="ok")


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
