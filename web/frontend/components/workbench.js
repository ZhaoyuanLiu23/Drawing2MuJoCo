"use client";

import { useRef, useState } from "react";
import Icon from "./icon";
import PipelinePreview from "./pipeline-preview";
import SimulationResults from "./simulation-results";
import { DRAWING_EXTENSIONS, MAX_UPLOAD_BYTES, uploadDrawing, generateCad, simulateJob } from "../lib/api";

function CoordinateInput({ id, axis, unit, value, onChange, disabled, placeholder }) {
  return (
    <div className="coordinate-field">
      <label htmlFor={id}>{axis}</label>
      <div className="input-wrap">
        <input id={id} name={id} type="number" step="any" required value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled} placeholder={placeholder} aria-describedby={`${id}-unit`} />
        <span id={`${id}-unit`} className="input-unit">{unit}</span>
      </div>
    </div>
  );
}

export default function Workbench() {
  const [notice, setNotice] = useState("");
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [job, setJob] = useState(null);
  const [cad, setCad] = useState(null);
  const [simulation, setSimulation] = useState(null);
  const [coordinates, setCoordinates] = useState({ objectX: "", objectY: "", yaw: "", targetX: "", targetY: "" });
  const simulationInFlight = useRef(false);
  const running = simulation?.status === "running";
  const generating = cad?.status === "generating";
  const fileInput = useRef(null);
  const uploadInFlight = useRef(false);
  const cadInFlight = useRef(false);

  async function handleFiles(files) {
    if (uploadInFlight.current || cadInFlight.current || simulationInFlight.current || !files?.length) return;
    setUploadError("");
    if (files.length !== 1) {
      setUploadError("Please select one drawing at a time.");
      return;
    }
    const file = files[0];
    if (!DRAWING_EXTENSIONS.test(file.name)) {
      setUploadError("Only PDF, PNG, JPG and JPEG files are allowed.");
      return;
    }
    if (!file.size || file.size > MAX_UPLOAD_BYTES) {
      setUploadError(file.size ? "The maximum file size is 10 MiB." : "The drawing file is empty.");
      return;
    }
    uploadInFlight.current = true;
    setUploading(true);
    setNotice("");
    try {
      const uploaded = await uploadDrawing(file);
      setJob(uploaded);
      setCad(null);
      setSimulation(null);
      setNotice("图纸已上传，点击“生成 3D”调用 Drawing2CAD。");
    } catch (error) {
      setUploadError(`${error.message}${job ? " Your previous drawing is unchanged." : ""}`);
    } finally {
      uploadInFlight.current = false;
      setUploading(false);
    }
  }

  async function handleGenerate() {
    if (!job || uploadInFlight.current || cadInFlight.current || simulationInFlight.current) return;
    cadInFlight.current = true;
    setCad({ status: "generating" });
    setSimulation(null);
    setNotice("");
    try {
      const result = await generateCad(job.job_id);
      setCad(result);
      setNotice(result.status === "generated" ? "CAD 已生成，包含推断信息。使用前请检查解析 JSON，可通过保存按钮下载模型。" : "未生成模型，请查看 CAD 卡片中的原因。");
    } catch (error) {
      setCad({ status: "error", error: { message: error.message } });
    } finally {
      cadInFlight.current = false;
    }
  }

  async function handleSimulate(event) {
    event.preventDefault();
    if (!job || cad?.status !== "generated" || simulationInFlight.current || uploadInFlight.current || cadInFlight.current) return;
    if (Object.values(coordinates).some((value) => value.trim() === "" || !Number.isFinite(Number(value)))) {
      setNotice("请填写全部有限数字坐标和偏航角；不会自动使用默认位置。");
      return;
    }
    const parameters = {
      object_start: { x: Number(coordinates.objectX), y: Number(coordinates.objectY), yaw_deg: Number(coordinates.yaw) },
      target: { x: Number(coordinates.targetX), y: Number(coordinates.targetY) },
    };
    simulationInFlight.current = true;
    setSimulation({ status: "running" });
    setNotice("");
    try {
      const result = await simulateJob(job.job_id, parameters);
      setSimulation(result);
      setNotice(result.status === "succeeded" ? "仿真完成，最终视觉复核通过。可保存本次结果 JSON。" : "本次仿真未成功，请查看真实失败原因。");
    } catch (error) {
      setSimulation({ status: "failed", error: { code: "SIMULATION_RESPONSE_ERROR", message: error.message } });
    } finally {
      simulationInFlight.current = false;
    }
  }

  const coordinate = (key) => ({ value: coordinates[key], disabled: running, onChange: (value) => setCoordinates((current) => ({ ...current, [key]: value })) });

  return (
    <>
      <header className="site-header">
        <div className="header-inner">
          <a className="brand" href="/" aria-label="Drawing2MuJoCo home">
            <span className="brand-mark"><Icon name="cube" size={27} /></span>
            <span><strong>Drawing<span className="brand-two">2</span>MuJoCo</strong><span className="brand-subtitle">From Engineering Drawing to Robot Simulation</span></span>
          </a>
          <div className="header-actions">
            <span className="version-badge">WEB V0.1</span>
            <a className="github-link" aria-label="打开 GitHub 仓库" href="https://github.com/ZhaoyuanLiu23/Drawing2MuJoCo" target="_blank" rel="noreferrer"><Icon name="github" size={18} /><span>GitHub 仓库</span><Icon name="external" size={13} /></a>
          </div>
        </div>
      </header>

      <main className="workspace">
        <div className="workspace-heading">
          <div><p className="eyebrow workspace-label"><span className="tiny-grid" /> ENGINEERING WORKBENCH</p><h1>From drawing to motion<span>.</span></h1></div>
          <span className="preview-badge"><span />Drawing → CAD</span>
        </div>

        <section
          className={`upload-zone${dragging ? " is-dragging" : ""}`}
          aria-labelledby="upload-heading"
          aria-busy={uploading}
          onDragEnter={(event) => { event.preventDefault(); if (!uploadInFlight.current && !cadInFlight.current && !simulationInFlight.current) setDragging(true); }}
          onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = uploading || generating || running ? "none" : "copy"; }}
          onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setDragging(false); }}
          onDrop={(event) => { event.preventDefault(); setDragging(false); handleFiles(event.dataTransfer.files); }}
        >
          <span className="upload-icon"><Icon name="upload" size={26} /></span>
          <div className="upload-copy">
            <h2 id="upload-heading">{uploading ? "上传中..." : job ? job.original_filename : "Start with an engineering drawing"}</h2>
            <p>Drag &amp; drop <span className="dot-divider">·</span> PDF / PNG / JPG / JPEG <span className="file-size-note">· Max 10 MiB</span></p>
            {job && <p className="job-reference" data-job-id={job.job_id}>Job: {job.job_id}</p>}
          </div>
          <input ref={fileInput} className="file-input" type="file" accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg" aria-label="选择工程图" disabled={uploading || generating || running} onChange={(event) => { handleFiles(event.target.files); event.target.value = ""; }} />
          <div className="upload-actions">
            <button className="button button-secondary" type="button" disabled={uploading || generating || running} onClick={() => fileInput.current?.click()}><Icon name="upload" size={17} />{uploading ? "上传中..." : job ? "更换图纸" : "上传图纸"}</button>
            {job && <button className="button button-primary" type="button" disabled={uploading || generating || running} onClick={handleGenerate}><Icon name="cube" size={17} />{generating ? "生成中..." : "生成 3D"}</button>}
          </div>
        </section>
        {uploadError && <p className="upload-error" role="alert">{uploadError}</p>}

        <div className="pipeline-caption"><span>THE PIPELINE</span><span>2D input <Icon name="arrow" size={12} /> Parametric model <Icon name="arrow" size={12} /> Robot motion</span></div>
        <PipelinePreview job={job} cad={cad} simulation={simulation} />

        <div className="bottom-grid">
          <form className="panel setup-panel" aria-labelledby="setup-heading" onSubmit={handleSimulate}>
            <header className="panel-heading"><Icon name="sliders" /><h2 id="setup-heading">仿真设置</h2><span className="panel-meta">世界坐标系</span></header>
            <div className="setup-fields">
              <fieldset><legend>物体初始位置</legend><div className="coordinate-row"><CoordinateInput id="object-x" axis="X 坐标" unit="米" placeholder="例如 0.50" {...coordinate("objectX")} /><CoordinateInput id="object-y" axis="Y 坐标" unit="米" placeholder="例如 0.00" {...coordinate("objectY")} /><CoordinateInput id="object-yaw" axis="偏航角" unit="度" placeholder="例如 0" {...coordinate("yaw")} /></div></fieldset>
              <fieldset><legend>目标位置</legend><div className="coordinate-row target-fields"><CoordinateInput id="target-x" axis="X 坐标" unit="米" placeholder="例如 0.46" {...coordinate("targetX")} /><CoordinateInput id="target-y" axis="Y 坐标" unit="米" placeholder="例如 -0.09" {...coordinate("targetY")} /></div></fieldset>
            </div>
            <p className="simulation-note">全部参数必填，不自动替换位置。测试密度 7800 千克/立方米（非识别材料），目标区域 0.16 × 0.16 米。</p>
            <div className="setup-footer"><span>{cad?.status === "generated" ? "当前 CAD · 视觉驱动 · 完成后播放录像" : "请先生成 CAD"}</span><button type="submit" className="button button-primary" disabled={!job || cad?.status !== "generated" || uploading || generating || running}><Icon name="play" size={16} />{running ? "正在运行仿真..." : "运行仿真"}<Icon name="arrow" size={16} /></button></div>
          </form>
          <SimulationResults result={simulation} />
        </div>

        <p className={`preview-notice${notice ? " has-notice" : ""}`} role="status" aria-live="polite">{uploading ? "正在上传图纸并生成预览..." : generating ? "正在从图纸生成 CAD..." : running ? "正在运行真实 Panda 仿真，请等待视觉复核结果..." : notice || "Web V0.1 · 图纸 → CAD → 视觉抓取放置。"}</p>
        <footer className="workspace-footer"><span>DRAWING2MUJOCO <span>/</span> LOCAL WORKSPACE</span><span>Engineering → Geometry → Motion</span></footer>
      </main>
    </>
  );
}
