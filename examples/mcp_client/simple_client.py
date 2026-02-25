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

import asyncio
import datetime
import json
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

# If deployed to Cloud Run, change it to the service address,
# like "https://mcp-bentorun-python-PROJECT_NUMBER.REGION.run.app/mcp"
SERVER_URL = "http://localhost:8080/mcp"

code = """
import socket
print(f"Hello World from {socket.gethostname()}")
"""

async def run_code():
    print(f"Connecting to {SERVER_URL}...")
    try:
        async with streamable_http_client(SERVER_URL) as streams:
            async with ClientSession(
                streams[0],
                streams[1],
                read_timeout_seconds=datetime.timedelta(seconds=30)
            ) as session:
                print("Connected!")
                await session.initialize()

                print(f"Executing code:\n{code}")

                result = await session.call_tool("execute_python", arguments={
                    "code": code
                })

                print("--- Execution Result ---")
                if result and result.content:
                    for i, content in enumerate(result.content):
                        if hasattr(content, "text"):
                            try:
                                content_dict = json.loads(content.text)
                                print(content_dict["stdout"])
                                if content_dict["stderr"]:
                                    print(f"\n\nSTDERR:\n{content_dict['stderr']}")
                            except json.JSONDecodeError:
                                print(f"Content[{i}]: {content.text}")
                        else:
                            print(f"Content[{i}] (non-text): {content}")
                else:
                    print("No content returned.")

    except Exception as e:
        print(f"Error during verification: {e}")

if __name__ == "__main__":
    try:
        if not SERVER_URL:
            raise ValueError("SERVER_URL is not set")
        if not SERVER_URL.endswith("/mcp"):
            SERVER_URL = SERVER_URL.strip("/") + "/mcp"
        asyncio.run(run_code())
    except ImportError:
        print("Please install 'mcp' package via: pip install mcp")
    except Exception as e:
        print(f"Error: {e}")
