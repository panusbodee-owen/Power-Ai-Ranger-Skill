# AGENTS.md — leading the AI team from any agent CLI

For Antigravity, Gemini CLI, Codex CLI, Cursor, or any agent that can run shell commands. You are the **Head**: you brief, the team drafts, you verify.

## Setup (the human does this once; never ask for or print keys)
```bash
python3 scripts/team.py presets
python3 scripts/team.py connect gemini     # or openai / xai / groq / openrouter / nvidia
python3 scripts/team.py connect codex-cli  # or agy-cli / claude-cli: use a logged-in subscription, no key
python3 scripts/team.py list               # seats and their models
```

## Daily commands
| Goal | Command |
|---|---|
| Ask one seat | `python3 scripts/team.py ask SEAT "brief" --job NAME` |
| Maker then reviewer, async | `python3 scripts/team.py submit --job NAME --flow "maker@@auto>reviewer@@auto" --rounds 2 "brief"` |
| Run the queue | `python3 scripts/team.py worker --concurrency 3` |
| Wait for results | `python3 scripts/team.py wait --timeout 900` |
| Read results | `python3 scripts/team.py results --job NAME` |
| Several seats, compare | `python3 scripts/team.py run --seats a,b --mode best-of-n "brief"` |
| Recent calls | `python3 scripts/team.py log 20` |
| Dashboard | `python3 scripts/dash.py` → http://localhost:8790 |

## Rules for the Head
1. Write briefs with the full context, exact output format, and a size limit (models truncate long files; split big work into files under ~180 lines with fixed interfaces).
2. Treat team output as a draft. Run it, test it, and fix integration seams yourself.
3. Never send secrets or personal data in a brief; the boundary blocks keys and 13-digit IDs, but don't rely on it.
4. Per-seat strengths and instructions: `docs/TEAM-GUIDE.md`, `docs/team/<seat>.md`.
