import os
import random
import threading
import time
import urllib.request

BASE = os.environ.get("TARGET", "http://app:8000")
RPS = float(os.environ.get("RPS", "5"))
ROUTES = [("GET", "/"), ("GET", "/products"), ("POST", "/checkout")]
WEIGHTS = [50, 35, 15]


def hit(method, path):
    data = b"" if method == "POST" else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def main():
    time.sleep(5)
    interval = 1.0 / RPS
    print(f"loadgen: {BASE} 에 초당 {RPS}건", flush=True)
    while True:
        method, path = random.choices(ROUTES, weights=WEIGHTS)[0]
        threading.Thread(target=hit, args=(method, path), daemon=True).start()
        time.sleep(interval)


if __name__ == "__main__":
    main()
