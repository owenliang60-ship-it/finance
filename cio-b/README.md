# CIO-B Workspace — Geralt Kowalski

Standalone workspace housing CIO-B personality (Pattern C deployment, locked 2026-05-06).

## What this is

`Finance/cio-b/` is a dedicated CC workspace where the model takes on the persona of **Geralt Kowalski** — CIO of Westbrook Capital, Boss's CIO-B partner. Personality is loaded via `CLAUDE.md` at session start (~23,500 words inline, all 5 sections of the personality md).

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
| `CLAUDE.md` | **Source of truth** — workspace config + Sections 1-5 personality inline |
| `cards/` | Workspace mirror of conviction cards (Obsidian `Cards/Stocks/{TICKER}.md` is master) |
| `cards/Rejected/` | Workspace mirror of rejection cards (no-go list) |
| `draft/` | Section 1-5 archived drafts (frozen; CLAUDE.md is now authoritative) |
| `research/` | Psychology research backstory used to construct the persona |

## Personality at a glance

- **Backstory**: Geralt Kowalski (b. 1974, Trenton NJ, Polish-American). Princeton ORFE 1996 + Kahneman PSY 312. Dual layer: baseball (Trenton High pitcher) + poker (Princeton underground + Atlantic City mid-stakes, half-pro). Career: Salomon → Columbia MBA → Druckenmiller mentee → SAC under Cohen → Westbrook Capital 2014 ($500M, capped). Boss is the 18% LP with direct-call privilege.
- **Five beliefs** (Section 2): What is, not what should be / Conviction without sizing is a hobby / Own your stops, own your sizes / Failure is a state, not the collapse of identity / Math and read, always both.
- **Four registers** (Section 3): Calm / Push / Refuse / L3 forcing function. One loyalty, four surfaces.
- **Workflow** (Section 4): Conviction card system. Each ticker that crosses Boss's conviction band gets a card built in calm-period dialogue (8 fields including OPRMS, suggested sizing, load-bearing variable with forward/backward tag, three-layer invalidation conditions, L3 commitments). Live conversation retrieves the card and surfaces the gap. No card → either build one or build a rejection card.
- **Refusal** (Sections 3.3 + 5.2): Geralt does not pull the trigger / does not become the scapegoat / does not soothe in drawdown / does not predict short-term price / does not write Boss's third-party explanations / does not commit new theses in emotionally activated states / does not frame P&L as self-worth.
- **Arbitration** (Section 5.3): Belief × Belief → smaller bound governs / Framework × experience → experience must pinpoint a variable / Boss × Geralt → Boss is final, but override of prior commitments must be in writing.

## Working with cards

Cards are written and read as ordinary markdown. Master copy lives in Obsidian (`Cards/Stocks/{TICKER}.md`); workspace mirror at `cards/{TICKER}.md` is what Geralt loads into context first.

Rejection cards (`cards/Rejected/{TICKER}.md` and Obsidian master): three fields — date / reason / reconsideration trigger. They prevent rejected names from re-entering the conviction stack under fresh framing.

When Boss queries a ticker without a card, Geralt blocks sizing math in the live conversation and proposes either:
1. Schedule a calm-period card-creation conversation, or
2. Decide the ticker is not conviction-band material and build a rejection card.

There are exactly two exits, never one.

## What this workspace is not

- Not a coding workspace. For code work on Finance, exit to the main `Finance/` worktree.
- Not a data tool. `market.db` and `company.db` are read-only here. Data ingest stays on the cloud Data Desk.
- Not a generic chat. Every conversation is a CIO-LP session. If Boss wants generic CC behavior, use a different workspace.

## Evolving the persona

When real conversations surface failure modes (refusal triggers missing, arbitration ambiguous, voice drifting), edit `CLAUDE.md` directly. `draft/` is historical and frozen.

Section 6 (full conflict resolution) and Section 7 (sanity tests) were intentionally deferred per Boss's 5-07 directive — failure modes that emerge in live use will tell us what those sections should contain, more accurately than dry-run pre-engineering would.

## Origin trace

- 2026-05-04: CIO-B redefined from "commander discipline" to "conviction exoskeleton + L3 forcing function"; Pattern C (standalone workspace) chosen for personality strength.
- 2026-05-06: Character locked (Geralt Kowalski). Section 1 (8420w) + Section 2 (3440w) drafted.
- 2026-05-07: Section 3 (4307w) + Section 4 (4576w) + Section 5 (1935w) drafted; integration into this CLAUDE.md.
- Total personality core: ~22,680 words English drafted across Sections 1-5.
