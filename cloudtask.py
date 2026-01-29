#!/usr/bin/env python3
"""
CloudTask CLI - A command-line task management system with SQLite storage.

A lightweight CLI for managing tasks locally with support for:
- Complex query filtering
- Batch operations
- Tag-based organization
- Priority management

Author: Phoebe Chau
License: MIT
"""

import argparse
import json
import os
import re
import sys
import time
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from concurrent.futures import ThreadPoolExecutor
import subprocess

# Application directories
DIRS = {
    'config': os.path.join(os.path.expanduser('~'), '.config'),
    'cache': os.path.join(os.path.expanduser('~'), '.cache'),
    'data': os.path.join(os.path.expanduser('~'), '.local', 'share'),
}

# Initialize application directories
APP_NAME = "cloudtask"
VERSION = "1.0.0"

for key in DIRS.keys():
    DIRS[key] = path = os.path.join(DIRS[key], APP_NAME)
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

CONFIG_FILE = os.path.join(DIRS['config'], "config.json")
DB_FILE = os.path.join(DIRS['data'], "tasks.db")

# Setup logging
LOG_FILE = os.path.join(DIRS['cache'], "cloudtask.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # Only show warnings/errors to stderr by default


# ============================================================================
# UTILITY CLASSES AND DECORATORS
# ============================================================================

class CloudTaskException(Exception):
    """Base exception for CloudTask CLI"""
    pass


class argument:
    """Wrapper for command arguments with metadata"""
    def __init__(self, *args, mutex_group: Optional[str] = None, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.mutex_group = mutex_group


class hidden_aliases:
    """Helper class for hidden command aliases"""
    def __init__(self, aliases: List[str]):
        self.aliases = aliases

    def __iter__(self):
        return iter(self.aliases)

    def __bool__(self):
        return False

    def append(self, item):
        self.aliases.append(item)


class CustomHelpFormatter(argparse.RawTextHelpFormatter):
    """Custom formatter with wider help text"""
    def __init__(self, prog):
        super().__init__(prog, width=120, max_help_position=45, indent_increment=2)


class CommandParserWrapper:
    """Argument parser wrapper enabling decorator-based command registration."""
    
    def __init__(self, *args, **kwargs):
        if "formatter_class" not in kwargs:
            kwargs["formatter_class"] = CustomHelpFormatter
        
        self.parser = argparse.ArgumentParser(*args, **kwargs)
        self.parser.set_defaults(func=self._fail_with_help)
        self.subparsers_ = None
        self.subparser_objs = []
        self.post_setup_hooks = []
        self.verbs = set()
        self.objects = set()

    def _fail_with_help(self, *args, **kwargs):
        """Default handler that prints help when no command specified"""
        self.parser.print_help(sys.stderr)
        raise SystemExit(1)

    def add_argument(self, *args, **kwargs):
        """Add global argument available to all subcommands"""
        parent_only = kwargs.pop("parent_only", False)
        
        # Store global args to add to future subparsers
        if not parent_only:
            if not hasattr(self, '_global_args'):
                self._global_args = []
            self._global_args.append((args, kwargs.copy()))
            
            # Add to existing subparsers
            for subparser in self.subparser_objs:
                try:
                    if not hasattr(subparser, '_global_options_group'):
                        subparser._global_options_group = subparser.add_argument_group(
                            'Global options (available for all commands)'
                        )
                    subparser_kwargs = kwargs.copy()
                    subparser_kwargs['default'] = argparse.SUPPRESS
                    subparser._global_options_group.add_argument(*args, **subparser_kwargs)
                except argparse.ArgumentError:
                    pass  # Argument already exists
        
        return self.parser.add_argument(*args, **kwargs)

    def subparsers(self, *args, **kwargs):
        """Get or create subparsers for commands"""
        if self.subparsers_ is None:
            kwargs["metavar"] = "command"
            kwargs["help"] = "command to run"
            self.subparsers_ = self.parser.add_subparsers(*args, **kwargs)
        return self.subparsers_

    def _get_command_name(self, verb: str, obj: str) -> str:
        """Generate command name from verb and object"""
        if obj:
            self.verbs.add(verb)
            self.objects.add(obj)
            return f"{verb} {obj}"
        else:
            self.objects.add(verb)
            return verb

    def command(self, *arguments, aliases: Tuple[str, ...] = (), help: Optional[str] = None, **kwargs):
        """Decorator for registering CLI commands."""
        help_text = help
        
        def decorator(func):
            # Parse function name: verb__object or just verb
            dashed_name = func.__name__.replace("_", "-")
            verb, _, obj = dashed_name.partition("--")
            name = self._get_command_name(verb, obj)
            
            # Transform aliases
            aliases_transformed = [] if aliases else hidden_aliases([])
            for alias in aliases:
                alias_verb, _, alias_obj = alias.partition(" ")
                aliases_transformed.append(self._get_command_name(alias_verb, alias_obj))
            
            if "formatter_class" not in kwargs:
                kwargs["formatter_class"] = CustomHelpFormatter
            
            subparser = self.subparsers().add_parser(
                name, 
                aliases=aliases_transformed, 
                help=help_text, 
                **kwargs
            )
            
            self.subparser_objs.append(subparser)
            
            # Add any global args that were defined before this command
            if hasattr(self, '_global_args'):
                if not hasattr(subparser, '_global_options_group'):
                    subparser._global_options_group = subparser.add_argument_group(
                        'Global options (available for all commands)'
                    )
                for arg_args, arg_kwargs in self._global_args:
                    try:
                        subparser_kwargs = arg_kwargs.copy()
                        subparser_kwargs['default'] = argparse.SUPPRESS
                        subparser._global_options_group.add_argument(*arg_args, **subparser_kwargs)
                    except argparse.ArgumentError:
                        pass  # Argument already exists
            
            self._process_arguments_with_groups(subparser, arguments)
            subparser.set_defaults(func=func)
            
            return func
        
        # Handle case where decorator is used without parentheses
        if len(arguments) == 1 and not isinstance(arguments[0], argument):
            func = arguments[0]
            arguments = []
            return decorator(func)
        
        return decorator

    def _process_arguments_with_groups(self, parser_obj: argparse.ArgumentParser, 
                                      arguments: Tuple[argument, ...]):
        """Process arguments and handle mutually exclusive groups"""
        mutex_groups_to_required = {}
        arg_to_group = {}
        
        # Determine mutex groups and their required status
        for arg in arguments:
            key = arg.args[0]
            if arg.mutex_group:
                is_required = arg.kwargs.pop('required', False)
                group_name = arg.mutex_group
                arg_to_group[key] = group_name
                if not mutex_groups_to_required.get(group_name):
                    mutex_groups_to_required[group_name] = is_required
        
        # Create mutex group parsers
        name_to_group_parser = {}
        for group_name, is_required in mutex_groups_to_required.items():
            mutex_group = parser_obj.add_mutually_exclusive_group(required=is_required)
            name_to_group_parser[group_name] = mutex_group
        
        # Add arguments to appropriate parser
        for arg in arguments:
            key = arg.args[0]
            target_parser = name_to_group_parser.get(arg_to_group.get(key), parser_obj)
            target_parser.add_argument(*arg.args, **arg.kwargs)

    def parse_args(self, argv: Optional[List[str]] = None, *args, **kwargs):
        """Parse command line arguments with special handling for multi-word commands"""
        if argv is None:
            argv = sys.argv[1:]
        
        # Handle multi-word commands (e.g., "create task" -> "create task")
        argv_processed = []
        for token in argv:
            if argv_processed and argv_processed[-1] in self.verbs:
                argv_processed[-1] += " " + token
            else:
                argv_processed.append(token)
        
        args_parsed = self.parser.parse_args(argv_processed, *args, **kwargs)
        
        # Run post-setup hooks
        for hook in self.post_setup_hooks:
            hook(args_parsed)
        
        return args_parsed


# ============================================================================
# DATABASE BACKEND
# ============================================================================

class SQLiteBackend:
    """SQLite database backend for task storage."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """Initialize database schema if it doesn't exist."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        description TEXT,
                        status TEXT DEFAULT 'pending',
                        priority INTEGER DEFAULT 5,
                        tags TEXT,
                        created REAL NOT NULL,
                        updated REAL NOT NULL,
                        due_date REAL,
                        assigned_to TEXT,
                        project TEXT
                    )
                ''')
                conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)
                ''')
                conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_priority ON tasks(priority)
                ''')
                conn.commit()
                logger.info(f"Database initialized at {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"Failed to initialize database: {e}")
            raise CloudTaskException(f"Database initialization failed: {e}")
    
    def _row_to_dict(self, row: sqlite3.Row) -> Dict:
        """Convert SQLite row to dictionary."""
        task = dict(row)
        if task.get('tags'):
            task['tags'] = json.loads(task['tags'])
        return task
    
    def create_task(self, task_data: Dict) -> Dict:
        """Create a new task."""
        now = time.time()
        task_data['created'] = now
        task_data['updated'] = now
        
        if 'tags' in task_data and isinstance(task_data['tags'], list):
            task_data['tags'] = json.dumps(task_data['tags'])
        
        fields = ', '.join(task_data.keys())
        placeholders = ', '.join(['?' for _ in task_data])
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    f'INSERT INTO tasks ({fields}) VALUES ({placeholders})',
                    list(task_data.values())
                )
                task_id = cursor.lastrowid
                conn.commit()
                logger.info(f"Created task {task_id}: {task_data.get('title')}")
                
                row = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
                return self._row_to_dict(row)
        except sqlite3.Error as e:
            logger.error(f"Failed to create task: {e}")
            raise CloudTaskException(f"Failed to create task: {e}")
    
    def search_tasks(self, query: Dict) -> List[Dict]:
        """Search tasks based on query filters."""
        where_clauses = []
        params = []
        
        for field, conditions in query.items():
            if field in ['order', 'limit']:
                continue
            
            if not isinstance(conditions, dict):
                continue
            
            for op, value in conditions.items():
                if op == 'eq':
                    where_clauses.append(f"{field} = ?")
                    params.append(value)
                elif op == 'neq':
                    where_clauses.append(f"{field} != ?")
                    params.append(value)
                elif op == 'gt':
                    where_clauses.append(f"{field} > ?")
                    params.append(value)
                elif op == 'gte':
                    where_clauses.append(f"{field} >= ?")
                    params.append(value)
                elif op == 'lt':
                    where_clauses.append(f"{field} < ?")
                    params.append(value)
                elif op == 'lte':
                    where_clauses.append(f"{field} <= ?")
                    params.append(value)
                elif op == 'in':
                    if field == 'tags':
                        # Special handling for tags stored as JSON
                        tag_conditions = []
                        for tag in value:
                            where_clauses.append(f"tags LIKE ?")
                            params.append(f'%"{tag}"%')
                    else:
                        placeholders = ','.join(['?' for _ in value])
                        where_clauses.append(f"{field} IN ({placeholders})")
                        params.extend(value)
                elif op == 'notin':
                    placeholders = ','.join(['?' for _ in value])
                    where_clauses.append(f"{field} NOT IN ({placeholders})")
                    params.extend(value)
        
        where_sql = ' AND '.join(where_clauses) if where_clauses else '1=1'
        
        order_by = 'priority DESC, created DESC'
        if 'order' in query and query['order']:
            order_parts = []
            for field, direction in query['order']:
                order_parts.append(f"{field} {direction.upper()}")
            order_by = ', '.join(order_parts)
        
        limit = query.get('limit', 100)
        
        sql = f'SELECT * FROM tasks WHERE {where_sql} ORDER BY {order_by} LIMIT ?'
        params.append(limit)
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, params).fetchall()
                logger.debug(f"Search returned {len(rows)} tasks")
                return [self._row_to_dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Search failed: {e}")
            raise CloudTaskException(f"Search failed: {e}")
    
    def update_task(self, task_id: int, updates: Dict) -> Dict:
        """Update an existing task."""
        updates['updated'] = time.time()
        
        if 'tags' in updates and isinstance(updates['tags'], list):
            updates['tags'] = json.dumps(updates['tags'])
        
        set_clause = ', '.join([f"{k} = ?" for k in updates.keys()])
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    f'UPDATE tasks SET {set_clause} WHERE id = ?',
                    list(updates.values()) + [task_id]
                )
                if cursor.rowcount == 0:
                    raise CloudTaskException(f"Task {task_id} not found")
                conn.commit()
                logger.info(f"Updated task {task_id}")
                
                row = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
                return self._row_to_dict(row)
        except sqlite3.Error as e:
            logger.error(f"Failed to update task {task_id}: {e}")
            raise CloudTaskException(f"Failed to update task: {e}")
    
    def delete_task(self, task_id: int) -> bool:
        """Delete a task."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
                if cursor.rowcount == 0:
                    raise CloudTaskException(f"Task {task_id} not found")
                conn.commit()
                logger.info(f"Deleted task {task_id}")
                return True
        except sqlite3.Error as e:
            logger.error(f"Failed to delete task {task_id}: {e}")
            raise CloudTaskException(f"Failed to delete task: {e}")
    
    def get_task(self, task_id: int) -> Optional[Dict]:
        """Get a single task by ID."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
                if row:
                    return self._row_to_dict(row)
                return None
        except sqlite3.Error as e:
            logger.error(f"Failed to get task {task_id}: {e}")
            raise CloudTaskException(f"Failed to get task: {e}")


# ============================================================================
# QUERY DSL - COMPLEX FILTERING SYSTEM
# ============================================================================

def parse_query(query_str: Optional[str], base_query: Optional[Dict] = None,
                valid_fields: Optional[set] = None,
                field_aliases: Optional[Dict] = None,
                field_multipliers: Optional[Dict] = None) -> Dict:
    """
    Parse query string into structured query dictionary.
    
    Query Syntax:
        field op value [field op value ...]
        
    Operators:
        <, <=, ==, !=, >=, >, in, notin, eq, neq, gt, gte, lt, lte
        
    Examples:
        "priority >= 5 status == active tags in [work,urgent]"
        "created > 2024-01-01 assigned != none"
    """
    # Handle list input first
    if isinstance(query_str, list):
        query_str = " ".join(query_str)
    
    if query_str is None or not query_str.strip():
        return base_query or {}
    
    result = base_query.copy() if base_query else {}
    field_aliases = field_aliases or {}
    field_multipliers = field_multipliers or {}
    valid_fields = valid_fields or set()
    
    query_str = query_str.strip()
    
    # Pattern to match: field operator value
    pattern = r"([a-zA-Z0-9_]+)( *[=><!]+| +(?:[lg]te?|nin|neq|eq|not ?eq|not ?in|in) )?( *)(\[[^\]]+\]|\"[^\"]+\"|[^ ]+)?( *)"
    matches = re.findall(pattern, query_str)
    
    # Verify entire string was consumed
    reconstructed = "".join("".join(m) for m in matches)
    if reconstructed != query_str:
        raise ValueError(
            f"Failed to parse query. Unconsumed text: {repr(query_str)}\n"
            f"Did you forget to quote your query?"
        )
    
    # Operator mapping
    op_names = {
        ">=": "gte", ">": "gt", "gt": "gt", "gte": "gte",
        "<=": "lte", "<": "lt", "lt": "lt", "lte": "lte",
        "!=": "neq", "==": "eq", "=": "eq", "eq": "eq", "neq": "neq",
        "noteq": "neq", "not eq": "neq",
        "notin": "notin", "not in": "notin", "nin": "notin",
        "in": "in",
    }
    
    for field, op, _, value, _ in matches:
        value = value.strip(",[]\"")
        op = op.strip()
        op_name = op_names.get(op)
        
        # Apply field aliases
        if field in field_aliases:
            field = field_aliases[field]
        
        # Validate field
        if valid_fields and field not in valid_fields:
            print(f"Warning: Unrecognized field '{field}'", file=sys.stderr)
        
        if not op_name:
            raise ValueError(f"Unknown operator: {repr(op)}")
        
        if not value:
            raise ValueError(f"Value cannot be blank for field '{field}'")
        
        # Handle wildcard values
        if value in ["?", "*", "any"]:
            if op_name != "eq":
                raise ValueError("Wildcard only valid with '=' operator")
            result.pop(field, None)
            continue
        
        # Handle list values for 'in' and 'notin' operators
        if op_name in ["in", "notin"]:
            value = [v.strip().replace('_', ' ') for v in value.split(",") if v.strip()]
        else:
            value = value.replace('_', ' ')
        
        # Apply multipliers for unit conversion
        if field in field_multipliers:
            try:
                value = float(value) * field_multipliers[field]
            except (ValueError, TypeError):
                pass
        
        # Type coercion
        if value == 'true' or value == 'True':
            value = True
        elif value == 'false' or value == 'False':
            value = False
        elif value == 'None' or value == 'null':
            value = None
        
        # Build query structure
        if field not in result:
            result[field] = {}
        result[field][op_name] = value
    
    return result


# ============================================================================
# CACHING SYSTEM
# ============================================================================

class Cache:
    """Simple file-based cache with time-based expiration."""
    
    def __init__(self, cache_file: str, duration: timedelta):
        self.cache_file = cache_file
        self.duration = duration

    def is_valid(self) -> bool:
        """Check if cache exists and is not expired"""
        if not os.path.exists(self.cache_file):
            return False
        
        cache_age = datetime.now() - datetime.fromtimestamp(
            os.path.getmtime(self.cache_file)
        )
        return cache_age < self.duration

    def get(self) -> Optional[Dict]:
        """Get cached data if valid"""
        if not self.is_valid():
            return None
        
        try:
            with open(self.cache_file, 'r') as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            print(f"Warning: Failed to read cache: {e}", file=sys.stderr)
            return None

    def set(self, data: Dict) -> bool:
        """Write data to cache"""
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(data, f, indent=2)
            return True
        except IOError as e:
            print(f"Warning: Failed to write cache: {e}", file=sys.stderr)
            return False

    def clear(self):
        """Remove cache file"""
        try:
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
        except IOError:
            pass


# ============================================================================
# CONFIGURATION MANAGEMENT
# ============================================================================

class Config:
    """Configuration file management."""
    
    def __init__(self, config_file: str):
        self.config_file = config_file
        self._config = self._load()

    def _load(self) -> Dict:
        """Load configuration from file"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    return json.load(f)
            except (IOError, json.JSONDecodeError):
                pass
        return {}

    def save(self) -> bool:
        """Save configuration to file"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self._config, f, indent=2)
            return True
        except IOError as e:
            print(f"Error: Failed to save config: {e}", file=sys.stderr)
            return False

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value"""
        return self._config.get(key, default)

    def set(self, key: str, value: Any):
        """Set configuration value"""
        self._config[key] = value

    def delete(self, key: str):
        """Delete configuration value"""
        self._config.pop(key, None)


# ============================================================================
# CONCURRENT OPERATIONS
# ============================================================================

def execute_concurrent(func, items: List, max_workers: int = 8, 
                       max_retries: int = 3) -> List:
    """Execute function concurrently on multiple items with retry logic."""
    results = []
    
    def worker_with_retry(item):
        """Worker function with retry logic"""
        backoff_time = 0.25
        
        for attempt in range(max_retries):
            try:
                if isinstance(item, tuple):
                    result = func(*item)
                else:
                    result = func(item)
                return result
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f"Error processing {item}: {e}", file=sys.stderr)
                    return None
                time.sleep(backoff_time)
                backoff_time *= 1.5
        
        return None
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(worker_with_retry, items))
    
    return [r for r in results if r is not None]


# ============================================================================
# DISPLAY UTILITIES
# ============================================================================

def display_table(rows: List[Dict], fields: Tuple[Tuple[str, str, str, Any, bool], ...]):
    """Display data in formatted table."""
    if not rows:
        print("No results found.")
        return
    
    # Extract headers
    headers = [display_name for _, display_name, _, _, _ in fields]
    out_rows = [headers]
    
    # Calculate column widths
    col_widths = [len(h) for h in headers]
    
    # Process each row
    for row in rows:
        display_row = []
        for idx, (key, _, fmt, transform, _) in enumerate(fields):
            value = row.get(key)
            
            if value is None:
                text = "-"
            else:
                if transform:
                    value = transform(value)
                text = fmt.format(value)
            
            text = text.replace(' ', '_')
            col_widths[idx] = max(col_widths[idx], len(text))
            display_row.append(text)
        
        out_rows.append(display_row)
    
    # Print table
    for row in out_rows:
        formatted_cols = []
        for text, width, field_info in zip(row, col_widths, fields):
            _, _, _, _, left_justify = field_info
            if left_justify:
                formatted_cols.append(text.ljust(width))
            else:
                formatted_cols.append(text.rjust(width))
        print("  ".join(formatted_cols))


def format_timestamp(ts: float) -> str:
    """Format Unix timestamp to readable string"""
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')


def deindent(text: str) -> str:
    """Remove common leading whitespace from multiline strings"""
    text = re.sub(r" *$", "", text, flags=re.MULTILINE)
    indents = [len(x) for x in re.findall("^ *(?=[^ ])", text, re.MULTILINE) if x]
    if indents:
        min_indent = min(indents)
        text = re.sub(r"^ {," + str(min_indent) + "}", "", text, flags=re.MULTILINE)
    return text.strip()


# ============================================================================
# COMMAND LINE INTERFACE
# ============================================================================

# Initialize global parser
parser = CommandParserWrapper(
    description="CloudTask CLI - Local Task Management System",
    epilog="Use 'cloudtask COMMAND --help' for more information about a command",
    formatter_class=CustomHelpFormatter
)

# Global arguments
parser.add_argument("--raw", help="Output raw JSON", action="store_true")
parser.add_argument("--explain", help="Show query details without executing", action="store_true")
parser.add_argument("--verbose", "-v", help="Enable verbose logging", action="store_true")

# Global database backend instance
db_backend: Optional[SQLiteBackend] = None


def get_backend(args: argparse.Namespace) -> SQLiteBackend:
    """Get or create database backend instance."""
    global db_backend
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    if not db_backend:
        db_backend = SQLiteBackend(DB_FILE)
    
    return db_backend


# ============================================================================
# COMMAND IMPLEMENTATIONS
# ============================================================================

# Task field definitions for query parsing and display
TASK_FIELDS = {
    "id", "title", "description", "status", "priority", "tags",
    "created", "updated", "due_date", "assigned_to", "project"
}

TASK_ALIASES = {
    "desc": "description",
    "prio": "priority",
}

TASK_DISPLAY_FIELDS = (
    ("id", "ID", "{}", None, True),
    ("title", "Title", "{}", lambda x: x[:40], True),
    ("status", "Status", "{}", None, True),
    ("priority", "Priority", "{}", None, False),
    ("tags", "Tags", "{}", lambda x: ",".join(x) if isinstance(x, list) else x, True),
    ("created", "Created", "{}", format_timestamp, True),
    ("due_date", "Due", "{}", format_timestamp, True),
    ("assigned_to", "Assigned", "{}", None, True),
)


@parser.command(
    argument("--title", help="Task title", type=str, required=True),
    argument("--description", help="Task description", type=str),
    argument("--priority", help="Priority (1-10)", type=int, default=5),
    argument("--tags", help="Comma-separated tags", type=str),
    argument("--due-date", help="Due date (YYYY-MM-DD)", type=str),
    argument("--assigned-to", help="Assign to user", type=str),
    argument("--status", help="Task status", type=str, default="pending"),
    help="Create a new task"
)
def create__task(args: argparse.Namespace):
    """Create a new task."""
    backend = get_backend(args)
    
    task_data = {
        "title": args.title,
        "priority": args.priority,
        "status": args.status,
    }
    
    if args.description:
        task_data["description"] = args.description
    if args.tags:
        task_data["tags"] = [t.strip() for t in args.tags.split(",")]
    if args.due_date:
        try:
            # Convert date string to Unix timestamp
            due_dt = datetime.strptime(args.due_date, "%Y-%m-%d")
            task_data["due_date"] = due_dt.timestamp()
        except ValueError:
            print(f"Error: Invalid date format. Use YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)
    if args.assigned_to:
        task_data["assigned_to"] = args.assigned_to
    
    if args.explain:
        print("Would create task with data:")
        print(json.dumps(task_data, indent=2))
        return
    
    try:
        result = backend.create_task(task_data)
        if args.raw:
            print(json.dumps(result, indent=2))
        else:
            print(f"✓ Task created successfully: ID {result.get('id')}")
            print(f"  Title: {result.get('title')}")
            print(f"  Priority: {result.get('priority')}")
    except CloudTaskException as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@parser.command(
    argument("query", help="Search query", nargs="*"),
    argument("--limit", help="Maximum results", type=int, default=50),
    argument("-o", "--order", help="Sort order (field or field-)", type=str, default="priority-"),
    help="Search for tasks using query syntax",
    epilog=deindent("""
        Query Examples:
            cloudtask search tasks "priority >= 7 status == active"
            cloudtask search tasks "tags in [work,urgent] assigned_to != none"
            cloudtask search tasks "priority >= 5"
            
        Available Fields:
            id, title, description, status, priority, tags,
            created, updated, due_date, assigned_to, project
            
        Operators:
            <, <=, ==, !=, >=, >, in, notin
    """)
)
def search__tasks(args: argparse.Namespace):
    """Search for tasks with complex filtering."""
    backend = get_backend(args)
    
    try:
        # Parse query
        query = parse_query(
            args.query,
            base_query={},
            valid_fields=TASK_FIELDS,
            field_aliases=TASK_ALIASES
        )
        
        # Add ordering
        order_field = args.order.rstrip('-+')
        order_dir = "desc" if args.order.endswith('-') else "asc"
        query["order"] = [[order_field, order_dir]]
        query["limit"] = args.limit
        
        if args.explain:
            print("Query:")
            print(json.dumps(query, indent=2))
            return
        
        tasks = backend.search_tasks(query)
        
        if args.raw:
            print(json.dumps(tasks, indent=2))
        else:
            if not tasks:
                print("No tasks found.")
            else:
                display_table(tasks, TASK_DISPLAY_FIELDS)
                print(f"\nFound {len(tasks)} task(s)")
            
    except (ValueError, CloudTaskException) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@parser.command(
    argument("task_id", help="Task ID", type=int),
    help="Delete a task"
)
def delete__task(args: argparse.Namespace):
    """Delete a task by ID."""
    backend = get_backend(args)
    
    if args.explain:
        print(f"Would delete task {args.task_id}")
        return
    
    try:
        # Check if task exists first
        task = backend.get_task(args.task_id)
        if not task:
            print(f"Error: Task {args.task_id} not found", file=sys.stderr)
            sys.exit(1)
        
        backend.delete_task(args.task_id)
        print(f"✓ Task {args.task_id} deleted successfully")
        print(f"  Title: {task.get('title')}")
    except CloudTaskException as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@parser.command(
    argument("task_ids", help="Task IDs to update", type=int, nargs="+"),
    argument("--status", help="New status", type=str),
    argument("--priority", help="New priority", type=int),
    help="Batch update multiple tasks"
)
def update__tasks(args: argparse.Namespace):
    """Update multiple tasks concurrently."""
    backend = get_backend(args)
    
    update_data = {}
    if args.status:
        update_data["status"] = args.status
    if args.priority is not None:
        update_data["priority"] = args.priority
    
    if not update_data:
        print("Error: Provide at least one field to update (--status or --priority)", file=sys.stderr)
        sys.exit(1)
    
    if args.explain:
        print(f"Would update tasks {args.task_ids} with:")
        print(json.dumps(update_data, indent=2))
        return
    
    def update_task(task_id: int):
        try:
            result = backend.update_task(task_id, update_data.copy())
            return f"✓ Task {task_id}: Updated successfully"
        except CloudTaskException as e:
            return f"✗ Task {task_id}: {e}"
    
    # Execute updates concurrently
    print(f"Updating {len(args.task_ids)} task(s)...")
    results = execute_concurrent(update_task, args.task_ids, max_workers=8)
    
    for result in results:
        print(result)


@parser.command(
    help="Show current configuration"
)
def show__config(args: argparse.Namespace):
    """Display current configuration."""
    config = Config(CONFIG_FILE)
    
    print("CloudTask CLI Configuration:")
    print(f"  Config file: {CONFIG_FILE}")
    print(f"  Database: {DB_FILE}")
    print(f"  Log file: {LOG_FILE}")
    
    # Show database stats
    try:
        backend = get_backend(args)
        tasks = backend.search_tasks({"limit": 10000})
        print(f"\nDatabase Statistics:")
        print(f"  Total tasks: {len(tasks)}")
        
        status_counts = {}
        for task in tasks:
            status = task.get('status', 'unknown')
            status_counts[status] = status_counts.get(status, 0) + 1
        
        for status, count in sorted(status_counts.items()):
            print(f"  {status}: {count}")
    except Exception as e:
        print(f"  Error reading database: {e}", file=sys.stderr)


@parser.command(
    help="Show version information"
)
def version(args: argparse.Namespace):
    """Display version and system information."""
    print(f"CloudTask CLI v{VERSION}")
    print(f"Python: {sys.version}")
    print(f"Database: {DB_FILE}")
    print(f"Config directory: {DIRS['config']}")
    print(f"Data directory: {DIRS['data']}")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Main entry point for the CLI"""
    try:
        args = parser.parse_args()
        args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted by user", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        import traceback
        print(f"Unexpected error: {e}", file=sys.stderr)
        if hasattr(args, 'verbose') and args.verbose:
            traceback.print_exc()
        else:
            logger.debug(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
