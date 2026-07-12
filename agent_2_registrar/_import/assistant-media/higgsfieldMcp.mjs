/**
 * Higgsfield official MCP client (Streamable HTTP).
 * Auth: OAuth Bearer token — HIGGSFIELD_MCP_ACCESS_TOKEN or HIGGSFIELD_OAUTH_TOKEN
 * URL: https://mcp.higgsfield.ai/mcp
 *
 * Note: platform API keys (Key id:secret) do NOT work for MCP — OAuth only.
 */
import { buildPrompt } from "./higgsfieldClient.mjs";

let sessionId = null;

function mcpUrl(cfg) {
  return cfg.mcp_url || process.env.HIGGSFIELD_MCP_URL || "https://mcp.higgsfield.ai/mcp";
}

function bearerToken() {
  return process.env.HIGGSFIELD_MCP_ACCESS_TOKEN || process.env.HIGGSFIELD_OAUTH_TOKEN;
}

function headers(extra = {}) {
  const token = bearerToken();
  if (!token) throw new Error("HIGGSFIELD_MCP_ACCESS_TOKEN not set (OAuth from MCP connect)");
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
    ...(sessionId ? { "Mcp-Session-Id": sessionId } : {}),
    ...extra,
  };
}

async function rpc(method, params, cfg) {
  const body = { jsonrpc: "2.0", id: Date.now(), method, params };
  const res = await fetch(mcpUrl(cfg), {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
  });

  const newSession = res.headers.get("mcp-session-id");
  if (newSession) sessionId = newSession;

  const text = await res.text();
  if (!res.ok) {
    throw new Error(`MCP HTTP ${res.status}: ${text.slice(0, 300)}`);
  }

  // SSE or JSON
  if (text.includes("data:")) {
    for (const line of text.split("\n")) {
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload || payload === "[DONE]") continue;
      try {
        const parsed = JSON.parse(payload);
        if (parsed.error) throw new Error(parsed.error.message || JSON.stringify(parsed.error));
        if (parsed.result !== undefined) return parsed.result;
      } catch {
        /* continue */
      }
    }
    throw new Error(`MCP SSE parse failed: ${text.slice(0, 300)}`);
  }

  const data = JSON.parse(text);
  if (data.error) throw new Error(data.error.message || JSON.stringify(data.error));
  return data.result;
}

export function isMcpConfigured(cfg) {
  if (cfg.enabled === false) return false;
  return Boolean(bearerToken());
}

export async function ensureMcpSession(cfg) {
  if (sessionId) return sessionId;
  await rpc(
    "initialize",
    {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "agent3-seedance", version: "1.0.0" },
    },
    cfg
  );
  await rpc("notifications/initialized", {}, cfg).catch(() => {});
  return sessionId;
}

export async function listMcpTools(cfg) {
  await ensureMcpSession(cfg);
  const result = await rpc("tools/list", {}, cfg);
  return result?.tools || [];
}

function pickGenerateTool(tools) {
  const names = tools.map((t) => t.name);
  const preferred = [
    "generate_create",
    "generate",
    "higgsfield_generate_create",
    "seedance_2_0",
  ];
  for (const p of preferred) {
    const hit = names.find((n) => n === p || n.includes(p));
    if (hit) return hit;
  }
  return names.find((n) => /generate|seedance|video/i.test(n));
}

function parseToolVideoUrl(result) {
  const text = typeof result === "string" ? result : JSON.stringify(result);
  const mp4 = text.match(/https:\/\/[^\s"'`]+\.mp4[^\s"'`]*/);
  if (mp4) return mp4[0];
  if (result?.content) {
    for (const block of result.content) {
      if (block.type === "text" && block.text) {
        const u = block.text.match(/https:\/\/[^\s"'`]+\.mp4[^\s"'`]*/);
        if (u) return u[0];
      }
    }
  }
  throw new Error(`MCP tool result has no video URL: ${text.slice(0, 400)}`);
}

export async function generateSeedanceViaMcp({ imageUrls, prompt, cfg }) {
  await ensureMcpSession(cfg);
  const tools = await listMcpTools(cfg);
  const toolName = pickGenerateTool(tools);
  if (!toolName) {
    throw new Error(`MCP: no generate tool found (${tools.map((t) => t.name).join(", ")})`);
  }

  const args = {
    job_type: cfg.model || "seedance_2_0",
    model: cfg.model || "seedance_2_0",
    prompt,
    aspect_ratio: cfg.aspect_ratio || "9:16",
    duration: cfg.batch_duration_seconds || cfg.duration_seconds || 12,
    resolution: cfg.resolution || "720p",
    image_urls: imageUrls,
    images: imageUrls,
    wait: true,
  };

  console.log(`Higgsfield MCP tool: ${toolName}, ${imageUrls.length} images`);
  const result = await rpc("tools/call", { name: toolName, arguments: args }, cfg);
  return parseToolVideoUrl(result);
}

export { buildPrompt };
