FROM python:3.14-slim

ARG REQUIREMENTS_FILE=requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-gpu.txt ./
RUN pip install --no-cache-dir -r ${REQUIREMENTS_FILE}

COPY server.py rag.py anglerfish_idle.gif content-packs.json ./
COPY docfish ./docfish
COPY static ./static

ENV HOST=0.0.0.0 PORT=8080
EXPOSE 8080
CMD ["python", "server.py"]
