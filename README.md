# CloudTask CLI

A lightweight command-line task manager with local SQLite storage and powerful query capabilities.

## Features

- **Local Storage**: All tasks stored in SQLite database (no cloud required)
- **Query Language**: Complex filtering with operators like `>=`, `==`, `in`, etc.
- **Batch Operations**: Update multiple tasks concurrently
- **Tag Organization**: Organize tasks with flexible tagging
- **Priority Management**: Set and filter by task priority (1-10)
- **Logging**: Built-in logging for debugging and monitoring

## Installation

```bash
# Clone the repository
git clone https://github.com/fifi-rgb/cloudtask-cli.git
cd cloudtask-cli

# No external dependencies required (uses Python standard library)
pip install -r requirements.txt  # Currently empty - no dependencies!
```

## Quick Start

```bash
# Create a task
python cloudtask.py create task --title "Fix bug in parser" --priority 8 --tags "bug,urgent"

# Search tasks
python cloudtask.py search tasks "priority >= 7"

# Search with multiple conditions
python cloudtask.py search tasks "status == pending priority >= 5"

# Search by tags
python cloudtask.py search tasks "tags in [bug,urgent]"

# Update multiple tasks
python cloudtask.py update tasks 1 2 3 --status "completed"

# Delete a task
python cloudtask.py delete task 1

# Show configuration and stats
python cloudtask.py show config
```

## Query Syntax

The search command supports a powerful query language:

### Operators
- `==`, `!=` - Equality/inequality
- `<`, `<=`, `>`, `>=` - Comparisons
- `in`, `notin` - List membership

### Fields
- `id`, `title`, `description`, `status`, `priority`
- `tags`, `created`, `updated`, `due_date`
- `assigned_to`, `project`

### Examples
```bash
# High priority tasks
search tasks "priority >= 8"

# Multiple conditions
search tasks "status == pending priority >= 5"

# Tag filtering
search tasks "tags in [urgent,bug]"

# Combined queries
search tasks "priority >= 7 status == pending assigned_to != none"
```

## Development

### Running Tests
```bash
python -m unittest test_cloudtask.py
```

### Debugging
```bash
# Enable verbose logging
python cloudtask.py --verbose search tasks

# View logs
tail -f ~/.cache/cloudtask/cloudtask.log
```

## Data Storage

- Database: `~/.local/share/cloudtask/tasks.db` (SQLite)
- Config: `~/.config/cloudtask/config.json`
- Logs: `~/.cache/cloudtask/cloudtask.log`

## License

MIT
