FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./backend/

# Create data directory (overridden by Fly volume mount)
RUN mkdir -p /data

EXPOSE 8080

ENV ENV=production
ENV DATABASE_URL=sqlite:////data/httrace.db

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
