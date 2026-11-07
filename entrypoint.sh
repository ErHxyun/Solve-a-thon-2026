#!/bin/sh
# One image, two roles. MODE=job runs the daily pipeline once; anything else serves the UI.
set -e
if [ "$MODE" = "job" ]; then
  python pipeline.py
else
  streamlit run app/streamlit_app.py --server.port 8000 --server.address 0.0.0.0 --server.headless true
fi
