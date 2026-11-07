# Third-Party Sources and Distribution

| Component | Authoritative source | License scope | Distribution requirement |
| --- | --- | --- | --- |
| Linux and downstream kernel changes | `kernel/manifest.json`, per-file SPDX | GPL-2.0-only and per-file exceptions | Supply exact corresponding source/config/build scripts and notices |
| U-Boot | `packaging/sources/manifest.json` | GPL-2.0-or-later and per-file exceptions | Supply corresponding source/config/build scripts and notices |
| ModemManager | same manifest; upstream COPYING and COPYING.LIB | GPL-2.0-or-later daemon; LGPL library, per-file notices | Preserve both license scopes; supply exact patched source |
| 81voltd | same manifest; per-file SPDX | GPL-2.0-or-later | Supply patched source and license notices |
| hexagonrpc | same manifest; COPYING and file headers | GPL-3.0-or-later and per-file exceptions | Supply patched source and notices |
| mkbootimg.py | pinned edk2-msm file in source manifest | Apache-2.0 header | Preserve copyright, license and applicable notices |
| Xiaomi compatibility DTB | pinned edk2-msm file in source manifest | Redistribution not cleared | Local build input; no new public release until checked |
| Xiaomi/Qualcomm firmware and calibration | `scripts/fuxi-firmware-pull.sh`, private PROVENANCE.txt | Vendor terms; not granted here | Excluded from Git and public APT publishing |
| Denial, Chromium, Mihomo, DeepSeek Harness | External downloads/installation scripts | Respective upstream licenses and services | Not relicensed by the application wrappers in `apps/` |
| Original scripts and applications | `scripts/`, `apps/`, `packaging/` | Existing per-file terms; otherwise no blanket grant | Rights-holder license decision still required |

Source hashes and full revisions are in the source manifests. Public release
must include an audit of bundled files rather than relying on the summary above
for every file. CI/test signatures do not establish copyright permission.
