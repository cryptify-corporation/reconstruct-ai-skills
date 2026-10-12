# Reconstruct AI skills

[![Smithery](https://smithery.ai/badge/cryptify/reconstruct)](https://smithery.ai/servers/cryptify/reconstruct)

[Reconstruct](https://reconstruct.dev) captures web interfaces — screenshots, element trees, styles, assets, routes and recorded flows — and serves them to AI coding agents through a hosted MCP server. This repository packages that server together with nine agent skills that guide an agent from inspecting a reference interface to building, verifying and launching a product.

It works as a Claude Code plugin, a Cursor plugin, a Gemini CLI extension and a plain [Agent Skills](https://agentskills.io) collection.

## Install

### Claude Code

```text
/plugin marketplace add cryptify-corporation/reconstruct-ai-skills
/plugin install reconstruct@reconstruct
```

Run `/mcp`, select `reconstruct` and sign in with your Reconstruct account in the browser.

### Any agent with Agent Skills support

Install the skills with the [skills CLI](https://skills.sh) for Claude Code, Codex, Cursor and other agents:

```sh
npx skills add cryptify-corporation/reconstruct-ai-skills
```

Add `-a claude-code`, `-a codex` or another agent name to choose the target, and `--skill '*'` to select all skills. The same catalog is published at `https://reconstruct.dev`, so `npx skills add https://reconstruct.dev` installs identical files. The skills need the Reconstruct MCP server; connect it as described in [Connect AI agents](https://reconstruct.dev/docs/connect-ai-agents).

### Cursor

Install **Reconstruct** from the Cursor Marketplace. To try it before then, clone this repository into `~/.cursor/plugins/local/reconstruct` and run **Developer: Reload Window**.

### Gemini CLI

```sh
gemini extensions install https://github.com/cryptify-corporation/reconstruct-ai-skills
```

### MCP server only

The server is available at `https://reconstruct.dev/mcp` over Streamable HTTP with OAuth sign-in. In Claude Code:

```sh
claude mcp add --transport http reconstruct https://reconstruct.dev/mcp
```

Guides for Claude Desktop, VS Code, Codex, Cursor, Gemini CLI and other clients, including personal access tokens, are in the [documentation](https://reconstruct.dev/docs/connect-ai-agents). The server is also listed in the [MCP Registry](https://registry.modelcontextprotocol.io) as `dev.reconstruct/reconstruct`.

## Skills

| Skill | Purpose |
| --- | --- |
| `reconstruct-inspect` | Inspect a captured interface and plan a bounded reconstruction task |
| `reconstruct-design` | Extract measured design tokens and component specifications |
| `reconstruct-build` | Implement a component, page or recorded flow in your existing stack |
| `reconstruct-verify` | Compare the result with pinned capture evidence: functional, visual and accessibility checks |
| `reconstruct-backend` | Design or implement API contracts, persistence, authorization and integrations behind the UI |
| `reconstruct-entrepreneur` | Research demand and differentiation from real user reviews and competitor complaints |
| `reconstruct-brand` | Create naming, visual identity and voice; replace reference branding |
| `reconstruct-launch` | Prepare positioning, landing pages, pricing proposals and store listings |
| `reconstruct-deploy` | Prepare and verify a release: hosting, DNS, migrations and production configuration |

Each skill works on its own. Supporting templates live inside the skill's directory.

## Example

```text
Use Reconstruct to inspect the pricing page of <product>, then build it as a React component in this project and verify it against the capture.
```

More prompts: [prompt examples](https://reconstruct.dev/docs/prompt-examples).

## Access and data

Reading captures requires a Reconstruct account; the plugin never asks for your password and signs in through your browser. Captured websites are treated as untrusted data. The skills submit new capture requests only when you ask for one. See the [privacy policy](https://reconstruct.dev/privacy) and [terms](https://reconstruct.dev/terms).

## Support

[reconstruct.dev/support](https://reconstruct.dev/support) or open an issue in this repository.

## License

Reconstruct AI Skills are part of Reconstruct, a product owned by Cryptify. Copyright (c) 2026 Cryptify. All rights reserved.

These skills are provided under a proprietary license for personal or internal business use with Reconstruct. Redistribution, resale, and publication of modified versions require prior written permission from Cryptify. See [LICENSE](LICENSE).
