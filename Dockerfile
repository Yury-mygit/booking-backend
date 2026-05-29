FROM python:3.12-slim

WORKDIR /app

# Зависимости отдельным слоем: при правке кода в `app/` этот слой
# переиспользуется из кеша, pip install не запускается заново.
# Wheels положены офлайн (см. wheels/) — `pypi.org` из РФ недоступен,
# зеркала отстают по версиям пакетов.
COPY pyproject.toml ./
COPY wheels ./wheels/
RUN pip install --no-cache-dir --no-index --find-links ./wheels .

# Исходники и миграции после pip install — их правка не инвалидирует
# слой с зависимостями.
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
