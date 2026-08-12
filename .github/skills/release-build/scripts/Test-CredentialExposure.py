"""Check release source history and artifacts for credential exposure.

The scanner intentionally reports only finding types and locations. It never
prints matched values.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath


TEXT_SUFFIXES = {
    ".bat",
    ".cfg",
    ".cmd",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".rst",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
DANGEROUS_NAMES = {
    ".env",
    ".token",
    "auth_token",
    "config.json",
    "credentials",
    "garmin_tokens",
}
HIGH_CONFIDENCE = {
    "PRIVATE_KEY": re.compile(
        rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
    ),
    "AWS_ACCESS_KEY": re.compile(
        rb"(?<![A-Z0-9])"
        rb"(?:A3T|AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASCA)"
        rb"[A-Z0-9]{16}(?![A-Z0-9])"
    ),
    "GITHUB_TOKEN": re.compile(
        rb"(?<![A-Za-z0-9_])"
        rb"(?:gh[pousr]_[A-Za-z0-9]{30,255}"
        rb"|github_pat_[A-Za-z0-9_]{20,255})"
    ),
    "GOOGLE_API_KEY": re.compile(
        rb"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])"
    ),
    "SLACK_TOKEN": re.compile(
        rb"(?<![A-Za-z0-9-])xox[baprs]-[A-Za-z0-9-]{10,}"
        rb"(?![A-Za-z0-9-])"
    ),
    "STRIPE_LIVE_KEY": re.compile(
        rb"(?<![A-Za-z0-9_])sk_live_[A-Za-z0-9]{16,}(?![A-Za-z0-9_])"
    ),
}
ASSIGNMENT = re.compile(
    r"(?im)\b(password|passwd|pwd|api_key|apikey|client_secret|access_token|"
    r"auth_token|refresh_token)\b\s*[:=]\s*(['\"])([^'\"\r\n]{3,})\2"
)
EMAIL = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)
MAIN_SKIP = {
    "email_entry",
    "email_frame",
    "password_entry",
    "pw_frame",
    "erraticradar",
    "show=",
    "Email:",
    "Password:",
}
PLACEHOLDERS = {
    "changeme",
    "dummy",
    "example",
    "fake",
    "foobar",
    "none",
    "null",
    "password",
    "placeholder",
    "secret",
    "test",
    "token",
    "xxxx",
}


class CredentialScanner:
    def __init__(
        self,
        root: Path,
        base_ref: str,
        target_ref: str,
        release_dir: Path | None,
        zip_path: Path | None,
    ) -> None:
        self.root = root
        self.base_ref = base_ref
        self.target_ref = target_ref
        self.release_dir = release_dir
        self.zip_path = zip_path
        self.findings: list[tuple[str, str]] = []
        self.scanned = {
            "tracked_files": 0,
            "history_snapshots": 0,
            "release_files": 0,
            "zip_entries": 0,
        }

    def git_bytes(self, *args: str) -> bytes:
        result = subprocess.run(
            ["git", *args],
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode:
            diagnostic = result.stderr.decode(errors="replace").strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {diagnostic}")
        return result.stdout

    @staticmethod
    def is_dangerous_name(name: str) -> bool:
        parts = [
            part.lower()
            for part in PurePosixPath(name.replace("\\", "/")).parts
        ]
        for part in parts:
            if part in DANGEROUS_NAMES or part.startswith(".env."):
                return True
            if "garmin_tokens" in part or "auth_token" in part:
                return True
            if part.startswith("credentials.") or part.endswith(".token"):
                return True
        return False

    @staticmethod
    def decode_text(data: bytes) -> str | None:
        if b"\x00" in data[:4096]:
            return None
        for encoding in ("utf-8-sig", "utf-8"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                pass
        return None

    @staticmethod
    def looks_placeholder(value: str) -> bool:
        normalized = value.strip().lower()
        return (
            normalized in PLACEHOLDERS
            or any(word in normalized for word in PLACEHOLDERS)
            or normalized.startswith(("${", "%", "<", "your_", "your-"))
            or set(normalized) <= {"x", "*", "-", "_"}
        )

    def scan_bytes(
        self,
        label: str,
        name: str,
        data: bytes,
        *,
        text_rules: bool,
    ) -> None:
        for kind, pattern in HIGH_CONFIDENCE.items():
            if pattern.search(data):
                self.findings.append((f"{label}/{kind}", name))

        if not text_rules:
            return
        text = self.decode_text(data)
        if text is None:
            return

        virtual_name = PurePosixPath(name.replace("\\", "/")).name.lower()
        is_main = virtual_name == "main.py" or virtual_name.endswith(":main.py")
        for line_number, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if is_main and any(item in line for item in MAIN_SKIP):
                continue
            for match in ASSIGNMENT.finditer(line):
                if not self.looks_placeholder(match.group(3)):
                    finding = f"{name}:{line_number}"
                    kind = f"{label}/ASSIGNED_{match.group(1).upper()}"
                    self.findings.append((kind, finding))
            if is_main and EMAIL.search(line):
                self.findings.append(
                    (f"{label}/HARDCODED_EMAIL", f"{name}:{line_number}")
                )

    def scan_current_tree(self) -> None:
        output = self.git_bytes("ls-files", "-z").decode()
        paths = [path for path in output.split("\0") if path]
        for name in paths:
            self.scanned["tracked_files"] += 1
            normalized = name.replace("\\", "/").lower()
            if normalized.startswith("test/"):
                self.findings.append(("GIT/LOCAL_TEST_TRACKED", name))
            if self.is_dangerous_name(name):
                self.findings.append(("GIT/DANGEROUS_FILENAME", name))
            path = self.root / name
            if not path.is_file():
                continue
            self.scan_bytes(
                "GIT",
                name,
                path.read_bytes(),
                text_rules=path.suffix.lower() in TEXT_SUFFIXES,
            )

    def scan_release_history(self) -> None:
        revision_range = f"{self.base_ref}..{self.target_ref}"
        commits = self.git_bytes(
            "rev-list", "--reverse", revision_range
        ).decode().split()
        for commit in commits:
            output = self.git_bytes(
                "ls-tree", "-r", "--name-only", "-z", commit
            ).decode()
            paths = [path for path in output.split("\0") if path]
            for name in paths:
                suffix = PurePosixPath(name).suffix.lower()
                if suffix not in TEXT_SUFFIXES:
                    continue
                self.scanned["history_snapshots"] += 1
                data = self.git_bytes("show", f"{commit}:{name}")
                self.scan_bytes(
                    "HISTORY",
                    f"{commit[:12]}:{name}",
                    data,
                    text_rules=True,
                )

    def scan_release_directory(self) -> None:
        assert self.release_dir is not None
        distribution = self.release_dir / "GeotagPhoto"
        if not distribution.is_dir():
            raise RuntimeError(f"Distribution directory is missing: {distribution}")
        for path in distribution.rglob("*"):
            if not path.is_file():
                continue
            name = path.relative_to(self.release_dir).as_posix()
            self.scanned["release_files"] += 1
            if self.is_dangerous_name(name):
                self.findings.append(("DIST/DANGEROUS_FILENAME", name))
            self.scan_bytes(
                "DIST",
                name,
                path.read_bytes(),
                text_rules=path.suffix.lower() in TEXT_SUFFIXES,
            )
        notes = self.release_dir / "release_notes.md"
        if not notes.is_file():
            raise RuntimeError(f"Release notes are missing: {notes}")
        self.scan_bytes(
            "DIST", "release_notes.md", notes.read_bytes(), text_rules=True
        )

    def scan_release_zip(self) -> None:
        assert self.zip_path is not None
        if not self.zip_path.is_file():
            raise RuntimeError(f"Release ZIP is missing: {self.zip_path}")
        with zipfile.ZipFile(self.zip_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                self.scanned["zip_entries"] += 1
                if self.is_dangerous_name(name):
                    self.findings.append(("ZIP/DANGEROUS_FILENAME", name))
                suffix = PurePosixPath(name).suffix.lower()
                self.scan_bytes(
                    "ZIP",
                    name,
                    archive.read(info),
                    text_rules=suffix in TEXT_SUFFIXES,
                )

    def run(self) -> int:
        self.scan_current_tree()
        self.scan_release_history()
        if self.release_dir is not None:
            self.scan_release_directory()
            self.scan_release_zip()

        if self.findings:
            print("credential-exposure-check: FAIL")
            for kind, location in sorted(set(self.findings)):
                print(f"{kind}: {location}")
            return 1

        print("credential-exposure-check: PASS")
        for key, value in self.scanned.items():
            print(f"{key}: {value}")
        print("secret-values-printed: no")
        return 0


def resolve_inside(root: Path, value: str, label: str) -> Path:
    path = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} must be inside the repository: {path}") from error
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", default=".")
    parser.add_argument("--base-ref")
    parser.add_argument("--target-ref", default="HEAD")
    parser.add_argument("--release-dir")
    parser.add_argument("--zip-path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.repository_root).resolve()
    if not (root / ".git").exists():
        raise RuntimeError(f"Not a Git repository root: {root}")
    if bool(args.release_dir) != bool(args.zip_path):
        raise ValueError("--release-dir and --zip-path must be specified together")

    base_ref = args.base_ref
    if base_ref is None:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", "main"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode:
            raise RuntimeError(
                "Could not determine the previous release tag from main: "
                + result.stderr.strip()
            )
        base_ref = result.stdout.strip()

    release_dir = (
        resolve_inside(root, args.release_dir, "release directory")
        if args.release_dir
        else None
    )
    zip_path = (
        resolve_inside(root, args.zip_path, "ZIP path") if args.zip_path else None
    )
    scanner = CredentialScanner(
        root=root,
        base_ref=base_ref,
        target_ref=args.target_ref,
        release_dir=release_dir,
        zip_path=zip_path,
    )
    return scanner.run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as error:
        print(f"credential-exposure-check: ERROR: {error}", file=sys.stderr)
        raise SystemExit(2) from error
