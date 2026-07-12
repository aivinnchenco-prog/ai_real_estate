/** Env overrides for config/pipeline.json (Docker: CURATOR_BASE_URL=http://curator:8077). */
export function applyEnvOverrides(cfg) {
  const out = structuredClone(cfg);
  const base = (process.env.CURATOR_BASE_URL || "").replace(/\/$/, "");
  if (base) {
    out.curator_url = `${base}/select`;
    out.curator_health_url = `${base}/health`;
    out.seedance = out.seedance || {};
    out.seedance.curator_diverse_url = `${base}/select-diverse`;
  }
  return out;
}
