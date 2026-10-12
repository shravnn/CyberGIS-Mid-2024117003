# Container image of the Bhoonidhi Chlorophyll WCS: GeoServer (private, 127.0.0.1:8080) behind the WCS-only
# gateway and web UI on port 8000. Built, checked and published by .github/workflows/image.yml.
FROM python:3.11-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/shravnn/CyberGIS-Mid-2024117003" \
      org.opencontainers.image.description="OGC WCS 2.0.1 over ISRO Bhoonidhi EOS-06 OCM-3 chlorophyll-a composites"

RUN apt-get update \
 && apt-get install -y --no-install-recommends openjdk-17-jre-headless curl unzip ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# GeoServer binary distribution (the version the project is tested with)
ARG GEOSERVER_VERSION=2.28.5
RUN curl -fsSL --retry 5 -o /tmp/geoserver.zip \
      "https://sourceforge.net/projects/geoserver/files/GeoServer/${GEOSERVER_VERSION}/geoserver-${GEOSERVER_VERSION}-bin.zip/download" \
 && mkdir -p /tmp/gs && unzip -q /tmp/geoserver.zip -d /tmp/gs && rm /tmp/geoserver.zip \
 && if [ -f /tmp/gs/start.jar ]; then mv /tmp/gs /opt/geoserver; else mv /tmp/gs/*/ /opt/geoserver; fi \
 && test -f /opt/geoserver/start.jar
ENV GEOSERVER_HOME=/opt/geoserver PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN pip install --no-cache-dir --no-deps -e . \
 && useradd -m -u 1000 app && chown -R app:app /app /opt/geoserver
USER app

# Build the GeoTIFF mosaic from the committed cube and configure GeoServer once, here, so a fresh container only
# has to boot GeoServer (configuration is kept in data/geoserver/data_dir inside the image)
RUN python scripts/prep_data.py \
 && python scripts/geoserver.py start \
 && python scripts/geoserver.py stop \
 && rm -f data/geoserver/*.log

EXPOSE 8000
CMD ["python", "scripts/run.py", "--hosted"]
