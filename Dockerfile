# syntax=docker/dockerfile:1.7
# ================================================================
# Barbier PDF Server — production image
# Stack : Flask + gunicorn + ReportLab + Pillow + OpenAI
# ================================================================
FROM python:3.12-slim

# Deps système pour :
#  - ReportLab (PDF) : nécessite fonts + image libs
#  - Pillow : libjpeg, libpng, zlib
#  - Puppeteer-like : chromium pour screenshots quartier/carte (si utilisé)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    fonts-liberation \
    fonts-dejavu-core \
    libfreetype6 \
    libpng16-16 \
    libjpeg62-turbo \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copie du code applicatif
COPY . .

# User non-root
RUN useradd --no-create-home --shell /bin/false barbier \
    && chown -R barbier:barbier /app
USER barbier

ENV PORT=8080
ENV NODE_ENV=production
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/ || exit 1

# Timeout 120s pour les générations de dossier PDF (peuvent être longues)
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 120 --log-level info --forwarded-allow-ips='*'"]
