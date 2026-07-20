from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import stat
import tempfile
from pathlib import Path

KEY_NAME = "MEMORY_ENCRYPTION_KEY"
KEY_LINE_PATTERN = re.compile(rf"^\s*(?:export\s+)?{KEY_NAME}\s*=(.*)$")


def inspect_memory_key(env_path: str | Path) -> str:
    path = Path(env_path)
    if not path.is_file():
        return "env_missing"
    values = _defined_values(path)
    if not values:
        return "missing"
    if len(values) > 1:
        return "duplicate"
    value = _unquote(values[0])
    if not value:
        return "blank"
    return "valid" if _is_valid_fernet_key(value) else "invalid"


def generate_memory_key(env_path: str | Path) -> None:
    path = Path(env_path)
    state = inspect_memory_key(path)
    if state not in {"blank", "missing"}:
        raise ValueError(f"cannot generate key while state is {state}")
    encoded_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    _write_key_atomically(path, encoded_key)


def _defined_values(path: Path) -> list[str]:
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = KEY_LINE_PATTERN.match(line)
        if match:
            values.append(match.group(1).strip())
    return values


def _unquote(value: str) -> str:
    normalized = value.strip()
    if not normalized or normalized.startswith("#"):
        return ""
    quoted = re.fullmatch(r"(['\"])(.*?)\1(?:\s+#.*)?", normalized)
    if quoted:
        return quoted.group(2).strip()
    return normalized.split(" #", 1)[0].strip()


def _is_valid_fernet_key(value: str) -> bool:
    try:
        decoded = base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError):
        return False
    return len(decoded) == 32


def _write_key_atomically(path: Path, encoded_key: str) -> None:
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    replacement = f"{KEY_NAME}={encoded_key}"
    replaced = False
    for index, line in enumerate(lines):
        if KEY_LINE_PATTERN.match(line.rstrip("\r\n")):
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
            lines[index] = replacement + ending
            replaced = True
            break
    if not replaced:
        separator = "" if not original or original.endswith(("\n", "\r")) else "\n"
        lines.append(f"{separator}{replacement}\n")
    _atomic_replace(path, "".join(lines))


def _atomic_replace(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("inspect", "generate"))
    parser.add_argument("env_file")
    args = parser.parse_args()
    if args.action == "inspect":
        print(inspect_memory_key(args.env_file))
        return 0
    generate_memory_key(args.env_file)
    print("generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
