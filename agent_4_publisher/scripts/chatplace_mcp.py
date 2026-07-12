#!/usr/bin/env python3
"""Minimal ChatPlace MCP client (JSON-RPC over HTTP/SSE)."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


DEFAULT_MCP_URL = "https://mcp.chatplace.io/mcp"


class ChatPlaceMcpError(RuntimeError):
    pass


def mcp_url(config: dict[str, Any] | None = None) -> str:
    if config:
        url = config.get("chatplace", {}).get("mcp_url")
        if url:
            return str(url)
    return os.environ.get("CHATPLACE_MCP_URL", DEFAULT_MCP_URL)


def mcp_api_key() -> str:
    key = os.environ.get("CHATPLACE_API_KEY", "").strip()
    if not key:
        raise ChatPlaceMcpError("CHATPLACE_API_KEY is not set in .env")
    return key


def _parse_sse_payload(raw: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for block in re.split(r"\n\n+", raw.strip()):
        data_lines = [
            line[5:].strip()
            for line in block.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        payload = "\n".join(data_lines)
        try:
            messages.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return messages


def _parse_response_body(raw: str, content_type: str) -> dict[str, Any]:
    if "text/event-stream" in content_type or raw.startswith("event:") or raw.startswith("data:"):
        messages = _parse_sse_payload(raw)
        for message in reversed(messages):
            if isinstance(message, dict) and "result" in message:
                return message
            if isinstance(message, dict) and "error" in message:
                raise ChatPlaceMcpError(str(message["error"]))
        raise ChatPlaceMcpError(f"No MCP result in SSE response: {raw[:500]}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ChatPlaceMcpError(f"Invalid MCP response: {raw[:500]}") from exc
    if isinstance(data, dict) and "error" in data:
        raise ChatPlaceMcpError(str(data["error"]))
    return data if isinstance(data, dict) else {"result": data}


def mcp_request(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    request_id: int = 1,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    req = urllib.request.Request(
        mcp_url(config),
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {mcp_api_key()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "real-estate-agent6-publisher/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        raise ChatPlaceMcpError(f"HTTP {exc.code}: {err[:1000]}") from exc

    message = _parse_response_body(raw, content_type)
    if "error" in message:
        raise ChatPlaceMcpError(str(message["error"]))
    return message.get("result", message)


def initialize_session(config: dict[str, Any] | None = None) -> dict[str, Any]:
    return mcp_request(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "agent6-publisher", "version": "1.0"},
        },
        request_id=1,
        config=config,
    )


def list_tools(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    initialize_session(config)
    result = mcp_request("tools/list", {}, request_id=2, config=config)
    tools = result.get("tools") if isinstance(result, dict) else None
    return tools if isinstance(tools, list) else []


def _tool_score(name: str, description: str) -> int:
    text = f"{name} {description}".lower()
    score = 0
    for token, weight in (
        ("automation", 8),
        ("funnel", 8),
        ("instagram", 6),
        ("comment", 5),
        ("keyword", 4),
        ("dm", 3),
        ("create", 3),
        ("setup", 2),
        ("chat", 1),
    ):
        if token in text:
            score += weight
    return score


def pick_funnel_tool(tools: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not tools:
        return None
    ranked = sorted(
        tools,
        key=lambda tool: _tool_score(
            str(tool.get("name") or ""),
            str(tool.get("description") or ""),
        ),
        reverse=True,
    )
    return ranked[0]


def call_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> Any:
    initialize_session(config)
    result = mcp_request(
        "tools/call",
        {"name": name, "arguments": arguments},
        request_id=3,
        config=config,
    )
    return result


def _extract_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
            if parts:
                return "\n".join(parts)
        for key in ("text", "message", "output", "result"):
            if result.get(key):
                return str(result[key])
    return json.dumps(result, ensure_ascii=False)


def extract_tool_json(result: Any) -> Any:
    text = _extract_text(result)
    text = text.strip()
    if not text:
        return result
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def list_bots(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    raw = extract_tool_json(call_tool("bots_list", {}, config=config))
    return raw if isinstance(raw, list) else []


def find_instagram_bot_id(config: dict[str, Any] | None = None) -> str:
    cp = (config or {}).get("chatplace", {})
    configured = str(cp.get("instagram_bot_id") or "").strip()
    if configured:
        return configured
    for bot in list_bots(config):
        platform = bot.get("platform") if isinstance(bot.get("platform"), dict) else {}
        label = str(platform.get("label") or platform.get("name") or "").lower()
        if label == "instagram" or "instagram" in label:
            bot_id = bot.get("id")
            if bot_id:
                return str(bot_id)
    raise ChatPlaceMcpError("No Instagram bot found in ChatPlace — connect IG account first")


def list_instagram_media(bot_id: str, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    raw = extract_tool_json(
        call_tool(
            "automations_triggers_list_instagram_media",
            {"botId": bot_id},
            config=config,
        )
    )
    return raw if isinstance(raw, list) else []


def normalize_instagram_permalink(url: str) -> str:
    return url.rstrip("/").split("?")[0].lower()


def match_media_id_for_post(bot_id: str, post_url: str, config: dict[str, Any] | None = None) -> str | None:
    target = normalize_instagram_permalink(post_url)
    for item in list_instagram_media(bot_id, config):
        permalink = str(item.get("permalink") or "")
        if permalink and normalize_instagram_permalink(permalink) == target:
            media_uuid = item.get("id")
            if media_uuid:
                return str(media_uuid)
    return None


def _find_automation_id_by_media(
    bot_id: str,
    media_id: str,
    config: dict[str, Any] | None = None,
) -> str | None:
    items = extract_tool_json(call_tool("automations_list", {"botId": bot_id}, config=config))
    if not isinstance(items, list):
        return None
    for item in items:
        medias = item.get("medias") or []
        if media_id in medias:
            automation_id = item.get("id")
            if automation_id:
                return str(automation_id)
    return None


def create_instagram_comment_funnel(
    *,
    bot_id: str,
    media_id: str,
    telegram_url: str,
    dm_message: str,
    welcome_message: str,
    welcome_button: str,
    button_text: str,
    trigger_type: str = "commentAnyValue",
    trigger_keyword: str | None = None,
    auto_comment: str | None = None,
    name: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "botId": bot_id,
        "triggerType": trigger_type,
        "templateType": "base",
        "welcomeMessage": welcome_message,
        "welcomeButton": welcome_button,
        "messageWithLink": dm_message,
        "buttonText": button_text,
        "buttonLink": telegram_url,
        "mediaIds": [media_id],
    }
    if trigger_type in {"commentContains", "commentEquals", "messageContains", "messageEquals"}:
        if not trigger_keyword:
            raise ChatPlaceMcpError(f"trigger_keyword required for {trigger_type}")
        args["startMessages"] = [trigger_keyword]
    if auto_comment:
        args["autoAnswers"] = [auto_comment]
    raw = call_tool("automations_quick_setup", args, config=config)
    payload = extract_tool_json(raw)
    automation_id = None
    if isinstance(payload, dict):
        automation_id = (
            payload.get("id")
            or payload.get("automationId")
            or payload.get("automation_id")
        )
        if not automation_id and payload.get("success"):
            automation_id = _find_automation_id_by_media(bot_id, media_id, config)
    if not automation_id and isinstance(payload, str):
        match = re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            payload,
            re.I,
        )
        if match:
            automation_id = match.group(0)
    if automation_id:
        if name:
            call_tool(
                "automations_update",
                {"automationId": automation_id, "name": name},
                config=config,
            )
        call_tool(
            "automations_change_status",
            {"automationId": automation_id, "status": "active"},
            config=config,
        )
    return {
        "tool": "automations_quick_setup",
        "arguments": args,
        "raw": raw,
        "text": _extract_text(raw),
        "automation_id": automation_id,
        "automation_name": name,
        "payload": payload,
    }


def run_funnel_prompt(prompt: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    tools = list_tools(config)
    tool = pick_funnel_tool(tools)
    if not tool:
        raise ChatPlaceMcpError("ChatPlace MCP returned no tools")

    tool_name = str(tool.get("name") or "")
    input_schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
    properties = input_schema.get("properties") if isinstance(input_schema, dict) else {}
    arguments: dict[str, Any] = {}

    if isinstance(properties, dict):
        if "prompt" in properties:
            arguments["prompt"] = prompt
        elif "message" in properties:
            arguments["message"] = prompt
        elif "instruction" in properties:
            arguments["instruction"] = prompt
        elif "text" in properties:
            arguments["text"] = prompt
        elif "query" in properties:
            arguments["query"] = prompt
        elif len(properties) == 1:
            only_key = next(iter(properties.keys()))
            arguments[only_key] = prompt
        else:
            arguments["prompt"] = prompt
    else:
        arguments["prompt"] = prompt

    raw = call_tool(tool_name, arguments, config=config)
    text = _extract_text(raw)
    return {
        "tool": tool_name,
        "arguments": arguments,
        "raw": raw,
        "text": text,
    }


def extract_funnel_id(text: str) -> str | None:
    patterns = (
        r"(?i)automation[_ ]?id[:\s]+([A-Za-z0-9_-]+)",
        r"(?i)funnel[_ ]?id[:\s]+([A-Za-z0-9_-]+)",
        r"(?i)id[:\s]+([0-9]{4,})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None
