# wheel-check

**wheel-check reads your built `.whl` files and tells you what is wrong with them, with the archive member each problem is about, before you upload.**

    pipx run --spec git+https://github.com/tick-ai/wheel-check wheel-check dist/*.whl

```
$ wheel-check dist/demo-1.0-py3-none-any.whl
error metadata-version-mismatch dist/demo-1.0-py3-none-any.whl!demo-1.0.dist-info/METADATA: Version '1.1' differs from the file name's '1.0'
error record-missing-entry dist/demo-1.0-py3-none-any.whl!demo/__init__.py: RECORD does not list this member
warning bytecode-member dist/demo-1.0-py3-none-any.whl!demo/__pycache__/core.cpython-311.pyc: compiled Python code ships in the wheel
warning tests-package dist/demo-1.0-py3-none-any.whl!tests/: a top-level tests package ships in the wheel
checked: 1, errors: 2, warnings: 2
$ echo $?
1
```

One line per finding, then a summary line. A clean wheel prints only `checked: 1, errors: 0, warnings: 0`.
Exit status 1 when there is a finding to fail on, so it works as the last step before an upload. It
uses only the Python 3.11 standard library. The archive is only read: wheel-check never installs,
imports, extracts or runs anything, opens no network connection and writes no file.

## Why

A wheel is a zip file, and what goes into it is decided by build configuration nobody reads: tests
and `__pycache__` ship to every user, a `RECORD` that does not match the files makes installers
refuse the wheel, a version in the file name that differs from `METADATA` confuses every tool after
it. Agents now change build configuration too. wheel-check finds these mistakes from the rules
stated below. Every finding comes from one of those rules; nothing is guessed.

## Run it

    pipx run --spec git+https://github.com/tick-ai/wheel-check wheel-check

or, from a clone of this repository (Python 3.11 or later, nothing to install):

    python3 wheel_check.py

The package is not on PyPI yet, so `pip install wheel-check` and `pipx install wheel-check` do not
work. (From a clone, `pipx install .` installs the `wheel-check` command.) Give it the wheels to
check; there is no default path.

    wheel-check WHEEL [WHEEL ...] [--json] [--allow RULE]... [--fail-on error|warning|none] [--help]

## Options

| option | meaning |
|---|---|
| `WHEEL ...` | one or more paths of wheel files, checked in the order given. A path given twice is checked twice, and reported twice. |
| `--json` | print one JSON document instead of the plain form. Given twice it is a usage error. |
| `--allow RULE` | turn the rule with this id off (one of the 24 ids below). The value is the next argument. It may be given any number of times. An allowed rule gives no finding: nothing of it is printed or counted. Every other rule behaves as if the option were absent: allowing `invalid-wheel-name` does not make an invalid file name valid for the rules that need a valid one. |
| `--fail-on LEVEL` | the lowest severity that makes the exit status 1: `error` (the default), `warning` or `none`. The value is the next argument; `--fail-on=warning` is an unknown option. Given twice it is a usage error. |
| `--help` | print the usage text on stdout and exit 0. |

A usage error is an unknown option (any argument that starts with `-`, including a lone `-` and
`--`; `--allow=x` is an unknown option); `--json` or `--fail-on` given twice; `--allow` without a
value or with a value that is not a rule id; `--fail-on` without a value or with a value that is not
one of the three; no wheel path at all (and no `--help`). Usage errors are found before any file is
looked at.

A wheel path is **unreadable** when it does not exist, is not a regular file, is not a zip archive,
or the data of one of its file members cannot be read in full. wheel-check reads the data of every
file member once, to hash it, so a CRC-32 that does not match, a damaged or unsupported compression
and an encrypted member make a wheel unreadable.

## Exit codes

| code | meaning |
|---|---|
| `0` | every path was read, and no finding has a severity at `--fail-on` or above (`error` fails on errors only, `warning` on any finding, `none` never). Allowed rules give no finding. |
| `1` | every path was read and at least one finding has a severity at `--fail-on` or above. The whole output is still printed. |
| `2` | a usage error, or at least one path is unreadable (all paths are read before anything is printed, so the paths after an unreadable one are read too). A message goes on stderr and nothing on stdout. |

## What it prints

Plain form: one line per finding, then always a summary line.

    <severity> <rule> <where>: <message>
    checked: <N>, errors: <E>, warnings: <W>

`<where>` is the path as it was given on the command line when the finding has no member, and
`<path>!<member>` when it has one. `N` is the number of paths given, `E` and `W` the numbers of
findings of each severity over all the wheels.

Order: the wheels in the order given; within a wheel, the findings sorted by the position of their
rule in the table below (the first row first), then by member (a finding with no member before all
others, then the members compared code point by code point). The sorting is not by severity. A rule
gives at most one finding for one member of one wheel, and at most one with no member.

## JSON format

`--json` prints exactly one JSON document and a newline:

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

`files` lists the paths as they were given, in the order given. A finding has the `file` (the path as
given), the `rule`, the `severity` (`error` or `warning`), a `member` and a `message` for people.
`member` is the archive path the finding is about (for a file that should be there, the path where it
should be), or `null` when the finding is about the file name or the wheel as a whole. A member is
written exactly as the archive holds it. `counts` counts the findings of each severity over all the
wheels. With nothing to report, `findings` is `[]` and `counts` is `{ "error": 0, "warning": 0 }`.

## What is read

* The **file name** of a wheel is the part of the path after its last `/`.
* A **member** is an entry of the zip archive, in the order of its central directory. A **directory
  member** is one whose name ends with `/`; a **file member** is any other. Only
  `unsafe-member-name` and `duplicate-member` look at directory members.
* When one name is held by several file members, the first of them stands for that name wherever a
  rule reads the data or the size of a member.
* The **dist-info folders** are the distinct first path parts of the file members whose name holds a
  `/` and whose first part ends with `.dist-info` (case matters). When there is exactly one, it is the
  dist-info `D`; when there is none or several, every rule that names `D` gives nothing. `D/WHEEL`,
  `D/METADATA` and `D/RECORD` are those names directly in the folder.
* **Normalization.** A normalized name has every run of `-`, `_` and `.` replaced by one `-`, then
  lower-cased; a normalized version has every run replaced by one `.`, then lower-cased. Two names (or
  versions) are equal when their normalized forms are equal as strings. There is no PEP 440 reading:
  `1.0` and `1.0.0` differ.
* **Headers** of `WHEEL` and `METADATA`: the data as UTF-8, cut at newlines, reading stops at the first
  empty line; a line that starts with a space or a tab, or has no `:`, is ignored; a key is compared in
  lower case and the first of a key stands.
* **RECORD** lines are read with Python's `csv.reader`. A row is well formed when it has exactly three
  fields (path, hash, size) and a non-empty path. Paths are compared exactly: `./pkg/a.py` is not
  `pkg/a.py`.
* A member's **digest** is `sha256=` and the urlsafe base64 of the SHA-256 of its data, without the `=`
  padding.

## The rules

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

### The file name: `invalid-wheel-name`, `wheel-name-not-normalized`

The file name must be `{distribution}-{version}(-{build tag})?-{python tag}-{abi tag}-{platform tag}.whl`:
it ends with `.whl` (lower case), and the rest cut at every `-` gives 5 or 6 parts (6 with a build tag,
the third). `distribution` fully matches `[A-Za-z0-9]([A-Za-z0-9._]*[A-Za-z0-9])?`, `version`
`[0-9][A-Za-z0-9._!+]*`, the build tag `[0-9][A-Za-z0-9._]*`, and each of the three tags
`[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*`.

```
demo-1.0-py3-none-any.whl                     # valid
demo-1.0-1-py2.py3-none-any.whl               # valid, with a build tag and a compressed tag set
demo-1.0-py3-none.whl                         # invalid-wheel-name: 4 parts
demo-1.0-py3-none-any.WHL                     # invalid-wheel-name: not .whl
My_Pkg-1.0-py3-none-any.whl                   # wheel-name-not-normalized (warning): write my_pkg
```

A valid name whose distribution differs from itself with every run of `-`, `_` and `.` replaced by one
`_` and lower-cased gives the warning. When the file name is invalid, every rule that compares with its
distribution or version gives nothing.

### The dist-info folder: `missing-dist-info`, `multiple-dist-info`, `dist-info-name-mismatch`

```
demo/__init__.py                              # no demo-1.0.dist-info/...: missing-dist-info
demo-1.0.dist-info/WHEEL + other-2.0.dist-info/WHEEL    # multiple-dist-info
demo-1.1.dist-info/ in demo-1.0-py3-none-any.whl        # dist-info-name-mismatch, member demo-1.1.dist-info/
```

The folder name without `.dist-info`, cut at every `-`, must give exactly two non-empty parts, the
first equal (normalized name) to the file name's distribution and the second equal (normalized
version) to its version. `Demo-1_0.dist-info` is fine for `demo-1.0`. The member of the finding is the
folder name followed by `/`.

### `WHEEL`: `missing-wheel-file`, `invalid-wheel-file`

```
Wheel-Version: 1.0
Generator: setuptools (75.8.0)
Root-Is-Purelib: true
Tag: py3-none-any
```

`invalid-wheel-file` (one finding, whatever the number of problems): `D/WHEEL` is not UTF-8; or its
`Wheel-Version` header is missing, does not fully match `[0-9]+\.[0-9]+`, or the number before the `.`
is not `1` (`Wheel-Version: 2.0`); or `Root-Is-Purelib` is missing or is not exactly `true` or `false`
(`True` is invalid).

### `METADATA`: `missing-metadata-file`, `invalid-metadata-file`, `metadata-name-mismatch`, `metadata-version-mismatch`

```
Metadata-Version: 2.1
Name: demo
Version: 1.0
Summary: A demo
```

`invalid-metadata-file`: not UTF-8, or the `Metadata-Version`, `Name` or `Version` header is missing or
has an empty value. When the file is valid and the file name is valid, `Name` must equal (normalized
name) the file name's distribution (`metadata-name-mismatch`) and `Version` must equal (normalized
version) the file name's version (`metadata-version-mismatch`): `Version: 1.0.0` in `demo-1.0-...` is a
mismatch.

### `RECORD`: `missing-record-file`, `invalid-record-file` and six more

```
demo/__init__.py,sha256=47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU,0
demo-1.0.dist-info/METADATA,sha256=…,41
demo-1.0.dist-info/WHEEL,sha256=…,86
demo-1.0.dist-info/RECORD,,
```

* `invalid-record-file`: `D/RECORD` is not UTF-8, or one of its non-empty lines is not well formed.
  The well formed lines are still used by the rules below, except when the file is not UTF-8.
* `record-missing-entry` (member = the member): a file member that is not the path of any entry. Not
  looked at: `D/RECORD`, `D/RECORD.jws` and `D/RECORD.p7s`.
* `record-extra-entry` (member = the path): the path of an entry that is not the name of any file
  member.
* `record-duplicate-entry` (member = the path): a path of two or more entries. For the next two rules
  and `record-own-line` only the first entry of a path is looked at.
* `record-hash-mismatch`: the hash field is not exactly the member's digest. An empty hash, another
  algorithm (`md5=…`), `=` padding, standard base64 (`+` and `/`) and a hash of other data are all a
  mismatch.
* `record-size-mismatch`: the size field is not exactly the decimal number of the member's size (no
  sign, no leading zero, `0` for an empty member, an empty size is a mismatch).
* `record-own-line`: `D/RECORD` has no entry, or its first entry has a hash or a size.

These rules give nothing when there is no single dist-info, no `D/RECORD`, or a `D/RECORD` that is not
UTF-8.

### Members that should not ship: `bytecode-member`, `vcs-member`, `tests-package`

These look at every file member, once per name.

```
demo/__pycache__/core.cpython-311.pyc         # bytecode-member (warning); so is any name ending .pyc
demo/.git/config, .hg/x, .svn/x, .bzr/x       # vcs-member (error): a part of the name is .git, .hg, .svn or .bzr
tests/test_core.py                            # tests-package (warning), member tests/
test/test_core.py                             # tests-package (warning), member test/
demo/tests/x.py                               # not a finding: a tests folder inside a package
```

`tests-package` gives one finding for each of the two folders (`tests`, `test`) that holds at least
one file member. A file member named just `tests` is not a finding.

### Unsafe and duplicate names: `unsafe-member-name`, `duplicate-member`

These look at every member, directory members too, once per name.

* `unsafe-member-name` (error): the name starts with `/`, or with an ASCII letter followed by `:`, or
  holds a `\`, or one of its `/`-separated parts is `..`.
* `duplicate-member` (error): the same name is held by two or more members, file members and
  directory members alike.

## What it does not check

Nothing outside the rules above is a finding: no PEP 440 validity of versions, no other `METADATA`
field, no `WHEEL` `Tag` or `Generator` header, no consistency of `Root-Is-Purelib` with the platform
tag, no signature, no Python source, no list of the files a package should hold, no file that is large
or executable, no `.data` folder, no network and no upload.

## Tests

    python3 -m unittest discover -s tests

## Where it came from

Built by agents on TickHub, as TickHub job `473802d2-3ee8-4017-a6fd-cac516cc8fc7`. The job, the
hand-ins and the receipt are on TickHub's public log:
https://tick.company/hub/#/job/473802d2-3ee8-4017-a6fd-cac516cc8fc7

`docs/the-job.md` is the job as it was given.

AI does the work. Tick makes it trustworthy. https://tick.company

## Licence

Apache-2.0. Copyright 2026 Parsa Esmaeili. See `LICENSE`.
