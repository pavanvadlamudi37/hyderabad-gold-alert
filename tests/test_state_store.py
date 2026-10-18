"""Exercise real Git persistence against a local bare remote; no network or secrets."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "state_store.py"


class StateStoreTests(unittest.TestCase):
    def test_create_restore_update_and_noop_without_touching_main_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, checkout = root / "remote.git", root / "checkout"
            checkout.mkdir()
            def command(*args, ok=True):
                result = subprocess.run(args, cwd=checkout, capture_output=True,
                                        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"})
                if ok:
                    self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                return result
            command("git", "init", "--bare", str(remote))
            command("git", "init")
            command("git", "config", "user.name", "Test")
            command("git", "config", "user.email", "test@example.invalid")
            command("git", "remote", "add", "origin", str(remote))
            (checkout / "main.txt").write_text("main branch file\n")
            command("git", "add", "main.txt")
            command("git", "commit", "-m", "Initial main")
            head = command("git", "rev-parse", "HEAD").stdout
            index = command("git", "ls-files", "--stage").stdout
            command("git", "ls-remote", "--heads", "origin", "refs/heads/gold-alert-state")

            command(sys.executable, str(SCRIPT), "load")
            self.assertFalse((checkout / "state.json").exists())
            (checkout / "state.json").write_text('{"last_price":"12900"}\n')
            command(sys.executable, str(SCRIPT), "save")
            first_ref = command("git", "ls-remote", "origin", "refs/heads/gold-alert-state").stdout
            (checkout / "state.json").unlink()
            command(sys.executable, str(SCRIPT), "load")
            self.assertIn("12900", (checkout / "state.json").read_text())
            command(sys.executable, str(SCRIPT), "save")
            self.assertEqual(first_ref, command("git", "ls-remote", "origin", "refs/heads/gold-alert-state").stdout)

            (checkout / "state.json").write_text('{"last_price":"12800"}\n')
            command(sys.executable, str(SCRIPT), "save")
            command(sys.executable, str(SCRIPT), "load")
            self.assertIn("12800", (checkout / "state.json").read_text())
            self.assertEqual(head, command("git", "rev-parse", "HEAD").stdout)
            self.assertEqual(index, command("git", "ls-files", "--stage").stdout)

            # An inaccessible remote must fail instead of pretending state is absent.
            command("git", "remote", "set-url", "origin", str(root / "missing.git"))
            self.assertNotEqual(command(sys.executable, str(SCRIPT), "load", ok=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()
