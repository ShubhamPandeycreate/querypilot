# Image for the Hugging Face Spaces mirror (SDK: docker, port 7860).
# Streamlit Community Cloud does not use this file — it installs
# requirements.txt directly.

FROM python:3.12-slim

# Spaces runs containers as uid 1000; installing as that user keeps the
# writable paths (matplotlib cache, uploaded databases) inside $HOME.
RUN useradd --create-home --uid 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/home/user/.cache/matplotlib \
    STREAMLIT_SERVER_PORT=7860 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR $HOME/app

COPY --chown=user requirements.txt ./
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user .streamlit ./.streamlit
COPY --chown=user src ./src
COPY --chown=user app ./app
COPY --chown=user data ./data

EXPOSE 7860
# python:3.12-slim ships no curl, so probe Streamlit's health endpoint directly.
HEALTHCHECK CMD python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:7860/_stcore/health')"

CMD ["streamlit", "run", "app/streamlit_app.py"]
