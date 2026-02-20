"""
Tests for CloudTask CLI

This demonstrates how the architecture is testable and production-ready.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
import json
import tempfile
import os

# Import modules to test
import sys
sys.path.insert(0, os.path.dirname(__file__))

from cloudtask import (
    parse_query,
    Cache,
    Config,
    APIClient,
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


class TestAPIClient(unittest.TestCase):
    """Test the API client with mocked requests"""
    
    @patch('cloudtask.requests.Session')
    def test_get_request(self, mock_session_class):
        # Setup mock
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "success"}
        mock_response.content = b'{"result": "success"}'
        mock_session.request.return_value = mock_response
        
        # Test
        client = APIClient("https://api.example.com", "test_key")
        result = client.get("/tasks")
        
        self.assertEqual(result, {"result": "success"})
        mock_session.request.assert_called_once()
    
    @patch('cloudtask.requests.Session')
    def test_retry_on_rate_limit(self, mock_session_class):
        # Setup mock
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        
        # First call returns 429, second succeeds
        mock_response_429 = Mock()
        mock_response_429.status_code = 429
        
        mock_response_200 = Mock()
        mock_response_200.status_code = 200
        mock_response_200.json.return_value = {"result": "success"}
        mock_response_200.content = b'{"result": "success"}'
        
        mock_session.request.side_effect = [mock_response_429, mock_response_200]
        
        # Test
        client = APIClient("https://api.example.com", "test_key")
        result = client.get("/tasks")
        
        self.assertEqual(result, {"result": "success"})
        self.assertEqual(mock_session.request.call_count, 2)
    
    @patch('cloudtask.requests.Session')
    def test_authentication_header(self, mock_session_class):
        # Setup mock
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_response.content = b'{}'
        mock_session.request.return_value = mock_response
        
        # Test
        client = APIClient("https://api.example.com", "secret_key_123")
        client.get("/tasks")
        
        # Verify Authorization header was set
        call_args = mock_session.request.call_args
        headers = call_args[1]['headers']
        self.assertEqual(headers['Authorization'], 'Bearer secret_key_123')


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
