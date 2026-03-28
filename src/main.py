import asyncio
import json
import logging
import os
from typing import Union, List

from fastmcp import FastMCP, Context
from mcp.types import TextContent
from fastmcp.utilities.types import File, Image

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
    instructions="""
    Python code execution engine.
    Any files must be written to `output` sub-directory of the current directory to be returned.
    `TMPDIR` env variable points to the temp file directory.
    """
)

@mcp.tool()
async def execute_python(
    ctx: Context,
    code: str,
    packages: list[str] = [],
    env_variables: dict[str, str] = {}
) -> List[Union[TextContent, File, Image]]:
    """
    Execute Python code in a secure, isolated environment.
    The code is allowed to write to the current directory of the isolated environment.
    Any files must be written to `output` sub-directory of the current directory to be returned.
    You can also use it to run CLI tools, such as Linux tools and or git.

    Args:
        code: The Python code to execute.
        packages: List of PyPI packages to ensure are installed.
        env_variables: Environment variables to set in the execution environment.
            For code using Google Cloud libraries,
            pass `CLOUDSDK_AUTH_ACCESS_TOKEN` environment variable
            with the access token from `gcloud auth print-access-token` CLI command.

    Returns:
        Dict[str, str]: Dictionary with keys "stdout" and "stderr". Values contains respective outputs from the execution.
    """
    logger.info(f"Received execution request for session {ctx.session_id}")

    results: List[Union[TextContent, File, Image]] = []
    text_output = {
        "stdout": [],
        "stderr": []
    }
    images = []
    files = []
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
            if event[0] in ["stdout", "stderr"]:
                lines.append(event)
            elif event[0].rsplit(".", 1)[1].lower() in [
                    "jpg",
                    "png",
                    "jpeg",
                    "gif",
                    "bmp",
                    "webp",
                    "tiff",
                    "svg"
                ]:
                file_type = event[0].rsplit('.', 1)[1].lower()
                if file_type == "svg":
                    file_type = "svg+xml"
                elif file_type == "jpg":
                    file_type = "jpeg"
                images.append(Image(
                    # path=event[0],
                    data=event[1], # type: ignore
                    format=file_type
                ))
            else:
                files.append(
                    File(
                        # path=event[0],
                        data=event[1], # type: ignore
                    )
                )

        logger.info(
            "Execution finished",
            extra={"session": ctx.session_id}
        )
        text_output["stdout"] = "\n".join( # type: ignore
            [line[1] for line in lines if line[0] == "stdout"]
        )
        text_output["stderr"] = "\n".join( # type: ignore
            [line[1] for line in lines if line[0] == "stderr"]
        )

    except Exception as e:
        logger.error(
            f"Error executing code: {e}",
            extra={"session": ctx.session_id},
            exc_info=True
        )
        text_output["stderr"].append(f"Error: {str(e)}")
    results.append(
        TextContent(
            text=json.dumps(text_output),
            type="text"
        )
    )
    results.extend(images)
    results.extend(files)

    return results

if __name__ == "__main__":
    asyncio.run(
        mcp.run_http_async(
            transport="streamable-http",
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 8080)),
            stateless_http=False
        )
    )
