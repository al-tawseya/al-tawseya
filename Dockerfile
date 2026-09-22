FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py product_search.py gunicorn.conf.py ./

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["gunicorn", "app:application", "--bind", "0.0.0.0:8000", "--conf", "gunicorn.conf.py"]
