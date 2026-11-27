#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Paragraph-aware Telegram Markdown v1 chunker & sanitizer for Daily TA report."""

from __future__ import annotations

import json
import sys


def sanitize_telegram_md_v1(text: str) -> str:
    """Ensure safe Telegram legacy Markdown v1 (no raw '_', '`', '[' and balanced '*' per line)."""
    out_lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        # Replace underscores/backticks/square-brackets that break Telegram legacy Markdown parser
        line = raw_line.replace("`", "").replace("_", " ").replace("[", "（").replace("]", "）")
        # Convert accidental **bold** to Telegram v1 *bold*
        while "**" in line:
            line = line.replace("**", "*")
        # Ensure even number of '*' on every single line so splits never break entities
        if line.count("*") % 2 == 1:
            last_idx = line.rfind("*")
            line = line[:last_idx] + line[last_idx + 1 :]
        out_lines.append(line)
    return "\n".join(out_lines).strip()


def split_paragraphs_safe(text: str, max_len: int = 3500) -> list[str]:
    """Split sanitized text on '\\n\\n' (fallback '\\n') so no chunk exceeds max_len."""
    clean = sanitize_telegram_md_v1(text)
    if not clean:
        return []
    if len(clean) <= max_len:
        return [clean]

    paragraphs = clean.split("\n\n")
    chunks: list[str] = []
    cur = ""

    def _flush() -> None:
        nonlocal cur
        if cur.strip():
            chunks.append(cur.strip())
        cur = ""

    for para in paragraphs:
        p = para.strip()
        if not p:
            continue
        if len(p) <= max_len:
            cand = f"{cur}\n\n{p}" if cur else p
            if len(cand) <= max_len:
                cur = cand
            else:
                _flush()
                cur = p
        else:
            # Fallback: split oversized paragraph line-by-line
            _flush()
            for line in p.split("\n"):
                ln = line.strip()
                if not ln:
                    continue
                if len(ln) > max_len:
                    # Safe hard-cut on plain sanitized line (strip '*' if cut)
                    for idx in range(0, len(ln), max_len):
                        piece = sanitize_telegram_md_v1(ln[idx : idx + max_len])
                        if piece:
                            chunks.append(piece)
                else:
                    cand_ln = f"{cur}\n{ln}" if cur else ln
                    if len(cand_ln) <= max_len:
                        cur = cand_ln
                    else:
                        _flush()
                        cur = ln
            _flush()

    _flush()
    return chunks


def main() -> None:
    raw = sys.stdin.read()
    chunks = split_paragraphs_safe(raw, max_len=3500)
    if not chunks:
        sys.stderr.write("ERROR: empty message content\n")
        sys.exit(1)
    print(json.dumps(chunks, ensure_ascii=False))


if __name__ == "__main__":
    main()
