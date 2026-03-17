FROM python:3.11-slim

WORKDIR /app

# Install git (for entrypoint repo cloning) and uv (runs subproject scripts)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && pip install uv \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN chmod +x entrypoint.sh

ENV PYTHONUTF8=1
ENV PYTHONIOENCODING=utf-8

ENTRYPOINT ["./entrypoint.sh"]
