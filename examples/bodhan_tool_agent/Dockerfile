# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Stage 1: fetch checksum-pinned static ffmpeg/ffprobe binaries.
# Debian's apt ffmpeg drags in libavdevice -> SDL2/X11/PulseAudio and
# libavfilter -> Mesa -> LLVM (~464 MB). These static builds are ~152 MB
# together and need no OS codec stack in the runtime image.
# ---------------------------------------------------------------------------
FROM debian:bookworm-slim AS ffmpeg-bin
ARG TARGETARCH
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl xz-utils && \
    rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) url="https://johnvansickle.com/ffmpeg/releases/ffmpeg-7.0.2-amd64-static.tar.xz"; \
             sha256="abda8d77ce8309141f83ab8edf0596834087c52467f6badf376a6a2a4c87cf67" ;; \
      arm64) url="https://johnvansickle.com/ffmpeg/releases/ffmpeg-7.0.2-arm64-static.tar.xz"; \
             sha256="f4149bb2b0784e30e99bdda85471c9b5930d3402014e934a5098b41d0f7201b1" ;; \
      *) echo "Unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "${url}" -o /tmp/ffmpeg.tar.xz; \
    echo "${sha256}  /tmp/ffmpeg.tar.xz" | sha256sum -c -; \
    mkdir -p /out; \
    tar -xJf /tmp/ffmpeg.tar.xz --strip-components=1 -C /out --wildcards '*/ffmpeg' '*/ffprobe'

# ---------------------------------------------------------------------------
# Stage 2: runtime image
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm

LABEL maintainer="Thomas Antony" \
      description="Bodhan Tool Server & Autonomous Task Agent"

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Auxiliary media ingest bridge (OGG, MP3, WebM, M4A, FLAC -> PCM WAV)
COPY --from=ffmpeg-bin /out/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-bin /out/ffprobe /usr/local/bin/ffprobe

WORKDIR /app

# Copy the runtime manifest first for Docker layer caching
COPY requirements.txt ./

# Runtime dependencies only; pip cache lives in the BuildKit cache, not the layer
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# Copy only the application source (datasets/outputs are runtime volumes)
COPY src ./src

# Build-time smoke test: FastMCP registry must import cleanly and expose all tools
RUN python -c "from src.bodhan_mcp.server import mcp; assert len(mcp._tool_manager.list_tools()) == 10"

# Default: run the MCP server over stdio
ENTRYPOINT ["python"]
CMD ["-m", "src.bodhan_mcp"]
