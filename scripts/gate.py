#!/usr/bin/env python3
"""Comprehension Gate: a Claude Code hook that makes you show you understand
the code Claude just wrote before Claude writes more.

One script, several subcommands, each wired to a hook in hooks/hooks.json:

  post-tool  (PostToolUse on Edit/Write/MultiEdit)  record how much code changed
  stop       (Stop)                                 if enough changed, make Claude ask check questions
  prompt     (UserPromptSubmit)                     treat your next message as the answer; handle !skip / !gate
  pre-tool   (PreToolUse)                           while locked, deny code-writing tools
  verdict    (run by Claude via Bash)               record pass/fail after grading your answer
  status     (run by you or /gate-status)           show your stats

State lives in ~/.claude/comprehension-gate/ (override with GATE_HOME).
"""
import json
import os
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("GATE_HOME", Path.home() / ".claude" / "comprehension-gate"))
SESSIONS = HOME / "sessions"
PROFILE = HOME / "profile.json"
LOG = HOME / "log.jsonl"
SCRIPT = Path(__file__).resolve()

MIN_LINES = int(os.environ.get("GATE_MIN_LINES", "15"))   # change size that triggers a check
MASTERY = int(os.environ.get("GATE_MASTERY", "2"))        # passes before a concept stops being asked about
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READONLY_BASH = ("git diff", "git show", "git log", "git status", "ls", "cat ", "head ", "tail ", "wc ")


# ---------- storage ----------

def _load(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def session_path(sid):
    safe = "".join(c for c in str(sid) if c.isalnum() or c in "-_") or "default"
    return SESSIONS / f"{safe}.json"


def load_session(sid):
    return _load(session_path(sid), {
        "state": "idle",        # idle | awaiting  (awaiting = locked until a pass)
        "enabled": True,
        "lines": 0,
        "files": [],
        "attempts": 0,
    })


def save_session(sid, s):
    _save(session_path(sid), s)


def load_profile():
    return _load(PROFILE, {"concepts": {}, "passes": 0, "fails": 0, "skips": 0, "checks": 0})


def log(event, **kw):
    HOME.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps({"t": int(time.time()), "event": event, **kw}) + "\n")


def emit(obj):
    print(json.dumps(obj))
    sys.exit(0)


def read_input():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def count_lines(tool, ti):
    if tool == "Write":
        return len((ti.get("content") or "").splitlines())
    if tool == "Edit":
        return len((ti.get("new_string") or "").splitlines())
    if tool == "MultiEdit":
        return sum(len((e.get("new_string") or "").splitlines()) for e in ti.get("edits", []))
    if tool == "NotebookEdit":
        return len((ti.get("new_source") or "").splitlines())
    return 0


def mastered(profile):
    return [c for c, n in profile["concepts"].items() if n >= MASTERY]


# ---------- hooks ----------

def cmd_post_tool():
    data = read_input()
    tool = data.get("tool_name", "")
    if tool not in WRITE_TOOLS:
        sys.exit(0)
    sid = data.get("session_id", "default")
    s = load_session(sid)
    ti = data.get("tool_input", {}) or {}
    s["lines"] += count_lines(tool, ti)
    fp = ti.get("file_path") or ti.get("notebook_path")
    if fp and fp not in s["files"]:
        s["files"].append(fp)
    save_session(sid, s)
    sys.exit(0)


def cmd_stop():
    data = read_input()
    if data.get("stop_hook_active"):
        sys.exit(0)  # we already made Claude continue once; let it stop now
    sid = data.get("session_id", "default")
    s = load_session(sid)
    if not s["enabled"] or s["state"] == "awaiting" or s["lines"] < MIN_LINES:
        sys.exit(0)

    profile = load_profile()
    known = mastered(profile)
    s["state"] = "awaiting"
    s["attempts"] = 0
    save_session(sid, s)
    profile["checks"] += 1
    _save(PROFILE, profile)
    log("check", session=sid, lines=s["lines"], files=s["files"])

    files = ", ".join(s["files"][:8]) or "the files you edited"
    known_txt = (f" The user has already shown solid understanding of: {', '.join(known)}. "
                 "Don't ask about those; look for something they haven't been tested on.") if known else ""
    reason = (
        f"COMPREHENSION GATE: you changed about {s['lines']} lines ({files}). Before handing back, end your "
        "turn with a short comprehension check for the user. Ask 1-2 questions, each answerable in a sentence "
        "or two, that test whether they understand WHY this change works: why this approach over an obvious "
        "alternative, what happens on an edge case, what would break if some piece were removed, or where "
        "they'd look if it misbehaved. No trivia ('what does line 14 do'), and don't hint at the answers. "
        "Keep it friendly and brief, and mention they can reply `!skip` to bypass this once. "
        "Editing tools stay locked until they pass." + known_txt
    )
    emit({"decision": "block", "reason": reason})


def cmd_prompt():
    data = read_input()
    sid = data.get("session_id", "default")
    prompt = (data.get("prompt") or "").strip()
    s = load_session(sid)
    low = prompt.lower()

    if low.startswith("!gate off"):
        s.update(enabled=False, state="idle", lines=0, files=[])
        save_session(sid, s)
        log("disabled", session=sid)
        emit({"decision": "block", "reason": "Comprehension gate is OFF for this session. Type `!gate on` to re-enable."})
    if low.startswith("!gate on"):
        s["enabled"] = True
        save_session(sid, s)
        emit({"decision": "block", "reason": "Comprehension gate is ON for this session."})

    if s["state"] != "awaiting":
        sys.exit(0)

    if low.startswith("!skip"):
        s.update(state="idle", lines=0, files=[], attempts=0)
        save_session(sid, s)
        p = load_profile()
        p["skips"] += 1
        _save(PROFILE, p)
        log("skip", session=sid)
        rest = prompt[5:].strip()
        ctx = ("COMPREHENSION GATE: the user skipped this check (it's logged in their stats). "
               "Don't lecture; just continue with their request" + (": " + rest if rest else "."))
        emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}})

    known = mastered(load_profile())
    ctx = (
        "COMPREHENSION GATE (locked): treat this message as the user's answer to your check questions. "
        "Read the relevant code if you need to. Grade for understanding, not wording: be generous with "
        "phrasing and strict on substance. Grade honestly; passing a wrong answer defeats the user's own goal.\n"
        f"- If they understand: say so in a line, run `python3 \"{SCRIPT}\" verdict pass --session {sid} "
        "--concepts \"<2-4 short concept names the questions covered>\"`, then continue with anything else "
        "they asked.\n"
        f"- If not: run `python3 \"{SCRIPT}\" verdict fail --session {sid} --concepts \"<same>\"`, explain the "
        "missing idea in 2-4 sentences, and ask ONE different question on the same idea. Do no other work.\n"
        "- If the message isn't an answer at all (a new request), remind them briefly that there's a pending "
        "check (or `!skip`) and restate the question. Don't do the new request yet.\n"
        f"Attempts so far: {s['attempts']}. After 3 failed attempts, walk through the answer fully, then pass "
        "them with the verdict command (they'll have learned it by then)."
        + (f"\nAlready mastered (don't re-ask): {', '.join(known)}" if known else "")
    )
    emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}})


def cmd_pre_tool():
    data = read_input()
    sid = data.get("session_id", "default")
    s = load_session(sid)
    if s["state"] != "awaiting" or not s["enabled"]:
        sys.exit(0)
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}
    if tool == "Bash":
        cmd = (ti.get("command") or "").strip()
        if str(SCRIPT) in cmd or "gate.py" in cmd or cmd.startswith(READONLY_BASH):
            sys.exit(0)
    elif tool not in WRITE_TOOLS and tool not in ("Task", "Agent"):
        sys.exit(0)  # reading, searching etc. stay allowed
    emit({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": ("Comprehension gate is locked: the user hasn't passed the check on the last "
                                     "change yet. Wait for their answer and grade it; don't write code."),
    }})


# ---------- commands ----------

def cmd_verdict(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="gate.py verdict")
    ap.add_argument("result", choices=["pass", "fail"])
    ap.add_argument("--session", default="default")
    ap.add_argument("--concepts", default="")
    a = ap.parse_args(argv)
    concepts = [c.strip().lower() for c in a.concepts.split(",") if c.strip()]
    s = load_session(a.session)
    p = load_profile()
    if a.result == "pass":
        for c in concepts:
            p["concepts"][c] = p["concepts"].get(c, 0) + 1
        p["passes"] += 1
        s.update(state="idle", lines=0, files=[], attempts=0)
        msg = "Gate unlocked. Nice."
    else:
        p["fails"] += 1
        s["attempts"] += 1
        msg = f"Recorded a miss (attempt {s['attempts']}). Gate stays locked."
    _save(PROFILE, p)
    save_session(a.session, s)
    log(a.result, session=a.session, concepts=concepts)
    print(msg)


def cmd_status():
    p = load_profile()
    checks = max(p["checks"], 1)
    print(f"Checks: {p['checks']}  passes: {p['passes']}  misses: {p['fails']}  skips: {p['skips']}")
    print(f"Skip rate: {100 * p['skips'] / checks:.0f}%")
    if p["concepts"]:
        print("Concepts you've shown you understand:")
        for c, n in sorted(p["concepts"].items(), key=lambda kv: -kv[1]):
            print(f"  {'*' if n >= MASTERY else ' '} {c} ({n})")
        print(f"(* = mastered, no longer asked about; threshold {MASTERY})")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd, rest = sys.argv[1], sys.argv[2:]
    try:
        {
            "post-tool": cmd_post_tool,
            "stop": cmd_stop,
            "prompt": cmd_prompt,
            "pre-tool": cmd_pre_tool,
            "status": cmd_status,
        }.get(cmd, lambda: cmd_verdict(rest) if cmd == "verdict" else print(__doc__))()
    except SystemExit:
        raise
    except Exception as e:  # never break the user's session because of the gate
        print(f"comprehension-gate error: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
