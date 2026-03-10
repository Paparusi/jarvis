---
name: mcp-github
description: >
  Tools from MCP server 'github'.
  Available tools: add_comment_to_pending_review, add_issue_comment, add_reply_to_pull_request_comment, assign_copilot_to_issue, create_branch, create_or_update_file, create_pull_request, create_repository, delete_file, fork_repository, get_commit, get_file_contents, get_label, get_latest_release, get_me, get_release_by_tag, get_tag, get_team_members, get_teams, issue_read, issue_write, list_branches, list_commits, list_issue_types, list_issues, list_pull_requests, list_releases, list_tags, merge_pull_request, pull_request_read, pull_request_review_write, push_files, request_copilot_review, search_code, search_issues, search_pull_requests, search_repositories, search_users, sub_issue_write, update_pull_request, update_pull_request_branch
version: 1.0.0
metadata:
  jarvis:
    auto_generated: true
    created_by: mcp_bridge
    category: mcp
    priority: 0.7
---

# MCP: github

## Available Tools
- `add_comment_to_pending_review`: Add review comment to the requester's latest pending pull request review. A pending review needs to already exist to call this (check with the user if not sure).
- `add_issue_comment`: Add a comment to a specific issue in a GitHub repository. Use this tool to add comments to pull requests as well (in this case pass pull request number as issue_number), but only if user is not asking specifically to add review comments.
- `add_reply_to_pull_request_comment`: Add a reply to an existing pull request comment. This creates a new comment that is linked as a reply to the specified comment.
- `assign_copilot_to_issue`: Assign Copilot to a specific issue in a GitHub repository.

This tool can help with the following outcomes:
- a Pull Request created with source code changes to resolve the issue


More information can be found at:
- https://docs.github.com/en/copilot/using-github-copilot/using-copilot-coding-agent-to-work-on-tasks/about-assigning-tasks-to-copilot

- `create_branch`: Create a new branch in a GitHub repository
- `create_or_update_file`: Create or update a single file in a GitHub repository. 
If updating, you should provide the SHA of the file you want to update. Use this tool to create or update a file in a GitHub repository remotely; do not use it for local file operations.

In order to obtain the SHA of original file version before updating, use the following git command:
git ls-tree HEAD <path to file>

If the SHA is not provided, the tool will attempt to acquire it by fetching the current file contents from the repository, which may lead to rewriting latest committed changes if the file has changed since last retrieval.

- `create_pull_request`: Create a new pull request in a GitHub repository.
- `create_repository`: Create a new GitHub repository in your account or specified organization
- `delete_file`: Delete a file from a GitHub repository
- `fork_repository`: Fork a GitHub repository to your account or specified organization
- `get_commit`: Get details for a commit from a GitHub repository
- `get_file_contents`: Get the contents of a file or directory from a GitHub repository
- `get_label`: Get a specific label from a repository.
- `get_latest_release`: Get the latest release in a GitHub repository
- `get_me`: Get details of the authenticated GitHub user. Use this when a request is about the user's own profile for GitHub. Or when information is missing to build other tool calls.
- `get_release_by_tag`: Get a specific release by its tag name in a GitHub repository
- `get_tag`: Get details about a specific git tag in a GitHub repository
- `get_team_members`: Get member usernames of a specific team in an organization. Limited to organizations accessible with current credentials
- `get_teams`: Get details of the teams the user is a member of. Limited to organizations accessible with current credentials
- `issue_read`: Get information about a specific issue in a GitHub repository.
- `issue_write`: Create a new or update an existing issue in a GitHub repository.
- `list_branches`: List branches in a GitHub repository
- `list_commits`: Get list of commits of a branch in a GitHub repository. Returns at least 30 results per page by default, but can return more if specified using the perPage parameter (up to 100).
- `list_issue_types`: List supported issue types for repository owner (organization).
- `list_issues`: List issues in a GitHub repository. For pagination, use the 'endCursor' from the previous response's 'pageInfo' in the 'after' parameter.
- `list_pull_requests`: List pull requests in a GitHub repository. If the user specifies an author, then DO NOT use this tool and use the search_pull_requests tool instead.
- `list_releases`: List releases in a GitHub repository
- `list_tags`: List git tags in a GitHub repository
- `merge_pull_request`: Merge a pull request in a GitHub repository.
- `pull_request_read`: Get information on a specific pull request in GitHub repository.
- `pull_request_review_write`: Create and/or submit, delete review of a pull request.

Available methods:
- create: Create a new review of a pull request. If "event" parameter is provided, the review is submitted. If "event" is omitted, a pending review is created.
- submit_pending: Submit an existing pending review of a pull request. This requires that a pending review exists for the current user on the specified pull request. The "body" and "event" parameters are used when submitting the review.
- delete_pending: Delete an existing pending review of a pull request. This requires that a pending review exists for the current user on the specified pull request.

- `push_files`: Push multiple files to a GitHub repository in a single commit
- `request_copilot_review`: Request a GitHub Copilot code review for a pull request. Use this for automated feedback on pull requests, usually before requesting a human reviewer.
- `search_code`: Fast and precise code search across ALL GitHub repositories using GitHub's native search engine. Best for finding exact symbols, functions, classes, or specific code patterns.
- `search_issues`: Search for issues in GitHub repositories using issues search syntax already scoped to is:issue
- `search_pull_requests`: Search for pull requests in GitHub repositories using issues search syntax already scoped to is:pr
- `search_repositories`: Find GitHub repositories by name, description, readme, topics, or other metadata. Perfect for discovering projects, finding examples, or locating specific repositories across GitHub.
- `search_users`: Find GitHub users by username, real name, or other profile information. Useful for locating developers, contributors, or team members.
- `sub_issue_write`: Add a sub-issue to a parent issue in a GitHub repository.
- `update_pull_request`: Update an existing pull request in a GitHub repository.
- `update_pull_request_branch`: Update the branch of a pull request with the latest changes from the base branch.

## Workflow
1. Identify which MCP tool is needed for the user's request
2. Call the appropriate tool with correct parameters
3. Format the result for the user

## Rules
- Always use the MCP-prefixed tool name (e.g., `mcp_github_toolname`)
- Handle errors gracefully — MCP servers may be unavailable
