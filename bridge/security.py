"""Security and sandboxing module for the ChatGPT-to-Local Agent Bridge.

Provides:
- Constant-time HMAC bearer token authentication
- Path traversal jail and boundary validation
- Command allowlist/denylist verification and argument vectorization
- Safe process execution with timeout ladders (SIGTERM -> SIGKILL)
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Union

logger = logging.getLogger("bridge.security")


class SecurityError(Exception):
    """Base exception for all security-related violations."""
    pass


class AuthenticationError(SecurityError):
    """Raised when authentication fails or credentials are invalid."""
    pass


class PathTraversalError(SecurityError):
    """Raised when a path escapes authorized workspace boundaries."""
    pass


class CommandSecurityError(SecurityError):
    """Raised when a command violates security policy or contains dangerous patterns."""
    pass


class ProcessTimeoutError(SecurityError):
    """Raised when process execution exceeds the configured timeout."""
    pass


def verify_token(provided_token: Optional[str], expected_token: str) -> bool:
    """Verify an authentication token using constant-time HMAC comparison.

    Args:
        provided_token: Token received from the client.
        expected_token: Expected secret token configured on daemon.

    Returns:
        True if tokens match, False otherwise.
    """
    if not provided_token or not expected_token:
        return False
    try:
        return hmac.compare_digest(
            provided_token.strip().encode("utf-8"),
            expected_token.strip().encode("utf-8"),
        )
    except Exception:
        return False


def extract_bearer_token(
    auth_header: Optional[str],
    api_key_header: Optional[str] = None,
) -> Optional[str]:
    """Extract bearer token from HTTP Authorization or X-API-Key headers.

    Args:
        auth_header: Authorization header value (e.g. 'Bearer <token>').
        api_key_header: X-API-Key header value.

    Returns:
        Extracted token string, or None if missing or malformed.
    """
    if auth_header:
        parts = auth_header.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        if len(parts) == 1 and not auth_header.lower().startswith("bearer"):
            return parts[0].strip()

    if api_key_header:
        return api_key_header.strip()

    return None


def generate_secure_token(length_bytes: int = 32) -> str:
    """Generate a cryptographically secure random token string.

    Args:
        length_bytes: Number of random bytes (default 32 -> ~43 URL-safe chars).

    Returns:
        URL-safe token string.
    """
    return secrets.token_urlsafe(length_bytes)


def validate_path(
    path: Union[str, Path],
    allowed_roots: Sequence[Union[str, Path]],
    allow_create: bool = True,
) -> Path:
    """Validate that a target path resolves strictly within allowed workspace roots.

    Prevents:
    - Directory traversal via '../' escapes
    - Symlink redirection outside workspace bounds
    - Null-byte injection
    - Root escapes (/etc, /root, /tmp, etc.)

    Args:
        path: Path string or Path object to validate.
        allowed_roots: Sequence of authorized base workspace directory paths.
        allow_create: If True, non-existent target files inside allowed roots are valid.

    Returns:
        Resolved canonical Path object.

    Raises:
        PathTraversalError: If path contains null bytes or resolves outside allowed roots.
    """
    path_str = str(path)
    if "\x00" in path_str:
        raise PathTraversalError(f"Null byte detected in path: {repr(path_str)}")

    if not allowed_roots:
        raise PathTraversalError("No allowed workspace roots configured.")

    try:
        # Resolve path to canonical absolute path
        target = Path(path).expanduser().resolve()
    except Exception as exc:
        raise PathTraversalError(f"Failed to resolve path '{path}': {exc}") from exc

    resolved_roots: List[Path] = []
    for root in allowed_roots:
        try:
            resolved_roots.append(Path(root).expanduser().resolve())
        except Exception:
            continue

    if not resolved_roots:
        raise PathTraversalError("None of the configured workspace roots could be resolved.")

    for r_root in resolved_roots:
        try:
            # Check if target is root itself or a sub-path
            if target == r_root or r_root in target.parents:
                return target
        except Exception:
            continue

    roots_str = ", ".join(str(r) for r in resolved_roots)
    raise PathTraversalError(
        f"Path '{path}' (resolved to '{target}') escapes authorized workspace roots: [{roots_str}]"
    )


def is_safe_relative_path(rel_path: str) -> bool:
    """Check if a path string is a safe relative path without escaping parent directories.

    Args:
        rel_path: Relative path string.

    Returns:
        True if relative and safe, False if absolute or containing parent directory escapes.
    """
    if not rel_path or "\x00" in rel_path:
        return False
    p = Path(rel_path)
    if p.is_absolute():
        return False
    parts = p.parts
    if ".." in parts:
        return False
    return True


DEFAULT_FORBIDDEN_COMMAND_PATTERNS = [
    r"\brm\s+-rf\s+[/~]",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bchmod\s+-R\s+777\s+/",
    r"\bchown\s+-R\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\binit\s+0\b",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
    r"\bsudo\b",
    r"\bsu\b",
]

DEFAULT_PERMITTED_COMMANDS = [
    r"^pi\b",
    r"^python3?\b",
    r"^pytest\b",
    r"^git\b",
    r"^npm\b",
    r"^node\b",
    r"^cargo\b",
    r"^go\b",
    r"^make\b",
    r"^gcc\b",
    r"^g\+\+\b",
    r"^clang\b",
    r"^cat\b",
    r"^ls\b",
    r"^find\b",
    r"^grep\b",
    r"^echo\b",
    r"^diff\b",
    r"^patch\b",
]


@dataclass
class CommandPolicy:
    """Policy governing executable commands and argument boundaries."""
    permitted_commands: List[str] = field(default_factory=lambda: list(DEFAULT_PERMITTED_COMMANDS))
    forbidden_commands: List[str] = field(default_factory=lambda: list(DEFAULT_FORBIDDEN_COMMAND_PATTERNS))
    allow_all: bool = False

    def validate_command(self, argv: Sequence[str]) -> None:
        """Validate argument vector against security policy.

        Args:
            argv: Sequence of argument strings (argv[0] is binary name).

        Raises:
            CommandSecurityError: If command is empty, violates forbidden patterns,
                                 or is not permitted.
        """
        if not argv or not isinstance(argv, (list, tuple)):
            raise CommandSecurityError("Command argv must be a non-empty list of strings.")

        for arg in argv:
            if not isinstance(arg, str):
                raise CommandSecurityError(f"All argv items must be strings, got: {type(arg)}")
            if "\x00" in arg:
                raise CommandSecurityError(f"Null byte detected in command argument: {repr(arg)}")

        full_cmd_str = " ".join(argv)
        executable = argv[0]
        exec_name = Path(executable).name

        # Check forbidden patterns
        for pattern in self.forbidden_commands:
            if re.search(pattern, full_cmd_str, re.IGNORECASE) or re.search(pattern, exec_name, re.IGNORECASE):
                raise CommandSecurityError(
                    f"Command '{full_cmd_str}' matches forbidden security rule: '{pattern}'"
                )

        if self.allow_all:
            return

        # Check permitted command patterns
        permitted = False
        for pattern in self.permitted_commands:
            if re.search(pattern, executable) or re.search(pattern, exec_name) or re.search(pattern, full_cmd_str):
                permitted = True
                break

        if not permitted:
            raise CommandSecurityError(
                f"Executable '{executable}' is not in permitted commands allowlist."
            )


@dataclass
class ProcessResult:
    """Result of a safely executed subprocess."""
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    error: Optional[str] = None


class SafeProcessRunner:
    """Subprocess runner enforcing argument vectorization, timeout ladders, and process group isolation."""

    def __init__(self, policy: Optional[CommandPolicy] = None):
        self.policy = policy or CommandPolicy()

    def run(
        self,
        argv: Sequence[str],
        cwd: Union[str, Path],
        timeout_seconds: int = 300,
        env: Optional[dict[str, str]] = None,
        input_data: Optional[str] = None,
        stdout_callback: Optional[Callable[[str], None]] = None,
        stderr_callback: Optional[Callable[[str], None]] = None,
    ) -> ProcessResult:
        """Run a process safely with argument vectorization (shell=False) and timeout ladder.

        Args:
            argv: Command argument vector (e.g. ['python3', '-m', 'unittest']).
            cwd: Working directory.
            timeout_seconds: Max seconds to allow before SIGTERM -> SIGKILL.
            env: Custom environment variables dict.
            input_data: Optional stdin string.
            stdout_callback: Optional stream callback for stdout lines.
            stderr_callback: Optional stream callback for stderr lines.

        Returns:
            ProcessResult with exit code, outputs, duration, and timeout status.
        """
        self.policy.validate_command(argv)

        resolved_cwd = Path(cwd).resolve()
        if not resolved_cwd.exists():
            raise FileNotFoundError(f"Working directory does not exist: {resolved_cwd}")

        proc_env = os.environ.copy()
        project_root = str(Path(__file__).resolve().parent.parent)
        existing_pp = proc_env.get("PYTHONPATH", "")
        proc_env["PYTHONPATH"] = f"{project_root}:{str(resolved_cwd)}:{existing_pp}".rstrip(":")
        if env:
            proc_env.update(env)

        start_time = time.time()
        stdout_chunks: List[str] = []
        stderr_chunks: List[str] = []
        timed_out = False

        # Use start_new_session=True on POSIX to create a distinct process group
        is_posix = os.name == "posix"
        popen_kwargs = {
            "cwd": str(resolved_cwd),
            "env": proc_env,
            "stdin": subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
            "text": True,
            "bufsize": 1,
        }
        if is_posix:
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(list(argv), **popen_kwargs)

        def read_stream(stream, chunks_list, callback):
            try:
                for line in iter(stream.readline, ""):
                    chunks_list.append(line)
                    if callback:
                        try:
                            callback(line)
                        except Exception as cb_exc:
                            logger.warning("Stream callback error: %s", cb_exc)
            except Exception as stream_exc:
                logger.debug("Error reading stream: %s", stream_exc)
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        t_out = threading.Thread(target=read_stream, args=(proc.stdout, stdout_chunks, stdout_callback), daemon=True)
        t_err = threading.Thread(target=read_stream, args=(proc.stderr, stderr_chunks, stderr_callback), daemon=True)
        t_out.start()
        t_err.start()

        if input_data is not None and proc.stdin:
            try:
                proc.stdin.write(input_data)
                proc.stdin.flush()
                proc.stdin.close()
            except Exception as stdin_exc:
                logger.debug("Stdin write exception: %s", stdin_exc)

        # Monitor execution with timeout ladder
        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            logger.warning("Process %s timed out after %ds. Initiating timeout ladder.", proc.pid, timeout_seconds)
            # 1. SIGTERM ladder
            try:
                if is_posix:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                else:
                    proc.terminate()
            except ProcessLookupError:
                pass
            except Exception as term_exc:
                logger.debug("SIGTERM error: %s", term_exc)

            # 2. Wait grace period
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                # 3. Escalation to SIGKILL
                logger.warning("Process %s survived SIGTERM. Escalating to SIGKILL.", proc.pid)
                try:
                    if is_posix:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    else:
                        proc.kill()
                except ProcessLookupError:
                    pass
                except Exception as kill_exc:
                    logger.debug("SIGKILL error: %s", kill_exc)

                try:
                    proc.wait(timeout=2.0)
                except Exception:
                    pass

        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)

        duration = time.time() - start_time
        exit_code = proc.returncode if proc.returncode is not None else -1

        return ProcessResult(
            exit_code=exit_code,
            stdout="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
            duration_seconds=duration,
            timed_out=timed_out,
            error="Process execution timed out" if timed_out else None,
        )
