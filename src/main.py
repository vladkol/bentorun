from fastmcp import FastMCP, Context
import logging
import asyncio
import os
from typing import Dict

from session_manager import SessionManager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from contextlib import asynccontextmanager

# Initialize SessionManager
session_manager = SessionManager()

@asynccontextmanager
async def lifespan(server: FastMCP):
    # Start background cleanup task
    cleanup_task = asyncio.create_task(session_manager.cleanup_loop())
    yield
    # Cleanup on shutdown
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


# Initialize FastMCP
mcp = FastMCP(
    "Python Sandbox",
    lifespan=lifespan,
)

@mcp.tool()
async def execute_python(
    ctx: Context,
    code: str,
    packages: list[str] = [],
    env_variables: dict[str, str] = {}
) -> Dict[str, str]:
    """
    Execute Python code in a secure, isolated environment.

    Args:
        code: The Python code to execute.
        session_id: A unique identifier for the session to maintain state.
        packages: List of PyPI packages to ensure are installed.
        env_variables: Environment variables to set in the execution environment.
            For code using Google Cloud libraries,
            pass `CLOUDSDK_AUTH_ACCESS_TOKEN` environment variable
            with the access token from `gcloud auth print-access-token` CLI command.

    Returns:
        Dict[str, str]: Dictionary with keys "stdout" and "stderr". Values contains respective outputs from the execution.
    """
    logger.info(f"Received execution request for session {ctx.session_id}")

    try:

        session = session_manager.get_session(ctx.session_id)
        logger.info(
            "Starting execution",
            extra={"session": ctx.session_id}
        )

        # Install packages if requested
        if packages:
            logger.info(
                "Installing packages",
                extra={"session": ctx.session_id}
            )
            session.install_packages(packages)

        x_goog_api_key = ctx.request_context.request.headers.get( # type: ignore
            "x-goog-api-key", None
        )
        if x_goog_api_key and "GOOGLE_API_KEY" not in env_variables:
            env_variables["GOOGLE_API_KEY"] = x_goog_api_key

        # Execute code
        lines = []
        async for event in session.execute(
            code,
            env_variables=env_variables,
        ):
            lines.append(event)

        logger.info(
            "Execution finished",
            extra={"session": ctx.session_id}
        )
        output = {}
        output["stdout"] = "\n".join([line[1] for line in lines if line[0] == "stdout"])
        output["stderr"] = "\n".join([line[1] for line in lines if line[0] == "stderr"])
        return output

    except Exception as e:
        logger.error(
            f"Error executing code: {e}",
            extra={"session": ctx.session_id}
        )
        return {"stderr": f"Error: {str(e)}"}

if __name__ == "__main__":
    asyncio.run(
        mcp.run_http_async(
            transport="streamable-http",
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 8080)),
            stateless_http=False
        )
    )
