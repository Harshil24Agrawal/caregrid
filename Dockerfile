FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CAREGRID_NO_DOTENV=1 \
    LLM_PROVIDER=mock DEMO_MODE=1 PORT=8000 \
    DATA_DIR=/data/synthetic DB_PATH=/data/caregrid.sqlite BRAIN_DIR=/data/second_brain
WORKDIR /app

COPY requirements-deploy.txt .
RUN pip install --no-cache-dir -r requirements-deploy.txt

COPY caregrid ./caregrid
COPY web ./web
COPY eval ./eval

# non-root user; /data is where the database, the generated data and the compiled Second Brain live (mount a persistent disk here)
RUN useradd --create-home --uid 10001 caregrid && mkdir -p /data && chown -R caregrid /data /app
USER caregrid
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8000'), timeout=4)"

# a fresh /data is seeded by the app itself on first start (reset_demo)
CMD ["sh", "-c", "exec uvicorn caregrid.api:app --host 0.0.0.0 --port ${PORT:-8000} --log-level warning --no-access-log"]
