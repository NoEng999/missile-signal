FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home monitor && mkdir /data && chown monitor:monitor /data
COPY monitor.py runner.py ./
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
USER monitor
CMD ["python", "runner.py"]
