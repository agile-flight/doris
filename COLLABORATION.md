# Share Doris and keep both laptops in sync

Canonical repo: https://github.com/agile-flight/doris (private).
Start with [HANDOFF.md](HANDOFF.md), [BUILD_PLAN.md](BUILD_PLAN.md), and
[contract/README.md](contract/README.md). All code, fixtures, tests, sample decisions,
and agent instructions travel together in this repository.

## Give your friend access

The owner must invite their friend's GitHub username with **Write** access at:
https://github.com/agile-flight/doris/settings/access
The friend must accept the invitation before cloning. A private repo link alone
is not access. Never share your GitHub token or API key.

## Friend: first setup

Install Git, Python 3.13, and uv if needed. Then:

```bash
git clone https://github.com/agile-flight/doris.git
cd doris
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/pytest -q
./contract/check.sh
.venv/bin/python -m detector --offline evaluate
.venv/bin/python -m detector --offline serve
```

The last command stays running at http://127.0.0.1:8787. These commands use no
Claude API credit. Each laptop runs its own backend and seeded fixture database.
For a live model run, supply your own ANTHROPIC_API_KEY; the default model (claude-opus-5-5)
was verified live on 2026-10-25 (5/5 fixtures, about 8 seconds a check). Do not commit keys.

## Both people: sync each finished slice

Git syncs **committed, pushed files**, not unsaved edits. Before starting, with a
clean working tree:

```bash
git switch main
git pull --ff-only origin main
```

After editing your assigned files:

```bash
.venv/bin/pytest -q
./contract/check.sh
git status --short
# Stage only the files you intend to share, for example:
# Agent A: git add detector coord/from_A.md
# Agent B: git add surfaces coord/from_B.md
git commit -m "Describe the completed change"
git pull --rebase origin main
git push origin main
```

Your teammate runs `git pull --ff-only origin main` to receive that change.
If a pull refuses because you have local work, commit it first, then use
`git pull --rebase origin main`. If rebase reports conflicts, resolve them and
run `git add <resolved-files>` followed by `git rebase --continue`; rerun tests
before pushing. `git rebase --abort` returns to the pre-rebase state. Never force
push shared main. For substantial shared changes, use a branch and pull request.

Agent A owns detector/; Agent B owns surfaces/. Contract edits need agreement
from both. Record interface changes in coord/from_A.md or coord/from_B.md.
The database, outbox, credentials, and virtual environment do not sync: data/
and .venv/ are deliberately ignored. Share synthetic fixtures through the repo.

## Message to send your friend

> Doris repo: https://github.com/agile-flight/doris
> Accept the GitHub invitation, then open HANDOFF.md and COLLABORATION.md.
> BUILD_PLAN.md summarizes the current plan and links the original Claude doc.
> You're Agent B: build surfaces/ against contract/samples/ first. The detector
> runs offline with no API spending. Push your commits; pull main to get mine.

The original Claude doc requires its own sign-in/sharing access. Its Handoff tab
should point to https://github.com/agile-flight/doris/blob/main/HANDOFF.md.
The GitHub handoff is the maintained handoff; the original doc was not edited
from this session.
