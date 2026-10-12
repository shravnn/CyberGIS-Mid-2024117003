# Hosted deployment of the Bhoonidhi Chlorophyll WCS as a Hugging Face Docker Space.
# The image fetches GeoServer and this project's GitHub repository, prepares the coverage, and runs
# GeoServer (127.0.0.1:8080, private) behind the WCS-only gateway and web UI on port 7860 (public).
FROM python:3.11-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends openjdk-17-jre-headless curl unzip git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# GeoServer binary distribution (the same version the project is tested with)
ARG GEOSERVER_VERSION=2.28.5
RUN curl -fsSL -o /tmp/geoserver.zip \
      "https://sourceforge.net/projects/geoserver/files/GeoServer/${GEOSERVER_VERSION}/geoserver-${GEOSERVER_VERSION}-bin.zip/download" \
 && mkdir -p /tmp/gs && unzip -q /tmp/geoserver.zip -d /tmp/gs && rm /tmp/geoserver.zip \
 && if [ -f /tmp/gs/start.jar ]; then mv /tmp/gs /opt/geoserver; else mv /tmp/gs/*/ /opt/geoserver; fi \
 && test -f /opt/geoserver/start.jar
ENV GEOSERVER_HOME=/opt/geoserver

# The project itself
ARG REPO=https://github.com/shravnn/CyberGIS-Mid-2024117003.git
ARG REF=main
RUN git clone --depth 1 --branch "${REF}" "${REPO}" /app
WORKDIR /app
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir --no-deps -e .

# Build the GeoTIFF mosaic from the committed data cube now, so the container starts quickly
RUN python scripts/prep_data.py

# Hugging Face runs the container as user 1000
RUN useradd -m -u 1000 user && chown -R user:user /app /opt/geoserver
USER user
ENV HOME=/home/user PYTHONUNBUFFERED=1

EXPOSE 7860
# SPACE_HOST is set by Hugging Face (e.g. "name-space.hf.space"); it becomes the address in the WCS capabilities
CMD python scripts/run.py --host 0.0.0.0 --port 7860 --public-url "https://${SPACE_HOST:-localhost:7860}"
