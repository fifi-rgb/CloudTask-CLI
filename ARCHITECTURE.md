# CloudTask CLI - Architecture

## Overview

CloudTask CLI is a command-line task manager using SQLite for local storage. This document explains key architectural decisions and implementation details.

## Architecture Layers

```
┌─────────────────────────────────────────┐
│         CLI Interface Layer             │
│  - Argument parsing                     │
│  - Command routing                      │
│  - Output formatting                    │
└─────────────────────────────────────────┘
                 ↓
┌─────────────────────────────────────────┐
│       Business Logic Layer              │
│  - Query DSL parser                     │
│  - Validation                           │
│  - Concurrent operations                │
└─────────────────────────────────────────┘
                 ↓
┌─────────────────────────────────────────┐
│        Data Access Layer                │
│  - SQLite backend                       │
│  - CRUD operations                      │
│  - Transaction management               │
└─────────────────────────────────────────┘
```

## Key Components

### 1. CommandParserWrapper

A wrapper around argparse that enables decorator-based command registration. This simplifies adding new commands by using Python decorators instead of imperative parser configuration.

**Why this approach:**
- Reduces boilerplate code
- Groups command logic with its argument definitions
- Makes command functions self-contained

### 2. SQLite Backend

All tasks are stored locally in a SQLite database. This provides:
- No network dependencies
- Fast queries with indexing
- ACID transactions
- Simple backup (just copy the .db file)

**Schema:**
```sql
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'pending',
    priority INTEGER DEFAULT 5,
    tags TEXT,  -- Stored as JSON array
    created REAL,
    updated REAL,
    due_date REAL,
    assigned_to TEXT,
    project TEXT
)
```

### 3. Query DSL

A simple domain-specific language for filtering tasks:

**Grammar:**
```
condition  := field operator value
operator   := '<' | '<=' | '==' | '!=' | '>=' | '>' | 'in' | 'notin'
value      := scalar | list
```

**Examples:**
```python
"priority >= 7"                          # Comparison
"status == pending"                      # Equality
"tags in [work,urgent]"                 # List membership
"priority >= 7 status == pending"       # Multiple conditions
```

**Implementation:** Uses regex to tokenize the query string and convert it to a structured dictionary that the SQLite backend can process.

### 4. Concurrent Operations

Batch updates use ThreadPoolExecutor to update multiple tasks in parallel:

```python
def execute_concurrent(func, items, max_workers=8, max_retries=3):
    # Process items with retry logic in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(worker_with_retry, items))
    return [r for r in results if r is not None]
```

**Benefits:**
- Faster batch operations
- Retry logic per item
- Non-blocking failures

## Design Decisions

### Why SQLite?

- **No setup required**: Works out of the box
- **Fast**: Excellent for local data
- **Reliable**: ACID transactions
- **Simple backup**: Just copy the .db file
- **Good enough**: Handles thousands of tasks easily

### Why Decorator-Based Commands?

Traditional argparse code is verbose and separates command logic from argument definitions. Decorators keep related code together:

```python
@parser.command(
    argument("--title", required=True),
    argument("--priority", type=int),
    help="Create a task"
)
def create__task(args):
    # Everything related to this command is in one place
    pass
```

### Why a Query DSL?

Instead of adding dozens of CLI flags (`--priority-gt`, `--status-eq`, etc.), a query language provides:
- More expressive filtering
- Familiar SQL-like syntax
- Extensible without code changes
- Composable conditions

## File Locations

- Database: `~/.local/share/cloudtask/tasks.db`
- Config: `~/.config/cloudtask/config.json`
- Logs: `~/.cache/cloudtask/cloudtask.log`

## Error Handling

All database operations wrap SQLite exceptions in `CloudTaskException` with helpful error messages. Logging captures all operations for debugging.
```

**Security**: API key stored with restrictive permissions
```python
with open(API_KEY_FILE, 'w') as f:
    f.write(api_key)
os.chmod(API_KEY_FILE, 0o600)  # rw------- (owner only)
```

### 6. Cache System

**Purpose**: Reduce API calls and improve performance.

**Features**:
- Time-based expiration
- Automatic validation
- JSON serialization
- Error recovery for corrupted cache

**Implementation**:
```python
class Cache:
    def is_valid(self) -> bool:
        if not os.path.exists(self.cache_file):
            return False
        
        cache_age = datetime.now() - datetime.fromtimestamp(
            os.path.getmtime(self.cache_file)
        )
        return cache_age < self.duration
    
    def get(self) -> Optional[Dict]:
        if not self.is_valid():
            return None
        
        try:
            with open(self.cache_file, 'r') as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError):
            return None  # Graceful degradation
```

**Performance Impact**:
```
Without cache: Every search = API call (200ms)
With cache (15min): First search = 200ms, subsequent = 5ms
40x speedup for repeated queries!
```

## Design Patterns Used

| Pattern | Component | Purpose |
## Implementation Notes

- Multi-word commands work by parsing function names: `create__task` becomes `create task`
- Tags are stored as JSON arrays in SQLite for simplicity
- Timestamps use Unix time (float) for easy sorting and comparison
- Error messages include context to help users fix issues

## Future Improvements

- Add full-text search on title/description
- Support for task dependencies (blocked by, blocks)
- Export/import to JSON or CSV
- Recurring task support
- Better date parsing (relative dates like "tomorrow", "next week")
