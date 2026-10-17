ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim-bookworm

WORKDIR /app

# configure uv
ENV UV_LINK_MODE=copy \
    UV_LOCKED=1 \
    UV_COMPILE_BYTECODE=1

# Install dependencies
RUN --mount=from=ghcr.io/astral-sh/uv,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-install-project --no-dev

# Copy Project
COPY README.md /app/README.md
COPY LICENSE /app/LICENSE
COPY pyproject.toml /app/pyproject.toml
COPY uv.lock /app/uv.lock
COPY src/gh_maintainer /app/src/gh_maintainer

# Install project
RUN --mount=from=ghcr.io/astral-sh/uv,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-dev --all-extras

# Environment Variables
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE="1" \
    PYTHONUNBUFFERED="1"


RUN useradd --create-home --uid 10001 maintainer \
    && mkdir -p /data \
    && chown maintainer:maintainer /data
USER maintainer
ENV GH_MAINTAINER_DB=/data/maintainer.sqlite
EXPOSE 8765
ENTRYPOINT ["gh-maintainer"]
CMD ["serve", "--host", "0.0.0.0"]
