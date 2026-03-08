# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A minimal Python 3.12 project managed with `pyproject.toml`. Entry point is `main.py`.

## Commands

Run the project:
```bash
python main.py
```

Install dependencies (when added):
```bash
pip install -e .
```

## Git Workflow

After completing any meaningful unit of work, commit and push to GitHub regularly so no progress is lost:

```bash
git add -A
git commit -m "relevant description of what was done"
git push origin master
```

- Commit after every significant change — don't batch unrelated changes into one commit.
- Write clear, specific commit messages describing what changed and why.
- Push after every commit (or group of related commits) to keep the remote up to date.
