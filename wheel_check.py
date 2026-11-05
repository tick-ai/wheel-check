#!/usr/bin/env python3
"""wheel-check: find what is wrong with a built wheel, before you upload it.

It reads one or more .whl files with zipfile and reports every mistake of a fixed list of
rules, with the archive member each one is about. The archive is only read: nothing is
installed, imported, unpacked or run, and no file is written.
"""

import base64
import csv
import hashlib
import json
import os
import re
import sys
import zipfile
from typing import NamedTuple

VERSION = "0.1.0"

ERROR = "error"
WARNING = "warning"

# The rules, in the order of their findings.
RULES: list[tuple[str, str]] = [
    ("invalid-wheel-name", ERROR),
    ("wheel-name-not-normalized", WARNING),
    ("missing-dist-info", ERROR),
    ("multiple-dist-info", ERROR),
    ("dist-info-name-mismatch", ERROR),
    ("missing-wheel-file", ERROR),
    ("invalid-wheel-file", ERROR),
    ("missing-metadata-file", ERROR),
    ("invalid-metadata-file", ERROR),
    ("metadata-name-mismatch", ERROR),
    ("metadata-version-mismatch", ERROR),
    ("missing-record-file", ERROR),
    ("invalid-record-file", ERROR),
    ("record-missing-entry", ERROR),
    ("record-extra-entry", ERROR),
    ("record-duplicate-entry", ERROR),
    ("record-hash-mismatch", ERROR),
    ("record-size-mismatch", ERROR),
    ("record-own-line", ERROR),
    ("bytecode-member", WARNING),
    ("vcs-member", ERROR),
    ("tests-package", WARNING),
    ("unsafe-member-name", ERROR),
    ("duplicate-member", ERROR),
]
SEVERITY: dict[str, str] = dict(RULES)
POSITION: dict[str, int] = {rule: index for index, (rule, _) in enumerate(RULES)}
FAIL_LEVELS = ("error", "warning", "none")

DIST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9._]*[A-Za-z0-9])?")
VERSION_RE = re.compile(r"[0-9][A-Za-z0-9._!+]*")
BUILD_RE = re.compile(r"[0-9][A-Za-z0-9._]*")
TAG_RE = re.compile(r"[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*")
WHEEL_VERSION_RE = re.compile(r"([0-9]+)\.[0-9]+")
DRIVE_RE = re.compile(r"[A-Za-z]:")
SEPARATORS_RE = re.compile(r"[-_.]+")

VCS_PARTS = frozenset((".git", ".hg", ".svn", ".bzr"))
RECORD_EXEMPT = ("RECORD", "RECORD.jws", "RECORD.p7s")
CHUNK = 1 << 20

USAGE = (
    "Usage: wheel_check.py WHEEL [WHEEL ...] [--json] [--allow RULE]... "
    "[--fail-on error|warning|none] [--help]"
)


class Finding(NamedTuple):
    rule: str
    member: str | None
    message: str


class Options(NamedTuple):
    paths: list[str]
    as_json: bool
    allow: frozenset[str]
    fail_on: str
    show_help: bool


class Wheel(NamedTuple):
    names: list[str]  # every member name, directory members too, in archive order
    files: list[str]  # file member names, in archive order
    sizes: dict[str, int]  # size of the first file member of each name
    digests: dict[str, str]  # digest of the first file member of each name
    data: dict[str, bytes]  # data of the dist-info WHEEL, METADATA and RECORD files


def parse_args(args: list[str]) -> tuple[Options | None, str | None]:
    """Read the command line; return (options, None) or (None, the usage error)."""
    paths: list[str] = []
    as_json = False
    json_seen = False
    allow: set[str] = set()
    fail_on = "error"
    fail_seen = False
    show_help = False
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if arg == "--json":
            if json_seen:
                return None, "--json given twice"
            json_seen = True
            as_json = True
        elif arg == "--help":
            show_help = True
        elif arg == "--allow":
            if index >= len(args):
                return None, "--allow needs a rule id"
            value = args[index]
            index += 1
            if value not in SEVERITY:
                return None, f"--allow: not a rule id: {value}"
            allow.add(value)
        elif arg == "--fail-on":
            if fail_seen:
                return None, "--fail-on given twice"
            fail_seen = True
            if index >= len(args):
                return None, "--fail-on needs one of error, warning, none"
            value = args[index]
            index += 1
            if value not in FAIL_LEVELS:
                return None, f"--fail-on: not one of error, warning, none: {value}"
            fail_on = value
        elif arg.startswith("-"):
            return None, f"unknown option: {arg}"
        else:
            paths.append(arg)
    if not paths and not show_help:
        return None, "no wheel path given"
    return Options(paths, as_json, frozenset(allow), fail_on, show_help), None


def is_data_name(name: str) -> bool:
    """True for a dist-info WHEEL, METADATA or RECORD, whose data is kept for the rules."""
    parts = name.split("/")
    return (
        len(parts) == 2
        and parts[0].endswith(".dist-info")
        and parts[1] in ("WHEEL", "METADATA", "RECORD")
    )


def read_wheel(path: str) -> Wheel | str:
    """Open a wheel and read the data of every file member once; return a reason if unreadable."""
    try:
        if not os.path.isfile(path):
            return "does not exist or is not a regular file"
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        return "not a zip archive"
    except Exception as error:
        return f"cannot be opened: {error}"
    with archive:
        try:
            infos = archive.infolist()
        except Exception as error:
            return f"cannot be read: {error}"
        names: list[str] = []
        files: list[str] = []
        sizes: dict[str, int] = {}
        digests: dict[str, str] = {}
        data: dict[str, bytes] = {}
        for info in infos:
            name = info.filename
            names.append(name)
            if name.endswith("/"):
                continue
            files.append(name)
            first = name not in sizes
            keep = first and is_data_name(name)
            hasher = hashlib.sha256()
            kept: list[bytes] = []
            try:
                with archive.open(info) as member:
                    while True:
                        chunk = member.read(CHUNK)
                        if not chunk:
                            break
                        hasher.update(chunk)
                        if keep:
                            kept.append(chunk)
            except Exception as error:
                return f"member {name!r} cannot be read: {error}"
            if first:
                sizes[name] = info.file_size
                raw = base64.urlsafe_b64encode(hasher.digest()).decode("ascii")
                digests[name] = "sha256=" + raw.rstrip("=")
                if keep:
                    data[name] = b"".join(kept)
    return Wheel(names, files, sizes, digests, data)


def normalize_name(name: str) -> str:
    return SEPARATORS_RE.sub("-", name).lower()


def normalize_version(version: str) -> str:
    return SEPARATORS_RE.sub(".", version).lower()


def parse_wheel_name(filename: str) -> tuple[str, str] | None:
    """Return (distribution, version) of a valid wheel file name, else None."""
    if not filename.endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) not in (5, 6):
        return None
    if DIST_RE.fullmatch(parts[0]) is None or VERSION_RE.fullmatch(parts[1]) is None:
        return None
    if len(parts) == 6 and BUILD_RE.fullmatch(parts[2]) is None:
        return None
    for tag in parts[-3:]:
        if TAG_RE.fullmatch(tag) is None:
            return None
    return parts[0], parts[1]


def parse_headers(text: str) -> dict[str, str]:
    """Headers of a WHEEL or METADATA text, keyed by lower-cased key; the first of a key stands."""
    headers: dict[str, str] = {}
    for line in text.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]
        if line == "":
            break
        if line[0] in " \t" or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.lower()
        if key not in headers:
            headers[key] = value.strip(" \t")
    return headers


def decode(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def wheel_file_is_invalid(text: str | None) -> bool:
    if text is None:
        return True
    headers = parse_headers(text)
    match = WHEEL_VERSION_RE.fullmatch(headers.get("wheel-version", ""))
    if match is None or match.group(1).lstrip("0") != "1":
        return True
    return headers.get("root-is-purelib") not in ("true", "false")


def read_record(text: str) -> tuple[list[list[str]], bool]:
    """Return (the entries, whether every non-empty line is well formed)."""
    entries: list[list[str]] = []
    valid = True
    for line in text.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]
        if line == "":
            continue
        try:
            rows = list(csv.reader([line]))
        except csv.Error:
            rows = []
        row = rows[0] if len(rows) == 1 else []
        if len(row) == 3 and row[0] != "":
            entries.append(row)
        else:
            valid = False
    return entries, valid


def check_wheel(path: str, wheel: Wheel) -> list[Finding]:
    """All the findings of one wheel, unsorted."""
    found: list[Finding] = []

    def add(rule: str, member: str | None, message: str) -> None:
        found.append(Finding(rule, member, message))

    filename = path.rsplit("/", 1)[-1]
    parsed = parse_wheel_name(filename)
    if parsed is None:
        add("invalid-wheel-name", None, f"{filename!r} is not {{distribution}}-{{version}}"
            "(-{build tag})?-{python tag}-{abi tag}-{platform tag}.whl")
    else:
        dist = parsed[0]
        if dist != SEPARATORS_RE.sub("_", dist).lower():
            add("wheel-name-not-normalized", None,
                f"distribution {dist!r} should be written {SEPARATORS_RE.sub('_', dist).lower()!r}")

    file_set = set(wheel.files)

    # The dist-info folder.
    folders: list[str] = []
    for name in wheel.files:
        if "/" in name:
            first = name.split("/", 1)[0]
            if first.endswith(".dist-info") and first not in folders:
                folders.append(first)
    dist_info: str | None = None
    if not folders:
        add("missing-dist-info", None, "the wheel has no .dist-info folder")
    elif len(folders) > 1:
        add("multiple-dist-info", None, "the wheel has several .dist-info folders: " + ", ".join(folders))
    else:
        dist_info = folders[0]

    if dist_info is not None:
        if parsed is not None:
            stem = dist_info[: -len(".dist-info")]
            parts = stem.split("-")
            ok = (
                len(parts) == 2
                and parts[0] != ""
                and parts[1] != ""
                and normalize_name(parts[0]) == normalize_name(parsed[0])
                and normalize_version(parts[1]) == normalize_version(parsed[1])
            )
            if not ok:
                add("dist-info-name-mismatch", dist_info + "/",
                    f"{dist_info} does not match {parsed[0]}-{parsed[1]}.dist-info")

        wheel_path = dist_info + "/WHEEL"
        if wheel_path not in file_set:
            add("missing-wheel-file", wheel_path, f"{wheel_path} is not in the wheel")
        elif wheel_file_is_invalid(decode(wheel.data[wheel_path])):
            add("invalid-wheel-file", wheel_path,
                "WHEEL is not UTF-8, or has no valid Wheel-Version (1.x) or Root-Is-Purelib (true or false)")

        metadata_path = dist_info + "/METADATA"
        if metadata_path not in file_set:
            add("missing-metadata-file", metadata_path, f"{metadata_path} is not in the wheel")
        else:
            text = decode(wheel.data[metadata_path])
            headers = parse_headers(text) if text is not None else {}
            if text is None or any(
                headers.get(key, "") == "" for key in ("metadata-version", "name", "version")
            ):
                add("invalid-metadata-file", metadata_path,
                    "METADATA is not UTF-8, or has no Metadata-Version, Name or Version")
            elif parsed is not None:
                if normalize_name(headers["name"]) != normalize_name(parsed[0]):
                    add("metadata-name-mismatch", metadata_path,
                        f"Name {headers['name']!r} differs from the file name's {parsed[0]!r}")
                if normalize_version(headers["version"]) != normalize_version(parsed[1]):
                    add("metadata-version-mismatch", metadata_path,
                        f"Version {headers['version']!r} differs from the file name's {parsed[1]!r}")

        record_path = dist_info + "/RECORD"
        if record_path not in file_set:
            add("missing-record-file", record_path, f"{record_path} is not in the wheel")
        else:
            record_text = decode(wheel.data[record_path])
            if record_text is None:
                add("invalid-record-file", record_path, "RECORD is not valid UTF-8")
            else:
                check_record(wheel, dist_info, record_path, record_text, add_to=found)

    # Members that should not ship.
    seen: set[str] = set()
    test_folders: set[str] = set()
    for name in wheel.files:
        if name in seen:
            continue
        seen.add(name)
        parts = name.split("/")
        if "__pycache__" in parts or name.endswith(".pyc"):
            add("bytecode-member", name, "compiled Python code ships in the wheel")
        if any(part in VCS_PARTS for part in parts):
            add("vcs-member", name, "version-control data ships in the wheel")
        if len(parts) > 1 and parts[0] in ("tests", "test"):
            test_folders.add(parts[0])
    for folder in sorted(test_folders):
        add("tests-package", folder + "/", f"a top-level {folder} package ships in the wheel")

    # Unsafe and duplicate names, directory members too.
    counts: dict[str, int] = {}
    for name in wheel.names:
        counts[name] = counts.get(name, 0) + 1
    for name, count in counts.items():
        parts = name.split("/")
        if (
            name.startswith("/")
            or DRIVE_RE.match(name) is not None
            or "\\" in name
            or ".." in parts
        ):
            add("unsafe-member-name", name, "the member name is an absolute path, has a .. part or a backslash")
        if count > 1:
            add("duplicate-member", name, f"the name is held by {count} members")
    return found


def check_record(
    wheel: Wheel, dist_info: str, record_path: str, text: str, add_to: list[Finding]
) -> None:
    """The RECORD rules, for a RECORD that is valid UTF-8."""
    entries, valid = read_record(text)
    if not valid:
        add_to.append(Finding("invalid-record-file", record_path,
                              "RECORD has a line that is not path, hash, size"))
    exempt = {dist_info + "/" + name for name in RECORD_EXEMPT}
    file_set = set(wheel.files)
    by_path: dict[str, list[list[str]]] = {}
    for entry in entries:
        by_path.setdefault(entry[0], []).append(entry)

    reported: set[str] = set()
    for name in wheel.files:
        if name in reported or name in exempt or name in by_path:
            continue
        reported.add(name)
        add_to.append(Finding("record-missing-entry", name, "RECORD does not list this member"))
    for path, rows in by_path.items():
        if path not in file_set:
            add_to.append(Finding("record-extra-entry", path, "RECORD lists a path that is not in the wheel"))
        if len(rows) > 1:
            add_to.append(Finding("record-duplicate-entry", path, f"RECORD lists this path {len(rows)} times"))
        if path in file_set and path != record_path:
            row = rows[0]
            if row[1] != wheel.digests[path]:
                add_to.append(Finding("record-hash-mismatch", path,
                                      f"RECORD has {row[1]!r}, the member's digest is {wheel.digests[path]}"))
            if row[2] != str(wheel.sizes[path]):
                add_to.append(Finding("record-size-mismatch", path,
                                      f"RECORD has {row[2]!r}, the member's size is {wheel.sizes[path]}"))
    own = by_path.get(record_path)
    if own is None:
        add_to.append(Finding("record-own-line", record_path, "RECORD has no line for itself"))
    elif own[0][1] != "" or own[0][2] != "":
        add_to.append(Finding("record-own-line", record_path, "RECORD's line for itself must have no hash and no size"))


def sort_key(finding: Finding) -> tuple[int, int, str]:
    return (
        POSITION[finding.rule],
        0 if finding.member is None else 1,
        finding.member or "",
    )


def write_stream(stream_name: str, text: str) -> None:
    stream = sys.stdout if stream_name == "stdout" else sys.stderr
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(text)
        stream.flush()
        return
    stream.flush()
    try:
        raw = text.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        raw = text.encode("utf-8", "replace")
    buffer.write(raw)
    buffer.flush()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    options, error = parse_args(args)
    if options is None:
        write_stream("stderr", f"wheel-check: {error}\n{USAGE}\n")
        return 2
    if options.show_help:
        write_stream("stdout", USAGE + "\n\n"
                     "Reads wheel files and reports what is wrong with them. Nothing is installed, unpacked or run.\n"
                     "  --json            print one JSON document instead of the plain form\n"
                     "  --allow RULE      turn a rule off (may be repeated)\n"
                     "  --fail-on LEVEL   lowest severity that gives exit status 1: error (default), warning, none\n"
                     "  --help            print this text\n"
                     "Exit status: 0 nothing to fail on, 1 findings, 2 usage error or unreadable wheel.\n")
        return 0

    wheels: list[Wheel] = []
    unreadable = False
    for path in options.paths:
        result = read_wheel(path)
        if isinstance(result, str):
            unreadable = True
            write_stream("stderr", f"wheel-check: {path}: {result}\n")
        else:
            wheels.append(result)
    if unreadable:
        return 2

    findings: list[tuple[str, Finding]] = []
    for path, wheel in zip(options.paths, wheels):
        own = [f for f in check_wheel(path, wheel) if f.rule not in options.allow]
        own.sort(key=sort_key)
        findings.extend((path, f) for f in own)

    errors = sum(1 for _, f in findings if SEVERITY[f.rule] == ERROR)
    warnings = len(findings) - errors
    if options.as_json:
        document = {
            "tool": "wheel-check",
            "version": VERSION,
            "files": list(options.paths),
            "findings": [
                {
                    "file": path,
                    "rule": f.rule,
                    "severity": SEVERITY[f.rule],
                    "member": f.member,
                    "message": f.message,
                }
                for path, f in findings
            ],
            "counts": {"error": errors, "warning": warnings},
        }
        output = json.dumps(document, indent=2) + "\n"
    else:
        lines = []
        for path, f in findings:
            where = path if f.member is None else f"{path}!{f.member}"
            lines.append(f"{SEVERITY[f.rule]} {f.rule} {where}: {f.message}\n")
        lines.append(f"checked: {len(options.paths)}, errors: {errors}, warnings: {warnings}\n")
        output = "".join(lines)
    write_stream("stdout", output)

    if options.fail_on == "error" and errors > 0:
        return 1
    if options.fail_on == "warning" and findings:
        return 1
    return 0


if __name__ == "__main__": raise SystemExit(main())
