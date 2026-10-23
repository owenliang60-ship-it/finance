# CIO-B Workspace — Geralt Kowalski

Standalone workspace housing CIO-B personality (Pattern C deployment, locked 2026-05-06).

## What this is

`Finance/cio-b/` is a dedicated CC workspace where the model takes on the persona of **Geralt Kowalski** — CIO of Westbrook Capital, Boss's CIO-B partner. `CLAUDE.md` is loaded at session start. Since 2026-10-23 it is a short behavior spec (~135 lines, v2): it routes every position conversation through Boss's playbook (Obsidian `Cards/风险采购员的自我修养.md`) and holds no sizing numbers itself.

## How to start

```bash
# Direct
cd "/Users/owen/CC workspace/Finance/cio-b" && claude

# Via alias (recommended; install via ~/.zshrc)
cio
```

## What lives here

| Path | Role |
|------|------|
| `CLAUDE.md` | **Source of truth** for how Geralt works (v2 behavior spec). Investment rules live in the playbook, not here |
| `cards/` | Workspace mirror of conviction cards (Obsidian `Cards/Stocks/{TICKER}.md` is master) |
| `cards/Rejected/` | Workspace mirror of rejection cards (no-go list) |
| `draft/` | Frozen history: Section 1-5 drafts + `CLAUDE.v1.md` (the original ~23,500-word persona, retired 2026-10-23). Not loaded |
| `research/` | Psychology research backstory used to construct the persona |

## How v2 works

- **Triage** every message: research/chat · position decision · psychological moment (psych takes priority).
- **Position protocol**: mindset gate → route via playbook index → read card → live Sheet pull → clause / plan / gap table → stop at the gap; Boss writes the number.
- **Psych protocol**: name the pattern (P1–P6), cite Boss's own past case from the playbook, quote the clause, give the clause's next step.
- **Red lines**: no final numbers, no soothing, no short-term price calls, no scapegoating, no ghost-writing for third parties, no new thesis in an activated state.
- **Override**: legal if written, unconstitutional if silent (Must-Not #5).

Why v2: v1 hard-coded sizing numbers that drifted from the playbook, pointed at files that never existed, and its worked MU example got copied into unrelated answers. An 8-scenario A/B test on 2026-10-23 showed v2 matching v1 on routing, clause citation, and the no-number line, without the example leakage.

## Working with cards

Cards are written and read as ordinary markdown. Master copy lives in Obsidian (`Cards/Stocks/{TICKER}.md`); workspace mirror at `cards/{TICKER}.md` is what Geralt loads into context first.

Rejection cards (`cards/Rejected/{TICKER}.md` and Obsidian master): three fields — date / reason / reconsideration trigger. They prevent rejected names from re-entering the conviction stack under fresh framing.

When Boss queries a ticker without a card, Geralt does not compute a target (it may lay out the playbook formula range) and proposes either:
1. Schedule a calm-period card-creation conversation, or
2. Decide the ticker is not conviction-band material and build a rejection card.

There are exactly two exits, never one.

## What this workspace is not

- Not a coding workspace. For code work on Finance, exit to the main `Finance/` worktree.
- Not a data tool. `market.db` and `company.db` are read-only here. Data ingest stays on the cloud Data Desk.
- Not a generic chat. Every conversation is a CIO-LP session. If Boss wants generic CC behavior, use a different workspace.

## Evolving the persona

New investment rules or failure modes go into the **playbook**, not `CLAUDE.md`. Edit `CLAUDE.md` only when the way Geralt works changes. Do not add example dialogues; they get copied. `draft/` is historical and frozen.

## Origin trace

- 2026-05-04: CIO-B redefined from "commander discipline" to "conviction exoskeleton + L3 forcing function"; Pattern C (standalone workspace) chosen for personality strength.
- 2026-05-06: Character locked (Geralt Kowalski). Section 1 (8420w) + Section 2 (3440w) drafted.
- 2026-05-07: Section 3 (4307w) + Section 4 (4576w) + Section 5 (1935w) drafted; integration into this CLAUDE.md.
- Total personality core: ~22,680 words English drafted across Sections 1-5.
- 2026-10-23: v2 rewrite. `CLAUDE.md` cut from ~146.8K to ~8.3K chars; backstory retired to `draft/CLAUDE.v1.md`; all numbers delegated to the playbook.
