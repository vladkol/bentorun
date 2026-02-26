# 🍱 BentoRun Python MCP

A Model Context Protocol (MCP) server that provides a secure isolated environment for executing Python code.

## Overview

BentoRun Python MCP allows AI Agents to execute Python code safely inside lightweight sandboxes in [Google Cloud Run](https://cloud.google.com/run). It uses a rigorous sandboxing approach to ensure that executed code cannot interfere with the host system or the MCP server itself, while still providing necessary access to the internet and specific libraries.

Each MCP session is isolated in a separate sandbox.

![BentoRun Python MCP Diagram](images/mcp-bentorun.png)

## Sandbox with gVisor

We use [**gVisor**](https://gvisor.dev/), an application kernel for containers, to create a secure environment for every code execution. It provides an additional layer of isolation between running applications and the host operating system.

gVisor was built by Google to provide strong isolation for multi-tenant workloads. It implements a substantial portion of the Linux system surface, allowing untrusted code to run safely without direct access to the host kernel.

gVisor uses the following features to create a secure isolated execution environment:

### 1. Application Kernel (The "Sentry")
gVisor intercepts application system calls and handles them in a user-space kernel called the Sentry. The Sentry implements the Linux kernel API, but is written in Go and memory-safe. This means:
* **Attack Surface Reduction**: The application doesn't talk directly to the host kernel.
* **Defense in Depth**: Even if the application compromises the Sentry, it is still isolated from the host.

### 2. Filesystem Proxy (The "Gofer")
File operations are proxied through a separate process called the Gofer. This ensures that the application only accesses files it is explicitly allowed to see, enforcing strict isolation boundaries.

### 3. OCI Compatibility
gVisor integrates seamlessly with OCI (Open Container Initiative) runtimes. We use `runsc` (the gVisor runtime) to spin up lightweight, ephemeral sandboxes for each execution, managing resources via standard container controls.

## Sandbox Environment Template

We use a sandbox environment template to ensure that executed code runs in a consistent and secure environment. The template is defined in `env_requirements.txt` and includes a list of packages that are installed in the sandbox environment.

If you expect your code to often require certain dependencies, customize the sandbox environment by modifying `env_requirements.txt` file
and by adding additional dependencies to the `Dockerfile`.

Depending on complexity of the code, you may also need to extend the list of system mounts by modifying `KNOWN_SYSTEM_MOUNTS` list in `src/sandbox.py` file.

## MCP Server Tools

BentoRun MCP exposes one tool to AI Agents: **execute_python**

This tool executes Python code in a sandboxed environment. Its parameters are:

- **code**: The Python code to execute.
- **packages**: A list of additional PyPI packages to install in the sandbox environment before executing the code.
- **env_variables**: A dictionary of environment variables to set in the sandbox environment.

    For code using Google Cloud libraries, you can pass credentials in 2 ways:
    - By passing `CLOUDSDK_AUTH_ACCESS_TOKEN` environment variable
    with the access token from `gcloud auth print-access-token` CLI command.
    - By passing `GOOGLE_APPLICATION_CREDENTIALS` environment variable with the content of the service account key json file.

The tool's instructions explicitly state that any output files should be written to `output` subdirectory.
Such files are processed and returned from `execute_python` tool as [Embedded Resources](https://modelcontextprotocol.io/specification/2025-06-18/server/tools#embedded-resources) or [Images](https://modelcontextprotocol.io/specification/2025-06-18/server/tools#image-content). See [`examples/adk/adk_sandbox/agent.py`](examples/adk/adk_sandbox/agent.py) for example of how to use it.

Those files and installed packages are preserved between executions within the same session.
Sessions are automatically cleaned up after 10 minutes of inactivity.

### Key Isolation Features

1.  **Filesystem Isolation**:
    -   The sandbox runs with a read-only view of the system's root filesystem.
    -   Only specific directories necessary for python execution are mounted (`/bin`, `/lib`, `/usr`, etc.).
    -   A temporary workspace is bind-mounted as the *only* writable location.
    -   This prevents the executed code from modifying system files, installing persistent malware, or accessing the MCP server's sensitive files.

2.  **Resource Limits** (Enforced via OCI/cgroups):
    -   **Memory**: Limited to 512MB by default via `runsc` configuration to prevent OOM attacks or leaks.
    -   **CPU Time**: Execution time is strictly limited (default 60s) via `RLIMIT_CPU` to prevent infinite loops from locking up resources.

3.  **Network Access**:
    -   The sandbox can share the network namespace with the host container (configurable).
    -   This allows the code to fetch data from the internet (e.g., pip install packages, make API calls) which is often required for useful tasks.
    -   *Security Note*: Since we are running inside a container (Cloud Run) that already has its own network policies, this is an acceptable trade-off. In Cloud Run, we make sure to use a "permissionless" Service Account, so the code cannot access any Google Cloud resources unless given an explicit authentication token as part of the code execution request.

4.  **User Isolation**:
    -   Code runs as a dedicated non-privileged user (`sandboxuser`, uid:1000) inside the sandbox which makes it even harder to escape the sandbox.

Each MCP session is executed in a separate, ephemeral gVisor sandbox.

## Code Execution Flow

1. **Session Initialization**: When a client connects, a new session is created.
2. **Package Installation**: If packages are requested, they are installed in the sandbox environment.
3. **Code Execution**: The code is executed in the sandbox environment.
4. **Output**: The output is returned to the client as a JSON object with `stdout` and `stderr` fields
  that contain respective outputs from the execution.

## Deploying and Running the BentoRun MCP Server

> You need a [Google Cloud Project](https://console.cloud.google.com/) with billing enabled to deploy the MCP server.

1. Make sure you have `gcloud` CLI installed and configured.
2. Authenticate with Google Cloud:

```bash
gcloud auth login --update-adc
```

3. Copy `env.template` to `.env`, and set `GOOGLE_CLOUD_PROJECT` to your Google Cloud Project Id.

4. Deploy the MCP server:

```bash
./deploy.sh
```

The script will deploy the MCP server to Cloud Run as `mcp-bentorun-python` service and provide the URL.

## Examples

### Simple Client

* Edit `examples/mcp_client/simple_client.py` to change the `SERVER_URL` to your deployed service address.
* Run the client:

```bash
python3 examples/mcp_client/simple_client.py
```

### ADK Agent Example

* Create `.env` in `examples/adk/` as a copy of `.env.example`
* Set environment variables in `.env` file:
    * `GOOGLE_GENAI_USE_VERTEXAI` - set to "true" to use Vertex AI.
    * `GOOGLE_CLOUD_PROJECT` - set to your Google Cloud project Id.
    * `GOOGLE_CLOUD_LOCATION` - Gemini API endpoint location. Keep it `global` if you don't have specific requirements.
    * `BENTORUN_MCP_URL` - set to your BentoRun MCP URL (e.g. `https://mcp-bentorun-python-PROJECT_NUMBER.REGION.run.app/mcp`)
    * `GEMINI_API_KEY` - if you prefer using Gemini API key, set `GOOGLE_GENAI_USE_VERTEXAI` to "false" and `GEMINI_API_KEY` to your Gemini API key.
* Install ADK requirements:

```bash
pip install -r examples/adk/adk_sandbox/requirements.txt
```

* Run the agent:

```bash
adk web examples/adk
```

* Open [http://127.0.0.1:8000](http://127.0.0.1:8000) to interact with the agent.

## Using BentoRun MCP with Gemini CLI

Add MCP Server to [Gemini CLI](https://geminicli.com/docs/tools/mcp-server/):

- Manually in `settings.json`:

```json
{
  "mcpServers": {
    "bentorun-mcp": {
      "url": "MCP_SERVER_URL"
    }
  }
}
```

**or** with CLI:

```bash
gemini mcp add --transport http bentorun-mcp MCP_SERVER_URL
```

> Replace `MCP_SERVER_URL` with your BentoRun MCP URL (e.g. `https://mcp-bentorun-python-PROJECT_NUMBER.REGION.run.app/mcp`)

## Disclaimer

This is not an officially supported Google product. This project is not eligible for the [Google Open Source Software Vulnerability Rewards Program](https://bughunters.google.com/open-source-security).
