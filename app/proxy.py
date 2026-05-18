"""Proxy logic and model routing for Model Maestro"""

from typing import Dict, Any, Optional, List, Tuple, AsyncGenerator
import httpx

# orjson is optional — provides ~8x faster JSON serialization.
# Falls back to standard json if not installed.
try:
    import orjson as _json_lib
    _json_loads = _json_lib.loads
    _json_decode_error = _json_lib.JSONDecodeError

    def _json_dumps(obj: Any, indent: bool = False) -> bytes:
        if indent:
            return _json_lib.dumps(obj, option=_json_lib.OPT_INDENT_2)
        return _json_lib.dumps(obj)
except ImportError:
    import json as _json_lib
    _json_loads = _json_lib.loads
    _json_decode_error = _json_lib.JSONDecodeError

    def _json_dumps(obj: Any, indent: bool = False) -> bytes:
        if indent:
            return _json_lib.dumps(obj, ensure_ascii=False, indent=2).encode('utf-8')
        return _json_lib.dumps(obj, ensure_ascii=False).encode('utf-8')

import json
import logging
import re
import time
import uuid
import xml.etree.ElementTree as ET
from fastapi import HTTPException, Depends
from fastapi.responses import StreamingResponse

from app.config import get_settings, model_mapper, model_group_manager, get_context_length_for_model
from app.user_manager import user_manager
from app.auth import get_current_user
from app.google_proxy import proxy_antigravity_request

logger = logging.getLogger(__name__)

# Maximum retries for failover (will try all fallback members in group)
MAX_FAILOVER_RETRIES = 5

# Client → proxy headers that must not be forwarded to Ollama/vLLM. Upstream gateways
# often apply IP allowlists using X-Forwarded-For / CF-Connecting-Ip; forwarding the
# browser's IP causes 403 "Your IP address is not allowed" even though the TCP connection
# is from this proxy (same behavior as curl from an allowlisted server without these hops).
_CLIENT_HEADERS_BLOCKED_FOR_UPSTREAM = frozenset({
    "authorization",
    "user-agent",
    "postman-token",
    "cookie",
    "accept",
    "content-type",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-forwarded-host",
    "x-real-ip",
    "forwarded",
    "cf-connecting-ip",
    "cf-ray",
    "cf-visitor",
    "cf-ipcountry",
    "cf-worker",
    "cdn-loop",
    "true-client-ip",
    "x-client-ip",
    "x-cluster-client-ip",
    "x-original-forwarded-for",
    "fastly-client-ip",
})


# Streaming activity tracker — background tasks check this to avoid interrupting streams
import asyncio as _asyncio
_active_stream_count = 0
_active_stream_lock = _asyncio.Lock()


async def mark_stream_start():
    global _active_stream_count
    async with _active_stream_lock:
        _active_stream_count += 1


async def mark_stream_end():
    global _active_stream_count
    async with _active_stream_lock:
        _active_stream_count = max(0, _active_stream_count - 1)


def is_streaming_active() -> bool:
    return _active_stream_count > 0


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
        _json_loads(args)
        return True
    except (_json_decode_error, TypeError):
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
        args = _json_loads(args_str)
    except (_json_decode_error, TypeError):
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
        func['arguments'] = _json_dumps(args).decode()

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
            arguments = _json_loads(args_str)
            arguments_str = _json_dumps(arguments).decode()
        except _json_decode_error as e:
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
# DeepSeek uses both ASCII pipe | and Unicode fullwidth pipe ｜ (U+FF5C) in DSML tags
_DSML_PIPE = r'[\|｜]'  # matches | or ｜
_DSML_OPEN_RE = re.compile(r'<' + _DSML_PIPE + r'DSML' + _DSML_PIPE + r'(\w+)([^>]*)>')
_DSML_CLOSE_RE = re.compile(r'</' + _DSML_PIPE + r'DSML' + _DSML_PIPE + r'(\w+)>')
_CANONICAL_OPEN_RE = re.compile(r'<(tool_calls|invoke|parameter)([^>]*)>')
_CANONICAL_CLOSE_RE = re.compile(r'</(tool_calls|invoke|parameter)>')

# Pre-compiled regex patterns for parse_deepseek_tool_calls to avoid
# recompilation on every streaming chunk.
_STC_PATTERN = re.compile(
    r'<tool_call\s+name=["\x27]([^"\x27]+)["\x27][^>]*>(.*?)</tool_call>',
    re.DOTALL
)
_INVOKE_PATTERN = re.compile(
    r'<invoke\s+name=["\x27]([^"\x27]+)["\x27]>(.*?)</invoke>',
    re.DOTALL
)
_PARAM_PATTERN = re.compile(
    r'<parameter\s+name=["\x27]([^"\x27]+)["\x27][^>]*>(.*?)(?:</parameter>)',
    re.DOTALL
)
_TOOL_CALL_INVOKE_PATTERN = re.compile(
    r'<tool_call\s+name=["\x27]([^"\x27]+)["\x27][^>]*>(.*?)</invoke>',
    re.DOTALL
)
_TOOL_CALL_PATTERN = re.compile(r'<tool_call\s*>(.*?)</tool_call\s *>', re.DOTALL)
_TOOL_CALL_PATTERN2 = re.compile(r'<tool_call[^>]*>(.*?)</tool_call\s *>', re.DOTALL)
_ELEM_PATTERN = re.compile(
    r'<(\w+)(?:\s+name=["\x27]([^"\x27]+)["\x27])?[^>]*>(.*?)</\1\s*>',
    re.DOTALL
)
_MCP_PATTERN = re.compile(r'<CallMcpTool>(.*?)</CallMcpTool>', re.DOTALL)
_KV_PATTERN = re.compile(r'(\w+)=(?:"([^"]*)"|\'([^\']*)\'|(\S+))')

# Marker for suspicion buffering — partial tag prefixes
# Include both ASCII pipe and Unicode fullwidth pipe variants
_DEEPSEEK_TAG_PREFIXES = ['<tool_c', '<tool_ca', '<tool_cal', '<tool_call', '<tool_calls',
                           '<|DSML', '<|DSML|', '<|DSML|t', '<|DSML|to', '<|DSML|too',
                           '<|DSML|tool', '<|DSML|tool_',
                           '<｜DSML', '<｜DSML｜', '<｜DSML｜t', '<｜DSML｜to',
                           '<｜DSML｜tool', '<｜DSML｜tool_',
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
        val = _json_loads(text)
        return val
    except (_json_decode_error, ValueError):
        pass

    return text


def parse_deepseek_tool_calls(content: str) -> Tuple[str, List[Dict[str, Any]], bool]:
    """Parse DeepSeek XML tool call format from content and convert to OpenAI format.

    Handles canonical XML (<tool_calls><invoke><parameter>), DSML-prefixed
    (<|DSML|tool_calls><|DSML|invoke>), and singular <tool_call name="...">
    (DeepSeek-v4-pro) formats.

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
            and '<|DSML|tool_calls>' not in content and '</|DSML|tool_calls>' not in content \
            and '<｜DSML｜tool_calls>' not in content and '</｜DSML｜tool_calls>' not in content \
            and '<CallMcpTool>' not in content and '</CallMcpTool>' not in content \
            and '<tool_call' not in content:
        # Also check partial matches at end (streaming suspicion)
        has_prefix = any(content.rstrip().endswith(p) for p in _DEEPSEEK_TAG_PREFIXES)
        if not has_prefix:
            return content, [], False

    # Strip markdown code fences to avoid parsing XML examples
    stripped = _CODE_FENCE_RE.sub('', content)

    # Normalize DSML tags to canonical XML
    normalized = _normalize_dsml_tags(stripped)

    # Determine wrapper format: <tool_calls>, <CallMcpTool>, or <tool_call name="..."> (singular)
    tc_open = '<tool_calls>'
    tc_close = '</tool_calls>'
    mcp_open = '<CallMcpTool>'
    mcp_close = '</CallMcpTool>'
    stc_open = '<tool_call'
    stc_close = '</tool_call>'

    has_tc_open = tc_open in normalized
    has_tc_close = tc_close in normalized
    has_mcp = mcp_open in normalized and mcp_close in normalized
    has_stc_open = stc_open in normalized
    has_stc_close = stc_close in normalized

    has_tc = has_tc_open and has_tc_close
    has_stc = has_stc_open and has_stc_close
    # Also match hybrid formats: e.g. <tool_call> opening with </tool_calls> closing
    has_hybrid = (has_stc_open and has_tc_close) or (has_tc_open and has_stc_close)

    if not has_tc and not has_mcp and not has_stc and not has_hybrid:
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
    elif has_mcp:
        # <CallMcpTool> format — extract each block individually
        start_idx = normalized.find(mcp_open)
        end_idx = normalized.rfind(mcp_close)
        content_before = normalized[:start_idx].strip()
        content_after = normalized[end_idx + len(mcp_close):].strip()
        section_content = normalized[start_idx:end_idx + len(mcp_close)]

    elif has_stc:
        # <tool_call name="..."> (singular) format — extract all such blocks
        stc_matches = list(_STC_PATTERN.finditer(normalized))
        first_start = stc_matches[0].start() if stc_matches else 0
        last_end = stc_matches[-1].end() if stc_matches else 0
        content_before = normalized[:first_start].strip()
        content_after = normalized[last_end:].strip()
        # Build section_content from all matched blocks so downstream parsers can handle it
        section_content = ''.join(m.group(0) for m in stc_matches)

    elif has_hybrid:
        # Hybrid format: e.g. <tool_call name="..."> opening with </tool_calls> closing
        # or <tool_call name="..."> opening with </invoke> closing (mixed DSML)
        # Extract from first tool_call tag to last closing tag
        start_idx = normalized.find(stc_open if has_stc_open else tc_open)
        end_close = tc_close if has_tc_close else stc_close
        end_idx = normalized.rfind(end_close)
        content_before = normalized[:start_idx].strip()
        content_after = normalized[end_idx + len(end_close):].strip()
        section_content = normalized[start_idx:end_idx]

    # Parse tool call blocks - supports formats:
    # 1. <invoke name="..."><parameter name="...">val</parameter></invoke> (canonical XML)
    # 2. Ollama native format (plain text with function name + args)
    # 3. Plain text between tags
    tool_calls = []
    tool_call_index = 0

    # Try <invoke> format first (canonical XML with name attributes)
    invoke_matches = list(_INVOKE_PATTERN.finditer(section_content))

    def _parse_invoke_body(name: str, body: str, tc_index: int) -> Optional[Dict[str, Any]]:
        arguments = {}
        for pm in _PARAM_PATTERN.finditer(body):
            p_name = pm.group(1)
            p_value = pm.group(2).strip()
            if p_value.startswith('<![CDATA[') and p_value.endswith(']]>'):
                p_value = p_value[9:-3]
            try:
                p_value = _json_loads(p_value)
            except (_json_decode_error, ValueError):
                if '<' in p_value and '>' in p_value:
                    try:
                        elem = ET.fromstring(f'<param>{p_value}</param>')
                        p_value = _parse_xml_parameter_value(elem)
                    except ET.ParseError:
                        pass
            arguments[p_name] = p_value
        if not arguments:
            arguments = _parse_plain_text_args(body.strip())
        arguments_str = _json_dumps(arguments).decode()
        tool_call = {
            "index": tc_index,
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": arguments_str
            }
        }
        _sanitize_tool_call_arguments(tool_call)
        return tool_call

    # Also match <tool_call name="...">...</invoke> (hybrid DSML format where
    # tool_call acts as invoke, closed by </invoke>)
    tc_invoke_matches = list(_TOOL_CALL_INVOKE_PATTERN.finditer(section_content))

    # Merge both match lists, sorted by start position, deduplicating overlaps
    all_invoke_matches: list = []
    for m in invoke_matches:
        all_invoke_matches.append(('invoke', m.start(), m))
    for m in tc_invoke_matches:
        # Skip if overlapping with an already-captured invoke match
        if any(abs(m.start() - inv.start()) < 3 for _, _, inv in all_invoke_matches):
            continue
        all_invoke_matches.append(('tc_invoke', m.start(), m))
    all_invoke_matches.sort(key=lambda x: x[1])

    for match_type, _, match in all_invoke_matches:
        func_name = match.group(1)
        body = match.group(2)
        tc = _parse_invoke_body(func_name, body, tool_call_index)
        if tc:
            tool_calls.append(tc)
            tool_call_index += 1
            label = "invoke" if match_type == 'invoke' else "tool_call-invoke"
            logger.info(f"[DEEPSEEK] Parsed {label}: {func_name}({tc['function']['arguments'][:80]}...)")

    if not tool_calls:
        # Try Ollama native tool_call tags
        # Content can be: "FunctionName\n{json_args}" or plain text
        tc_matches = list(_TOOL_CALL_PATTERN.finditer(section_content))

        if not tc_matches:
            tc_matches = list(_TOOL_CALL_PATTERN2.finditer(section_content))

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
                    arguments = _json_loads(args_str)
                except (_json_decode_error, ValueError):
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
                            arguments = _json_loads(args_str)
                        except (_json_decode_error, ValueError):
                            arguments = _parse_plain_text_args(args_str)

            if func_name:
                arguments_str = _json_dumps(arguments).decode()
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

            arguments_str = _json_dumps(arguments).decode()
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

    # Try <tool_call name="..."> (singular with name attribute) format (DeepSeek-v4-pro)
    if not tool_calls:
        stc_pattern = re.compile(
            r'<tool_call\s+name=["\x27]([^"\x27]+)["\x27][^>]*>(.*?)</tool_call>',
            re.DOTALL
        )
        for stc_match in stc_pattern.finditer(section_content):
            func_name = stc_match.group(1).strip()
            stc_body = stc_match.group(2).strip()

            # Parse sub-elements: named elements + <parameter> children
            arguments = {}
            param_idx = 0
            # Match all XML sub-elements: <tagname>value</tagname>
            for elem_match in _ELEM_PATTERN.finditer(stc_body):
                tag_name = elem_match.group(1)
                name_attr = elem_match.group(2)
                elem_value = elem_match.group(3).strip()

                # Try JSON parse on the value
                try:
                    elem_value = _json_loads(elem_value)
                except (or_json_decode_error, ValueError):
                    pass

                if tag_name == 'parameter' and name_attr:
                    # <parameter name="key">value</parameter> — named parameter
                    arguments[name_attr] = elem_value
                elif tag_name == 'parameter':
                    # <parameter>value</parameter> — unnamed positional parameter
                    arguments[str(param_idx)] = elem_value
                    param_idx += 1
                else:
                    # Named element like <path>val</path>, <line>42</line>
                    arguments[tag_name] = elem_value

            if not arguments:
                # No elements found, try plain text arg parsing
                arguments = _parse_plain_text_args(stc_body)

            if func_name:
                arguments_str = _json_dumps(arguments).decode()
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
                logger.info(f'[DEEPSEEK] Parsed singular tool_call: {func_name}({arguments_str[:80]}...)')


    # Try <CallMcpTool> format (DeepSeek-v4-pro MCP-style tool calls)
    if not tool_calls:
        for mcp_match in _MCP_PATTERN.finditer(section_content):
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
                    arguments = _json_loads(arguments_raw)
                    if not isinstance(arguments, dict):
                        arguments = {"input": arguments}
                except (_json_decode_error, ValueError):
                    arguments = _parse_plain_text_args(arguments_raw)

            arguments_str = _json_dumps(arguments).decode()
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
    matches = list(_KV_PATTERN.finditer(text))
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

    async def _resolve_node_id_by_url(self, base_url: str) -> Optional[int]:
        """Look up node_id from base_url using an in-memory cache."""
        if not base_url:
            return None
        try:
            from app.database import async_session_maker
            from app.repositories.node_repository import NodeRepository
            async with async_session_maker() as session:
                node_repo = NodeRepository(session)
                nodes = await node_repo.list_active()
                for n in nodes:
                    if n.base_url == base_url:
                        return n.id
        except Exception:
            pass
        return None

    async def _check_user_node_access(self, username: Optional[str], base_url: str) -> bool:
        """Check if user has access to the node identified by base_url."""
        if not username:
            return True
        node_id = await self._resolve_node_id_by_url(base_url)
        if not node_id:
            return True
        from app.auth import check_node_access
        return await check_node_access(username, node_id)

    @staticmethod
    def _merge_allowed_node_ids(
        group_prefs: Optional[List[int]],
        mapping_restrict: Optional[List[int]],
    ) -> Optional[List[int]]:
        """
        Combine group-member preferred nodes with model-mapping node restrictions.
        None means no constraint from that side.
        """
        if not group_prefs and not mapping_restrict:
            return None
        if not group_prefs:
            return list(dict.fromkeys(mapping_restrict or []))
        if not mapping_restrict:
            return list(dict.fromkeys(group_prefs))
        inter = list(set(group_prefs) & set(mapping_restrict))
        if not inter:
            logger.warning(
                "[LB] Group preferred nodes %s and model-mapping nodes %s have empty intersection; "
                "using group preferred nodes only for routing (mapping real_name still applies only on mapping nodes)",
                group_prefs,
                mapping_restrict,
            )
            return list(dict.fromkeys(group_prefs))

        return list(dict.fromkeys(inter))

    def _prepare_routing_allowed(
        self,
        data: Optional[Dict[str, Any]],
        model_name: Optional[str],
    ) -> Optional[List[int]]:
        """Strip internal preferred-node keys from request body and compute LB restriction."""
        if not isinstance(data, dict):
            mmap = model_mapper.get_restricted_node_ids(model_name) if model_name else None
            logger.debug(f"[LB][RoutingDebug] data is not dict, model={model_name}, mapping_restrict={mmap}")
            return mmap
        legacy = data.pop('_preferred_node_id', None)
        pids = data.pop('_preferred_node_ids', None)
        if legacy is not None and not pids:
            pids = [legacy]

        # Only apply mapping restrictions if the model is actually mapped.
        # Unmapped models should not be restricted by phantom/stale mapping data.
        mmap = None
        if model_name and model_mapper._mapping_lookup_key(model_name) is not None:
            mmap = model_mapper.get_restricted_node_ids(model_name)

        merged = self._merge_allowed_node_ids(pids, mmap)
        logger.info(
            f"[LB][RoutingDebug] model={model_name} _preferred_node_ids={pids} "
            f"mapping_restrict={mmap} merged={merged}"
        )
        return merged

    async def _gather_nodes_for_model_candidates(
        self,
        model_name: str,
        *,
        routing_catalog_names: Optional[List[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """
        Nodes that advertise this model under either the mapped real name or the client/display name.

        Using only the first successful lookup misses nodes whose catalog uses the other spelling
        (e.g. display ``deepseek-v4-pro`` vs real ``deepseek-v4-pro:cloud``).

        ``routing_catalog_names`` adds extra spellings (e.g. other members of the same model group)
        so LB can find any node in the group's union pool that lists an alias.
        """
        from app.node_manager import node_manager

        ordered: List[str] = []
        for raw in [model_name, *(routing_catalog_names or [])]:
            if raw and raw not in ordered:
                ordered.append(raw)

        merged: Dict[int, Dict[str, Any]] = {}
        seen_strings: set[str] = set()
        primary_real = model_mapper.get_real_model_name(model_name)

        for label in ordered:
            real_label = model_mapper.get_real_model_name(label)
            for v in (label, real_label):
                if not v or v in seen_strings:
                    continue
                seen_strings.add(v)
                batch = await node_manager.get_nodes_for_model(v)
                for n in batch:
                    nid = n.get("node_id")
                    if nid is None:
                        continue
                    merged.setdefault(nid, n)

        return list(merged.values()), primary_real

    async def _select_node_url(
        self,
        model_name: str,
        exclude_nodes: Optional[List[str]] = None,
        exclude_scoped: bool = False,
        allowed_node_ids: Optional[List[int]] = None,
        routing_catalog_names: Optional[List[str]] = None,
    ) -> Tuple[str, Optional[str], str, Optional[Dict[str, Any]], bool]:
        """
        Select the best node URL for a model using load balancing.
        Uses Redis cache first (zero DB hits per request).

        Args:
            model_name: The model name to route
            exclude_nodes: List of base_url strings to exclude (already tried nodes)
            exclude_scoped: If True, skip nodes with scoped_models=True (normal load balancing)

        Returns:
            Tuple of (base_url, api_key, node_type, headers). Falls back to self.base_url if load balancing is not configured or no nodes available.
        """
        try:
            from app.node_manager import node_manager
            from app.load_balancer import load_balancer

            nodes, real_model_name = await self._gather_nodes_for_model_candidates(
                model_name,
                routing_catalog_names=routing_catalog_names,
            )

            # Fallback for unmapped/ungrouped models: scan all active healthy nodes
            has_mapping = model_mapper._mapping_lookup_key(model_name) is not None if model_name else False
            is_grouped = model_group_manager.is_group(model_name) if model_name else False

            if not nodes and not has_mapping and not is_grouped:
                # Before falling back, check if model exists in DB but is deactivated
                try:
                    from app.database import async_session_maker
                    from app.repositories.node_repository import NodeModelRepository
                    async with async_session_maker() as session:
                        model_repo = NodeModelRepository(session)
                        if await model_repo.model_exists(model_name):
                            if not await model_repo.has_any_available(model_name):
                                logger.debug(f"[LB] Unmapped model '{model_name}' exists but is deactivated in DB — refusing fallback")
                                return "", None, 'ollama', None, False
                except Exception:
                    pass

                logger.debug(f"[LB] Model '{model_name}' is unmapped/ungrouped and not in any catalog. Falling back to all active nodes.")
                nodes = await node_manager.get_all_active_healthy_nodes()
                logger.debug(f"[LB] Fallback returned {len(nodes)} active healthy node(s) for unmapped model '{model_name}'")

            if not nodes:
                # No nodes have this model
                # For mapped/grouped models, don't fall back to default URL —
                # the model is genuinely unavailable (is_available=False or no healthy node)
                if has_mapping or is_grouped:
                    logger.debug(f"[LB] Mapped/grouped model '{model_name}' has no available nodes, refusing fallback")
                    return "", None, 'ollama', None, False

                # For unmapped models: if the model exists in DB but is marked unavailable,
                # do NOT fall back to the default URL
                try:
                    from app.database import async_session_maker
                    from app.repositories.node_repository import NodeModelRepository
                    async with async_session_maker() as session:
                        model_repo = NodeModelRepository(session)
                        if await model_repo.model_exists(model_name):
                            if not await model_repo.has_any_available(model_name):
                                logger.debug(f"[LB] Unmapped model '{model_name}' exists but is deactivated — refusing fallback")
                                return "", None, 'ollama', None, False
                except Exception:
                    pass

                if exclude_nodes and self.base_url in exclude_nodes:
                    logger.debug(f"[LB] No nodes found for model {model_name} and default URL excluded")
                    return "", None, 'ollama', None, False
                logger.debug(f"[LB] No nodes found for model {model_name}, using default URL")
                return self.base_url, None, 'ollama', None, False

            if allowed_node_ids:
                allow = set(allowed_node_ids)
                filtered_allow = [n for n in nodes if n.get('node_id') in allow]
                if filtered_allow:
                    nodes = filtered_allow
                    logger.debug(f"[LB] Restricted routing to {len(nodes)} allowed node(s) for model {model_name}")
                else:
                    # For unmapped/ungrouped models, don't 503; widen the pool instead
                    if not has_mapping and not is_grouped:
                        logger.warning(
                            f"[LB] allowed_node_ids={allowed_node_ids} produced no candidates for unmapped/ungrouped model "
                            f"'{model_name}' (mapped real '{real_model_name}'). Using all {len(nodes)} found node(s) instead."
                        )
                    else:
                        logger.error(
                            f"[LB] allowed_node_ids={allowed_node_ids} produced no candidates for model "
                            f"'{model_name}' (mapped real '{real_model_name}'); refusing to widen pool"
                        )
                        raise HTTPException(
                            status_code=503,
                            detail=(
                                "Routing is restricted to specific nodes for this model, but none of those nodes "
                                f"currently advertise '{model_name}' or mapped real name '{real_model_name}'. "
                                "Sync node catalogs so preferred nodes list the same spelling your mapping uses, "
                                "or adjust mapping / preferred nodes."
                            ),
                        )

            # Filter out scoped nodes when doing normal load balancing
            if exclude_scoped:
                before_count = len(nodes)
                nodes = [n for n in nodes if not n.get('scoped_models')]
                filtered_count = before_count - len(nodes)
                if filtered_count:
                    logger.debug(f"[LB] Filtered out {filtered_count} scoped nodes for model {model_name}")

            if not nodes:
                if exclude_nodes and self.base_url in exclude_nodes:
                    logger.debug(f"[LB] No non-scoped nodes found for model {model_name} and default URL excluded")
                    return "", None, 'ollama', None, False
                logger.debug(f"[LB] All nodes for model {model_name} are scoped, using default URL")
                return self.base_url, None, 'ollama', None, False

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
                        logger.debug(f"[LB] All nodes excluded for model {model_name}, trying default URL")
                        return self.base_url, None, 'ollama', None, False
                    logger.debug(f"[LB] All nodes excluded for model {model_name}, no alternatives")
                    return "", None, 'ollama', None, False

            # Select best node using load balancer (Redis-first, no session)
            selected_node = await load_balancer.select_node(
                nodes, strategy="least_loaded"
            )

            if selected_node:
                node_name = selected_node.get('node_name') or selected_node.get('name', 'unknown')
                node_base_url = selected_node.get('base_url')
                node_api_key = selected_node.get('api_key')
                node_type = selected_node.get('node_type', 'ollama')
                node_headers = selected_node.get('headers')
                node_auto_cookie_refresh = selected_node.get('auto_cookie_refresh', False)
                logger.debug(f"[LB] Selected node {node_name} ({node_type}) for model {model_name}")
                if node_base_url:
                    return node_base_url, node_api_key, node_type, node_headers, node_auto_cookie_refresh

            return self.base_url, None, 'ollama', None, False

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[LB] Error selecting node for model '{model_name}': {e!r}, falling back to default URL", exc_info=True)
            return self.base_url, None, 'ollama', None, False
    
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
                timeout=httpx.Timeout(1200.0, connect=30.0, read=1200.0, write=30.0),
                limits=limits,
                http2=False  # HTTP/2 disabled for streaming stability
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
            resolved_model, preferred_node_ids = await model_group_manager.resolve_model_with_metadata(original_model, data_copy)
            if resolved_model != original_model:
                logger.info(f"[ModelGroup] Resolved group '{original_model}' -> '{resolved_model}' (preferred_nodes={preferred_node_ids})")
            data_copy['model'] = resolved_model
            if preferred_node_ids:
                data_copy['_preferred_node_ids'] = preferred_node_ids

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

    @staticmethod
    def _strip_images_from_messages(data: Dict[str, Any], model_name: str) -> Dict[str, Any]:
        """
        Remove image content from messages when the target model doesn't support vision.

        When a user switches from a vision-capable model to a non-vision model mid-conversation,
        the chat history may still contain image_url parts. This strips them out to prevent
        400 errors from Ollama models that don't support image input.
        """
        messages = data.get("messages")
        if not messages or not isinstance(messages, list):
            return data

        capabilities = model_mapper.get_capabilities(model_name)
        # Only strip if capabilities are explicitly configured and don't include vision
        # If capabilities are None (unconfigured), we can't know, so don't strip
        if capabilities is None or "vision" in capabilities:
            return data

        modified = False
        cleaned_messages = []

        for msg in messages:
            content = msg.get("content")
            if not content:
                cleaned_messages.append(msg)
                continue

            # String content: strip base64 data URLs
            if isinstance(content, str):
                if "data:image/" in content:
                    cleaned_messages.append({**msg, "content": "[image removed]"})
                    modified = True
                else:
                    cleaned_messages.append(msg)
                continue

            # List content (OpenAI format): remove image_url parts and base64 in text parts
            if isinstance(content, list):
                new_parts = []
                has_image = False
                for part in content:
                    if not isinstance(part, dict):
                        new_parts.append(part)
                        continue

                    if part.get("type") == "image_url":
                        has_image = True
                        continue

                    if part.get("type") == "text":
                        text = part.get("text", "")
                        if isinstance(text, str) and "data:image/" in text:
                            new_parts.append({"type": "text", "text": "[image removed]"})
                            has_image = True
                            continue

                    if "image" in part:
                        has_image = True
                        continue

                    new_parts.append(part)

                if has_image:
                    if not new_parts:
                        new_parts = [{"type": "text", "text": "[image removed]"}]
                    modified = True
                    cleaned_messages.append({**msg, "content": new_parts})
                else:
                    cleaned_messages.append(msg)
                continue

            cleaned_messages.append(msg)

        if modified:
            logger.info(f"[STRIP] Removed image content from messages for non-vision model '{model_name}'")
            return {**data, "messages": cleaned_messages}

        return data

    def _map_model_to_ollama(
        self,
        data: Dict[str, Any],
        selected_node_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Map model names in request data from client format to Ollama format

        Note: This should be called AFTER _resolve_model_groups to ensure
        groups are resolved first. Group resolution must happen before mapping
        because it needs to know the actual model name.

        Args:
            data: Request data with potential model field
            selected_node_id: Outbound node id when applying node-scoped mappings

        Returns:
            Modified data with real model names
        """
        if not data:
            return data

        data_copy = data.copy()

        # Handle 'model' field
        if 'model' in data_copy:
            data_copy['model'] = model_mapper.get_real_model_name_for_node(
                data_copy['model'], selected_node_id
            )

        # Handle 'name' field (used in show, delete, pull, push)
        if 'name' in data_copy:
            data_copy['name'] = model_mapper.get_real_model_name_for_node(
                data_copy['name'], selected_node_id
            )

        # Handle 'source' and 'destination' fields (used in copy)
        if 'source' in data_copy:
            data_copy['source'] = model_mapper.get_real_model_name_for_node(
                data_copy['source'], selected_node_id
            )
        if 'destination' in data_copy:
            data_copy['destination'] = model_mapper.get_real_model_name_for_node(
                data_copy['destination'], selected_node_id
            )

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

    async def _map_model_from_ollama(self, data: Any) -> Any:
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
                            if reasoning and ('<|tool_calls_section_begin|>' in reasoning or '<tool_calls>' in reasoning or '<|DSML|tool_calls>' in reasoning or '<｜DSML｜tool_calls>' in reasoning or '<CallMcpTool>' in reasoning or '<tool_call' in reasoning):
                                # Route to appropriate parser based on format
                                if '<|tool_calls_section_begin|>' in reasoning:
                                    clean_reasoning, tool_calls_from_reasoning, has_tool_calls = await _asyncio.to_thread(parse_kimi_tool_calls, reasoning)
                                    parser_name = 'KIMI'
                                else:
                                    clean_reasoning, tool_calls_from_reasoning, has_tool_calls = await _asyncio.to_thread(parse_deepseek_tool_calls, reasoning)
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
                                clean_content, tool_calls, has_tool_calls = await _asyncio.to_thread(parse_kimi_tool_calls, content)

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
                            if content and (('<tool_calls>' in content and '</tool_calls>' in content) or ('<|DSML|tool_calls>' in content) or ('<｜DSML｜tool_calls>' in content) or ('<CallMcpTool>' in content and '</CallMcpTool>' in content) or ('<tool_call' in content and '</tool_call>' in content)):
                                clean_content, tool_calls, has_tool_calls = await _asyncio.to_thread(parse_deepseek_tool_calls, content)

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
                            if reasoning and ('<|tool_calls_section_begin|>' in reasoning or '<tool_calls>' in reasoning or '<|DSML|tool_calls>' in reasoning or '<｜DSML｜tool_calls>' in reasoning or '<CallMcpTool>' in reasoning or '<tool_call' in reasoning):
                                # Route to appropriate parser based on format
                                if '<|tool_calls_section_begin|>' in reasoning:
                                    clean_reasoning, tool_calls_from_reasoning, has_tool_calls = await _asyncio.to_thread(parse_kimi_tool_calls, reasoning)
                                    parser_name = 'KIMI'
                                else:
                                    clean_reasoning, tool_calls_from_reasoning, has_tool_calls = await _asyncio.to_thread(parse_deepseek_tool_calls, reasoning)
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
                                clean_content, tool_calls, has_tool_calls = await _asyncio.to_thread(parse_kimi_tool_calls, content)

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
                            if content and (('<tool_calls>' in content and '</tool_calls>' in content) or ('<|DSML|tool_calls>' in content) or ('<｜DSML｜tool_calls>' in content) or ('<CallMcpTool>' in content and '</CallMcpTool>' in content) or ('<tool_call' in content)):
                                clean_content, tool_calls, has_tool_calls = await _asyncio.to_thread(parse_deepseek_tool_calls, content)

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
    
    def _detect_request_source(self, endpoint: str) -> str:
        """Detect request source/client from endpoint path."""
        if endpoint.startswith('/openclaw'):
            return 'OpenClaw'
        if endpoint.startswith('/claude'):
            return 'Claude'
        if endpoint.startswith('/grafana'):
            return 'Grafana'
        if endpoint.startswith('/api/'):
            return 'Ollama Native'
        if endpoint.startswith('/v1/'):
            return 'OpenAI-Compatible'
        return 'Unknown'

    def _parse_node_prefix(self, model_name: str):
        """Parse node-scoped model name prefix.

        Format: node:{code}:{actual_model_name}
        Example: node:trmix:kimi-k2.6:latest -> ("trmix", "kimi-k2.6:latest")

        Returns: (node_code, actual_model) or (None, model_name) if no prefix.
        """
        if isinstance(model_name, str) and model_name.startswith('node:'):
            parts = model_name.split(':', 2)
            if len(parts) >= 3:
                return parts[1], parts[2]
        return None, model_name

    async def _resolve_selected_node_id(self, base_url: Optional[str]) -> Optional[int]:
        """Resolve DB node id from active node's ``base_url`` (LB-selected URL)."""
        if not base_url:
            return None
        try:
            from app.database import async_session_maker
            from app.repositories.node_repository import NodeRepository

            async with async_session_maker() as session:
                node_repo = NodeRepository(session)
                nodes = await node_repo.list_active()
                for n in nodes:
                    if n.base_url == base_url:
                        return n.id
        except Exception as e:
            logger.warning(f"[LB] Error resolving node id for base_url: {e}")
        return None

    async def _rebind_body_to_node(
        self,
        body: Dict[str, Any],
        routing_snapshot: Dict[str, str],
        base_url: Optional[str],
    ) -> Dict[str, Any]:
        """
        After LB switches nodes, remap ``model`` / ``name`` / ``source`` / ``destination``
        from pre-mapping snapshot names using node-aware model mappings.
        """
        if not routing_snapshot:
            return body
        nid = await self._resolve_selected_node_id(base_url)
        remap_src = body.copy()
        for field in ('model', 'name', 'source', 'destination'):
            if field in routing_snapshot:
                remap_src[field] = routing_snapshot[field]
        out = self._map_model_to_ollama(remap_src, nid)
        mapped_for_strip = out.get('model') or out.get('name')
        if mapped_for_strip:
            out = self._strip_images_from_messages(out, mapped_for_strip)
        return out

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
        error_message: str = None,
        source: str = None,
        url_path: str = None
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
            source: Request source/client identifier
            url_path: Full request URL path
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
            error_message=error_message,
            source=source,
            url_path=url_path
        )
    
    async def proxy_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        stream: bool = False,
        username: Optional[str] = None,
        client_headers: Optional[Dict[str, str]] = None,
        source: Optional[str] = None,
        url_path: Optional[str] = None,
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

        # Normalize reasoning values: Ollama rejects 'minimal' in both body and body.options
        if data and isinstance(data, dict):
            for key in ("reasoning", "reasoning_effort"):
                if data.get(key) == "minimal":
                    data[key] = "low"
                    logger.debug(f"[Normalize] {key} 'minimal' -> 'low' in request body")
                options = data.get("options")
                if isinstance(options, dict) and options.get(key) == "minimal":
                    options[key] = "low"
                    logger.debug(f"[Normalize] {key} 'minimal' -> 'low' in options")

        # Track original model name and group for failover
        original_model = None
        original_group = None
        tried_models: set = set()
        tried_nodes: set = set()  # Track node base_urls already tried

        # Determine if this request came through a model group.
        # Group requests bypass node and node-model access controls because
        # the group is an abstraction layer — the user does not target a specific node.
        is_group_request: bool = False

        # Step 1: Resolve model groups (if model name is a group, pick appropriate member)
        if data:
            # Store original model name before resolution
            original_model = data.get('model') or data.get('name')
            data = await self._resolve_model_groups(data)

            # Check if original model was a group (for failover)
            if original_model:
                original_group = self._find_group_for_model(original_model)
                if original_group:
                    is_group_request = True

        # Extract model name for node selection and logging
        model_name = None
        if data and isinstance(data, dict):
            model_name = data.get('model') or data.get('name')
            if model_name:
                tried_models.add(model_name)

        # Node-scoped routing via model name prefix (node:code:model)
        node_code: Optional[str] = None
        node_scoped_model: Optional[str] = None
        if model_name and isinstance(model_name, str):
            node_code, actual_model = self._parse_node_prefix(model_name)
            if node_code:
                try:
                    from app.database import async_session_maker
                    from app.repositories.node_repository import NodeRepository
                    async with async_session_maker() as session:
                        node_repo = NodeRepository(session)
                        node = await node_repo.get_by_code(node_code)
                        if not node:
                            raise HTTPException(
                                status_code=404,
                                detail=f"Node with code '{node_code}' not found"
                            )
                        # Inject preferred node(s) and replace model name
                        data['_preferred_node_ids'] = [node.id]
                        if 'model' in data:
                            data['model'] = actual_model
                        elif 'name' in data:
                            data['name'] = actual_model
                        model_name = actual_model
                        node_scoped_model = actual_model
                        tried_models.discard(f"node:{node_code}:{actual_model}")
                        tried_models.add(actual_model)
                        logger.debug(f"[LB] Node-scoped routing: code='{node_code}' -> node='{node.name}', model='{actual_model}'")
                except HTTPException:
                    raise
                except Exception as e:
                    logger.warning(f"[LB] Error looking up node by code '{node_code}': {e}")
                    raise HTTPException(status_code=500, detail=f"Error resolving node code '{node_code}'")

        # Determine if request is node-scoped for scoped model filtering
        is_node_scoped = bool(node_code)

        routing_catalog_names: Optional[List[str]] = None
        if original_group:
            rc = list(model_group_manager.get_member_catalog_names(original_group))
            if original_group in model_mapper.get_all_mappings() and original_group not in rc:
                rc.append(original_group)
            routing_catalog_names = rc if rc else None

        routing_allowed_node_ids = self._prepare_routing_allowed(data, model_name)

        base_url = None
        api_key = None
        node_type = 'ollama'
        node_headers = None

        base_url, api_key, node_type, node_headers, auto_cookie_refresh = await self._select_node_url(
            model_name or '',
            exclude_scoped=not is_node_scoped and not is_group_request,
            allowed_node_ids=routing_allowed_node_ids,
            routing_catalog_names=routing_catalog_names,
        )

        # Block if no node found (model unavailable)
        if not base_url:
            logger.warning(f"[Proxy] No available node found for model '{model_name}' — model may be unavailable")
            duration_ms = int((time.monotonic() - start_time) * 1000)
            if username and model_name:
                await self._log_user_activity(
                    username=username,
                    model_name=model_name,
                    request_type=endpoint.replace('/api/', '').replace('/v1/', '') if endpoint != '/api/chat' else 'chat/completions',
                    status_code=404,
                    duration_ms=duration_ms,
                    error_message=f"Model '{model_name}' is not available",
                    source=source or self._detect_request_source(endpoint),
                    url_path=url_path or endpoint
                )
            raise HTTPException(
                status_code=404,
                detail=f"Model '{model_name}' is not available"
            )

        url = f"{base_url}{endpoint}"
        if base_url:
            tried_nodes.add(base_url)

        # Resolve node_id for access control and node-scoped model mappings
        selected_node_id = await self._resolve_selected_node_id(base_url)

        # ============================================================
        # NODE ACCESS CONTROL
        # ============================================================
        # Group requests bypass node-level access control because the user
        # does not target a specific node — the system picks one via the group.
        if not is_group_request and username and selected_node_id:
            from app.auth import check_node_access
            if not await check_node_access(username, selected_node_id):
                logger.warning(f"[Access] User '{username}' denied access to node {selected_node_id}")
                raise HTTPException(status_code=403, detail="Access denied to this node")

        # Pre-mapping snapshot (group-resolved client-visible names) for LB failover remaps
        routing_snapshot: Dict[str, str] = {}
        if data and isinstance(data, dict):
            for snap_key in ('model', 'name', 'source', 'destination'):
                v = data.get(snap_key)
                if isinstance(v, str):
                    routing_snapshot[snap_key] = v

        # Step 2: Map model names (display_name -> real_name)
        mapped_model_name = None
        if data:
            data = self._map_model_to_ollama(data, selected_node_id)
            # Restore node-scoped model name to avoid mapping it to a different real name
            if node_scoped_model and isinstance(data, dict):
                if 'model' in data:
                    data['model'] = node_scoped_model
                elif 'name' in data:
                    data['name'] = node_scoped_model
            mapped_model_name = data.get('model') or data.get('name')

        # Node-model access check
        # Group requests also bypass node-model access control for the same reason.
        if not is_group_request and username and selected_node_id and mapped_model_name:
            from app.auth import check_node_model_access
            if not await check_node_model_access(username, selected_node_id, mapped_model_name):
                logger.warning(f"[Access] User '{username}' denied access to model '{mapped_model_name}' on node {selected_node_id}")
                raise HTTPException(status_code=403, detail="Access denied to this model on this node")

        # ============================================================
        # ANTIGRAVITY (Google v1internal) ROUTING
        # ============================================================
        if node_type == 'antigravity' and isinstance(data, dict):
            # Antigravity only supports OpenAI-compatible chat completions
            if endpoint in ('/v1/chat/completions', '/cursor/chat/completions', '/v1/completions'):
                logger.info(f"[Antigravity] Routing request to Google v1internal for model={model_name}")
                # Retrieve node info including oauth_tokens and project_id
                node_info = None
                try:
                    from app.database import async_session_maker
                    from app.repositories.node_repository import NodeRepository
                    async with async_session_maker() as session:
                        node_repo = NodeRepository(session)
                        # Find the node by base_url
                        nodes = await node_repo.list_active()
                        for n in nodes:
                            if n.base_url == base_url and n.node_type == 'antigravity':
                                node_info = n
                                break
                except Exception as e:
                    logger.warning(f"[Antigravity] Failed to look up node info: {e}")

                if not node_info or not node_info.oauth_tokens:
                    raise HTTPException(status_code=500, detail="Antigravity node missing OAuth tokens")

                return await proxy_antigravity_request(
                    data=data,
                    stream=stream,
                    endpoint=endpoint,
                    base_url=base_url,
                    oauth_tokens=node_info.oauth_tokens,
                    project_id=node_info.project_id,
                    node_headers=node_headers,
                    model_name=model_name or data.get('model', 'unknown'),
                    username=username,
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Antigravity nodes do not support endpoint: {endpoint}"
                )

        # ============================================================
        # BEDROCK ROUTING
        # ============================================================
        if node_type == 'bedrock' and isinstance(data, dict):
            if endpoint in ('/v1/chat/completions', '/cursor/chat/completions'):
                logger.info(f"[Bedrock] Routing request to AWS Bedrock for model={model_name}")
                # Retrieve node info including AWS credentials
                node_info = None
                try:
                    from app.database import async_session_maker
                    from app.repositories.node_repository import NodeRepository
                    async with async_session_maker() as session:
                        node_repo = NodeRepository(session)
                        nodes = await node_repo.list_active()
                        for n in nodes:
                            if n.base_url == base_url and n.node_type == 'bedrock':
                                node_info = n
                                break
                except Exception as e:
                    logger.warning(f"[Bedrock] Failed to look up node info: {e}")

                if not node_info or not node_info.api_key or not node_info.aws_secret_key or not node_info.aws_region:
                    raise HTTPException(status_code=500, detail="Bedrock node missing AWS credentials or region")

                from app.bedrock_proxy import proxy_bedrock_request
                return await proxy_bedrock_request(
                    data=data,
                    stream=stream,
                    endpoint=endpoint,
                    base_url=base_url,
                    access_key=node_info.api_key,
                    secret_key=node_info.aws_secret_key,
                    region=node_info.aws_region,
                    session_token=node_info.aws_session_token,
                    model_name=mapped_model_name or data.get('model', 'unknown'),
                    username=username,
                    node_headers=node_headers,
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Bedrock nodes only support chat completions endpoint, got: {endpoint}"
                )

        # vLLM nodes don't support Ollama-specific parameters
        if node_type == 'vllm' and isinstance(data, dict):
            data = data.copy()
            data.pop('keep_alive', None)
            if isinstance(data.get('options'), dict):
                data['options'] = {k: v for k, v in data['options'].items() if k not in ('num_ctx', 'num_gpu', 'num_thread')}
                if not data['options']:
                    data.pop('options', None)

            # NVIDIA NIM endpoints are very strict about params (Kilo-Org/kilocode#9652).
            # Strip unsupported params to avoid 422/500 errors.
            is_nvidia = base_url and ('nvidia.com' in base_url or 'integrate.api.nvidia.com' in base_url)
            if is_nvidia:
                nvidia_unsupported = ('tools', 'tool_choice', 'stream_options', 'presence_penalty',
                                      'frequency_penalty', 'logit_bias', 'logprobs', 'top_logprobs',
                                      'response_format', 'parallel_tool_calls', 'store', 'metadata',
                                      'prediction', 'modalities', 'audio', 'service_tier', 'user')
                stripped_nvidia = [k for k in nvidia_unsupported if k in data]
                for k in stripped_nvidia:
                    data.pop(k, None)
                # Also remove the entire 'options' dict — NVIDIA does not recognise it
                if 'options' in data:
                    data.pop('options', None)
                    stripped_nvidia.append('options')
                if stripped_nvidia:
                    logger.info(f"[vLLM-NVIDIA] Stripped unsupported params for NVIDIA: {', '.join(stripped_nvidia)}")

            # vLLM needs max_tokens to avoid "0 output tokens" errors when input is long.
            # If not provided, default to a reasonable value.
            # Skip for NVIDIA NIM endpoints — known to crash with certain models (e.g. Kimi K2.6)
            # when max_tokens is injected. See: CherryHQ/cherry-studio#14868
            # Also skip when node headers contain x-skip-max-tokens-injection (for endpoints
            # like Agent Router that reject injected max_tokens).
            skip_max_tokens = (
                is_nvidia or
                (node_headers and str(node_headers.get('x-skip-max-tokens-injection', '')).lower() in ('true', '1', 'yes'))
            )
            # Agent Router specifically rejects injected max_tokens with content-blocked error.
            if not skip_max_tokens and base_url and 'agentrouter' not in base_url and 'max_tokens' not in data and 'max_completion_tokens' not in data:
                data['max_tokens'] = 4096
                logger.info(f"[vLLM] Default max_tokens=4096 injected for model {model_name}")

        # Step 3: Strip images from messages if model doesn't support vision
        # Use the mapped (real) model name for capability lookup
        mapped_model = data.get('model') or data.get('name') if data else None
        if data and mapped_model:
            data = self._strip_images_from_messages(data, mapped_model)

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
                    api_key=api_key,
                    start_time=start_time,
                    node_type=node_type,
                    node_headers=node_headers,
                    auto_cookie_refresh=auto_cookie_refresh,
                    exclude_scoped=not is_node_scoped and not is_group_request,
                    bypass_node_access=is_group_request,
                    client_headers=client_headers,
                    allowed_node_ids=routing_allowed_node_ids,
                    routing_snapshot=routing_snapshot,
                    routing_catalog_names=routing_catalog_names,
                    source=source,
                    url_path=url_path,
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
                api_key=api_key,
                start_time=start_time,
                node_type=node_type,
                node_headers=node_headers,
                auto_cookie_refresh=auto_cookie_refresh,
                exclude_scoped=not is_node_scoped and not is_group_request,
                bypass_node_access=is_group_request,
                client_headers=client_headers,
                allowed_node_ids=routing_allowed_node_ids,
                routing_snapshot=routing_snapshot,
                routing_catalog_names=routing_catalog_names,
                source=source,
                url_path=url_path,
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
        api_key: Optional[str],
        start_time: float,
        node_type: str = 'ollama',
        node_headers: Optional[Dict[str, Any]] = None,
        auto_cookie_refresh: bool = False,
        exclude_scoped: bool = False,
        bypass_node_access: bool = False,
        client_headers: Optional[Dict[str, str]] = None,
        allowed_node_ids: Optional[List[int]] = None,
        routing_snapshot: Optional[Dict[str, str]] = None,
        routing_catalog_names: Optional[List[str]] = None,
        source: Optional[str] = None,
        url_path: Optional[str] = None,
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
        rsnap = routing_snapshot if routing_snapshot is not None else {}

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
            current_api_key = api_key
            current_headers = node_headers

            for attempt in range(MAX_FAILOVER_RETRIES + 1):
                client = await self._get_http_client()
                current_model = current_data.get('model', 'unknown')

                logger.debug(f"[STREAM START] Attempt {attempt + 1}: Sending streaming request to {current_url}")
                logger.debug(f"[STREAM START] Model: {current_model}, OpenAI endpoint: {is_openai_endpoint}")
                logger.debug(f"[STREAM START] max_tokens: {current_data.get('max_tokens', 'not set')}, temperature: {current_data.get('temperature', 'not set')}")

                try:
                    if current_data.get("tools"):
                        logger.debug(f"[STREAM START] Tools provided: {[t.get('function', {}).get('name') for t in current_data.get('tools', [])]}")

                    request_headers = {}
                    if client_headers:
                        # Filter: never forward client IP / CDN hop headers to upstream (see module doc).
                        request_headers.update({
                            k: v for k, v in client_headers.items()
                            if k.lower() not in _CLIENT_HEADERS_BLOCKED_FOR_UPSTREAM
                        })
                    if current_headers:
                        # Filter out internal proxy directives (not for upstream)
                        upstream_headers = {k: v for k, v in current_headers.items() if k.lower() != 'x-skip-max-tokens-injection'}
                        request_headers.update(upstream_headers)
                    if current_api_key:
                        request_headers["Authorization"] = f"Bearer {current_api_key}"

                    # Log full outgoing request body and headers for debugging (debug level only)
                    if current_data:
                        _provider_label = 'vLLM' if node_type == 'vllm' else 'Ollama'
                        logger.debug(f"[OUTGOING] {_provider_label} request body: {_json_dumps(current_data, indent=True).decode()}")
                    logger.debug(f"[OUTGOING] request headers: {request_headers}")

                    async with client.stream("POST", current_url, json=current_data, headers=request_headers) as resp:
                        provider_label = 'vLLM' if node_type == 'vllm' else 'Ollama'
                        if resp.status_code >= 400:
                            logger.warning(f"[STREAM] {provider_label} response status: {resp.status_code}")
                        else:
                            logger.debug(f"[STREAM] {provider_label} response status: {resp.status_code}")

                        # Check status code before streaming
                        if resp.status_code != 200:
                            # === WAF COOKIE REFRESH ===
                            # Before failover, try refreshing the WAF cookie on the same node
                            if auto_cookie_refresh and resp.status_code in (302, 401, 403, 405, 407) and attempt < MAX_FAILOVER_RETRIES:
                                from app.waf_cookie_handler import refresh_waf_cookie
                                current_base_url = current_url.rsplit(endpoint, 1)[0] if endpoint in current_url else base_url
                                refreshed, updated_headers, refresh_error = await refresh_waf_cookie(
                                    base_url=current_base_url,
                                    api_key=current_api_key,
                                    node_type=node_type,
                                    existing_headers=current_headers,
                                    timeout=10.0,
                                )
                                if refreshed and updated_headers:
                                    logger.warning(f"[WAF] Refreshed cookie for {current_base_url}, retrying same node")
                                    current_headers = updated_headers
                                    continue
                                logger.warning(f"[WAF] Cookie refresh failed for {current_base_url}: {refresh_error}")

                            error_text = await resp.aread()
                            error_msg = error_text.decode()

                            # Parse error message
                            try:
                                error_json = _json_loads(error_msg)
                                if isinstance(error_json, dict) and 'error' in error_json:
                                    error_detail = error_json['error']
                                    if isinstance(error_detail, dict) and 'message' in error_detail:
                                        error_msg = error_detail['message']
                                    elif isinstance(error_detail, str):
                                        error_msg = error_detail
                            except (_json_decode_error, KeyError, TypeError):
                                pass

                            logger.error(f"{provider_label} upstream error ({resp.status_code}): {error_msg}")
                            logger.error(f"Request URL: {current_url}")
                            logger.error(f"Request data: {_json_dumps(current_data, indent=True).decode()}")

                            # === NODE-LEVEL RETRY ===
                            # Try the same model on a different node first
                            if resp.status_code in self.NODE_RETRYABLE_STATUS_CODES and attempt < MAX_FAILOVER_RETRIES:
                                # Add current node to tried list
                                current_base_url = current_url.rsplit(endpoint, 1)[0] if endpoint in current_url else base_url
                                tried_nodes.add(current_base_url)

                                new_base_url, new_api_key, _, new_headers, _ = await self._select_node_url(
                                    current_model, exclude_nodes=list(tried_nodes),
                                    exclude_scoped=exclude_scoped,
                                    allowed_node_ids=allowed_node_ids,
                                    routing_catalog_names=routing_catalog_names,
                                )
                                if new_base_url:
                                    # Check node access for failover node
                                    if not bypass_node_access and username and not await self._check_user_node_access(username, new_base_url):
                                        logger.warning(f"[NODE RETRY] User '{username}' denied access to failover node {new_base_url}, skipping")
                                        tried_nodes.add(new_base_url)
                                        continue
                                    logger.warning(
                                        f"[NODE RETRY] Stream error {resp.status_code} from {current_base_url}, "
                                        f"trying node {new_base_url} for model {current_model}"
                                    )
                                    current_url = f"{new_base_url}{endpoint}"
                                    current_api_key = new_api_key
                                    current_headers = new_headers
                                    current_data = await self._rebind_body_to_node(
                                        current_data, rsnap, new_base_url
                                    )
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
                                failed_for_group = rsnap.get('model') or rsnap.get('name') or current_model
                                fallback_model = self._get_fallback_model(original_group, failed_for_group, tried_models)

                                if fallback_model and fallback_model not in tried_models:
                                    logger.warning(f"[FAILOVER] Stream error {resp.status_code}, trying fallback model: {fallback_model}")
                                    tried_models.add(fallback_model)

                                    # Update data with fallback model
                                    current_data = original_data.copy() if original_data else {}
                                    current_data['model'] = fallback_model
                                    current_data = await self._resolve_model_groups(current_data)
                                    rsnap.clear()
                                    for snap_key in ('model', 'name', 'source', 'destination'):
                                        v = current_data.get(snap_key)
                                        if isinstance(v, str):
                                            rsnap[snap_key] = v

                                    fb_display = current_data.get('model') or current_data.get('name')
                                    fb_allowed = self._prepare_routing_allowed(current_data, fb_display)
                                    # Select new node URL for fallback (reset tried_nodes for new model)
                                    new_base_url, new_api_key, _, new_headers, _ = await self._select_node_url(
                                        fb_display or fallback_model,
                                        exclude_scoped=exclude_scoped,
                                        allowed_node_ids=fb_allowed,
                                        routing_catalog_names=routing_catalog_names,
                                    )
                                    if not bypass_node_access and new_base_url and username and not await self._check_user_node_access(username, new_base_url):
                                        logger.warning(f"[FAILOVER] User '{username}' denied access to fallback node {new_base_url}, skipping")
                                        tried_nodes.add(new_base_url)
                                        new_base_url = None
                                    current_url = f"{new_base_url}{endpoint}" if new_base_url else ""
                                    if new_base_url:
                                        tried_nodes.add(new_base_url)
                                        current_api_key = new_api_key
                                        current_headers = new_headers
                                        current_data = await self._rebind_body_to_node(
                                            current_data, rsnap, new_base_url
                                        )

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
                                provider_label = 'vLLM' if node_type == 'vllm' else 'Ollama'
                                friendly_msg = f"{provider_label} upstream error: {error_msg}"

                            error_response = {
                                "error": {
                                    "message": friendly_msg,
                                    "type": error_type,
                                    "code": error_code
                                }
                            }
                            yield b'data: ' + _json_dumps(error_response) + b'\n\n'
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
                                logger.debug(f"[STREAM CHUNK {chunk_count}] Received {len(chunk)} bytes: {chunk[:200]!r}")

                            buffer += chunk

                            # Guard against unbounded buffer growth from malformed upstream
                            if len(buffer) > 1024 * 1024:
                                logger.debug(f"[STREAM] Buffer exceeded 1MB, discarding {len(buffer)} bytes")
                                buffer = b""
                                continue

                            while b'\n' in buffer:
                                line, buffer = buffer.split(b'\n', 1)
                                if line:
                                    try:
                                        if line.startswith(b'data: '):
                                            json_str = line[6:].decode('utf-8').strip()
                                            if json_str and json_str != '[DONE]':
                                                logger.debug(f"[OLLAMA IN] {json_str}")
                                                json_data = _json_loads(json_str)

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
                                                        logger.debug(f"[KIMI DEBUG] Received content chunk: {content[:100]!r}")
                                                    if reasoning:
                                                        logger.debug(f"[KIMI DEBUG] Received reasoning chunk: {reasoning[:100]!r}")

                                                    if kimi_suspicion_buffer:
                                                        logger.debug(f"[KIMI DEBUG] Appending suspicion buffer: {kimi_suspicion_buffer!r} to current combined")
                                                        combined_for_detection = kimi_suspicion_buffer + combined_for_detection
                                                        kimi_suspicion_buffer = ""

                                                if is_kimi_model and kimi_buffering_active:
                                                    kimi_content_buffer += combined_for_detection

                                                    if '<|tool_calls_section_end|>' in kimi_content_buffer:
                                                        logger.info(f"[KIMI] Tool call section complete, processing buffer")

                                                        clean_content, tool_calls, has_tool_calls = await _asyncio.to_thread(parse_kimi_tool_calls, kimi_content_buffer)

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
                                                                yield b'data: ' + _json_dumps(content_chunk) + b'\n\n'

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
                                                            yield b'data: ' + _json_dumps(tool_calls_chunk) + b'\n\n'

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
                                                            yield b'data: ' + _json_dumps(finish_chunk) + b'\n\n'
                                                            first_chunk_sent = True
                                                        else:
                                                            mapped_data = await self._map_model_from_ollama(_json_loads(json_str))
                                                            if isinstance(mapped_data, dict) and 'choices' in mapped_data:
                                                                choices = mapped_data.get('choices', [])
                                                                if choices and len(choices) > 0:
                                                                    choices[0]['delta']['content'] = kimi_content_buffer
                                                            yield b'data: ' + _json_dumps(mapped_data) + b'\n\n'
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
                                                        logger.debug(f"[KIMI DEBUG] Combined content is suspicious, buffering: {combined_for_detection!r}")
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

                                                    # Check if the tool call section is complete (any format)
                                                    if '</tool_calls>' in deepseek_content_buffer or '</|DSML|tool_calls>' in deepseek_content_buffer or '</｜DSML｜tool_calls>' in deepseek_content_buffer or '</CallMcpTool>' in deepseek_content_buffer or '</tool_call>' in deepseek_content_buffer:
                                                        logger.info(f"[DEEPSEEK] Tool call section complete, processing buffer ({len(deepseek_content_buffer)} chars)")

                                                        clean_content, tool_calls, has_tool_calls = await _asyncio.to_thread(parse_deepseek_tool_calls, deepseek_content_buffer)

                                                        if has_tool_calls:
                                                            logger.info(f"[DEEPSEEK] Converted {len(tool_calls)} tool call(s) to OpenAI format")

                                                            if clean_content:
                                                                if '<tool_calls>' in clean_content or '</tool_calls>' in clean_content or '<CallMcpTool>' in clean_content or '</CallMcpTool>' in clean_content or '｜DSML｜' in clean_content or '|DSML|' in clean_content:
                                                                    # First normalize DSML tags to canonical form, then strip all tool call tags
                                                                    clean_content = _normalize_dsml_tags(clean_content)
                                                                    clean_content = re.sub(r'</?(?:tool_calls|CallMcpTool|tool_call)[^>]*>', '', clean_content).strip()
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
                                                                yield b'data: ' + _json_dumps(content_chunk) + b'\n\n'

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
                                                            yield b'data: ' + _json_dumps(tool_calls_chunk) + b'\n\n'

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
                                                            yield b'data: ' + _json_dumps(finish_chunk) + b'\n\n'
                                                            first_chunk_sent = True
                                                        else:
                                                            # Parsing failed — emit buffered content as plain text
                                                            mapped_data = await self._map_model_from_ollama(_json_loads(json_str))
                                                            if isinstance(mapped_data, dict) and 'choices' in mapped_data:
                                                                choices = mapped_data.get('choices', [])
                                                                if choices and len(choices) > 0:
                                                                    choices[0]['delta']['content'] = deepseek_content_buffer
                                                            yield b'data: ' + _json_dumps(mapped_data) + b'\n\n'
                                                            first_chunk_sent = True

                                                        deepseek_content_buffer = ""
                                                        deepseek_buffering_active = False
                                                        continue
                                                    else:
                                                        # Still buffering — wait for more chunks
                                                        continue

                                                # DeepSeek: detect start of tool call section (<tool_calls>, <CallMcpTool>, <tool_call singular>, or DSML variants)
                                                if is_deepseek_model and ('<tool_calls>' in (content + reasoning) or '<|DSML|tool_calls>' in (content + reasoning) or '<｜DSML｜tool_calls>' in (content + reasoning) or '<CallMcpTool>' in (content + reasoning) or '<tool_call' in (content + reasoning)):
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

                                                    # After combining suspicion buffer, check if we now have a complete tag
                                                    if ds_combined and ('<tool_calls>' in ds_combined or '<|DSML|tool_calls>' in ds_combined or '<｜DSML｜tool_calls>' in ds_combined or '<CallMcpTool>' in ds_combined or '<tool_call' in ds_combined):
                                                        deepseek_buffering_active = True
                                                        deepseek_content_buffer = ds_combined
                                                        logger.info(f"[DEEPSEEK] Tool call detected after suspicion merge, buffering")
                                                        continue

                                                    if ds_combined:
                                                        for prefix in _DEEPSEEK_TAG_PREFIXES:
                                                            if len(ds_combined) >= len(prefix) and ds_combined.rstrip().endswith(prefix):
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
                                                mapped_data = await self._map_model_from_ollama(json_data)

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
                                                    if usage:
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
                                                        yield b'data: ' + _json_dumps(mapped_data) + b'\n\n'
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

                                                    yield b'data: ' + _json_dumps(out_chunk) + b'\n\n'
                                                    first_chunk_sent = True

                                            elif line == b'data: [DONE]':
                                                logger.debug(f"[STREAM] Received [DONE] marker")
                                                yield b'data: [DONE]\n\n'
                                                done_marker_sent = True
                                            else:
                                                # Non-SSE format (native Ollama)
                                                logger.info(f"[OLLAMA IN NATIVE] {line.decode('utf-8', errors='replace')[:200]}")
                                                json_str = line.decode('utf-8', errors='replace').strip()
                                                if json_str:
                                                    try:
                                                        json_data = _json_loads(json_str)
                                                        mapped_data = await self._map_model_from_ollama(json_data)
                                                        yield _json_dumps(mapped_data) + b'\n'
                                                    except (_json_decode_error, UnicodeDecodeError) as e:
                                                        logger.warning(f"[STREAM] JSON parse error: {e}, line: {line[:100]!r}")
                                                        yield line + b'\n'

                                    except (_json_decode_error, UnicodeDecodeError) as e:
                                        logger.warning(f"[STREAM] Buffer parse error: {e}, buffer: {line[:100]!r}")
                                        yield line + b'\n'

                        # Log user activity after streaming
                        if username and current_model:
                            duration_ms = int((time.monotonic() - start_time) * 1000)
                            await self._log_user_activity(
                                username=username,
                                model_name=current_model,
                                request_type=endpoint.replace('/api/', '').replace('/v1/', '') if endpoint != '/api/chat' else 'chat/completions',
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                total_tokens=prompt_tokens + completion_tokens,
                                status_code=200,
                                duration_ms=duration_ms,
                                source=source or self._detect_request_source(endpoint),
                                url_path=url_path or endpoint
                            )

                        # Send [DONE] if not already sent
                        if not done_marker_sent and is_openai_endpoint:
                            # DeepSeek flush: if still buffering tool calls at stream end, try to parse
                            if deepseek_buffering_active and deepseek_content_buffer:
                                logger.info(f"[DEEPSEEK] Stream ended while buffering, attempting flush ({len(deepseek_content_buffer)} chars)")
                                clean_content, tool_calls, has_tool_calls = await _asyncio.to_thread(parse_deepseek_tool_calls, deepseek_content_buffer)

                                if has_tool_calls:
                                    logger.info(f"[DEEPSEEK] Flushed {len(tool_calls)} tool call(s)")
                                    if clean_content:
                                        content_chunk = {
                                            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                                            "object": "chat.completion.chunk",
                                            "model": model_mapper.get_display_model_name(current_model),
                                            "choices": [{"index": 0, "delta": {"content": clean_content}, "finish_reason": None}]
                                        }
                                        yield b'data: ' + _json_dumps(content_chunk) + b'\n\n'

                                    tool_calls_chunk = {
                                        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                                        "object": "chat.completion.chunk",
                                        "model": model_mapper.get_display_model_name(current_model),
                                        "choices": [{"index": 0, "delta": {"tool_calls": tool_calls}, "finish_reason": None}]
                                    }
                                    yield b'data: ' + _json_dumps(tool_calls_chunk) + b'\n\n'

                                    finish_chunk = {
                                        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                                        "object": "chat.completion.chunk",
                                        "model": model_mapper.get_display_model_name(current_model),
                                        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]
                                    }
                                    yield b'data: ' + _json_dumps(finish_chunk) + b'\n\n'
                                else:
                                    # Emit as plain text
                                    text_chunk = {
                                        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                                        "object": "chat.completion.chunk",
                                        "model": model_mapper.get_display_model_name(current_model),
                                        "choices": [{"index": 0, "delta": {"content": deepseek_content_buffer}, "finish_reason": None}]
                                    }
                                    yield b'data: ' + _json_dumps(text_chunk) + b'\n\n'

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
                                yield b'data: ' + _json_dumps(text_chunk) + b'\n\n'
                                deepseek_suspicion_buffer = ""

                            logger.debug(f"[STREAM END] Sending [DONE] marker (not received from upstream)")
                            yield b'data: [DONE]\n\n'

                        logger.debug(f"[STREAM END] Stream complete. Total chunks: {chunk_count}, bytes: {total_bytes}")
                        return  # Successfully completed

                except httpx.RequestError as e:
                    logger.error(f"Network error while streaming to Ollama: {str(e)}")
                    current_model = current_data.get('model', 'unknown')

                    # === NODE-LEVEL RETRY ===
                    # Try the same model on a different node first
                    if attempt < MAX_FAILOVER_RETRIES:
                        current_base_url = current_url.rsplit(endpoint, 1)[0] if endpoint in current_url else base_url
                        tried_nodes.add(current_base_url)

                        new_base_url, new_api_key, _, _, _ = await self._select_node_url(
                            current_model, exclude_nodes=list(tried_nodes),
                            exclude_scoped=exclude_scoped,
                            allowed_node_ids=allowed_node_ids,
                            routing_catalog_names=routing_catalog_names,
                        )
                        if new_base_url:
                            if not bypass_node_access and username and not await self._check_user_node_access(username, new_base_url):
                                logger.warning(f"[NODE RETRY] User '{username}' denied access to failover node {new_base_url}, skipping")
                                tried_nodes.add(new_base_url)
                                continue
                            logger.warning(
                                f"[NODE RETRY] Connection error from {current_base_url}, "
                                f"trying node {new_base_url} for model {current_model}"
                            )
                            current_url = f"{new_base_url}{endpoint}"
                            current_api_key = new_api_key
                            current_data = await self._rebind_body_to_node(
                                current_data, rsnap, new_base_url
                            )
                            last_error = e
                            continue
                        logger.info(f"[NODE RETRY] No more nodes available for model {current_model}")

                    # === MODEL-LEVEL FALLBACK ===
                    if original_group and attempt < MAX_FAILOVER_RETRIES:
                        failed_for_group = rsnap.get('model') or rsnap.get('name') or current_model
                        fallback_model = self._get_fallback_model(original_group, failed_for_group, tried_models)

                        if fallback_model and fallback_model not in tried_models:
                            logger.warning(f"[FAILOVER] Connection error, trying fallback model: {fallback_model}")
                            tried_models.add(fallback_model)

                            current_data = original_data.copy() if original_data else {}
                            current_data['model'] = fallback_model
                            current_data = await self._resolve_model_groups(current_data)
                            rsnap.clear()
                            for snap_key in ('model', 'name', 'source', 'destination'):
                                v = current_data.get(snap_key)
                                if isinstance(v, str):
                                    rsnap[snap_key] = v

                            fb_display = current_data.get('model') or current_data.get('name')
                            fb_allowed = self._prepare_routing_allowed(current_data, fb_display)
                            new_base_url, new_api_key, _, _, _ = await self._select_node_url(
                                fb_display or fallback_model,
                                exclude_scoped=exclude_scoped,
                                allowed_node_ids=fb_allowed,
                                routing_catalog_names=routing_catalog_names,
                            )
                            if not bypass_node_access and new_base_url and username and not await self._check_user_node_access(username, new_base_url):
                                logger.warning(f"[FAILOVER] User '{username}' denied access to fallback node {new_base_url}, skipping")
                                tried_nodes.add(new_base_url)
                                new_base_url = None
                            current_url = f"{new_base_url}{endpoint}" if new_base_url else ""
                            if new_base_url:
                                tried_nodes.add(new_base_url)
                                current_api_key = new_api_key
                                current_data = await self._rebind_body_to_node(
                                    current_data, rsnap, new_base_url
                                )

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
                    yield b'data: ' + _json_dumps(error_response) + b'\n\n'
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
                    yield b'data: ' + _json_dumps(error_response) + b'\n\n'
                    yield b'data: [DONE]\n\n'
                    return

        async def _tracked_stream():
            """Wraps stream generator with streaming activity tracking."""
            await mark_stream_start()
            try:
                async for chunk in stream_generator_with_failover():
                    yield chunk
            finally:
                await mark_stream_end()

        return StreamingResponse(
            _tracked_stream(),
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
        api_key: Optional[str],
        start_time: float,
        node_type: str = 'ollama',
        node_headers: Optional[Dict[str, Any]] = None,
        auto_cookie_refresh: bool = False,
        exclude_scoped: bool = False,
        bypass_node_access: bool = False,
        client_headers: Optional[Dict[str, str]] = None,
        allowed_node_ids: Optional[List[int]] = None,
        routing_snapshot: Optional[Dict[str, str]] = None,
        routing_catalog_names: Optional[List[str]] = None,
        source: Optional[str] = None,
        url_path: Optional[str] = None,
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
        rsnap = routing_snapshot if routing_snapshot is not None else {}
        last_error = None
        current_url = url
        current_data = data.copy() if data else {}
        current_api_key = api_key
        current_headers = node_headers

        for attempt in range(MAX_FAILOVER_RETRIES + 1):
            client = await self._get_http_client()
            current_model = current_data.get('model') or model_name or 'unknown'

            try:
                logger.info(f"Sending request to Ollama: {current_url} (attempt {attempt + 1})")

                request_headers = {}
                if client_headers:
                    # Filter: never forward client IP / CDN hop headers to upstream (see module doc).
                    request_headers.update({
                        k: v for k, v in client_headers.items()
                        if k.lower() not in _CLIENT_HEADERS_BLOCKED_FOR_UPSTREAM
                    })
                if current_headers:
                    # Filter out internal proxy directives (not for upstream)
                    upstream_headers = {k: v for k, v in current_headers.items() if k.lower() != 'x-skip-max-tokens-injection'}
                    request_headers.update(upstream_headers)
                if current_api_key:
                    request_headers["Authorization"] = f"Bearer {current_api_key}"

                # Log full outgoing request body and headers for debugging (debug level only)
                if current_data:
                    _provider_label = 'vLLM' if node_type == 'vllm' else 'Ollama'
                    logger.debug(f"[OUTGOING] {_provider_label} request body: {_json_dumps(current_data, indent=True).decode()}")
                logger.debug(f"[OUTGOING] request headers: {request_headers}")

                if method.upper() == "GET":
                    response = await client.get(current_url, headers=request_headers)
                elif method.upper() == "POST":
                    response = await client.post(current_url, json=current_data, headers=request_headers)
                elif method.upper() == "DELETE":
                    response = await client.delete(current_url, json=current_data, headers=request_headers)
                else:
                    raise HTTPException(status_code=405, detail="Method not allowed")

                # Check response status
                if response.status_code >= 400:
                    # === WAF COOKIE REFRESH ===
                    # Before failover, try refreshing the WAF cookie on the same node
                    if auto_cookie_refresh and response.status_code in (302, 401, 403, 405, 407) and attempt < MAX_FAILOVER_RETRIES:
                        from app.waf_cookie_handler import refresh_waf_cookie
                        current_base_url = current_url.rsplit(endpoint, 1)[0] if endpoint in current_url else url.rsplit(endpoint, 1)[0]
                        refreshed, updated_headers, refresh_error = await refresh_waf_cookie(
                            base_url=current_base_url,
                            api_key=current_api_key,
                            node_type=node_type,
                            existing_headers=current_headers,
                            timeout=10.0,
                        )
                        if refreshed and updated_headers:
                            logger.warning(f"[WAF] Refreshed cookie for {current_base_url}, retrying same node")
                            current_headers = updated_headers
                            continue
                        logger.warning(f"[WAF] Cookie refresh failed for {current_base_url}: {refresh_error}")

                    error_text = response.text
                    provider_label = 'vLLM' if node_type == 'vllm' else 'Ollama'
                    logger.error(f"{provider_label} error ({response.status_code}): {error_text}")
                    logger.error(f"Request URL: {current_url}")
                    if current_data:
                        logger.error(f"Request data: {_json_dumps(current_data, indent=True).decode()}")

                    # === NODE-LEVEL RETRY ===
                    # Try the same model on a different node first
                    if response.status_code in self.NODE_RETRYABLE_STATUS_CODES and attempt < MAX_FAILOVER_RETRIES:
                        current_base_url = current_url.rsplit(endpoint, 1)[0] if endpoint in current_url else url.rsplit(endpoint, 1)[0]
                        tried_nodes.add(current_base_url)

                        new_base_url, new_api_key, _, new_headers, _ = await self._select_node_url(
                            current_model, exclude_nodes=list(tried_nodes),
                            exclude_scoped=exclude_scoped,
                            allowed_node_ids=allowed_node_ids,
                            routing_catalog_names=routing_catalog_names,
                        )
                        if new_base_url:
                            if not bypass_node_access and username and not await self._check_user_node_access(username, new_base_url):
                                logger.warning(f"[NODE RETRY] User '{username}' denied access to failover node {new_base_url}, skipping")
                                tried_nodes.add(new_base_url)
                                continue
                            logger.warning(
                                f"[NODE RETRY] Error {response.status_code} from {current_base_url}, "
                                f"trying node {new_base_url} for model {current_model}"
                            )
                            current_url = f"{new_base_url}{endpoint}"
                            current_api_key = new_api_key
                            current_headers = new_headers
                            current_data = await self._rebind_body_to_node(
                                current_data, rsnap, new_base_url
                            )
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
                        failed_for_group = rsnap.get('model') or rsnap.get('name') or current_model
                        fallback_model = self._get_fallback_model(original_group, failed_for_group, tried_models)

                        if fallback_model and fallback_model not in tried_models:
                            logger.warning(f"[FAILOVER] Error {response.status_code}, trying fallback model: {fallback_model}")
                            tried_models.add(fallback_model)

                            # Update data with fallback model
                            current_data = data.copy() if data else {}
                            current_data['model'] = fallback_model
                            current_data = await self._resolve_model_groups(current_data)
                            rsnap.clear()
                            for snap_key in ('model', 'name', 'source', 'destination'):
                                v = current_data.get(snap_key)
                                if isinstance(v, str):
                                    rsnap[snap_key] = v

                            fb_display = current_data.get('model') or current_data.get('name')
                            fb_allowed = self._prepare_routing_allowed(current_data, fb_display)
                            # Select new node URL for fallback
                            new_base_url, new_api_key, _, new_headers, _ = await self._select_node_url(
                                fb_display or fallback_model,
                                exclude_scoped=exclude_scoped,
                                allowed_node_ids=fb_allowed,
                                routing_catalog_names=routing_catalog_names,
                            )
                            if not bypass_node_access and new_base_url and username and not await self._check_user_node_access(username, new_base_url):
                                logger.warning(f"[FAILOVER] User '{username}' denied access to fallback node {new_base_url}, skipping")
                                tried_nodes.add(new_base_url)
                                new_base_url = None
                            current_url = f"{new_base_url}{endpoint}" if new_base_url else ""
                            if new_base_url:
                                tried_nodes.add(new_base_url)
                                current_api_key = new_api_key
                                current_headers = new_headers
                                current_data = await self._rebind_body_to_node(
                                    current_data, rsnap, new_base_url
                                )

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
                            request_type=endpoint.replace('/api/', '').replace('/v1/', '') if endpoint != '/api/chat' else 'chat/completions',
                            prompt_tokens=0,
                            completion_tokens=0,
                            total_tokens=0,
                            source=source or self._detect_request_source(endpoint),
                            url_path=url_path or endpoint
                        )
                    return response.text

                # Map model names in response
                if endpoint == "/api/tags":
                    response_data = self._map_models_list(response_data)
                elif endpoint == "/v1/models":
                    response_data = self._map_openai_models_list(response_data)
                elif is_openai_endpoint:
                    response_data = await self._map_model_from_ollama(response_data)
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
                        request_type=endpoint.replace('/api/', '').replace('/v1/', '') if endpoint != '/api/chat' else 'chat/completions',
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens or (prompt_tokens + completion_tokens),
                        status_code=200,
                        duration_ms=duration_ms,
                        source=source or self._detect_request_source(endpoint),
                        url_path=url_path or endpoint
                    )

                return response_data

            except httpx.RequestError as e:
                logger.error(f"Failed to connect to Ollama: {str(e)}")

                # === NODE-LEVEL RETRY ===
                # Try the same model on a different node first
                if attempt < MAX_FAILOVER_RETRIES:
                    current_base_url = current_url.rsplit(endpoint, 1)[0] if endpoint in current_url else url.rsplit(endpoint, 1)[0]
                    tried_nodes.add(current_base_url)

                    new_base_url, new_api_key, _, _, _ = await self._select_node_url(
                        current_model, exclude_nodes=list(tried_nodes),
                        exclude_scoped=exclude_scoped,
                        allowed_node_ids=allowed_node_ids,
                        routing_catalog_names=routing_catalog_names,
                    )
                    if new_base_url:
                        if not bypass_node_access and username and not await self._check_user_node_access(username, new_base_url):
                            logger.warning(f"[NODE RETRY] User '{username}' denied access to failover node {new_base_url}, skipping")
                            tried_nodes.add(new_base_url)
                            continue
                        logger.warning(
                            f"[NODE RETRY] Connection error from {current_base_url}, "
                            f"trying node {new_base_url} for model {current_model}"
                        )
                        current_url = f"{new_base_url}{endpoint}"
                        current_api_key = new_api_key
                        current_data = await self._rebind_body_to_node(
                            current_data, rsnap, new_base_url
                        )
                        last_error = HTTPException(
                            status_code=503,
                            detail=f"Failed to connect to Ollama: {str(e)}"
                        )
                        continue
                    logger.info(f"[NODE RETRY] No more nodes available for model {current_model}")

                # === MODEL-LEVEL FALLBACK ===
                if original_group and attempt < MAX_FAILOVER_RETRIES:
                    failed_for_group = rsnap.get('model') or rsnap.get('name') or current_model
                    fallback_model = self._get_fallback_model(original_group, failed_for_group, tried_models)

                    if fallback_model and fallback_model not in tried_models:
                        logger.warning(f"[FAILOVER] Connection error, trying fallback model: {fallback_model}")
                        tried_models.add(fallback_model)

                        current_data = data.copy() if data else {}
                        current_data['model'] = fallback_model
                        current_data = await self._resolve_model_groups(current_data)
                        rsnap.clear()
                        for snap_key in ('model', 'name', 'source', 'destination'):
                            v = current_data.get(snap_key)
                            if isinstance(v, str):
                                rsnap[snap_key] = v

                        fb_display = current_data.get('model') or current_data.get('name')
                        fb_allowed = self._prepare_routing_allowed(current_data, fb_display)
                        new_base_url, new_api_key, _, _, _ = await self._select_node_url(
                            fb_display or fallback_model,
                            exclude_scoped=exclude_scoped,
                            allowed_node_ids=fb_allowed,
                            routing_catalog_names=routing_catalog_names,
                        )
                        if not bypass_node_access and new_base_url and username and not await self._check_user_node_access(username, new_base_url):
                            logger.warning(f"[FAILOVER] User '{username}' denied access to fallback node {new_base_url}, skipping")
                            tried_nodes.add(new_base_url)
                            new_base_url = None
                        current_url = f"{new_base_url}{endpoint}" if new_base_url else ""
                        if new_base_url:
                            tried_nodes.add(new_base_url)
                            current_api_key = new_api_key
                            current_data = await self._rebind_body_to_node(
                                current_data, rsnap, new_base_url
                            )

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
                request_type=endpoint.replace('/api/', '').replace('/v1/', '') if endpoint != '/api/chat' else 'chat/completions',
                status_code=error_status,
                duration_ms=duration_ms,
                error_message=error_msg[:500],
                source=source or self._detect_request_source(endpoint),
                url_path=url_path or endpoint
            )
        if last_error:
            raise last_error
        raise HTTPException(
            status_code=500,
            detail="All fallback attempts failed"
        )


    async def get_node_url(self, model_name: str = "") -> str:
        """Public helper: get the best node URL for a given model (using load balancer)."""
        await self._ensure_mappings_loaded()
        try:
            base_url, _, _, _, _ = await self._select_node_url(model_name, exclude_scoped=True)
            return base_url
        except Exception:
            return self.base_url

    async def stream_ollama(self, data: Dict[str, Any], model_name: str = "", username: Optional[str] = None) -> AsyncGenerator[bytes, None]:
        """Public helper: return raw SSE bytes stream for a chat completion.
        Caller must wrap in StreamingResponse."""
        from fastapi.responses import StreamingResponse  # type: ignore
        # Delegate to proxy_request for full failover and logging logic,
        # but unwrap the StreamingResponse by consuming its internal iterator.
        resp = await self.proxy_request(
            method="POST",
            endpoint="/v1/chat/completions",
            data=data,
            stream=True,
            username=username,
        )
        # FastAPI StreamingResponse stores body_iterator internally
        # (it may be a starlette Response or our own StreamingResponse)
        if hasattr(resp, "body_iterator"):
            async for chunk in resp.body_iterator:  # type: ignore
                yield chunk  # type: ignore
        elif hasattr(resp, "__aiter__"):
            async for chunk in resp:
                yield chunk
        else:
            raise RuntimeError("Unexpected response type from proxy_request")


# Global proxy instance
ollama_proxy = OllamaProxy()
