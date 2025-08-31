FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev ca-certificates curl \
    build-essential gcc \
 && rm -rf /var/lib/apt/lists/*

# Create venv and use absolute paths explicitly
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

# Leverage cache: copy only requirements first
COPY requirements.txt /app/requirements.txt

# Use venv pip explicitly
RUN /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install -r /app/requirements.txt

# Now copy your code
COPY . /app

EXPOSE 8080
# Use venv uvicorn explicitly
CMD ["/opt/venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
