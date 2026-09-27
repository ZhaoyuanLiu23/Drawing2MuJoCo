"use client";

import { useState } from "react";
import Icon from "./icon";
import { simulationStatus } from "./simulation-results";

export default function SimulationPreview({ result }) {
  const [failedUrl, setFailedUrl] = useState(null);
  const running = result.status === "running";
  const video = !running && result.video_available === true && result.video_url && failedUrl !== result.video_url;
  return (
    <div className={`stage-canvas simulation-status${video ? " has-video" : ""}`} aria-busy={running}>
      {video ? <video key={result.video_url} className="simulation-video" src={result.video_url}
        controls autoPlay muted playsInline preload="metadata" aria-label="本次真实仿真录像"
        onError={() => setFailedUrl(result.video_url)} /> : <Icon name="results" size={32} />}
      <strong className={result.status === "failed" || result.status === "rejected" ? "simulation-failed" : ""}>
        {simulationStatus(result)}{result.status === "failed" ? " · FAILED" : ""}
      </strong>
      <span className="stage-detail">{running ? "正在运行仿真并录制..." : video ? "本次执行录像 · 可播放、暂停和拖动进度" : "视频不可用 · Video unavailable"}</span>
      {result.video?.error && <span className="stage-detail">录像未完整生成；任务判定不受影响</span>}
      <div className="simulation-downloads">
        {video && <a className="button button-secondary simulation-save" href={result.video_url} download="simulation.mp4">保存视频</a>}
        {result.result_json_url && <a className="button button-secondary simulation-save" href={result.result_json_url} download>保存仿真结果</a>}
      </div>
    </div>
  );
}
