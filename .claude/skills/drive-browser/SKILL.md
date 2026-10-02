---
name: drive-browser
description: Attach to the operator's logged-in Chromium over the DevTools protocol (port 9222) with a zero-dependency helper to read, navigate, click and screenshot NetBox and app.netboxlabs.com. Thin companion to the SE vault's drive-netbox-platform skill, which owns the platform knowledge.
---

# Drive the operator's browser

**Read the SE vault's `drive-netbox-platform` skill first** — it is the canonical
guide: why a real logged-in browser, the nine Visual Explorer view paths,
readiness polling instead of sleeps, map-camera limits, per-profile VE config,
leaf-location floorplans, token invalidation on tenant restarts. It lives in
`~/Github/se-vault/skills/drive-netbox-platform/SKILL.md` (unmerged at the time
of writing: `git -C ~/Github/se-vault show
drive-netbox-waits-and-map-camera:skills/drive-netbox-platform/SKILL.md`).
Do not duplicate it here; add platform lessons there.

This file only adds the dependency-free driver used from this repo. `cdp.mjs`
needs Node 22+ (built-in `fetch`/`WebSocket`), no `npm install`:

```sh
D=.claude/skills/drive-browser/cdp.mjs
node $D - tabs                                   # which tabs exist
node $D crsk8600 nav https://crsk8600.cloud.netboxapp.com/dcim/sites/
node $D crsk8600 shot build/review/sites.png     # then Read the PNG
node $D crsk8600 shot build/review/site.png full # full page, capped 6000px
node $D app.netboxlabs text                      # innerText (SPA: wait for real counts)
node $D app.netboxlabs click 'Visual Explorer'   # CSS selector or exact visible text
node $D crsk8600 eval 'document.querySelectorAll("tr").length'
```

The first argument picks the tab by URL or title substring. If
`curl -s 127.0.0.1:9222/json` lists nothing, the browser was started without
`--remote-debugging-port` — ask the operator; never launch a second browser
(it would not carry their sessions).

Repo-specific rules:

- It is the operator's real session: read-only unless the task authorizes the
  write, and only on the tenant the task names.
- One driver per tab — parallel agents get separate tabs or take turns.
- Save evidence under `build/` and pair every visual claim with the REST count
  for the same scope (`visual-review` skill).
