# Matches the interpreter this project has been built and tested against
# throughout (python3.9 in the local .venv) - deliberately not jumping to
# a newer Python here, to avoid introducing a new compatibility variable
# right before going live.
FROM python:3.9-slim

WORKDIR /app

# psycopg-binary (already in requirements.txt) ships its own compiled
# libpq - no system packages (libpq-dev, build-essential) needed to
# install or run this image.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
