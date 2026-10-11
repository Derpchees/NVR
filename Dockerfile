FROM node:22-alpine AS hls-assets
RUN npm install --prefix /tmp/hls hls.js@1.5.17

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    HOME=/home/app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY --from=hls-assets /tmp/hls/node_modules/hls.js/dist/hls.min.js /app/app/static/hls.min.js
RUN mkdir -p /data /home/app && chown -R 10001:10001 /app /data /home/app
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]