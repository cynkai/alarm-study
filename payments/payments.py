import logging
import random
import time

from flask import Flask, jsonify

app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.WARNING)


@app.get("/stock")
def stock():
    time.sleep(random.uniform(0.01, 0.04))
    return jsonify(stock=42)


@app.post("/pay")
def pay():
    time.sleep(random.uniform(0.05, 0.12))
    return jsonify(paid=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000, threaded=True)
