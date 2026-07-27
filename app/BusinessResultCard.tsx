"use client";

import { useState } from "react";
import type { BusinessResult } from "../lib/langgraph-stream";

const verdictConfig: Record<
  BusinessResult["verdict_type"],
  { icon: string; className: string; label: string }
> = {
  pass: { icon: "✓", className: "br-pass", label: "PASS" },
  fail: { icon: "✗", className: "br-fail", label: "FAIL" },
  warning: { icon: "⚠", className: "br-warning", label: "WARNING" },
};

export default function BusinessResultCard({
  result,
}: {
  result: BusinessResult;
}) {
  const [extraExpanded, setExtraExpanded] = useState(false);
  const config = verdictConfig[result.verdict_type];
  const hasExtra = Object.keys(result.extra).length > 0;

  return (
    <article className={`business-result-card panel ${config.className}`}>
      <div className="br-header">
        <div className="br-verdict-row">
          <span className="br-icon">{config.icon}</span>
          <h3 className="br-verdict">{result.verdict}</h3>
          <span className="br-badge">{config.label}</span>
        </div>
        {result.confidence !== undefined && result.confidence !== null ? (
          <div className="br-confidence">
            <span>CONFIDENCE</span>
            <strong>{Math.round(result.confidence * 100)}%</strong>
          </div>
        ) : null}
      </div>

      {result.details.length > 0 ? (
        <ul className="br-details">
          {result.details.map((item) => (
            <li
              key={item.name}
              className={`br-detail-item br-detail-${item.status}`}
            >
              <span className="br-detail-icon">
                {item.status === "pass"
                  ? "✓"
                  : item.status === "fail"
                    ? "✗"
                    : "⚠"}
              </span>
              <div>
                <strong>{item.name}</strong>
                {item.reason ? <p>{item.reason}</p> : null}
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      {hasExtra ? (
        <div className="br-extra">
          <button
            type="button"
            className="br-extra-toggle"
            onClick={() => setExtraExpanded((prev) => !prev)}
          >
            {extraExpanded ? "▾ 收起详情" : "▸ 展开 extra 数据"}
          </button>
          {extraExpanded ? (
            <pre className="br-extra-content">
              {JSON.stringify(result.extra, null, 2)}
            </pre>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
