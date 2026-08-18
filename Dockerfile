# 3.10 deliberately: kafka-python 2.0.2 breaks on 3.12 (vendored six)
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/srv/backend
WORKDIR /srv

RUN apt-get update -qq && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus
RUN mkdir -p /tmp/prometheus

COPY entrypoint.sh /srv/entrypoint.sh
RUN chmod +x /srv/entrypoint.sh

COPY backend ./backend

ENTRYPOINT ["/srv/entrypoint.sh"]

# one image, three entrypoints — compose picks which
CMD ["gunicorn", "--chdir", "/srv/backend", "main:app", "-b", "0.0.0.0:8081"]
