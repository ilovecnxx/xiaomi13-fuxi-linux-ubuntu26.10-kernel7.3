# Repository Guidelines

## Start Here

Read this file and `TASKS.md` to continue work. `TASKS.md` alone owns current
priorities, pause state, source/device versions, latest results and next actions.
Open a technical document only when needed for the task. Historical reports are
evidence, not current instructions. User instructions supersede repository rules.

## Rapid Iteration

- Aim for one observable result per iteration: implement the related changes,
  run relevant checks, observe the result, then choose the next change. State a
  hypothesis and stop condition briefly for uncertain hardware experiments.
- Reuse the active task worktree and compatible build output. Preserve unrelated
  work and shared fixes. Add isolation only for conflicting changes or toolchains.
  Do not create a report, worktree or recipe layer for every small edit.
- Fix known correctness defects before deployment. Unknown hardware parameters
  can be tested as explicit hypotheses in bounded experiments. After a hardware
  stall, stop captures; resume only after recovery with a changed hypothesis or
  implementation. Host tests do not establish device acceptance.
- Continue from the latest version that passed the relevant validation. Keep a
  bootable recovery version. Revert only for a concrete regression or a user
  request, and record the reason. Do not routinely revert after successful tests.
- Record current candidate, result, blocker and next action once in `TASKS.md`.
  Keep detailed logs/artifacts under `work/`. Update plans when the plan changes;
  write a technical report only for a useful milestone or nontrivial finding.
  Routine pauses use `TASKS.md`, not new dated handoffs or backup copies.
  Delete redundant backups once their history is committed. Keep distinct
  technical evidence and dependency recipes; check references before removal.

## Verification

| Change | Development check |
| --- | --- |
| Documentation | Diff and local links: `bash scripts/fuxi-ci.sh docs` |
| Isolated script/application | Affected tests; use the application's existing runner |
| Shared scripts/build/installation behavior | Full script suite: `bash scripts/fuxi-ci.sh scripts` |
| SuperTerm | `bash scripts/fuxi-ci.sh apps` |
| Kernel/DT | Focused ARM64 `W=1`, affected DT/binding and lifecycle checks |

Do not repeat passing checks without relevant changes or an unresolved concern.
Broaden tests for shared behavior, failures and integration. Before deploying,
verify the candidate's relevant regressions, package/source/config provenance,
target boot files and recovery entry, then perform bounded device validation.
Save source changes (including new files) before building; the kernel deb builder
does this automatically. Consolidate reproducible recipes at validated milestones;
replay them when changing/consolidating a recipe, not after each source edit.

Final stability matrices apply when accepting a feature. Full clean rebuilds,
release signatures, redistribution and upstream submission requirements apply at
integration/publication stages, not as gates for every local experiment.
CI selects checks by changed paths; unknown paths and manual/weekly runs broaden
coverage. `bash scripts/fuxi-ci.sh host` remains the full local host entry.

## Ownership and Commands

- `kernel/`: maintained source recipe; `build/linux-standard/`: reconstructed tree.
- `packaging/`: installed device files, dependency recipes and build environment.
- `apps/`: application sources; `scripts/` and `scripts/tests/`: tooling and tests.
- `docs/`: technical operations, acceptance criteria and historical evidence.
- `work/`, `build/`: local outputs; `scripts/legacy/`, old overlays: historical.

Use existing patterns, Python standard-library APIs, four-space Python indentation,
and Bash with explicit error handling/quoting. Preserve executable modes.
Prepare the standard tree with `python3 scripts/fuxi-kernel-source.py prepare`.
Build with `scripts/fuxi-build-deb.sh <unique-name>`; use `FUXI_KTREE`/`FUXI_KOUT`
for the active experimental source/output. Reuse compatible output, but never
overwrite an installed/published package version. Operational instructions live
in `docs/内核维护与独立升级.md`; publication in `docs/内核持续维护与发布.md`.

## Device and Data Boundaries

Normal authorized Linux deb upgrades and normal reboots can proceed without
repeated confirmation when the task is active. Honor explicit pauses. Daily
kernel updates do not rebuild the bootloader or flash recovery. Do not write GPT,
slots, Android/firmware partitions, clear BCB, force reboot or restart the desktop
without explicit authorization for that operation. Preserve user configurations.
Do not commit proprietary binaries, generated images, private keys, raw device
logs or identity data. Do not send external messages or publish releases without
authorization. These boundaries do not require a new approval for routine fixes.

## Commits and Sync

Make a focused commit for a reviewable result, combining related small edits.
Stage only that work. Use a short subsystem prefix and concrete summary. Push
verified milestones to `main`, checking remote divergence first. Do not create a
branch or push per tiny edit. Report what changed, how it was verified and any
remaining limitation. A CI pass is not a claim of device or feature acceptance.
