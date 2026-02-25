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

import os
import subprocess
from google.adk.agents import LlmAgent

from google.adk.tools.mcp_tool.mcp_toolset import (
    McpToolset,
    StreamableHTTPConnectionParams,
)
from google.adk.tools.google_search_agent_tool import (
    create_google_search_agent,
    GoogleSearchAgentTool
)

from dotenv import load_dotenv
load_dotenv()

def get_bentorun_mcp_tools():
    """Gets tools from the BentoRun MCP Server."""
    server_url = os.getenv("BENTORUN_MCP_URL")
    if not server_url:
        raise ValueError("Environment variable BENTORUN_MCP_URL is not set")
    if not server_url.endswith("/mcp"):
        server_url = server_url.strip("/") + "/mcp"
    tools = McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=server_url,
            timeout=30,
        )
    )
    print("MCP Toolset created successfully.")
    return tools

def get_google_auth_token():
    """Gets the Google Cloud authentication token by running `gcloud auth print-access-token` CLI command.
    Value can be used with `execute_python` tool as `CLOUDSDK_AUTH_ACCESS_TOKEN` in `env_variables`.
    """
    return subprocess.check_output(["gcloud", "auth", "print-access-token", "-q"]).decode().strip()

def get_google_cloud_project():
    """Gets the Google Cloud project ID by running `gcloud config get-value project` CLI command.
    Value can be used with `execute_python` tool as `GOOGLE_CLOUD_PROJECT` in `env_variables`.
    """
    return subprocess.check_output(["gcloud", "config", "get-value", "project", "-q"]).decode().strip()

# Define the ADK agent, linking the function as a tool
root_agent = LlmAgent(
    model="gemini-3-flash-preview",
    name='coding_agent',
    instruction="""
You are an agent that can write and execute Python code in a sandbox.
Use Google Search to find data sources and information about useful Python libraries.
Do not come up with your own data. Use Google Search to find them.
Use `execute_python` tool to execute Python code.
""",
    tools=[
        GoogleSearchAgentTool(
            create_google_search_agent("gemini-3-flash-preview")
        ), # for Google Search
        get_bentorun_mcp_tools(), # for BentoRun MCP
        get_google_auth_token, # for Google Cloud authentication
        get_google_cloud_project # for Google Cloud project
    ]
)
