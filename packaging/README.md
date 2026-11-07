# Package Ownership

`fuxi-device/root`, `fuxi-modem/root` and `fuxi-desktop/root` are the authoritative
installed files. Update these files and their package scripts together; the old
`work/rootfs-overlay` is historical, not a second deployable source of truth.

`sources/manifest.json` fixes upstream archives/files, revisions and patch hashes.
`scripts/fuxi-sources.py prepare` reconstructs them in `build/sources`; it refuses
to reuse altered trees. Original local experiments are not changed.
The `build/Containerfile` fixes the OCI base and signed APT snapshot. Build
artifacts and environment records stay under ignored `work/` and `build/`.

Build order:

1. Prepare third-party sources and the kernel source.
2. Build the pinned amd64 and arm64 environments.
3. Build hexagonrpc, ModemManager and 81voltd from source in the arm64 environment.
4. Build kernel debs independently in the amd64 environment.
5. Build device packages and publish an APT snapshot. Only kernel builds listed
   in `apt/kernels.list` are advertised; changing this list requires device evidence.

`scripts/fuxi-modem-bin-pull.sh` remains a recovery/diagnostic export tool; its
device-pulled output cannot satisfy the standard source-provenance checks.
Firmware extraction is separate and private. It is never replaced by a fabricated
"open-source firmware build". See `../docs/内核持续维护与发布.md` for commands,
release ownership decisions, signatures and client trust setup.
