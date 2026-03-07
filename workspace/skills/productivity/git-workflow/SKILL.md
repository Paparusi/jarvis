---
name: git-workflow
description: >
  Git workflow management — check status, view changes, commit, branch,
  and review history. Streamlined git operations with best practices.
version: 1.0.0
metadata:
  jarvis:
    category: productivity
    emoji: "🌿"
    priority: 0.85
    requires:
      bins: [git]
---

# Git Workflow

## Khi nao kich hoat
Khi user yeu cau:
- Git operations: status, diff, commit, branch
- "co thay doi gi chua?", "commit giup tao"
- Code review, xem history
- Branch management

## Workflow
1. Check git status (dung `git_status`)
2. Tuy theo yeu cau:
   - Review changes: `git_diff` (staged va unstaged)
   - History: `git_log` (oneline cho overview)
   - Commit: `git_commit` voi message mo ta ro
   - Branch: `git_branch` list/create/switch
3. Bao cao ket qua va goi y buoc tiep theo

## Rules
- Luon check status truoc khi commit
- Commit message phai mo ta ro thay doi (tieng Viet hoac tieng Anh)
- Canh bao neu commit len main/master truc tiep
- Goi y tao branch moi cho features lon
- Khong force push tru khi user yeu cau ro rang
