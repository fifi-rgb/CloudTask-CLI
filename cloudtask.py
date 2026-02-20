#!/usr/bin/env python3
"""
CloudTask CLI - A Professional Task Management System with Cloud Sync
"""

import argparse
import json
import os
import re
import sys
import time
import shutil
import importlib.metadata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus
import subprocess

try:
    import requests
except ImportError:
    print("Error: 'requests' library required. Install with: pip install requests")
    sys.exit(1)

try:
    import xdg
    DIRS = {
        'config': xdg.xdg_config_home(),
        'cache': xdg.xdg_cache_home()
    }
except ImportError:
    # Reasonable defaults for systems without xdg
    DIRS = {
        'config': os.path.join(os.path.expanduser('~'), '.config'),
        'cache': os.path.join(os.path.expanduser('~'), '.cache'),
    }

# Initialize application directories
APP_NAME = "cloudtask"
VERSION = "1.0.0"

for key in DIRS.keys():
    DIRS[key] = path = os.path.join(DIRS[key], APP_NAME)
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

API_KEY_FILE = os.path.join(DIRS['config'], "api_key")
CONFIG_FILE = os.path.join(DIRS['config'], "config.json")
CACHE_FILE = os.path.join(DIRS['cache'], "task_cache.json")
CACHE_DURATION = timedelta(minutes=15)

# Default API endpoint
API_BASE_URL = os.getenv("CLOUDTASK_URL", "https://api.cloudtask.example.com")


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
    """
    Custom argument parser wrapper that enables decorator-based command registration.
    
    This pattern allows for clean, maintainable CLI command definitions using
    Python decorators rather than imperative parser configuration.
    """
    
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
        
        if not parent_only:
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
        """
        Decorator for registering CLI commands.
        
        Usage:
            @parser.command(
                argument("--name", help="Task name", required=True),
                help="Create a new task"
            )
            def create__task(args):
                # Implementation here
                pass
        """
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
# API CLIENT LAYER
# ============================================================================

class APIClient:
    """
    REST API client with retry logic, exponential backoff, and authentication.
    
    Demonstrates:
    - HTTP request handling with proper error management
    - Retry logic with exponential backoff
    - Bearer token authentication
    - JSON serialization/deserialization
    """
    
    def __init__(self, base_url: str, api_key: Optional[str] = None, 
                 max_retries: int = 3, timeout: int = 30):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = requests.Session()

    def _get_headers(self) -> Dict[str, str]:
        """Generate request headers with authentication"""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, method: str, endpoint: str, 
                 params: Optional[Dict] = None, 
                 json_data: Optional[Dict] = None) -> requests.Response:
        """
        Make HTTP request with retry logic and exponential backoff.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint path
            params: Query parameters
            json_data: JSON request body
            
        Returns:
            Response object
            
        Raises:
            CloudTaskException: On request failure after all retries
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = self._get_headers()
        
        backoff_time = 0.25
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_data,
                    timeout=self.timeout
                )
                
                # Handle rate limiting with exponential backoff
                if response.status_code == 429:
                    if attempt < self.max_retries - 1:
                        time.sleep(backoff_time)
                        backoff_time *= 1.5
                        continue
                
                response.raise_for_status()
                return response
                
            except requests.exceptions.RequestException as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    time.sleep(backoff_time)
                    backoff_time *= 1.5
                    continue
        
        raise CloudTaskException(
            f"Request failed after {self.max_retries} attempts: {last_exception}"
        )

    def get(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """Make GET request"""
        response = self._request("GET", endpoint, params=params)
        return response.json() if response.content else {}

    def post(self, endpoint: str, json_data: Optional[Dict] = None) -> Dict:
        """Make POST request"""
        response = self._request("POST", endpoint, json_data=json_data)
        return response.json() if response.content else {}

    def put(self, endpoint: str, json_data: Optional[Dict] = None) -> Dict:
        """Make PUT request"""
        response = self._request("PUT", endpoint, json_data=json_data)
        return response.json() if response.content else {}

    def delete(self, endpoint: str, json_data: Optional[Dict] = None) -> Dict:
        """Make DELETE request"""
        response = self._request("DELETE", endpoint, json_data=json_data)
        return response.json() if response.content else {}


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
        
    This demonstrates:
    - Custom DSL parsing
    - Regular expressions for complex pattern matching
    - Query builder pattern
    - Type coercion and validation
    """
    if query_str is None or not query_str.strip():
        return base_query or {}
    
    result = base_query.copy() if base_query else {}
    field_aliases = field_aliases or {}
    field_multipliers = field_multipliers or {}
    valid_fields = valid_fields or set()
    
    if isinstance(query_str, list):
        query_str = " ".join(query_str)
    
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
    """
    Simple file-based cache with expiration.
    
    Demonstrates:
    - File I/O with JSON serialization
    - Time-based cache invalidation
    - Error handling for file operations
    """
    
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
    """
    Configuration file management with XDG directory support.
    
    Demonstrates:
    - Configuration file handling
    - Default value management
    - Secure API key storage
    """
    
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
    """
    Execute function concurrently on multiple items with retry logic.
    
    Demonstrates:
    - ThreadPoolExecutor for parallel execution
    - Retry logic with exponential backoff
    - Error handling in concurrent context
    
    Args:
        func: Function to execute on each item
        items: List of items to process
        max_workers: Maximum number of concurrent workers
        max_retries: Maximum retry attempts per item
        
    Returns:
        List of results
    """
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
    """
    Display data in formatted table.
    
    Args:
        rows: List of data dictionaries
        fields: Tuple of (key, display_name, format_str, transform_func, left_justify)
        
    Demonstrates:
    - Formatted output generation
    - Dynamic column width calculation
    - Data transformation pipeline
    """
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
    description="CloudTask CLI - Professional Task Management System",
    epilog="Use 'cloudtask COMMAND --help' for more information about a command",
    formatter_class=CustomHelpFormatter
)

# Global arguments
parser.add_argument("--api-key", help="API key for authentication", type=str)
parser.add_argument("--url", help="API base URL", type=str, default=API_BASE_URL)
parser.add_argument("--raw", help="Output raw JSON", action="store_true")
parser.add_argument("--explain", help="Show request details without executing", action="store_true")

# Global API client instance
api_client: Optional[APIClient] = None


def get_api_client(args: argparse.Namespace) -> APIClient:
    """Get or create API client instance"""
    global api_client
    
    api_key = args.api_key
    if not api_key and os.path.exists(API_KEY_FILE):
        try:
            with open(API_KEY_FILE, 'r') as f:
                api_key = f.read().strip()
        except IOError:
            pass
    
    if not api_client or api_client.api_key != api_key:
        api_client = APIClient(args.url, api_key)
    
    return api_client


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
    help="Create a new task"
)
def create__task(args: argparse.Namespace):
    """
    Create a new task in the system.
    
    Example:
        cloudtask create task --title "Complete report" --priority 8 --tags "work,urgent"
    """
    client = get_api_client(args)
    
    task_data = {
        "title": args.title,
        "priority": args.priority,
    }
    
    if args.description:
        task_data["description"] = args.description
    if args.tags:
        task_data["tags"] = [t.strip() for t in args.tags.split(",")]
    if args.due_date:
        task_data["due_date"] = args.due_date
    if args.assigned_to:
        task_data["assigned_to"] = args.assigned_to
    
    if args.explain:
        print("Would create task with data:")
        print(json.dumps(task_data, indent=2))
        return
    
    try:
        result = client.post("/tasks", task_data)
        if args.raw:
            print(json.dumps(result, indent=2))
        else:
            print(f"Task created successfully: ID {result.get('id')}")
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
            cloudtask search tasks "created > 2024-01-01"
            
        Available Fields:
            id, title, description, status, priority, tags,
            created, updated, due_date, assigned_to, project
            
        Operators:
            <, <=, ==, !=, >=, >, in, notin
    """)
)
def search__tasks(args: argparse.Namespace):
    """Search for tasks with complex filtering"""
    client = get_api_client(args)
    
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
        
        result = client.post("/tasks/search", query)
        
        if args.raw:
            print(json.dumps(result, indent=2))
        else:
            tasks = result.get("tasks", [])
            display_table(tasks, TASK_DISPLAY_FIELDS)
            
    except (ValueError, CloudTaskException) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@parser.command(
    argument("task_id", help="Task ID", type=int),
    help="Delete a task"
)
def delete__task(args: argparse.Namespace):
    """Delete a task by ID"""
    client = get_api_client(args)
    
    if args.explain:
        print(f"Would delete task {args.task_id}")
        return
    
    try:
        result = client.delete(f"/tasks/{args.task_id}")
        if args.raw:
            print(json.dumps(result, indent=2))
        else:
            print(f"Task {args.task_id} deleted successfully")
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
    """
    Update multiple tasks concurrently.
    
    Demonstrates concurrent operations with ThreadPoolExecutor.
    """
    client = get_api_client(args)
    
    update_data = {}
    if args.status:
        update_data["status"] = args.status
    if args.priority is not None:
        update_data["priority"] = args.priority
    
    if not update_data:
        print("Error: Provide at least one field to update", file=sys.stderr)
        sys.exit(1)
    
    if args.explain:
        print(f"Would update tasks {args.task_ids} with:")
        print(json.dumps(update_data, indent=2))
        return
    
    def update_task(task_id: int):
        try:
            result = client.put(f"/tasks/{task_id}", update_data)
            return f"Task {task_id}: Success"
        except CloudTaskException as e:
            return f"Task {task_id}: Error - {e}"
    
    # Execute updates concurrently
    results = execute_concurrent(update_task, args.task_ids, max_workers=8)
    
    for result in results:
        print(result)


@parser.command(
    argument("--key", help="API key", type=str, required=True),
    help="Set API key for authentication"
)
def set__api_key(args: argparse.Namespace):
    """Save API key to configuration file"""
    try:
        with open(API_KEY_FILE, 'w') as f:
            f.write(args.api_key)
        os.chmod(API_KEY_FILE, 0o600)  # Secure permissions
        print(f"API key saved to {API_KEY_FILE}")
    except IOError as e:
        print(f"Error: Failed to save API key: {e}", file=sys.stderr)
        sys.exit(1)


@parser.command(
    help="Show current configuration"
)
def show__config(args: argparse.Namespace):
    """Display current configuration"""
    config = Config(CONFIG_FILE)
    
    print("Configuration:")
    print(f"  Config file: {CONFIG_FILE}")
    print(f"  API key file: {API_KEY_FILE}")
    print(f"  Cache file: {CACHE_FILE}")
    print(f"  API URL: {args.url}")
    print(f"\nAPI Key: {'Set' if os.path.exists(API_KEY_FILE) else 'Not set'}")


@parser.command(
    help="Clear cache"
)
def clear__cache(args: argparse.Namespace):
    """Clear the application cache"""
    cache = Cache(CACHE_FILE, CACHE_DURATION)
    cache.clear()
    print("Cache cleared successfully")


@parser.command(
    help="Show version information"
)
def version(args: argparse.Namespace):
    """Display version and system information"""
    print(f"CloudTask CLI v{VERSION}")
    print(f"Python: {sys.version}")
    print(f"Config directory: {DIRS['config']}")
    print(f"Cache directory: {DIRS['cache']}")


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
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
