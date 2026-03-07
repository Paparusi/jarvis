---
name: mcp-github
description: >
  Quản lý GitHub repositories, issues, pull requests, branches, commits.
  Kích hoạt khi user yêu cầu thao tác với GitHub: tạo repo, tìm code,
  quản lý issues/PRs, review code, hoặc tìm kiếm trên GitHub.
version: 1.0.0
metadata:
  jarvis:
    auto_generated: true
    created_by: mcp_bridge
    category: mcp
    emoji: ""
    priority: 0.85
    requires:
      env: [GITHUB_PERSONAL_ACCESS_TOKEN]
    mcp_tools:
      - get_me
      - search_repositories
      - search_code
      - search_issues
      - search_pull_requests
      - search_users
      - get_file_contents
      - create_repository
      - create_branch
      - create_or_update_file
      - create_pull_request
      - merge_pull_request
      - issue_read
      - issue_write
      - list_issues
      - list_pull_requests
      - list_branches
      - list_commits
      - fork_repository
      - push_files
---

# GitHub Integration

## Available Tools (41)

### Repository Management
- `get_file_contents`: Xem nội dung file/directory trong repo
- `create_repository`: Tạo repo mới
- `create_branch`: Tạo branch mới
- `create_or_update_file`: Tạo hoặc cập nhật file
- `push_files`: Push nhiều files cùng lúc (1 commit)
- `delete_file`: Xóa file trong repo
- `fork_repository`: Fork repo
- `list_branches`: Liệt kê branches
- `list_commits`: Xem commit history
- `list_tags`: Liệt kê tags
- `list_releases`: Liệt kê releases
- `get_commit`: Chi tiết 1 commit
- `get_latest_release`: Release mới nhất

### Issues & Pull Requests
- `issue_read`: Đọc chi tiết issue
- `issue_write`: Tạo/cập nhật issue
- `list_issues`: Liệt kê issues
- `add_issue_comment`: Comment vào issue
- `pull_request_read`: Đọc chi tiết PR
- `create_pull_request`: Tạo PR mới
- `update_pull_request`: Cập nhật PR
- `merge_pull_request`: Merge PR
- `list_pull_requests`: Liệt kê PRs
- `update_pull_request_branch`: Cập nhật branch của PR

### Search
- `search_repositories`: Tìm repos trên GitHub
- `search_code`: Tìm code across all repos
- `search_issues`: Tìm issues
- `search_pull_requests`: Tìm PRs
- `search_users`: Tìm users

### Account
- `get_me`: Thông tin user hiện tại

## Workflow
1. Xác định action cần thực hiện (search, create, read, update)
2. Gọi MCP tool tương ứng với prefix `mcp_github_`
3. Format kết quả cho user (JSON → readable)

## Rules
- Tool names có prefix `mcp_github_` (VD: `mcp_github_search_repositories`)
- Luôn xác nhận với user trước khi thực hiện write operations (create, update, delete, merge)
- Khi search, giới hạn `perPage` hợp lý (3-5 cho overview, 10 cho detailed)
- Khi tạo PR/issue, yêu cầu user cung cấp đủ thông tin (title, body)
