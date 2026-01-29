"""
Example usage scripts for CloudTask CLI

This file demonstrates various ways to use the CLI and its advanced features.
"""

# ============================================================================
# BASIC TASK OPERATIONS
# ============================================================================

# Create a simple task
"""
python cloudtask.py create task \
    --title "Fix authentication bug" \
    --priority 9
"""

# Create a detailed task
"""
python cloudtask.py create task \
    --title "Implement user dashboard" \
    --description "Create responsive dashboard with charts and user stats" \
    --priority 7 \
    --tags "frontend,dashboard,high-priority" \
    --due-date "2026-03-01" \
    --assigned-to "alice@example.com"
"""

# ============================================================================
# QUERY DSL EXAMPLES
# ============================================================================

# Simple equality search
"""
python cloudtask.py search tasks "status == active"
"""

# Multiple conditions with AND logic
"""
python cloudtask.py search tasks "priority >= 8 status == pending assigned_to != none"
"""

# Using IN operator for tags
"""
python cloudtask.py search tasks "tags in [urgent,bug,security]"
"""

# Using NOTIN operator for exclusion
"""
python cloudtask.py search tasks "status notin [completed,cancelled]"
"""

# Date range queries
"""
python cloudtask.py search tasks "created >= 2026-01-01 created < 2026-02-01"
"""

# Complex multi-field query
"""
python cloudtask.py search tasks \
    "priority >= 7 status == active tags in [work,urgent] assigned_to == john@example.com"
"""

# Search with custom ordering
"""
python cloudtask.py search tasks "status == pending" --order "priority-,created"
"""

# Limit results
"""
python cloudtask.py search tasks "project == web-app" --limit 10
"""

# ============================================================================
# BATCH OPERATIONS (CONCURRENT EXECUTION)
# ============================================================================

# Update status for multiple tasks in parallel
"""
python cloudtask.py update tasks 101 102 103 104 105 106 \
    --status "in-progress"
"""

# Bulk priority update
"""
python cloudtask.py update tasks 201 202 203 204 \
    --priority 10
"""

# Update both status and priority
"""
python cloudtask.py update tasks 301 302 303 \
    --status "completed" \
    --priority 5
"""

# ============================================================================
# CONFIGURATION AND MANAGEMENT
# ============================================================================

# Set API key (stored securely)
"""
python cloudtask.py set api-key --key "sk_live_abc123xyz789..."
"""

# View configuration
"""
python cloudtask.py show config
"""

# Clear cache
"""
python cloudtask.py clear cache
"""

# Check version
"""
python cloudtask.py version
"""

# ============================================================================
# DEBUGGING AND DEVELOPMENT
# ============================================================================

# Dry-run mode (explain without executing)
"""
python cloudtask.py create task \
    --title "Test task" \
    --priority 5 \
    --explain
"""

# Raw JSON output
"""
python cloudtask.py search tasks "status == active" --raw
"""

# Custom API endpoint
"""
python cloudtask.py --url "http://localhost:8000/api" \
    search tasks "priority >= 5"
"""

# Custom API key for single command
"""
python cloudtask.py --api-key "temp_key_123" \
    search tasks "status == pending"
"""

# ============================================================================
# ADVANCED QUERY EXAMPLES
# ============================================================================

# Find unassigned high-priority tasks
"""
python cloudtask.py search tasks \
    "priority >= 8 assigned_to == none status notin [completed,cancelled]" \
    --order "priority-"
"""

# Find overdue tasks
"""
python cloudtask.py search tasks \
    "due_date < $(date +%Y-%m-%d) status != completed" \
    --order "due_date"
"""

# Find tasks created this week
"""
python cloudtask.py search tasks \
    "created >= 2026-01-23 created < 2026-01-30" \
    --order "created-"
"""

# Complex project query
"""
python cloudtask.py search tasks \
    "project == web-redesign tags in [frontend,ui] status == active priority >= 6" \
    --limit 25 \
    --order "priority-,created"
"""

# ============================================================================
# REAL-WORLD WORKFLOW EXAMPLES
# ============================================================================

# Daily standup: Show my active tasks
"""
python cloudtask.py search tasks \
    "assigned_to == $USER status == active" \
    --order "priority-" \
    --limit 10
"""

# Weekly review: Show completed tasks
"""
python cloudtask.py search tasks \
    "status == completed updated >= 2026-01-22" \
    --order "updated-"
"""

# Sprint planning: High-priority unassigned tasks
"""
python cloudtask.py search tasks \
    "priority >= 7 assigned_to == none status == pending" \
    --order "priority-"
"""

# Bug triage: Show all bugs
"""
python cloudtask.py search tasks \
    "tags in [bug,issue] status notin [completed,wontfix]" \
    --order "priority-,created"
"""

# End of sprint: Mark multiple tasks as completed
"""
# Get task IDs from search
TASK_IDS=$(python cloudtask.py search tasks \
    "status == in-progress assigned_to == $USER" \
    --raw | jq -r '.tasks[].id' | tr '\n' ' ')

# Batch update
python cloudtask.py update tasks $TASK_IDS --status "completed"
"""
