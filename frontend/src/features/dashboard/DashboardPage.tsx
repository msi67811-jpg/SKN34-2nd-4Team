import { useEffect, useState } from "react";

import type { AuthUser } from "../../api/auth";
import {
  createCampaignTarget,
  getCampaignPerformance,
  listCampaignTargets,
  updateCampaignTarget,
  type CampaignStatus,
  type CampaignTarget,
  type CampaignPerformance,
} from "../../api/campaigns";
import {
  getCustomerInsight,
  getCustomerInsightHistory,
  listCustomerInsights,
  type CustomerInsight,
  type CustomerInsightDetail,
  type CustomerInsightHistory,
  type CustomerInsightList,
  type InsightQuery,
} from "../../api/insights";
import { getLatestBatch, type LatestBatch } from "../../api/modelRuns";
import { EdaChartsSection } from "./EdaCharts";

const PAGE_SIZE = 8;

const riskLabels: Record<CustomerInsight["risk_level"], string> = {
  low: "낮음",
  medium: "주의",
  high: "높음",
};

const riskColors: Record<CustomerInsight["risk_level"], string> = {
  low: "#50b88b",
  medium: "#e8a34f",
  high: "#e66c78",
};

const campaignStatusLabels: Record<CampaignStatus, string> = {
  pending: "대기",
  assigned: "담당 배정",
  contacted: "접촉 완료",
  completed: "처리 완료",
  cancelled: "취소",
};

const campaignStatusTransitions: Record<CampaignStatus, CampaignStatus[]> = {
  pending: ["pending", "assigned", "cancelled"],
  assigned: ["assigned", "contacted", "cancelled"],
  contacted: ["contacted", "completed", "cancelled"],
  completed: ["completed"],
  cancelled: ["cancelled"],
};

type DashboardTab = "insights" | "eda";

type RiskFilter = "" | NonNullable<InsightQuery["risk_level"]>;
type SortBy = NonNullable<InsightQuery["sort_by"]>;
type SortOrder = NonNullable<InsightQuery["sort_order"]>;

type DashboardPageProps = {
  user: AuthUser;
  showCampaignFeedback?: boolean;
};

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("ko-KR").format(value);
}

function formatDecimal(value: number): string {
  return new Intl.NumberFormat("ko-KR", {
    maximumFractionDigits: 1,
  }).format(value);
}

const USD_TO_KRW_RATE = 1400;

function formatWon(usdValue: number): string {
  return `${formatNumber(Math.round(usdValue * USD_TO_KRW_RATE))}원`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatSignedDecimal(value: number): string {
  return `${value > 0 ? "+" : ""}${formatDecimal(value)}`;
}

function getPageNumbers(currentPage: number, totalPages: number): (number | "ellipsis")[] {
  const windowSize = 5;
  if (totalPages <= windowSize) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  let start = Math.max(1, currentPage - 2);
  let end = start + windowSize - 1;
  if (end > totalPages) {
    end = totalPages;
    start = end - windowSize + 1;
  }

  const result: (number | "ellipsis")[] = [];
  if (start > 1) {
    result.push(1);
    if (start > 2) result.push("ellipsis");
  }
  for (let page = start; page <= end; page += 1) {
    result.push(page);
  }
  if (end < totalPages) {
    if (end < totalPages - 1) result.push("ellipsis");
    result.push(totalPages);
  }
  return result;
}

function escapeCsv(value: unknown): string {
  const normalized = String(value ?? "").replaceAll('"', '""');
  return `"${normalized}"`;
}

function getCustomerIdFilter(value: string): number | undefined {
  const normalized = value.trim();
  if (normalized === "") {
    return undefined;
  }

  const customerId = Number(normalized);
  return Number.isInteger(customerId) && customerId > 0
    ? customerId
    : undefined;
}

function getDominantCluster(clusterCounts: Record<string, number>): [string, number] | null {
  const [cluster] = Object.entries(clusterCounts).sort(
    ([, countA], [, countB]) => countB - countA,
  );
  return cluster ?? null;
}

function getClusterOptions(clusterCounts: Record<string, number>): [string, number][] {
  return Object.entries(clusterCounts).sort(([clusterA], [clusterB]) =>
    clusterA.localeCompare(clusterB, "ko"),
  );
}

function RiskBadge({ risk }: { risk: CustomerInsight["risk_level"] }) {
  return (
    <span className={`risk-badge risk-badge--${risk}`}>
      <span aria-hidden="true" />
      {riskLabels[risk]}
    </span>
  );
}

function SortableHeader({
  field,
  label,
  sortBy,
  sortOrder,
  onSort,
}: {
  field: SortBy;
  label: string;
  sortBy: SortBy;
  sortOrder: SortOrder;
  onSort: (field: SortBy) => void;
}) {
  const isActive = field === sortBy;
  return (
    <button
      type="button"
      className={isActive ? "insight-header-sort insight-header-sort--active" : "insight-header-sort"}
      onClick={() => onSort(field)}
      aria-label={`${label} 기준 정렬${isActive ? (sortOrder === "desc" ? " (내림차순)" : " (오름차순)") : ""}`}
    >
      {label}
      <span className="insight-header-sort__arrow" aria-hidden="true">
        {isActive ? (sortOrder === "desc" ? "▼" : "▲") : "⇅"}
      </span>
    </button>
  );
}

function DashboardSkeleton() {
  return (
    <section className="bento-grid" aria-label="고객 분석 대시보드 로딩 중">
      <div className="bento-card dashboard-skeleton dashboard-skeleton--hero" />
      <div className="bento-card dashboard-skeleton" />
      <div className="bento-card dashboard-skeleton" />
      <div className="bento-card dashboard-skeleton dashboard-skeleton--table" />
    </section>
  );
}

function RiskOverview({ data }: { data: CustomerInsightList }) {
  const riskEntries = (["high", "medium", "low"] as const).map((risk) => ({
    risk,
    count: data.stats.risk_counts[risk] ?? 0,
  }));
  const maxCount = Math.max(...riskEntries.map(({ count }) => count), 1);

  return (
    <article className="bento-card bento-card--risk">
      <div className="bento-card__heading">
        <div>
          <p className="card-kicker">RISK MONITOR</p>
          <h2>위험도 분포</h2>
        </div>
        <span className="card-icon card-icon--alert" aria-hidden="true">!</span>
      </div>
      <div className="risk-bars">
        {riskEntries.map(({ risk, count }) => (
          <div className="risk-bar" key={risk}>
            <div className="risk-bar__label">
              <span>
                <i
                  className="risk-dot"
                  style={{ backgroundColor: riskColors[risk] }}
                />
                {riskLabels[risk]}
              </span>
              <strong>
                {formatNumber(count)} · {formatPercent(data.stats.total > 0 ? count / data.stats.total : 0)}
              </strong>
            </div>
            <div className="risk-bar__track">
              <span
                style={{
                  width: `${Math.max((count / maxCount) * 100, count > 0 ? 5 : 0)}%`,
                  backgroundColor: riskColors[risk],
                }}
              />
            </div>
          </div>
        ))}
      </div>
    </article>
  );
}

function InsightRow({
  insight,
  onSelect,
}: {
  insight: CustomerInsight;
  onSelect: (customerId: number) => void;
}) {
  return (
    <button
      className="insight-row"
      type="button"
      onClick={() => onSelect(insight.customer_id)}
    >
      <span className="insight-row__customer">
        <strong>{insight.customer_id}</strong>
        <small>{formatDate(insight.scored_at)}</small>
      </span>
      <span>
        <RiskBadge risk={insight.risk_level} />
      </span>
      <span className="insight-row__metric">
        <strong>{formatPercent(insight.churn_probability)}</strong>
        <small>이탈 확률</small>
      </span>
      <span className="insight-row__actual">
        {formatDecimal(insight.expected_transaction_count + insight.activity_gap)}건
        <small>실제 거래</small>
      </span>
      <span className="insight-row__expected">
        {formatDecimal(insight.expected_transaction_count)}건
        <small>예상 거래</small>
      </span>
      <span className={`insight-row__gap ${insight.activity_gap < 0 ? "insight-row__gap--negative" : ""}`}>
        {formatSignedDecimal(insight.activity_gap)}
        <small>거래 차이</small>
      </span>
      <span className="insight-row__amount">
        {formatWon(insight.total_trans_amt)}
        <small>총 거래금액</small>
      </span>
      <span className="insight-row__card">
        {insight.card_category}
        <small>카드분류</small>
      </span>
      <span className="insight-row__contacts">
        {formatNumber(insight.contacts_count_12_mon)}회
        <small>문의횟수</small>
      </span>
      <span className="insight-row__cluster">
        {insight.cluster_name}
        <small>
          신뢰도 {insight.cluster_confidence == null ? "-" : formatPercent(insight.cluster_confidence)}
        </small>
      </span>
      <span className="insight-row__action">{insight.recommended_action}</span>
      <span className="insight-row__arrow" aria-hidden="true">↗</span>
    </button>
  );
}

function BatchOverview({ batch }: { batch: LatestBatch | null }) {
  return (
    <article className="bento-card bento-card--batch">
      <div className="bento-card__heading">
        <div>
          <p className="card-kicker">DATA FRESHNESS</p>
          <h2>최근 배치 상태</h2>
        </div>
        <span className="batch-status">{batch === null ? "확인 중" : "SYNCED"}</span>
      </div>
      {batch === null ? (
        <p className="empty-copy">배치 실행 정보를 불러오는 중입니다.</p>
      ) : (
        <>
          <strong className="batch-time">
            {formatDate(batch.completed_at ?? batch.started_at)}
          </strong>
          <p className="batch-caption">
            {batch.processed_rows === null
              ? "처리 행 수 미상"
              : `${formatNumber(batch.processed_rows)}명 분석 완료`}
          </p>
          <div className="batch-models">
            {batch.runs.map((run) => (
              <span key={run.id}>{run.task} · {run.model_version}</span>
            ))}
          </div>
        </>
      )}
    </article>
  );
}

function signedPercentagePoints(value: number | null): string {
  if (value === null) {
    return "-";
  }
  return `${value > 0 ? "+" : ""}${Math.round(value * 100)}%p`;
}

function CampaignFeedback({
  performance,
  isLoading,
  error,
}: {
  performance: CampaignPerformance | null;
  isLoading: boolean;
  error: string;
}) {
  const [showMetricHelp, setShowMetricHelp] = useState(false);
  const metrics = performance?.summary ?? null;
  const pendingCount = metrics === null
    ? 0
    : Math.max(metrics.target_count - metrics.contacted_count, 0);

  useEffect(() => {
    if (!showMetricHelp) {
      return;
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setShowMetricHelp(false);
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [showMetricHelp]);

  return (
    <article className="bento-card bento-card--campaign campaign-feedback">
      <div className="table-card__header">
        <div>
          <p className="card-kicker">CAMPAIGN FEEDBACK</p>
          <h2>캠페인 실행 피드백</h2>
        </div>
        <div className="campaign-feedback__header-actions">
          <span className="table-count">
            {metrics === null ? "전체 캠페인" : `${formatNumber(metrics.target_count)}명 대상`}
          </span>
          <button
            className="campaign-feedback-help"
            type="button"
            aria-expanded={showMetricHelp}
            aria-controls="campaign-feedback-metric-help"
            onClick={() => setShowMetricHelp((current) => !current)}
          >
            <span aria-hidden="true">?</span>
            지표 설명
          </button>
        </div>
      </div>
      <p className="campaign-feedback__intro">
        실행 결과를 바탕으로 타기팅 품질과 운영 병목을 확인합니다. 개별 고객 처리는 운영팀이 담당합니다.
      </p>
      {showMetricHelp && (
        <div className="campaign-feedback-help-backdrop">
          <section
            className="campaign-feedback-help-dialog"
            id="campaign-feedback-metric-help"
            role="dialog"
            aria-modal="true"
            aria-labelledby="campaign-feedback-metric-help-title"
          >
            <div className="campaign-feedback-help-dialog__header">
              <div>
                <p className="card-kicker">METRIC GUIDE</p>
                <h3 id="campaign-feedback-metric-help-title">캠페인 지표 설명</h3>
              </div>
              <button
                className="campaign-feedback-help-close"
                type="button"
                aria-label="지표 설명 닫기"
                onClick={() => setShowMetricHelp(false)}
              >
                ×
              </button>
            </div>
            <div className="campaign-feedback__help">
              <div>
                <strong>접촉률</strong>
                <span>전체 대상 중 실제로 전화·문자·이메일 등 연락이 완료된 고객의 비율입니다. 100명 중 70명에게 연락했다면 70%입니다.</span>
              </div>
              <div>
                <strong>미처리 대상</strong>
                <span>아직 연락이 완료되지 않은 고객 수입니다. 숫자가 많으면 분석보다 운영팀의 실행이 지연되고 있을 수 있습니다.</span>
              </div>
              <div>
                <strong>전환율</strong>
                <span>캠페인 대상 중 신청·재이용·추가 거래 등 캠페인이 목표로 한 행동을 한 고객의 비율입니다.</span>
              </div>
              <div>
                <strong>치료군 전환율</strong>
                <span>캠페인 메시지·혜택·상담을 실제로 받은 그룹의 전환율입니다. 이 수치만으로는 캠페인 효과를 확정하지 않고 대조군과 비교합니다.</span>
              </div>
              <div>
                <strong>대조군 전환율</strong>
                <span>비슷한 고객 중 일부러 캠페인을 받지 않도록 남겨둔 비교 그룹의 전환율입니다. 캠페인이 없어도 자연스럽게 전환하는 비율을 보여줍니다.</span>
              </div>
              <div>
                <strong>증분 전환 효과</strong>
                <span>치료군 전환율에서 대조군 전환율을 뺀 값입니다. 예를 들어 치료군이 30%, 대조군이 10%이면 캠페인의 추가 효과는 +20%p로 해석합니다.</span>
              </div>
              <div>
                <strong>ROI</strong>
                <span>캠페인에 쓴 비용 대비 추가로 얻은 매출입니다. 0%보다 크면 비용보다 많은 추가 매출을 만들었다는 뜻입니다.</span>
              </div>
              <div>
                <strong>유지율</strong>
                <span>전환 후 설정된 관측 기간(예: 30일)이 지나도 계속 활동하거나 거래한 고객의 비율입니다. 아직 기간이 지나지 않은 고객은 계산에서 제외합니다.</span>
              </div>
            </div>
            <p className="campaign-feedback__help-note">
              치료군과 대조군의 차이가 클수록 캠페인 자체의 효과가 높다고 볼 수 있지만, 충분한 대상 수와 관측 기간이 함께 확보되어야 신뢰할 수 있습니다.
            </p>
          </section>
        </div>
      )}
      {isLoading ? (
        <p className="queue-state">캠페인 성과를 집계하는 중입니다.</p>
      ) : error !== "" ? (
        <p className="campaign-feedback__error" role="alert">{error}</p>
      ) : metrics === null || metrics.target_count === 0 ? (
        <p className="queue-state">분석할 캠페인 실행 결과가 없습니다.</p>
      ) : (
        <>
          <div className="campaign-feedback__metrics">
            <div>
              <span>접촉률</span>
              <strong>{formatPercent(metrics.contact_rate)}</strong>
              <small>{formatNumber(metrics.contacted_count)} / {formatNumber(metrics.target_count)}명 접촉</small>
            </div>
            <div>
              <span>미처리 대상</span>
              <strong>{formatNumber(pendingCount)}명</strong>
              <small>실행 병목 확인 대상</small>
            </div>
            <div>
              <span>전환율</span>
              <strong>{formatPercent(metrics.conversion_rate)}</strong>
              <small>{formatNumber(metrics.converted_count)}명 전환</small>
            </div>
            <div>
              <span>증분 전환 효과</span>
              <strong>{signedPercentagePoints(metrics.incremental_conversion_effect)}</strong>
              <small>치료군 − 대조군</small>
            </div>
            <div>
              <span>ROI</span>
              <strong>{metrics.roi === null ? "-" : formatPercent(metrics.roi)}</strong>
              <small>증분 매출 기준</small>
            </div>
          </div>
          <div className="campaign-feedback__comparison">
            <div>
              <span>치료군 전환율</span>
              <strong>{formatPercent(metrics.treatment_conversion_rate ?? 0)}</strong>
            </div>
            <div>
              <span>대조군 전환율</span>
              <strong>{formatPercent(metrics.control_conversion_rate ?? 0)}</strong>
            </div>
            <div>
              <span>유지율</span>
              <strong>{metrics.retention_rate === null ? "관측 중" : formatPercent(metrics.retention_rate)}</strong>
            </div>
          </div>
          <p className="campaign-feedback__note">
            미처리 대상은 실행 병목, 치료군과 대조군의 차이는 타기팅의 실제 효과를 나타냅니다.
          </p>
        </>
      )}
    </article>
  );
}

type CampaignDraft = {
  status: CampaignStatus;
  result: string;
  result_notes: string;
  converted: boolean;
};

function CampaignQueue({
  targets,
  drafts,
  canManage,
  user,
  isLoading,
  onDraftChange,
  onSave,
}: {
  targets: CampaignTarget[];
  drafts: Record<number, CampaignDraft>;
  canManage: boolean;
  user: AuthUser;
  isLoading: boolean;
  onDraftChange: (targetId: number, draft: CampaignDraft) => void;
  onSave: (targetId: number) => void;
}) {
  return (
    <article className="bento-card bento-card--campaign">
      <div className="table-card__header">
        <div>
          <p className="card-kicker">WORK QUEUE</p>
          <h2>캠페인 처리 현황</h2>
        </div>
        <span className="table-count">{formatNumber(targets.length)}건</span>
      </div>
      {isLoading ? (
        <p className="queue-state">처리 현황을 불러오는 중입니다.</p>
      ) : targets.length === 0 ? (
        <p className="queue-state">등록된 캠페인 대상이 없습니다.</p>
      ) : (
        <div className="campaign-list">
          {targets.map((target) => {
            const draft = drafts[target.id] ?? {
              status: target.status,
              result: target.result ?? "",
              result_notes: target.result_notes ?? "",
              converted: target.converted,
            };
            const canEditTarget = canManage
              && target.experiment_group === "treatment"
              && target.campaign_status === "active"
              && (user.role === "admin" || target.assigned_to_user_id === user.id);
            const availableStatuses = campaignStatusTransitions[target.status].filter(
              (value) => value !== "assigned" || target.assigned_to_user_id !== null,
            );
            return (
              <div className="campaign-row" key={target.id}>
                <div className="campaign-row__customer">
                  <strong>{target.customer_id}</strong>
                  <span>{target.campaign_name}</span>
                </div>
                <span className={`campaign-status campaign-status--${target.status}`}>
                  {campaignStatusLabels[target.status]}
                </span>
                <span className="campaign-assignee">
                  {target.assigned_to_display_name ?? "미배정"}
                </span>
                {canEditTarget ? (
                  <div className="campaign-row__controls">
                    <select
                      value={draft.status}
                      aria-label={`${target.customer_id} 처리 상태`}
                      onChange={(event) =>
                        onDraftChange(target.id, {
                          ...draft,
                          status: event.target.value as CampaignStatus,
                        })
                      }
                    >
                      {availableStatuses.map((value) => (
                        <option value={value} key={value}>
                          {campaignStatusLabels[value]}
                        </option>
                      ))}
                    </select>
                    <input
                      value={draft.result}
                      placeholder="처리 결과"
                      aria-label={`${target.customer_id} 처리 결과`}
                      onChange={(event) =>
                        onDraftChange(target.id, { ...draft, result: event.target.value })
                      }
                    />
                    <label className="campaign-conversion">
                      <input
                        type="checkbox"
                        checked={draft.converted}
                        disabled={draft.status !== "completed"}
                        onChange={(event) =>
                          onDraftChange(target.id, {
                            ...draft,
                            converted: event.target.checked,
                          })
                        }
                      />
                      전환
                    </label>
                    <button type="button" onClick={() => onSave(target.id)}>저장</button>
                  </div>
                ) : (
                  <span className="campaign-result">{target.result ?? "-"}</span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </article>
  );
}

function CustomerDetailPanel({
  detail,
  history,
  historyLoading,
  campaigns,
  canManageCampaigns,
  campaignName,
  campaignSubmitting,
  campaignMessage,
  onCampaignNameChange,
  onCreateCampaign,
  isLoading,
  error,
  onClose,
}: {
  detail: CustomerInsightDetail | null;
  history: CustomerInsightHistory | null;
  historyLoading: boolean;
  campaigns: CampaignTarget[];
  canManageCampaigns: boolean;
  campaignName: string;
  campaignSubmitting: boolean;
  campaignMessage: string;
  onCampaignNameChange: (value: string) => void;
  onCreateCampaign: () => void;
  isLoading: boolean;
  error: string;
  onClose: () => void;
}) {
  return (
    <aside className="insight-drawer" role="dialog" aria-modal="true" aria-labelledby="detail-title">
      <div className="insight-drawer__header">
        <div>
          <p className="card-kicker">CUSTOMER PROFILE</p>
          <h2 id="detail-title">
            {detail === null ? "고객 상세 정보" : `고객 ${detail.customer_id}`}
          </h2>
        </div>
        <button className="drawer-close" type="button" aria-label="고객 상세 닫기" onClick={onClose}>
          ×
        </button>
      </div>

      {isLoading && <p className="drawer-state">상세 정보를 불러오는 중입니다.</p>}
      {!isLoading && error !== "" && <p className="drawer-state drawer-state--error">{error}</p>}
      {!isLoading && error === "" && detail !== null && (
        <div className="drawer-content">
          <div className="drawer-risk-summary">
            <RiskBadge risk={detail.risk_level} />
            <strong>{formatPercent(detail.churn_probability)}</strong>
            <span>예상 이탈 확률</span>
          </div>
          <p className="drawer-recommendation">{detail.recommended_action}</p>

          <dl className="customer-facts">
            <div><dt>고객 연령</dt><dd>{detail.customer.customer_age}세</dd></div>
            <div><dt>카드 등급</dt><dd>{detail.customer.card_category}</dd></div>
            <div><dt>소득 구간</dt><dd>{detail.customer.income_category}</dd></div>
            <div><dt>최근 거래 건수</dt><dd>{formatNumber(detail.customer.total_trans_ct)}건</dd></div>
            <div><dt>거래 금액</dt><dd>{formatWon(detail.customer.total_trans_amt)}</dd></div>
            <div><dt>비활성 개월</dt><dd>{detail.customer.months_inactive_12_mon}개월</dd></div>
            <div><dt>예상 거래 건수</dt><dd>{formatDecimal(detail.expected_transaction_count)}건</dd></div>
            <div><dt>활동성 갭</dt><dd>{formatDecimal(detail.activity_gap)}</dd></div>
            <div><dt>고객 군집</dt><dd>{detail.cluster_name}</dd></div>
            <div><dt>군집 신뢰도</dt><dd>{detail.cluster_confidence == null ? "-" : formatPercent(detail.cluster_confidence)}</dd></div>
          </dl>

          <div className="drawer-history">
            <div className="drawer-section-heading">
              <h3>분석 이력</h3>
              <span>{history?.items.length ?? 0}회</span>
            </div>
            {historyLoading ? (
              <p className="drawer-inline-state">분석 이력을 불러오는 중입니다.</p>
            ) : history === null ? (
              <p className="drawer-inline-state">분석 이력이 없습니다.</p>
            ) : (
              <div className="history-list">
                {history.items.slice(0, 6).map((item) => (
                  <div className="history-item" key={item.id}>
                    <span>{formatDate(item.scored_at)}</span>
                    <strong>{formatPercent(item.churn_probability)}</strong>
                    <em className={item.activity_gap < 0 ? "history-gap--negative" : ""}>
                      {formatSignedDecimal(item.activity_gap)}
                    </em>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="drawer-reasons">
            <h3>추천 근거</h3>
            <div className="reason-list">
              {detail.reason_codes !== null && typeof detail.reason_codes === "object"
                ? Object.entries(detail.reason_codes).map(([key, value]) => (
                    <span key={key}>{key}: {String(value)}</span>
                  ))
                : <span>분석 근거가 등록되지 않았습니다.</span>}
            </div>
          </div>

          <div className="drawer-campaigns">
            <div className="drawer-section-heading">
              <h3>캠페인 업무</h3>
              <span>{campaigns.length}건</span>
            </div>
            {campaigns.map((campaign) => (
              <div className="drawer-campaign-row" key={campaign.id}>
                <span>{campaign.campaign_name}</span>
                <strong>{campaignStatusLabels[campaign.status]}</strong>
              </div>
            ))}
            {canManageCampaigns && (
              <div className="campaign-create-form">
                <input
                  value={campaignName}
                  aria-label="캠페인 이름"
                  placeholder="캠페인 이름"
                  onChange={(event) => onCampaignNameChange(event.target.value)}
                />
                <button
                  type="button"
                  disabled={campaignSubmitting || campaignName.trim() === ""}
                  onClick={onCreateCampaign}
                >
                  {campaignSubmitting ? "등록 중..." : "대상 등록"}
                </button>
              </div>
            )}
            {campaignMessage !== "" && <p className="campaign-message" role="status">{campaignMessage}</p>}
          </div>
        </div>
      )}
    </aside>
  );
}

export function DashboardPage({ user, showCampaignFeedback = false }: DashboardPageProps) {
  const [data, setData] = useState<CustomerInsightList | null>(null);
  const [error, setError] = useState("");
  const [riskFilter, setRiskFilter] = useState<RiskFilter>("");
  const [clusterFilter, setClusterFilter] = useState("");
  const [customerIdFilter, setCustomerIdFilter] = useState("");
  const [sortBy, setSortBy] = useState<SortBy>("churn_probability");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");
  const [page, setPage] = useState(1);
  const [selectedCustomerId, setSelectedCustomerId] = useState<number | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<CustomerInsightDetail | null>(null);
  const [selectedHistory, setSelectedHistory] = useState<CustomerInsightHistory | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [batch, setBatch] = useState<LatestBatch | null>(null);
  const [campaignTargets, setCampaignTargets] = useState<CampaignTarget[]>([]);
  const [campaignDrafts, setCampaignDrafts] = useState<Record<number, CampaignDraft>>({});
  const [campaignLoading, setCampaignLoading] = useState(true);
  const [campaignError, setCampaignError] = useState("");
  const [campaignPerformance, setCampaignPerformance] = useState<CampaignPerformance | null>(null);
  const shouldShowCampaignFeedback = user.role === "analyst" || showCampaignFeedback;
  const [campaignPerformanceLoading, setCampaignPerformanceLoading] = useState(shouldShowCampaignFeedback);
  const [campaignPerformanceError, setCampaignPerformanceError] = useState("");
  const [campaignName, setCampaignName] = useState("이탈 위험 리텐션");
  const [campaignSubmitting, setCampaignSubmitting] = useState(false);
  const [campaignMessage, setCampaignMessage] = useState("");
  // 로그아웃 상태는 AppShell이 소유합니다(헤더가 그쪽으로 옮겨졌기 때문).
  const [activeTab, setActiveTab] = useState<DashboardTab>("insights");

  const canCreateCampaignTargets = user.role === "admin" || user.role === "marketing";
  const canProcessCampaignTargets = user.role === "admin" || user.role === "operations";

  useEffect(() => {
    let isActive = true;
    const query: InsightQuery = {
      risk_level: riskFilter || undefined,
      cluster_name: clusterFilter.trim() || undefined,
      customer_id: getCustomerIdFilter(customerIdFilter),
      sort_by: sortBy,
      sort_order: sortOrder,
      page,
      page_size: PAGE_SIZE,
    };

    const loadInsights = async () => {
      try {
        const response = await listCustomerInsights(query);
        if (isActive) {
          setData(response);
          setError("");
        }
      } catch (requestError) {
        if (isActive) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : "고객 분석 결과를 불러오지 못했습니다.",
          );
        }
      }
    };

    void loadInsights();
    return () => {
      isActive = false;
    };
  }, [clusterFilter, customerIdFilter, page, riskFilter, sortBy, sortOrder]);

  useEffect(() => {
    let isActive = true;
    const loadBatch = async () => {
      try {
        const response = await getLatestBatch();
        if (isActive) {
          setBatch(response);
        }
      } catch {
        if (isActive) {
          setBatch(null);
        }
      }
    };
    void loadBatch();
    return () => {
      isActive = false;
    };
  }, []);

  useEffect(() => {
    if (!shouldShowCampaignFeedback) {
      return;
    }

    let isActive = true;
    void getCampaignPerformance()
      .then((response) => {
        if (isActive) {
          setCampaignPerformance(response);
          setCampaignPerformanceError("");
        }
      })
      .catch((requestError) => {
        if (isActive) {
          setCampaignPerformanceError(
            requestError instanceof Error
              ? requestError.message
              : "캠페인 성과를 불러오지 못했습니다.",
          );
        }
      })
      .finally(() => {
        if (isActive) {
          setCampaignPerformanceLoading(false);
        }
      });

    return () => {
      isActive = false;
    };
  }, [shouldShowCampaignFeedback]);

  useEffect(() => {
    let isActive = true;
    const loadCampaigns = async () => {
      try {
        const response = await listCampaignTargets({ page: 1, page_size: 20 });
        if (isActive) {
          setCampaignTargets(response.items);
          setCampaignError("");
          setCampaignLoading(false);
        }
      } catch (requestError) {
        if (isActive) {
          setCampaignLoading(false);
          setCampaignError(
            requestError instanceof Error
              ? requestError.message
              : "캠페인 처리 현황을 불러오지 못했습니다.",
          );
        }
      }
    };
    void loadCampaigns();
    return () => {
      isActive = false;
    };
  }, []);

  const handleSelectCustomer = async (customerId: number) => {
    setSelectedCustomerId(customerId);
    setSelectedDetail(null);
    setSelectedHistory(null);
    setDetailError("");
    setDetailLoading(true);
    setHistoryLoading(true);
    setCampaignMessage("");
    setCampaignName("이탈 위험 리텐션");
    try {
      const [detail, history] = await Promise.all([
        getCustomerInsight(customerId),
        getCustomerInsightHistory(customerId),
      ]);
      setSelectedDetail(detail);
      setSelectedHistory(history);
    } catch (requestError) {
      setDetailError(
        requestError instanceof Error
          ? requestError.message
          : "고객 상세 정보를 불러오지 못했습니다.",
      );
    } finally {
      setDetailLoading(false);
      setHistoryLoading(false);
    }
  };

  const handleCreateCampaign = async () => {
    if (selectedDetail === null || campaignName.trim() === "") {
      return;
    }
    setCampaignSubmitting(true);
    setCampaignMessage("");
    try {
      const target = await createCampaignTarget({
        customer_insight_id: selectedDetail.id,
        campaign_name: campaignName.trim(),
      });
      setCampaignTargets((current) => [target, ...current]);
      setCampaignMessage("캠페인 대상에 등록했습니다.");
      setCampaignName("이탈 위험 리텐션");
    } catch (requestError) {
      setCampaignMessage(
        requestError instanceof Error ? requestError.message : "캠페인 등록에 실패했습니다.",
      );
    } finally {
      setCampaignSubmitting(false);
    }
  };

  const handleCampaignDraftChange = (targetId: number, draft: CampaignDraft) => {
    setCampaignDrafts((current) => ({ ...current, [targetId]: draft }));
  };

  const handleSaveCampaign = async (targetId: number) => {
    const target = campaignTargets.find((item) => item.id === targetId);
    if (target === undefined) {
      return;
    }
    const draft = campaignDrafts[targetId] ?? {
      status: target.status,
      result: target.result ?? "",
      result_notes: target.result_notes ?? "",
      converted: target.converted,
    };
    try {
      const updated = await updateCampaignTarget(targetId, {
        status: draft.status,
        result: draft.result || undefined,
        result_notes: draft.result_notes || undefined,
        result_code: draft.status === "completed"
          ? (draft.converted ? "converted" : "not_converted")
          : draft.status === "contacted" ? "contacted" : undefined,
        converted: draft.converted,
      });
      setCampaignTargets((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setCampaignMessage("캠페인 처리 결과를 저장했습니다.");
    } catch (requestError) {
      setCampaignError(
        requestError instanceof Error ? requestError.message : "캠페인 처리 결과 저장에 실패했습니다.",
      );
    }
  };

  const downloadCsv = () => {
    if (data === null) {
      return;
    }
    const header = ["customer_id", "risk_level", "churn_probability", "expected_transaction_count", "activity_gap", "cluster_name", "cluster_confidence", "recommended_action", "scored_at"];
    const rows = data.items.map((item) => [
      item.customer_id,
      item.risk_level,
      item.churn_probability,
      item.expected_transaction_count,
      item.activity_gap,
      item.cluster_name,
      item.cluster_confidence ?? "",
      item.recommended_action,
      item.scored_at,
    ]);
    const csv = [header, ...rows].map((row) => row.map(escapeCsv).join(",")).join("\n");
    const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "customer-insights.csv";
    link.click();
    URL.revokeObjectURL(url);
  };

  const focusHighRisk = () => {
    setRiskFilter("high");
    setPage(1);
  };

  const resetFilters = () => {
    setRiskFilter("");
    setClusterFilter("");
    setCustomerIdFilter("");
    setSortBy("churn_probability");
    setSortOrder("desc");
    setPage(1);
  };

  const handleHeaderSort = (field: SortBy) => {
    if (field === sortBy) {
      setSortOrder((current) => (current === "desc" ? "asc" : "desc"));
    } else {
      setSortBy(field);
      setSortOrder("desc");
    }
    setPage(1);
  };


  const isInitialLoading = data === null && error === "";
  const dominantCluster = data === null ? null : getDominantCluster(data.stats.cluster_counts);
  const clusterOptions = data === null ? [] : getClusterOptions(data.stats.cluster_options);
  const highRiskCount = data?.stats.risk_counts.high ?? 0;

  return (
    <>
      {/* 헤더·로그아웃·워크스페이스 전환은 AppShell이 담당합니다. */}
      <nav className="dashboard-tabs" aria-label="대시보드 탭">
        <button
          type="button"
          className={`dashboard-tab${activeTab === "insights" ? " dashboard-tab--active" : ""}`}
          onClick={() => setActiveTab("insights")}
        >
          고객 인사이트
        </button>
        <button
          type="button"
          className={`dashboard-tab${activeTab === "eda" ? " dashboard-tab--active" : ""}`}
          onClick={() => setActiveTab("eda")}
        >
          EDA 차트
        </button>
      </nav>

      {isInitialLoading && <DashboardSkeleton />}

      {error !== "" && (
        <section className="dashboard-error" role="alert">
          <strong>분석 결과를 불러오지 못했습니다.</strong>
          <span>{error}</span>
          <button type="button" onClick={() => window.location.reload()}>새로고침</button>
        </section>
      )}

      {data !== null && error === "" && activeTab === "eda" && (
        <section className="bento-grid" aria-label="EDA 차트">
          <EdaChartsSection stats={data.stats} />
        </section>
      )}

      {data !== null && error === "" && activeTab === "insights" && (
        <section className="bento-grid" aria-label="고객 분석 대시보드">
          <article className="bento-card bento-card--hero">
            <div className="bento-card__heading">
              <div>
                <p className="card-kicker">CUSTOMER HEALTH</p>
                <h2>분석 대상 고객</h2>
              </div>
              <span className="card-icon card-icon--spark" aria-hidden="true">✦</span>
            </div>
            <strong className="hero-number">{formatNumber(data.stats.total)}</strong>
            <p className="hero-caption">현재 필터 조건에 해당하는 최신 분석 스냅샷</p>
            <div className="hero-meta">
              <span><i className="status-pulse" /> 데이터 연결됨</span>
            </div>
          </article>

          <article className="bento-card bento-card--average">
            <p className="card-kicker">CHURN PROBABILITY</p>
            <span className="metric-label">평균 이탈 확률</span>
            <strong className="metric-number">{formatPercent(data.stats.average_churn_probability)}</strong>
            <div className="metric-footnote"><span className="metric-trend">●</span> 최신 분석 기준</div>
          </article>

          <button
            className="bento-card bento-card--priority"
            type="button"
            onClick={focusHighRisk}
            aria-label="높은 이탈 위험 고객만 보기"
          >
            <div className="priority-visual"><span>{formatNumber(highRiskCount)}</span><small>HIGH</small></div>
            <div>
              <p className="card-kicker">ACTION PRIORITY</p>
              <h2>우선 관리 고객</h2>
              <p>높은 이탈 위험으로 분류된 고객입니다. 클릭해서 바로 확인하세요.</p>
            </div>
          </button>

          <RiskOverview data={data} />

          <article className="bento-card bento-card--cluster">
            <div className="bento-card__heading">
              <div>
                <p className="card-kicker">SEGMENTATION</p>
                <h2>주요 고객 군집</h2>
              </div>
              <span className="card-icon card-icon--cluster" aria-hidden="true">◌</span>
            </div>
            {dominantCluster === null ? (
              <p className="empty-copy">군집 데이터가 없습니다.</p>
            ) : (
              <>
                <strong className="cluster-name">{dominantCluster[0]}</strong>
                <p className="cluster-count">{formatNumber(dominantCluster[1])}명 · 전체 군집 중 최다</p>
              </>
            )}
          </article>

          <BatchOverview batch={batch} />

          <article className="bento-card bento-card--table">
            <div className="table-card__header">
              <div>
                <p className="card-kicker">CUSTOMER INSIGHTS</p>
                <h2>고객별 분석 결과</h2>
              </div>
              <div className="table-card__actions">
                <button className="export-button" type="button" onClick={downloadCsv}>
                  CSV 다운로드
                </button>
                <span className="table-count">총 {formatNumber(data.total)}명</span>
              </div>
            </div>

            <div className="insight-filters" aria-label="고객 분석 결과 필터">
              <label className="filter-field filter-field--search">
                <span>고객 ID</span>
                <input
                  type="search"
                  inputMode="numeric"
                  value={customerIdFilter}
                  placeholder="ID 검색"
                  onChange={(event) => {
                    setCustomerIdFilter(event.target.value);
                    setPage(1);
                  }}
                />
              </label>
              <label className="filter-field">
                <span>위험도</span>
                <select
                  value={riskFilter}
                  onChange={(event) => {
                    setRiskFilter(event.target.value as RiskFilter);
                    setPage(1);
                  }}
                >
                  <option value="">전체</option>
                  <option value="high">높음</option>
                  <option value="medium">주의</option>
                  <option value="low">낮음</option>
                </select>
              </label>
              <label className="filter-field filter-field--cluster">
                <span>군집</span>
                <select
                  aria-label="군집"
                  value={clusterFilter}
                  onChange={(event) => {
                    setClusterFilter(event.target.value);
                    setPage(1);
                  }}
                >
                  <option value="">전체 군집</option>
                  {clusterOptions.map(([cluster, count]) => (
                    <option key={cluster} value={cluster}>
                      {cluster} ({formatNumber(count)}명)
                    </option>
                  ))}
                </select>
              </label>
              <button className="reset-filter-button" type="button" onClick={resetFilters}>초기화</button>
            </div>

            {data.items.length === 0 ? (
              <div className="empty-state">
                <span aria-hidden="true">⌁</span>
                <strong>조건에 맞는 고객이 없습니다.</strong>
                <p>필터를 초기화하거나 다른 조건으로 검색해 보세요.</p>
              </div>
            ) : (
              <div className="insight-list" role="list" aria-label="고객 분석 결과 목록">
                <div className="insight-list__header">
                  <span>고객</span><span>위험도</span>
                  <SortableHeader field="churn_probability" label="이탈 확률" sortBy={sortBy} sortOrder={sortOrder} onSort={handleHeaderSort} />
                  <SortableHeader field="actual_transaction_count" label="실제 거래" sortBy={sortBy} sortOrder={sortOrder} onSort={handleHeaderSort} />
                  <SortableHeader field="expected_transaction_count" label="예상 거래" sortBy={sortBy} sortOrder={sortOrder} onSort={handleHeaderSort} />
                  <SortableHeader field="activity_gap" label="거래 차이" sortBy={sortBy} sortOrder={sortOrder} onSort={handleHeaderSort} />
                  <SortableHeader field="total_trans_amt" label="총 거래금액" sortBy={sortBy} sortOrder={sortOrder} onSort={handleHeaderSort} />
                  <SortableHeader field="card_category" label="카드분류" sortBy={sortBy} sortOrder={sortOrder} onSort={handleHeaderSort} />
                  <SortableHeader field="contacts_count_12_mon" label="문의횟수" sortBy={sortBy} sortOrder={sortOrder} onSort={handleHeaderSort} />
                  <span>군집</span><span>추천 액션</span><span />
                </div>
                {data.items.map((insight) => (
                  <InsightRow key={insight.id} insight={insight} onSelect={(id) => void handleSelectCustomer(id)} />
                ))}
              </div>
            )}

            <div className="pagination">
              <span className="pagination__summary">{data.total === 0 ? "0" : `${(data.page - 1) * data.page_size + 1}–${Math.min(data.page * data.page_size, data.total)}`} / {formatNumber(data.total)}명</span>
              <div className="pagination__pages">
                <button
                  type="button"
                  className="pagination__arrow"
                  disabled={data.page <= 1}
                  onClick={() => setPage((current) => current - 1)}
                  aria-label="이전 페이지"
                >
                  ←
                </button>
                {getPageNumbers(data.page, data.total_pages).map((pageNumber, index) =>
                  pageNumber === "ellipsis" ? (
                    <span key={`ellipsis-${index}`} className="pagination__ellipsis">…</span>
                  ) : (
                    <button
                      key={pageNumber}
                      type="button"
                      className={pageNumber === data.page ? "pagination__page pagination__page--active" : "pagination__page"}
                      aria-current={pageNumber === data.page ? "page" : undefined}
                      aria-label={`${pageNumber} 페이지`}
                      onClick={() => setPage(pageNumber)}
                    >
                      {pageNumber}
                    </button>
                  ),
                )}
                <button
                  type="button"
                  className="pagination__arrow"
                  disabled={data.page >= data.total_pages}
                  onClick={() => setPage((current) => current + 1)}
                  aria-label="다음 페이지"
                >
                  →
                </button>
              </div>
              <span className="pagination__spacer" aria-hidden="true" />
            </div>
          </article>

          {campaignError !== "" && <p className="campaign-error" role="alert">{campaignError}</p>}
          {shouldShowCampaignFeedback ? (
            <CampaignFeedback
              performance={campaignPerformance}
              isLoading={campaignPerformanceLoading}
              error={campaignPerformanceError}
            />
          ) : (
            <CampaignQueue
              targets={campaignTargets}
              drafts={campaignDrafts}
              canManage={canProcessCampaignTargets}
              user={user}
              isLoading={campaignLoading}
              onDraftChange={handleCampaignDraftChange}
              onSave={(targetId) => void handleSaveCampaign(targetId)}
            />
          )}
        </section>
      )}

      {selectedCustomerId !== null && (
        <>
          <button className="drawer-backdrop" type="button" aria-label="상세 패널 닫기" onClick={() => setSelectedCustomerId(null)} />
          <CustomerDetailPanel
            detail={selectedDetail}
            history={selectedHistory}
            historyLoading={historyLoading}
            campaigns={campaignTargets.filter((target) => target.customer_id === selectedCustomerId)}
            canManageCampaigns={canCreateCampaignTargets}
            campaignName={campaignName}
            campaignSubmitting={campaignSubmitting}
            campaignMessage={campaignMessage}
            onCampaignNameChange={setCampaignName}
            onCreateCampaign={() => void handleCreateCampaign()}
            isLoading={detailLoading}
            error={detailError}
            onClose={() => setSelectedCustomerId(null)}
          />
        </>
      )}
    </>
  );
}
