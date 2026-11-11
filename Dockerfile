FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY agent ./agent
COPY knowledge ./knowledge
RUN useradd -r -u 10001 app && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health/ready').status==200 else 1)"
CMD ["uvicorn", "agent.api:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
