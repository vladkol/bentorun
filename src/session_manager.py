import shutil
import time
import asyncio
import datetime
import logging
import subprocess
from pathlib import Path
import tempfile
import json
from typing import AsyncGenerator, Dict, Tuple, Union

from sandbox import SandboxWrapper

logger = logging.getLogger(__name__)

TEMPLATE_VENV_PATH = Path("/opt/template_venv")

class Session:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.workspace_dir = Path(f"/tmp/session_{session_id}")
        self.venv_dir = self.workspace_dir / "venv"
        self.workspace_dir = Path(f"/tmp/session_{session_id}")
        self.venv_dir = self.workspace_dir / "venv"
        self.temp_dir = Path("/tmp")
        self.output_dir = self.workspace_dir / "output"
        self.session_output_files = {}
        self.last_active = time.time()
        self.sandbox = SandboxWrapper()

    def touch(self):
        self.last_active = time.time()

    def initialize(self):
        if not self.workspace_dir.exists():
            logger.info(f"Creating workspace for session {self.session_id}")
            self.workspace_dir.mkdir(parents=True, exist_ok=True)
            self.temp_dir.mkdir(exist_ok=True)
            self.output_dir.mkdir(exist_ok=True)

            # Create a fresh, lightweight venv (uses symlinks for python/stdlib)
            subprocess.check_call(
                ["uv", "venv", str(self.venv_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            # Fix permissions: The session manager runs as root, but the sandbox runs as uid 1000.
            # We must ensure the workspace is owned by 1000.
            # Recursive chown
            for path in [self.workspace_dir]:
                shutil.chown(path, user=1000, group=1000)
                for item in path.rglob("*"):
                    shutil.chown(item, user=1000, group=1000)

            # Link the template venv's packages via a .pth file
            template_path = Path(TEMPLATE_VENV_PATH)
            if template_path.exists():
                # Find the site-packages directory in the template
                template_site = next(template_path.glob("lib/python*/site-packages"), None)

                # Find the site-packages directory in the new venv
                venv_site = next(self.venv_dir.glob("lib/python*/site-packages"), None)

                if template_site and venv_site:
                    # Create a .pth file that adds the template's site-packages to sys.path
                    pth_file = venv_site / "_template_packages.pth"
                    # Write the absolute path to the template's site-packages
                    pth_file.write_text(str(template_site.resolve()))
                    logger.info(f"Linked template packages: {template_site}")
                else:
                    logger.warning("Could not locate site-packages to link template venv.")
            else:
                logger.warning(f"Template venv not found at {TEMPLATE_VENV_PATH}")

    def install_packages(self, packages: list[str]):
        if not packages:
            return

        # We need to run uv pip install on the host
        # access to internet is available on host
        cmd = [
            "uv", "pip", "install",
            "--python", str(self.venv_dir),
        ] + packages

        logger.info(f"Installing packages for session {self.session_id}: {packages}")
        subprocess.check_call(cmd)

    # start() and stop() are no longer needed as we use one-shot execution

    async def execute(
        self,
        code: str,
        env_variables: dict[str, str] = {},
    ) -> AsyncGenerator[Tuple[str, Union[str, bytes]], None]:
        temp_cred_path = None
        self.touch()

        # Create a temporary script file
        import uuid
        script_name = f".script_{uuid.uuid4().hex}.py"
        script_path = self.workspace_dir / script_name

        # Home directory
        env_variables["HOME"] = str(self.workspace_dir)
        # Temporary files directory
        env_variables["TMPDIR"] = str(self.temp_dir)
        # Output files directory
        env_variables["OUTPUT_DIR"] = str(self.output_dir)

        # Work on env variables
        if (
            "GOOGLE_APPLICATION_CREDENTIALS" in env_variables
            and env_variables[
                "GOOGLE_APPLICATION_CREDENTIALS"
            ].strip().startswith("{")
        ):
            json_str = env_variables["GOOGLE_APPLICATION_CREDENTIALS"]
            cred_dict = json.loads(json_str)
            if "type" not in cred_dict:
                cred_dict["type"] = "authorized_user"
                json_str = json.dumps(cred_dict)
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.json',
                delete=False,
                dir=str(self.workspace_dir)
            ) as temp_cred_file:
                temp_cred_file.write(
                    json_str
                )
                temp_cred_path = temp_cred_file.name
            logger.info(f"Saved credentials to {temp_cred_path}")
            env_variables["GOOGLE_APPLICATION_CREDENTIALS"] = temp_cred_path
        elif (
            "CLOUDSDK_AUTH_ACCESS_TOKEN" in env_variables
            and env_variables["CLOUDSDK_AUTH_ACCESS_TOKEN"]
        ):
            fix_code = """
import datetime
import os
import google.auth
from google.oauth2.credentials import Credentials as oauth2_credentials
_credentials = oauth2_credentials(
    token=os.environ.get("CLOUDSDK_AUTH_ACCESS_TOKEN"),
    expiry=datetime.datetime.now() + datetime.timedelta(days=365)
)
_credentials.refresh = lambda request: None
def __mock_default(*args, **kwargs):
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "fallback-project-id")
    return _credentials, project_id
google.auth.default = __mock_default
"""
            code = fix_code + "\n\n" + code

        try:
            # Write code to file
            script_path.write_text(code)

            # Execute script using sandbox
            python_executable = self.workspace_dir / "venv" / "bin" / "python3"
            cmd = [str(python_executable), str(script_path)]

            # Note: sandbox.run_command is now async
            process = await self.sandbox.run_command(
                cmd=cmd,
                workspace_path=str(self.workspace_dir),
                env_vars=env_variables,
                mount_paths=[str("/opt")],
                time_limit=60
            )

            # Stream output
            async def read_stream(stream, stream_name):
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    yield (stream_name, f"{line.decode('utf-8', errors='replace').rstrip()}")

            # We use a helper to merge streams or just yield as they come
            # For simplicity in this specialized tool helper, let's just yield lines
            # But we need to handle both streams concurrently.

            # Alternative: simpler loop
            # We can use asyncio.gather to run readers, but we want to yield freely.

            # Better approach for a generator:
            # Queue to aggregate lines
            queue = asyncio.Queue()

            async def pipe_reader(stream, label):
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    await queue.put((label, line.decode('utf-8', errors='replace')))
                # Signal done
                await queue.put(None)

            stdout_task = asyncio.create_task(pipe_reader(process.stdout, "stdout"))
            stderr_task = asyncio.create_task(pipe_reader(process.stderr, "stderr"))

            finished_streams = 0
            while finished_streams < 2:
                item = await queue.get()
                if item is None:
                    finished_streams += 1
                else:
                    label, content = item
                    yield (label, content)

            await process.wait()

            if process.returncode != 0:
                yield ("stderr", f"Process exited with code {process.returncode}")

            # Recursively list files in output directory,
            # with "keys" being the relative path from output directory.
            # Only include files that were created/modified after the last execution.
            for file_path in self.output_dir.rglob("*"):
                if file_path.is_file():
                    relative_file_path = str(
                        file_path.relative_to(self.output_dir)
                    )
                    file_m_time = datetime.datetime.fromtimestamp(
                        file_path.stat().st_mtime,
                        tz=datetime.timezone.utc
                    )
                    if (
                        relative_file_path in self.session_output_files
                        and self.session_output_files[
                            relative_file_path
                        ] >= file_m_time
                    ):
                        continue
                    self.session_output_files[relative_file_path] = file_m_time
                    yield (
                        str(file_path.relative_to(self.output_dir)),
                        file_path.read_bytes()
                    )

        except Exception as e:
            logger.error(f"Error during execution: {e}")
            yield ("stderr", f"ERROR: {e}")
        finally:
            self.touch()
            # Cleanup script
            if temp_cred_path:
                Path(temp_cred_path).unlink(missing_ok=True)
            if script_path.exists():
                script_path.unlink(missing_ok=True)

class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, Session] = {}

    def get_session(self, session_id: str) -> Session:
        if session_id not in self.sessions:
            session = Session(session_id)
            session.initialize()
            self.sessions[session_id] = session
        return self.sessions[session_id]

    async def cleanup_loop(self):
        while True:
            await asyncio.sleep(60) # Check every minute
            now = time.time()
            to_remove = []
            for sid, session in self.sessions.items():
                if now - session.last_active > 1200: # 20 minutes
                    logger.info(f"Cleaning up inactive session {sid}")
                    shutil.rmtree(session.workspace_dir, ignore_errors=True)
                    to_remove.append(sid)

            for sid in to_remove:
                del self.sessions[sid]
