# docker/openclaw/linkup-skills

Vendored official [Linkup](https://github.com/LinkupPlatform/skills) OpenClaw
skills. Photon installs these packages into the global OpenClaw skill directory.
The skill requests use the authenticated MaraClaw engine proxy.

## File Convention

- `linkup-search/` and `linkup-fetch/`
  are the official skill packages (`SKILL.md` + `references/`).
- Folder names are the canonical skill keys used by generated `openclaw.json`
  `skills.entries`.
- `manifest.json` records the upstream repo/commit and default-install list.
- `.upstream-commit` pins the Git SHA used for the last sync.

## Editing Rules

- Prefer refreshing from upstream over rewriting skill prose.
- Do not add an engine Python search handler for Linkup.

## Image Ownership

- The image installs both packages at build time and validates their assets
  at startup without credentials, network access, or workspace changes.
