FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY apps apps
COPY packages packages
RUN pip install --no-cache-dir .
RUN addgroup --system app && adduser --system --ingroup app app
USER app
EXPOSE 8000
CMD ["uvicorn", "scalpforge_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
