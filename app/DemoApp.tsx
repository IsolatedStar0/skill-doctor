"use client";

import { useEffect, useMemo, useState } from "react";
import {
  demoCase,
  diagnose,
  proposePatch,
  validatePatch,
  type TraceStep,
} from "../lib/demo-engine";

type View = "overview" | "trace" | "diagnosis" | "patch";

const views: { id: View; label: string; eyebrow: string }[] = [
  { id: "overview", label: "运行概览", eyebrow: "00" },
  { id: "trace", label: "证据轨迹", eyebrow: "01" },
  { id: "diagnosis", label: "故障归因", eyebrow: "02" },
  { id: "patch", label: "修复验证", eyebrow: "03" },
];

const stageLabels = ["冻结证据", "定位故障", "生成补丁", "回放验证"];

function Percent({ value }: { value: number }) {
  return <>{Math.round(value * 100)}%</>;
}

function TraceCard({ step, index }: { step: TraceStep; index: number }) {
  return (
    <article className={`trace-card trace-${step.status}`}>
      <div className="trace-rail">
        <span>{String(index + 1).padStart(2, "0")}</span>
        <i />
      </div>
      <div className="trace-body">
        <div className="trace-meta">
          <span>{step.at}</span>
          <span>{step.kind}</span>
          <span className={`status-dot ${step.status}`}>{step.status}</span>
        </div>
        <h3>{step.title}</h3>
        <p>{step.detail}</p>
        {step.evidence && <code>{step.evidence}</code>}
      </div>
    </article>
  );
}

export default function DemoApp() {
  const [view, setView] = useState<View>("overview");
  const [runKey, setRunKey] = useState(0);
  const [stage, setStage] = useState(4);

  const result = useMemo(() => {
    const diagnosis = diagnose(demoCase);
    const patch = proposePatch(demoCase, diagnosis);
    const validation = validatePatch(demoCase, diagnosis, patch);
    return { diagnosis, patch, validation };
  }, []);

  useEffect(() => {
    if (runKey === 0) return;
    setStage(0);
    const timer = window.setInterval(() => {
      setStage((current) => {
        if (current >= 4) {
          window.clearInterval(timer);
          return 4;
        }
        return current + 1;
      });
    }, 620);
    return () => window.clearInterval(timer);
  }, [runKey]);

  const rerun = () => {
    setView("overview");
    setRunKey((value) => value + 1);
  };

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div>
          <div className="brand-mark">SD</div>
          <p className="brand-name">Skill Doctor</p>
          <p className="brand-caption">Trace-driven skill evolution</p>
        </div>

        <nav aria-label="Demo sections">
          {views.map((item) => (
            <button
              type="button"
              key={item.id}
              className={view === item.id ? "active" : ""}
              onClick={() => setView(item.id)}
              data-testid={`nav-${item.id}`}
            >
              <span>{item.eyebrow}</span>
              {item.label}
            </button>
          ))}
        </nav>

        <div className="sidebar-foot">
          <span className="live-dot" />
          <span>DETERMINISTIC DEMO</span>
          <small>case-revenue-042</small>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <span className="kicker">SKILL EVOLUTION LAB / LOCAL RUN</span>
            <h1>
              让每一次失败
              <br />
              <em>留下可验证的修复。</em>
            </h1>
          </div>
          <button className="run-button" type="button" onClick={rerun}>
            <span>运行完整闭环</span>
            <b>↗</b>
          </button>
        </header>

        <div className="stage-strip" aria-label="Pipeline progress">
          {stageLabels.map((label, index) => (
            <div key={label} className={stage > index ? "complete" : stage === index ? "running" : ""}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{label}</strong>
              <i />
            </div>
          ))}
        </div>

        {view === "overview" && (
          <section className="view-grid">
            <article className="case-panel panel">
              <div className="panel-heading">
                <span>FAILED SESSION</span>
                <strong>可复现</strong>
              </div>
              <h2>{demoCase.task}</h2>
              <div className="outcome-grid">
                <div>
                  <span>EXPECTED</span>
                  <strong>{demoCase.expected}</strong>
                </div>
                <div className="bad-value">
                  <span>ACTUAL</span>
                  <strong>{demoCase.actual}</strong>
                </div>
              </div>
              <dl className="case-facts">
                <div><dt>Trace</dt><dd>5 steps / 2 tools</dd></div>
                <div><dt>Skill</dt><dd>{demoCase.skill.id}@{demoCase.skill.version}</dd></div>
                <div><dt>Runtime</dt><dd>local deterministic harness</dd></div>
              </dl>
            </article>

            <article className="decision-panel panel">
              <div className="panel-heading">
                <span>RELEASE DECISION</span>
                <strong className="adopt-chip">{result.validation.decision}</strong>
              </div>
              <div className="score-ring">
                <div><strong>0</strong><span>→</span><strong>1</strong></div>
                <p>original replay</p>
              </div>
              <ul className="reason-list">
                {result.validation.reasons.map((reason) => (
                  <li key={reason}><span>✓</span>{reason}</li>
                ))}
              </ul>
            </article>

            <article className="metric-card panel">
              <span>ATTRIBUTION</span>
              <strong><Percent value={result.diagnosis.confidence} /></strong>
              <p>归因置信度</p>
              <div style={{ "--meter": "91%" } as React.CSSProperties}><i /></div>
            </article>
            <article className="metric-card panel">
              <span>RESPONSIBILITY</span>
              <strong><Percent value={result.diagnosis.responsibility} /></strong>
              <p>目标 Skill 责任权重</p>
              <div style={{ "--meter": "86%" } as React.CSSProperties}><i /></div>
            </article>
            <article className="metric-card panel">
              <span>REGRESSION</span>
              <strong>0</strong>
              <p>4 个历史案例无退化</p>
              <div style={{ "--meter": "100%" } as React.CSSProperties}><i /></div>
            </article>
          </section>
        )}

        {view === "trace" && (
          <section className="trace-view">
            <div className="section-intro">
              <div>
                <span className="kicker">EVIDENCE SNAPSHOT</span>
                <h2>从加载 Skill 到结果失败，证据链保持完整。</h2>
              </div>
              <dl>
                <div><dt>Snapshot</dt><dd>sha256:8a7f…42c1</dd></div>
                <div><dt>Loaded</dt><dd>spreadsheet-summary@1.2.0</dd></div>
              </dl>
            </div>
            <div className="timeline">
              {demoCase.trace.map((step, index) => (
                <TraceCard key={step.id} step={step} index={index} />
              ))}
            </div>
          </section>
        )}

        {view === "diagnosis" && (
          <section className="diagnosis-view">
            <div className="section-intro">
              <div>
                <span className="kicker">STEP-LEVEL ATTRIBUTION</span>
                <h2>最早可行动故障发生在 <em>{result.diagnosis.primaryFaultStep}</em>。</h2>
              </div>
              <div className="confidence-block">
                <strong><Percent value={result.diagnosis.confidence} /></strong>
                <span>confidence</span>
              </div>
            </div>

            <div className="diagnosis-grid">
              <article className="taxonomy-card panel">
                <span>7-CLASS TAXONOMY</span>
                <div className="taxonomy-list">
                  {[
                    "Skill Recall Failure",
                    "Selection Error",
                    "Loading Miss",
                    "Instruction Violation",
                    "Tool Misuse",
                    "Content Gap",
                    "Non-Skill Cause",
                  ].map((item) => (
                    <div key={item} className={item === result.diagnosis.taxonomy ? "selected" : ""}>
                      <i /><span>{item}</span>
                      {item === result.diagnosis.taxonomy && <b>0.91</b>}
                    </div>
                  ))}
                </div>
              </article>

              <article className="mechanism-card panel">
                <div className="panel-heading">
                  <span>CAUSAL MECHANISM</span>
                  <strong>{result.diagnosis.action}</strong>
                </div>
                <blockquote>{result.diagnosis.mechanism}</blockquote>
                <div className="fault-chain">
                  {result.diagnosis.faultChain.map((item, index) => (
                    <span key={item}>
                      {item}
                      {index < result.diagnosis.faultChain.length - 1 && <b>→</b>}
                    </span>
                  ))}
                </div>
                <h3>Evidence refs</h3>
                <div className="evidence-list">
                  {result.diagnosis.evidenceRefs.map((ref) => <code key={ref}>{ref}</code>)}
                </div>
              </article>
            </div>
          </section>
        )}

        {view === "patch" && (
          <section className="patch-view">
            <div className="section-intro">
              <div>
                <span className="kicker">SCOPED REPAIR + QUALIFICATION</span>
                <h2>只改动一条指令，然后用失败案例和回归集证明它。</h2>
              </div>
              <div className="version-badge">
                <span>{result.patch.baseVersion}</span><b>→</b><strong>{result.patch.nextVersion}</strong>
              </div>
            </div>

            <div className="patch-grid">
              <article className="diff-panel panel">
                <div className="panel-heading">
                  <span>SKILL.MD / PROCEDURE</span>
                  <strong>1 LINE CHANGED</strong>
                </div>
                <div className="diff-lines">
                  {result.patch.before.map((line, index) => {
                    const changed = index + 1 === result.patch.changedLine;
                    return changed ? (
                      <div className="diff-change" key={line}>
                        <p className="removed"><span>-</span>{line}</p>
                        <p className="added"><span>+</span>{result.patch.after[index]}</p>
                      </div>
                    ) : (
                      <p key={line}><span>{index + 1}</span>{line}</p>
                    );
                  })}
                </div>
                <footer>
                  <span>scope: {result.patch.scope}</span>
                  <span>rollback: {result.patch.rollbackRef}</span>
                </footer>
              </article>

              <article className="validation-panel panel">
                <div className="panel-heading">
                  <span>VALIDATION GATE</span>
                  <strong className="adopt-chip">{result.validation.decision}</strong>
                </div>
                <div className="validation-bars">
                  <div>
                    <span>Original replay</span>
                    <p><i style={{ width: `${result.validation.originalReplay.after * 100}%` }} /></p>
                    <strong>0 → 1</strong>
                  </div>
                  <div>
                    <span>Similar cases</span>
                    <p><i style={{ width: `${result.validation.similarCases.after * 100}%` }} /></p>
                    <strong>50 → 100%</strong>
                  </div>
                  <div>
                    <span>Regression</span>
                    <p><i style={{ width: `${result.validation.regression.after * 100}%` }} /></p>
                    <strong>100 → 100%</strong>
                  </div>
                  <div>
                    <span>Tool errors</span>
                    <p><i style={{ width: "0%" }} /></p>
                    <strong>1 → 0</strong>
                  </div>
                </div>
                <div className="gate-rule">
                  original_fixed <b>AND</b> cluster_improved <b>AND</b> no_regression
                </div>
              </article>
            </div>
          </section>
        )}
      </section>
    </main>
  );
}
