# CloudTask CLI

Command-line task manager with a query DSL and concurrent batch operations.

## Features

- **Query DSL** - Search tasks with expressions like `priority >= 7 status == active tags in [work,urgent]`
- **Batch updates** - Update multiple tasks concurrently with automatic retries
- **Decorator-based commands** - Clean command registration with `@parser.command`
- **Resilient API client** - Exponential backoff, rate limit handling, automatic retries
- **XDG directory support** - Follows platform conventions for config and cache
- **File-based caching** - Configurable TTL to reduce API calls

## Architecture

```
┌─────────────────────────────────────────┐
│         Command Parser Layer            │
│    (decorator-based registration)       │
└─────────────┬───────────────────────────┘
              │
┌─────────────▼───────────────────────────┐
│          API Client Layer               │
│  (retry logic, auth, rate limiting)     │
└─────────────┬───────────────────────────┘
              │
┌─────────────▼───────────────────────────┐
│      Query Engine & Utilities           │
│  (DSL parser, cache, config, display)   │
└─────────────────────────────────────────┘
```

The command parser uses decorators to register multi-word commands (e.g., `create task`). The API client wraps `requests.Session` with retry logic and handles rate limiting. The query engine parses filter expressions and converts them to API parameters.

## Installation

```bash
pip install requests

# Optional: for XDG directory support
pip install xdg
```

## Usage

### Create tasks
```bash
python cloudtask.py create task --title "Complete project documentation" --priority 8

python cloudtask.py create task \
  --title "Review pull requests" \
  --description "Review and merge pending PRs" \
  --priority 9 \
  --tags "development,urgent" \
  --due-date "2026-02-15" \
  --assigned-to "john@example.com"
```

### Search with query DSL
```bash
# High-priority active tasks
python cloudtask.py search tasks "priority >= 7 status == active"

# Multiple conditions
python cloudtask.py search tasks "tags in [work,urgent] assigned_to != none created > 2024-01-01"

# Sort and limit
python cloudtask.py search tasks "status == pending" --order "priority-" --limit 20
```

### Batch operations
```bash
# Update multiple tasks concurrently
python cloudtask.py update tasks 101 102 103 104 105 --status completed

python cloudtask.py update tasks 201 202 203 --priority 10
```

### Config
```bash
# Set API key (stored with 0o600 permissions)
python cloudtask.py set api-key --key "your-api-key-here"

# Show config
python cloudtask.py show config

# Clear cache
python cloudtask.py clear cache
```

## Implementation Details

### Query DSL

Supports operators: `<`, `<=`, `==`, `!=`, `>=`, `>`, `in`, `notin`

```python
# Input
"priority >= 7 status == active tags in [work,urgent]"

# Parsed output
{
  "priority": {"gte": 7}, 
  "status": {"eq": "active"}, 
  "tags": {"in": ["work", "urgent"]}
}
```

### Command Registration

Commands are registered using decorators:

```python
@parser.command(
    argument("--title", required=True),
    argument("--priority", type=int, default=5),
    help="Create a new task"
)
def create__task(args: argparse.Namespace):
    # Double underscore maps to "create task"
    client = get_api_client(args)
    result = client.post("/tasks", json={...})
```

### Retry Logic

The API client retries failed requests with exponential backoff:

```python
for attempt in range(max_retries):
    try:
        response = session.request(method, url, timeout=30)
        if response.status_code == 429:  # Rate limited
            time.sleep(backoff_time)
            backoff_time *= 1.5
            continue
        return response
    except RequestException:
        if attempt < max_retries - 1:
            time.sleep(backoff_time)
            backoff_time *= 2
```

### Concurrent Execution

Batch operations use `ThreadPoolExecutor`:

```python
def execute_concurrent(func, items, max_workers=8):
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(func, items))
    return results
```

## Extending

Add new commands by defining functions with the decorator:

```python
@parser.command(
    argument("--filter", help="Filter expression"),
    help="List all projects"
)
def list__projects(args: argparse.Namespace):
    client = get_api_client(args)
    result = client.get("/projects", params={"filter": args.filter})
    display_table(result)
```

## Notes

- API endpoints in the code are examples - point to your actual API with `--url` or config
- API key is stored in `~/.config/cloudtask/config.json` (or XDG equivalent) with 0o600 permissions
- Cache is stored in `~/.cache/cloudtask/` with configurable TTL
- Use `--explain` flag for dry-run mode
- Use `--raw` flag for JSON output instead of formatted tables

---

Built with Python 3.8+. Uses `argparse`, `requests`, `concurrent.futures`, and optional `xdg`.
