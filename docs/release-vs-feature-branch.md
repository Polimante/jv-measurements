# Branches, Tags, and Releases — Quick Reference
**Repository:** https://github.com/Polimante/jv-measurements

---

## The Three Concepts

| Concept | What it is | Who touches it |
|---|---|---|
| `main` branch | Stable, tested code only. What users run. | Only via merges from feature branches |
| Feature branch | Where active development happens | You and Claude Code, every session |
| Tag / Release | A permanent snapshot of `main` at a known-good moment | Created before and after major changes |

---

## ASCII Diagram

```
GitHub: origin/main
─────────────────────────────────────────────────────────────────► time
  │                    │                         │
  │                    │                         │
  tag: pre-features-v2.6.1                    (future tag: v2.7.0)
  │                    │
  │    dev-features-2026Q2  (your working branch)
  │    ┌───────────────────────────────────────┐
  │    │  commit A  →  commit B  →  commit C   │
  │    └───────────────────────────────────────┘
  │                                            │
  │                                   merge into main
  │                                            │
  ●────────────────────────────────────────────●
 v2.6.1 (current)                           v2.7.0
```

---

## Rules

1. **Never commit directly to `main`.**  
   All changes go to `dev-features-2026Q2` first.

2. **`main` only moves forward via a merge.**  
   When a feature is complete and tested, it is merged in one clean step.

3. **Tags are read-only anchors.**  
   `pre-features-v2.6.1` permanently marks the state of `main` before
   this quarter's work begins. If anything goes badly wrong, that tag
   lets us return to a known-good state.

4. **Feature branches are disposable.**  
   They can be deleted after merging. The history lives on in `main`.

---

## Merging — When You Are Ready

When all features are tested and stable, the merge into `main` looks like:

```bash
git checkout main
git pull
git merge dev-features-2026Q2
git push origin main
git tag v2.7.0
git push origin v2.7.0
```

**Do not run this until explicitly agreed with your collaborators.**
