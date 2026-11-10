FROM python:3.12-slim

# fpcalc (Chromaprint) + ffmpeg for Audio Scanner on EthioDeploy
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromaprint-tools \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
