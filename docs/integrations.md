# Integrations

Optional integrations that give AI entities access to external systems during conversations. All are disabled by default.

## GitHub Repository Integration

GitHub integration allows AI entities to interact with repositories during conversations — reading files, creating branches, making commits, managing pull requests, and more.

**Configuration:**
```bash
GITHUB_TOOLS_ENABLED=true
GITHUB_REPOS='[
  {
    "owner": "your-username",
    "repo": "your-repo",
    "label": "My Project",
    "token": "ghp_xxxxxxxxxxxx",
    "protected_branches": ["main", "master"],
    "capabilities": ["read", "branch", "commit", "pr", "issue"],
    "commit_author_name": "Your Name",
    "commit_author_email": "your.email@example.com"
  }
]'
```

**Repository fields:**
- `owner`, `repo`, `label`, `token` — required identification and access
- `protected_branches` — branches that cannot be committed to directly (default: main, master)
- `capabilities` — allowed operations: `read`, `branch`, `commit`, `pr`, `issue` (default: all)
- `local_clone_path` — path to local clone for faster operations and codebase navigator (optional)
- `commit_author_name`, `commit_author_email` — commit attribution (optional)

See [GitHub Tools](tools.md#github-tools) for the full list of available tools.

## Codebase Navigator Setup

The codebase navigator uses Mistral's Devstral model to efficiently explore codebases before implementing changes.

**Configuration:**
```bash
CODEBASE_NAVIGATOR_ENABLED=true
MISTRAL_API_KEY=your_mistral_api_key
```

Requires `local_clone_path` in at least one GitHub repository configuration. See [Codebase Navigator Tools](tools.md#codebase-navigator-tools) for the full list of available tools.

## Moltbook Integration

Moltbook is a social network for AI agents. The integration allows AI entities to browse feeds, create posts, comment, vote, search content, and follow other agents.

**Configuration:**
```bash
MOLTBOOK_ENABLED=true
MOLTBOOK_API_KEY=your_moltbook_api_key
MOLTBOOK_API_URL=https://www.moltbook.com/api/v1  # Must use www subdomain
```

All Moltbook responses are wrapped with a security banner to prevent prompt injection from external content.
