import asyncio
import logging
import os
import json
import shutil
import tempfile
import uuid
import glob
from typing import List, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Standard system paths to mount read-only
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
    "/dev/urandom",
    "/dev/null",
    "/dev/zero",
    # We might need more depending on the environment
]

@dataclass
class SandboxConfig:
    use_sudo: bool = False
    rootless: bool = False # Default to False to match cloud-run-sandbox structure (assuming root in container)
    network: str = "host" # 'host' or 'none'. 'sandbox' requires netns setup which is complex.
    enable_gpu: bool = False
    debug: bool = False

class SandboxWrapper:
    def __init__(self,
        runsc_path: str = "runsc",
        config: SandboxConfig = SandboxConfig()
    ):
        self.runsc_path = runsc_path
        self.config = config

    async def run_command(
        self,
        cmd: List[str],
        workspace_path: str,
        mount_paths: List[str] = [],
        env_vars: Dict[str, str] = {},
        time_limit: int = 60,
        max_memory: int = 512, # MB - note: runsc might not enforce this directly without cgroups
        max_output_size: int = 256, # MB
    ) -> asyncio.subprocess.Process:
        """
        Runs a command in a runsc sandbox using a one-shot OCI bundle.
        Returns the process object.
        """
        sandbox_id = str(uuid.uuid4())[:8]
        bundle_dir = os.path.join(tempfile.gettempdir(), f"bundle_{sandbox_id}")
        os.makedirs(bundle_dir, exist_ok=True)

        logger.info(f"Preparing runsc bundle at {bundle_dir} for command: {cmd}")

        try:
            self._create_oci_config(
                bundle_dir=bundle_dir,
                cmd=cmd,
                workspace_path=workspace_path,
                mount_paths=mount_paths,
                env_vars=env_vars,
                hostname=f"sandbox-{sandbox_id}",
                time_limit=time_limit,
                max_memory=max_memory,
                max_output_size=max_output_size,
                rootless=self.config.rootless,
            )

            # Build runsc command
            # runsc --rootless run <bundle_id>
            # We execute in the bundle_dir so it finds config.json automatically?
            # Or pass --bundle.
            # Usually: cd bundle_dir && runsc run <id>

            runsc_cmd = [self.runsc_path]
            # Debug flags
            # runsc_cmd.extend(["--debug", "--debug-log", "/tmp/runsc.log"])

            if self.config.rootless:
                runsc_cmd.append("--rootless")

            if self.config.enable_gpu:
                runsc_cmd.append("--nvproxy")

            runsc_cmd.extend(["--network", self.config.network])

            # Using simple 'run' command which runs the container to completion
            # The container ID must be unique
            container_id = f"sandbox-{sandbox_id}"

            runsc_cmd.extend(["run", "--bundle", bundle_dir, container_id])

            logger.info(f"Starting runsc: {' '.join(runsc_cmd)}")

            # Start the process
            process = await asyncio.create_subprocess_exec(
                *runsc_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # We don't need to pass FDs/env because everything is in config.json
            )

            # We can't easily clean up the bundle folder *immediately* because the process is async.
            # We rely on an async cleanup or just leave it for /tmp cleanup if we want to be lazy,
            # but ideally we should hook into the process lifecycle.
            # For now, we'll attach a cleanup callback to the process object if possible,
            # or wrap it.
            # actually, SessionManager awaits the process, so we can't clean up here.
            # We should probably return a wrapper or handle cleanup in SessionManager?
            # Or, best effort: schedule a cleanup task that waits for process exit.
            asyncio.create_task(self._cleanup_bundle(process, bundle_dir, container_id))

            return process

        except Exception as e:
            logger.error(f"Failed to start sandbox: {e}")
            shutil.rmtree(bundle_dir, ignore_errors=True)
            raise

    async def _cleanup_bundle(self, process: asyncio.subprocess.Process, bundle_dir: str, container_id: str):
        try:
            await process.wait()
        finally:
            # Force delete if it hangs?
            # Runsc delete might be needed if it wasn't a clean exit?
            # 'runsc run' deletes the container after exit typically, but let's be safe.
            # check if we need to run 'runsc delete <id>'?
            # For now, just remove files.
            shutil.rmtree(bundle_dir, ignore_errors=True)
            logger.debug(f"Cleaned up bundle {bundle_dir}")

    def _create_oci_config(
        self,
        bundle_dir: str,
        cmd: List[str],
        workspace_path: str,
        mount_paths: List[str],
        env_vars: Dict[str, str],
        hostname: str,
        time_limit: int,
        max_memory: int,
        max_output_size: int,
        rootless: bool = False,
    ):
        # OCI config.json generation

        # Prepare environment
        env_list = [
            f"PATH={workspace_path}/venv/bin:/usr/local/bin:/usr/bin:/bin",
            "PYTHONUNBUFFERED=1",
            "PYTHONIOENCODING=utf-8",
            "TERM=xterm"
        ]
        for k, v in env_vars.items():
            env_list.append(f"{k}={v}")

        # Mounts
        # We need to mount the host filesystem parts we need.
        # Since we are in a container, we can bind mount from /

        mounts = [
            {"destination": "/proc", "type": "proc", "source": "proc"},
            {"destination": "/dev", "type": "tmpfs", "source": "tmpfs", "options": ["nosuid", "strictatime", "mode=755", "size=65536k"]},
            {"destination": "/dev/pts", "type": "devpts", "source": "devpts", "options": ["nosuid", "noexec", "newinstance", "ptmxmode=0666", "mode=0620", "gid=5"]},
            {"destination": "/sys", "type": "sysfs", "source": "sysfs", "options": ["nosuid", "noexec", "nodev", "ro"]},
        ]

        # Mount minimal system directories as RO
        for path in KNOWN_SYSTEM_MOUNTS:
            if os.path.exists(path):
                mounts.append({
                    "destination": path,
                    "type": "bind",
                    "source": path,
                    "options": ["rbind", "ro"]
                })

        # Mount GPU devices if enabled
        if self.config.enable_gpu:
             # Search for all nvidia devices
             gpu_devices = glob.glob("/dev/nvidia*")
             for path in gpu_devices:
                 mounts.append({
                     "destination": path,
                     "type": "bind",
                     "source": path,
                     "options": ["rbind", "rw"]
                 })

        # Mount workspace as RW
        mounts.append({
            "destination": workspace_path,
            "type": "bind",
            "source": workspace_path,
            "options": ["rbind", "rw"]
        })

        # Mount extra paths (like RO templates)
        for path in mount_paths:
             if os.path.exists(path):
                mounts.append({
                    "destination": path,
                    "type": "bind",
                    "source": path,
                    "options": ["rbind", "ro"]
                })

        # Rootless configuration
        # We assume we are running as uid 1000 (sandboxuser).
        # We want to map this user to root inside the container?
        # Or just run as uid 1000 inside?
        # Typically for python scripts we want to run as the owner of the workspace.

        config = {
            "ociVersion": "1.0.2",
            "process": {
                "terminal": False,
                "user": {
                    "uid": 1000,
                    "gid": 1000
                },
                "args": cmd,
                "env": env_list,
                "cwd": workspace_path,
                "capabilities": {
                    "bounding": ["CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_FOWNER", "CAP_FSETID", "CAP_KILL", "CAP_SETGID", "CAP_SETUID", "CAP_SETPCAP", "CAP_NET_BIND_SERVICE", "CAP_NET_RAW", "CAP_SYS_CHROOT", "CAP_MKNOD", "CAP_AUDIT_WRITE", "CAP_SETFCAP"],
                    "effective": ["CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_FOWNER", "CAP_FSETID", "CAP_KILL", "CAP_SETGID", "CAP_SETUID", "CAP_SETPCAP", "CAP_NET_BIND_SERVICE", "CAP_NET_RAW", "CAP_SYS_CHROOT", "CAP_MKNOD", "CAP_AUDIT_WRITE", "CAP_SETFCAP"],
                    "inheritable": ["CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_FOWNER", "CAP_FSETID", "CAP_KILL", "CAP_SETGID", "CAP_SETUID", "CAP_SETPCAP", "CAP_NET_BIND_SERVICE", "CAP_NET_RAW", "CAP_SYS_CHROOT", "CAP_MKNOD", "CAP_AUDIT_WRITE", "CAP_SETFCAP"],
                    "permitted": ["CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_FOWNER", "CAP_FSETID", "CAP_KILL", "CAP_SETGID", "CAP_SETUID", "CAP_SETPCAP", "CAP_NET_BIND_SERVICE", "CAP_NET_RAW", "CAP_SYS_CHROOT", "CAP_MKNOD", "CAP_AUDIT_WRITE", "CAP_SETFCAP"],
                    "ambient": ["CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_FOWNER", "CAP_FSETID", "CAP_KILL", "CAP_SETGID", "CAP_SETUID", "CAP_SETPCAP", "CAP_NET_BIND_SERVICE", "CAP_NET_RAW", "CAP_SYS_CHROOT", "CAP_MKNOD", "CAP_AUDIT_WRITE", "CAP_SETFCAP"]
                },
                "rlimits": [
                    {"type": "RLIMIT_NOFILE", "hard": 1024, "soft": 1024},
                    {"type": "RLIMIT_CPU", "hard": time_limit + 5, "soft": time_limit}, # +5s grace for hard limit
                    {"type": "RLIMIT_AS", "hard": max_memory * 1024 * 1024, "soft": max_memory * 1024 * 1024},
                    {"type": "RLIMIT_FSIZE", "hard": max_output_size * 1024 * 1024, "soft": max_output_size * 1024 * 1024}
                ]
            },
            "root": {
                "path": "/", # Use host root (since we bind mounted everything we need?)
                # Actually runsc requires a rootfs.
                # If we use "/", we are using the container's root.
                # runsc expects this to be the root of the filesystem for the container.
                # If we set readonly: true, it should be fine.
                "readonly": True
            },
            "hostname": hostname,
            "mounts": mounts,
            "linux": {
                "namespaces": [
                    {"type": "pid"},
                    {"type": "ipc"},
                    {"type": "uts"},
                    {"type": "mount"}
                ]
            }
        }

        # Add network namespace only if NOT using host networking
        if self.config.network != "host":
             config["linux"]["namespaces"].append({"type": "network"})

        # Add UID/GID mappings only if rootless
        if rootless:
             config["linux"]["uidMappings"] = [ # type: ignore
                {"containerID": 1000, "hostID": 1000, "size": 1}
            ]
             config["linux"]["gidMappings"] = [ # type: ignore
                {"containerID": 1000, "hostID": 1000, "size": 1}
            ]

        with open(os.path.join(bundle_dir, "config.json"), "w") as f:
            json.dump(config, f, indent=4)
