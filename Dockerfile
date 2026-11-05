FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NIFTY_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app \
    && mkdir /data \
    && chown app:app /data

COPY dashboard.py nifty_option_chain.py openchart_option_chain.py option_rsi.py ./
COPY ui/ ./ui/

USER app
EXPOSE 8765
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/state', timeout=3).close()"
ENTRYPOINT ["python", "dashboard.py"]
CMD ["--host", "0.0.0.0", "--port", "8765"]
