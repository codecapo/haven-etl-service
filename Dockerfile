# Haven ETL worker image. Runs the CLI as a batch job (run-to-completion) on
# Fly.io Machines in London (lhr). Python 3.12 — 3.14's broken pyexpat aside,
# slim is enough since all deps ship binary/pure-python wheels (no build tools).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HAVEN_DATA_DIR=/data

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY haven_etl ./haven_etl
COPY tests ./tests

# Persistent volume holds the OS Open UPRN Parquet + working artifacts between runs.
VOLUME ["/data"]

# CLI is the entrypoint, so `fly machine run <app> os-uprn --zip ...` just works.
ENTRYPOINT ["python", "-m", "haven_etl"]
CMD ["--help"]
