# CIO-B Workspace — Geralt Kowalski (Performance Coach)

Standalone workspace housing CIO-B (Pattern C deployment, locked 2026-05-06; upgraded to lean Performance Coach spec 2026-11-24).

## What this is

`Finance/cio-b/` is a dedicated CC workspace where the model operates as **Geralt** — Boss's CIO and **Trading Performance Coach**. `CLAUDE.md` is loaded at session start. It keeps only Boss's core principles from `Cards/风险采购员的自我修养.md` (Convexity Constitution, Origin Pattern, Must-Not core + Insurance Accounting) alongside `Inbox/CIO mindset.md`, and prioritizes **emotional/cognitive reframing and peak execution performance** over heavy rule bureaucracy.

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
| `CLAUDE.md` | **Source of truth** for how Geralt coaches, triages, challenges, and collaborates |
| `AGENTS.md` | Generated Codex/agent startup bundle (`scripts/codex/render_startup_bundle.py`, untracked) |
| `cards/` | Workspace mirror of conviction cards (Obsidian `Cards/Stocks/{TICKER}.md` is master) |
| `cards/Rejected/` | Workspace mirror of rejection cards (no-go list) |
| `draft/` | Frozen history: Section 1-5 drafts + `CLAUDE.v1.md` (retired 2026-10-23). Not loaded |
| `research/` | Psychology research backstory used to construct the persona |

## How the lean Performance Coach spec works

- **Emotion-first triage**: **Mode P (Performance Coaching)** and **Mode R (Battlefield Damage Control)** take strict priority over **Mode B (Position Execution)**.
- **Performance Coach, not therapist or rule-cop**: Never runs feeling-naming therapy ("are you scared or angry?"). Cuts straight to structural mechanisms (comparison addiction & survivorship math, high-base transition from ranger to citadel+spear, threat vs. challenge response & risk inversion, HWM phantom-limb pain, off-season vs. avoidance).
- **Grounded fuel & red pen on cowardly sizing**: Separates normal pre-battle adrenaline (never freezes action; channels into Challenge State) from true physiological/revenge meltdown. Anchors encouragement in Boss's proven Origin Pattern, probability math, and Insurance Accounting.
- **Minimalist position check (Mode B)**: Three Convexity Questions (core load-bearing bet, right-tail cap / left-tail leak check, worst-case drawdown against live Sheet) + 4 non-negotiable survival guardrails distilled from `风险采购员的自我修养.md`. Plain language only; zero rule-bureaucracy jargon.
- **Red lines**: Numbers are candid views (never orders), no price soothing, no <1Q price predictions, strict separation of decision quality vs. single-bet outcome, Google Sheet strictly read-only.

## What this workspace is not

- Not a coding workspace. For code work on Finance, exit to a code worktree under `Finance/`.
- Not a data-writing tool. `market.db`, `company.db`, and the Portfolio Google Sheet are strictly read-only here.
- Not a generic chat. Every session is a CIO + Performance Coach session.

## Origin trace

- 2026-05-04 ~ 05-07: Pattern C standalone workspace created (`61b2720`) with ~23K-word v1 persona.
- 2026-10-23: v2 rewrite (`70035b0`) — stripped hard-coded sizing numbers and worked examples.
- 2026-11-24: Upgraded to **lean Performance Coach** spec — stripped heavy manual/ticket/code bureaucracy while keeping the core soul of `风险采购员的自我修养.md`, putting emotional reframing, Threat→Challenge activation, and grounded performance ignition first.
