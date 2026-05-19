---
name: Environment report
about: Report how safeatomic behaves on a specific filesystem, OS, or platform
title: "[env] "
labels: environment
---

<!--
Environment reports help us improve the supported-environments matrix
and the capability detector. They are especially useful for:

- Tier 2 platforms (BSDs);
- borderline configurations (FUSE, overlay FS, encrypted FS, ZFS);
- NonTargets that surprisingly work or surprisingly fail;
- container / VM / sandbox setups;
- unusual mount options.

Please run doctor() and inspect_guarantees() on a representative path.
-->

## Summary

<!--
One sentence: "X works / fails / partially works on filesystem Y, OS Z."
-->

## Environment

| Field | Value |
|---|---|
| `safeatomic` version | <!-- `pip show safeatomic` -->  |
| Python version       | <!-- `python --version` -->    |
| OS / distro          | <!-- e.g. FreeBSD 14.1 -->     |
| Kernel               | <!-- `uname -a` -->            |
| Filesystem class     | <!-- ext4 / xfs / btrfs / zfs / apfs / ufs / nfs / smb / fuse / other --> |
| Mount options        | <!-- relevant line from `mount` --> |
| Container / VM       | <!-- bare metal / Docker / podman / k8s / WSL2 / VMware / Vagrant / other --> |
| Path tested          | <!-- e.g. /var/lib/app on NFSv4 --> |

## `doctor(destructive=True)` output

```python
from safeatomic import doctor
print(doctor("/path/to/your/dir", destructive=True))
```

```text
# Paste the full DoctorReport output.
```

## `inspect_guarantees()` output

```python
from safeatomic import inspect_guarantees
print(inspect_guarantees("/path/to/your/dir"))
```

```text
# Paste the full GuaranteeReport output.
```

## Observed behaviour

<!--
What works, what does not, what is surprising. Examples:

- write_atomic succeeds but reader sees partial content under high concurrency
- fsync_dir probe fails on this FUSE filesystem
- locks are not respected across two containers sharing the same volume
- doctor() classifies this as 'unknown' but it is a local POSIX FS
-->

## Test snippet (optional)

<!--
If you have a reproducible scenario, paste a minimal script using
tempfile.TemporaryDirectory so we can try to reproduce.
-->

```python
# Optional reproducer.
```

## Why this matters

<!--
Optional context: production use, CI runners, niche deployments.
Helps us prioritize.
-->
