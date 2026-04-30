"""Proxy logic and model routing for Model Maestro"""

from typing import Dict, Any, Optional, List, Tuple
import httpx
import json
import logging
import re
import time
import uuid
import xml.etree.ElementTree as ET
from fastapi import HTTPException, Depends
from fastapi.responses import StreamingResponse

from app.config import get_settings, model_mapper, model_group_manager
from app.user_manager import user_manager
from app.auth import get_current_user

logger = logging.getLogger(__name__)

# Maximum retries for failover (will try all fallback members in group)
MAX_FAILOVER_RETRIES = 5


# ============================================================================
# TOOL CALL VALIDATION (LiteLLM-inspired)
# ============================================================================
def _is_tool_call_valid(tool_call: Dict[str, Any]) -> bool:
    """
    Check if a tool call has valid and complete arguments.

    This is critical for Cursor compatibility - incomplete or invalid tool calls
    should not be buffered/yielded as they cause Cursor to hang.

    Args:
        tool_call: Tool call object with function.name and function.arguments

    Returns:
        True if tool call is complete and valid, False otherwise
    """
    if not isinstance(tool_call, dict):
        return False

    func = tool_call.get('function', {})
    if not isinstance(func, dict):
        return False

    args = func.get('arguments', '')

    # Empty arguments is valid (no-arg function)
    if not args:
        return True

    # Check if arguments is valid JSON
    try:
        json.loads(args)
        return True
    except (json.JSONDecodeError, TypeError):
        return False


# ============================================================================
# TOOL CALL ARGUMENT SANITIZATION (Cursor compatibility)
# ============================================================================
# LLMs (e.g. qwen3-coder) sometimes generate invalid tool arguments that cause
# "Unexpected non-whitespace character after JSON" or tool execution failures.
# Example: Read(path="x", offset=-100) - Cursor expects offset as positive line number.
# ============================================================================
def _sanitize_tool_call_arguments(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize tool call arguments for Cursor IDE compatibility.
    Fixes common model mistakes (e.g. offset:-100 for "last N lines") that cause errors.

    Args:
        tool_call: Tool call dict with function.name and function.arguments

    Returns:
        Tool call with sanitized arguments (modified in-place, also returned)
    """
    func = tool_call.get('function', {})
    if not isinstance(func, dict):
        return tool_call

    args_str = func.get('arguments', '')
    if not args_str:
        return tool_call

    try:
        args = json.loads(args_str)
    except (json.JSONDecodeError, TypeError):
        return tool_call

    if not isinstance(args, dict):
        return tool_call

    changed = False
    tool_name = func.get('name', '')

    # Read tool: offset must be positive integer (line number, 1-based)
    # Models often use offset:-100 thinking "last 100 lines" - Cursor doesn't support that
    if tool_name == 'Read':
        if 'offset' in args:
            offset_val = args['offset']
            if isinstance(offset_val, (int, float)):
                if offset_val < 1:
                    args['offset'] = 1
                    changed = True
                    logger.info(f"[SANITIZE] Read tool: offset {offset_val} -> 1 (Cursor requires positive line number)")
            else:
                # Non-numeric offset, remove it
                del args['offset']
                changed = True
        if 'limit' in args:
            limit_val = args['limit']
            if isinstance(limit_val, (int, float)):
                if limit_val < 1:
                    args['limit'] = min(500, max(100, abs(int(limit_val))))  # e.g. -100 -> 100
                    changed = True
                    logger.info(f"[SANITIZE] Read tool: limit {limit_val} -> {args['limit']}")
            else:
                del args['limit']
                changed = True

    if changed:
        func['arguments'] = json.dumps(args, ensure_ascii=False)

    return tool_call


# ============================================================================
# KIMI TOOL CALL CONVERTER
# ============================================================================
# Kimi models use a custom tool call format that needs to be converted
# to OpenAI's standard tool_calls format for Cursor IDE compatibility.
#
# Kimi format:
#   <|tool_calls_section_begin|>
#   <|tool_call_begin|>functions.FunctionName:index<|tool_call_argument_begin|>
#   {"arg1": "value1", ...}
#   <|tool_call_end|>
#   <|tool_calls_section_end|>
#
# OpenAI format (in delta):
#   {"tool_calls": [{"index": 0, "id": "call_xxx", "type": "function", 
#     "function": {"name": "FunctionName", "arguments": "{...}"}}]}
# ============================================================================

# Regex patterns for Kimi tool call format
KIMI_TOOL_CALL_SECTION_START = r'<\|tool_calls_section_begin\|>'
KIMI_TOOL_CALL_SECTION_END = r'<\|tool_calls_section_end\|>'

# More flexible pattern that handles nested JSON objects
# Uses lazy matching to find content between markers
KIMI_TOOL_CALL_PATTERN = re.compile(
    r'<\|tool_call_begin\|>\s*'
    r'(?:functions\.)?(\w+)(?::\d+)?\s*'
    r'<\|tool_call_argument_begin\|>\s*'
    r'(\{.*?\})\s*'  # Lazy match for JSON - handles nested objects
    r'<\|tool_call_end\|>',
    re.DOTALL
)


def parse_kimi_tool_calls(content: str) -> Tuple[str, List[Dict[str, Any]], bool]:
    """
    Parse Kimi tool call format from content and convert to OpenAI format.
    
    Args:
        content: The content string that may contain Kimi tool calls
        
    Returns:
        Tuple of:
        - clean_content: Content with tool call markers removed
        - tool_calls: List of OpenAI-formatted tool call objects
        - has_tool_calls: Whether any tool calls were found
    """
    if not content:
        return content, [], False
    
    # Check if content contains Kimi tool call markers
    section_start = '<|tool_calls_section_begin|>'
    section_end = '<|tool_calls_section_end|>'
    
    if section_start not in content:
        return content, [], False
    
    tool_calls = []
    tool_call_index = 0
    
    # Find the section boundaries
    start_idx = content.find(section_start)
    end_idx = content.find(section_end)
    
    if start_idx == -1:
        return content, [], False
    
    # Extract content before and after the tool call section
    content_before = content[:start_idx].strip()
    content_after = content[end_idx + len(section_end):].strip() if end_idx != -1 else ""
    
    # Extract the tool call section
    if end_idx != -1:
        section_content = content[start_idx + len(section_start):end_idx]
    else:
        section_content = content[start_idx + len(section_start):]
    
    # Parse individual tool calls from the section
    tool_call_begin = '<|tool_call_begin|>'
    tool_call_end = '<|tool_call_end|>'
    arg_begin = '<|tool_call_argument_begin|>'
    
    current_pos = 0
    while True:
        # Find next tool call
        call_start = section_content.find(tool_call_begin, current_pos)
        if call_start == -1:
            break
        
        call_end = section_content.find(tool_call_end, call_start)
        if call_end == -1:
            break
        
        # Extract the tool call content
        call_content = section_content[call_start + len(tool_call_begin):call_end].strip()
        
        # Find the argument marker
        arg_start = call_content.find(arg_begin)
        if arg_start == -1:
            current_pos = call_end + len(tool_call_end)
            continue
        
        # Extract function name (everything before arg_begin)
        func_part = call_content[:arg_start].strip()
        # Remove "functions." prefix if present
        if func_part.startswith('functions.'):
            func_part = func_part[10:]
        # Remove trailing index like ":11"
        if ':' in func_part:
            func_part = func_part.split(':')[0]
        function_name = func_part.strip()
        
        # Extract arguments JSON (everything after arg_begin)
        args_str = call_content[arg_start + len(arg_begin):].strip()
        
        # Parse JSON arguments
        try:
            arguments = json.loads(args_str)
            arguments_str = json.dumps(arguments, ensure_ascii=False)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse Kimi tool call arguments: {args_str[:100]}... Error: {e}")
            current_pos = call_end + len(tool_call_end)
            continue
        
        tool_call = {
            "index": tool_call_index,
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {
                "name": function_name,
                "arguments": arguments_str
            }
        }
        _sanitize_tool_call_arguments(tool_call)
        tool_calls.append(tool_call)
        tool_call_index += 1
        logger.info(f"[KIMI] Parsed tool call: {function_name}({arguments_str[:50]}...)")
        
        current_pos = call_end + len(tool_call_end)
    
    # Combine clean content
    clean_content = f"{content_before} {content_after}".strip()
    
    return clean_content, tool_calls, len(tool_calls) > 0


def convert_kimi_content_to_openai_delta(content: str, model: str) -> List[Dict[str, Any]]:
    """
    Convert Kimi content with tool calls to OpenAI delta format chunks.
    
    Args:
        content: Content that may contain Kimi tool calls
        model: Model name for the response
        
    Returns:
        List of OpenAI-formatted delta chunks to send
    """
    clean_content, tool_calls, has_tool_calls = parse_kimi_tool_calls(content)
    
    chunks = []
    
    # If there's clean content before/after tool calls, send it as regular content
    if clean_content:
        chunks.append({
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"content": clean_content},
                "finish_reason": None
            }]
        })
    
    # Send tool calls if present
    if has_tool_calls:
        # First chunk: tool call with function name and arguments
        chunks.append({
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"tool_calls": tool_calls},
                "finish_reason": None
            }]
        })
        
        # Final chunk: finish_reason = tool_calls
        chunks.append({
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "tool_calls"
            }]
        })
    
    return chunks


# ============================================================================
# DEEPSEEK TOOL CALL CONVERTER
# ============================================================================
# DeepSeek models use canonical XML format for tool calls that needs to be
# converted to OpenAI's standard tool_calls format for Cursor IDE compatibility.
#
# DeepSeek format:
#   <tool_calls>
#   <invoke name="FunctionName">
#   <parameter name="arg1">value1</parameter>
#   <parameter name="arg2">value2</parameter>
#   </invoke>
#   </tool_calls>
#
# Also supports DSML-prefixed variants:
#   <|DSML|tool_calls>
#   <|DSML|invoke name="FunctionName">
#   <|DSML|parameter name="arg1">value1</|DSML|parameter>
#   </|DSML|tool_calls>
#
# OpenAI format (in delta):
#   {"tool_calls": [{"index": 0, "id": "call_xxx", "type": "function",
#     "function": {"name": "FunctionName", "arguments": "{...}"}}]}
# ============================================================================

# Regex to strip markdown code fences (```...``` or ~~~...~~~)
_CODE_FENCE_RE = re.compile(r'(```[\s\S]*?```|~~~[\s\S]*?~~~)', re.MULTILINE)

# DSML tag normalization regexes
_DSML_OPEN_RE = re.compile(r'<\|DSML\|(\w+)([^>]*)>')
_DSML_CLOSE_RE = re.compile(r'</\|DSML\|(\w+)>')
_CANONICAL_OPEN_RE = re.compile(r'<(tool_calls|invoke|parameter)([^>]*)>')
_CANONICAL_CLOSE_RE = re.compile(r'</(tool_calls|invoke|parameter)>')

# Marker for suspicion buffering — partial tag prefixes
_DEEPSEEK_TAG_PREFIXES = ['<tool_c', '<tool_ca', '<tool_cal', '<tool_call', '<tool_calls',
                           '<|DSML', '<|DSML|', '<|DSML|t', '<|DSML|to', '<|DSML|too',
                           '<|DSML|tool', '<|DSML|tool_',
                           '<Call', '<CallM', '<CallMc', '<CallMcp', '<CallMcpT', '<CallMcpTo', '<CallMcpToo']


def _normalize_dsml_tags(text: str) -> str:
    """Convert DSML-prefixed tags to canonical XML form for unified parsing.

    <|DSML|tool_calls> → <tool_calls>
    </|DSML|tool_calls> → </tool_calls>
    <|DSML|invoke name="..."> → <invoke name="...">
    <|DSML|parameter name="..."> → <parameter name="...">
    """
    text = _DSML_OPEN_RE.sub(r'<\1\2>', text)
    text = _DSML_CLOSE_RE.sub(r'</\1>', text)
    return text


def _parse_xml_parameter_value(element: ET.Element) -> Any:
    """Parse a <parameter> element's value, handling CDATA, nested XML, and JSON literals.

    Handles:
    - CDATA sections: element.text returns inner content
    - Nested XML objects: <field>value</field> → {"field": "value"}
    - Arrays: <item>v1</item><item>v2</item> → ["v1", "v2"]
    - JSON literals: numbers, booleans, null
    - Plain text strings
    """
    # Check for child elements
    children = list(element)
    if children:
        # Has child elements — parse as structured data
        # Check if all children are <item> (array pattern)
        if all(c.tag == 'item' for c in children):
            return [_parse_xml_text_value(c.text) for c in children]
        # Otherwise parse as object
        result = {}
        for child in children:
            child_value = _parse_xml_parameter_value(child)
            key = child.tag
            if key in result:
                # Convert to array if duplicate keys
                existing = result[key]
                if isinstance(existing, list):
                    existing.append(child_value)
                else:
                    result[key] = [existing, child_value]
            else:
                result[key] = child_value
        return result

    # No children — parse text content
    raw_text = element.text
    # ElementTree may leave CDATA closing marker remnants
    if raw_text and raw_text.endswith(']]>'):
        raw_text = raw_text[:-3]
    return _parse_xml_text_value(raw_text)


def _parse_xml_text_value(text: Optional[str]) -> Any:
    """Parse text content as JSON literal if possible, otherwise return as string."""
    if text is None:
        return ""

    text = text.strip()

    if not text:
        return ""

    # Try JSON parse (primitives, arrays, objects)
    try:
        val = json.loads(text)
        return val
    except (json.JSONDecodeError, ValueError):
        pass

    return text


def parse_deepseek_tool_calls(content: str) -> Tuple[str, List[Dict[str, Any]], bool]:
    """Parse DeepSeek XML tool call format from content and convert to OpenAI format.

    Handles both canonical XML (<tool_calls><invoke><parameter>) and DSML-prefixed
    (<|DSML|tool_calls><|DSML|invoke>) formats.

    Code blocks (```...``` or ~~~...~~~) are stripped before parsing to prevent
    false positives from XML examples inside markdown.

    Args:
        content: The content string that may contain DeepSeek tool calls

    Returns:
        Tuple of:
        - clean_content: Content with tool call XML removed
        - tool_calls: List of OpenAI-formatted tool call objects
        - has_tool_calls: Whether any tool calls were found
    """
    if not content:
        return content, [], False

    # Quick check — if no tool_calls marker at all, skip
    if '<tool_calls>' not in content and '</tool_calls>' not in content \
            and '<|DSML|tool_calls>' not in content \
            and '<CallMcpTool>' not in content and '</CallMcpTool>' not in content:
        # Also check partial matches at end (streaming suspicion)
        has_prefix = any(content.rstrip().endswith(p) for p in _DEEPSEEK_TAG_PREFIXES)
        if not has_prefix:
            return content, [], False

    # Strip markdown code fences to avoid parsing XML examples
    stripped = _CODE_FENCE_RE.sub('', content)

    # Normalize DSML tags to canonical XML
    normalized = _normalize_dsml_tags(stripped)

    # Determine wrapper format: <tool_calls> or <CallMcpTool>
    tc_open = '<tool_calls>'
    tc_close = '</tool_calls>'
    mcp_open = '<CallMcpTool>'
    mcp_close = '</CallMcpTool>'

    has_tc = tc_open in normalized and tc_close in normalized
    has_mcp = mcp_open in normalized and mcp_close in normalized

    if not has_tc and not has_mcp:
        return content, [], False

    # Prefer <tool_calls> wrapper if both exist
    if has_tc:
        start_idx = normalized.find(tc_open)
        end_idx = normalized.rfind(tc_close)
        content_before = normalized[:start_idx].strip()
        content_after = normalized[end_idx + len(tc_close):].strip()
        section_content = normalized[start_idx + len(tc_open):end_idx]

        # Remove nested <tool_calls> wrappers (DeepSeek sometimes wraps twice)
        while tc_open in section_content and tc_close in section_content:
            inner_start = section_content.find(tc_open)
            inner_end = section_content.rfind(tc_close)
            if inner_start != -1 and inner_end != -1:
                section_content = (section_content[:inner_start] +
                                  section_content[inner_start + len(tc_open):inner_end] +
                                  section_content[inner_end + len(tc_close):])
    else:
        # <CallMcpTool> format — extract each block individually
        start_idx = normalized.find(mcp_open)
        end_idx = normalized.rfind(mcp_close)
        content_before = normalized[:start_idx].strip()
        content_after = normalized[end_idx + len(mcp_close):].strip()
        section_content = normalized[start_idx:end_idx + len(mcp_close)]

    # Parse tool call blocks - supports formats:
    # 1. <invoke name="..."><parameter name="...">val</parameter></invoke> (canonical XML)
    # 2. Ollama native format (plain text with function name + args)
    # 3. Plain text between tags
    tool_calls = []
    tool_call_index = 0

    # Try <invoke> format first (canonical XML with name attributes)
    invoke_pattern = re.compile(
        r'<invoke\s+name=["\x27]([^"\x27]+)["\x27]>(.*?)</invoke>',
        re.DOTALL
    )
    invoke_matches = list(invoke_pattern.finditer(section_content))

    if invoke_matches:
        for match in invoke_matches:
            func_name = match.group(1)
            invoke_body = match.group(2)

            # Parse <parameter> children
            arguments = {}
            param_pattern = re.compile(
                r'<parameter\s+name=["\x27]([^"\x27]+)["\x27]>(.*?)(?:</parameter>)',
                re.DOTALL
            )
            for param_match in param_pattern.finditer(invoke_body):
                param_name = param_match.group(1)
                param_value = param_match.group(2).strip()
                # Remove CDATA wrapper if present
                if param_value.startswith('<![CDATA[') and param_value.endswith(']]>'):
                    param_value = param_value[9:-3]
                # Try JSON parse
                try:
                    param_value = json.loads(param_value)
                except (json.JSONDecodeError, ValueError):
                    # Try ElementTree for nested XML structures
                    if '<' in param_value and '>' in param_value:
                        try:
                            elem = ET.fromstring(f'<param>{param_value}</param>')
                            param_value = _parse_xml_parameter_value(elem)
                        except ET.ParseError:
                            pass
                arguments[param_name] = param_value

            arguments_str = json.dumps(arguments, ensure_ascii=False)
            tool_call = {
                "index": tool_call_index,
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": func_name,
                    "arguments": arguments_str
                }
            }
            _sanitize_tool_call_arguments(tool_call)
            tool_calls.append(tool_call)
            tool_call_index += 1
            logger.info(f"[DEEPSEEK] Parsed invoke tool call: {func_name}({arguments_str[:80]}...)")

    if not tool_calls:
        # Try Ollama native tool_call tags
        # Content can be: "FunctionName\n{json_args}" or plain text
        tool_call_pattern = re.compile(
            r'<tool_call\s*>(.*?)</tool_call\s *>',
            re.DOTALL
        )
        tc_matches = list(tool_call_pattern.finditer(section_content))

        if not tc_matches:
            # Broader pattern: everything between tags with possible attrs
            tool_call_pattern2 = re.compile(
                r'<tool_call[^>]*>(.*?)</tool_call\s *>',
                re.DOTALL
            )
            tc_matches = list(tool_call_pattern2.finditer(section_content))

        for tc_match in tc_matches:
            tc_content = tc_match.group(1).strip()

            # Try to split into name + JSON arguments
            # Format 1: "FunctionName\n{...}" (name on first line, JSON args below)
            # Format 2: "FunctionName(args)" (plain text)
            # Format 3: Just text like "ToolRun Command \"...\""
            func_name = ""
            arguments = {}

            newline_idx = tc_content.find('\n')
            if newline_idx != -1:
                func_name = tc_content[:newline_idx].strip()
                args_str = tc_content[newline_idx + 1:].strip()

                # Try JSON parse
                try:
                    arguments = json.loads(args_str)
                except (json.JSONDecodeError, ValueError):
                    # Not JSON - try plain text arg parsing
                    arguments = _parse_plain_text_args(args_str)
            else:
                # No newline - try to parse whole content
                parts = tc_content.split(None, 1)
                if parts:
                    func_name = parts[0].strip('"\'')

                    # If there is remaining text, try to parse as arguments
                    if len(parts) > 1:
                        args_str = parts[1].strip()
                        try:
                            arguments = json.loads(args_str)
                        except (json.JSONDecodeError, ValueError):
                            arguments = _parse_plain_text_args(args_str)

            if func_name:
                arguments_str = json.dumps(arguments, ensure_ascii=False)
                tool_call = {
                    "index": tool_call_index,
                    "id": f"call_{uuid.uuid4().hex[:24]}",
                    "type": "function",
                    "function": {
                        "name": func_name,
                        "arguments": arguments_str
                    }
                }
                _sanitize_tool_call_arguments(tool_call)
                tool_calls.append(tool_call)
                tool_call_index += 1
                logger.info(f"[DEEPSEEK] Parsed tool_call: {func_name}({arguments_str[:80]}...)")

    if not tool_calls:
        # Last resort: try ElementTree for well-formed XML
        xml_text = f'<root>{section_content}</root>'
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return content, [], False

        for invoke_elem in root.findall('invoke'):
            func_name = invoke_elem.get('name', '')
            if not func_name:
                continue
            arguments = {}
            for param_elem in invoke_elem.findall('parameter'):
                param_name = param_elem.get('name', '')
                if not param_name:
                    continue
                param_value = _parse_xml_parameter_value(param_elem)
                arguments[param_name] = param_value

            arguments_str = json.dumps(arguments, ensure_ascii=False)
            tool_call = {
                "index": tool_call_index,
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": func_name,
                    "arguments": arguments_str
                }
            }
            _sanitize_tool_call_arguments(tool_call)
            tool_calls.append(tool_call)
            tool_call_index += 1
            logger.info(f"[DEEPSEEK] Parsed XML tool call: {func_name}({arguments_str[:80]}...)")

    # Try <CallMcpTool> format (DeepSeek-v4-pro MCP-style tool calls)
    if not tool_calls:
        mcp_pattern = re.compile(
            r'<CallMcpTool>(.*?)</CallMcpTool>',
            re.DOTALL
        )
        for mcp_match in mcp_pattern.finditer(section_content):
            mcp_body = mcp_match.group(1).strip()

            server_name = ""
            tool_name = ""
            arguments_raw = ""

            # Extract <serverName>
            sn_match = re.search(r'<serverName>(.*?)</serverName>', mcp_body, re.DOTALL)
            if sn_match:
                server_name = sn_match.group(1).strip()

            # Extract <toolName>
            tn_match = re.search(r'<toolName>(.*?)</toolName>', mcp_body, re.DOTALL)
            if tn_match:
                tool_name = tn_match.group(1).strip()

            # Extract <arguments>
            args_match = re.search(r'<arguments>(.*?)</arguments>', mcp_body, re.DOTALL)
            if args_match:
                arguments_raw = args_match.group(1).strip()

            if not tool_name:
                continue

            func_name = f"{server_name}_{tool_name}" if server_name else tool_name

            # Parse arguments — may be JSON or key=value text
            arguments = {}
            if arguments_raw:
                try:
                    arguments = json.loads(arguments_raw)
                    if not isinstance(arguments, dict):
                        arguments = {"input": arguments}
                except (json.JSONDecodeError, ValueError):
                    arguments = _parse_plain_text_args(arguments_raw)

            arguments_str = json.dumps(arguments, ensure_ascii=False)
            tool_call = {
                "index": tool_call_index,
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": func_name,
                    "arguments": arguments_str
                }
            }
            _sanitize_tool_call_arguments(tool_call)
            tool_calls.append(tool_call)
            tool_call_index += 1
            logger.info(f"[DEEPSEEK] Parsed CallMcpTool: {func_name}({arguments_str[:80]}...)")

    if not tool_calls:
        return content, [], False

    # Combine clean content
    clean_content = f"{content_before} {content_after}".strip()

    return clean_content, tool_calls, True


def _parse_plain_text_args(text: str) -> Dict[str, Any]:
    """Parse plain text arguments from Ollama native tool call format.

    Handles formats like:
    - ToolRun Command "find /path -type f"
    - command="find /path" description="list files"
    - Key-value pairs separated by spaces

    Args:
        text: Plain text content inside a tool call tag

    Returns:
        Dict of parsed arguments
    """
    if not text or not text.strip():
        return {}

    text = text.strip()
    arguments = {}

    # Try key="value" pairs first (most structured)
    kv_pattern = re.compile(r'(\w+)=(?:"([^"]*)"|\'([^\']*)\'|(\S+))')
    matches = list(kv_pattern.finditer(text))
    if matches:
        for match in matches:
            key = match.group(1)
            value = match.group(2) or match.group(3) or match.group(4) or ""
            arguments[key] = value
        if arguments:
            return arguments

    # Try quoted string arguments after command name
    # e.g. ToolRun Command "find /path -type f"
    # Split by spaces but respect quotes
    parts = []
    current = ""
    in_quotes = False
    quote_char = None
    for char in text:
        if char in '"\'':
            if in_quotes and char == quote_char:
                in_quotes = False
                quote_char = None
            elif not in_quotes:
                in_quotes = True
                quote_char = char
            else:
                current += char
        elif char == ' ' and not in_quotes:
            if current:
                parts.append(current)
                current = ""
        else:
            current += char
    if current:
        parts.append(current)

    # First part is usually the command name, rest are arguments
    if len(parts) >= 2:
        # Common pattern: "Command" followed by the actual command string
        # e.g. ["ToolRun", "Command", "find /path -type f"]
        # or ["Bash", "find /path -type f"]
        # Skip label-like words (Command, Run, Execute, etc.)
        label_words = {'command', 'run', 'execute', 'tool', 'toolrun', 'bash', 'shell'}
        arg_parts = []
        for part in parts:
            if part.lower() not in label_words or arg_parts:
                arg_parts.append(part)

        if arg_parts:
            # Join the remaining parts as the command
            arguments['command'] = ' '.join(arg_parts)

    return arguments


class OllamaProxy:
    """Proxy requests to Ollama with model name manipulation"""

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.ollama_base_url
        self._mappings_loaded = False
        self._http_client: Optional[httpx.AsyncClient] = None
        self._use_load_balancing = False  # Will be enabled when nodes are configured
    
    # HTTP status codes that should trigger a retry on another node
    # (model might be available on a different node)
    NODE_RETRYABLE_STATUS_CODES = {404, 423, 429, 500, 502, 503, 504}

    async def _select_node_url(self, model_name: str, exclude_nodes: Optional[List[str]] = None) -> str:
        """
        Select the best node URL for a model using load balancing.

        Args:
            model_name: The model name to route
            exclude_nodes: List of base_url strings to exclude (already tried nodes)

        Falls back to self.base_url if load balancing is not configured or no nodes available.
        """
        try:
            from app.database import async_session_maker
            from app.node_manager import node_manager
            from app.load_balancer import load_balancer

            async with async_session_maker() as session:
                # Get real model name
                real_model_name = model_mapper.get_real_model_name(model_name)

                # Get available nodes for this model
                nodes = await node_manager.get_nodes_for_model(real_model_name, session)

                if not nodes:
                    # Try display name as fallback
                    nodes = await node_manager.get_nodes_for_model(model_name, session)

                if not nodes:
                    # No nodes have this model - use default (if not excluded)
                    if exclude_nodes and self.base_url in exclude_nodes:
                        logger.info(f"[LB] No nodes found for model {model_name} and default URL excluded")
                        return ""
                    logger.info(f"[LB] No nodes found for model {model_name}, using default URL")
                    return self.base_url

                # Filter out excluded nodes
                if exclude_nodes:
                    filtered_nodes = [
                        n for n in nodes
                        if n.get('base_url') not in exclude_nodes
                    ]
                    if filtered_nodes:
                        nodes = filtered_nodes
                        logger.info(
                            f"[LB] Filtered to {len(nodes)} nodes for model {model_name} "
                            f"(excluded {len(exclude_nodes)} tried nodes)"
                        )
                    else:
                        # All known nodes excluded, try default if not excluded
                        if self.base_url not in exclude_nodes:
                            logger.info(f"[LB] All nodes excluded for model {model_name}, trying default URL")
                            return self.base_url
                        logger.info(f"[LB] All nodes excluded for model {model_name}, no alternatives")
                        return ""

                # Select best node using load balancer
                selected_node = await load_balancer.select_node(
                    nodes, strategy="least_loaded", session=session
                )

                if selected_node:
                    node_name = selected_node.get('node_name') or selected_node.get('name', 'unknown')
                    node_base_url = selected_node.get('base_url')
                    logger.info(f"[LB] Selected node {node_name} for model {model_name}")
                    if node_base_url:
                        return node_base_url

                return self.base_url

        except Exception as e:
            logger.error(f"[LB] Error selecting node: {e}, falling back to default URL")
            return self.base_url
    
    async def _ensure_mappings_loaded(self):
        """Ensure model mappings and groups are loaded from database"""
        # Only load once at startup, rely on cache invalidation
        if not self._mappings_loaded:
            await model_mapper.ensure_loaded()
            await model_group_manager.ensure_loaded()
            self._mappings_loaded = True
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """
        Get or create persistent HTTP client with connection pooling
        
        Returns:
            Configured AsyncClient with HTTP/2 support
        """
        if self._http_client is None:
            # Connection limits
            # Increased for agentic workflows (Cursor Agent can spawn multiple requests)
            limits = httpx.Limits(
                max_keepalive_connections=40,
                max_connections=100,
                keepalive_expiry=300  # 5 minutes
            )
            
            # Async HTTP client
            # HTTP/2 disabled for better compatibility with Ollama
            self._http_client = httpx.AsyncClient(
                timeout=1200.0,  # 20 minutes (for long reasoning/tools)
                limits=limits,
                http2=True  # Disabled to prevent connection stability issues
            )
        
        return self._http_client
    
    async def close(self):
        """Close HTTP client connection pool"""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
    
    async def _resolve_model_groups(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Resolve model group names to actual model names in request data.

        If a model name is a group, it's resolved to an appropriate member based on:
        - Request capabilities (vision detection)
        - Group strategy (round_robin/weighted/priority)
        - Member capabilities and priorities

        If not a group, the model name is left unchanged (backward compatible).

        Args:
            data: Request data with potential model field

        Returns:
            Modified data with resolved model names
        """
        if not data:
            return data

        data_copy = data.copy()

        # Handle 'model' field - resolve groups first
        if 'model' in data_copy:
            original_model = data_copy['model']
            resolved_model = await model_group_manager.resolve_model(original_model, data_copy)
            if resolved_model != original_model:
                logger.info(f"[ModelGroup] Resolved group '{original_model}' -> '{resolved_model}'")
            data_copy['model'] = resolved_model

        # Handle 'name' field (used in show, delete, pull, push) - resolve groups
        if 'name' in data_copy:
            original_name = data_copy['name']
            resolved_name = await model_group_manager.resolve_model(original_name)
            if resolved_name != original_name:
                logger.info(f"[ModelGroup] Resolved group '{original_name}' -> '{resolved_name}'")
            data_copy['name'] = resolved_name

        # Handle 'source' and 'destination' fields (used in copy)
        if 'source' in data_copy:
            original_source = data_copy['source']
            resolved_source = await model_group_manager.resolve_model(original_source)
            if resolved_source != original_source:
                logger.info(f"[ModelGroup] Resolved group '{original_source}' -> '{resolved_source}'")
            data_copy['source'] = resolved_source
        if 'destination' in data_copy:
            original_dest = data_copy['destination']
            resolved_dest = await model_group_manager.resolve_model(original_dest)
            if resolved_dest != original_dest:
                logger.info(f"[ModelGroup] Resolved group '{original_dest}' -> '{resolved_dest}'")
            data_copy['destination'] = resolved_dest

        return data_copy

    def _find_group_for_model(self, model_name: str) -> Optional[str]:
        """
        Find which group (if any) a model belongs to.

        Args:
            model_name: The model name (either group name or member display name)

        Returns:
            Group name if the model is a group or belongs to a group, None otherwise
        """
        # First check if model_name is a group name
        if model_group_manager.is_group(model_name):
            return model_name

        # Check if model_name is a member of any group
        for group_name, group_data in model_group_manager._groups.items():
            members = group_data.get("members", [])
            for member in members:
                if member.model_display_name == model_name:
                    return group_name

        return None

    def _get_fallback_model(self, group_name: str, failed_model: str, tried_models: Optional[set] = None) -> Optional[str]:
        """
        Get the next fallback model from a group after a failure.

        Args:
            group_name: Name of the model group
            failed_model: The model that failed (display name)
            tried_models: Set of model names already tried

        Returns:
            Next fallback model display name, or None if no fallback available
        """
        return model_group_manager.get_fallback(group_name, failed_model, tried_models)

    def _map_model_to_ollama(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map model names in request data from client format to Ollama format

        Note: This should be called AFTER _resolve_model_groups to ensure
        groups are resolved first. Group resolution must happen before mapping
        because it needs to know the actual model name.

        Args:
            data: Request data with potential model field

        Returns:
            Modified data with real model names
        """
        if not data:
            return data

        data_copy = data.copy()

        # Handle 'model' field
        if 'model' in data_copy:
            data_copy['model'] = model_mapper.get_real_model_name(data_copy['model'])

        # Handle 'name' field (used in show, delete, pull, push)
        if 'name' in data_copy:
            data_copy['name'] = model_mapper.get_real_model_name(data_copy['name'])

        # Handle 'source' and 'destination' fields (used in copy)
        if 'source' in data_copy:
            data_copy['source'] = model_mapper.get_real_model_name(data_copy['source'])
        if 'destination' in data_copy:
            data_copy['destination'] = model_mapper.get_real_model_name(data_copy['destination'])

        return data_copy
    
    async def _map_model_to_display(self, real_name: str) -> str:
        """
        Map real model name to display name (reverse mapping)
        
        Args:
            real_name: Real model name from Ollama
        
        Returns:
            Display model name for client
        """
        await self._ensure_mappings_loaded()
        return model_mapper.get_display_model_name(real_name)
    
    def _map_native_ollama_response(self, data: Any) -> Any:
        """
        Lightweight model name mapping for native Ollama endpoints.
        Only maps model/name fields. Does NOT remove any fields or apply
        any OpenAI/Cursor-specific transformations.
        This ensures the response is identical to what native Ollama would return,
        except with display model names.
        """
        if isinstance(data, dict):
            data_copy = data.copy()
            if 'model' in data_copy:
                data_copy['model'] = model_mapper.get_display_model_name(data_copy['model'])
            if 'name' in data_copy:
                data_copy['name'] = model_mapper.get_display_model_name(data_copy['name'])
            return data_copy
        return data

    def _map_model_from_ollama(self, data: Any) -> Any:
        """
        Map model names in response data from Ollama format to client format.
        Also transforms response to be Cursor-compatible.
        
        Args:
            data: Response data with potential model fields
        
        Returns:
            Modified data with display model names and Cursor-compatible format
        """
        if isinstance(data, dict):
            data_copy = data.copy()
            
            # Handle 'model' field
            if 'model' in data_copy:
                data_copy['model'] = model_mapper.get_display_model_name(data_copy['model'])
            
            # Handle 'name' field
            if 'name' in data_copy:
                data_copy['name'] = model_mapper.get_display_model_name(data_copy['name'])
            
            # Handle 'parent_model' field
            if 'parent_model' in data_copy:
                data_copy['parent_model'] = model_mapper.get_display_model_name(data_copy['parent_model'])
            
            # Remove remote_model field to make cloud models look like local models
            if 'remote_model' in data_copy:
                del data_copy['remote_model']
            
            # Remove remote_host field to make cloud models look like local models
            if 'remote_host' in data_copy:
                del data_copy['remote_host']
            
            # Handle nested details
            if 'details' in data_copy and isinstance(data_copy['details'], dict):
                if 'parent_model' in data_copy['details']:
                    data_copy['details']['parent_model'] = model_mapper.get_display_model_name(
                        data_copy['details']['parent_model']
                    )
            
            # ============================================================
            # CURSOR COMPATIBILITY: Transform non-standard fields
            # ============================================================
            # Handle 'choices' array for streaming chunks (OpenAI format)
            if 'choices' in data_copy and isinstance(data_copy['choices'], list):
                transformed_choices = []
                for choice in data_copy['choices']:
                    if isinstance(choice, dict):
                        choice_copy = choice.copy()
                        
                        # Handle 'delta' in streaming responses
                        if 'delta' in choice_copy and isinstance(choice_copy['delta'], dict):
                            delta = choice_copy['delta'].copy()
                            
                            # Keep 'reasoning' field as-is for clients that support it
                            # But also check for tool calls in reasoning
                            reasoning = delta.get('reasoning', '')
                            if reasoning and ('<|tool_calls_section_begin|>' in reasoning or '<tool_calls>' in reasoning or '<CallMcpTool>' in reasoning):
                                # Route to appropriate parser based on format
                                if '<|tool_calls_section_begin|>' in reasoning:
                                    clean_reasoning, tool_calls_from_reasoning, has_tool_calls = parse_kimi_tool_calls(reasoning)
                                    parser_name = 'KIMI'
                                else:
                                    clean_reasoning, tool_calls_from_reasoning, has_tool_calls = parse_deepseek_tool_calls(reasoning)
                                    parser_name = 'DEEPSEEK'

                                if has_tool_calls:
                                    logger.info(f"[{parser_name}] Detected {len(tool_calls_from_reasoning)} tool call(s) in reasoning, converting to OpenAI format")
                                    # Update reasoning with clean content
                                    if clean_reasoning:
                                        delta['reasoning'] = clean_reasoning
                                    else:
                                        delta.pop('reasoning', None)

                                    # Add tool_calls to delta (merge if already exists)
                                    existing_tool_calls = delta.get('tool_calls', [])
                                    delta['tool_calls'] = existing_tool_calls + tool_calls_from_reasoning
                                    
                            # CURSOR COMPATIBILITY: Cursor expects 'reasoning_content' instead of 'reasoning'
                            if 'reasoning' in delta:
                                r_val = delta.pop('reasoning')
                                if r_val: # Only map if not empty
                                    delta['reasoning_content'] = r_val
                            
                            # KIMI TOOL CALL FIX: Convert Kimi's custom tool call format
                            # to OpenAI's standard tool_calls format (in content)
                            content = delta.get('content', '')
                            if content and '<|tool_calls_section_begin|>' in content:
                                clean_content, tool_calls, has_tool_calls = parse_kimi_tool_calls(content)

                                if has_tool_calls:
                                    logger.info(f"[KIMI] Detected {len(tool_calls)} tool call(s) in content, converting to OpenAI format")
                                    # Update delta with clean content and tool_calls
                                    if clean_content:
                                        delta['content'] = clean_content
                                    else:
                                        # If no clean content, remove content field entirely
                                        delta.pop('content', None)

                                    # Add tool_calls to delta (merge if already exists from reasoning)
                                    existing_tool_calls = delta.get('tool_calls', [])
                                    delta['tool_calls'] = existing_tool_calls + tool_calls

                            # DEEPSEEK TOOL CALL FIX: Convert DeepSeek's XML tool call format
                            # to OpenAI's standard tool_calls format (in content)
                            content = delta.get('content', '')
                            if content and (('<tool_calls>' in content and '</tool_calls>' in content) or ('<CallMcpTool>' in content and '</CallMcpTool>' in content)):
                                clean_content, tool_calls, has_tool_calls = parse_deepseek_tool_calls(content)

                                if has_tool_calls:
                                    logger.info(f"[DEEPSEEK] Detected {len(tool_calls)} tool call(s) in content, converting to OpenAI format")
                                    if clean_content:
                                        delta['content'] = clean_content
                                    else:
                                        delta.pop('content', None)

                                    existing_tool_calls = delta.get('tool_calls', [])
                                    delta['tool_calls'] = existing_tool_calls + tool_calls
                            
                            choice_copy['delta'] = delta
                        
                        # Handle 'message' in non-streaming responses
                        if 'message' in choice_copy and isinstance(choice_copy['message'], dict):
                            message = choice_copy['message'].copy()
                            
                            # Keep 'reasoning' field as-is for clients that support it
                            # But also check for tool calls in reasoning
                            reasoning = message.get('reasoning', '')
                            if reasoning and ('<|tool_calls_section_begin|>' in reasoning or '<tool_calls>' in reasoning or '<CallMcpTool>' in reasoning):
                                # Route to appropriate parser based on format
                                if '<|tool_calls_section_begin|>' in reasoning:
                                    clean_reasoning, tool_calls_from_reasoning, has_tool_calls = parse_kimi_tool_calls(reasoning)
                                    parser_name = 'KIMI'
                                else:
                                    clean_reasoning, tool_calls_from_reasoning, has_tool_calls = parse_deepseek_tool_calls(reasoning)
                                    parser_name = 'DEEPSEEK'

                                if has_tool_calls:
                                    logger.info(f"[{parser_name}] Detected {len(tool_calls_from_reasoning)} tool call(s) in message reasoning, converting to OpenAI format")
                                    # Update reasoning with clean content
                                    if clean_reasoning:
                                        message['reasoning'] = clean_reasoning
                                    else:
                                        message.pop('reasoning', None)

                                    # Add tool_calls to message (merge if already exists)
                                    existing_tool_calls = message.get('tool_calls', [])
                                    message['tool_calls'] = existing_tool_calls + tool_calls_from_reasoning
                            
                            # CURSOR COMPATIBILITY: Cursor expects 'reasoning_content' instead of 'reasoning'
                            if 'reasoning' in message:
                                message['reasoning_content'] = message.pop('reasoning')
                            
                            # KIMI TOOL CALL FIX: Convert Kimi's custom tool call format
                            # to OpenAI's standard tool_calls format (non-streaming, in content)
                            content = message.get('content', '')
                            if content and '<|tool_calls_section_begin|>' in content:
                                clean_content, tool_calls, has_tool_calls = parse_kimi_tool_calls(content)

                                if has_tool_calls:
                                    logger.info(f"[KIMI] Detected {len(tool_calls)} tool call(s) in message content, converting to OpenAI format")
                                    # Update message with clean content and tool_calls
                                    if clean_content:
                                        message['content'] = clean_content
                                    else:
                                        message['content'] = None

                                    # Add tool_calls to message (merge if already exists from reasoning)
                                    existing_tool_calls = message.get('tool_calls', [])
                                    message['tool_calls'] = existing_tool_calls + tool_calls

                            # DEEPSEEK TOOL CALL FIX: Convert DeepSeek's XML tool call format
                            # to OpenAI's standard tool_calls format (non-streaming, in content)
                            content = message.get('content', '')
                            if content and (('<tool_calls>' in content and '</tool_calls>' in content) or ('<CallMcpTool>' in content and '</CallMcpTool>' in content)):
                                clean_content, tool_calls, has_tool_calls = parse_deepseek_tool_calls(content)

                                if has_tool_calls:
                                    logger.info(f"[DEEPSEEK] Detected {len(tool_calls)} tool call(s) in message content, converting to OpenAI format")
                                    if clean_content:
                                        message['content'] = clean_content
                                    else:
                                        message['content'] = None

                                    existing_tool_calls = message.get('tool_calls', [])
                                    message['tool_calls'] = existing_tool_calls + tool_calls
                            
                            choice_copy['message'] = message
                        
                        transformed_choices.append(choice_copy)
                    else:
                        transformed_choices.append(choice)
                
                data_copy['choices'] = transformed_choices
            
            return data_copy
        
        return data
    
    def _map_models_list(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map model names in /api/tags response
        
        Args:
            data: Response from /api/tags
        
        Returns:
            Modified data with display model names
        """
        if not isinstance(data, dict) or 'models' not in data:
            return data
        
        data_copy = data.copy()
        models = []
        
        for model in data_copy.get('models', []):
            model_copy = model.copy() if isinstance(model, dict) else model
            
            if isinstance(model_copy, dict):
                # Map name field
                if 'name' in model_copy:
                    model_copy['name'] = model_mapper.get_display_model_name(model_copy['name'])
                
                # Map model field if exists
                if 'model' in model_copy:
                    model_copy['model'] = model_mapper.get_display_model_name(model_copy['model'])
                
                # Remove remote_model field to make cloud models look like local models
                if 'remote_model' in model_copy:
                    del model_copy['remote_model']
                
                # Remove remote_host field to make cloud models look like local models
                if 'remote_host' in model_copy:
                    del model_copy['remote_host']
                
                # Map parent_model in details
                if 'details' in model_copy and isinstance(model_copy['details'], dict):
                    if 'parent_model' in model_copy['details']:
                        model_copy['details']['parent_model'] = model_mapper.get_display_model_name(
                            model_copy['details']['parent_model']
                        )
            
            models.append(model_copy)
        
        data_copy['models'] = models
        return data_copy
    
    def _map_openai_models_list(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map model names in /v1/models response (OpenAI compatible format)
        
        Args:
            data: Response from /v1/models
        
        Returns:
            Modified data with display model names
        """
        if not isinstance(data, dict) or 'data' not in data:
            return data
        
        data_copy = data.copy()
        models = []
        
        for model in data_copy.get('data', []):
            model_copy = model.copy() if isinstance(model, dict) else model
            
            if isinstance(model_copy, dict):
                # Map id field (model name in OpenAI format)
                if 'id' in model_copy:
                    model_copy['id'] = model_mapper.get_display_model_name(model_copy['id'])
            
            models.append(model_copy)
        
        data_copy['data'] = models
        return data_copy
    
    async def check_user_limits(self, username: str, request_type: str) -> bool:
        """
        Check if user has exceeded their limits (with Redis caching)
        
        Args:
            username: Username
            request_type: Type of request (generate, chat, embeddings, etc.)
        
        Returns:
            True if user is within limits, False otherwise
        """
        from datetime import datetime, timedelta
        from app.redis import redis_manager, CACHE_KEYS, CACHE_TTL
        
        # 1. Get user limit from cache or DB
        limit_cache_key = CACHE_KEYS["USER_LIMIT"].format(username=username)
        user_limit = await redis_manager.get(limit_cache_key)
        
        if not user_limit:
            # Cache miss - get from DB
            user_limit = await user_manager.get_user_limit(username)
            if user_limit:
                await redis_manager.set(limit_cache_key, user_limit, expire=CACHE_TTL["USER_LIMIT"])
            else:
                # No limits set, allow request
                return True
        
        # 2. Get daily usage from cache or DB
        today = datetime.utcnow().strftime("%Y-%m-%d")
        usage_cache_key = CACHE_KEYS["USER_DAILY_USAGE"].format(username=username, date=today)
        
        # Try to get from cache (as hash)
        daily_usage = None
        try:
            if redis_manager._connected and redis_manager.redis_client:
                # Check if key exists and get type
                key_exists = await redis_manager.redis_client.exists(usage_cache_key)
                if key_exists:
                    key_type = await redis_manager.redis_client.type(usage_cache_key)
                    if key_type == 'hash':
                        # Read as hash
                        hash_data = await redis_manager.redis_client.hgetall(usage_cache_key)
                        daily_usage = {
                            "total_requests": int(hash_data.get("total_requests", 0)),
                            "total_tokens": int(hash_data.get("total_tokens", 0)),
                            "prompt_tokens": int(hash_data.get("prompt_tokens", 0)),
                            "completion_tokens": int(hash_data.get("completion_tokens", 0))
                        }
                    else:
                        # Wrong type, delete it
                        await redis_manager.redis_client.delete(usage_cache_key)
        except Exception as e:
            logger.warning(f"Error reading daily usage cache: {e}")
        
        if not daily_usage:
            # Cache miss - get from DB
            start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day + timedelta(days=1)
            daily_usage = await user_manager.get_user_token_usage(username, start_of_day, end_of_day)
            if daily_usage and redis_manager._connected and redis_manager.redis_client:
                # Store as hash
                await redis_manager.redis_client.hset(
                    usage_cache_key,
                    mapping={
                        "total_requests": daily_usage.get("total_requests", 0),
                        "total_tokens": daily_usage.get("total_tokens", 0),
                        "prompt_tokens": daily_usage.get("prompt_tokens", 0),
                        "completion_tokens": daily_usage.get("completion_tokens", 0)
                    }
                )
        
        # 3. Check request limit
        request_limit = user_limit.get("request_limit")
        if request_limit is not None and daily_usage:
            if daily_usage.get("total_requests", 0) >= request_limit:
                return False
        
        # 4. Check token limit
        token_limit = user_limit.get("token_limit")
        if token_limit is not None and daily_usage:
            if daily_usage.get("total_tokens", 0) >= token_limit:
                return False
        
        return True
    
    async def _log_user_activity(
        self,
        username: str,
        model_name: str,
        request_type: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        status_code: int = None,
        duration_ms: int = None,
        error_message: str = None
    ):
        """
        Log user activity for token usage and model access (batch processing)

        Queues the activity log for background batch processing.

        Args:
            username: Username
            model_name: Model name used
            request_type: Type of request (generate, chat, embeddings, etc.)
            prompt_tokens: Number of prompt tokens used
            completion_tokens: Number of completion tokens used
            total_tokens: Total tokens used
            status_code: HTTP status code of the response
            duration_ms: Request duration in milliseconds
            error_message: Error message for failed requests
        """
        from app.background_tasks import queue_activity_log_async

        await queue_activity_log_async(
            username=username,
            model_name=model_name,
            request_type=request_type,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens or (prompt_tokens + completion_tokens),
            status_code=status_code,
            duration_ms=duration_ms,
            error_message=error_message
        )
    
    async def proxy_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        stream: bool = False,
        username: Optional[str] = None
    ):
        """
        Proxy request to Ollama with automatic failover.

        Failover strategy (two levels):
        1. Node retry: On retryable errors (404, 423, 429, 5xx, connection errors),
           try the same model on a different node.
        2. Model fallback: If all nodes are exhausted and the model belongs to a group,
           try the next fallback model in the group (only for 5xx and connection errors).

        Args:
            method: HTTP method (GET, POST, DELETE)
            endpoint: Ollama API endpoint
            data: Request body data
            stream: Whether to stream the response
            username: Username for logging and limit checking

        Returns:
            Response from Ollama (mapped model names)
        """
        start_time = time.monotonic()

        # Ensure model mappings and groups are loaded from database
        await self._ensure_mappings_loaded()

        # Track original model name and group for failover
        original_model = None
        original_group = None
        tried_models: set = set()
        tried_nodes: set = set()  # Track node base_urls already tried

        # Step 1: Resolve model groups (if model name is a group, pick appropriate member)
        if data:
            # Store original model name before resolution
            original_model = data.get('model') or data.get('name')
            data = await self._resolve_model_groups(data)

            # Check if original model was a group (for failover)
            if original_model:
                original_group = self._find_group_for_model(original_model)

        # Extract model name for node selection and logging
        model_name = None
        if data and isinstance(data, dict):
            model_name = data.get('model') or data.get('name')
            if model_name:
                tried_models.add(model_name)

        # Select node URL: use load balancer if nodes exist, else OLLAMA_BASE_URL fallback
        base_url = await self._select_node_url(model_name or '')
        url = f"{base_url}{endpoint}"
        if base_url:
            tried_nodes.add(base_url)

        # Step 2: Map model names (display_name -> real_name)
        if data:
            data = self._map_model_to_ollama(data)

        # Validate data for POST requests
        if method.upper() == "POST" and not data:
            raise HTTPException(
                status_code=400,
                detail="Request body is required for POST requests"
            )

        # Detect if this is an OpenAI-compatible endpoint (for SSE formatting)
        is_openai_endpoint = endpoint.startswith("/v1/")

        # ============================================================
        # FAILOVER WRAPPER
        # ============================================================
        # For streaming: failover handled inside generator
        # For non-streaming: failover handled in try-except below

        try:
            if method.upper() == "POST" and stream:
                # Handle streaming response with failover support
                return await self._stream_with_failover(
                    url=url,
                    data=data,
                    is_openai_endpoint=is_openai_endpoint,
                    username=username,
                    original_group=original_group,
                    tried_models=tried_models,
                    tried_nodes=tried_nodes,
                    original_data=data.copy() if data else None,
                    endpoint=endpoint,
                    base_url=base_url,
                    start_time=start_time
                )

            # Non-streaming requests with failover support
            return await self._non_streaming_with_failover(
                url=url,
                method=method,
                data=data,
                endpoint=endpoint,
                is_openai_endpoint=is_openai_endpoint,
                username=username,
                original_group=original_group,
                tried_models=tried_models,
                tried_nodes=tried_nodes,
                model_name=model_name,
                start_time=start_time
            )

        except HTTPException:
            raise
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=503,
                detail=f"Failed to connect to Ollama: {str(e)}"
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Proxy error: {str(e)}"
            )

    async def _stream_with_failover(
        self,
        url: str,
        data: Dict[str, Any],
        is_openai_endpoint: bool,
        username: Optional[str],
        original_group: Optional[str],
        tried_models: set,
        tried_nodes: set,
        original_data: Optional[Dict[str, Any]],
        endpoint: str,
        base_url: str,
        start_time: float
    ):
        """
        Handle streaming requests with automatic failover.

        Two-level failover:
        1. Node retry: On retryable errors (404, 423, 429, 5xx), try same model on another node.
        2. Model fallback: If all nodes exhausted and model is in a group, try next fallback model.

        Args:
            url: Target URL
            data: Request data
            is_openai_endpoint: Whether this is an OpenAI-compatible endpoint
            username: Username for logging
            original_group: Original group name (if model came from a group)
            tried_models: Set of models already tried
            tried_nodes: Set of node base_urls already tried
            original_data: Original request data before mapping
            endpoint: API endpoint
            base_url: Base URL for node selection

        Returns:
            StreamingResponse with failover support
        """
        # Determine media type
        if is_openai_endpoint:
            media_type = "text/event-stream"
        else:
            media_type = "application/x-ndjson"

        async def stream_generator_with_failover():
            """Generator that handles failover for streaming requests"""
            nonlocal tried_models, tried_nodes

            # Store error info for potential failover
            last_error = None
            current_data = data.copy() if data else {}
            current_url = url

            for attempt in range(MAX_FAILOVER_RETRIES + 1):
                client = await self._get_http_client()
                current_model = current_data.get('model', 'unknown')

                logger.info(f"[STREAM START] Attempt {attempt + 1}: Sending streaming request to {current_url}")
                logger.info(f"[STREAM START] Model: {current_model}, OpenAI endpoint: {is_openai_endpoint}")
                logger.info(f"[STREAM START] max_tokens: {current_data.get('max_tokens', 'not set')}, temperature: {current_data.get('temperature', 'not set')}")

                try:
                    if current_data.get("tools"):
                        logger.info(f"[STREAM START] Tools provided: {[t.get('function', {}).get('name') for t in current_data.get('tools', [])]}")

                    async with client.stream("POST", current_url, json=current_data) as resp:
                        logger.info(f"[STREAM] Ollama response status: {resp.status_code}")

                        # Check status code before streaming
                        if resp.status_code != 200:
                            error_text = await resp.aread()
                            error_msg = error_text.decode()

                            # Parse error message
                            try:
                                error_json = json.loads(error_msg)
                                if isinstance(error_json, dict) and 'error' in error_json:
                                    error_detail = error_json['error']
                                    if isinstance(error_detail, dict) and 'message' in error_detail:
                                        error_msg = error_detail['message']
                                    elif isinstance(error_detail, str):
                                        error_msg = error_detail
                            except (json.JSONDecodeError, KeyError, TypeError):
                                pass

                            logger.error(f"Ollama upstream error ({resp.status_code}): {error_msg}")
                            logger.error(f"Request URL: {current_url}")
                            logger.error(f"Request data: {json.dumps(current_data, ensure_ascii=False, indent=2)}")

                            # === NODE-LEVEL RETRY ===
                            # Try the same model on a different node first
                            if resp.status_code in self.NODE_RETRYABLE_STATUS_CODES and attempt < MAX_FAILOVER_RETRIES:
                                # Add current node to tried list
                                current_base_url = current_url.rsplit(endpoint, 1)[0] if endpoint in current_url else base_url
                                tried_nodes.add(current_base_url)

                                new_base_url = await self._select_node_url(
                                    current_model, exclude_nodes=list(tried_nodes)
                                )
                                if new_base_url:
                                    logger.warning(
                                        f"[NODE RETRY] Stream error {resp.status_code} from {current_base_url}, "
                                        f"trying node {new_base_url} for model {current_model}"
                                    )
                                    current_url = f"{new_base_url}{endpoint}"
                                    continue
                                logger.info(f"[NODE RETRY] No more nodes available for model {current_model}")

                            # === MODEL-LEVEL FALLBACK ===
                            # Only for 5xx errors and connection errors, try a different model from the group
                            should_model_failover = (
                                resp.status_code >= 500 and
                                original_group and
                                attempt < MAX_FAILOVER_RETRIES
                            )

                            if should_model_failover:
                                # Try to get fallback model
                                fallback_model = self._get_fallback_model(original_group, current_model, tried_models)

                                if fallback_model and fallback_model not in tried_models:
                                    logger.warning(f"[FAILOVER] Stream error {resp.status_code}, trying fallback model: {fallback_model}")
                                    tried_models.add(fallback_model)

                                    # Update data with fallback model
                                    current_data = original_data.copy() if original_data else {}
                                    current_data['model'] = fallback_model
                                    current_data = await self._resolve_model_groups(current_data)
                                    current_data = self._map_model_to_ollama(current_data)

                                    # Select new node URL for fallback (reset tried_nodes for new model)
                                    new_base_url = await self._select_node_url(fallback_model)
                                    current_url = f"{new_base_url}{endpoint}"
                                    if new_base_url:
                                        tried_nodes.add(new_base_url)

                                    # Log failover attempt
                                    logger.info(f"[FAILOVER] Retrying with fallback model {fallback_model} (attempt {attempt + 2})")
                                    continue

                            # No failover available or max retries reached - yield error
                            error_type = "api_error"
                            error_code = resp.status_code

                            # Check for context error
                            error_msg_lower = error_msg.lower()
                            is_context_error = any(phrase in error_msg_lower for phrase in [
                                'context length', 'context_length', 'num_ctx',
                                'context window', 'token limit', 'too long',
                                'maximum context', 'exceeds the model',
                            ])

                            if is_context_error:
                                error_type = "context_length_exceeded"
                                error_code = "context_length_exceeded"
                                friendly_msg = (
                                    f"Context limiti aşıldı. Model'in context penceresi doldu. "
                                    f"Lütfen yeni bir chat başlatın veya context'i temizleyin. "
                                    f"Detay: {error_msg}"
                                )
                                logger.warning(f"[CONTEXT OVERFLOW] Model context limit reached: {error_msg}")
                            else:
                                friendly_msg = f"Ollama upstream error: {error_msg}"

                            error_response = {
                                "error": {
                                    "message": friendly_msg,
                                    "type": error_type,
                                    "code": error_code
                                }
                            }
                            yield b'data: ' + json.dumps(error_response, ensure_ascii=False).encode('utf-8') + b'\n\n'
                            yield b'data: [DONE]\n\n'
                            return

                        # Success! Stream the response
                        # Use the existing streaming logic from line 1057 onwards
                        # (this is the happy path)

                        buffer = b""
                        pending_tool_calls: Dict[str, Dict[str, Any]] = {}
                        prompt_tokens = 0
                        completion_tokens = 0
                        first_chunk_sent = False
                        done_marker_sent = False
                        usage_chunk_received = False
                        just_yielded_assembled_tools = False
                        chunk_count = 0
                        total_bytes = 0

                        kimi_content_buffer = ""
                        kimi_buffering_active = False
                        kimi_suspicion_buffer = ""
                        current_model = current_data.get('model', 'unknown')

                        # DeepSeek XML tool call buffering state
                        deepseek_content_buffer = ""
                        deepseek_buffering_active = False
                        deepseek_suspicion_buffer = ""

                        in_thinking = False
                        think_suspicion = ""

                        is_kimi_model = 'kimi' in current_model.lower() or 'moonshot' in current_model.lower()
                        is_deepseek_model = 'deepseek' in current_model.lower() or 'ds-' in current_model.lower()

                        async for chunk in resp.aiter_raw():
                            if not chunk:
                                continue

                            chunk_count += 1
                            total_bytes += len(chunk)

                            if chunk_count <= 3:
                                logger.info(f"[STREAM CHUNK {chunk_count}] Received {len(chunk)} bytes: {chunk[:200]!r}")

                            buffer += chunk

                            while b'\n' in buffer:
                                line, buffer = buffer.split(b'\n', 1)
                                if line:
                                    try:
                                        if line.startswith(b'data: '):
                                            json_str = line[6:].decode('utf-8').strip()
                                            if json_str and json_str != '[DONE]':
                                                logger.info(f"[OLLAMA IN] {json_str}")
                                                json_data = json.loads(json_str)

                                                # Kimi tool call handling
                                                content = ""
                                                reasoning = ""
                                                combined_for_detection = ""

                                                if isinstance(json_data, dict) and 'choices' in json_data:
                                                    for choice in json_data.get('choices', []):
                                                        if isinstance(choice, dict):
                                                            delta = choice.get('delta', {})
                                                            if isinstance(delta, dict):
                                                                content = delta.get('content', '') or ''
                                                                reasoning = delta.get('reasoning', '') or ''

                                                if is_kimi_model:
                                                    combined_for_detection = content + reasoning

                                                    if content:
                                                        logger.info(f"[KIMI DEBUG] Received content chunk: {content[:100]!r}")
                                                    if reasoning:
                                                        logger.info(f"[KIMI DEBUG] Received reasoning chunk: {reasoning[:100]!r}")

                                                    if kimi_suspicion_buffer:
                                                        logger.info(f"[KIMI DEBUG] Appending suspicion buffer: {kimi_suspicion_buffer!r} to current combined")
                                                        combined_for_detection = kimi_suspicion_buffer + combined_for_detection
                                                        kimi_suspicion_buffer = ""

                                                if is_kimi_model and kimi_buffering_active:
                                                    kimi_content_buffer += combined_for_detection

                                                    if '<|tool_calls_section_end|>' in kimi_content_buffer:
                                                        logger.info(f"[KIMI] Tool call section complete, processing buffer")

                                                        clean_content, tool_calls, has_tool_calls = parse_kimi_tool_calls(kimi_content_buffer)

                                                        if has_tool_calls:
                                                            logger.info(f"[KIMI] Converted {len(tool_calls)} tool call(s) to OpenAI format")

                                                            if clean_content:
                                                                if '<|tool_calls_' in clean_content:
                                                                    clean_content = re.sub(r'<\|tool_calls_[^>]+>', '', clean_content)

                                                                content_chunk = {
                                                                    "id": json_data.get('id', f"chatcmpl-{uuid.uuid4().hex[:12]}"),
                                                                    "object": "chat.completion.chunk",
                                                                    "model": model_mapper.get_display_model_name(current_model),
                                                                    "choices": [{
                                                                        "index": 0,
                                                                        "delta": {"content": clean_content},
                                                                        "finish_reason": None
                                                                    }]
                                                                }
                                                                yield b'data: ' + json.dumps(content_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'

                                                            tool_calls_chunk = {
                                                                "id": json_data.get('id', f"chatcmpl-{uuid.uuid4().hex[:12]}"),
                                                                "object": "chat.completion.chunk",
                                                                "model": model_mapper.get_display_model_name(current_model),
                                                                "choices": [{
                                                                    "index": 0,
                                                                    "delta": {"tool_calls": tool_calls},
                                                                    "finish_reason": None
                                                                }]
                                                            }
                                                            yield b'data: ' + json.dumps(tool_calls_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'

                                                            finish_chunk = {
                                                                "id": json_data.get('id', f"chatcmpl-{uuid.uuid4().hex[:12]}"),
                                                                "object": "chat.completion.chunk",
                                                                "model": model_mapper.get_display_model_name(current_model),
                                                                "choices": [{
                                                                    "index": 0,
                                                                    "delta": {},
                                                                    "finish_reason": "tool_calls"
                                                                }]
                                                            }
                                                            yield b'data: ' + json.dumps(finish_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                            first_chunk_sent = True
                                                        else:
                                                            mapped_data = self._map_model_from_ollama(json.loads(json_str))
                                                            if isinstance(mapped_data, dict) and 'choices' in mapped_data:
                                                                choices = mapped_data.get('choices', [])
                                                                if choices and len(choices) > 0:
                                                                    choices[0]['delta']['content'] = kimi_content_buffer
                                                            yield b'data: ' + json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                            first_chunk_sent = True

                                                        kimi_content_buffer = ""
                                                        kimi_buffering_active = False
                                                        continue
                                                    else:
                                                        continue

                                                if is_kimi_model and '<|tool_calls_section_begin|>' in combined_for_detection:
                                                    kimi_buffering_active = True
                                                    kimi_content_buffer = combined_for_detection
                                                    logger.info(f"[KIMI] Tool call section started, buffering")
                                                    continue

                                                if is_kimi_model:
                                                    marker_start = "<|tool_calls_section_begin|>"
                                                    is_suspicious = False

                                                    if combined_for_detection:
                                                        for i in range(1, len(marker_start)):
                                                            if i > len(combined_for_detection):
                                                                break
                                                            suffix = combined_for_detection[-i:]
                                                            if marker_start.startswith(suffix):
                                                                is_suspicious = True
                                                                break

                                                    if is_suspicious:
                                                        logger.info(f"[KIMI DEBUG] Combined content is suspicious, buffering: {combined_for_detection!r}")
                                                        kimi_suspicion_buffer = combined_for_detection
                                                        continue

                                                # ============================================================
                                                # DEEPSEEK XML TOOL CALL HANDLING
                                                # ============================================================
                                                # DeepSeek models output tool calls as canonical XML:
                                                #   <tool_calls><invoke name="..."><parameter name="...">val</parameter></invoke></tool_calls>
                                                # We buffer these during streaming and convert to OpenAI tool_calls format.
                                                if is_deepseek_model and deepseek_buffering_active:
                                                    deepseek_content_buffer += content + reasoning

                                                    # Check if the tool call section is complete (either format)
                                                    if '</tool_calls>' in deepseek_content_buffer or '</CallMcpTool>' in deepseek_content_buffer:
                                                        logger.info(f"[DEEPSEEK] Tool call section complete, processing buffer ({len(deepseek_content_buffer)} chars)")

                                                        clean_content, tool_calls, has_tool_calls = parse_deepseek_tool_calls(deepseek_content_buffer)

                                                        if has_tool_calls:
                                                            logger.info(f"[DEEPSEEK] Converted {len(tool_calls)} tool call(s) to OpenAI format")

                                                            if clean_content:
                                                                if '<tool_calls>' in clean_content or '</tool_calls>' in clean_content or '<CallMcpTool>' in clean_content or '</CallMcpTool>' in clean_content:
                                                                    clean_content = re.sub(r'</?(?:tool_calls|CallMcpTool)>', '', clean_content).strip()
                                                                content_chunk = {
                                                                    "id": json_data.get('id', f"chatcmpl-{uuid.uuid4().hex[:12]}"),
                                                                    "object": "chat.completion.chunk",
                                                                    "model": model_mapper.get_display_model_name(current_model),
                                                                    "choices": [{
                                                                        "index": 0,
                                                                        "delta": {"content": clean_content},
                                                                        "finish_reason": None
                                                                    }]
                                                                }
                                                                yield b'data: ' + json.dumps(content_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'

                                                            tool_calls_chunk = {
                                                                "id": json_data.get('id', f"chatcmpl-{uuid.uuid4().hex[:12]}"),
                                                                "object": "chat.completion.chunk",
                                                                "model": model_mapper.get_display_model_name(current_model),
                                                                "choices": [{
                                                                    "index": 0,
                                                                    "delta": {"tool_calls": tool_calls},
                                                                    "finish_reason": None
                                                                }]
                                                            }
                                                            yield b'data: ' + json.dumps(tool_calls_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'

                                                            finish_chunk = {
                                                                "id": json_data.get('id', f"chatcmpl-{uuid.uuid4().hex[:12]}"),
                                                                "object": "chat.completion.chunk",
                                                                "model": model_mapper.get_display_model_name(current_model),
                                                                "choices": [{
                                                                    "index": 0,
                                                                    "delta": {},
                                                                    "finish_reason": "tool_calls"
                                                                }]
                                                            }
                                                            yield b'data: ' + json.dumps(finish_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                            first_chunk_sent = True
                                                        else:
                                                            # Parsing failed — emit buffered content as plain text
                                                            mapped_data = self._map_model_from_ollama(json.loads(json_str))
                                                            if isinstance(mapped_data, dict) and 'choices' in mapped_data:
                                                                choices = mapped_data.get('choices', [])
                                                                if choices and len(choices) > 0:
                                                                    choices[0]['delta']['content'] = deepseek_content_buffer
                                                            yield b'data: ' + json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                            first_chunk_sent = True

                                                        deepseek_content_buffer = ""
                                                        deepseek_buffering_active = False
                                                        continue
                                                    else:
                                                        # Still buffering — wait for more chunks
                                                        continue

                                                # DeepSeek: detect start of tool call section (<tool_calls> or <CallMcpTool>)
                                                if is_deepseek_model and ('<tool_calls>' in (content + reasoning) or '<CallMcpTool>' in (content + reasoning)):
                                                    deepseek_buffering_active = True
                                                    deepseek_content_buffer = content + reasoning
                                                    logger.info(f"[DEEPSEEK] Tool call section started, buffering")
                                                    continue

                                                # DeepSeek: suspicion buffering for partial <tool_calls tag
                                                if is_deepseek_model:
                                                    ds_combined = content + reasoning
                                                    is_ds_suspicious = False
                                                    if ds_combined and deepseek_suspicion_buffer:
                                                        ds_combined = deepseek_suspicion_buffer + ds_combined
                                                        deepseek_suspicion_buffer = ""

                                                    if ds_combined:
                                                        for prefix in _DEEPSEEK_TAG_PREFIXES:
                                                            if len(ds_combined) >= len(prefix) and ds_combined.rstrip().endswith(prefix):
                                                                # Check it's not a complete tag already
                                                                if '<tool_calls>' not in ds_combined:
                                                                    is_ds_suspicious = True
                                                                    break
                                                            # Partial match at end of string
                                                            if len(ds_combined) < len(prefix) and prefix.startswith(ds_combined):
                                                                is_ds_suspicious = True
                                                                break

                                                    if is_ds_suspicious:
                                                        logger.info(f"[DEEPSEEK] Suspicious content, buffering: {ds_combined!r}")
                                                        deepseek_suspicion_buffer = ds_combined
                                                        continue
                                                    elif deepseek_suspicion_buffer:
                                                        # Previous suspicion resolved as non-tool content
                                                        # Yield the buffered suspicion as content
                                                        deepseek_suspicion_buffer = ""

                                                # Normal processing
                                                mapped_data = self._map_model_from_ollama(json_data)

                                                # Extract delta for usage tracking
                                                delta_obj = {}
                                                if isinstance(mapped_data, dict) and 'choices' in mapped_data:
                                                    choices = mapped_data.get('choices', [])
                                                    if choices and len(choices) > 0:
                                                        delta_obj = choices[0].get('delta', {})

                                                if content != (delta_obj.get('content', '') or ''):
                                                    if isinstance(mapped_data, dict) and 'choices' in mapped_data:
                                                        choices = mapped_data.get('choices', [])
                                                        if choices and len(choices) > 0:
                                                            if isinstance(choices[0], dict) and 'delta' in choices[0]:
                                                                choices[0]['delta']['content'] = content

                                                if isinstance(mapped_data, dict) and 'usage' in mapped_data:
                                                    usage = mapped_data['usage']
                                                    prompt_tokens += usage.get('prompt_tokens', 0)
                                                    completion_tokens += usage.get('completion_tokens', 0)

                                                should_skip = False

                                                if isinstance(mapped_data, dict) and 'usage' in mapped_data:
                                                    usage_data = mapped_data['usage']
                                                    if isinstance(usage_data, dict):
                                                        prompt_tokens = usage_data.get('prompt_tokens', prompt_tokens)
                                                        completion_tokens = usage_data.get('completion_tokens', completion_tokens)

                                                    choices_list = mapped_data.get('choices', [])
                                                    if not choices_list or len(choices_list) == 0:
                                                        logger.info(f"[PROXY YIELD USAGE] prompt={prompt_tokens}, completion={completion_tokens}, total={prompt_tokens + completion_tokens}")
                                                        yield b'data: ' + json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                        usage_chunk_received = True
                                                        first_chunk_sent = True
                                                        continue

                                                # Tool call buffering and other processing (simplified for brevity)
                                                if isinstance(mapped_data, dict) and 'choices' in mapped_data and len(mapped_data['choices']) > 0:
                                                    choice = mapped_data['choices'][0]
                                                    delta_obj = choice.get('delta', {})
                                                    content_str = delta_obj.get('content')
                                                    fr = choice.get('finish_reason')

                                                    reasoning_str = delta_obj.get('reasoning_content', "") or ""
                                                    has_reasoning = bool(reasoning_str)

                                                    # Thinking tag handling (simplified)
                                                    if content_str is not None:
                                                        _temp_text = think_suspicion + content_str
                                                        think_suspicion = ""
                                                        content_str = ""

                                                        _proc_text = _temp_text
                                                        while _proc_text:
                                                            if not in_thinking:
                                                                idx_start = _proc_text.find("<tool_call>")
                                                                if idx_start != -1:
                                                                    content_str += _proc_text[:idx_start]
                                                                    in_thinking = True
                                                                    _proc_text = _proc_text[idx_start+7:]
                                                                    continue

                                                                idx_end = _proc_text.find("...")
                                                                if idx_end != -1:
                                                                    reasoning_str += _proc_text[:idx_end]
                                                                    has_reasoning = True
                                                                    _proc_text = _proc_text[idx_end+8:]
                                                                    continue

                                                                found_partial = False
                                                                for tag in ["<tool_call>", "..."]:
                                                                    for i in range(len(tag)-1, 0, -1):
                                                                        if _proc_text.endswith(tag[:i]):
                                                                            think_suspicion = _proc_text[-i:]
                                                                            content_str += _proc_text[:-i]
                                                                            _proc_text = ""
                                                                            found_partial = True
                                                                            break
                                                                    if found_partial: break

                                                                if not found_partial:
                                                                    content_str += _proc_text
                                                                    _proc_text = ""
                                                            else:
                                                                idx_end = _proc_text.find("...")
                                                                if idx_end != -1:
                                                                    reasoning_str += _proc_text[:idx_end]
                                                                    has_reasoning = True
                                                                    in_thinking = False
                                                                    _proc_text = _proc_text[idx_end+8:]
                                                                    continue

                                                                idx_start = _proc_text.find("<tool_call>")
                                                                if idx_start != -1:
                                                                    reasoning_str += _proc_text[:idx_start]
                                                                    has_reasoning = True
                                                                    in_thinking = True
                                                                    _proc_text = _proc_text[idx_start+7:]
                                                                    continue

                                                                found_partial = False
                                                                for tag in ["...", "<tool_call>"]:
                                                                    for i in range(len(tag)-1, 0, -1):
                                                                        if _proc_text.endswith(tag[:i]):
                                                                            think_suspicion = _proc_text[-i:]
                                                                            reasoning_str += _proc_text[:-i]
                                                                            has_reasoning = True
                                                                            _proc_text = ""
                                                                            found_partial = True
                                                                            break
                                                                    if found_partial: break

                                                                if not found_partial:
                                                                    reasoning_str += _proc_text
                                                                    _proc_text = ""

                                                    # Build output delta
                                                    out_delta = {}
                                                    if content_str:
                                                        out_delta['content'] = content_str
                                                    if reasoning_str:
                                                        out_delta['reasoning_content'] = reasoning_str
                                                    if delta_obj.get('tool_calls'):
                                                        out_delta['tool_calls'] = delta_obj['tool_calls']

                                                    # Construct output chunk
                                                    out_chunk = {
                                                        "id": mapped_data.get('id', f"chatcmpl-{uuid.uuid4().hex[:12]}"),
                                                        "object": mapped_data.get('object', 'chat.completion.chunk'),
                                                        "model": model_mapper.get_display_model_name(current_model),
                                                        "choices": [{
                                                            "index": 0,
                                                            "delta": out_delta,
                                                            "finish_reason": fr
                                                        }]
                                                    }

                                                    # Check for usage in final chunk
                                                    if 'usage' in mapped_data:
                                                        out_chunk['usage'] = mapped_data['usage']

                                                    yield b'data: ' + json.dumps(out_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                                    first_chunk_sent = True

                                            elif line == b'data: [DONE]':
                                                logger.info(f"[STREAM] Received [DONE] marker")
                                                yield b'data: [DONE]\n\n'
                                                done_marker_sent = True
                                            else:
                                                # Non-SSE format (native Ollama)
                                                logger.info(f"[OLLAMA IN NATIVE] {line.decode('utf-8', errors='replace')[:200]}")
                                                json_str = line.decode('utf-8', errors='replace').strip()
                                                if json_str:
                                                    try:
                                                        json_data = json.loads(json_str)
                                                        mapped_data = self._map_model_from_ollama(json_data)
                                                        yield json.dumps(mapped_data, ensure_ascii=False).encode('utf-8') + b'\n'
                                                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                                                        logger.warning(f"[STREAM] JSON parse error: {e}, line: {line[:100]!r}")
                                                        yield line + b'\n'

                                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                                        logger.warning(f"[STREAM] Buffer parse error: {e}, buffer: {line[:100]!r}")
                                        yield line + b'\n'

                        # Log user activity after streaming
                        if username and current_model:
                            duration_ms = int((time.monotonic() - start_time) * 1000)
                            await self._log_user_activity(
                                username=username,
                                model_name=current_model,
                                request_type=endpoint.replace('/api/', '').replace('/v1/', ''),
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                total_tokens=prompt_tokens + completion_tokens,
                                status_code=200,
                                duration_ms=duration_ms
                            )

                        # Send [DONE] if not already sent
                        if not done_marker_sent and is_openai_endpoint:
                            # DeepSeek flush: if still buffering tool calls at stream end, try to parse
                            if deepseek_buffering_active and deepseek_content_buffer:
                                logger.info(f"[DEEPSEEK] Stream ended while buffering, attempting flush ({len(deepseek_content_buffer)} chars)")
                                clean_content, tool_calls, has_tool_calls = parse_deepseek_tool_calls(deepseek_content_buffer)

                                if has_tool_calls:
                                    logger.info(f"[DEEPSEEK] Flushed {len(tool_calls)} tool call(s)")
                                    if clean_content:
                                        content_chunk = {
                                            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                                            "object": "chat.completion.chunk",
                                            "model": model_mapper.get_display_model_name(current_model),
                                            "choices": [{"index": 0, "delta": {"content": clean_content}, "finish_reason": None}]
                                        }
                                        yield b'data: ' + json.dumps(content_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'

                                    tool_calls_chunk = {
                                        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                                        "object": "chat.completion.chunk",
                                        "model": model_mapper.get_display_model_name(current_model),
                                        "choices": [{"index": 0, "delta": {"tool_calls": tool_calls}, "finish_reason": None}]
                                    }
                                    yield b'data: ' + json.dumps(tool_calls_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'

                                    finish_chunk = {
                                        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                                        "object": "chat.completion.chunk",
                                        "model": model_mapper.get_display_model_name(current_model),
                                        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]
                                    }
                                    yield b'data: ' + json.dumps(finish_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                else:
                                    # Emit as plain text
                                    text_chunk = {
                                        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                                        "object": "chat.completion.chunk",
                                        "model": model_mapper.get_display_model_name(current_model),
                                        "choices": [{"index": 0, "delta": {"content": deepseek_content_buffer}, "finish_reason": None}]
                                    }
                                    yield b'data: ' + json.dumps(text_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'

                                deepseek_content_buffer = ""
                                deepseek_buffering_active = False

                            # DeepSeek: emit any remaining suspicion buffer
                            if deepseek_suspicion_buffer:
                                text_chunk = {
                                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                                    "object": "chat.completion.chunk",
                                    "model": model_mapper.get_display_model_name(current_model),
                                    "choices": [{"index": 0, "delta": {"content": deepseek_suspicion_buffer}, "finish_reason": None}]
                                }
                                yield b'data: ' + json.dumps(text_chunk, ensure_ascii=False).encode('utf-8') + b'\n\n'
                                deepseek_suspicion_buffer = ""

                            logger.info(f"[STREAM END] Sending [DONE] marker (not received from upstream)")
                            yield b'data: [DONE]\n\n'

                        logger.info(f"[STREAM END] Stream complete. Total chunks: {chunk_count}, bytes: {total_bytes}")
                        return  # Successfully completed

                except httpx.RequestError as e:
                    logger.error(f"Network error while streaming to Ollama: {str(e)}")
                    current_model = current_data.get('model', 'unknown')

                    # === NODE-LEVEL RETRY ===
                    # Try the same model on a different node first
                    if attempt < MAX_FAILOVER_RETRIES:
                        current_base_url = current_url.rsplit(endpoint, 1)[0] if endpoint in current_url else base_url
                        tried_nodes.add(current_base_url)

                        new_base_url = await self._select_node_url(
                            current_model, exclude_nodes=list(tried_nodes)
                        )
                        if new_base_url:
                            logger.warning(
                                f"[NODE RETRY] Connection error from {current_base_url}, "
                                f"trying node {new_base_url} for model {current_model}"
                            )
                            current_url = f"{new_base_url}{endpoint}"
                            last_error = e
                            continue
                        logger.info(f"[NODE RETRY] No more nodes available for model {current_model}")

                    # === MODEL-LEVEL FALLBACK ===
                    if original_group and attempt < MAX_FAILOVER_RETRIES:
                        fallback_model = self._get_fallback_model(original_group, current_model, tried_models)

                        if fallback_model and fallback_model not in tried_models:
                            logger.warning(f"[FAILOVER] Connection error, trying fallback model: {fallback_model}")
                            tried_models.add(fallback_model)

                            current_data = original_data.copy() if original_data else {}
                            current_data['model'] = fallback_model
                            current_data = await self._resolve_model_groups(current_data)
                            current_data = self._map_model_to_ollama(current_data)

                            new_base_url = await self._select_node_url(fallback_model)
                            current_url = f"{new_base_url}{endpoint}"
                            if new_base_url:
                                tried_nodes.add(new_base_url)

                            last_error = e
                            continue

                    # No failover available - yield error
                    error_response = {
                        "error": {
                            "message": f"Connection error: {str(e)}",
                            "type": "connection_error",
                            "code": 503
                        }
                    }
                    yield b'data: ' + json.dumps(error_response, ensure_ascii=False).encode('utf-8') + b'\n\n'
                    yield b'data: [DONE]\n\n'
                    return

                except Exception as e:
                    logger.error(f"Unexpected error while streaming: {str(e)}", exc_info=True)
                    error_response = {
                        "error": {
                            "message": f"Unexpected error: {str(e)}",
                            "type": "internal_error",
                            "code": 500
                        }
                    }
                    yield b'data: ' + json.dumps(error_response, ensure_ascii=False).encode('utf-8') + b'\n\n'
                    yield b'data: [DONE]\n\n'
                    return

        return StreamingResponse(
            stream_generator_with_failover(),
            media_type=f"{media_type}; charset=utf-8" if media_type == "text/event-stream" else media_type,
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            }
        )

    async def _non_streaming_with_failover(
        self,
        url: str,
        method: str,
        data: Optional[Dict[str, Any]],
        endpoint: str,
        is_openai_endpoint: bool,
        username: Optional[str],
        original_group: Optional[str],
        tried_models: set,
        tried_nodes: set,
        model_name: Optional[str],
        start_time: float
    ):
        """
        Handle non-streaming requests with automatic failover.

        Two-level failover:
        1. Node retry: On retryable errors (404, 423, 429, 5xx), try same model on another node.
        2. Model fallback: If all nodes exhausted and model is in a group, try next fallback model.

        Args:
            url: Target URL
            method: HTTP method
            data: Request data
            endpoint: API endpoint
            is_openai_endpoint: Whether this is an OpenAI-compatible endpoint
            username: Username for logging
            original_group: Original group name (if model came from a group)
            tried_models: Set of models already tried
            tried_nodes: Set of node base_urls already tried
            model_name: Model name for logging

        Returns:
            Response from Ollama
        """
        last_error = None
        current_url = url
        current_data = data.copy() if data else {}

        for attempt in range(MAX_FAILOVER_RETRIES + 1):
            client = await self._get_http_client()
            current_model = current_data.get('model') or model_name or 'unknown'

            try:
                logger.info(f"Sending request to Ollama: {current_url} (attempt {attempt + 1})")

                if method.upper() == "GET":
                    response = await client.get(current_url)
                elif method.upper() == "POST":
                    response = await client.post(current_url, json=current_data)
                elif method.upper() == "DELETE":
                    response = await client.delete(current_url, json=current_data)
                else:
                    raise HTTPException(status_code=405, detail="Method not allowed")

                # Check response status
                if response.status_code >= 400:
                    error_text = response.text
                    logger.error(f"Ollama error ({response.status_code}): {error_text}")
                    logger.error(f"Request URL: {current_url}")
                    if current_data:
                        logger.error(f"Request data: {json.dumps(current_data, ensure_ascii=False, indent=2)}")

                    # === NODE-LEVEL RETRY ===
                    # Try the same model on a different node first
                    if response.status_code in self.NODE_RETRYABLE_STATUS_CODES and attempt < MAX_FAILOVER_RETRIES:
                        current_base_url = current_url.rsplit(endpoint, 1)[0] if endpoint in current_url else url.rsplit(endpoint, 1)[0]
                        tried_nodes.add(current_base_url)

                        new_base_url = await self._select_node_url(
                            current_model, exclude_nodes=list(tried_nodes)
                        )
                        if new_base_url:
                            logger.warning(
                                f"[NODE RETRY] Error {response.status_code} from {current_base_url}, "
                                f"trying node {new_base_url} for model {current_model}"
                            )
                            current_url = f"{new_base_url}{endpoint}"
                            last_error = HTTPException(
                                status_code=response.status_code,
                                detail=f"Ollama error: {error_text}"
                            )
                            continue
                        logger.info(f"[NODE RETRY] No more nodes available for model {current_model}")

                    # === MODEL-LEVEL FALLBACK ===
                    # Only for 5xx errors, try a different model from the group
                    should_model_failover = (
                        response.status_code >= 500 and
                        original_group and
                        attempt < MAX_FAILOVER_RETRIES
                    )

                    if should_model_failover:
                        fallback_model = self._get_fallback_model(original_group, current_model, tried_models)

                        if fallback_model and fallback_model not in tried_models:
                            logger.warning(f"[FAILOVER] Error {response.status_code}, trying fallback model: {fallback_model}")
                            tried_models.add(fallback_model)

                            # Update data with fallback model
                            current_data = data.copy() if data else {}
                            current_data['model'] = fallback_model
                            current_data = await self._resolve_model_groups(current_data)
                            current_data = self._map_model_to_ollama(current_data)

                            # Select new node URL for fallback
                            new_base_url = await self._select_node_url(fallback_model)
                            current_url = f"{new_base_url}{endpoint}"
                            if new_base_url:
                                tried_nodes.add(new_base_url)

                            last_error = HTTPException(
                                status_code=response.status_code,
                                detail=f"Ollama error: {error_text}"
                            )
                            continue

                    # No failover - raise error
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"Ollama error: {error_text}"
                    )

                # Success! Process response
                try:
                    response_data = response.json()
                except:
                    if username and model_name:
                        await self._log_user_activity(
                            username=username,
                            model_name=model_name,
                            request_type=endpoint.replace('/api/', '').replace('/v1/', ''),
                            prompt_tokens=0,
                            completion_tokens=0,
                            total_tokens=0
                        )
                    return response.text

                # Map model names in response
                if endpoint == "/api/tags":
                    response_data = self._map_models_list(response_data)
                elif endpoint == "/v1/models":
                    response_data = self._map_openai_models_list(response_data)
                elif is_openai_endpoint:
                    response_data = self._map_model_from_ollama(response_data)
                else:
                    response_data = self._map_native_ollama_response(response_data)

                # Extract token usage for logging
                prompt_tokens = 0
                completion_tokens = 0
                total_tokens = 0

                if isinstance(response_data, dict):
                    if 'prompt_eval_count' in response_data:
                        prompt_tokens = response_data.get('prompt_eval_count', 0)
                    if 'eval_count' in response_data:
                        completion_tokens = response_data.get('eval_count', 0)
                    if 'total_duration' in response_data and 'load_duration' in response_data:
                        total_tokens = prompt_tokens + completion_tokens

                    if 'usage' in response_data:
                        usage = response_data['usage']
                        prompt_tokens = usage.get('prompt_tokens', 0)
                        completion_tokens = usage.get('completion_tokens', 0)
                        total_tokens = usage.get('total_tokens', 0)

                # Log user activity
                if username and model_name:
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    await self._log_user_activity(
                        username=username,
                        model_name=model_name,
                        request_type=endpoint.replace('/api/', '').replace('/v1/', ''),
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens or (prompt_tokens + completion_tokens),
                        status_code=200,
                        duration_ms=duration_ms
                    )

                return response_data

            except httpx.RequestError as e:
                logger.error(f"Failed to connect to Ollama: {str(e)}")

                # === NODE-LEVEL RETRY ===
                # Try the same model on a different node first
                if attempt < MAX_FAILOVER_RETRIES:
                    current_base_url = current_url.rsplit(endpoint, 1)[0] if endpoint in current_url else url.rsplit(endpoint, 1)[0]
                    tried_nodes.add(current_base_url)

                    new_base_url = await self._select_node_url(
                        current_model, exclude_nodes=list(tried_nodes)
                    )
                    if new_base_url:
                        logger.warning(
                            f"[NODE RETRY] Connection error from {current_base_url}, "
                            f"trying node {new_base_url} for model {current_model}"
                        )
                        current_url = f"{new_base_url}{endpoint}"
                        last_error = HTTPException(
                            status_code=503,
                            detail=f"Failed to connect to Ollama: {str(e)}"
                        )
                        continue
                    logger.info(f"[NODE RETRY] No more nodes available for model {current_model}")

                # === MODEL-LEVEL FALLBACK ===
                if original_group and attempt < MAX_FAILOVER_RETRIES:
                    fallback_model = self._get_fallback_model(original_group, current_model, tried_models)

                    if fallback_model and fallback_model not in tried_models:
                        logger.warning(f"[FAILOVER] Connection error, trying fallback model: {fallback_model}")
                        tried_models.add(fallback_model)

                        current_data = data.copy() if data else {}
                        current_data['model'] = fallback_model
                        current_data = await self._resolve_model_groups(current_data)
                        current_data = self._map_model_to_ollama(current_data)

                        new_base_url = await self._select_node_url(fallback_model)
                        current_url = f"{new_base_url}{endpoint}"
                        if new_base_url:
                            tried_nodes.add(new_base_url)

                        last_error = HTTPException(
                            status_code=503,
                            detail=f"Failed to connect to Ollama: {str(e)}"
                        )
                        continue

                raise HTTPException(
                    status_code=503,
                    detail=f"Failed to connect to Ollama: {str(e)}"
                )

            except HTTPException:
                raise

            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Proxy error: {str(e)}"
                )

        # All retries exhausted
        duration_ms = int((time.monotonic() - start_time) * 1000)
        error_msg = str(last_error.detail) if last_error else "All fallback attempts failed"
        error_status = last_error.status_code if last_error else 500
        if username and model_name:
            await self._log_user_activity(
                username=username,
                model_name=model_name,
                request_type=endpoint.replace('/api/', '').replace('/v1/', ''),
                status_code=error_status,
                duration_ms=duration_ms,
                error_message=error_msg[:500]
            )
        if last_error:
            raise last_error
        raise HTTPException(
            status_code=500,
            detail="All fallback attempts failed"
        )


# Global proxy instance
ollama_proxy = OllamaProxy()
