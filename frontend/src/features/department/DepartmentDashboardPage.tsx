import { useEffect, useMemo, useState, type ReactNode } from "react";

import { listTeamMembers, updateTeamMember, type TeamMember } from "../../api/team";
import type { ApiError } from "../../api/client";
import type { AuthUser } from "../../api/auth";
import {
  createCampaignTarget,
  listCampaignTargets,
  updateCampaignTarget,
  type CampaignStatus,
  type CampaignTarget,
  type CampaignTargetList,
  type CampaignResultCode,
} from "../../api/campaigns";
import {
  listCustomerInsights,
  type CustomerInsight,
  type CustomerInsightList,
  type InsightQuery,
} from "../../api/insights";
import { getLatestBatch, type LatestBatch } from "../../api/modelRuns";

const roleLabels: Record<AuthUser["role"], string> = {
  admin: "관리자",
  analyst: "분석 담당자",
  operations: "운영 담당자",
  marketing: "마케팅 담당자",
};

const riskLabels: Record<CustomerInsight["risk_level"], string> = {
  low: "낮음",
  medium: "주의",
  high: "높음",
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
  assigned: ["pending", "assigned", "contacted", "cancelled"],
  contacted: ["contacted", "completed", "cancelled"],
  completed: ["completed"],
  cancelled: ["cancelled"],
};

const campaignResultLabels: Record<CampaignResultCode, string> = {
  contacted: "접촉",
  converted: "전환",
  not_converted: "미전환",
  no_response: "응답 없음",
  declined: "거절",
  opted_out: "수신 거부",
  invalid_contact: "연락처 오류",
};

const finalCampaignResultCodes: CampaignResultCode[] = [
  "converted",
  "not_converted",
  "no_response",
  "declined",
  "opted_out",
  "invalid_contact",
];

const roleDescriptions: Record<AuthUser["role"], string> = {
  admin: "팀 계정과 업무 권한을 관리합니다.",
  analyst: "고객 분석 결과와 모델 배치 상태를 확인합니다.",
  operations: "고위험 고객의 상담과 후속 처리를 관리합니다.",
  marketing: "고객 세그먼트와 캠페인 실행 결과를 관리합니다.",
};

const MARKETING_INSIGHT_PAGE_SIZE = 8;
const OPERATIONS_INSIGHT_PAGE_SIZE = 8;
const CAMPAIGN_QUEUE_PAGE_SIZE = 8;
const PAGE_GROUP_SIZE = 10;

type DepartmentDashboardPageProps = {
  user: AuthUser;
};

type CampaignDraft = {
  status: CampaignStatus;
  result: string;
  result_code: CampaignResultCode | "";
  assigned_to_user_id: number | null;
  converted: boolean;
  retained: boolean | null;
  retainedDirty: boolean;
  outcome_revenue: string;
};

type MarketingRiskFilter = "" | NonNullable<InsightQuery["risk_level"]>;
type MarketingSortBy = NonNullable<InsightQuery["sort_by"]>;
type MarketingSortOrder = NonNullable<InsightQuery["sort_order"]>;
type CampaignTargetStats = NonNullable<CampaignTargetList["stats"]>;

function formatNumber(value: number): string {
  return new Intl.NumberFormat("ko-KR").format(value);
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatDecimal(value: number): string {
  return new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 }).format(value);
}

function formatDate(value: string | null): string {
  if (value === null) {
    return "-";
  }
  return new Intl.DateTimeFormat("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function campaignQueueErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  const translations: Record<string, string> = {
    "Retention cannot be recorded before retention_window_days has elapsed.": "처리 완료와 전환은 저장할 수 있지만, 유지 결과는 유지 관측 기간(기본 30일)이 지난 후에 입력할 수 있습니다. 유지 여부를 '유지 미관측'으로 두고 먼저 저장한 뒤, 30일이 지나면 유지 또는 미유지를 입력해 주세요.",
    "A treatment target must be completed before retention is recorded.": "유지 결과를 입력하려면 먼저 대상을 처리 완료 상태로 저장해야 합니다.",
    "Retention cannot be recorded before the observation period starts.": "유지 관측 시작일이 아직 도래하지 않아 유지 결과를 입력할 수 없습니다.",
    "A treatment result code requires a completed target.": "전환·미전환과 같은 최종 결과 코드는 대상을 처리 완료 상태로 저장한 후 입력할 수 있습니다.",
    "A completed treatment target requires a final structured result code.": "처리 완료로 저장하려면 전환, 미전환, 응답 없음, 거절, 수신 거부 또는 연락처 오류 중 하나를 선택해야 합니다.",
    "Outcome revenue can only be recorded for a converted target.": "성과 매출은 전환으로 처리된 고객에게만 입력할 수 있습니다.",
  };
  return translations[message] ?? (message || "캠페인 처리 결과를 저장하지 못했습니다. 입력값을 확인한 후 다시 시도해 주세요.");
}

function DepartmentRiskBadge({ risk }: { risk: CustomerInsight["risk_level"] }) {
  return (
    <span className={`risk-badge risk-badge--${risk}`}>
      <span aria-hidden="true" />
      {riskLabels[risk]}
    </span>
  );
}

function StatCard({ label, value, caption, tone = "purple" }: {
  label: string;
  value: string;
  caption: string;
  tone?: "purple" | "orange" | "green" | "pink";
}) {
  return (
    <article className={`department-stat department-stat--${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{caption}</small>
    </article>
  );
}

function BatchCard({ batch }: { batch: LatestBatch | null }) {
  return (
    <article className="department-panel department-panel--batch">
      <div className="department-panel__heading">
        <div>
          <p className="card-kicker">DATA FRESHNESS</p>
          <h2>최근 분석 배치</h2>
        </div>
        <span className="batch-status">{batch === null ? "확인 중" : "SYNCED"}</span>
      </div>
      <strong className="department-batch-time">
        {formatDate(batch?.completed_at ?? batch?.started_at ?? null)}
      </strong>
      <p className="department-panel__caption">
        {batch?.processed_rows === null || batch === null
          ? "배치 실행 정보를 불러오는 중입니다."
          : `${formatNumber(batch.processed_rows)}명 분석 완료`}
      </p>
      <div className="department-batch-models">
        {batch?.runs.map((run) => (
          <span key={run.id}>{run.task} · {run.model_version}</span>
        ))}
      </div>
    </article>
  );
}

function MarketingCandidateFilters({
  riskLevel,
  clusterName,
  sortBy,
  sortOrder,
  clusterOptions,
  onRiskLevelChange,
  onClusterNameChange,
  onSortByChange,
  onSortOrderChange,
  onReset,
}: {
  riskLevel: MarketingRiskFilter;
  clusterName: string;
  sortBy: MarketingSortBy;
  sortOrder: MarketingSortOrder;
  clusterOptions: Record<string, number>;
  onRiskLevelChange: (value: MarketingRiskFilter) => void;
  onClusterNameChange: (value: string) => void;
  onSortByChange: (value: MarketingSortBy) => void;
  onSortOrderChange: (value: MarketingSortOrder) => void;
  onReset: () => void;
}) {
  const sortedClusterOptions = Object.entries(clusterOptions).sort(
    ([firstName, firstCount], [secondName, secondCount]) => secondCount - firstCount
      || firstName.localeCompare(secondName),
  );

  return (
    <>
      <div className="insight-filters department-insight-filters">
        <label className="filter-field">
          <span>위험도</span>
          <select
            aria-label="캠페인 후보 위험도"
            value={riskLevel}
            onChange={(event) => onRiskLevelChange(event.target.value as MarketingRiskFilter)}
          >
            <option value="">전체 위험도</option>
            <option value="high">높음</option>
            <option value="medium">주의</option>
            <option value="low">낮음</option>
          </select>
        </label>
        <label className="filter-field filter-field--cluster">
          <span>군집</span>
          <select
            aria-label="캠페인 후보 군집"
            value={clusterName}
            onChange={(event) => onClusterNameChange(event.target.value)}
          >
            <option value="">전체 군집</option>
            {sortedClusterOptions.map(([name, count]) => (
              <option key={name} value={name}>
                {name} ({formatNumber(count)}명)
              </option>
            ))}
          </select>
        </label>
        <label className="filter-field">
          <span>정렬 기준</span>
          <select
            aria-label="캠페인 후보 정렬 기준"
            value={sortBy}
            onChange={(event) => onSortByChange(event.target.value as MarketingSortBy)}
          >
            <option value="churn_probability">이탈 확률</option>
            <option value="activity_gap">활동성 갭</option>
            <option value="expected_transaction_count">예상 거래 건수</option>
            <option value="scored_at">최근 분석 시각</option>
          </select>
        </label>
        <label className="filter-field">
          <span>정렬 순서</span>
          <select
            aria-label="캠페인 후보 정렬 순서"
            value={sortOrder}
            onChange={(event) => onSortOrderChange(event.target.value as MarketingSortOrder)}
          >
            <option value="desc">높은 값부터</option>
            <option value="asc">낮은 값부터</option>
          </select>
        </label>
        <button className="reset-filter-button" type="button" onClick={onReset}>초기화</button>
      </div>
      <p className="department-panel__caption">
        분석 결과를 기준으로 후보를 좁힌 뒤, 정렬된 고객부터 캠페인에 등록할 수 있습니다.
      </p>
    </>
  );
}

function CampaignConflictDialog({
  message,
  onClose,
}: {
  message: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <div className="campaign-feedback-help-backdrop">
      <section
        className="campaign-feedback-help-dialog campaign-conflict-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="campaign-conflict-title"
      >
        <div className="campaign-feedback-help-dialog__header">
          <div>
            <p className="card-kicker">CAMPAIGN GUARD</p>
            <h3 id="campaign-conflict-title">캠페인 등록 불가</h3>
          </div>
          <button
            className="campaign-feedback-help-close"
            type="button"
            aria-label="캠페인 등록 안내 닫기"
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <p className="campaign-conflict-dialog__message">{message}</p>
        <p className="campaign-conflict-dialog__message">
          중복 접촉을 막기 위해 이미 처리 중인 캠페인, 최근 접촉 고객, 수신 거부 고객은 후보 목록에서 자동으로 제외됩니다.
        </p>
        <button
          className="department-action-button campaign-conflict-dialog__confirm"
          type="button"
          onClick={onClose}
        >
          확인
        </button>
      </section>
    </div>
  );
}

function CampaignQueueFeedbackDialog({
  message,
  onClose,
  variant,
}: {
  message: string;
  onClose: () => void;
  variant: "error" | "success";
}) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  const title = variant === "success" ? "저장 완료" : "캠페인 처리 저장 안내";

  return (
    <div className="campaign-feedback-help-backdrop">
      <section
        className={`campaign-feedback-help-dialog campaign-conflict-dialog campaign-action-dialog campaign-action-dialog--${variant}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="campaign-queue-feedback-title"
      >
        <div className="campaign-feedback-help-dialog__header">
          <div>
            <p className="card-kicker">{variant === "success" ? "CAMPAIGN SAVED" : "CAMPAIGN UPDATE"}</p>
            <h3 id="campaign-queue-feedback-title">{title}</h3>
          </div>
          <button
            className="campaign-feedback-help-close"
            type="button"
            aria-label={`${title} 닫기`}
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <p className="campaign-conflict-dialog__message">{message}</p>
        <button className="department-action-button campaign-conflict-dialog__confirm" type="button" onClick={onClose}>
          확인
        </button>
      </section>
    </div>
  );
}

function DepartmentPagination({
  label,
  currentStart,
  currentEnd,
  total,
  page,
  totalPages,
  onPageChange,
}: {
  label: string;
  currentStart: number;
  currentEnd: number;
  total: number;
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
}) {
  if (totalPages <= 1) {
    return null;
  }
  const groupStart = Math.floor((page - 1) / PAGE_GROUP_SIZE) * PAGE_GROUP_SIZE + 1;
  const groupEnd = Math.min(groupStart + PAGE_GROUP_SIZE - 1, totalPages);
  const pages = Array.from({ length: groupEnd - groupStart + 1 }, (_, index) => groupStart + index);
  return (
    <div className="department-insight-pagination">
      <span>{formatNumber(currentStart)}–{formatNumber(currentEnd)} / {formatNumber(total)}명</span>
      <div className="department-insight-pagination__pages">
        <button
          type="button"
          aria-label={`이전 ${label} 페이지 묶음`}
          disabled={groupStart === 1}
          onClick={() => onPageChange(groupStart - 1)}
        >
          ‹
        </button>
        {pages.map((pageNumber) => (
          <button
            type="button"
            key={pageNumber}
            className={pageNumber === page ? "department-insight-pagination__page department-insight-pagination__page--active" : "department-insight-pagination__page"}
            aria-label={`${label} ${pageNumber}페이지`}
            aria-current={pageNumber === page ? "page" : undefined}
            onClick={() => onPageChange(pageNumber)}
          >
            {pageNumber}
          </button>
        ))}
        <button
          type="button"
          aria-label={`다음 ${label} 페이지 묶음`}
          disabled={groupEnd === totalPages}
          onClick={() => onPageChange(groupEnd + 1)}
        >
          ›
        </button>
      </div>
    </div>
  );
}

function InsightPriorityTable({
  kicker,
  heading,
  toolbar,
  insights,
  targets,
  campaignName,
  onCreate,
  isCreating,
  total,
  page,
  pageSize,
  totalPages,
  onPageChange,
}: {
  kicker: string;
  heading: string;
  toolbar?: ReactNode;
  insights: CustomerInsight[];
  targets: CampaignTarget[];
  campaignName: string;
  onCreate?: (insight: CustomerInsight) => void;
  isCreating: number | null;
  total: number;
  page?: number;
  pageSize?: number;
  totalPages?: number;
  onPageChange?: (page: number) => void;
}) {
  const targetInsightIds = new Set(targets.map((target) => target.customer_insight_id));
  const currentPage = page ?? 1;
  const currentPageSize = pageSize ?? insights.length;
  const currentStart = total === 0 ? 0 : (currentPage - 1) * currentPageSize + 1;
  const currentEnd = total === 0 ? 0 : Math.min(currentPage * currentPageSize, total);
  const hasPagination = onPageChange !== undefined && totalPages !== undefined && totalPages > 1;
  return (
    <section className="department-panel department-panel--wide">
      <div className="department-panel__heading">
        <div>
          <p className="card-kicker">{kicker}</p>
          <h2>{heading}</h2>
        </div>
        <span className="table-count">{formatNumber(total)}명</span>
      </div>
      {toolbar}
      {insights.length === 0 ? (
        <p className="department-empty">현재 조건에 맞는 고객이 없습니다.</p>
      ) : (
        <div className="department-insight-list" role="list" aria-label={`${heading} 목록`}>
          <div className="department-insight-list__header" aria-hidden="true">
            <span>고객</span>
            <span>위험도</span>
            <span>이탈 확률</span>
            <span>활동성 갭</span>
            <span>예상 거래</span>
            <span>군집</span>
            <span>추천 액션</span>
            <span>캠페인</span>
          </div>
          {insights.map((insight) => {
            const isRegistered = targetInsightIds.has(insight.id);
            return (
              <div className="department-insight-row" key={insight.id} role="listitem">
                <span className="department-insight-row__customer">
                  <strong>{insight.customer_id}</strong>
                  <small>{formatDate(insight.scored_at)}</small>
                </span>
                <span className="department-insight-row__risk">
                  <DepartmentRiskBadge risk={insight.risk_level} />
                </span>
                <span className="department-insight-row__metric">
                  <strong>{formatPercent(insight.churn_probability)}</strong>
                  <small>이탈 확률</small>
                </span>
                <span className={`department-insight-row__gap ${insight.activity_gap < 0 ? "department-gap--negative" : ""}`}>
                  {insight.activity_gap > 0 ? "+" : ""}{formatDecimal(insight.activity_gap)}
                  <small>활동성 갭</small>
                </span>
                <span className="department-insight-row__expected">
                  {formatDecimal(insight.expected_transaction_count)}건
                  <small>예상 거래</small>
                </span>
                <span className="department-insight-row__cluster">
                  {insight.cluster_name}
                  <small>
                    신뢰도 {insight.cluster_confidence == null ? "-" : formatPercent(insight.cluster_confidence)}
                  </small>
                </span>
                <span className="department-insight-row__action">{insight.recommended_action}</span>
                {onCreate ? (
                  <button
                    type="button"
                    className="department-action-button"
                    disabled={isRegistered || isCreating === insight.id}
                    onClick={() => onCreate(insight)}
                  >
                    {isRegistered ? "등록됨" : isCreating === insight.id ? "등록 중..." : campaignName}
                  </button>
                ) : (
                  <span className="department-campaign-result">마케팅팀 등록</span>
                )}
              </div>
            );
          })}
        </div>
      )}
      {hasPagination && (
        <DepartmentPagination
          label="우선 고객"
          currentStart={currentStart}
          currentEnd={currentEnd}
          total={total}
          page={currentPage}
          totalPages={totalPages ?? 0}
          onPageChange={onPageChange!}
        />
      )}
    </section>
  );
}

function CampaignQueue({
  targets,
  total,
  page,
  pageSize,
  totalPages,
  onPageChange,
  canManage,
  assignees,
  user,
  onUpdated,
}: {
  targets: CampaignTarget[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  canManage: boolean;
  assignees: TeamMember[];
  user: AuthUser;
  onUpdated: (target: CampaignTarget) => void;
}) {
  const [drafts, setDrafts] = useState<Record<number, CampaignDraft>>({});
  const [savingId, setSavingId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const currentStart = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const currentEnd = total === 0 ? 0 : Math.min(page * pageSize, total);
  const hasPagination = totalPages > 1;

  const save = async (target: CampaignTarget) => {
    const draft = drafts[target.id] ?? {
      status: target.status,
      result: target.result ?? "",
      result_code: target.result_code ?? "",
      assigned_to_user_id: target.assigned_to_user_id,
      converted: target.converted,
      retained: target.retained ?? null,
      retainedDirty: false,
      outcome_revenue: target.outcome_revenue == null ? "" : String(target.outcome_revenue),
    };
    setSavingId(target.id);
    setError("");
    setSuccess("");
    try {
      const updated = await updateCampaignTarget(target.id, {
        status: draft.status,
        ...(draft.assigned_to_user_id !== target.assigned_to_user_id
          ? { assigned_to_user_id: draft.assigned_to_user_id }
          : {}),
        result: draft.result || undefined,
        result_code: draft.result_code || undefined,
        converted: draft.converted,
        ...(draft.retainedDirty ? { retained: draft.retained } : {}),
        outcome_revenue: draft.outcome_revenue === "" ? null : Number(draft.outcome_revenue),
      });
      onUpdated(updated);
      setSuccess("캠페인 처리 결과를 저장했습니다.");
    } catch (requestError) {
      setError(campaignQueueErrorMessage(requestError));
    } finally {
      setSavingId(null);
    }
  };

  return (
    <section className="department-panel department-panel--wide">
      <div className="department-panel__heading">
        <div>
          <p className="card-kicker">WORK QUEUE</p>
          <h2>캠페인 처리 현황</h2>
        </div>
        <span className="table-count">{formatNumber(total)}건</span>
      </div>
      {error !== "" && <CampaignQueueFeedbackDialog message={error} variant="error" onClose={() => setError("")} />}
      {success !== "" && <CampaignQueueFeedbackDialog message={success} variant="success" onClose={() => setSuccess("")} />}
      {targets.length === 0 ? (
        <p className="department-empty">등록된 캠페인 대상이 없습니다.</p>
      ) : (
        <div className="department-campaign-list">
          {targets.map((target) => {
            const draft = drafts[target.id] ?? {
              status: target.status,
              result: target.result ?? "",
              result_code: target.result_code ?? "",
              assigned_to_user_id: target.assigned_to_user_id,
              converted: target.converted,
              retained: target.retained ?? null,
              retainedDirty: false,
              outcome_revenue: target.outcome_revenue == null ? "" : String(target.outcome_revenue),
            };
            const canEditTarget = canManage && (
              user.role === "admin"
              || (
                target.experiment_group === "treatment"
                && (target.assigned_to_user_id === null || target.assigned_to_user_id === user.id)
              )
            );
            const availableStatuses = campaignStatusTransitions[target.status].filter((status) => {
              if (target.experiment_group === "control") {
                return status === "pending" || status === "cancelled";
              }
              if (target.campaign_status !== "active") {
                return !["contacted", "completed"].includes(status);
              }
              return true;
            });
            const finalCodeRequired = draft.status === "completed"
              && !finalCampaignResultCodes.includes(draft.result_code as CampaignResultCode);
            const availableResultCodes = Object.entries(campaignResultLabels).filter(([code]) => {
              if (target.experiment_group === "control") {
                return code !== "contacted";
              }
              if (code === "contacted") {
                return draft.status === "contacted" || draft.status === "completed";
              }
              return draft.status === "completed";
            });
            const retentionDisabled = target.experiment_group === "treatment" && draft.status !== "completed";
            return (
              <div className="department-campaign-row" key={target.id}>
                <div>
                  <strong>{target.customer_id}</strong>
                  <small>{target.campaign_name} · {target.assigned_to_display_name ?? "미배정"}</small>
                </div>
                <span className={`campaign-status campaign-status--${target.status}`}>
                  {campaignStatusLabels[target.status]}
                </span>
                {canEditTarget ? (
                  <div className="department-campaign-controls">
                    <select
                      aria-label={`${target.customer_id} 처리 상태`}
                      value={draft.status}
                      onChange={(event) => setDrafts((current) => {
                        const status = event.target.value as CampaignStatus;
                        const isCompleted = status === "completed";
                        const isFinalCode = finalCampaignResultCodes.includes(draft.result_code as CampaignResultCode);
                        return {
                          ...current,
                          [target.id]: {
                            ...draft,
                            status,
                            result_code: !isCompleted && isFinalCode ? "" : draft.result_code,
                            converted: isCompleted ? draft.converted : false,
                            retained: retentionDisabled || !isCompleted ? null : draft.retained,
                            outcome_revenue: isCompleted && draft.converted ? draft.outcome_revenue : "",
                          },
                        };
                      })}
                    >
                      {availableStatuses.map((value) => (
                        <option value={value} key={value}>
                          {campaignStatusLabels[value]}
                        </option>
                      ))}
                    </select>
                    <div className="department-campaign-performance">
                      <select
                        aria-label={`${target.customer_id} 유지 여부`}
                        value={draft.retained === null ? "" : String(draft.retained)}
                        disabled={retentionDisabled}
                        onChange={(event) => setDrafts((current) => ({
                          ...current,
                          [target.id]: {
                          ...draft,
                            retained: event.target.value === "" ? null : event.target.value === "true",
                            retainedDirty: true,
                          },
                        }))}
                      >
                        <option value="">유지 미관측</option>
                        <option value="true">유지</option>
                        <option value="false">미유지</option>
                      </select>
                      <input
                        type="number"
                        min={0}
                        step={1000}
                        aria-label={`${target.customer_id} 성과 매출`}
                        value={draft.outcome_revenue}
                        placeholder="성과 매출"
                        disabled={!draft.converted}
                        onChange={(event) => setDrafts((current) => ({
                          ...current,
                          [target.id]: { ...draft, outcome_revenue: event.target.value },
                        }))}
                      />
                    </div>
                    <select
                      aria-label={`${target.customer_id} 결과 코드`}
                      value={draft.result_code}
                      onChange={(event) => setDrafts((current) => ({
                        ...current,
                        [target.id]: {
                          ...draft,
                          result_code: event.target.value as CampaignResultCode | "",
                          converted: event.target.value === "converted",
                          outcome_revenue: event.target.value === "converted" ? draft.outcome_revenue : "",
                        },
                      }))}
                    >
                      <option value="">결과 코드</option>
                      {availableResultCodes.map(([code, label]) => <option value={code} key={code}>{label}</option>)}
                    </select>
                    <select
                      aria-label={`${target.customer_id} 담당자`}
                      value={draft.assigned_to_user_id === null ? "" : String(draft.assigned_to_user_id)}
                      onChange={(event) => setDrafts((current) => ({
                        ...current,
                        [target.id]: {
                          ...draft,
                          assigned_to_user_id: event.target.value === "" ? null : Number(event.target.value),
                        },
                      }))}
                    >
                      <option value="">담당자 선택</option>
                      {assignees.map((assignee) => (
                        <option value={assignee.id} key={assignee.id}>
                          {assignee.display_name} · {roleLabels[assignee.role]}
                        </option>
                      ))}
                    </select>
                    <input
                      aria-label={`${target.customer_id} 처리 결과`}
                      value={draft.result}
                      placeholder="처리 결과"
                      onChange={(event) => setDrafts((current) => ({
                        ...current,
                        [target.id]: { ...draft, result: event.target.value },
                      }))}
                    />
                    <button type="button" disabled={savingId === target.id || finalCodeRequired} onClick={() => void save(target)}>
                      {savingId === target.id ? "저장 중..." : "저장"}
                    </button>
                  </div>
                ) : (
                  <span className="department-campaign-result">{target.result ?? "결과 대기"}</span>
                )}
              </div>
            );
          })}
        </div>
      )}
      {hasPagination && (
        <DepartmentPagination
          label="캠페인 처리"
          currentStart={currentStart}
          currentEnd={currentEnd}
          total={total}
          page={page}
          totalPages={totalPages}
          onPageChange={onPageChange}
        />
      )}
    </section>
  );
}

function RoleSummary({ user }: { user: AuthUser }) {
  return (
    <aside className="department-panel department-panel--role">
      <p className="card-kicker">YOUR WORKSPACE</p>
      <h2>{roleLabels[user.role]}</h2>
      <p>{roleDescriptions[user.role]}</p>
      <div className="department-role-pill">{user.username}</div>
    </aside>
  );
}

function TeamRoster({
  members,
  onUpdated,
}: {
  members: TeamMember[];
  onUpdated: (member: TeamMember) => void;
}) {
  const [drafts, setDrafts] = useState<Record<number, { role: TeamMember["role"]; is_active: boolean }>>({});
  const [savingId, setSavingId] = useState<number | null>(null);
  const [error, setError] = useState("");

  const save = async (member: TeamMember) => {
    const draft = drafts[member.id] ?? { role: member.role, is_active: member.is_active };
    setSavingId(member.id);
    setError("");
    try {
      const updated = await updateTeamMember(member.id, draft);
      onUpdated(updated);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "팀 계정 변경에 실패했습니다.");
    } finally {
      setSavingId(null);
    }
  };

  return (
    <section className="department-panel department-panel--wide">
      <div className="department-panel__heading">
        <div>
          <p className="card-kicker">TEAM ACCESS</p>
          <h2>활성 팀 계정</h2>
        </div>
        <span className="table-count">{formatNumber(members.length)}명</span>
      </div>
      {error !== "" && <p className="department-inline-error" role="alert">{error}</p>}
      <div className="department-team-list">
        {members.map((member) => {
          const draft = drafts[member.id] ?? { role: member.role, is_active: member.is_active };
          return (
            <div className="department-team-row" key={member.id}>
              <div>
                <strong>{member.display_name}</strong>
                <small>{member.username} · {member.is_active ? "활성" : "비활성"}</small>
              </div>
              <select
                aria-label={`${member.display_name} 역할`}
                className={member.role === "admin" ? "department-team-select department-team-select--locked" : "department-team-select"}
                value={draft.role}
                disabled={member.role === "admin"}
                onChange={(event) => setDrafts((current) => ({
                  ...current,
                  [member.id]: { ...draft, role: event.target.value as TeamMember["role"] },
                }))}
              >
                <option value="admin">관리자</option>
                <option value="analyst">분석 담당자</option>
                <option value="operations">운영 담당자</option>
                <option value="marketing">마케팅 담당자</option>
              </select>
              <select
                aria-label={`${member.display_name} 계정 상태`}
                className="department-team-select"
                value={draft.is_active ? "active" : "inactive"}
                onChange={(event) => setDrafts((current) => ({
                  ...current,
                  [member.id]: { ...draft, is_active: event.target.value === "active" },
                }))}
              >
                <option value="active">활성</option>
                <option value="inactive">비활성</option>
              </select>
              <button type="button" disabled={savingId === member.id} onClick={() => void save(member)}>
                {savingId === member.id ? "저장 중..." : "저장"}
              </button>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function DepartmentDashboardPage({ user }: DepartmentDashboardPageProps) {
  const [insights, setInsights] = useState<CustomerInsightList | null>(null);
  const [targets, setTargets] = useState<CampaignTarget[]>([]);
  const [campaignTargetTotal, setCampaignTargetTotal] = useState(0);
  const [campaignQueuePage, setCampaignQueuePage] = useState(1);
  const [campaignQueueTotalPages, setCampaignQueueTotalPages] = useState(0);
  const [campaignQueueStats, setCampaignQueueStats] = useState<CampaignTargetStats | null>(null);
  const [batch, setBatch] = useState<LatestBatch | null>(null);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isCreating, setIsCreating] = useState<number | null>(null);
  const [operationsInsightPage, setOperationsInsightPage] = useState(1);
  const [insightPage, setInsightPage] = useState(1);
  const [marketingRiskFilter, setMarketingRiskFilter] = useState<MarketingRiskFilter>("");
  const [marketingClusterFilter, setMarketingClusterFilter] = useState("");
  const [marketingSortBy, setMarketingSortBy] = useState<MarketingSortBy>("churn_probability");
  const [marketingSortOrder, setMarketingSortOrder] = useState<MarketingSortOrder>("desc");
  const [createMessage, setCreateMessage] = useState("");
  const [campaignConflictMessage, setCampaignConflictMessage] = useState("");
  const [insightRefreshKey, setInsightRefreshKey] = useState(0);

  const canProcessTargets = user.role === "admin" || user.role === "operations";
  const canCreateCampaignTargets = user.role === "admin" || user.role === "marketing";
  const insightQuery = useMemo(() => {
    if (user.role === "operations") {
      return {
        risk_level: "high" as const,
        sort_by: "churn_probability" as const,
        sort_order: "desc" as const,
        page: operationsInsightPage,
        page_size: OPERATIONS_INSIGHT_PAGE_SIZE,
      };
    }
    if (user.role === "marketing") {
      return {
        risk_level: marketingRiskFilter || undefined,
        cluster_name: marketingClusterFilter || undefined,
        sort_by: marketingSortBy,
        sort_order: marketingSortOrder,
        campaign_candidates_only: true,
        page: insightPage,
        page_size: MARKETING_INSIGHT_PAGE_SIZE,
      };
    }
    return {
      sort_by: "churn_probability" as const,
      sort_order: "desc" as const,
      page: 1,
      page_size: 100,
    };
  }, [insightPage, marketingClusterFilter, marketingRiskFilter, marketingSortBy, marketingSortOrder, operationsInsightPage, user.role]);

  useEffect(() => {
    let isActive = true;
    const load = async () => {
    const [insightResult, campaignResult, batchResult] = await Promise.allSettled([
        listCustomerInsights(insightQuery),
        listCampaignTargets({
    page: campaignQueuePage,
    page_size: CAMPAIGN_QUEUE_PAGE_SIZE,
    ...(user.role === "operations" ? { sort_by_priority: true } : {}),
  }),
  getLatestBatch(),
]);
      if (!isActive) {
        return;
      }
      if (insightResult.status === "fulfilled") {
        setInsights(insightResult.value);
      } else {
        setError(insightResult.reason instanceof Error ? insightResult.reason.message : "분석 결과를 불러오지 못했습니다.");
      }
      if (campaignResult.status === "fulfilled") {
        setTargets(campaignResult.value.items);
        setCampaignTargetTotal(campaignResult.value.total);
        setCampaignQueueTotalPages(campaignResult.value.total_pages);
        setCampaignQueueStats(campaignResult.value.stats ?? null);
      }
      if (batchResult.status === "fulfilled") {
        setBatch(batchResult.value);
      }
      setIsLoading(false);
    };
    void load();
    return () => {
      isActive = false;
    };
  }, [campaignQueuePage, insightQuery, insightRefreshKey]);

  useEffect(() => {
    let isActive = true;
    void listTeamMembers(user.role === "admin")
      .then((response) => {
        if (isActive) {
          const operationsMembers = response.filter(
            (member) => member.role === "operations" && member.is_active,
          );
          setMembers(
            user.role === "admin"
              ? response
              : user.role === "operations"
              ? operationsMembers.filter((member) => member.id === user.id)
              : operationsMembers,
          );
        }
      })
      .catch(() => {
        if (isActive) {
          setMembers([]);
        }
      });
    return () => {
      isActive = false;
    };
  }, [user.id, user.role]);

  const createCampaign = async (insight: CustomerInsight) => {
    if (!canCreateCampaignTargets) {
      return;
    }
    setIsCreating(insight.id);
    setCreateMessage("");
    try {
      const target = await createCampaignTarget({
        customer_insight_id: insight.id,
        campaign_name: user.role === "marketing" ? "세그먼트 리텐션 캠페인" : "고위험 고객 리텐션",
      });
      setTargets((current) => [target, ...current]);
      setCampaignTargetTotal((current) => current + 1);
      setCreateMessage("캠페인 대상에 미배정 상태로 등록했습니다.");
      setCampaignQueuePage(1);
      setInsightPage(1);
      setInsightRefreshKey((current) => current + 1);
    } catch (requestError) {
      const apiError = requestError as ApiError;
      if (apiError.status === 409) {
        setCreateMessage("");
        setCampaignConflictMessage(
          "다른 사용자가 먼저 등록했거나 이 고객이 최근 접촉·수신 거부 상태로 변경되어 등록할 수 없습니다.",
        );
        setCampaignQueuePage(1);
        setInsightPage(1);
        setInsightRefreshKey((current) => current + 1);
      } else {
        setCreateMessage(requestError instanceof Error ? requestError.message : "캠페인 등록에 실패했습니다.");
      }
    } finally {
      setIsCreating(null);
    }
  };

  const updateMarketingRiskFilter = (value: MarketingRiskFilter) => {
    setMarketingRiskFilter(value);
    setInsightPage(1);
  };

  const handleCampaignQueueUpdated = (updated: CampaignTarget) => {
    setTargets((current) => current.map((target) => target.id === updated.id ? updated : target));
    setInsightRefreshKey((current) => current + 1);
  };

  const updateMarketingClusterFilter = (value: string) => {
    setMarketingClusterFilter(value);
    setInsightPage(1);
  };

  const updateMarketingSortBy = (value: MarketingSortBy) => {
    setMarketingSortBy(value);
    setMarketingSortOrder(value === "activity_gap" ? "asc" : "desc");
    setInsightPage(1);
  };

  const updateMarketingSortOrder = (value: MarketingSortOrder) => {
    setMarketingSortOrder(value);
    setInsightPage(1);
  };

  const resetMarketingFilters = () => {
    setMarketingRiskFilter("");
    setMarketingClusterFilter("");
    setMarketingSortBy("churn_probability");
    setMarketingSortOrder("desc");
    setInsightPage(1);
  };

  const highRiskCount = insights?.stats.risk_counts.high ?? 0;
  const pendingCount = campaignQueueStats?.unprocessed_targets ?? 0;
  const completedCount = campaignQueueStats?.status_counts.completed ?? 0;
  const campaignStatusCounts = useMemo(() => Object.fromEntries(
    Object.keys(campaignStatusLabels).map((status) => [
      status,
      campaignQueueStats?.status_counts[status] ?? 0,
    ]),
  ) as Record<CampaignStatus, number>, [campaignQueueStats]);
  const campaignStatusTotal = Math.max(
    campaignQueueStats?.total_targets ?? campaignTargetTotal,
    1,
  );

  const roleContent = user.role === "operations" ? (
    <>
      <section className="department-stats">
        <StatCard label="HIGH RISK" value={formatNumber(highRiskCount)} caption="우선 상담 대상" tone="pink" />
        <StatCard label="OPEN QUEUE" value={formatNumber(pendingCount)} caption="처리 대기 캠페인" tone="orange" />
        <StatCard label="AVERAGE CHURN" value={formatPercent(insights?.stats.average_churn_probability ?? 0)} caption="고위험 고객 기준" tone="purple" />
        <BatchCard batch={batch} />
      </section>
      <InsightPriorityTable
        kicker="CUSTOMER PRIORITY"
        heading="우선 관리 고객"
        insights={insights?.items ?? []}
        targets={targets}
        campaignName="리텐션 등록"
        onCreate={canCreateCampaignTargets ? (item) => void createCampaign(item) : undefined}
        isCreating={isCreating}
        total={insights?.total ?? 0}
        page={operationsInsightPage}
        pageSize={OPERATIONS_INSIGHT_PAGE_SIZE}
        totalPages={insights?.total_pages ?? 0}
        onPageChange={setOperationsInsightPage}
      />
      <CampaignQueue
        targets={targets}
        total={campaignTargetTotal}
        page={campaignQueuePage}
        pageSize={CAMPAIGN_QUEUE_PAGE_SIZE}
        totalPages={campaignQueueTotalPages}
        onPageChange={setCampaignQueuePage}
        canManage={canProcessTargets}
        assignees={members.filter((member) => member.role === "operations" && member.is_active)}
        user={user}
        onUpdated={handleCampaignQueueUpdated}
      />
    </>
  ) : user.role === "marketing" ? (
    <>
      <section className="department-stats">
        <StatCard label="TARGETS" value={formatNumber(campaignTargetTotal)} caption="등록된 캠페인 대상" tone="purple" />
        <StatCard label="OPEN QUEUE" value={formatNumber(pendingCount)} caption="실행 대기 대상" tone="orange" />
        <StatCard label="COMPLETED" value={formatNumber(completedCount)} caption="처리 완료 캠페인" tone="green" />
        <BatchCard batch={batch} />
      </section>
      <section className="department-panel department-panel--wide">
        <div className="department-panel__heading">
          <div>
            <p className="card-kicker">CAMPAIGN FUNNEL</p>
            <h2>캠페인 상태 분포</h2>
          </div>
        </div>
        <div className="department-funnel">
          {Object.entries(campaignStatusLabels).map(([status, label]) => (
            <div key={status}>
              <span>{label}</span>
              <strong>{campaignStatusCounts[status as CampaignStatus]}</strong>
              <i
                style={{
                  width: `${Math.min(
                    Math.max(
                      (campaignStatusCounts[status as CampaignStatus] / campaignStatusTotal) * 100,
                      campaignStatusCounts[status as CampaignStatus] > 0 ? 8 : 0,
                    ),
                    100,
                  )}%`,
                }}
              />
            </div>
          ))}
        </div>
      </section>
      <InsightPriorityTable
        kicker="CAMPAIGN CANDIDATES"
        heading="캠페인 후보 고객"
        toolbar={(
          <MarketingCandidateFilters
            riskLevel={marketingRiskFilter}
            clusterName={marketingClusterFilter}
            sortBy={marketingSortBy}
            sortOrder={marketingSortOrder}
            clusterOptions={insights?.stats.cluster_options ?? {}}
            onRiskLevelChange={updateMarketingRiskFilter}
            onClusterNameChange={updateMarketingClusterFilter}
            onSortByChange={updateMarketingSortBy}
            onSortOrderChange={updateMarketingSortOrder}
            onReset={resetMarketingFilters}
          />
        )}
        insights={insights?.items ?? []}
        targets={targets}
        campaignName="캠페인 등록"
        onCreate={undefined}
        isCreating={isCreating}
        total={insights?.total ?? 0}
        page={insightPage}
        pageSize={MARKETING_INSIGHT_PAGE_SIZE}
        totalPages={insights?.total_pages ?? 0}
        onPageChange={setInsightPage}
      />
      <CampaignQueue
        targets={targets}
        total={campaignTargetTotal}
        page={campaignQueuePage}
        pageSize={CAMPAIGN_QUEUE_PAGE_SIZE}
        totalPages={campaignQueueTotalPages}
        onPageChange={setCampaignQueuePage}
        canManage={canProcessTargets}
        assignees={members.filter((member) => member.role === "operations" && member.is_active)}
        user={user}
        onUpdated={handleCampaignQueueUpdated}
      />
    </>
  ) : (
    <>
      <section className="department-stats">
        <StatCard label="CUSTOMERS" value={formatNumber(insights?.stats.total ?? 0)} caption="분석 대상 고객" tone="purple" />
        <StatCard label="CAMPAIGN QUEUE" value={formatNumber(campaignTargetTotal)} caption="전체 업무 대상" tone="orange" />
        <StatCard label="HIGH RISK" value={formatNumber(highRiskCount)} caption="고위험 고객" tone="pink" />
        <BatchCard batch={batch} />
      </section>
      <TeamRoster members={members} onUpdated={(updated) => setMembers((current) => current.map((member) => member.id === updated.id ? updated : member))} />
    </>
  );

  return (
    <div className={`department-layout--${user.role}`}>
      {isLoading && <section className="department-loading">부서별 업무 데이터를 불러오는 중입니다.</section>}
      {!isLoading && error !== "" && <section className="department-error" role="alert">{error}</section>}
      {!isLoading && error === "" && (
        <div className="department-content">
          <RoleSummary user={user} />
          {createMessage !== "" && <p className="department-message" role="status">{createMessage}</p>}
          {roleContent}
        </div>
      )}
      {campaignConflictMessage !== "" && (
        <CampaignConflictDialog
          message={campaignConflictMessage}
          onClose={() => setCampaignConflictMessage("")}
        />
      )}
    </div>
  );
}
