# Application Sources

- `superterm/`: terminal application, Python package and regression tests.
- `mihomo-gui/`: GTK client; Mihomo is an external upstream dependency.
- `dsh-phone/`: phone frontend and deployment assets; the harness is external.

Generated packages, screenshots from new experiments, caches and device logs
belong in ignored `work/` subdirectories. Historical tracked screenshots live
under `docs/assets/`. Deployment entry points are `scripts/superterm-deploy.sh`
and `scripts/fuxi-dsh-deploy.sh`. Private configuration left in the old local
`work/` directories is not migrated or published.

These paths do not change existing copyright or licensing. See `LICENSE.md`
and `THIRD_PARTY.md` before redistributing sources or binary dependencies.
