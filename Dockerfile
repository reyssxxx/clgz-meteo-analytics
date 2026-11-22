FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY meteo ./meteo
COPY web ./web
COPY data ./data

EXPOSE 8080
HEALTHCHECK CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://localhost:{os.environ[\"PORT\"]}/api/datasets')"
CMD uvicorn meteo.api:app --host 0.0.0.0 --port $PORT --workers 1
