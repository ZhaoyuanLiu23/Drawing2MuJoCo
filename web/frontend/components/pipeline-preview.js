"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import Icon from "./icon";
import SimulationPreview from "./simulation-preview";

const StlViewer = dynamic(() => import("./stl-viewer"), { ssr: false, loading: () => <div className="stl-viewer viewer-loading">加载 3D 查看器...</div> });

function CadPreview({ cad }) {
  if (cad.status === "generated") {
    return (
      <div className="stage-canvas cad-viewer-panel">
        <StlViewer url={cad.stl_url} />
        <div className="cad-summary"><strong>CAD 已生成</strong><span>拖动旋转 · 滚轮缩放</span></div>
        <div className="cad-downloads"><a href={cad.stl_url} download>保存 STL ↓</a><a href={cad.step_url} download>保存 STEP ↓</a><a href={cad.parsed_json_url} download>保存解析 JSON ↓</a></div>
        <p className="cad-assumptions">含推断信息 · 请核查解析 JSON</p>
      </div>
    );
  }
  return (
    <div className="stage-canvas cad-status-panel" aria-busy={cad.status === "generating"}>
      <Icon name="cube" size={32} />
      <strong>{cad.status === "generating" ? "生成中..." : cad.status === "needs_review" ? "需要复核" : "生成失败"}</strong>
      <p role={cad.error ? "alert" : "status"}>{cad.error?.message || "Drawing2CAD is processing your drawing."}</p>
      {cad.parsed_json_url && <a href={cad.parsed_json_url} download>保存解析 JSON ↓</a>}
    </div>
  );
}

function UploadedDrawing({ job }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="stage-canvas drawing-preview">
      <span className="canvas-label">{job.file_type === "pdf" ? "PDF · PAGE 1" : job.file_type.toUpperCase()}</span>
      {failed ? <p className="image-error" role="alert">Preview could not be loaded. Please upload again.</p> : (
        <a className="drawing-image-link" href={job.preview_url} target="_blank" rel="noreferrer" aria-label="查看图纸大图">
          <img src={job.preview_url} alt={`Drawing preview: ${job.original_filename}${job.file_type === "pdf" ? " (page 1)" : ""}`} onError={() => setFailed(true)} />
        </a>
      )}
      <p className="drawing-filename" title={job.original_filename}>{job.original_filename}</p>
      <span className="stage-detail">{job.file_type === "pdf" ? "PDF 首页预览 · 点击查看大图" : "图纸预览 · 点击查看大图"}</span>
    </div>
  );
}

function DrawingGraphic() {
  return (
    <svg viewBox="0 0 240 150" fill="none" aria-hidden="true">
      <rect x="61" y="20" width="118" height="112" rx="3" fill="var(--diagram-paper)" stroke="var(--diagram-faint)" />
      <g stroke="currentColor" strokeWidth="1.4">
        <rect x="81" y="54" width="78" height="45" rx="2" />
        <circle cx="100" cy="76" r="8" /><circle cx="140" cy="76" r="8" />
        <path d="M78 42h84M81 36v12m78-12v12M169 51v52m-4-49h8m-8 45h8" />
      </g>
      <g stroke="var(--diagram-faint)"><path d="M91 76h18m-9-9v18m31-9h18m-9-9v18M75 115h29m7 0h39" /></g>
    </svg>
  );
}

function CadGraphic() {
  return (
    <svg viewBox="0 0 240 150" fill="none" aria-hidden="true">
      <path d="m47 78 100-40 48 31-100 43Z" fill="var(--diagram-paper)" stroke="currentColor" strokeWidth="1.5" />
      <path d="m47 78 48 34v16L47 94Zm48 34 100-43v16L95 128Z" fill="var(--diagram-fill)" stroke="currentColor" strokeWidth="1.5" />
      <ellipse cx="103" cy="76" rx="13" ry="7" transform="rotate(-22 103 76)" stroke="currentColor" strokeWidth="1.5" />
      <ellipse cx="148" cy="67" rx="13" ry="7" transform="rotate(-22 148 67)" stroke="currentColor" strokeWidth="1.5" />
      <path d="M29 112v23l20 10m-20-10 20-8" stroke="var(--diagram-faint)" />
    </svg>
  );
}

function RobotGraphic() {
  return (
    <svg viewBox="0 0 240 150" fill="none" aria-hidden="true">
      <path d="m40 119 94-25 66 29-95 23Z" fill="var(--diagram-paper)" stroke="var(--diagram-faint)" />
      <g stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round">
        <path d="M69 112v-9h34v9l-17 5Z" fill="var(--diagram-fill)" />
        <path d="M78 103V71l12-2 3 34" fill="var(--diagram-paper)" />
        <path d="m78 68 21-40 12 7-21 40Z" fill="var(--diagram-fill)" />
        <path d="m111 28 48 26-7 12-48-25Z" fill="var(--diagram-paper)" />
        <circle cx="106" cy="35" r="10" fill="var(--diagram-paper)" />
        <circle cx="84" cy="72" r="9" fill="var(--diagram-paper)" />
        <circle cx="154" cy="61" r="8" fill="var(--diagram-paper)" />
        <path d="M154 69v13m-10 7v-7h20v7m-20 0v6m20-6v6" />
        <path d="m133 111 21-7 18 8-21 7Z" fill="var(--diagram-fill)" />
      </g>
      <path d="m128 114 23 10 27-11" stroke="var(--diagram-faint)" strokeDasharray="3 3" />
    </svg>
  );
}

const stages = [
  { title: "2D Drawing", label: "INPUT", empty: "Awaiting a drawing", detail: "Your engineering drawing appears here", Graphic: DrawingGraphic },
  { title: "3D CAD", label: "GEOMETRY", empty: "No model generated", detail: "A space for the reconstructed CAD", Graphic: CadGraphic },
  { title: "Robot Simulation", label: "MOTION", empty: "Ready when you are", detail: "A space for Panda simulation", Graphic: RobotGraphic },
];

export default function PipelinePreview({ job, cad, simulation }) {
  return (
    <section className={`pipeline${cad?.status === "generated" ? " has-cad" : ""}`} aria-label="Drawing to CAD to robot simulation">
      {stages.map(({ title, label, empty, detail, Graphic }, index) => (
        <article className="stage panel" key={title}>
          <header className="stage-heading">
            <span className="stage-number">0{index + 1}</span>
            <h2>{title}</h2>
            <span className="eyebrow">{label}</span>
          </header>
          {index === 0 && job ? <UploadedDrawing key={job.job_id} job={job} /> : index === 1 && cad ? <CadPreview cad={cad} /> : index === 2 && simulation ? <SimulationPreview result={simulation} /> : <div className={`stage-canvas${index === 2 ? " simulation-placeholder" : ""}`}>
            <span className="canvas-label">PLACEHOLDER</span>
            <Graphic />
            <p>{empty}</p>
            <span className="stage-detail">{index === 2 ? "生成 CAD 并填写参数后可运行仿真" : detail}</span>
            {index === 2 && <button className="button button-secondary simulation-save" type="button" disabled title="运行仿真后可保存结果 JSON">保存仿真结果</button>}
          </div>}
          {index < stages.length - 1 && <span className="stage-connector"><Icon name="arrow" size={16} /></span>}
        </article>
      ))}
    </section>
  );
}
