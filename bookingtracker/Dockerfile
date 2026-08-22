ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:3.22
FROM $BUILD_FROM
RUN apk add --no-cache python3 py3-pip chromium nss freetype harfbuzz ttf-freefont
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts
COPY run.sh /run.sh
RUN python3 -m pip install --break-system-packages --no-cache-dir .
RUN chmod a+x /run.sh
CMD ["/run.sh"]
