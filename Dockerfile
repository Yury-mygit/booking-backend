FROM python:3.12-slim

WORKDIR /app

# hatchling builds the local `booking-app` package, so app/ must be
# present before pip install. Deps resolve from pypi (egress OK on new
# host; the old offline wheels/ crutch was for the RF-blocked host).
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic ./alembic

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
