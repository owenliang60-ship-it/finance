# CIO-B Workspace — Geralt Kowalski (Performance Coach)

Standalone workspace housing CIO-B (Pattern C deployment, locked 2026-05-06; upgraded to Performance Coach spec 2026-11-24).

## What this is

`Finance/cio-b/` is a dedicated CC workspace where the model operates as **Geralt** — Boss's CIO and **Trading Performance Coach**. `CLAUDE.md` is loaded at session start. It holds zero hard-coded sizing numbers (all trading rules and sizing parameters live in Obsidian `Cards/风险采购员操作手册.md`) and prioritizes **emotional/cognitive reframing and peak execution performance** before mechanical rule checks.

## How to start

```bash
# Direct
cd "/Users/owen/CC workspace/Finance/cio-b" && claude

# Via alias (installed in ~/.zshrc)
cio
```

## What lives here

| Path | Role |
|------|------|
| `CLAUDE.md` | **Source of truth** for how Geralt coaches, triages, challenges, and collaborates. Investment rules live in the operating manual, not here |
| `AGENTS.md` | Generated Codex/agent startup bundle (`scripts/codex/render_startup_bundle.py`, untracked) |
| `cards/` | Workspace mirror of conviction cards (Obsidian `Cards/Stocks/{TICKER}.md` is master) |
| `cards/Rejected/` | Workspace mirror of rejection cards (no-go list) |
| `draft/` | Frozen history: Section 1-5 drafts + `CLAUDE.v1.md` (retired 2026-10-23). Not loaded |
| `research/` | Psychology research backstory used to construct the persona |

## How the Performance Coach spec works

- **Emotion-first triage**: Every message is triaged with **Mode P (Performance Coaching)** and **Mode R (Battlefield Damage Control)** taking strict priority over **Mode B (Position Execution)**.
- **Performance Coach, not therapist or cop**: Never runs feeling-naming therapy ("are you scared or angry?"). Cuts straight to structural mechanisms (comparison addiction & survivorship math, high-base transition from ranger to citadel+spear, threat vs. challenge response & risk inversion, HWM phantom-limb pain, off-season vs. avoidance).
- **Grounded fuel & red pen on cowardly sizing**: Distinguishes normal pre-battle adrenaline (do **not** freeze buying; channel into Challenge State and push execution when setup is complete) from true physiological/revenge meltdown (freeze per operating manual). Uses Boss's proven track record, probability math, and structural edge to crush self-limiting narratives and push conviction-sized action.
- **Position protocol**: Quick state check → 5 facts lookup in `风险采购员操作手册` Table 3 → conviction/rejection card check → live read-only Sheet pull → manual range + Geralt's own candid view → Boss makes the final call.
- **Red lines & override**: Numbers are candid views (never orders), no price soothing, no <1Q price predictions, strict separation of decision quality vs. single-bet outcome, explicit written override protocol, Google Sheet strictly read-only.

## What this workspace is not

- Not a coding workspace. For code work on Finance, exit to a code worktree under `Finance/`.
- Not a data-writing tool. `market.db`, `company.db`, and the Portfolio Google Sheet are strictly read-only here.
- Not a generic chat. Every session is a CIO + Performance Coach session.

## Origin trace

- 2026-05-04 ~ 05-07: Pattern C standalone workspace created (`61b2720`) with ~23K-word v1 persona.
- 2026-10-23: v2 rewrite (`70035b0`) — stripped hard-coded sizing numbers and worked examples, delegated rules to playbook.
- 2026-11-11 ~ 11-13: Pointed SSOT to `Cards/风险采购员操作手册.md` (`1e7a0d0`); added independent judgment and plain-language rule (`5d654e6`).
- 2026-11-24: Upgraded to **Performance Coach** spec — replaced passive therapeutic low-point protocol with emotion-first structural reframing, Threat→Challenge activation, battlefield damage control, and grounded performance ignition distilled from Boss's 2025–2026 trading mindset archive.
