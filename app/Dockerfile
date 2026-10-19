FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
ENV PYTHONUNBUFFERED=1
ENV PROMETHEUS_DISABLE_CREATED_SERIES=True
CMD ["python", "app.py"]
