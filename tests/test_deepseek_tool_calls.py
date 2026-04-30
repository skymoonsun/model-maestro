"""Tests for DeepSeek XML tool call parsing."""

import json
import pytest
from app.proxy import (
    parse_deepseek_tool_calls,
    _normalize_dsml_tags,
    _parse_xml_parameter_value,
    _parse_xml_text_value,
)


class TestNormalizeDSMLTags:
    """Test DSML tag normalization."""

    def test_normalize_dsml_open_tags(self):
        text = '<|DSML|tool_calls><|DSML|invoke name="Bash"><|DSML|parameter name="cmd">ls</|DSML|parameter></|DSML|invoke></|DSML|tool_calls>'
        result = _normalize_dsml_tags(text)
        assert '<tool_calls>' in result
        assert '<invoke name="Bash">' in result
        assert '<parameter name="cmd">ls</parameter>' in result
        assert '</invoke>' in result
        assert '</tool_calls>' in result

    def test_normalize_dsml_close_tags(self):
        text = '</|DSML|tool_calls>'
        result = _normalize_dsml_tags(text)
        assert result == '</tool_calls>'

    def test_mixed_tags_unchanged(self):
        text = '<tool_calls><invoke name="Bash"></invoke></tool_calls>'
        result = _normalize_dsml_tags(text)
        assert result == text


class TestParseXMLTextValue:
    """Test text value parsing."""

    def test_none_returns_empty_string(self):
        assert _parse_xml_text_value(None) == ""

    def test_empty_string(self):
        assert _parse_xml_text_value("") == ""

    def test_string_value(self):
        assert _parse_xml_text_value("hello") == "hello"

    def test_integer_value(self):
        assert _parse_xml_text_value("42") == 42

    def test_float_value(self):
        assert _parse_xml_text_value("3.14") == 3.14

    def test_boolean_true(self):
        assert _parse_xml_text_value("true") is True

    def test_boolean_false(self):
        assert _parse_xml_text_value("false") is False

    def test_null_value(self):
        assert _parse_xml_text_value("null") is None

    def test_json_array(self):
        result = _parse_xml_text_value('[1, 2, 3]')
        assert result == [1, 2, 3]

    def test_json_object(self):
        result = _parse_xml_text_value('{"key": "val"}')
        assert result == {"key": "val"}

    def test_plain_text_not_json(self):
        assert _parse_xml_text_value("find / -type f") == "find / -type f"

    def test_whitespace_stripped(self):
        assert _parse_xml_text_value("  hello  ") == "hello"


class TestParseDeepseekToolCalls:
    """Test DeepSeek XML tool call parsing."""

    def test_empty_content(self):
        clean, calls, has = parse_deepseek_tool_calls("")
        assert clean == ""
        assert calls == []
        assert has is False

    def test_none_content(self):
        clean, calls, has = parse_deepseek_tool_calls(None)
        assert clean is None
        assert calls == []
        assert has is False

    def test_no_tool_calls(self):
        content = "Hello, this is a regular response without any tool calls."
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert clean == content
        assert calls == []
        assert has is False

    def test_simple_single_tool_call(self):
        content = '<tool_calls>\n<invoke name="Bash">\n<parameter name="command">find /Users -type f</parameter>\n<parameter name="description">List files</parameter>\n</invoke>\n</tool_calls>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        assert len(calls) == 1
        assert calls[0]['function']['name'] == 'Bash'
        args = json.loads(calls[0]['function']['arguments'])
        assert args['command'] == 'find /Users -type f'
        assert args['description'] == 'List files'

    def test_multiple_invoke_blocks(self):
        content = '<tool_calls>\n<invoke name="Read">\n<parameter name="file_path">/etc/hosts</parameter>\n</invoke>\n<invoke name="Bash">\n<parameter name="command">ls -la</parameter>\n</invoke>\n</tool_calls>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        assert len(calls) == 2
        assert calls[0]['function']['name'] == 'Read'
        assert calls[1]['function']['name'] == 'Bash'
        args0 = json.loads(calls[0]['function']['arguments'])
        assert args0['file_path'] == '/etc/hosts'

    def test_content_before_tool_calls(self):
        content = 'I will examine the project for you.\n\n<tool_calls>\n<invoke name="Bash">\n<parameter name="command">find . -type f</parameter>\n</invoke>\n</tool_calls>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        assert len(calls) == 1
        assert 'examine the project' in clean

    def test_content_after_tool_calls(self):
        content = '<tool_calls>\n<invoke name="Bash">\n<parameter name="command">ls</parameter>\n</invoke>\n</tool_calls>\n\nHere are the results.'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        assert 'Here are the results' in clean

    def test_cdata_in_parameter(self):
        content = '<tool_calls>\n<invoke name="Bash">\n<parameter name="command"><![CDATA[find / -name "*.py" | xargs grep "import"]]]></parameter>\n</invoke>\n</tool_calls>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        args = json.loads(calls[0]['function']['arguments'])
        # CDATA content should be extracted
        assert 'find' in args['command']

    def test_incomplete_xml_no_closing_tag(self):
        content = '<tool_calls>\n<invoke name="Bash">\n<parameter name="command">ls</parameter>\n</invoke>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        # No closing </tool_calls> tag — should return as-is
        assert has is False

    def test_nested_xml_object_parameter(self):
        content = '<tool_calls>\n<invoke name="Edit">\n<parameter name="file_path"><path>/tmp/test.py</path><line>42</line></parameter>\n</invoke>\n</tool_calls>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        args = json.loads(calls[0]['function']['arguments'])
        assert isinstance(args['file_path'], dict)

    def test_tool_call_id_format(self):
        content = '<tool_calls>\n<invoke name="Bash">\n<parameter name="command">ls</parameter>\n</invoke>\n</tool_calls>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert calls[0]['id'].startswith('call_')
        assert calls[0]['type'] == 'function'
        assert calls[0]['index'] == 0

    def test_sequential_indices(self):
        content = '<tool_calls>\n<invoke name="Bash">\n<parameter name="command">ls</parameter>\n</invoke>\n<invoke name="Read">\n<parameter name="file_path">/tmp/x</parameter>\n</invoke>\n</tool_calls>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert calls[0]['index'] == 0
        assert calls[1]['index'] == 1

    def test_empty_parameter_value(self):
        content = '<tool_calls>\n<invoke name="Bash">\n<parameter name="command"></parameter>\n</invoke>\n</tool_calls>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        args = json.loads(calls[0]['function']['arguments'])
        assert args['command'] == ""

    def test_integer_parameter_value(self):
        content = '<tool_calls>\n<invoke name="Read">\n<parameter name="offset">42</parameter>\n<parameter name="file_path">/tmp/x</parameter>\n</invoke>\n</tool_calls>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        args = json.loads(calls[0]['function']['arguments'])
        assert args['offset'] == 42

    def test_dsml_format(self):
        content = '<|DSML|tool_calls>\n<|DSML|invoke name="Bash">\n<|DSML|parameter name="command"><![CDATA[ls -la]]></|DSML|parameter>\n</|DSML|invoke>\n</|DSML|tool_calls>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        assert calls[0]['function']['name'] == 'Bash'

    def test_code_fence_not_parsed(self):
        """Tool calls inside markdown code fences should NOT be parsed."""
        content = 'Here is an example:\n```xml\n<tool_calls>\n<invoke name="Bash">\n<parameter name="command">ls</parameter>\n</invoke>\n</tool_calls>\n```\nDone.'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is False

    def test_real_deepseek_cursor_output(self):
        """Test with actual DeepSeek output format from the logs."""
        content = 'Projeyi detaylıca inceleyeceğim.\n\n<tool_calls>\n<invoke name="Bash">\n<parameter name="command">find /Users/gokaygunes/Projects/model-maestro -type f -name "*.py" | head -20</parameter>\n<parameter name="description">List Python files in project</parameter>\n</invoke>\n</tool_calls>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        assert len(calls) == 1
        assert calls[0]['function']['name'] == 'Bash'
        args = json.loads(calls[0]['function']['arguments'])
        assert 'find' in args['command']
        assert 'List Python files' in args['description']
        assert 'Projeyi' in clean

    def test_tilde_code_fence_not_parsed(self):
        content = '~~~\n<tool_calls>\n<invoke name="Bash">\n<parameter name="command">rm -rf /</parameter>\n</invoke>\n</tool_calls>\n~~~'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is False

    def test_partial_tag_prefix_not_parsed(self):
        """Content ending with partial <tool_c should not crash."""
        content = "I'm thinking about using <tool_c"
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is False
        assert clean == content


class TestCallMcpToolFormat:
    """Test <CallMcpTool> XML format parsing (DeepSeek-v4-pro MCP-style)."""

    def test_simple_mcp_tool_call(self):
        content = 'I will examine the project.\n\n<CallMcpTool>\n  <serverName>filesystem</serverName>\n  <toolName>list_directory</toolName>\n  <arguments>{"path": "/Users/test/project", "maxDepth": 3}</arguments>\n</CallMcpTool>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        assert len(calls) == 1
        assert calls[0]['function']['name'] == 'filesystem_list_directory'
        args = json.loads(calls[0]['function']['arguments'])
        assert args['path'] == '/Users/test/project'
        assert args['maxDepth'] == 3
        assert 'examine the project' in clean

    def test_mcp_tool_call_no_server_name(self):
        content = '<CallMcpTool>\n  <serverName></serverName>\n  <toolName>read_file</toolName>\n  <arguments>{"path": "/tmp/test.py"}</arguments>\n</CallMcpTool>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        assert calls[0]['function']['name'] == 'read_file'
        args = json.loads(calls[0]['function']['arguments'])
        assert args['path'] == '/tmp/test.py'

    def test_mcp_tool_call_plain_text_args(self):
        content = '<CallMcpTool>\n  <serverName>shell</serverName>\n  <toolName>run_command</toolName>\n  <arguments>command="ls -la" description="List files"</arguments>\n</CallMcpTool>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        assert calls[0]['function']['name'] == 'shell_run_command'

    def test_mcp_tool_call_content_before_and_after(self):
        content = 'Let me check the files.\n\n<CallMcpTool>\n  <serverName>filesystem</serverName>\n  <toolName>list_directory</toolName>\n  <arguments>{"path": "/root"}</arguments>\n</CallMcpTool>\n\nHere are the results.'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        assert 'check the files' in clean
        assert 'Here are the results' in clean
        assert len(calls) == 1

    def test_mcp_tool_call_code_fence_not_parsed(self):
        content = '```xml\n<CallMcpTool>\n  <serverName>filesystem</serverName>\n  <toolName>list_directory</toolName>\n  <arguments>{"path": "/root"}</arguments>\n</CallMcpTool>\n```'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is False

    def test_mcp_tool_call_partial_not_parsed(self):
        content = 'I will use <CallMc'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is False
        assert clean == content

    def test_real_deepseek_v4_pro_output(self):
        """Test with actual DeepSeek-v4-pro output from logs."""
        content = 'Projeyi detaylıca inceleyeceğim.\n\n<CallMcpTool>\n  <serverName>filesystem</serverName>\n  <toolName>list_directory</toolName>\n  <arguments>{"path": "/Users/gokaygunes/Projects/model-maestro", "maxDepth": 3}</arguments>\n</CallMcpTool>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        assert len(calls) == 1
        assert calls[0]['function']['name'] == 'filesystem_list_directory'
        args = json.loads(calls[0]['function']['arguments'])
        assert args['path'] == '/Users/gokaygunes/Projects/model-maestro'
        assert 'Projeyi' in clean

class TestToolCallSingularFormat:
    """Test <tool_call name="..."></tool_call> singular format (DeepSeek-v4-pro)."""

    def test_simple_named_tool_call(self):
        content = 'Let me read the file.\n\n<tool_call name="Read">\n<parameter>/Users/test/project/main.py</parameter>\n<parameter>100</parameter>\n</tool_call>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        assert len(calls) == 1
        assert calls[0]['function']['name'] == 'Read'
        assert 'Let me read' in clean

    def test_named_tool_call_with_named_elements(self):
        content = '<tool_call name="Read">\n<path>filePath</path>\n<parameter>/Users/gokaygunes/Projects/model-maestro</parameter>\n<parameter>limit</parameter>\n<parameter>100</parameter>\n</tool_call>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        assert calls[0]['function']['name'] == 'Read'
        args = json.loads(calls[0]['function']['arguments'])
        assert 'path' in args
        assert args['path'] == 'filePath'

    def test_named_tool_call_content_before(self):
        content = 'Projeyi inceleyecegim.\n\n<tool_call name="Bash">\n<parameter>ls -la</parameter>\n</tool_call>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        assert 'Projeyi' in clean
        assert calls[0]['function']['name'] == 'Bash'

    def test_named_tool_call_code_fence_not_parsed(self):
        content = '```xml\n<tool_call name="Bash">\n<parameter>rm -rf /</parameter>\n</tool_call>\n```'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is False

    def test_real_deepseek_v4_pro_singular_output(self):
        """Test with actual DeepSeek-v4-pro singular output from logs."""
        content = 'Projeyi kapsamli bir sekilde incelemeye basliyorum.\n\n<tool_call name="Read"><path>filePath</path>\n<parameter>/Users/gokaygunes/Projects/model-maestro</parameter>\n<parameter>limit</parameter>\n<parameter>100</parameter>\n</tool_call>'
        clean, calls, has = parse_deepseek_tool_calls(content)
        assert has is True
        assert calls[0]['function']['name'] == 'Read'
        assert 'Projeyi' in clean
