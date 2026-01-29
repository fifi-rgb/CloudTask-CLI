#!/usr/bin/env python3
"""
Tests for CloudTask CLI

Author: Phoebe Chau
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
import json
import tempfile
import os
import sqlite3

# Import modules to test
import sys
sys.path.insert(0, os.path.dirname(__file__))

from cloudtask import (
    parse_query,
    Cache,
    Config,
    SQLiteBackend,
    CloudTaskException,
    execute_concurrent,
    format_timestamp,
    deindent
)


class TestQueryParser(unittest.TestCase):
    """Test the query DSL parser"""
    
    def test_simple_equality(self):
        result = parse_query("status == active")
        self.assertEqual(result, {"status": {"eq": "active"}})
    
    def test_numeric_comparison(self):
        result = parse_query("priority >= 5")
        self.assertEqual(result, {"priority": {"gte": "5"}})
    
    def test_in_operator(self):
        result = parse_query("tags in [work,urgent]")
        self.assertEqual(result, {"tags": {"in": ["work", "urgent"]}})
    
    def test_multiple_conditions(self):
        result = parse_query("priority >= 7 status == active")
        expected = {
            "priority": {"gte": "7"},
            "status": {"eq": "active"}
        }
        self.assertEqual(result, expected)
    
    def test_field_aliases(self):
        aliases = {"prio": "priority"}
        result = parse_query("prio >= 5", field_aliases=aliases)
        self.assertEqual(result, {"priority": {"gte": "5"}})
    
    def test_field_multipliers(self):
        multipliers = {"size": 1024}
        result = parse_query("size >= 10", field_multipliers=multipliers)
        self.assertEqual(result, {"size": {"gte": 10240.0}})
    
    def test_invalid_operator_raises_error(self):
        with self.assertRaises(ValueError):
            parse_query("status ~= invalid")
    
    def test_wildcard_value(self):
        result = parse_query("status = any")
        self.assertEqual(result, {})
    
    def test_boolean_values(self):
        result = parse_query("completed == True")
        self.assertEqual(result, {"completed": {"eq": True}})
        
        result = parse_query("archived == False")
        self.assertEqual(result, {"archived": {"eq": False}})
    
    def test_none_values(self):
        result = parse_query("assigned_to == None")
        self.assertEqual(result, {"assigned_to": {"eq": None}})


class TestCache(unittest.TestCase):
    """Test the caching system"""
    
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(delete=False)
        self.temp_file.close()
        self.cache = Cache(self.temp_file.name, timedelta(seconds=1))
    
    def tearDown(self):
        try:
            os.unlink(self.temp_file.name)
        except:
            pass
    
    def test_cache_miss_when_empty(self):
        # Remove the empty file so cache.get() sees no file rather than invalid JSON
        os.unlink(self.temp_file.name)
        self.assertIsNone(self.cache.get())
    
    def test_cache_set_and_get(self):
        data = {"key": "value"}
        self.cache.set(data)
        self.assertEqual(self.cache.get(), data)
    
    def test_cache_expiration(self):
        import time
        data = {"key": "value"}
        self.cache.set(data)
        
        # Wait for cache to expire
        time.sleep(1.5)
        
        self.assertIsNone(self.cache.get())
    
    def test_cache_clear(self):
        data = {"key": "value"}
        self.cache.set(data)
        self.cache.clear()
        self.assertIsNone(self.cache.get())


class TestConfig(unittest.TestCase):
    """Test configuration management"""
    
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(delete=False, mode='w')
        self.temp_file.close()
        self.config = Config(self.temp_file.name)
    
    def tearDown(self):
        try:
            os.unlink(self.temp_file.name)
        except:
            pass
    
    def test_get_with_default(self):
        value = self.config.get("nonexistent", "default")
        self.assertEqual(value, "default")
    
    def test_set_and_get(self):
        self.config.set("key", "value")
        self.assertEqual(self.config.get("key"), "value")
    
    def test_save_and_load(self):
        self.config.set("key1", "value1")
        self.config.set("key2", 123)
        self.config.save()
        
        # Create new config instance with same file
        new_config = Config(self.temp_file.name)
        self.assertEqual(new_config.get("key1"), "value1")
        self.assertEqual(new_config.get("key2"), 123)
    
    def test_delete(self):
        self.config.set("key", "value")
        self.config.delete("key")
        self.assertIsNone(self.config.get("key"))


class TestSQLiteBackend(unittest.TestCase):
    """Test the SQLite backend"""
    
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.backend = SQLiteBackend(self.temp_db.name)
    
    def tearDown(self):
        try:
            os.unlink(self.temp_db.name)
        except:
            pass
    
    def test_create_task(self):
        task_data = {
            "title": "Test task",
            "priority": 5,
            "status": "pending"
        }
        result = self.backend.create_task(task_data)
        
        self.assertIsNotNone(result.get('id'))
        self.assertEqual(result['title'], "Test task")
        self.assertEqual(result['priority'], 5)
        self.assertIsNotNone(result.get('created'))
    
    def test_search_tasks(self):
        # Create some tasks
        self.backend.create_task({"title": "Task 1", "priority": 8})
        self.backend.create_task({"title": "Task 2", "priority": 3})
        self.backend.create_task({"title": "Task 3", "priority": 9})
        
        # Search for high priority tasks
        query = {"priority": {"gte": "8"}}
        results = self.backend.search_tasks(query)
        
        self.assertEqual(len(results), 2)
        priorities = [r['priority'] for r in results]
        self.assertTrue(all(p >= 8 for p in priorities))
    
    def test_update_task(self):
        # Create a task
        task = self.backend.create_task({"title": "Original", "priority": 5})
        task_id = task['id']
        
        # Update it
        updated = self.backend.update_task(task_id, {"status": "completed", "priority": 10})
        
        self.assertEqual(updated['status'], "completed")
        self.assertEqual(updated['priority'], 10)
        self.assertEqual(updated['title'], "Original")
    
    def test_delete_task(self):
        # Create a task
        task = self.backend.create_task({"title": "To delete"})
        task_id = task['id']
        
        # Delete it
        result = self.backend.delete_task(task_id)
        self.assertTrue(result)
        
        # Verify it's gone
        found = self.backend.get_task(task_id)
        self.assertIsNone(found)
    
    def test_get_task(self):
        # Create a task
        task = self.backend.create_task({"title": "Find me"})
        task_id = task['id']
        
        # Get it back
        found = self.backend.get_task(task_id)
        self.assertIsNotNone(found)
        self.assertEqual(found['title'], "Find me")
    
    def test_task_with_tags(self):
        # Create task with tags
        task = self.backend.create_task({
            "title": "Tagged task",
            "tags": ["work", "urgent"]
        })
        
        # Verify tags are stored and retrieved correctly
        found = self.backend.get_task(task['id'])
        self.assertEqual(found['tags'], ["work", "urgent"])


class TestConcurrentExecution(unittest.TestCase):
    """Test concurrent execution utilities"""
    
    def test_concurrent_execution(self):
        def square(x):
            return x * x
        
        items = [1, 2, 3, 4, 5]
        results = execute_concurrent(square, items, max_workers=2)
        
        expected = [1, 4, 9, 16, 25]
        self.assertEqual(sorted(results), sorted(expected))
    
    def test_concurrent_with_retry(self):
        call_counts = {}
        
        def flaky_function(x):
            # Fail twice, then succeed
            if x not in call_counts:
                call_counts[x] = 0
            call_counts[x] += 1
            
            if call_counts[x] < 3:
                raise ValueError("Simulated failure")
            return x * 2
        
        items = [1, 2, 3]
        results = execute_concurrent(flaky_function, items, max_workers=2, max_retries=5)
        
        expected = [2, 4, 6]
        self.assertEqual(sorted(results), sorted(expected))


class TestUtilityFunctions(unittest.TestCase):
    """Test utility functions"""
    
    def test_format_timestamp(self):
        timestamp = 1706572800.0  # 2024-01-30 00:00:00
        result = format_timestamp(timestamp)
        self.assertIn("2024", result)
        self.assertIn("01", result)
    
    def test_deindent(self):
        text = """
            Line 1
            Line 2
                Indented
        """
        result = deindent(text)
        self.assertFalse(result.startswith(" "))
        self.assertIn("Line 1", result)


if __name__ == '__main__':
    unittest.main()
