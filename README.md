# 🍱 BentoRun Python MCP

A Model Context Protocol (MCP) server that provides a secure isolated environment for executing Python code.

## Overview

BentoRun Python MCP allows AI Agents to execute Python code safely inside lightweight sandboxes in Docker or [Google Cloud Run](https://cloud.google.com/run). It uses a rigorous sandboxing approach to ensure that executed code cannot interfere with the host system or the MCP server itself, while still providing necessary access to the internet and specific libraries.

Each MCP session is isolated in a separate sandbox.

![BentoRun Python MCP Diagram](images/mcp-bentorun.png)

## Sandbox with NsJail

We use [**NsJail**](https://nsjail.dev/), a lightweight process isolation tool by Google, to create a secure environment for every code execution. It utilizes Linux namespaces, cgroups, rlimits and seccomp-bpf syscall filters, leveraging the Kafel BPF language for enhanced security.

NsJail was originally built by Google's security team for Capture The Flag (CTF) competitions, where the entire goal is to let strangers run malicious code on your server without them breaking out.

NsJail uses the following Linux features to create a secure isolated execution environment:

### 1. Namespaces (The "What I See" Filter)

Namespaces tell a process that the world is much smaller than it actually is. NsJail uses these to:

* **PID isolation:** The process thinks it’s the only thing running (it sees itself as PID 1) and cannot see or "kill" other processes on your machine.
* **Mount isolation:** You can give the process a "fake" root directory. It won't even know your `/home` or `/etc` folders exist unless you explicitly "mount" them into the jail.
* **Network isolation:** You can completely disconnect the process from the internet or give it a dedicated virtual network interface. In our case, we share the network namespace with the host container (`--disable_clone_newnet`) to allow code to use the internet.

### 2. Cgroups (The "How Much I Can Take" Limiter)

Control Groups (cgroups) manage hardware resources. Nsjail lets you set hard ceilings so a buggy or malicious script doesn't crash your host:

* **Memory:** "You can only use 512MB of RAM."
* **CPU:** "You can only use 10% of one core."
* **PIDs:** "You can only spawn 5 child processes" (prevents "fork bombs").

### 3. Seccomp-bpf (The "What I'm Allowed to Ask" Guard)

This is the most powerful security feature. Every time a program wants to do something (read a file, open a socket, get the time), it asks the Linux kernel via a **syscall**.

* Nsjail allows you to create an allowlist of syscalls: "This process can `read` and `write`, but it is forbidden from using `execve` (running other programs) or `socket` (opening network connections)."

## Sandbox Environment Template

We use a sandbox environment template to ensure that executed code runs in a consistent and secure environment. The template is defined in `env_requirements.txt` and includes a list of packages that are installed in the sandbox environment.

If you expect your code to often require certain dependencies, customize the sandbox environment by modifying `env_requirements.txt` file
and by adding additional dependencies to the `Dockerfile`.

Depending on complexity of the code, you may also need to extend the list of system mounts by modifying `KNOWN_SYSTEM_MOUNTS` list in `src/nsjail.py` file.

## MCP Server Tools

BentoRun MCP exposes one tool to AI Agents: **execute_python**

This tool executes Python code in a sandboxed environment. Its parameters are:

- **code**: The Python code to execute.
- **packages**: A list of additional PyPI packages to install in the sandbox environment before executing the code.
- **env_variables**: A dictionary of environment variables to set in the sandbox environment.
    > For code using Google Cloud libraries, you can pass credentials in 2 ways:
    - By passing `CLOUDSDK_AUTH_ACCESS_TOKEN` environment variable
    with the access token from `gcloud auth print-access-token` CLI command.
    - By passing `GOOGLE_APPLICATION_CREDENTIALS` environment variable with the content of the service account key json file.

### Key Isolation Features

1.  **Filesystem Isolation**:
    -   The sandbox runs with a read-only view of the system's root filesystem.
    -   Only specific directories necessary for python execution are mounted (`/bin`, `/lib`, `/usr`, etc.).
    -   A temporary workspace is bind-mounted as the *only* writable location.
    -   This prevents the executed code from modifying system files, installing persistent malware, or accessing the MCP server's sensitive files.

2.  **Resource Limits** (Enforced via cgroups/rlimits):
    -   **Memory**: Limited to 512MB by default to prevent OOM attacks or leaks.
    -   **CPU Time**: Execution time is strictly limited (default 60s) to prevent infinite loops from locking up resources.
    -   **Output Size**: Stdout/Stderr is capped to prevent log flooding.

3.  **Network Access**:
    -   The sandbox shares the network namespace with the host container (`--disable_clone_newnet`).
    -   This allows the code to fetch data from the internet (e.g., pip install packages, make API calls) which is often required for useful tasks.
    -   *Security Note*: Since we are running inside a container (Cloud Run/Docker) that already has its own network policies, this is an acceptable trade-off. In Cloud Run, we make sure to use a "permissionless" Service Account, so the code cannot access any Google Cloud resources unless given an explicit authentication token as part of the code execution request.

4.  **User Isolation**:
    -   Code runs as a dedicated non-privileged user (`sandboxuser`, uid:1000).
    -   This prevents privilege escalation within the container even if the code manages to escape the sandbox.

Each MCP session is isolated in a separate NsJail sandbox.

## Code Execution Flow

1. **Session Initialization**: When a client connects, a new session is created.
2. **Package Installation**: If packages are requested, they are installed in the sandbox environment.
3. **Code Execution**: The code is executed in the sandbox environment.
4. **Output**: The output is returned to the client as a JSON object with `stdout` and `stderr` fields
  that contain respective outputs from the execution.

## Running the BentoRun MCP Server

### Deploy to Cloud Run

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

### Run in Docker

You can also use [`podman`](https://podman.io/) instead of `docker`.

```bash
docker build -t bentorun-mcp .
docker run -p 8080:8080 -e PORT=8080 --privileged bentorun-mcp
```

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
    * `BENTORUN_MCP_URL` - set to your BentoRun MCP URL (e.g. `https://mcp-bentorun-python-PROJECT_NUMBER.REGION.run.app/mcp` or `http://localhost:8080/mcp`)
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

> Replace `MCP_SERVER_URL` with your BentoRun MCP URL (e.g. `https://mcp-bentorun-python-PROJECT_NUMBER.REGION.run.app/mcp` or `http://localhost:8080/mcp`)

## Disclaimer

This is not an officially supported Google product. This project is not eligible for the [Google Open Source Software Vulnerability Rewards Program](https://bughunters.google.com/open-source-security).
