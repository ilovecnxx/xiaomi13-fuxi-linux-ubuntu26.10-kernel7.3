# Licensing Scope

This repository does not grant a new blanket open-source license. Original
project files without an explicit license retain their existing copyright and
permissions. A copyright holder must approve a license grant for those files;
automation cannot make that decision for every contributor.

Upstream code, reconstructed kernel sources, third-party patches and copied
assets retain their upstream or per-file licenses. This document does not
override GPL, LGPL, BSD, Apache or any other pre-existing license. In particular,
Linux kernel modifications remain subject to their applicable kernel licenses.

Before distributing a binary, provide the applicable license notices and the
complete corresponding source, build scripts and exact patches as required by
that license. A source manifest is useful provenance, but does not replace an
obligation to supply corresponding source. Preserve attribution and notices.

Private firmware, calibration data, captured proprietary protocol payloads,
device backups and credentials are not licensed for redistribution by this
project. Availability in another public repository is not evidence of permission.

See `THIRD_PARTY.md` for the current inventory. The scoped `v0.9.0-rc1`
prerelease approval covers only the GPL Linux kernel package and its
corresponding source inputs; it does not grant a blanket license for this
repository or clear the excluded compatibility DTB, firmware, calibration data,
or other project components. A release that includes those components still
requires clearance from their rights holders. Local builds and tests do not
assert that such clearance has been obtained.
