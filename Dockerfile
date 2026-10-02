# syntax=docker/dockerfile:1
#
# Momentum-plan compilation service.  Standard-library only: the image
# builds with no package installation and no external accounts.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY app/ app/
COPY tests/ tests/
COPY verify/ verify/
COPY Dockerfile README.md ./

RUN useradd --system --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=12 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/health', timeout=2)" || exit 1

CMD ["python", "-m", "app.main"]
