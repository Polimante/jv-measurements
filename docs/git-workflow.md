# Git Workflow — JV Measurements Project
**Repository:** https://github.com/Polimante/jv-measurements  
**Feature branch:** `dev-features-2026Q2`  
**Production branch:** `main`

---

## Daily Routine

Run these commands every time you sit down to work.

### 1. Before you start coding

```bash
git status            # Must show "nothing to commit, working tree clean"
git checkout dev-features-2026Q2   # Make sure you are on the right branch
git pull              # Download any changes from GitHub
```

### 2. While working — check what you have changed

```bash
git status            # Lists which files are modified or new
git diff              # Shows the exact lines you changed (press q to exit)
git diff main         # Shows everything that differs from the main branch
```

### 3. Save your work (add → commit → push)

```bash
git add app/gui/main_window.py          # Stage one specific file
git add app/io/exporter.py              # Stage another specific file
# -- OR stage everything in a folder --
git add app/                            # Stage all changes inside app/

git status                              # Double-check what is staged

git commit -m "feat: describe what you added"   # Save a snapshot

git push origin dev-features-2026Q2    # Upload to GitHub
```

#### Conventional commit prefixes

| Prefix | Use for |
|---|---|
| `feat:` | New feature or behaviour |
| `fix:` | Bug fix |
| `docs:` | Documentation only |
| `test:` | Adding or fixing tests |
| `refactor:` | Code restructuring, no behaviour change |

---

## Reading What Changed

```bash
git status                    # Overview: modified, staged, untracked files
git diff                      # Line-by-line diff of unstaged changes
git diff --staged             # Line-by-line diff of staged (added) changes
git log --oneline -10         # Last 10 commits, one line each
git log --oneline main..HEAD  # Commits on your branch not yet in main
git show <commit-hash>        # Full details of one commit
```

---

## Switching Branches Safely

**Always commit or stash your work before switching branches.**  
If you switch with unsaved changes, Git may refuse or mix up your files.

```bash
git status                         # Check — must be clean before switching
git checkout main                  # Switch to main (read-only reference)
git checkout dev-features-2026Q2   # Switch back to your working branch
```

---

## What To Do If Something Goes Wrong

### Scenario A — "I changed files but want to throw away those changes"
*(Only affects files you have NOT yet staged or committed)*

```bash
git status                        # See which files are modified
git restore app/gui/main_window.py   # Discard changes in ONE file
```

> **Warning:** `git restore` on a file permanently discards your edits to that
> file. Only use it if you are certain you do not need those changes.

---

### Scenario B — "I committed something by mistake — but did NOT push yet"

```bash
git log --oneline -5      # Find the bad commit; note the hash above it
git revert HEAD           # Creates a NEW commit that undoes the last one
```

`git revert` is safe: it does not erase history, it adds an "undo" commit.

---

### Scenario C — "I committed AND pushed, and need to undo without rewriting history"

```bash
git revert HEAD           # Creates a new "undo" commit locally
git push origin dev-features-2026Q2   # Push the undo commit to GitHub
```

This is the correct approach for pushed commits. It keeps the full history
visible on GitHub and does not disturb any collaborators.

---

### Scenario D — "I have changes I want to save, but need to switch branches first"

```bash
git stash                          # Temporarily shelve all uncommitted changes
git checkout main                  # Switch to another branch safely
# ... do what you need on main ...
git checkout dev-features-2026Q2   # Come back to your branch
git stash pop                      # Restore your shelved changes
```

`git stash` is like a clipboard for uncommitted work. `git stash pop` pastes
it back. Run `git stash list` to see everything on the clipboard.

---

## Per-Session Checklist

### Before starting
```bash
git status                              # Must show "clean"
git pull                                # Get latest from GitHub
git checkout dev-features-2026Q2        # Confirm you are on the right branch
```

### During the session
1. Claude Code presents its plan — **you approve before execution**
2. Claude Code implements the changes
3. Run tests: `python -m pytest tests/ -v`
4. Review what changed: `git diff main`
5. Stage and commit:
   ```bash
   git add <files>
   git commit -m "feat: short description of what was done"
   ```

### Before ending the session
```bash
git push origin dev-features-2026Q2    # Upload your work to GitHub
# Then: /clear in Claude Code, close VS Code, rest
```

---

## Glossary

| Term | Plain-English meaning |
|---|---|
| **Working tree** | The actual files on your hard drive as you edit them |
| **Staged** | Files you have `git add`-ed and are ready to be committed |
| **Commit** | A saved snapshot of your staged changes, with a message |
| **Branch** | An independent line of development; like a parallel copy |
| **Remote** | The copy of the repo on GitHub (`origin`) |
| **Push** | Upload your local commits to GitHub |
| **Pull** | Download changes from GitHub to your local machine |
| **Merge** | Combine changes from one branch into another |
| **Conflict** | When two branches changed the same line differently; Git asks you to decide |
| **Tag** | A permanent label pointing to one specific commit (like a bookmark) |
| **Stash** | A temporary clipboard for uncommitted work |

---

## FORBIDDEN Commands

Never run these as a beginner. They can permanently destroy work or rewrite
shared history in ways that are very difficult to recover from.

| Command | Why it is dangerous |
|---|---|
| `git push --force` | Overwrites GitHub history; collaborators lose commits |
| `git reset --hard` | Permanently discards all uncommitted changes with no undo |
| `git rebase` on a pushed branch | Rewrites history others may depend on |
| `git clean -fd` | Permanently deletes untracked files and folders |
| `git checkout -- .` | Discards ALL uncommitted changes in the entire repo |
| `git commit --amend` (after push) | Rewrites a commit others may have already downloaded |

If you feel you need one of these, **stop and ask Claude Code first.**
