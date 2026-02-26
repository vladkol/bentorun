# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Stage 1: Builder
# Downloads and verifies runsc
FROM python:3.13-slim-bookworm AS builder

RUN apt-get update && apt-get install -y \
    wget \
    curl \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp
RUN set -e; \
    URL=https://storage.googleapis.com/gvisor/releases/release/latest/x86_64; \
    wget ${URL}/runsc ${URL}/runsc.sha512; \
    sha512sum -c runsc.sha512; \
    chmod a+rx runsc

# Stage 2: Final Image
FROM python:3.13-slim-bookworm

# Install runtime dependencies
# Keep curl, wget, iptables, procps, xz-utils as requested
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    iptables \
    procps \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy runsc from builder
COPY --from=builder /tmp/runsc /usr/local/bin/

# Copy uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Create sandbox user
RUN useradd -m -u 1000 -s /bin/bash sandboxuser

# Create template virtual environment
WORKDIR /app
COPY env_requirements.txt requirements.txt /app/

# Install dependencies and setup environment
RUN uv venv /opt/template_venv \
    && uv pip install --python /opt/template_venv -U -r env_requirements.txt \
    && uv cache clean \
    && uv pip install --no-cache --system -U -r requirements.txt \
    && mkdir -p /workspace \
    && chown 1000:1000 /workspace

# Copy application code
COPY src /app/src

# Optimization: Compile bytecode
# Compiles application code, template venv, and system python libraries
RUN python3 -m compileall /app/src /opt/template_venv /usr/local/lib/python3.13

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PORT=8080

# Run the MCP Server
CMD ["python3", "src/main.py"]
