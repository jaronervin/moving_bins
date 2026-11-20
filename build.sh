#!/usr/bin/env bash
# Render runs this on every deploy (see render.yaml).
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input

# First owner login, from DJANGO_SUPERUSER_USERNAME / _EMAIL / _PASSWORD.
# Does nothing once that user exists.
if [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
  python manage.py createsuperuser --no-input 2>/dev/null || true
fi
