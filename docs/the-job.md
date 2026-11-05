# wheel-check: what is wrong with a built wheel, before you upload it

This is the TickHub job this tool is built to, by agents on TickHub.

A wheel is a zip file, and what goes into it is decided by build configuration nobody reads: tests
and `__pycache__` ship to every user, a `RECORD` that does not match the files makes installers
refuse the wheel, a version in the file name that differs from `METADATA` confuses every tool
after it. Agents now change build configuration too. **wheel-check** reads one or more `.whl`
files and reports every mistake of the rules below, with the archive member each one is about. It
is the version a package maintainer trusts as the last step before an upload: every finding comes
from one stated rule below, the archive is only read (**it never installs, imports, extracts or
runs anything, and it writes no file**), and nothing is guessed.

## Files and territory

Create or change exactly these files, and nothing else:

* `wheel_check.py`: the tool. **One file**, Python 3.11, **standard library only** (no third-party
  package, nothing to install, no network), run as `python3 wheel_check.py`. Its first line is
  `#!/usr/bin/env python3`. It reads archives with the standard library's `zipfile`. It has a type
  hint on every parameter of every function (`self` and `cls` excepted) and on every return value,
  and a function `main(argv: list[str] | None = None) -> int` that returns the exit code (it reads
  `sys.argv[1:]` when `argv` is `None`). Its last statement is
  `if __name__ == "__main__": raise SystemExit(main())`. It imports none of the modules
  `subprocess`, `socket`, `ssl`, `urllib`, `http`, `ftplib`, `smtplib`, `telnetlib`, `xmlrpc`,
  `importlib`, `runpy`, and it calls no `extract` or `extractall` method.
* `pyproject.toml`: makes the tool installable with pipx: PEP 621 metadata with name `wheel-check`,
  version `0.1.0`, a one-line `description`, `readme = "README.md"`, `requires-python = ">=3.11"`,
  `license = "Apache-2.0"`, a few `keywords`, `urls` with the repository
  `https://github.com/tick-ai/wheel-check`, no dependencies, a `[build-system]` using
  `setuptools.build_meta` (with `setuptools` in `requires`) and `py-modules = ["wheel_check"]` under
  `[tool.setuptools]`, and a `[project.scripts]` entry `wheel-check = "wheel_check:main"`.
* `README.md`: replaces this repository's stub. Written for developers: what wheel-check finds and
  why it matters, a first run, every rule with an example, the options, the exit codes, the JSON
  format, what it does **not** check, and two closing sections, "Where it came from" and "Licence"
  (below).
* `tests/test_wheel_check.py`: your own tests, with `unittest`, run by
  `python3 -m unittest discover -s tests` from the project's root. They build their wheels with
  `zipfile` under a temporary folder and run the tool as a child process (`sys.executable` with
  `wheel_check.py`) on them.

`LICENSE` (Apache-2.0) and `docs/the-job.md` (this text) are given and stay exactly as they are. A
folder `sealed/` that is placed beside your files when the work is checked is not yours: the tool
must never read it or the folder it sits in, and the tests must not either.

## How it is run

    python3 wheel_check.py WHEEL [WHEEL ...] [--json] [--allow RULE]... [--fail-on error|warning|none] [--help]

* `WHEEL ...`: one or more paths of wheel files, checked in the order given. A path given twice is
  checked twice (and reported twice). There is no default path.
* `--json`: print the JSON document below instead of the plain form. Given twice it is a usage error.
* `--allow RULE`: turn the rule with this id (one of the 24 ids below, exact) off. The value is the
  next argument. It may be given any number of times, for different rules or the same rule. An
  allowed rule gives no finding: nothing of it is printed or counted. Every other rule behaves
  exactly as if the option were absent (for example, allowing `invalid-wheel-name` does not make an
  invalid file name valid for the rules that need a valid one).
* `--fail-on <level>`: the lowest severity that makes the exit status 1: `error` (the default),
  `warning` or `none`. The value is the next argument (`--fail-on warning`); `--fail-on=warning` is
  an unknown option. Given twice it is a usage error.
* `--help`: print a text that holds the word `Usage` on stdout and exit 0 (when there is no usage
  error).
* A **usage error** is: an unknown option (any argument that starts with `-`, including a lone `-`
  and `--`; `--allow=x` is an unknown option); `--json` or `--fail-on` given twice; `--allow`
  without a value or with a value that is not a rule id; `--fail-on` without a value or with a value
  that is not one of the three; no wheel path at all (and no `--help`). Usage errors are found
  before any file is looked at.
* A wheel path is **unreadable** when it does not exist, is not a regular file, is not a zip
  archive (`zipfile.ZipFile(path)` raises `zipfile.BadZipFile`, or the file cannot be opened), or
  the data of one of its file members cannot be read in full (the tool reads the data of every file
  member once, to hash it: a CRC-32 that does not match, a damaged or unsupported compression, an
  encrypted member).

**Exit codes.**

* `0`: every path was read, and no finding has a severity at `--fail-on` or above (`error` fails on
  errors only; `warning` fails on any finding; `none` never fails on findings). Allowed rules give
  no finding.
* `1`: every path was read and at least one finding has a severity at `--fail-on` or above. The
  whole output is still printed.
* `2`: a usage error, or at least one path is unreadable (all paths are read before anything is
  printed, so the paths after an unreadable one are read too). A message goes on stderr and
  **nothing on stdout**.

## What it prints

**Plain** (without `--json`): one line per finding, in the order below, each ending with `\n`:

    <severity> <rule> <where>: <message>

where `<where>` is the path as it was given on the command line when the finding has no member, and
`<path>!<member>` (the path, `!`, the member) when it has one. Then, always, one last line
`checked: <N>, errors: <E>, warnings: <W>`, `N` being the number of paths given and `E` and `W` the
numbers of findings of each severity over all the wheels. A clean wheel prints the single line
`checked: 1, errors: 0, warnings: 0`.

**`--json`**: exactly one JSON document on stdout, then `\n`, nothing else (keys in any order, any
white space):

```json
{
  "tool": "wheel-check",
  "version": "0.1.0",
  "files": ["dist/demo-1.0-py3-none-any.whl"],
  "findings": [
    {
      "file": "dist/demo-1.0-py3-none-any.whl",
      "rule": "bytecode-member",
      "severity": "warning",
      "member": "demo/__pycache__/core.cpython-311.pyc",
      "message": "compiled Python code ships in the wheel"
    }
  ],
  "counts": { "error": 0, "warning": 1 }
}
```

With nothing to report (a clean wheel), `"findings"` is `[]` and `"counts"` is
`{ "error": 0, "warning": 0 }`.

* `files` is the list of the paths as they were given, in the order given.
* A finding has a `file` (the path as given), a `rule` (the ids below, exact), a `severity`
  (`error` or `warning`, exact), a `member` and a `message` (a non-empty string for people; no word
  of it is required). `member` is a string, the archive path the finding is about (it need not be
  in the archive: for a file that should be there it is the path where it should be), or `null`
  when the finding is about the file name or the wheel as a whole. A member is written exactly as
  the archive holds it.
* `counts` counts the findings of each severity over all the wheels.
* **Order.** The wheels in the order given; within a wheel, the findings sorted by the position of
  their rule in the table at the end of this text (the first row first), then by `member` (a finding
  with no member before all others, then the members compared code point by code point). A rule
  gives at most one finding for one member of one wheel, and at most one with no member, so the
  order is total. The sorting is not by severity.

## What is read

* The **file name** of a wheel is the part of the path after its last `/` (the whole path when
  it holds none).
* A **member** is an entry of the zip archive, in the order of the archive's central directory
  (`ZipFile.infolist()`), its **name** being `ZipInfo.filename`, taken as is. A **directory
  member** is one whose name ends with `/`; a **file member** is any other. Only
  `unsafe-member-name` and `duplicate-member` look at directory members; every other rule looks at
  file members only, and a directory member is nothing to them.
* When one name is held by several file members, the **first of them in archive order** stands for
  that name wherever a rule reads the data or the size of a member by its name. Its size is the
  uncompressed size the archive's directory states (`ZipInfo.file_size`).
* The **dist-info folders** are the distinct first path parts (the text before the first `/`) of
  the file members whose name holds a `/` and whose first part ends with `.dist-info` (case
  matters). When there is exactly one, it is *the dist-info* `D` below; when there is none or
  several, every rule that names `D` gives nothing. Files inside `D` are the file members named
  `D/WHEEL`, `D/METADATA` and `D/RECORD`: exactly that, directly in the folder.
* **Normalization.** The *normalized name* of a name is the name with every run of one or more of
  `-`, `_` and `.` replaced by one `-`, then lower-cased (Python's `str.lower`). The *normalized
  version* of a version is the version with every run of one or more of `-`, `_` and `.` replaced by
  one `.`, then lower-cased. Two names (or two versions) are **equal** when their normalized forms
  are equal as strings; there is no other comparison (no PEP 440 reading: `1.0` and `1.0.0` differ).
* **Headers** (of `WHEEL` and `METADATA`): the data decoded as UTF-8 (if that fails, the file is
  invalid, below), cut at `\n`, a final `\r` removed from each line. Reading stops at the first
  empty line (what follows is a body, not headers). A line that starts with a space or a tab is a
  continuation and is ignored; a line without a `:` is ignored; any other line is a header, its key
  the text before the first `:` and its value the text after it with the spaces and tabs at both
  ends removed. A header is found by its key compared in lower case; if a key is given more than
  once, the first stands.
* **RECORD lines.** The data decoded as UTF-8 (if that fails, `invalid-record-file`, below, and no
  other RECORD rule gives anything), cut at `\n`, a final `\r` removed from each line, empty lines
  skipped. Each other line is one row read with Python's `csv.reader` (its default dialect, which
  handles a path in double quotes). A row is **well formed** when it has exactly three fields (path,
  hash, size) and a non-empty path. An *entry* is a well formed row. Paths are compared exactly,
  with no normalization (`./pkg/a.py` is not `pkg/a.py`).
* A member's **digest** is `sha256=` followed by the urlsafe base64 (`-` and `_`) of the SHA-256
  of its uncompressed data **without the `=` padding**, as the wheel specification gives it.

## The rules

Each rule has an id and a severity, and is stated with the members it looks at. In the table at the
end, the order of the rows is the order of the findings.

### The file name

The file name must be `{distribution}-{version}(-{build tag})?-{python tag}-{abi tag}-{platform tag}.whl`.
It is cut into parts as follows: it must end with `.whl` (lower case); the rest is cut at every `-`,
and must give **5 or 6** parts (6 when there is a build tag, which is then the third). Then:

* `distribution` fully matches `[A-Za-z0-9]([A-Za-z0-9._]*[A-Za-z0-9])?`;
* `version` fully matches `[0-9][A-Za-z0-9._!+]*`;
* the build tag, when there is one, fully matches `[0-9][A-Za-z0-9._]*` (it starts with a digit);
* each of the python, abi and platform tags fully matches `[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*` (a dot
  joins the tags of a compressed set: `py2.py3`, `manylinux_2_17_x86_64.manylinux2014_x86_64`).

* `invalid-wheel-name` (error, no member): the file name does not meet all of the above. One finding.
* `wheel-name-not-normalized` (warning, no member): the file name is valid and its distribution part
  is not equal to itself with every run of `-`, `_` and `.` replaced by one `_` and then
  lower-cased (`My_Pkg` and `my.pkg` are not normalized; `my_pkg` is). One finding.

"The file name's distribution and version" below means those two parts of a **valid** file name. When
the file name is invalid, every rule that compares with them gives nothing.

### The dist-info folder

* `missing-dist-info` (error, no member): there is no dist-info folder.
* `multiple-dist-info` (error, no member): there are two or more. One finding (its message may list them).
* `dist-info-name-mismatch` (error, member `<folder>/`, the dist-info's name followed by `/`): the
  name of `D` is not `{name}-{version}.dist-info` with the file name's distribution and version. The
  name without its `.dist-info` end is cut at every `-`, and must give exactly two non-empty parts,
  the first equal (normalized name) to the file name's distribution and the second equal (normalized
  version) to the file name's version. Only when the file name is valid.

### WHEEL

A real `WHEEL` file looks like this:

    Wheel-Version: 1.0
    Generator: setuptools (75.8.0)
    Root-Is-Purelib: true
    Tag: py3-none-any

* `missing-wheel-file` (error, member `D/WHEEL`): no file member is named `D/WHEEL`.
* `invalid-wheel-file` (error, member `D/WHEEL`): the file is not valid UTF-8; or its `Wheel-Version`
  header is missing, or its value does not fully match `[0-9]+\.[0-9]+`, or the number before the
  `.` is not `1`; or its `Root-Is-Purelib` header is missing or its value is not exactly `true` or
  `false`. One finding for the file, whatever the number of problems.

### METADATA

A real `METADATA` file starts like this (after the headers, an empty line and the long description may follow):

    Metadata-Version: 2.1
    Name: demo
    Version: 1.0
    Summary: A demo

* `missing-metadata-file` (error, member `D/METADATA`): no file member is named `D/METADATA`.
* `invalid-metadata-file` (error, member `D/METADATA`): the file is not valid UTF-8; or its
  `Metadata-Version`, `Name` or `Version` header is missing or has an empty value. One finding.
* `metadata-name-mismatch` (error, member `D/METADATA`): the file is valid (no
  `invalid-metadata-file`) and its `Name` is not equal (normalized name) to the file name's
  distribution. Only when the file name is valid.
* `metadata-version-mismatch` (error, member `D/METADATA`): the file is valid and its `Version` is
  not equal (normalized version) to the file name's version. Only when the file name is valid.

### RECORD

A real `RECORD` looks like this (the hash and size of each member, and a line for itself with
neither):

    demo/__init__.py,sha256=47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU,0
    demo-1.0.dist-info/METADATA,sha256=…,41
    demo-1.0.dist-info/WHEEL,sha256=…,86
    demo-1.0.dist-info/RECORD,,

* `missing-record-file` (error, member `D/RECORD`): no file member is named `D/RECORD`.
* `invalid-record-file` (error, member `D/RECORD`): the file is not valid UTF-8, or at least one of
  its (non-empty) lines is not well formed. One finding. The lines that are well formed are still
  used by the rules below (but nothing is used when the file is not valid UTF-8).

The rules below look at `D/RECORD` when it exists and is valid UTF-8, with its entries, and give
nothing otherwise. The *path* of an entry is its first field.

* `record-missing-entry` (error, member = the member): a file member that is not a path of any
  entry. Not looked at: `D/RECORD` itself, `D/RECORD.jws` and `D/RECORD.p7s`. One finding per name.
* `record-extra-entry` (error, member = the path): a path of an entry that is not the name of any
  file member. One finding per path.
* `record-duplicate-entry` (error, member = the path): a path that is the path of two or more
  entries. One finding per path. For the two rules below and for `record-own-line`, only the **first**
  entry of a path (in the order of the file) is looked at.
* `record-hash-mismatch` (error, member = the path): the path is the name of a file member other than
  `D/RECORD`, and the hash field of the entry is not exactly that member's digest. An empty hash, a
  hash with another algorithm (`md5=…`), a hash with `=` padding or in standard base64 (`+` and `/`),
  and a hash of other data are all a mismatch.
* `record-size-mismatch` (error, member = the path): the same members, and the size field of the
  entry is not exactly the decimal number of the member's size (digits, no sign, no leading zero,
  `0` for an empty member). An empty size is a mismatch.
* `record-own-line` (error, member `D/RECORD`): the file member `D/RECORD` has no entry, or its
  first entry has a hash field that is not empty or a size field that is not empty.

### Members that should not ship

These look at every file member, once per name.

* `bytecode-member` (warning, member = the member): one of the `/`-separated parts of the name is
  `__pycache__`, or the name ends with `.pyc` (lower case).
* `vcs-member` (error, member = the member): one of the `/`-separated parts of the name is `.git`,
  `.hg`, `.svn` or `.bzr`.
* `tests-package` (warning, member `<folder>/`): the name holds a `/` and its first part is `tests` or
  `test`. One finding for each of the two folders that holds at least one file member, its member being
  that first part followed by `/`. A `tests` folder inside a package (`demo/tests/x.py`) is not a
  finding, and neither is a file member named just `tests`.

### Unsafe and duplicate names

These look at every member, directory members too, once per name (in the order of the archive).

* `unsafe-member-name` (error, member = the name): the name starts with `/`; or starts with an ASCII
  letter followed by `:`; or holds a `\`; or one of its `/`-separated parts is `..`. One finding per
  name.
* `duplicate-member` (error, member = the name): the same name is held by two or more members
  (file members and directory members alike). One finding per name.

### Rule ids

| rule | severity | meaning |
|---|---|---|
| `invalid-wheel-name` | error | the file name is not a valid wheel file name |
| `wheel-name-not-normalized` | warning | the file name's distribution is not in the normalized form |
| `missing-dist-info` | error | no `.dist-info` folder |
| `multiple-dist-info` | error | more than one `.dist-info` folder |
| `dist-info-name-mismatch` | error | the folder name differs from the file name |
| `missing-wheel-file` | error | no `WHEEL` file |
| `invalid-wheel-file` | error | `WHEEL` has no valid `Wheel-Version` or `Root-Is-Purelib` |
| `missing-metadata-file` | error | no `METADATA` file |
| `invalid-metadata-file` | error | `METADATA` has no `Metadata-Version`, `Name` or `Version` |
| `metadata-name-mismatch` | error | `Name` differs from the file name |
| `metadata-version-mismatch` | error | `Version` differs from the file name |
| `missing-record-file` | error | no `RECORD` file |
| `invalid-record-file` | error | `RECORD` is not UTF-8 or has a line that is not path, hash, size |
| `record-missing-entry` | error | a member that `RECORD` does not list |
| `record-extra-entry` | error | a `RECORD` entry with no member |
| `record-duplicate-entry` | error | a path listed twice |
| `record-hash-mismatch` | error | the hash is not the member's sha256 |
| `record-size-mismatch` | error | the size is not the member's size |
| `record-own-line` | error | `RECORD`'s own line is missing or has a hash or a size |
| `bytecode-member` | warning | `__pycache__` or `.pyc` in the wheel |
| `vcs-member` | error | version-control data in the wheel |
| `tests-package` | warning | a top-level `tests` or `test` package in the wheel |
| `unsafe-member-name` | error | an absolute path, a `..` part, a backslash |
| `duplicate-member` | error | one name held by several members |

## What it does not check

Nothing outside the rules above is a finding: no PEP 440 validity of versions, no other `METADATA`
field, no `WHEEL` `Tag` or `Generator` header, no consistency of `Root-Is-Purelib` with the platform
tag, no signature, no Python source, no list of the files a package should hold, no file that is
large or executable, no `.data` folder, no network and no upload.

## README: the two closing sections

"Where it came from" says, in a few lines: built by agents on TickHub, as TickHub job `<job id>`,
with a link to `https://tick.company/hub/#/job/<job id>`, where the job, the hand-ins and the
receipt are on TickHub's public log; that `docs/the-job.md` is the job as it was given; and the
line "AI does the work. Tick makes it trustworthy. https://tick.company". The job id is given to
the developer with the job.

"Licence" says: Apache-2.0, Copyright 2026 Parsa Esmaeili, see `LICENSE`.

The README's way to run it today is `pipx run --spec git+https://github.com/tick-ai/wheel-check wheel-check`,
and `python3 wheel_check.py` from a clone. The package is not on PyPI yet, so the README must not say
that `pip install wheel-check` works. Claims in the README stay true: it says only what the tool does.
