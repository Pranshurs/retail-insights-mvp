FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY samples ./samples
RUN useradd --create-home appuser && mkdir -p data && chown appuser data
USER appuser
EXPOSE 8080
# API_KEY must be provided at run time: docker run -e API_KEY=... -p 8080:8080 retail-insights
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
