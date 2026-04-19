FROM node:20-slim AS admin-builder

WORKDIR /build/apps/admin_web

COPY apps/admin_web/package*.json ./
RUN npm ci

COPY apps/admin_web/ ./
RUN npm run build


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app
COPY --from=admin-builder /build/src/web/admin_dist /app/src/web/admin_dist
