export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const DRAWING_EXTENSIONS = /\.(pdf|png|jpe?g)$/i;

export async function simulateJob(jobId, parameters) {
  let response;
  try {
    response = await fetch(`/api/jobs/${jobId}/simulate`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(parameters),
    });
  } catch {
    throw new Error("仿真服务连接失败。不能确定后台执行结果，请检查本次 simulation/result.json。");
  }
  const result = await response.json().catch(() => null);
  if (!result || result.job_id !== jobId || !["succeeded", "failed", "rejected"].includes(result.status)) {
    throw new Error("仿真服务返回无效结果，请检查后台日志。");
  }
  if (result.status === "succeeded" && (!response.ok || result.visual_task_success !== true)) {
    throw new Error("没有收到完整的视觉成功证据。");
  }
  if (result.result_json_url && (!/^[0-9a-f]{32}$/.test(result.run_id) || result.result_json_url !== `/api/jobs/${jobId}/simulation/${result.run_id}/result.json`)) {
    throw new Error("仿真结果文件地址无效。");
  }
  if (result.video_available === true && (!/^[0-9a-f]{32}$/.test(result.run_id) || result.video_url !== `/api/jobs/${jobId}/simulation/${result.run_id}/simulation.mp4`)) {
    throw new Error("仿真视频地址无效。");
  }
  if (result.video_available !== true) result.video_url = null;
  return result;
}

export async function generateCad(jobId) {
  let response;
  try {
    response = await fetch(`/api/jobs/${jobId}/generate-cad`, { method: "POST" });
  } catch {
    throw new Error("CAD service unavailable. Check the backend and try again.");
  }
  const result = await response.json().catch(() => null);
  if (!result || result.job_id !== jobId || !["generated", "needs_review", "error"].includes(result.status)) {
    throw new Error("CAD generation could not complete. Check the backend and try again.");
  }
  for (const [key, filename] of [["stl_url", "model.stl"], ["step_url", "model.step"], ["parsed_json_url", "parsed.json"]]) {
    if (result[key] != null && result[key] !== `/api/jobs/${jobId}/cad/${filename}`) throw new Error("Invalid CAD artifact URL.");
    if (result.status === "generated" && !result[key]) throw new Error("The CAD export is incomplete.");
  }
  if (!response.ok && result.status === "generated") throw new Error("The CAD export did not succeed.");
  return result;
}

export async function uploadDrawing(file) {
  const form = new FormData();
  form.append("file", file);
  let response;
  try {
    response = await fetch("/api/jobs", { method: "POST", body: form });
  } catch {
    throw new Error("Upload service unavailable. Check the backend and try again.");
  }
  const result = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(result?.error?.message || "Upload failed. Check the backend and try again.");
  }
  if (!/^[0-9a-f]{32}$/.test(result?.job_id) || result.preview_url !== `/api/jobs/${result.job_id}/preview`) {
    throw new Error("The upload service returned an invalid response.");
  }
  return result;
}
