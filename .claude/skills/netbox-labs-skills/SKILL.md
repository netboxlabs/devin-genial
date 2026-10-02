---
name: netbox-labs-skills
description: Index of the official NetBox Labs agent skills (github.com/netboxlabs/skills, Apache-2.0) and which one to load before touching each Cloud feature from this repo — Diode, Assurance/Discovery/Orb agents, Asset Lifecycle, Validation, NDX, Branching, data-model review.
---

# Official NetBox Labs skills

NetBox Labs publishes product skills in
[netboxlabs/skills](https://github.com/netboxlabs/skills) (Apache-2.0). They
carry the product's own API mechanics and caveats; read the matching one
before designing a seeder or a demo step, and cite it. Keep a local copy
under ignored `build/`:

```sh
git clone --depth 1 https://github.com/netboxlabs/skills build/vendor/netboxlabs-skills \
  || git -C build/vendor/netboxlabs-skills pull --ff-only
```

| Task in this repo | Official skill |
| --- | --- |
| Diode export/ingest, matching identities, drift twin | `netbox-diode`, `netbox-assurance` |
| Deviations: apply / ignore / rediff | `netbox-assurance` |
| Orb agents, credentials, discovery jobs, agent.yaml | `netbox-discovery` |
| Asset Lifecycle sidecar (vendors → BOM → PO → shipment → assets, spares) | `netbox-asset-lifecycle` |
| Validation policy packs, runs, findings | `netbox-validation` |
| NDX device-type enrichment (read-only; never hand-author) | `netbox-ndx` |
| Branches, reviewable loads | `netbox-branching` |
| Is this estate modelled the way a NetBox expert would? | `netbox-review-datamodel`, `netbox-data-modeling` |
| Config contexts / templates | `netbox-config-templates` |
| Platform MCP (Code Mode) | `netboxlabs-platform-mcp` |

Related in-repo skills: `drive-browser` (and the SE vault's
`drive-netbox-platform` it points to), `visual-review`, `cold-start-se`,
`showcase-tenant`.

When an official skill and this repo disagree, the live target wins: check
`/api/schema/?format=json` and `/api/status/` on the pinned target and record
what you verified.
