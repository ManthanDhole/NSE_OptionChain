FROM python:3.13-slim AS backend
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app \
    && mkdir -p /data/spool && chown -R app:app /data
COPY services/ ./services/
COPY database/ ./database/
COPY nifty_option_chain.py openchart_option_chain.py option_rsi.py ./
USER app
CMD ["python", "-m", "services.api", "--host", "0.0.0.0"]

FROM python:3.13-slim AS ui
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app
COPY services/__init__.py services/ui.py ./services/
COPY ui/ ./ui/
USER app
EXPOSE 8765
CMD ["python", "-m", "services.ui", "--host", "0.0.0.0"]
