# This file tells Railway / Render how to start the server.
# No changes needed.
web: playwright install chromium && gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120
