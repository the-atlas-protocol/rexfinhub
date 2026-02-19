#!/bin/bash
# Azure App Service startup script
# Runtime: Python 3.13 on Linux

# Install dependencies
pip install -r requirements.txt

# Initialize database
python -c "from webapp.database import init_db; init_db()"

# Start Gunicorn with Uvicorn workers
gunicorn webapp.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
