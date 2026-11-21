# Comprehension Gate

A Claude Code plugin that won't let Claude keep writing code for you until you show you understand what it just wrote.

## How it works

1. Claude writes or edits code as usual. The plugin counts how many lines changed.
2. When Claude finishes a turn that changed **15+ lines**, it has to end with 1–2 quick questions about *why* the change works: a design choice, an edge case, what would break. No "what does line 14 do" trivia.
3. Code-writing tools (Edit, Write, most Bash, subagents) are now **locked**. Claude can still read and search files and run `git diff`.
4. Your next message counts as your answer. Claude grades it for understanding, not wording.
   - **Pass:** unlocked, and Claude carries on.
   - **Miss:** Claude explains the missing idea and asks one different question. After 3 misses it walks you through the answer and unlocks.
5. Concepts you pass twice count as mastered and aren't asked about again, so checks get rarer and harder over time.

## Escape hatches

| Type | Effect |
|---|---|
| `!skip` (optionally followed by your request) | Bypass this one check. Logged in your stats. |
| `!gate off` / `!gate on` | Turn the gate off or on for the current session. |
| `/gate-status` | See passes, misses, skip rate and mastered concepts. |

Your skip rate is the mirror. Nobody else sees it.

## Install

You need Claude Code and `python3` on your PATH. Inside Claude Code, run:

```
/plugin marketplace add PRANAVKALLA230106/comprehension-gate
/plugin install comprehension-gate@comprehension-gate
```

Restart Claude Code, then run `/hooks` to confirm the four hooks are registered.

## Configuration (environment variables)

- `GATE_MIN_LINES`: lines changed before a check triggers (default `15`)
- `GATE_MASTERY`: passes before a concept counts as mastered (default `2`)
- `GATE_HOME`: where stats are stored (default `~/.claude/comprehension-gate`)

## Honest limitations

- Claude does the grading itself, so this is a commitment device, not an anti-cheat system. It works because you chose to turn it on.
- Line count is a rough measure of "meaningful change." A 3-line fix to tricky concurrency code won't trigger it; you can lower `GATE_MIN_LINES`.
- If anything in the script errors, it fails open (it never breaks your session).

## License

MIT
