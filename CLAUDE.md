# t3-muse

Rules for agents working in this repo.

t3-muse is a companion that lets Muse drive T3 Code: Muse acts as PM and
T3 Code does the implementation. For how it is set up, see
`skills/t3-muse-starter/`. For how it is run day to day, see
`skills/t3-muse-pm/`, and for how Muse uses the bridge, see
`skills/t3-muse-bridge/`. For the architecture, see `README.md`. For the
bridge's design and security notes, see `bridge/README.md`.

## Code

- Write original code only. Do not port or closely copy any other companion
  implementation, even where the license allows it.
- Keep personal paths and identifiers out of the repo. Keep the `__HOME__`
  placeholder in the plists.

## Pull requests

- Change `main` through pull requests only. Never push to it directly.
- Merge by squash only. Never merge your own PR; merging is the user's
  decision.
- Before pushing, run every `run:` step in `.github/workflows/ci.yml` locally
  and make them pass. Report the results honestly, including failures and
  anything you skipped.

## Keeping docs in step

- When the architecture changes, update `docs/architecture.svg`,
  `docs/architecture.ja.svg`, `README.md` and `README.ja.md`. Keep the two SVGs
  structurally identical, with only the labels in each language.
- When you change anything the skills describe (`bridge/`, `tools/`,
  `installer/`, `docs/`, the READMEs, the setup guides), update the skill that
  describes it in the same PR: `skills/t3-muse-starter/SKILL.md` for setup,
  `skills/t3-muse-pm/SKILL.md` for day-to-day operation,
  `skills/t3-muse-bridge/SKILL.md` for how the bridge and its tools are used,
  or whichever of them apply.
- Write docs in English by default. When a doc also has a Japanese version,
  name it `<name>.ja.md` beside the English `<name>.md`, as with `README.md`
  and `README.ja.md` or `setup.md` and `setup.ja.md`, and keep the content of
  each pair in sync.

## Security boundaries

- Do not widen the bridge allowlist lightly.
- Do not weaken the approval flow. A human decides approvals in chat, and
  `--user-approved` on `t3a` records that decision.
- Do not read or touch the `t3a` token file (`.t3a-token`).
- If the user's machine already has another companion's install, do not read
  or touch it or its token.
