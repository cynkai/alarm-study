FROM prom/prometheus:v3.5.0 AS prom

FROM python:3.12-slim
COPY --from=prom /bin/promtool /usr/local/bin/promtool
WORKDIR /app
RUN pip install --no-cache-dir pyyaml==6.0.2
COPY check.py .
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/app/check.py"]
