#!/bin/sh
# One image, three roles:
#   MODE=job        run the daily pipeline once and exit
#   MODE=streamlit  the old Streamlit page (fallback UI)
#   anything else   the web app: FastAPI serving index.html + /api (default)
set -e
if [ "$MODE" = "job" ]; then
  python pipeline.py
elif [ "$MODE" = "streamlit" ]; then
  streamlit run app/streamlit_app.py --server.port 8000 --server.address 0.0.0.0 --server.headless true
else
  uvicorn app.api:app --host 0.0.0.0 --port 8000 --proxy-headers
fi
