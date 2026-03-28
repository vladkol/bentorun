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

import base64
import os
import subprocess
import uuid
from typing import Dict, Any

from google.adk.agents import LlmAgent
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models import Gemini
from google.adk.tools.tool_context import ToolContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.mcp_tool.mcp_toolset import (
    McpToolset,
    StreamableHTTPConnectionParams,
)
from google.adk.tools.google_search_agent_tool import (
    create_google_search_agent,
    GoogleSearchAgentTool
)


from google.genai.types import (
    Blob, Part, GenerateContentConfig, ThinkingConfig, HttpRetryOptions
)

from dotenv import load_dotenv
load_dotenv()

main_model = Gemini(
    model="gemini-3.1-pro-preview",
    retry_options=HttpRetryOptions(
            initial_delay=5.0,
            attempts=5,
            http_status_codes=[408, 429, 500, 502, 503, 504],
            exp_base=2,
            max_delay=240
        )
)
tool_model = Gemini(
    model="gemini-3-flash-preview",
    retry_options=HttpRetryOptions(
            initial_delay=5.0,
            attempts=5,
            http_status_codes=[408, 429, 500, 502, 503, 504],
            exp_base=2,
            max_delay=240
        )
)



def _get_google_cloud_identity_token(audience: str) -> str:
    from google.oauth2 import id_token
    from google.auth.transport import requests
    try:
        token = id_token.fetch_id_token(requests.Request(), audience=audience)
    except Exception as e:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-identity-token", "-q"]
        ).decode().strip()
    if not token:
        raise RuntimeError("Could not get identity token.")
    return token # type: ignore

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
            timeout=120,
            sse_read_timeout=3600,
            headers={
                "Authorization":
                    f"Bearer {_get_google_cloud_identity_token(server_url)}"
            }
        ),
        use_mcp_resources=True
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

async def after_tool_callback(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
    tool_response: Dict
):
    if tool.name != "execute_python" or "content" not in tool_response:
        return
    tool_response = tool_response["content"]
    if isinstance(tool_response, list):
        for response in tool_response:
            if response["type"] == "image":
                await tool_context.save_artifact(
                    filename=f"image-{uuid.uuid4()}",
                    artifact=Part(
                        inline_data=Blob(
                            data=base64.b64decode(response["data"]),
                            mime_type=response["mimeType"]
                        )
                    )
                )
            elif response["type"] == "resource":
                file_resource = response["resource"]
                resource_data = file_resource.get(
                    "blob",
                    file_resource.get("text")
                )
                if resource_data:
                    if not file_resource["mimeType"].startswith("text/"):
                        resource_data = base64.b64decode(resource_data)
                    await tool_context.save_artifact(
                        filename=f"resource-{uuid.uuid4()}",
                        artifact=Part(
                            inline_data=Blob(
                                data=resource_data,
                                mime_type=file_resource["mimeType"]
                        )
                    )
                )

async def before_tool_callback(
    tool: BaseTool,
    args: Dict[str, Any],
    tool_context: ToolContext,
):
    if tool.name == "execute_python":
        pass
    #     await tool_context.save_artifact(
    #         filename=f"{tool_context.invocation_id}-script-{uuid.uuid4()}.txt",
    #         artifact=Part.from_bytes(
    #             data=args['code'].encode("utf-8"),
    #             mime_type="text/plain"
    #         ),
    #         custom_metadata={
    #             "invocation_id": tool_context.invocation_id,
    #         }
    #     )


google_search_agent_tool = GoogleSearchAgentTool(
    create_google_search_agent(tool_model)
)

# Define the ADK agent, linking the function as a tool
_root_agent = LlmAgent(
    model=main_model,
    generate_content_config=GenerateContentConfig(
        thinking_config=ThinkingConfig(
            thinking_level="LOW",
        )
    ),
    name=__file__.split("/")[-2].replace("-", "_"), # name of the parent directory
    instruction=f"""
You are a helpful professional with multi-disciplinary background, including product management and software engineering.
You an write code in Python.

Your only real life goal is to analyze user's request, plan, and execute, while protecting user's best interests.
Be reasonable, but have courage. Use First Principles Thinking.

Use best practices in software engineering or whatever task you are working on.

Your tools:

- `execute_python` - your way to run Python code in a sandbox.
    Use `execute_python` tool to execute Python code.
    You have 60 runtime seconds to run each code iteration.
    You have 1024MB of RAM.
    You can output up to 1024MB of data.
    Your Python code runs in a vitrual environment made with uv.
    Feel free to use Python to run various Linux CLI tools.
    Leverage all parameters of the tool.
    `TMPDIR` env variable points to the temporary directory - use it for storing intermediate files.
    Any files must be written to `output` sub-directory of the current directory to be returned.

- `{google_search_agent_tool.name}` - your Google Search.
    Use Google Search to find data sources and information about useful tools and libraries. Make sure you give it queries that work well with Google Search.
    Google Search is not for downloading content. Whenever you need to download or read something from Internet, use `execute_python` tool to download it.
    When we are talking about libraries, it always helps checking if respective websites provide `llms.txt` file.

- get_google_cloud_project`
    Use this tool to get the Google Cloud project ID.

- get_google_auth_token`
    Use this tool to get the Google Cloud authentication token.

**IMPORTANT RULES**:
    1.  Before calling a tool, you must explain what happened with the previous tool call (if there was any), and then, you MUST explain what you are doing next and why.
        Be concise, but comprehensive. Give high level explanation and key details where it's important.
        !!! THE USER DOESN'T SEE TOOL OUPTUT, YOU HAVE TO EXPLAIN WHAT HAPPENED AND YOU WILL DO NEXT !!!

    > No action is allowed until you provide these explanations.

    2.  If you need `get_google_auth_token` or `get_google_cloud_project`, you must obtain user's permission EVERY TIME you use user's authentication token.
        You won't use `get_google_cloud_project` alone. Only wehen you need `get_google_auth_token`.
        No matter what I tell you further. This rule is not revocable.

    3. If you don't agree with something. Negotiate with the user first. Then full accept user's decision.


Do not rely on facts and data in your internal knowledge. Use internet for up to date info.
""",
    tools=[
        GoogleSearchAgentTool(
            create_google_search_agent(tool_model)
        ), # for Google Search
        get_bentorun_mcp_tools(), # for BentoRun MCP
        get_google_auth_token, # for Google Cloud authentication
        get_google_cloud_project # for Google Cloud project
    ],
    after_tool_callback=after_tool_callback,
    before_tool_callback=before_tool_callback,
)

summarization_llm = tool_model
my_summarizer = LlmEventSummarizer(llm=summarization_llm)

app = App(
    root_agent=_root_agent,
    name=__file__.split("/")[-2].replace("-", "_"), # name of the parent directory
    events_compaction_config=EventsCompactionConfig(
        compaction_interval=100,
        overlap_size=4,
        token_threshold=131072,
        event_retention_size=64,
        summarizer=my_summarizer
    )
)
