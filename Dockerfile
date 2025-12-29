FROM python:3.10-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi uvicorn redis apscheduler aiofiles python-multipart jinja2

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]