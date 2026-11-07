# docker/openclaw/tavily-skills

Photon installs these Tavily packages into the global OpenClaw skill directory. The skill
requests use the authenticated MaraClaw engine proxy.

## File Convention

- `tavily-search/` and `tavily-crawl/` are the skill packages
  (`SKILL.md` + `references/`).
- Folder names are the canonical skill keys used by generated `openclaw.json`
  `skills.entries`.
- `manifest.json` lists the skills the installer installs (`skills`) and the
  default-install list (`default_skills`).
- Skills read exactly `TAVILY_API_BASE` (proxy base, no `/v1`) and
  `TAVILY_API_KEY` (guest proxy token) and call `${TAVILY_API_BASE}/v1/search`
  and `${TAVILY_API_BASE}/v1/crawl` with a Bearer header.

## Editing Rules

- Keep the packages limited to search and crawl, with request and response
  contracts in the bundled reference files.
- Keep `SKILL.md` frontmatter to the single-line `name`, `metadata`, and
  `description` fields that `validate-tavily.sh` accepts.
- Do not add an engine Python search handler for Tavily.

## Image Ownership

- The image installs both packages at build time and validates their assets
  at startup without credentials, network access, or workspace changes.
