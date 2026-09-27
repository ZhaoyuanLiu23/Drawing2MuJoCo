import Icon from "./icon";

const statuses = { running: "正在仿真", succeeded: "任务成功", failed: "任务失败", rejected: "请求已拒绝" };
export const simulationStatus = (result) => statuses[result?.status] || "等待运行";

export default function SimulationResults({ result }) {
  const stepStatus = (skill) => {
    const step = result?.steps?.find((entry) => entry.skill === skill);
    if (step?.status === "succeeded") return skill === "verify" ? "通过" : "阶段已完成";
    if (step?.status === "failed") return "失败";
    if (step?.status === "skipped") return "未执行";
    return result?.status === "running" ? "运行中，等待结果" : result ? "未完成" : "待执行";
  };
  const metric = (value) => Number.isFinite(value) ? value.toFixed(3) : "—";
  return (
    <section className="panel results-panel" aria-labelledby="results-heading" aria-busy={result?.status === "running"}>
      <header className="panel-heading"><Icon name="results" /><h2 id="results-heading">运行结果</h2><span className="panel-meta">{result ? "真实仿真" : "尚未运行"}</span></header>
      <div className="task-status"><span>任务状态</span><span className="waiting-badge"><Icon name="clock" size={14} />{simulationStatus(result)}</span></div>
      <dl className="result-checks">{[["pick", "抓取"], ["place", "放置"], ["verify", "视觉复核"]].map(([skill, name]) => <div key={skill}><dt><span className="empty-check">—</span>{name}</dt><dd>{stepStatus(skill)}</dd></div>)}</dl>
      <dl className="result-metrics"><div><dt>位置误差</dt><dd>{metric(result?.position_error_mm)} <span>毫米</span></dd></div><div><dt>姿态误差</dt><dd>{metric(result?.orientation_error_deg)} <span>度</span></dd></div></dl>
      {result && result.status !== "running" && <p className="simulation-note">位置误差来自释放后的视觉复核。{result.orientation_error_reason || "姿态误差未提供。"}抓取阶段完成不代表独立验证了持物成功。</p>}
      {result?.error && <p className="simulation-error" role="alert">{result.error.code}：{result.error.message}</p>}
    </section>
  );
}
