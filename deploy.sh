#!/bin/bash
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

set -e

CRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "${SCRIPT_DIR}"

# Load .env file if it exists.
# Optionally, use a custom .env file path via ENV_FILE environment variable.
if [[ "$ENV_FILE" == "" ]]; then
    export ENV_FILE=".env"
fi
if [ -f "$ENV_FILE" ]; then
    source "$ENV_FILE"
else
    # Warn the user that the .env file is not found, unless we are running in a Cloud Build pipeline.
    echo "⚠️ WARNING: $ENV_FILE file not found. Using current or default values."
fi

# If GOOGLE_CLOUD_PROJECT is not defined, get current project from gcloud CLI
if [[ "${GOOGLE_CLOUD_PROJECT}" == "" ]]; then
    GOOGLE_CLOUD_PROJECT=$(gcloud config get-value project -q)
fi
if [[ "${GOOGLE_CLOUD_PROJECT}" == "" ]]; then
    echo "ERROR: Run 'gcloud config set project' command to set active project, or set GOOGLE_CLOUD_PROJECT environment variable."
    exit 1
fi

# GOOGLE_CLOUD_REGION is the region where Cloud Run services will be deployed.
# GOOGLE_CLOUD_LOCATION is a cloud location used for Gemini API calls, it may be a region, and may be "global".
# If GOOGLE_CLOUD_REGION is not defined, it will be the same as GOOGLE_CLOUD_LOCATION unless GOOGLE_CLOUD_LOCATION is "global".
# In that case, the region will be assigned to the default Cloud Run region configured with gcloud CLI.
# If none is configured, "us-central1" is the default value.
if [[ "${GOOGLE_CLOUD_REGION}" == "" ]]; then
    GOOGLE_CLOUD_REGION="${GOOGLE_CLOUD_LOCATION}"
fi
if [[ "${GOOGLE_CLOUD_REGION}" == "global" ]]; then
    echo "GOOGLE_CLOUD_REGION is set to 'global'. Getting a default location for Cloud Run."
    GOOGLE_CLOUD_REGION=""
fi
if [[ "${GOOGLE_CLOUD_REGION}" == "" ]]; then
    GOOGLE_CLOUD_REGION=$(gcloud config get-value run/region -q)
    if [[ "${GOOGLE_CLOUD_REGION}" == "" ]]; then
        GOOGLE_CLOUD_REGION="us-central1"
        echo "WARNING: Cannot get a configured Cloud Run region. Defaulting to ${GOOGLE_CLOUD_REGION}."
    fi
fi
# If GOOGLE_CLOUD_LOCATION is empty, "global" will be used.
if [[ "${GOOGLE_CLOUD_LOCATION}" == "" ]]; then
    GOOGLE_CLOUD_LOCATION="global"
fi

if [[ "${CLOUD_RUN_SERVICE_ACCOUNT}" == "" ]]; then
    CLOUD_RUN_SERVICE_ACCOUNT="no-permissions-sandbox"
fi
# If `@` not in CLOUD_RUN_SERVICE_ACCOUNT, resove to email
if [[ "${CLOUD_RUN_SERVICE_ACCOUNT}" != "*@*" ]]; then
    CLOUD_RUN_SERVICE_ACCOUNT="${CLOUD_RUN_SERVICE_ACCOUNT}@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
fi
# Get part before @
CLOUD_RUN_SERVICE_ACCOUNT_NAME="${CLOUD_RUN_SERVICE_ACCOUNT%@*}"

# Create service account if not exists
if ! gcloud iam service-accounts describe "${CLOUD_RUN_SERVICE_ACCOUNT}" --project "${GOOGLE_CLOUD_PROJECT}" &> /dev/null; then
    echo "Creating service account ${CLOUD_RUN_SERVICE_ACCOUNT} for Cloud Run."
    gcloud iam service-accounts create ${CLOUD_RUN_SERVICE_ACCOUNT_NAME} --project "${GOOGLE_CLOUD_PROJECT}" --display-name "Sandbox MCP Service Account"
    sleep 10
fi

echo "Using project ${GOOGLE_CLOUD_PROJECT}."
echo "Using Cloud Run region ${GOOGLE_CLOUD_REGION}."

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION}"
export GOOGLE_CLOUD_REGION="${GOOGLE_CLOUD_REGION}"

SERVICE_NAME="mcp-bentorun-python"

# Deploy to Cloud Run
gcloud run deploy $SERVICE_NAME \
    --source . \
    --project "${GOOGLE_CLOUD_PROJECT}" \
    --region $GOOGLE_CLOUD_REGION \
    --service-account "${CLOUD_RUN_SERVICE_ACCOUNT}" \
    --no-allow-unauthenticated \
    --execution-environment gen2 \
    --session-affinity \
    --memory 16Gi \
    --cpu 8 \
    --concurrency 32 \
    --timeout 10m \
    --port 8080 \
    --set-env-vars="GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT}" \
    --set-env-vars="GOOGLE_CLOUD_REGION=${GOOGLE_CLOUD_REGION}"

SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --project $GOOGLE_CLOUD_PROJECT --region $GOOGLE_CLOUD_REGION --format 'value(status.url)')
echo "Deployment complete. MCP Server URL: ${SERVICE_URL}/mcp"
