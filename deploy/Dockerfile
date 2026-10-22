FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home portal
COPY . .
RUN mkdir -p /var/log/portal /var/log/management && chown -R portal:portal /app /var/log/portal /var/log/management
USER portal
CMD ["uvicorn", "portal.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
