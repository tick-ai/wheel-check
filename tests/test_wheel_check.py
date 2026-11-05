import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "wheel_check.py"
NAME = "demo-1.0-py3-none-any.whl"
WHEEL_TEXT = b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
METADATA_TEXT = b"Metadata-Version: 2.1\nName: demo\nVersion: 1.0\nSummary: A demo\n"


def digest(data: bytes) -> str:
    raw = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii")
    return "sha256=" + raw.rstrip("=")


def good_files() -> list:
    """The members of a clean wheel, RECORD last, as (name, bytes) pairs."""
    files = [
        ("demo/__init__.py", b""),
        ("demo/core.py", b"print('hi')\n"),
        ("demo-1.0.dist-info/WHEEL", WHEEL_TEXT),
        ("demo-1.0.dist-info/METADATA", METADATA_TEXT),
    ]
    return files + [("demo-1.0.dist-info/RECORD", record_for(files, "demo-1.0.dist-info/RECORD"))]


def record_for(files: list, own: str) -> bytes:
    lines = [f"{n},{digest(d)},{len(d)}" for n, d in files if not n.endswith("/")]
    lines.append(f"{own},,")
    return ("\n".join(lines) + "\n").encode("utf-8")


class Result:
    def __init__(self, proc: subprocess.CompletedProcess) -> None:
        self.code = proc.returncode
        self.out = proc.stdout.decode("utf-8")
        self.err = proc.stderr.decode("utf-8")

    def lines(self) -> list:
        return self.out.splitlines()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def wheel(self, files: list, name: str = NAME) -> str:
        """Write a wheel with these members; a name ending in / is a directory member."""
        path = self.dir / name
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # zipfile warns about duplicate names, which tests want
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                for member, data in files:
                    archive.writestr(member, data)
        return str(path)

    def run_tool(self, *args: str) -> Result:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, str(TOOL), *args], cwd=self.tmp.name,
            capture_output=True, env=env, timeout=60,
        )
        return Result(proc)

    def findings(self, files: list, name: str = NAME, *extra: str) -> list:
        """(rule, member) of every finding of a wheel, in the order printed."""
        path = self.wheel(files, name)
        result = self.run_tool(path, "--json", *extra)
        self.assertIn(result.code, (0, 1), result.err)
        return [(f["rule"], f["member"]) for f in json.loads(result.out)["findings"]]

    def replaced(self, name: str, data: bytes, fix_record: bool = True) -> list:
        files = [(n, data if n == name else d) for n, d in good_files()]
        if fix_record:
            files[-1] = ("demo-1.0.dist-info/RECORD", record_for(files[:-1], "demo-1.0.dist-info/RECORD"))
        return files

    def with_record(self, text: str) -> list:
        return good_files()[:-1] + [("demo-1.0.dist-info/RECORD", text.encode("utf-8"))]


class CleanAndOutputTests(Base):
    def test_clean_wheel_plain(self) -> None:
        result = self.run_tool(self.wheel(good_files()))
        self.assertEqual(result.out, "checked: 1, errors: 0, warnings: 0\n")
        self.assertEqual(result.code, 0)
        self.assertEqual(result.err, "")

    def test_clean_wheel_json(self) -> None:
        path = self.wheel(good_files())
        result = self.run_tool(path, "--json")
        self.assertEqual(result.code, 0)
        self.assertTrue(result.out.endswith("}\n"))
        self.assertEqual(json.loads(result.out), {
            "tool": "wheel-check", "version": "0.1.0", "files": [path],
            "findings": [], "counts": {"error": 0, "warning": 0},
        })

    def test_plain_lines(self) -> None:
        files = good_files()[:-1] + [("demo/__pycache__/core.cpython-311.pyc", b"x")]
        files.append(("demo-1.0.dist-info/RECORD", record_for(files, "demo-1.0.dist-info/RECORD")))
        path = self.wheel(files)
        result = self.run_tool(path)
        self.assertEqual(result.code, 0)
        self.assertEqual(result.lines(), [
            f"warning bytecode-member {path}!demo/__pycache__/core.cpython-311.pyc: "
            + result.lines()[0].split(": ", 1)[1],
            "checked: 1, errors: 0, warnings: 1",
        ])
        self.assertTrue(result.lines()[0].split(": ", 1)[1])

    def test_plain_line_without_member_uses_path_as_given(self) -> None:
        self.wheel(good_files(), "bad.whl")
        result = self.run_tool("./bad.whl")
        self.assertEqual(result.lines()[0].split(": ")[0], "error invalid-wheel-name ./bad.whl")
        self.assertEqual(result.lines()[-1], "checked: 1, errors: 1, warnings: 0")
        self.assertEqual(result.code, 1)

    def test_json_finding_shape(self) -> None:
        files = good_files()[:-1] + [("demo/x.pyc", b"x")]
        files.append(("demo-1.0.dist-info/RECORD", record_for(files, "demo-1.0.dist-info/RECORD")))
        path = self.wheel(files)
        doc = json.loads(self.run_tool(path, "--json").out)
        self.assertEqual(doc["counts"], {"error": 0, "warning": 1})
        finding = doc["findings"][0]
        self.assertEqual(sorted(finding), ["file", "member", "message", "rule", "severity"])
        self.assertEqual((finding["file"], finding["rule"], finding["severity"], finding["member"]),
                         (path, "bytecode-member", "warning", "demo/x.pyc"))
        self.assertTrue(finding["message"])

    def test_json_member_null(self) -> None:
        self.wheel(good_files(), "bad.whl")
        doc = json.loads(self.run_tool("bad.whl", "--json").out)
        self.assertIsNone(doc["findings"][0]["member"])
        self.assertEqual(doc["findings"][0]["severity"], "error")

    def test_several_wheels_one_summary_and_repeat(self) -> None:
        good = self.wheel(good_files())
        self.wheel(good_files(), "bad.whl")
        result = self.run_tool(good, "bad.whl", good, "bad.whl")
        self.assertEqual(result.code, 1)
        lines = result.lines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("error invalid-wheel-name bad.whl: "))
        self.assertTrue(lines[1].startswith("error invalid-wheel-name bad.whl: "))
        self.assertEqual(lines[2], "checked: 4, errors: 2, warnings: 0")
        doc = json.loads(self.run_tool(good, "bad.whl", good, "--json").out)
        self.assertEqual(doc["files"], [good, "bad.whl", good])

    def test_wheels_in_order_given(self) -> None:
        self.wheel(good_files(), "b.whl")
        self.wheel(good_files(), "a.whl")
        lines = self.run_tool("b.whl", "a.whl").lines()
        self.assertIn(" b.whl: ", lines[0])
        self.assertIn(" a.whl: ", lines[1])


class ExitCodeTests(Base):
    def setUp(self) -> None:
        super().setUp()
        files = good_files()[:-1] + [("demo/x.pyc", b"x")]
        files.append(("demo-1.0.dist-info/RECORD", record_for(files, "demo-1.0.dist-info/RECORD")))
        self.warn_only = self.wheel(files, "warn-1.0-py3-none-any.whl")
        self.with_error = self.wheel(good_files(), "bad.whl")
        self.clean = self.wheel(good_files())

    def test_warning_only(self) -> None:
        # the warn-only wheel is named differently, so use a matching one
        files = [(n.replace("demo", "warn"), d) for n, d in good_files()[:-1]]
        files = [(n, d.replace(b"demo", b"warn")) for n, d in files] + [("warn/x.pyc", b"x")]
        files.append(("warn-1.0.dist-info/RECORD", record_for(files, "warn-1.0.dist-info/RECORD")))
        path = self.wheel(files, "warn-1.0-py3-none-any.whl")
        self.assertEqual(self.run_tool(path).out.splitlines()[-1], "checked: 1, errors: 0, warnings: 1")
        self.assertEqual(self.run_tool(path).code, 0)
        self.assertEqual(self.run_tool(path, "--fail-on", "error").code, 0)
        self.assertEqual(self.run_tool(path, "--fail-on", "warning").code, 1)
        self.assertEqual(self.run_tool(path, "--fail-on", "none").code, 0)

    def test_error(self) -> None:
        self.assertEqual(self.run_tool(self.with_error).code, 1)
        self.assertEqual(self.run_tool(self.with_error, "--fail-on", "error").code, 1)
        self.assertEqual(self.run_tool(self.with_error, "--fail-on", "warning").code, 1)
        result = self.run_tool(self.with_error, "--fail-on", "none")
        self.assertEqual(result.code, 0)
        self.assertEqual(len(result.lines()), 2)

    def test_clean_never_fails(self) -> None:
        for level in ("error", "warning", "none"):
            self.assertEqual(self.run_tool(self.clean, "--fail-on", level).code, 0)

    def test_allow_gives_no_finding_and_no_count(self) -> None:
        result = self.run_tool(self.with_error, "--allow", "invalid-wheel-name")
        self.assertEqual(result.out, "checked: 1, errors: 0, warnings: 0\n")
        self.assertEqual(result.code, 0)
        result = self.run_tool(self.with_error, "--allow", "invalid-wheel-name", "--fail-on", "warning", "--json")
        self.assertEqual(result.code, 0)
        self.assertEqual(json.loads(result.out)["findings"], [])


class UsageTests(Base):
    def assert_usage(self, *args: str) -> None:
        result = self.run_tool(*args)
        self.assertEqual(result.code, 2, args)
        self.assertEqual(result.out, "", args)
        self.assertNotEqual(result.err, "", args)

    def test_usage_errors(self) -> None:
        path = self.wheel(good_files())
        cases = [
            [], ["--json"], ["--fail-on", "none"], [path, "--bogus"], [path, "-"], [path, "--"], [path, "-x"],
            [path, "--allow=bytecode-member"], [path, "--json", "--json"],
            [path, "--fail-on", "none", "--fail-on", "none"], [path, "--fail-on=warning"],
            [path, "--allow"], [path, "--allow", "nope"], [path, "--allow", "BYTECODE-MEMBER"],
            [path, "--fail-on"], [path, "--fail-on", "fatal"], [path, "--fail-on", "Error"],
            [path, "--allow", "--json"], [path, "--help", "--bogus"], ["--help", "--json", "--json"],
        ]
        for case in cases:
            self.assert_usage(*case)

    def test_usage_error_before_files_are_looked_at(self) -> None:
        result = self.run_tool("missing.whl", "--bogus")
        self.assertEqual(result.code, 2)
        self.assertEqual(result.out, "")
        self.assertIn("--bogus", result.err)

    def test_help(self) -> None:
        for args in (["--help"], [self.wheel(good_files()), "--help"], ["--help", "missing.whl"]):
            result = self.run_tool(*args)
            self.assertEqual(result.code, 0, args)
            self.assertIn("Usage", result.out)

    def test_allow_may_repeat(self) -> None:
        self.wheel(good_files(), "bad.whl")
        result = self.run_tool("bad.whl", "--allow", "bytecode-member", "--allow", "bytecode-member",
                               "--allow", "invalid-wheel-name")
        self.assertEqual(result.code, 0)

    def test_every_rule_id_is_allowed(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        ids = re.findall(r'^    \("([a-z-]+)", (?:ERROR|WARNING)\),$', text, re.M)
        self.assertEqual(len(ids), 24)
        path = self.wheel(good_files())
        for rule in ids:
            self.assertEqual(self.run_tool(path, "--allow", rule).code, 0, rule)


class UnreadableTests(Base):
    def test_unreadable_paths(self) -> None:
        good = self.wheel(good_files())
        (self.dir / "notzip.whl").write_bytes(b"this is not a zip file")
        (self.dir / "empty.whl").write_bytes(b"")
        (self.dir / "folder.whl").mkdir()
        for args in (["missing.whl"], ["notzip.whl"], ["empty.whl"], ["folder.whl"],
                     [good, "missing.whl"], ["missing.whl", good], [good, "notzip.whl", "--json"],
                     [good, "folder.whl", "--fail-on", "none"]):
            result = self.run_tool(*args)
            self.assertEqual(result.code, 2, args)
            self.assertEqual(result.out, "", args)
            self.assertNotEqual(result.err, "", args)

    def test_all_paths_are_read_before_failing(self) -> None:
        result = self.run_tool("missing-one.whl", "missing-two.whl")
        self.assertEqual(result.code, 2)
        self.assertIn("missing-one.whl", result.err)
        self.assertIn("missing-two.whl", result.err)

    def test_bad_crc(self) -> None:
        path = self.wheel(good_files())
        raw = bytearray(Path(path).read_bytes())
        marker = b"print('hi')"
        # the member is deflated, so build a stored one to damage
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
            for name, data in good_files():
                archive.writestr(name, data)
        raw = bytearray(Path(path).read_bytes())
        at = raw.index(marker)
        raw[at] = ord("P")
        Path(path).write_bytes(bytes(raw))
        result = self.run_tool(path)
        self.assertEqual(result.code, 2)
        self.assertEqual(result.out, "")

    def test_encrypted_member(self) -> None:
        path = self.wheel(good_files())
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
            for name, data in good_files():
                archive.writestr(name, data)
        raw = bytearray(Path(path).read_bytes())
        # set the "encrypted" flag bit in every central directory header
        position = 0
        while True:
            position = raw.find(b"PK\x01\x02", position)
            if position < 0:
                break
            raw[position + 8] |= 1
            position += 4
        Path(path).write_bytes(bytes(raw))
        result = self.run_tool(path)
        self.assertEqual(result.code, 2)
        self.assertEqual(result.out, "")

    def test_unsupported_compression(self) -> None:
        path = self.wheel(good_files())
        raw = bytearray(Path(path).read_bytes())
        position = raw.find(b"PK\x01\x02")
        raw[position + 10] = 99  # compression method
        Path(path).write_bytes(bytes(raw))
        result = self.run_tool(path)
        self.assertEqual(result.code, 2)
        self.assertEqual(result.out, "")

    def test_wheel_is_not_written_or_changed(self) -> None:
        path = self.wheel(good_files())
        before = Path(path).read_bytes()
        names = sorted(os.listdir(self.dir))
        self.run_tool(path)
        self.assertEqual(Path(path).read_bytes(), before)
        self.assertEqual(sorted(os.listdir(self.dir)), names)


class FileNameTests(Base):
    def name_findings(self, name: str) -> list:
        files = good_files()
        return [f for f in self.findings(files, name) if f[0] in ("invalid-wheel-name", "wheel-name-not-normalized")]

    def test_valid_names(self) -> None:
        for name in ("demo-1.0-py3-none-any.whl", "demo-1.0-1-py3-none-any.whl", "demo-1.0-py2.py3-none-any.whl",
                     "demo-1.0-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
                     "a-1-py3-none-any.whl", "demo-1.0.post1+local.1!x-py3-none-any.whl",
                     "demo-1.0-1b.2-py3-none-any.whl", "demo-1-py3-none-any.whl"):
            self.assertEqual(self.name_findings(name), [], name)

    def test_invalid_names(self) -> None:
        for name in ("demo-1.0-py3-none-any.WHL", "demo-1.0-py3-none-any.zip", "demo-1.0-py3-none.whl",
                     "demo-1.0-1-2-py3-none-any.whl", "-1.0-py3-none-any.whl", "demo--py3-none-any.whl",
                     "demo-a1-py3-none-any.whl", "demo-1.0-x1-py3-none-any.whl", "demo-1.0-py3-none-.whl",
                     "demo-1.0-py3.-none-any.whl", "demo-1.0-py3-no ne-any.whl", "_demo-1.0-py3-none-any.whl",
                     "demo_-1.0-py3-none-any.whl", "de mo-1.0-py3-none-any.whl", "demo-1.0-py3-none-any.whl.whl.x",
                     "demo-1.0-py3-none-any", ".whl", "demo-1.0-py3-none-any-extra-more.whl",
                     "d\u00e9mo-1.0-py3-none-any.whl"):
            self.assertEqual(self.name_findings(name), [("invalid-wheel-name", None)], name)

    def test_name_is_after_last_slash(self) -> None:
        (self.dir / "sub").mkdir()
        path = self.dir / "sub" / NAME
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in good_files():
                archive.writestr(name, data)
        result = self.run_tool("sub/" + NAME)
        self.assertEqual(result.out, "checked: 1, errors: 0, warnings: 0\n")
        self.assertEqual(self.run_tool("./sub/../sub/" + NAME).code, 0)

    def test_not_normalized(self) -> None:
        for dist, ok in (("My_Pkg", False), ("my.pkg", False), ("my__pkg", False), ("my_pkg", True), ("Demo", False),
                         ("demo", True), ("demo2", True), ("a.b_c", False)):
            name = f"{dist}-1.0-py3-none-any.whl"
            files = [(n.replace("demo", dist), d.replace(b"demo", dist.encode())) for n, d in good_files()[:-1]]
            files.append((f"{dist}-1.0.dist-info/RECORD", record_for(files, f"{dist}-1.0.dist-info/RECORD")))
            found = self.findings(files, name)
            self.assertEqual(found, [] if ok else [("wheel-name-not-normalized", None)], dist)
        path = self.wheel(good_files(), "Demo-1.0-py3-none-any.whl")
        result = self.run_tool(path)
        self.assertTrue(result.lines()[0].startswith("warning wheel-name-not-normalized "))

    def test_invalid_name_silences_comparing_rules(self) -> None:
        files = [(n.replace("demo-1.0", "other-9"), d) for n, d in good_files()]
        files = [(n, d.replace(b"Name: demo", b"Name: zzz")) for n, d in files]
        found = [rule for rule, _ in self.findings(files, "bad.whl")]
        self.assertIn("invalid-wheel-name", found)
        for rule in ("dist-info-name-mismatch", "metadata-name-mismatch", "metadata-version-mismatch"):
            self.assertNotIn(rule, found)

    def test_allowing_name_rule_does_not_make_it_valid(self) -> None:
        files = [(n.replace("demo-1.0", "other-9"), d) for n, d in good_files()]
        found = self.findings(files, "bad.whl", "--allow", "invalid-wheel-name")
        self.assertNotIn(("dist-info-name-mismatch", "other-9.dist-info/"), found)
        self.assertFalse([f for f in found if f[0] in ("dist-info-name-mismatch", "metadata-name-mismatch")])


class DistInfoTests(Base):
    def test_missing(self) -> None:
        self.assertEqual(self.findings([("demo/__init__.py", b"")]), [("missing-dist-info", None)])

    def test_dist_info_needs_a_file_inside_a_folder(self) -> None:
        # a file member named x.dist-info (no slash) and a directory member alone do not count
        files = [("demo/__init__.py", b""), ("demo-1.0.dist-info", b"x"), ("demo-1.0.dist-info/", b"")]
        self.assertEqual(self.findings(files), [("missing-dist-info", None)])

    def test_case_matters(self) -> None:
        files = [("demo/__init__.py", b""), ("demo-1.0.DIST-INFO/WHEEL", b"")]
        self.assertEqual(self.findings(files), [("missing-dist-info", None)])

    def test_multiple(self) -> None:
        files = good_files() + [("other-2.0.dist-info/WHEEL", WHEEL_TEXT), ("other-2.0.dist-info/x", b"1")]
        found = self.findings(files)
        self.assertEqual([f for f in found if f[0] == "multiple-dist-info"], [("multiple-dist-info", None)])
        self.assertFalse([f for f in found if f[0] in (
            "dist-info-name-mismatch", "missing-wheel-file", "invalid-wheel-file", "missing-metadata-file",
            "missing-record-file", "record-missing-entry", "record-own-line")])

    def test_name_mismatch(self) -> None:
        def renamed(folder: str) -> list:
            files = [(n.replace("demo-1.0.dist-info", folder), d) for n, d in good_files()[:-1]]
            files.append((folder + "/RECORD", record_for(files, folder + "/RECORD")))
            return files
        for folder, ok in (("demo-1.0.dist-info", True), ("Demo-1.0.dist-info", True), ("demo-1.0.0.dist-info", False),
                           ("other-1.0.dist-info", False), ("demo.dist-info", False), ("demo-1.0-x.dist-info", False),
                           ("-1.0.dist-info", False), ("demo-.dist-info", False), (".dist-info", False),
                           ("demo-1_0.dist-info", True), ("DEMO-1.0.dist-info", True), ("de_mo-1.0.dist-info", False),
                           ("demo-1.0a.dist-info", False)):
            found = self.findings(renamed(folder))
            expected = [] if ok else [("dist-info-name-mismatch", folder + "/")]
            self.assertEqual([f for f in found if f[0] == "dist-info-name-mismatch"], expected, folder)

    def test_name_normalization(self) -> None:
        name = "my_pkg-1.0.post1-py3-none-any.whl"
        folder = "My.Pkg-1_0_POST1.dist-info"
        files = [("my_pkg/__init__.py", b""), (folder + "/WHEEL", WHEEL_TEXT),
                 (folder + "/METADATA", b"Metadata-Version: 2.1\nName: my-PKG\nVersion: 1.0-post1\n")]
        files.append((folder + "/RECORD", record_for(files, folder + "/RECORD")))
        self.assertEqual(self.findings(files, name), [])


class WheelFileTests(Base):
    def wheel_findings(self, data: bytes) -> list:
        return [f for f in self.findings(self.replaced("demo-1.0.dist-info/WHEEL", data))]

    def test_missing(self) -> None:
        files = [f for f in good_files() if not f[0].endswith("/WHEEL")]
        files[-1] = ("demo-1.0.dist-info/RECORD", record_for(files[:-1], "demo-1.0.dist-info/RECORD"))
        self.assertEqual(self.findings(files), [("missing-wheel-file", "demo-1.0.dist-info/WHEEL")])

    def test_valid(self) -> None:
        for data in (WHEEL_TEXT, b"Wheel-Version: 1.9\r\nRoot-Is-Purelib: false\r\n", b"wheel-version:1.0\nROOT-IS-PURELIB:  true \n",
                     b"Root-Is-Purelib: true\nWheel-Version: 1.0\n\nWheel-Version: bad\n",
                     b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nWheel-Version: 2.0\n",
                     b"Wheel-Version: 1.0\nbad line\n  continuation: x\nRoot-Is-Purelib: true",
                     b"Wheel-Version: 1.10\nRoot-Is-Purelib: true\n"):
            self.assertEqual(self.wheel_findings(data), [], data)

    def test_invalid(self) -> None:
        target = [("invalid-wheel-file", "demo-1.0.dist-info/WHEEL")]
        for data in (b"", b"Root-Is-Purelib: true\n", b"Wheel-Version: 1.0\n", b"Wheel-Version: 2.0\nRoot-Is-Purelib: true\n",
                     b"Wheel-Version: 0.9\nRoot-Is-Purelib: true\n", b"Wheel-Version: 1\nRoot-Is-Purelib: true\n",
                     b"Wheel-Version: 1.0.1\nRoot-Is-Purelib: true\n", b"Wheel-Version: 1.x\nRoot-Is-Purelib: true\n",
                     b"Wheel-Version: 1.0\nRoot-Is-Purelib: True\n", b"Wheel-Version: 1.0\nRoot-Is-Purelib: yes\n",
                     b"Wheel-Version: 1.0\nRoot-Is-Purelib:\n", b"Wheel-Version: 1.0\n\nRoot-Is-Purelib: true\n",
                     b"Wheel-Version: 1.0\n Root-Is-Purelib: true\n", b"Wheel-Version: 1.0\nRoot-Is-Purelib : true\n",
                     b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\xff\n", b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\n\xff\n",
                     b"Wheel-Version: 2.0\nRoot-Is-Purelib: maybe\n\xff"):
            self.assertEqual(self.wheel_findings(data), target, data)

    def test_one_finding_for_several_problems(self) -> None:
        self.assertEqual(self.wheel_findings(b"Wheel-Version: x\nRoot-Is-Purelib: y\n"),
                         [("invalid-wheel-file", "demo-1.0.dist-info/WHEEL")])

    def test_first_header_stands(self) -> None:
        self.assertEqual(self.wheel_findings(b"Wheel-Version: 2.0\nWheel-Version: 1.0\nRoot-Is-Purelib: true\n"),
                         [("invalid-wheel-file", "demo-1.0.dist-info/WHEEL")])

    def test_wheel_in_a_subfolder_is_not_the_wheel_file(self) -> None:
        files = [(n, d) for n, d in good_files() if not n.endswith("/WHEEL")]
        files.insert(0, ("demo-1.0.dist-info/sub/WHEEL", WHEEL_TEXT))
        files = files[:-1] + [("demo-1.0.dist-info/RECORD", record_for(files[:-1], "demo-1.0.dist-info/RECORD"))]
        self.assertEqual(self.findings(files), [("missing-wheel-file", "demo-1.0.dist-info/WHEEL")])


class MetadataFileTests(Base):
    def meta_findings(self, data: bytes) -> list:
        return self.findings(self.replaced("demo-1.0.dist-info/METADATA", data))

    def test_missing(self) -> None:
        files = [f for f in good_files() if not f[0].endswith("/METADATA")]
        files[-1] = ("demo-1.0.dist-info/RECORD", record_for(files[:-1], "demo-1.0.dist-info/RECORD"))
        self.assertEqual(self.findings(files), [("missing-metadata-file", "demo-1.0.dist-info/METADATA")])

    def test_valid(self) -> None:
        for data in (METADATA_TEXT, b"Metadata-Version: 2.1\r\nName: Demo\r\nVersion: 1_0\r\n\r\nBody\nName: x\n",
                     b"name: demo\nversion: 1.0\nmetadata-version: 2.3\nSummary:\n continued\n",
                     b"Metadata-Version: 2.1\nName:\tdemo \nVersion: 1.0\nName: zzz\n"):
            self.assertEqual(self.meta_findings(data), [], data)

    def test_invalid(self) -> None:
        target = [("invalid-metadata-file", "demo-1.0.dist-info/METADATA")]
        for data in (b"", b"Name: demo\nVersion: 1.0\n", b"Metadata-Version: 2.1\nVersion: 1.0\n",
                     b"Metadata-Version: 2.1\nName: demo\n", b"Metadata-Version:\nName: demo\nVersion: 1.0\n",
                     b"Metadata-Version: 2.1\nName:   \nVersion: 1.0\n", b"Metadata-Version: 2.1\nName: demo\nVersion: \n",
                     b"Metadata-Version: 2.1\n\nName: demo\nVersion: 1.0\n", b"Metadata-Version: 2.1\n Name: demo\nVersion: 1.0\n",
                     b"Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n\xff", b"Metadata-Version: 2.1\nName: demo\nVersion: 1.0\xc3\n",
                     b"Metadata-Version: 2.1\nName: \nName: demo\nVersion: 1.0\n"):
            self.assertEqual(self.meta_findings(data), target, data)

    def test_invalid_file_has_no_mismatch_findings(self) -> None:
        found = self.meta_findings(b"Metadata-Version: 2.1\nName: other\n")
        self.assertEqual(found, [("invalid-metadata-file", "demo-1.0.dist-info/METADATA")])

    def test_name_mismatch(self) -> None:
        self.assertEqual(self.meta_findings(b"Metadata-Version: 2.1\nName: other\nVersion: 1.0\n"),
                         [("metadata-name-mismatch", "demo-1.0.dist-info/METADATA")])
        self.assertEqual(self.meta_findings(b"Metadata-Version: 2.1\nName: Dem.o\nVersion: 1.0\n"),
                         [("metadata-name-mismatch", "demo-1.0.dist-info/METADATA")])
        self.assertEqual(self.meta_findings(b"Metadata-Version: 2.1\nName: DEMO\nVersion: 1.0\n"), [])

    def test_version_mismatch(self) -> None:
        for version in (b"1.0.0", b"2.0", b"1.0.1", b"1"):
            self.assertEqual(self.meta_findings(b"Metadata-Version: 2.1\nName: demo\nVersion: " + version + b"\n"),
                             [("metadata-version-mismatch", "demo-1.0.dist-info/METADATA")], version)

    def test_both_mismatches_in_rule_order(self) -> None:
        self.assertEqual(self.meta_findings(b"Metadata-Version: 2.1\nName: x\nVersion: 2\n"),
                         [("metadata-name-mismatch", "demo-1.0.dist-info/METADATA"),
                          ("metadata-version-mismatch", "demo-1.0.dist-info/METADATA")])


class RecordTests(Base):
    R = "demo-1.0.dist-info/RECORD"

    def record_lines(self, mutate) -> list:
        lines = record_for(good_files()[:-1], self.R).decode().splitlines()
        return mutate(lines)

    def findings_with(self, lines: list, eol: str = "\n") -> list:
        return self.findings(self.with_record(eol.join(lines) + eol))

    def test_missing(self) -> None:
        files = good_files()[:-1]
        self.assertEqual(self.findings(files), [("missing-record-file", self.R)])

    def test_not_utf8(self) -> None:
        files = good_files()[:-1] + [(self.R, b"demo/__init__.py,,\n\xff\n")]
        self.assertEqual(self.findings(files), [("invalid-record-file", self.R)])

    def test_clean_variants(self) -> None:
        lines = self.record_lines(lambda ls: ls)
        self.assertEqual(self.findings_with(lines), [])
        self.assertEqual(self.findings_with(lines, "\r\n"), [])
        self.assertEqual(self.findings_with(lines[::-1]), [])
        self.assertEqual(self.findings_with(lines[:2] + [""] + lines[2:] + [""]), [])
        quoted = ['"demo/__init__.py",' + lines[0].split(",", 1)[1]] + lines[1:]
        self.assertEqual(self.findings_with(quoted), [])

    def test_missing_entry(self) -> None:
        lines = self.record_lines(lambda ls: [l for l in ls if not l.startswith("demo/core.py")])
        self.assertEqual(self.findings_with(lines), [("record-missing-entry", "demo/core.py")])

    def test_missing_entry_exempt_files(self) -> None:
        files = good_files()
        for exempt in ("RECORD.jws", "RECORD.p7s"):
            more = files + [("demo-1.0.dist-info/" + exempt, b"sig")]
            self.assertEqual(self.findings(more), [], exempt)
        more = files + [("demo-1.0.dist-info/RECORD.asc", b"sig"), ("demo-1.0.dist-info/sub/RECORD", b"x")]
        self.assertEqual(self.findings(more), [("record-missing-entry", "demo-1.0.dist-info/RECORD.asc"),
                                               ("record-missing-entry", "demo-1.0.dist-info/sub/RECORD")])

    def test_missing_entry_ignores_directories_and_reports_once(self) -> None:
        files = good_files()[:-1] + [("demo/", b""), ("demo/data.txt", b"1"), ("demo/data.txt", b"2")]
        files.append((self.R, record_for(files[:-2], self.R)))
        found = self.findings(files)
        self.assertEqual([f for f in found if f[0] == "record-missing-entry"],
                         [("record-missing-entry", "demo/data.txt")])

    def test_extra_entry(self) -> None:
        lines = self.record_lines(lambda ls: ls + ["demo/gone.py,sha256=abc,3", "demo/,,"])
        self.assertEqual(self.findings_with(lines), [("record-extra-entry", "demo/"), ("record-extra-entry", "demo/gone.py")])

    def test_extra_entry_once_and_paths_exact(self) -> None:
        lines = self.record_lines(lambda ls: ls + ["./demo/core.py,sha256=abc,3", "./demo/core.py,sha256=abc,3"])
        found = self.findings_with(lines)
        self.assertEqual(found, [("record-extra-entry", "./demo/core.py"), ("record-duplicate-entry", "./demo/core.py")])

    def test_duplicate_entry(self) -> None:
        lines = self.record_lines(lambda ls: ls + [ls[0], ls[0]])
        self.assertEqual(self.findings_with(lines), [("record-duplicate-entry", "demo/__init__.py")])

    def test_only_first_entry_is_checked(self) -> None:
        lines = self.record_lines(lambda ls: ls + [ls[1].split(",")[0] + ",sha256=bad,99"])
        self.assertEqual(self.findings_with(lines), [("record-duplicate-entry", "demo/core.py")])
        lines = self.record_lines(lambda ls: [ls[1].split(",")[0] + ",sha256=bad,99"] + ls)
        self.assertEqual(self.findings_with(lines), [
            ("record-duplicate-entry", "demo/core.py"), ("record-hash-mismatch", "demo/core.py"),
            ("record-size-mismatch", "demo/core.py")])

    def test_hash_mismatch(self) -> None:
        good = digest(b"print('hi')\n")
        std = base64.b64encode(hashlib.sha256(b"print('hi')\n").digest()).decode()
        for bad in ("", "sha256=" + digest(b"x")[7:], "md5=abc", good + "=", "sha256=" + std + "=",
                    good.upper(), "sha256=", " " + good, "sha512=" + good[7:]):
            lines = self.record_lines(lambda ls: [f"demo/core.py,{bad},12" if l.startswith("demo/core.py") else l for l in ls])
            self.assertEqual(self.findings_with(lines), [("record-hash-mismatch", "demo/core.py")], bad)

    def test_hash_standard_base64_with_plus_slash(self) -> None:
        # find data whose digest has - or _ so standard base64 differs
        for i in range(500):
            data = str(i).encode()
            raw = hashlib.sha256(data).digest()
            if b"-" in base64.urlsafe_b64encode(raw) or b"_" in base64.urlsafe_b64encode(raw):
                break
        else:
            self.fail("no sample")
        files = [("demo/data.bin", data)] + [f for f in good_files()[:-1]]
        std = "sha256=" + base64.b64encode(raw).decode().rstrip("=")
        lines = [f"demo/data.bin,{std},{len(data)}"] + record_for(good_files()[:-1], self.R).decode().splitlines()
        self.assertEqual(self.findings(files + [(self.R, ("\n".join(lines) + "\n").encode())]),
                         [("record-hash-mismatch", "demo/data.bin")])

    def test_hash_uses_first_member_of_duplicate_name(self) -> None:
        files = good_files()[:-1] + [("demo/core.py", b"other\n")]
        files.append((self.R, record_for(files[:-1], self.R)))
        self.assertEqual(self.findings(files), [("duplicate-member", "demo/core.py")])
        files = good_files()[:-1]
        files = files[:1] + [("demo/core.py", b"other\n")] + files[1:]
        files.append((self.R, record_for(good_files()[:-1], self.R)))
        found = self.findings(files)
        self.assertIn(("record-hash-mismatch", "demo/core.py"), found)
        self.assertIn(("record-size-mismatch", "demo/core.py"), found)

    def test_size_mismatch(self) -> None:
        good = digest(b"print('hi')\n")
        for bad in ("", "13", "012", "+12", "-12", "12.0", "1 2", " 12", "0x0c", "\u0661\u0662", "x"):
            lines = self.record_lines(lambda ls: [
                f"demo/core.py,{good},{bad}" if l.startswith("demo/core.py") else l for l in ls])
            self.assertEqual(self.findings_with(lines), [("record-size-mismatch", "demo/core.py")], bad)

    def test_size_of_empty_member_is_zero(self) -> None:
        lines = self.record_lines(lambda ls: ls)
        self.assertTrue(any(l.endswith(",0") and l.startswith("demo/__init__.py") for l in lines))
        for bad in ("00", "", "0 "):
            changed = [l.rsplit(",", 1)[0] + "," + bad if l.startswith("demo/__init__.py") else l for l in lines]
            self.assertEqual(self.findings_with(changed), [("record-size-mismatch", "demo/__init__.py")], bad)

    def test_hash_and_size_both_wrong_in_rule_order(self) -> None:
        lines = self.record_lines(lambda ls: ["demo/core.py,sha256=x,1" if l.startswith("demo/core.py") else l for l in ls])
        self.assertEqual(self.findings_with(lines), [("record-hash-mismatch", "demo/core.py"), ("record-size-mismatch", "demo/core.py")])

    def test_stale_record_after_a_member_changes(self) -> None:
        files = [(n, b"changed\n" if n == "demo/core.py" else d) for n, d in good_files()]
        self.assertEqual(self.findings(files), [("record-hash-mismatch", "demo/core.py"), ("record-size-mismatch", "demo/core.py")])

    def test_own_line(self) -> None:
        for lines, expected in (
            (lambda ls: ls[:-1], [("record-own-line", self.R)]),
            (lambda ls: ls[:-1] + [self.R + ",sha256=abc,"], [("record-own-line", self.R)]),
            (lambda ls: ls[:-1] + [self.R + ",,5"], [("record-own-line", self.R)]),
            (lambda ls: ls[:-1] + [self.R + ",sha256=abc,5"], [("record-own-line", self.R)]),
            (lambda ls: ls, []),
            (lambda ls: ls + [self.R + ",sha256=abc,5"], [("record-duplicate-entry", self.R)]),
            (lambda ls: [self.R + ",sha256=abc,5"] + ls, [("record-duplicate-entry", self.R), ("record-own-line", self.R)]),
        ):
            self.assertEqual(self.findings_with(self.record_lines(lines)), expected)

    def test_record_hash_of_own_line_is_never_a_hash_mismatch(self) -> None:
        lines = self.record_lines(lambda ls: ls[:-1] + [self.R + ",sha256=abc,5"])
        self.assertNotIn("record-hash-mismatch", [r for r, _ in self.findings_with(lines)])

    def test_malformed_lines(self) -> None:
        for bad in ("onlypath", "a,b", "a,b,c,d", ",sha256=x,1", "demo/x.py,,,", '"unterminated,sha256=x,1', "   "):
            lines = self.record_lines(lambda ls: ls + [bad])
            found = self.findings_with(lines)
            self.assertIn(("invalid-record-file", self.R), found, bad)
            self.assertEqual(sum(1 for f in found if f[0] == "invalid-record-file"), 1)
            # the well formed lines are still used
            self.assertFalse([f for f in found if f[0] in ("record-missing-entry", "record-hash-mismatch")], bad)

    def test_malformed_line_does_not_hide_missing_entries(self) -> None:
        lines = self.record_lines(lambda ls: [l for l in ls if not l.startswith("demo/core.py")] + ["junk"])
        self.assertEqual(self.findings_with(lines), [("invalid-record-file", self.R), ("record-missing-entry", "demo/core.py")])

    def test_quoted_path_with_comma(self) -> None:
        data = b"x"
        files = good_files()[:-1] + [("demo/a,b.txt", data)]
        lines = record_for(files, self.R).decode().splitlines()
        lines = [f'"demo/a,b.txt",{digest(data)},1' if l.startswith("demo/a,b.txt") else l for l in lines]
        files.append((self.R, ("\n".join(lines) + "\n").encode()))
        self.assertEqual(self.findings(files), [])

    def test_record_rules_need_the_dist_info(self) -> None:
        files = [("demo/__init__.py", b""), ("RECORD", b"x,,\n")]
        self.assertEqual(self.findings(files), [("missing-dist-info", None)])

    def test_record_rules_skip_directory_members(self) -> None:
        files = good_files()[:-1] + [("demo/sub/", b"")]
        files.append((self.R, record_for(files, self.R)))
        self.assertEqual(self.findings(files), [])
        lines = record_for(good_files()[:-1], self.R).decode().splitlines() + ["demo/sub/,,"]
        files = good_files()[:-1] + [("demo/sub/", b""), (self.R, ("\n".join(lines) + "\n").encode())]
        self.assertEqual(self.findings(files), [("record-extra-entry", "demo/sub/")])


class ShipRuleTests(Base):
    def with_extra(self, *names: str) -> list:
        files = good_files()[:-1] + [(n, b"x") for n in names]
        files.append(("demo-1.0.dist-info/RECORD", record_for(files, "demo-1.0.dist-info/RECORD")))
        return files

    def test_bytecode(self) -> None:
        names = ["demo/__pycache__/core.cpython-311.pyc", "demo/core.pyc", "__pycache__/x", "a/__pycache__",
                 "demo/__pycache__/notes.txt"]
        found = self.findings(self.with_extra(*names))
        self.assertEqual(found, [("bytecode-member", n) for n in sorted(names)])

    def test_bytecode_negatives(self) -> None:
        names = ["demo/core.PYC", "demo/core.pyc.txt", "demo/__pycache__x/y", "demo/my__pycache__/y", "demo/pyc", "demo/.pyc2"]
        self.assertEqual(self.findings(self.with_extra(*names)), [])
        self.assertEqual(self.findings(self.with_extra("demo/.pyc")), [("bytecode-member", "demo/.pyc")])

    def test_bytecode_directory_member_alone_is_nothing(self) -> None:
        files = good_files()[:-1] + [("demo/__pycache__/", b"")]
        files.append(("demo-1.0.dist-info/RECORD", record_for(files, "demo-1.0.dist-info/RECORD")))
        self.assertEqual(self.findings(files), [])

    def test_vcs(self) -> None:
        names = [".git/config", "demo/.hg/x", ".svn", "a/b/.bzr/c", "demo/.git"]
        found = self.findings(self.with_extra(*names))
        self.assertEqual(found, [("vcs-member", n) for n in sorted(names)])
        self.assertEqual(self.findings(self.with_extra("demo/.github/x", "demo/.gitignore", "demo/x.git", "demo/.GIT/x")), [])

    def test_vcs_directory_member_is_nothing(self) -> None:
        files = good_files()[:-1] + [(".git/", b"")]
        files.append(("demo-1.0.dist-info/RECORD", record_for(files, "demo-1.0.dist-info/RECORD")))
        self.assertEqual(self.findings(files), [])

    def test_tests_package(self) -> None:
        found = self.findings(self.with_extra("tests/test_a.py", "tests/sub/b.py", "test/c.py"))
        self.assertEqual(found, [("tests-package", "test/"), ("tests-package", "tests/")])

    def test_tests_package_negatives(self) -> None:
        names = ["demo/tests/x.py", "demo/test/y.py", "tests", "test", "Tests/x.py", "tests2/x.py", "testing/x.py"]
        self.assertEqual(self.findings(self.with_extra(*names)), [])
        files = good_files()[:-1] + [("tests/", b"")]
        files.append(("demo-1.0.dist-info/RECORD", record_for(files, "demo-1.0.dist-info/RECORD")))
        self.assertEqual(self.findings(files), [])

    def test_one_member_can_have_several_findings(self) -> None:
        found = self.findings(self.with_extra("tests/__pycache__/x.pyc", ".git/x"))
        self.assertEqual(found, [("bytecode-member", "tests/__pycache__/x.pyc"), ("vcs-member", ".git/x"),
                                 ("tests-package", "tests/")])

    def test_reported_once_per_name(self) -> None:
        files = good_files()[:-1] + [("demo/x.pyc", b"1"), ("demo/x.pyc", b"2"), ("tests/a.py", b"1"), ("tests/a.py", b"2")]
        files.append(("demo-1.0.dist-info/RECORD", record_for(files, "demo-1.0.dist-info/RECORD")))
        found = self.findings(files)
        self.assertEqual(found.count(("bytecode-member", "demo/x.pyc")), 1)
        self.assertEqual(found.count(("tests-package", "tests/")), 1)
        self.assertEqual(found.count(("duplicate-member", "demo/x.pyc")), 1)


class UnsafeAndDuplicateTests(Base):
    def test_unsafe_names(self) -> None:
        names = ["/etc/passwd", "C:/x", "c:rel", "demo/../x", "../x", "demo/..", "a\\b", "demo\\", "z:", "/"]
        files = [(n, b"x") for n in names]
        found = self.findings(good_files() + files)
        unsafe = [m for r, m in found if r == "unsafe-member-name"]
        self.assertEqual(unsafe, sorted(names))

    def test_unsafe_negatives(self) -> None:
        names = ["demo/..x", "demo/x..", "demo/a..b/c", "1:/x", "ab:c", "demo/C:/x", ".../x", "demo/./x", "demo//x"]
        found = self.findings(good_files() + [(n, b"x") for n in names])
        self.assertEqual([m for r, m in found if r == "unsafe-member-name"], [])

    def test_unsafe_directory_members_count(self) -> None:
        found = self.findings(good_files() + [("../up/", b""), ("/abs/", b""), ("a\\b/", b""), ("ok/", b"")])
        self.assertEqual([m for r, m in found if r == "unsafe-member-name"], ["../up/", "/abs/", "a\\b/"])
        self.assertEqual([r for r, _ in found], ["unsafe-member-name"] * 3)

    def test_unsafe_file_member_has_other_findings_too(self) -> None:
        found = self.findings(good_files() + [("/abs/x.py", b"1")])
        self.assertEqual(found, [("record-missing-entry", "/abs/x.py"), ("unsafe-member-name", "/abs/x.py")])

    def test_duplicates(self) -> None:
        files = good_files()[:-1] + [("demo/core.py", b"print('hi')\n"), ("pkg/", b""), ("pkg/", b""), ("d/", b""), ("d", b"x"),
                                     ("demo/core.py", b"again")]
        files.append(("demo-1.0.dist-info/RECORD", record_for(files[:-1], "demo-1.0.dist-info/RECORD")))
        found = [f for f in self.findings(files) if f[0] == "duplicate-member"]
        self.assertEqual(found, [("duplicate-member", "demo/core.py"), ("duplicate-member", "pkg/")])

    def test_file_and_directory_of_one_name_are_not_the_same_name(self) -> None:
        files = good_files()[:-1] + [("d/", b""), ("d", b"x")]
        files.append(("demo-1.0.dist-info/RECORD", record_for(files, "demo-1.0.dist-info/RECORD")))
        self.assertEqual(self.findings(files), [])

    def test_duplicate_dist_info_file_uses_first(self) -> None:
        files = good_files()[:-1] + [("demo-1.0.dist-info/WHEEL", b"garbage")]
        files.append(("demo-1.0.dist-info/RECORD", record_for(good_files()[:-1], "demo-1.0.dist-info/RECORD")))
        self.assertEqual(self.findings(files), [("duplicate-member", "demo-1.0.dist-info/WHEEL")])


class OrderTests(Base):
    def test_sorted_by_rule_then_member_not_by_severity(self) -> None:
        names = ["zz/__pycache__/b.pyc", "aa/a.pyc", ".git/x", "tests/t.py"]
        files = good_files()[:-1] + [(n, b"1") for n in names] + [("../esc", b"1")]
        record = record_for([f for f in files if f[0] != "aa/a.pyc"], "demo-1.0.dist-info/RECORD")
        files.append(("demo-1.0.dist-info/RECORD", record))
        result = self.findings(files, "My_Demo-1.0-py3-none-any.whl")
        rules = [r for r, _ in result]
        order = [r for r in rules]
        self.assertEqual(order, sorted(order, key=rules_position))
        self.assertEqual([m for r, m in result if r == "bytecode-member"], ["aa/a.pyc", "zz/__pycache__/b.pyc"])
        self.assertIn("wheel-name-not-normalized", rules)
        self.assertIn("record-missing-entry", rules[: rules.index("bytecode-member")])

    def test_none_member_first_and_code_point_order(self) -> None:
        names = ["b.pyc", "B.pyc", "a.pyc", "\u00e9.pyc", "_.pyc"]
        files = good_files()[:-1] + [(n, b"1") for n in names]
        files.append(("demo-1.0.dist-info/RECORD", record_for(files, "demo-1.0.dist-info/RECORD")))
        found = [m for r, m in self.findings(files) if r == "bytecode-member"]
        self.assertEqual(found, sorted(names))
        self.assertEqual(found, ["B.pyc", "_.pyc", "a.pyc", "b.pyc", "\u00e9.pyc"])

    def test_plain_and_json_agree(self) -> None:
        files = good_files()[:-1] + [("demo/a.pyc", b"1"), ("demo/b.pyc", b"1")]
        path = self.wheel(files)
        plain = self.run_tool(path).lines()
        doc = json.loads(self.run_tool(path, "--json").out)
        expected = [f"{f['severity']} {f['rule']} {path}!{f['member']}: {f['message']}" if f["member"] is not None
                    else f"{f['severity']} {f['rule']} {path}: {f['message']}" for f in doc["findings"]]
        self.assertEqual(plain[:-1], expected)
        self.assertTrue(len(expected) >= 3)

    def test_non_ascii_member_names(self) -> None:
        files = good_files()[:-1] + [("demo/caf\u00e9/__pycache__/x.pyc", b"1")]
        files.append(("demo-1.0.dist-info/RECORD", record_for(files, "demo-1.0.dist-info/RECORD")))
        path = self.wheel(files)
        result = self.run_tool(path)
        self.assertIn(f"{path}!demo/caf\u00e9/__pycache__/x.pyc: ", result.out)
        self.assertEqual(json.loads(self.run_tool(path, "--json").out)["findings"][0]["member"],
                         "demo/caf\u00e9/__pycache__/x.pyc")


def rules_position(rule: str) -> int:
    ids = re.findall(r'^    \("([a-z-]+)", (?:ERROR|WARNING)\),$', TOOL.read_text(encoding="utf-8"), re.M)
    return ids.index(rule)


class DirectoryMemberTests(Base):
    def test_directory_members_are_ignored_by_the_other_rules(self) -> None:
        files = good_files()[:-1] + [
            ("demo/", b""), ("demo/__pycache__/", b""), ("tests/", b""), (".git/", b""), ("other.dist-info/", b""),
            ("demo-2.0.dist-info/", b""),
        ]
        files.append(("demo-1.0.dist-info/RECORD", record_for(files, "demo-1.0.dist-info/RECORD")))
        self.assertEqual(self.findings(files), [])

    def test_directory_member_with_wrong_record_is_extra_not_missing(self) -> None:
        files = good_files()[:-1] + [("demo/", b"")]
        files.append(("demo-1.0.dist-info/RECORD", record_for(good_files()[:-1], "demo-1.0.dist-info/RECORD")))
        self.assertEqual(self.findings(files), [])


class SourceTests(unittest.TestCase):
    def setUp(self) -> None:
        import ast
        self.text = TOOL.read_text(encoding="utf-8")
        self.tree = ast.parse(self.text)
        self.ast = ast

    def test_first_and_last_lines(self) -> None:
        lines = self.text.rstrip("\n").split("\n")
        self.assertEqual(lines[0], "#!/usr/bin/env python3")
        self.assertEqual(lines[-1], 'if __name__ == "__main__": raise SystemExit(main())')

    def test_type_hints_everywhere(self) -> None:
        ast = self.ast
        count = 0
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                count += 1
                self.assertIsNotNone(node.returns, node.name)
                args = node.args
                every = args.posonlyargs + args.args + args.kwonlyargs
                if args.vararg:
                    every.append(args.vararg)
                if args.kwarg:
                    every.append(args.kwarg)
                for arg in every:
                    if arg.arg not in ("self", "cls"):
                        self.assertIsNotNone(arg.annotation, f"{node.name}({arg.arg})")
            self.assertNotIsInstance(node, ast.Lambda)
        self.assertGreater(count, 5)

    def test_main_signature(self) -> None:
        ast = self.ast
        mains = [n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
        self.assertEqual(len(mains), 1)
        self.assertEqual(ast.unparse(mains[0].args), "argv: list[str] | None=None")
        self.assertEqual(ast.unparse(mains[0].returns), "int")

    def test_no_banned_modules_or_extraction(self) -> None:
        ast = self.ast
        banned = {"subprocess", "socket", "ssl", "urllib", "http", "ftplib", "smtplib", "telnetlib", "xmlrpc",
                  "importlib", "runpy"}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], banned)
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0], banned)
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, ("extract", "extractall"))
            if isinstance(node, ast.Name):
                self.assertNotIn(node.id, ("__import__", "eval", "exec"))

    def test_main_in_process(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("wheel_check_under_test", TOOL)
        module = importlib.util.module_from_spec(spec)
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
        self.assertEqual(module.main(["--bogus"]), 2)
        self.assertEqual(module.VERSION, "0.1.0")


class ProjectFilesTests(unittest.TestCase):
    def test_pyproject(self) -> None:
        import tomllib
        root = Path(__file__).resolve().parent.parent
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        project = data["project"]
        self.assertEqual(project["name"], "wheel-check")
        self.assertEqual(project["version"], "0.1.0")
        self.assertEqual(project["readme"], "README.md")
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["license"], "Apache-2.0")
        self.assertEqual(project["urls"]["Repository"], "https://github.com/tick-ai/wheel-check")
        self.assertEqual(project["dependencies"], [])
        self.assertEqual(project["scripts"], {"wheel-check": "wheel_check:main"})
        self.assertIn("setuptools", data["build-system"]["requires"])
        self.assertEqual(data["build-system"]["build-backend"], "setuptools.build_meta")
        self.assertEqual(data["tool"]["setuptools"]["py-modules"], ["wheel_check"])
        self.assertEqual(len(project["description"].splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
