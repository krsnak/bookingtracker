ARG BUILD_FROM=debian:12-slim
FROM $BUILD_FROM
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv chromium fonts-dejavu-core ca-certificates \
    xvfb openbox x11vnc novnc websockify xauth \
    && test -f /usr/share/novnc/vnc.html \
    && test -d /usr/share/novnc/app \
    && test -d /usr/share/novnc/core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts
COPY run.sh /run.sh
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir .
RUN chmod a+x /run.sh
CMD ["/run.sh"]
