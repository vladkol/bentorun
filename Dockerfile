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

FROM python:3.13-slim-bookworm AS builder

# Install build dependencies for nsjail
# Added g++ as requested by user
RUN apt-get update && apt-get install -y \
    bison \
    flex \
    libprotobuf-dev \
    libnl-route-3-dev \
    protobuf-compiler \
    gcc \
    g++ \
    make \
    git \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Build nsjail
RUN git clone https://github.com/google/nsjail.git /nsjail \
    && cd /nsjail \
    && make \
    && mv nsjail /usr/local/bin/

# Final stage
FROM python:3.13-slim-bookworm

# Copy nsjail binary from builder
COPY --from=builder /usr/local/bin/nsjail /usr/local/bin/nsjail

# Copy uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install runtime dependencies for nsjail (libprotobuf, libnl)
# Combine with user setup to save layers
RUN apt-get update && apt-get install -y \
    libprotobuf32 \
    libnl-route-3-200 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash sandboxuser

# Create template virtual environment
# Copy requirements files first to cache them
WORKDIR /app
COPY env_requirements.txt requirements.txt /app/

# Install template dependencies
# Combine uv commands where possible, but here we have two distinct environments:
# 1. /opt/template_venv for the sandboxed sessions
# 2. System python for the MCP server itself
RUN uv venv /opt/template_venv \
    && uv pip install --python /opt/template_venv -U -r env_requirements.txt \
    # Clear uv cache to save space
    && uv cache clean \
    # Install system dependencies for the app
    && uv pip install --no-cache --system -U -r requirements.txt \
    # Create workspace and set permissions
    && mkdir -p /workspace \
    && chown 1000:1000 /workspace

# Copy application code
COPY src /app/src

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PORT=8080

# Run the MCP Server
CMD ["python3", "src/main.py"]
