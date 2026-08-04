import { useEffect, useMemo, useState } from "react";

import type { ApiError } from "../../api/client";
import {
  createCampaignTarget,
  type Campaign,
} from "../../api/campaigns";
import {
  listCustomerInsights,
  type CustomerInsight,
  type CustomerInsightList,
  type InsightQuery,
} from "../../api/insights";
import { CampaignPagination } from "./CampaignPagination";

const PAGE_SIZE = 8;

const riskLabels: Record<CustomerInsight["risk_level"], string> = {
  low: "낮음",
  medium: "주의",
  high: "높음",
};

type RiskFilter = "" | NonNullable<InsightQuery["risk_level"]>;
type SortBy = NonNullable<InsightQuery["sort_by"]>;
type SortOrder = NonNullable<InsightQuery["sort_order"]>;

function formatNumber(value: number): string {
  return new Intl.NumberFormat("ko-KR").format(value);
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatDecimal(value: number): string {
  return new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 }).format(value);
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function RiskBadge({ risk }: { risk: CustomerInsight["risk_level"] }) {
  return (
    <span className={`risk-badge risk-badge--${risk}`}>
      <span aria-hidden="true" />
      {riskLabels[risk]}
    </span>
  );
}

function CandidateConflictDialog({ onClose }: { onClose: () => void }) {
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
        aria-labelledby="campaign-management-conflict-title"
      >
        <div className="campaign-feedback-help-dialog__header">
          <div>
            <p className="card-kicker">CAMPAIGN GUARD</p>
            <h3 id="campaign-management-conflict-title">캠페인 대상 등록 불가</h3>
          </div>
          <button
            className="campaign-feedback-help-close"
            type="button"
            aria-label="캠페인 대상 등록 안내 닫기"
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <p className="campaign-conflict-dialog__message">
          다른 사용자가 먼저 등록했거나, 최근 접촉·수신 거부·중복 캠페인 상태로 변경되었습니다.
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

export function CampaignCandidatePanel({
  selectedCampaign,
  canManage,
  onRegistered,
}: {
  selectedCampaign: Campaign | null;
  canManage: boolean;
  onRegistered: () => void;
}) {
  const [fetchedData, setFetchedData] = useState<CustomerInsightList | null>(null);
  const [riskFilter, setRiskFilter] = useState<RiskFilter>("");
  const [clusterFilter, setClusterFilter] = useState("");
  const [sortBy, setSortBy] = useState<SortBy>("churn_probability");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");
  const [page, setPage] = useState(1);
  const [refreshKey, setRefreshKey] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [isCreating, setIsCreating] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [showConflict, setShowConflict] = useState(false);

  const query = useMemo(() => ({
    risk_level: riskFilter || undefined,
    cluster_name: clusterFilter || undefined,
    sort_by: sortBy,
    sort_order: sortOrder,
    campaign_candidates_only: true,
    campaign_id: selectedCampaign?.id,
    page,
    page_size: PAGE_SIZE,
  }), [clusterFilter, page, riskFilter, selectedCampaign?.id, sortBy, sortOrder]);

  const selectedCampaignId = selectedCampaign?.id ?? null;

  useEffect(() => {
    if (selectedCampaignId === null) {
      return;
    }
    let isActive = true;
    const loadCandidates = async () => {
      setIsLoading(true);
      try {
        const response = await listCustomerInsights(query);
        if (isActive) {
          setFetchedData(response);
          setError("");
        }
      } catch (requestError) {
        if (isActive) {
          setError(errorMessage(requestError, "캠페인 후보를 불러오지 못했습니다."));
        }
      } finally {
        if (isActive) {
          setIsLoading(false);
        }
      }
    };
    void loadCandidates();
    return () => {
      isActive = false;
    };
  }, [query, refreshKey, selectedCampaignId]);

  // 캠페인 선택이 해제되면 직전 캠페인의 후보 목록을 보여주지 않는다
  // (effect 안에서 setState로 초기화하는 대신 파생값으로 처리).
  const data = selectedCampaignId === null ? null : fetchedData;

  const clusterOptions = Object.entries(data?.stats.cluster_options ?? {}).sort(
    ([firstName, firstCount], [secondName, secondCount]) => secondCount - firstCount
      || firstName.localeCompare(secondName),
  );
  const canRegister = canManage
    && selectedCampaign !== null
    && ["draft", "scheduled", "active"].includes(selectedCampaign.status);

  const changePage = (nextPage: number) => {
    setPage(nextPage);
  };

  const resetFilters = () => {
    setRiskFilter("");
    setClusterFilter("");
    setSortBy("churn_probability");
    setSortOrder("desc");
    setPage(1);
  };

  const registerCandidate = async (insight: CustomerInsight) => {
    if (!canRegister || selectedCampaign === null) {
      return;
    }
    setIsCreating(insight.id);
    setMessage("");
    setError("");
    try {
      await createCampaignTarget({
        customer_insight_id: insight.id,
        campaign_id: selectedCampaign.id,
      });
      setMessage(`고객 ${insight.customer_id}을(를) ${selectedCampaign.name}에 등록했습니다.`);
      setRefreshKey((current) => current + 1);
      onRegistered();
    } catch (requestError) {
      const apiError = requestError as ApiError;
      if (apiError.status === 409) {
        setShowConflict(true);
        setRefreshKey((current) => current + 1);
      } else {
        setError(errorMessage(requestError, "캠페인 대상 등록에 실패했습니다."));
      }
    } finally {
      setIsCreating(null);
    }
  };

  return (
    <section className="campaign-candidate-panel" aria-label="캠페인 후보 고객">
      <div className="campaign-panel-heading">
        <div>
          <p className="card-kicker">CUSTOMER CANDIDATES</p>
          <h3>캠페인 후보 고객</h3>
        </div>
        <span className="campaign-panel-count">{data === null ? "-" : `${formatNumber(data.total)}명`}</span>
      </div>
      <p className="campaign-candidate-panel__caption">
        분석 결과를 확인한 뒤 현재 선택한 캠페인에 등록할 고객을 선택합니다.
      </p>
      {selectedCampaign === null ? (
        <p className="campaign-empty-copy">먼저 캠페인을 선택하세요.</p>
      ) : (
        <>
          <div className="insight-filters campaign-candidate-filters">
            <label className="filter-field">
              <span>위험도</span>
              <select
                aria-label="캠페인 관리 후보 위험도"
                value={riskFilter}
                onChange={(event) => {
                  setRiskFilter(event.target.value as RiskFilter);
                  setPage(1);
                }}
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
                aria-label="캠페인 관리 후보 군집"
                value={clusterFilter}
                onChange={(event) => {
                  setClusterFilter(event.target.value);
                  setPage(1);
                }}
              >
                <option value="">전체 군집</option>
                {clusterOptions.map(([name, count]) => (
                  <option value={name} key={name}>{name} ({formatNumber(count)}명)</option>
                ))}
              </select>
            </label>
            <label className="filter-field">
              <span>정렬 기준</span>
              <select
                aria-label="캠페인 관리 후보 정렬 기준"
                value={sortBy}
                onChange={(event) => {
                  const value = event.target.value as SortBy;
                  setSortBy(value);
                  setSortOrder(value === "activity_gap" ? "asc" : "desc");
                  setPage(1);
                }}
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
                aria-label="캠페인 관리 후보 정렬 순서"
                value={sortOrder}
                onChange={(event) => {
                  setSortOrder(event.target.value as SortOrder);
                  setPage(1);
                }}
              >
                <option value="desc">높은 값부터</option>
                <option value="asc">낮은 값부터</option>
              </select>
            </label>
            <button className="reset-filter-button" type="button" onClick={resetFilters}>초기화</button>
          </div>
          {!canRegister && (
            <p className="campaign-candidate-panel__notice">
              완료·취소된 캠페인에는 새 대상을 등록할 수 없습니다.
            </p>
          )}
          {message !== "" && <p className="campaign-management-message" role="status">{message}</p>}
          {error !== "" && <p className="campaign-management-error" role="alert">{error}</p>}
          {isLoading ? (
            <p className="campaign-empty-copy">후보 고객을 불러오는 중입니다.</p>
          ) : data === null || data.items.length === 0 ? (
            <p className="campaign-empty-copy">현재 조건에 맞는 후보 고객이 없습니다.</p>
          ) : (
            <div className="department-insight-list campaign-candidate-list" role="list">
              <div className="department-insight-list__header" aria-hidden="true">
                <span>고객</span><span>위험도</span><span>이탈 확률</span><span>활동성 갭</span>
                <span>예상 거래</span><span>군집</span><span>추천 액션</span><span>캠페인</span>
              </div>
              {data.items.map((insight) => (
                <div className="department-insight-row" role="listitem" key={insight.id}>
                  <span className="department-insight-row__customer">
                    <strong>{insight.customer_id}</strong>
                    <small>{formatDate(insight.scored_at)}</small>
                  </span>
                  <span className="department-insight-row__risk"><RiskBadge risk={insight.risk_level} /></span>
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
                    <small>신뢰도 {insight.cluster_confidence == null ? "-" : formatPercent(insight.cluster_confidence)}</small>
                  </span>
                  <span className="department-insight-row__action">{insight.recommended_action}</span>
                  <button
                    className="department-action-button"
                    type="button"
                    disabled={!canRegister || isCreating === insight.id}
                    onClick={() => void registerCandidate(insight)}
                  >
                    {isCreating === insight.id ? "등록 중..." : canRegister ? "대상 등록" : "등록 불가"}
                  </button>
                </div>
              ))}
            </div>
          )}
          {data !== null && data.total > 0 && (
            <CampaignPagination
              label="캠페인 후보"
              total={data.total}
              page={data.page}
              pageSize={data.page_size}
              totalPages={Math.max(data.total_pages, 1)}
              summarySuffix="명"
              onPageChange={changePage}
            />
          )}
        </>
      )}
      {showConflict && <CandidateConflictDialog onClose={() => setShowConflict(false)} />}
    </section>
  );
}
