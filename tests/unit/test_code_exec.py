"""Tests for Code Execution Tool."""

import pytest

from src.tools.code_exec import (
    _BLOCKED_PATTERNS,
    _check_code_safety,
    execute_python,
)


class TestCodeSafety:
    def test_safe_code(self):
        assert _check_code_safety("print('hello')") is None

    def test_safe_math(self):
        assert _check_code_safety("x = 1 + 2\nprint(x)") is None

    def test_blocked_os_import(self):
        result = _check_code_safety("import os\nos.listdir('/')")
        assert result is not None
        assert "import os" in result

    def test_blocked_subprocess(self):
        result = _check_code_safety("import subprocess\nsubprocess.run(['ls'])")
        assert result is not None

    def test_blocked_eval(self):
        result = _check_code_safety("eval('1+1')")
        assert result is not None

    def test_blocked_exec(self):
        result = _check_code_safety("exec('print(1)')")
        assert result is not None

    def test_blocked_open(self):
        result = _check_code_safety("open('/etc/passwd').read()")
        assert result is not None

    def test_blocked_dunder_import(self):
        result = _check_code_safety("__import__('os').system('ls')")
        assert result is not None

    def test_blocked_os_system(self):
        result = _check_code_safety("os.system('rm -rf /')")
        assert result is not None

    def test_safe_numpy(self):
        assert _check_code_safety("import numpy as np\nprint(np.array([1,2,3]))") is None

    def test_safe_json(self):
        assert _check_code_safety("import json\nprint(json.dumps({'a': 1}))") is None


class TestCodeExecution:
    @pytest.mark.asyncio
    async def test_simple_print(self):
        result = await execute_python("print('hello world')")
        assert result.success
        assert "hello world" in result.output

    @pytest.mark.asyncio
    async def test_math_calculation(self):
        result = await execute_python("print(2 ** 10)")
        assert result.success
        assert "1024" in result.output

    @pytest.mark.asyncio
    async def test_multiline_code(self):
        code = """
def fibonacci(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

print(fibonacci(10))
"""
        result = await execute_python(code)
        assert result.success
        assert "55" in result.output

    @pytest.mark.asyncio
    async def test_syntax_error(self):
        result = await execute_python("print('hello")
        assert not result.success
        assert result.error  # Should have error message

    @pytest.mark.asyncio
    async def test_runtime_error(self):
        result = await execute_python("print(1/0)")
        assert not result.success
        assert "ZeroDivision" in result.error

    @pytest.mark.asyncio
    async def test_blocked_code_rejected(self):
        result = await execute_python("import os\nos.listdir('/')")
        assert not result.success
        assert "blocked" in result.error.lower() or "từ chối" in result.error.lower()

    @pytest.mark.asyncio
    async def test_no_output(self):
        result = await execute_python("x = 42")
        assert result.success
        assert result.output == "(no output)"

    @pytest.mark.asyncio
    async def test_list_comprehension(self):
        result = await execute_python("print([x**2 for x in range(5)])")
        assert result.success
        assert "[0, 1, 4, 9, 16]" in result.output
