FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
WORKDIR /app
COPY requirements.txt requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements-cloud.txt \
    && python -m playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 uptodate \
    && chmod -R a+rX /opt/playwright
COPY backend ./backend
USER uptodate
ENV PORT=8080
CMD ["sh", "-c", "exec python -m uvicorn backend.app:create_app --factory --host 0.0.0.0 --port \"${PORT:-8080}\" --no-access-log"]
