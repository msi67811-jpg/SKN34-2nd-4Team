import { useEffect, useState } from "react";

import {
  getCampaignPerformance,
  type CampaignPerformance,
} from "../../api/campaigns";

type CampaignPerformancePanelProps = {
  campaignId: number;
  refreshKey: number;
};

function formatNumber(value: number): string {
  return new Intl.NumberFormat("ko-KR").format(value);
}

function formatPercent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("ko-KR", {
    style: "currency",
    currency: "KRW",
    maximumFractionDigits: 0,
  }).format(value);
}

function isCampaignPerformance(value: CampaignPerformance): boolean {
  return Boolean(
    value
    && value.summary
    && Array.isArray(value.by_campaign)
    && Array.isArray(value.by_segment)
    && Array.isArray(value.by_assignee),
  );
}

function PerformanceCard({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <article className="campaign-performance-card">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </article>
  );
}

function BreakdownTable({
  title,
  items,
}: {
  title: string;
  items: CampaignPerformance["by_campaign"];
}) {
  return (
    <section className="campaign-performance-breakdown">
      <div className="campaign-panel-heading">
        <h4>{title}</h4>
        <span>{formatNumber(items.length)}개</span>
      </div>
      {items.length === 0 ? (
        <p className="campaign-empty-copy">비교할 데이터가 없습니다.</p>
      ) : (
        <div className="campaign-performance-table-wrap">
          <table className="campaign-performance-table">
            <thead>
              <tr>
                <th scope="col">구분</th>
                <th scope="col">대상</th>
                <th scope="col">대조군</th>
                <th scope="col">대상군 전환율</th>
                <th scope="col">유지율</th>
                <th scope="col">ROI</th>
              </tr>
            </thead>
            <tbody>
              {items.slice(0, 8).map((item) => (
                <tr key={item.key}>
                  <th scope="row">{item.label}</th>
                  <td>{formatNumber(item.target_count)}</td>
                  <td>{formatNumber(item.control_count)}</td>
                  <td>{formatPercent(item.treatment_conversion_rate)}</td>
                  <td>{formatPercent(item.retention_rate)}</td>
                  <td>{formatPercent(item.roi)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

type ComparisonTabKey = "by_campaign" | "by_segment" | "by_assignee";

const COMPARISON_TABS: Array<{ key: ComparisonTabKey; label: string }> = [
  { key: "by_campaign", label: "캠페인별 비교" },
  { key: "by_segment", label: "세그먼트별 비교" },
  { key: "by_assignee", label: "담당자별 비교" },
];

export function CampaignPerformancePanel({ campaignId, refreshKey }: CampaignPerformancePanelProps) {
  const [campaignPerformance, setCampaignPerformance] = useState<CampaignPerformance | null>(null);
  const [globalPerformance, setGlobalPerformance] = useState<CampaignPerformance | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [activeComparisonTab, setActiveComparisonTab] = useState<ComparisonTabKey>("by_campaign");

  useEffect(() => {
    let isActive = true;
    void Promise.all([
      getCampaignPerformance({ campaign_id: campaignId }),
      getCampaignPerformance(),
    ])
      .then(([campaignResult, globalResult]) => {
        if (!isActive) {
          return;
        }
        if (!isCampaignPerformance(campaignResult) || !isCampaignPerformance(globalResult)) {
          throw new Error("성과 응답 형식이 올바르지 않습니다.");
        }
        setCampaignPerformance(campaignResult);
        setGlobalPerformance(globalResult);
        setError("");
      })
      .catch((requestError: unknown) => {
        if (isActive) {
          setError(requestError instanceof Error ? requestError.message : "성과 데이터를 불러오지 못했습니다.");
        }
      })
      .finally(() => {
        if (isActive) {
          setIsLoading(false);
        }
      });
    return () => {
      isActive = false;
    };
  }, [campaignId, refreshKey]);

  if (isLoading) {
    return <section className="campaign-performance-panel"><p className="campaign-empty-copy">성과 데이터를 집계하는 중입니다.</p></section>;
  }
  if (error !== "" || campaignPerformance === null || globalPerformance === null) {
    return <section className="campaign-performance-panel"><p className="campaign-management-error" role="alert">{error || "성과 데이터를 표시할 수 없습니다."}</p></section>;
  }

  const metrics = campaignPerformance.summary;
  return (
    <section className="campaign-performance-panel" aria-label="캠페인 성과 대시보드">
      <div className="campaign-panel-heading">
        <div>
          <p className="card-kicker">PERFORMANCE MEASUREMENT</p>
          <h3>캠페인 성과 대시보드</h3>
        </div>
        <span className="campaign-performance-experiment">
          대상군 {formatNumber(metrics.treatment_count)} · 대조군 {formatNumber(metrics.control_count)}
        </span>
      </div>
      <div className="campaign-performance-cards">
        <PerformanceCard label="대상군 전환율" value={formatPercent(metrics.treatment_conversion_rate)} detail={`${formatNumber(metrics.converted_count)}명 전체 전환`} />
        <PerformanceCard label="유지율" value={formatPercent(metrics.retention_rate)} detail={`${formatNumber(metrics.retention_observed_count)} / ${formatNumber(metrics.retention_eligible_count)}명 관측`} />
        <PerformanceCard label="증분 전환효과" value={formatPercent(metrics.incremental_conversion_effect)} detail={`${metrics.incremental_conversions.toFixed(1)}명 추정`} />
        <PerformanceCard label="증분 ROI" value={formatPercent(metrics.roi)} detail={`${formatCurrency(metrics.incremental_revenue)} 귀속 매출`} />
      </div>
      <div className="campaign-performance-substats">
        <span>접촉률 <strong>{formatPercent(metrics.contact_rate)}</strong></span>
        <span>총 비용 <strong>{formatCurrency(metrics.total_cost)}</strong></span>
        <span>관측 매출 <strong>{formatCurrency(metrics.observed_revenue)}</strong></span>
        <span>유지 관측률 <strong>{formatPercent(metrics.retention_observation_rate)}</strong></span>
        <span>대상군 전환율 <strong>{formatPercent(metrics.treatment_conversion_rate)}</strong></span>
        <span>대조군 전환율 <strong>{formatPercent(metrics.control_conversion_rate)}</strong></span>
        <span>증분 유지효과 <strong>{formatPercent(metrics.incremental_retention_effect)}</strong></span>
      </div>
      <div className="campaign-performance-comparisons">
        <div className="campaign-performance-tabs" role="tablist" aria-label="비교 기준 선택">
          {COMPARISON_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={activeComparisonTab === tab.key}
              className={`campaign-performance-tab${activeComparisonTab === tab.key ? " is-active" : ""}`}
              onClick={() => setActiveComparisonTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <BreakdownTable
          title={COMPARISON_TABS.find((tab) => tab.key === activeComparisonTab)?.label ?? ""}
          items={globalPerformance[activeComparisonTab]}
        />
      </div>
    </section>
  );
}
