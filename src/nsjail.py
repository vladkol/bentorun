import asyncio
import logging
import os
import sys
from typing import List

logger = logging.getLogger(__name__)

KNOWN_SYSTEM_MOUNTS = [
    "/bin",
    "/sbin",
    "/usr",
    "/lib",
    "/lib64",
    "/etc/resolv.conf",
    "/etc/ssl/certs",
    "/etc/ld.so.cache",
    "/etc/ld.so.conf.d",
    "/dev/random",
    "/dev/urandom",
    "/dev/zero",
    "/dev/null",
    "/opt",
]

class NSJailWrapper:
    def __init__(self, nsjail_path: str = "/usr/local/bin/nsjail"):
        self.nsjail_path = nsjail_path

    async def run_command(
        self,
        cmd: List[str],
        workspace_path: str,
        mount_paths: List[str] = [],
        env_vars: dict = {},
        time_limit: int = 60, # seconds
        max_memory: int = 512, # MB
        max_output_size: int = 256, # MB
    ) -> asyncio.subprocess.Process:
        """
        Constructs and runs the nsjail command asynchronously.
        Returns the Process object for interaction.
        """
        # Prepare a separate file descriptor for NSJail's internal logs
        # We duplicate sys.stderr so we can pass it to nsjail as a specific FD
        # This allows us to capture the process's stderr separately from nsjail's logs
        try:
            log_fd = os.dup(sys.stderr.fileno())
        except Exception as e:
            logger.warning(f"Failed to duplicate stderr for nsjail logs: {e}")
            log_fd = 2 # Fallback to standard stderr if dup fails

        # Process system mounts
        system_mounts = []
        for path in KNOWN_SYSTEM_MOUNTS:
            if os.path.exists(path):
                system_mounts.append(path)

        nsjail_cmd = [
            self.nsjail_path,
            "-Mo",
            "--user", "1000", "--group", "1000", # Map to sandboxuser

            # Log to the duplicated FD
            "--log_fd", str(log_fd),

            # To allow internet access:
            # We must SHARE the network namespace with the host (the container).
            "--disable_clone_newnet",

            # Bind mount workspace as RW
            "-B", f"{workspace_path}:{workspace_path}",
            "--cwd", f"{workspace_path}",

            # Limits
            "--rlimit_as", str(max_memory),
            "--rlimit_fsize", str(max_output_size),
            "--rlimit_cpu", str(time_limit),

            # Environment
            "--env", f"PATH={workspace_path}/venv/bin:{os.environ.get('PATH', '/bin:/usr/bin')}",
            "--env", "PYTHONUNBUFFERED=1",
            "--env", "PYTHONIOENCODING=utf-8",
            "--env", "LD_LIBRARY_PATH=/usr/local/lib",
        ]

        # Core read-only system mounts
        for path in system_mounts:
            nsjail_cmd.extend(["-R", path])

        # Add extra mounts if needed
        for path in mount_paths:
             nsjail_cmd.extend(["-R", path])

        # Add generic environment variables
        for key, value in env_vars.items():
            nsjail_cmd.extend(["--env", f"{key}={value}"])

        # Target command
        nsjail_cmd.append("--")
        nsjail_cmd.extend(cmd)

        logger.info(f"Starting nsjail with command: {' '.join(nsjail_cmd)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *nsjail_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                pass_fds=[log_fd]
            )
            # We must close the duplicated FD in the parent after creating the subprocess
            os.close(log_fd)
            return process
        except Exception as e:
            # Ensure we close log_fd if subprocess creation fails
            try:
                os.close(log_fd)
            except OSError:
                pass
            raise e
