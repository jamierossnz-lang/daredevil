# ── Tailwind CSS ─────────────────────────────────────────────────────────────
# Compiled once here from the classes used in templates/ and apps/, instead of
# by the Play CDN in every visitor's browser on every page load.
FROM python:3.13-slim AS css
ARG TARGETARCH
ARG TAILWIND_VERSION=v3.4.17
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN case "${TARGETARCH:-amd64}" in arm64) arch=arm64 ;; *) arch=x64 ;; esac \
    && curl -fsSL -o /usr/local/bin/tailwindcss \
       "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-${arch}" \
    && chmod +x /usr/local/bin/tailwindcss
WORKDIR /src
COPY static/ static/
COPY templates/ templates/
COPY apps/ apps/
RUN tailwindcss -c static/tailwind.config.js -i static/css/tailwind.src.css -o /out/tailwind.css --minify

# ── App ──────────────────────────────────────────────────────────────────────
FROM python:3.13-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer-cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code, plus the compiled stylesheet
COPY . .
COPY --from=css /out/tailwind.css static/css/tailwind.css

EXPOSE 8000

ENTRYPOINT ["sh", "docker-entrypoint.sh"]
# gthread (not sync) workers: each open Queue page holds a long-lived SSE
# connection (~55s, self-renewing) — sync workers would pin a whole
# process per open tab and starve normal requests. Sized for a few
# concurrent tabs with headroom left for actual page loads.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "8", "--worker-class", "gthread", "--timeout", "120"]
