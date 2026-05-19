---
name: Bug report
about: Report unexpected behaviour in safeatomic
title: "[bug] "
labels: bug
---

<!--
Before opening, please check:
- docs/troubleshooting.md — many surprises are documented there
- docs/supported-environments.md — NFS / SMB / Windows are NonTargets
- existing issues — your bug may already be tracked

The more of the fields below you can fill in, the faster this gets fixed.
-->

## What happened

<!-- One or two sentences. What did you expect, what did you observe. -->

## Minimal reproducer

```python
# Smallest possible script that triggers the bug.
# Please use tempfile.TemporaryDirectory so we can run it as-is.

import tempfile
from pathlib import Path
from safeatomic import write_atomic, read_atomic

with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "example.json"
    # ...
```

## Stack trace

```text
# Full traceback, including the exception type from safeatomic.
```

## `doctor()` output

<!--
Run on the actual path (or its parent directory) where the bug happens.
destructive=True is preferred — it tells us which probes pass and fail.
-->

```python
from safeatomic import doctor
print(doctor("/path/to/your/file_or_parent", destructive=True))
```

```text
# Paste the full DoctorReport output here.
```

## `inspect_guarantees()` output

```python
from safeatomic import inspect_guarantees
print(inspect_guarantees("/path/to/your/file_or_parent"))
```

```text
# Paste the full GuaranteeReport output here.
```

## Environment

| Field | Value |
|---|---|
| `safeatomic` version | <!-- `pip show safeatomic` -->  |
| Python version       | <!-- `python --version` -->    |
| OS / distro          | <!-- e.g. Ubuntu 24.04 -->     |
| Kernel               | <!-- `uname -r` -->            |
| Filesystem           | <!-- `df -T <path>` -->        |
| Path location        | <!-- local disk / NFS / SMB / container volume / external drive --> |
| Mounted via          | <!-- `mount` line for the path's mount point --> |

## Anything else

<!--
Logs, screenshots, related issues, what you have already tried.
If you suspect a specific module (locks, checksum, doctor, capabilities),
say so — guesses are useful.
-->
