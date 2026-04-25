FROM python:3.12-slim

WORKDIR /app

# System dependencies for pdfplumber (poppler)
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# Streamlit config
RUN mkdir -p /root/.streamlit
COPY deploy/streamlit_config.toml /root/.streamlit/config.toml

# Create data directory
RUN mkdir -p /data/deals /data/backups

EXPOSE 8501

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
  CMD curl -f http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "gui/app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.maxUploadSize=100"]
