"""Persist state on a dedicated Git branch, never in an expiring Actions cache."""
import os
from pathlib import Path
import subprocess
import sys

REF = "refs/heads/gold-alert-state"
MARKER = Path(".state-parent")


def git(*args, input=None, env=None):
    result = subprocess.run(["git", *args], input=input, capture_output=True, env=env)
    if result.returncode:
        raise RuntimeError(f"Git operation failed: {args[0]} (check repository write permissions / branch rules)")
    return result.stdout


def load():
    refs = git("ls-remote", "--heads", "origin", REF).decode().splitlines()
    if not refs:
        MARKER.write_text("", encoding="ascii")
        Path("state.json").unlink(missing_ok=True)
        return
    git("fetch", "--no-tags", "origin", REF)
    parent = git("rev-parse", "FETCH_HEAD").decode().strip()
    data = git("show", f"{parent}:state.json")
    Path("state.json").write_bytes(data)
    MARKER.write_text(parent, encoding="ascii")


def save():
    parent = MARKER.read_text(encoding="ascii").strip()
    data = Path("state.json").read_bytes()
    if parent and git("show", f"{parent}:state.json") == data:
        print("State unchanged")
        return
    env = dict(os.environ, GIT_INDEX_FILE=str(Path(".state-index").resolve()),
               GIT_AUTHOR_NAME="gold-alert-bot", GIT_AUTHOR_EMAIL="gold-alert@users.noreply.github.com",
               GIT_COMMITTER_NAME="gold-alert-bot", GIT_COMMITTER_EMAIL="gold-alert@users.noreply.github.com")
    git("read-tree", "--empty", env=env)
    blob = git("hash-object", "-w", "--stdin", input=data).decode().strip()
    git("update-index", "--add", "--cacheinfo", f"100644,{blob},state.json", env=env)
    tree = git("write-tree", env=env).decode().strip()
    parents = ["-p", parent] if parent else []
    commit = git("commit-tree", tree, *parents, input=b"Update gold alert state\n", env=env).decode().strip()
    # Normal fast-forward push: conflicting writers fail instead of overwriting state.
    git("push", "origin", f"{commit}:{REF}")
    print("State persisted to gold-alert-state")


if __name__ == "__main__":
    try:
        {"load": load, "save": save}[sys.argv[1]]()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
