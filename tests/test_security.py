"""Unit and integration tests for bridge security guardrails.

Covers:
- Constant-time HMAC bearer token verification & header extraction
- Path traversal jail & boundary containment
- Safe command execution with argument vectorization (shell=False)
- Command allowlist and forbidden pattern policy enforcement
- Process timeout ladder (SIGTERM -> SIGKILL)
- Error handling and edge cases
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bridge.security import (
    AuthenticationError,
    CommandPolicy,
    CommandSecurityError,
    PathTraversalError,
    ProcessResult,
    ProcessTimeoutError,
    SafeProcessRunner,
    SecurityError,
    extract_bearer_token,
    generate_secure_token,
    is_safe_relative_path,
    validate_path,
    verify_token,
)


class TestTokenAuthentication(unittest.TestCase):
    """Test constant-time HMAC token verification and token extraction."""

    def test_verify_token_valid(self):
        token = "secret-token-12345"
        self.assertTrue(verify_token(token, token))
        self.assertTrue(verify_token("  secret-token-12345  ", token))

    def test_verify_token_invalid(self):
        self.assertFalse(verify_token("wrong-token", "secret-token-12345"))
        self.assertFalse(verify_token("", "secret-token-12345"))
        self.assertFalse(verify_token(None, "secret-token-12345"))
        self.assertFalse(verify_token("secret-token-12345", ""))
        self.assertFalse(verify_token("secret-token-12345", None))
        self.assertFalse(verify_token(None, None))
        # Non-string types triggering exception handler
        self.assertFalse(verify_token(12345, "secret-token-12345"))
        self.assertFalse(verify_token("secret-token-12345", 12345))

    def test_extract_bearer_token_standard(self):
        header = "Bearer secret-abc-123"
        self.assertEqual(extract_bearer_token(header), "secret-abc-123")
        self.assertEqual(extract_bearer_token("bearer  secret-abc-123 "), "secret-abc-123")
        self.assertEqual(extract_bearer_token("BEARER secret-abc-123"), "secret-abc-123")

    def test_extract_bearer_token_raw_or_fallback(self):
        # Raw token without Bearer prefix
        self.assertEqual(extract_bearer_token("raw-token-value"), "raw-token-value")
        self.assertIsNone(extract_bearer_token(None))
        self.assertIsNone(extract_bearer_token(""))

    def test_extract_bearer_token_api_key_header(self):
        self.assertEqual(extract_bearer_token(None, api_key_header="api-key-999"), "api-key-999")
        self.assertEqual(extract_bearer_token("", api_key_header="api-key-999"), "api-key-999")
        # Bearer header takes precedence
        self.assertEqual(
            extract_bearer_token("Bearer primary-token", api_key_header="api-key-999"),
            "primary-token",
        )

    def test_generate_secure_token(self):
        t1 = generate_secure_token(32)
        t2 = generate_secure_token(32)
        self.assertIsInstance(t1, str)
        self.assertGreaterEqual(len(t1), 32)
        self.assertNotEqual(t1, t2)


class TestPathValidation(unittest.TestCase):
    """Test path traversal jail and boundary checks."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        # Create subdirectories and sample files
        self.sub_dir = self.workspace_root / "src"
        self.sub_dir.mkdir()
        self.test_file = self.sub_dir / "main.py"
        self.test_file.write_text("print('hello')")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_validate_path_valid_contained(self):
        # Workspace root itself
        self.assertEqual(validate_path(self.workspace_root, [self.workspace_root]), self.workspace_root)
        # Nested file
        self.assertEqual(validate_path(self.test_file, [self.workspace_root]), self.test_file)
        # Relative-style path within workspace
        rel_target = self.workspace_root / "src" / ".." / "src" / "main.py"
        self.assertEqual(validate_path(rel_target, [self.workspace_root]), self.test_file)

    def test_validate_path_allow_create_nonexistent(self):
        new_file = self.workspace_root / "src" / "new_file.py"
        self.assertEqual(validate_path(new_file, [self.workspace_root], allow_create=True), new_file)

    def test_validate_path_traversal_escape_raises(self):
        escape_path = self.workspace_root / ".." / ".." / "etc" / "passwd"
        with self.assertRaises(PathTraversalError):
            validate_path(escape_path, [self.workspace_root])

    def test_validate_path_null_byte_raises(self):
        bad_path = str(self.workspace_root / "src") + "\x00/evil.py"
        with self.assertRaises(PathTraversalError):
            validate_path(bad_path, [self.workspace_root])

    def test_validate_path_empty_allowed_roots_raises(self):
        with self.assertRaises(PathTraversalError):
            validate_path(self.test_file, [])

    def test_validate_path_multiple_roots(self):
        temp_dir2 = tempfile.TemporaryDirectory()
        root2 = Path(temp_dir2.name).resolve()
        file2 = root2 / "config.json"
        file2.write_text("{}")

        # Resolves when root2 is in allowed roots
        self.assertEqual(validate_path(file2, [self.workspace_root, root2]), file2)
        temp_dir2.cleanup()

    def test_validate_path_unresolvable_roots(self):
        class BadRoot:
            def expanduser(self):
                raise RuntimeError("Cannot expand user")

        with self.assertRaises(PathTraversalError):
            validate_path(self.test_file, [BadRoot()])

    def test_validate_path_symlink_inside_allowed(self):
        symlink_path = self.workspace_root / "link_to_main.py"
        try:
            symlink_path.symlink_to(self.test_file)
            self.assertEqual(validate_path(symlink_path, [self.workspace_root]), self.test_file)
        except OSError:
            # Skip if symlink creation is restricted
            pass

    def test_validate_path_symlink_escaping_raises(self):
        outside_dir = tempfile.TemporaryDirectory()
        outside_file = Path(outside_dir.name) / "secret.txt"
        outside_file.write_text("secret")

        symlink_escape = self.workspace_root / "leak_link.txt"
        try:
            symlink_escape.symlink_to(outside_file)
            with self.assertRaises(PathTraversalError):
                validate_path(symlink_escape, [self.workspace_root])
        except OSError:
            pass
        finally:
            outside_dir.cleanup()

    def test_is_safe_relative_path(self):
        self.assertTrue(is_safe_relative_path("src/main.py"))
        self.assertTrue(is_safe_relative_path("index.html"))
        self.assertTrue(is_safe_relative_path("a/b/c/d.txt"))
        self.assertFalse(is_safe_relative_path("../escape.py"))
        self.assertFalse(is_safe_relative_path("a/../../escape.py"))
        self.assertFalse(is_safe_relative_path("/absolute/path"))
        self.assertFalse(is_safe_relative_path(""))
        self.assertFalse(is_safe_relative_path("a/\x00/b"))


class TestCommandPolicy(unittest.TestCase):
    """Test command allowlist, denylist, and argument inspection."""

    def setUp(self):
        self.policy = CommandPolicy()

    def test_validate_command_permitted(self):
        # Permitted default tools
        self.policy.validate_command(["python3", "--version"])
        self.policy.validate_command(["git", "status"])
        self.policy.validate_command(["pytest", "-v"])
        self.policy.validate_command(["pi", "-p", "Refactor auth"])
        self.policy.validate_command(["cat", "README.md"])
        self.policy.validate_command(["grep", "-rn", "TODO", "."])

    def test_validate_command_forbidden_patterns(self):
        # Destructive root removal
        with self.assertRaises(CommandSecurityError):
            self.policy.validate_command(["rm", "-rf", "/"])
        with self.assertRaises(CommandSecurityError):
            self.policy.validate_command(["rm", "-rf", "/var"])
        # Sudo execution
        with self.assertRaises(CommandSecurityError):
            self.policy.validate_command(["sudo", "apt", "install", "nmap"])
        # Fork bomb
        with self.assertRaises(CommandSecurityError):
            self.policy.validate_command([":(){ :|:& };:"])
        # Reboot / shutdown
        with self.assertRaises(CommandSecurityError):
            self.policy.validate_command(["shutdown", "-h", "now"])

    def test_validate_command_unpermitted_executable(self):
        # Executable not in allowlist
        with self.assertRaises(CommandSecurityError):
            self.policy.validate_command(["curl", "https://malicious.site/payload.sh"])
        with self.assertRaises(CommandSecurityError):
            self.policy.validate_command(["bash", "-c", "whoami"])

    def test_validate_command_empty_or_invalid_type(self):
        with self.assertRaises(CommandSecurityError):
            self.policy.validate_command([])
        with self.assertRaises(CommandSecurityError):
            self.policy.validate_command("python3 --version")  # type: ignore
        with self.assertRaises(CommandSecurityError):
            self.policy.validate_command(["python3", None])  # type: ignore

    def test_validate_command_null_byte_arg(self):
        with self.assertRaises(CommandSecurityError):
            self.policy.validate_command(["python3", "file.py\x00--flag"])

    def test_allow_all_policy(self):
        policy_all = CommandPolicy(allow_all=True)
        # Any binary allowed unless forbidden pattern matches
        policy_all.validate_command(["curl", "https://example.com"])
        with self.assertRaises(CommandSecurityError):
            policy_all.validate_command(["rm", "-rf", "/"])


class TestSafeProcessRunner(unittest.TestCase):
    """Test safe process execution, streams, and timeout ladder."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cwd = Path(self.temp_dir.name).resolve()
        self.runner = SafeProcessRunner()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_run_simple_command_success(self):
        res = self.runner.run(
            argv=[sys.executable, "-c", "print('output line 1'); print('output line 2')"],
            cwd=self.cwd,
            timeout_seconds=10,
        )
        self.assertEqual(res.exit_code, 0)
        self.assertFalse(res.timed_out)
        self.assertIn("output line 1", res.stdout)
        self.assertIn("output line 2", res.stdout)
        self.assertGreater(res.duration_seconds, 0)

    def test_run_command_with_stderr(self):
        res = self.runner.run(
            argv=[sys.executable, "-c", "import sys; sys.stderr.write('error log\\n')"],
            cwd=self.cwd,
            timeout_seconds=10,
        )
        self.assertEqual(res.exit_code, 0)
        self.assertIn("error log", res.stderr)

    def test_run_command_with_stdin(self):
        res = self.runner.run(
            argv=[sys.executable, "-c", "import sys; data = sys.stdin.read(); print(f'got:{data}')"],
            cwd=self.cwd,
            input_data="payload_data",
            timeout_seconds=10,
        )
        self.assertEqual(res.exit_code, 0)
        self.assertIn("got:payload_data", res.stdout)

    def test_run_command_streaming_callbacks(self):
        out_lines = []
        err_lines = []
        res = self.runner.run(
            argv=[sys.executable, "-c", "import sys; print('stdout1'); sys.stderr.write('stderr1\\n')"],
            cwd=self.cwd,
            stdout_callback=out_lines.append,
            stderr_callback=err_lines.append,
            timeout_seconds=10,
        )
        self.assertEqual(res.exit_code, 0)
        self.assertTrue(any("stdout1" in line for line in out_lines))
        self.assertTrue(any("stderr1" in line for line in err_lines))

    def test_run_command_nonexistent_cwd_raises(self):
        with self.assertRaises(FileNotFoundError):
            self.runner.run(
                argv=[sys.executable, "--version"],
                cwd=self.cwd / "nonexistent_dir",
                timeout_seconds=5,
            )

    def test_run_command_timeout_ladder(self):
        # Command sleeps for 10 seconds, timeout is 1 second
        start = time.time()
        res = self.runner.run(
            argv=[sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=self.cwd,
            timeout_seconds=1,
        )
        duration = time.time() - start
        self.assertTrue(res.timed_out)
        self.assertIsNotNone(res.error)
        self.assertLess(duration, 8.0)  # Should terminate well before 10s sleep

    def test_run_command_custom_env(self):
        res = self.runner.run(
            argv=[sys.executable, "-c", "import os; print(os.environ.get('CUSTOM_BRIDGE_VAR'))"],
            cwd=self.cwd,
            env={"CUSTOM_BRIDGE_VAR": "magic_value_42"},
            timeout_seconds=10,
        )
        self.assertEqual(res.exit_code, 0)
        self.assertIn("magic_value_42", res.stdout)

    def test_validate_path_single_string_root(self):
        root_str = str(self.cwd)
        # Passing string as allowed_roots
        res = validate_path(self.cwd / "sub" / "file.txt", [root_str], allow_create=True)
        self.assertTrue(str(res).startswith(root_str))

    def test_run_command_callback_exception_handled(self):
        def bad_cb(line):
            raise RuntimeError("stream callback failure")

        res = self.runner.run(
            argv=[sys.executable, "-c", "print('hello from subprocess')"],
            cwd=self.cwd,
            stdout_callback=bad_cb,
            stderr_callback=bad_cb,
            timeout_seconds=5,
        )
    def test_extract_bearer_token_unsupported_scheme(self):
        self.assertIsNone(extract_bearer_token("Basic dXNlcjpwYXNz"))
        self.assertIsNone(extract_bearer_token("Digest realm=abc"))
        self.assertIsNone(extract_bearer_token(None, None))

    def test_validate_path_empty_or_none_allowed_roots(self):
        with self.assertRaises(PathTraversalError):
            validate_path("some/file.txt", [])
        with self.assertRaises(PathTraversalError):
            validate_path("some/file.txt", None)


if __name__ == "__main__":
    unittest.main()
