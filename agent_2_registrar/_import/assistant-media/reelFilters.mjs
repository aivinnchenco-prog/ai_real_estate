export const MAX_ZOOM = 1.15;
const ZOOM_RANGE = 0.15;

/**
 * Cover-crop to target aspect (no stretch), then centered zoom in/out only.
 * x/y locked to image center — no side panning.
 */
export function buildClipFilter(zoomIn, frames, w, h, fps) {
  const zw = Math.ceil(w * MAX_ZOOM);
  const zh = Math.ceil(h * MAX_ZOOM);
  const denom = Math.max(frames - 1, 1);

  const cover = `scale=${zw}:${zh}:force_original_aspect_ratio=increase,crop=${zw}:${zh}`;

  const zExpr = zoomIn
    ? `1+${ZOOM_RANGE}*on/${denom}`
    : `${MAX_ZOOM}-${ZOOM_RANGE}*on/${denom}`;

  const xExpr = "trunc(iw/2-(iw/zoom/2))";
  const yExpr = "trunc(ih/2-(ih/zoom/2))";

  return `${cover},zoompan=z='${zExpr}':x='${xExpr}':y='${yExpr}':d=${frames}:s=${w}x${h}:fps=${fps},format=yuv420p`;
}
