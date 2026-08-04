import { useState } from "react";

import {
  cancelBulkTargeting,
  executeBulkTargeting,
  previewBulkTargeting,
  rerunBulkTargeting,
  type BulkTargetingRun,
  type BulkTargetingSegment,
} from "../../api/campaigns";
import type { TeamMember } from "../../api/team";

const segmentLabels: Record<BulkTargetingSegment, string> = {
  small_balance_decline: "소액 잔액·거래 급감 (긴급)",
  dormant_full_payer: "완납형 저활동 (이탈 임박)",
  high_risk_retention: "고위험 고객 리텐션",
  medium_reactivation: "중위험·활동성 하락 재활성화",
  active_full_payer: "완납형 우량 (거래 활성화)",
  low_risk_upsell: "저위험·우량군 업셀링",
  stable_prime: "안정 우량 (업셀링)",
};

// 담당자가 근거 없이 캠페인을 집행하지 않도록, 선정 조건과 원본 데이터에서
// 검증된 실제 이탈률을 함께 보여줍니다(백엔드 기본 설명과 같은 내용).
const segmentDescriptions: Record<BulkTargetingSegment, string> = {
  small_balance_decline:
    "리볼빙 잔액 1~499원 + 분기 거래가 0.6배 미만으로 급감한 고객. 실제 이탈률 약 90%로 가장 시급합니다.",
  dormant_full_payer:
    "리볼빙 잔액 0원(완납) + 연간 거래 55건 미만인 고객. 실제 이탈률 72%로, 잔액이 없어 전환 비용도 없는 상태입니다.",
  high_risk_retention: "risk_level이 high인 고객을 이탈 확률 우선순위로 선정합니다.",
  medium_reactivation:
    "risk_level이 medium이고 activity_gap 하위 20% 기준 이하인 고객을 선정합니다.",
  active_full_payer:
    "리볼빙 잔액 0원(완납)이지만 연간 거래 55건 이상인 고객. 실제 이탈률 12%로 평균(16%)보다 낮아, 리볼빙 유도 대신 거래 리워드·교차판매 대상입니다.",
  low_risk_upsell: "risk_level이 low이고 우량(예상이상) 군집인 고객을 선정합니다.",
  stable_prime:
    "리볼빙 잔액 1,000~2,000원 구간 고객. 실제 이탈률 4.6%로 전 구간 중 가장 낮아 한도 상향·프리미엄 전환에 적합합니다.",
};

// 우선순위가 높은(시급한) 세그먼트부터 나열합니다.
const segmentActions: BulkTargetingSegment[] = [
  "small_balance_decline",
  "dormant_full_payer",
  "high_risk_retention",
  "medium_reactivation",
  "active_full_payer",
  "low_risk_upsell",
  "stable_prime",
];

type BulkTargetingPanelProps = {
  assignees: TeamMember[];
  onExecuted: () => void;
};

function formatNumber(value: number): string {
  return new Intl.NumberFormat("ko-KR").format(value);
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function defaultCampaignName(segment: BulkTargetingSegment): string {
  return `${segmentLabels[segment]} 일괄 캠페인`;
}

export function BulkTargetingPanel({ assignees, onExecuted }: BulkTargetingPanelProps) {
  const [segment, setSegment] = useState<BulkTargetingSegment>("high_risk_retention");
  const [campaignName, setCampaignName] = useState(defaultCampaignName("high_risk_retention"));
  const [channel, setChannel] = useState("전화");
  const [assignedToUserId, setAssignedToUserId] = useState("");
  const [recentContactDays, setRecentContactDays] = useState(30);
  const [maxTargets, setMaxTargets] = useState(1000);
  const [experimentEnabled, setExperimentEnabled] = useState(true);
  const [controlGroupRatio, setControlGroupRatio] = useState(0.2);
  const [fixedCost, setFixedCost] = useState(0);
  const [costPerContact, setCostPerContact] = useState(0);
  const [revenuePerConversion, setRevenuePerConversion] = useState(0);
  const [retentionWindowDays, setRetentionWindowDays] = useState(30);
  const [run, setRun] = useState<BulkTargetingRun | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const handleSegmentChange = (nextSegment: BulkTargetingSegment) => {
    setSegment(nextSegment);
    setCampaignName(defaultCampaignName(nextSegment));
    setRun(null);
    setMessage("");
    setError("");
  };

  const handlePreview = async () => {
    setIsSubmitting(true);
    setMessage("");
    setError("");
    try {
      const response = await previewBulkTargeting({
        segment,
        campaign_name: campaignName.trim() || null,
        description: `${segmentLabels[segment]} 세그먼트 자동 타기팅`,
        channel: channel.trim() || null,
        assigned_to_user_id: assignedToUserId === "" ? null : Number(assignedToUserId),
        recent_contact_days: recentContactDays,
        activity_gap_quantile: 0.2,
        cluster_name: segment === "low_risk_upsell" ? "우량(예상이상)" : null,
        max_targets: maxTargets,
        source_as_of_date: null,
        experiment_enabled: experimentEnabled,
        control_group_ratio: experimentEnabled ? controlGroupRatio : 0,
        fixed_cost: fixedCost,
        cost_per_contact: costPerContact,
        revenue_per_conversion: revenuePerConversion,
        retention_window_days: retentionWindowDays,
      });
      setRun(response);
      setMessage("미리보기를 생성했습니다. 대상과 제외 집계를 확인한 뒤 실행하세요.");
    } catch (requestError: unknown) {
      setError(errorMessage(requestError, "일괄 타기팅 미리보기에 실패했습니다."));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleExecute = async () => {
    if (run === null || run.status !== "previewed") {
      return;
    }
    setIsSubmitting(true);
    setMessage("");
    setError("");
    try {
      const response = await executeBulkTargeting(run.id);
      setRun(response);
      setMessage(`${formatNumber(response.created_count)}명을 draft 캠페인 대상으로 등록했습니다.`);
      onExecuted();
    } catch (requestError: unknown) {
      setError(errorMessage(requestError, "일괄 타기팅 실행에 실패했습니다."));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCancel = async () => {
    if (run === null || run.status === "cancelled") {
      return;
    }
    setIsSubmitting(true);
    setMessage("");
    setError("");
    try {
      const response = await cancelBulkTargeting(run.id);
      setRun(response);
      setMessage(
        response.cancelled_target_count > 0
          ? `${formatNumber(response.cancelled_target_count)}명의 미처리 대상을 취소했습니다.`
          : "일괄 타기팅 미리보기를 취소했습니다.",
      );
      onExecuted();
    } catch (requestError: unknown) {
      setError(errorMessage(requestError, "일괄 타기팅 취소에 실패했습니다."));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRerun = async () => {
    if (run === null || run.status !== "cancelled") {
      return;
    }
    setIsSubmitting(true);
    setMessage("");
    setError("");
    try {
      const response = await rerunBulkTargeting(run.id, {
        campaign_name: `${campaignName.trim() || run.campaign_name} 재실행`,
        max_targets: maxTargets,
      });
      setRun(response);
      setMessage("취소된 정책으로 새 미리보기를 생성했습니다.");
    } catch (requestError: unknown) {
      setError(errorMessage(requestError, "일괄 타기팅 재실행에 실패했습니다."));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <section className="bulk-targeting-panel" aria-label="세그먼트 일괄 타기팅">
      <div className="campaign-panel-heading">
        <div>
          <p className="card-kicker">SEGMENT TARGETING</p>
          <h2>세그먼트 일괄 타기팅</h2>
        </div>
        <span className="bulk-targeting-badge">ADMIN · MARKETING</span>
      </div>
      <div className="bulk-targeting-layout">
        <div className="bulk-targeting-form">
          <label>
            <span>타기팅 세그먼트</span>
            <select
              value={segment}
              onChange={(event) => handleSegmentChange(event.target.value as BulkTargetingSegment)}
            >
              {segmentActions.map((option) => (
                <option value={option} key={option}>{segmentLabels[option]}</option>
              ))}
            </select>
          </label>
          <p className="bulk-targeting-description">{segmentDescriptions[segment]}</p>
          <label>
            <span>캠페인 이름</span>
            <input value={campaignName} maxLength={150} onChange={(event) => setCampaignName(event.target.value)} />
          </label>
          <div className="bulk-targeting-form-row">
            <label>
              <span>채널</span>
              <input value={channel} maxLength={30} onChange={(event) => setChannel(event.target.value)} />
            </label>
            <label>
              <span>초기 담당자</span>
              <select value={assignedToUserId} onChange={(event) => setAssignedToUserId(event.target.value)}>
                <option value="">미배정</option>
                {assignees.map((assignee) => (
                  <option value={assignee.id} key={assignee.id}>{assignee.display_name}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="bulk-targeting-form-row bulk-targeting-form-row--experiment">
            <label className="bulk-targeting-checkbox">
              <input type="checkbox" checked={experimentEnabled} onChange={(event) => setExperimentEnabled(event.target.checked)} />
              <span>A/B 테스트·대조군 사용</span>
            </label>
            <label>
              <span>대조군 비율</span>
              <input type="number" min={0.01} max={0.99} step={0.01} disabled={!experimentEnabled} value={controlGroupRatio} onChange={(event) => setControlGroupRatio(Number(event.target.value))} />
            </label>
            <label>
              <span>유지 관측(일)</span>
              <input type="number" min={1} max={365} value={retentionWindowDays} onChange={(event) => setRetentionWindowDays(Number(event.target.value))} />
            </label>
          </div>
          <div className="bulk-targeting-form-row">
            <label>
              <span>고정 비용</span>
              <input type="number" min={0} step={1000} value={fixedCost} onChange={(event) => setFixedCost(Number(event.target.value))} />
            </label>
            <label>
              <span>접촉당 비용</span>
              <input type="number" min={0} step={100} value={costPerContact} onChange={(event) => setCostPerContact(Number(event.target.value))} />
            </label>
            <label>
              <span>전환당 매출</span>
              <input type="number" min={0} step={1000} value={revenuePerConversion} onChange={(event) => setRevenuePerConversion(Number(event.target.value))} />
            </label>
          </div>
          <div className="bulk-targeting-form-row">
            <label>
              <span>최근 접촉 제외(일)</span>
              <input type="number" min={0} max={365} value={recentContactDays} onChange={(event) => setRecentContactDays(Number(event.target.value))} />
            </label>
            <label>
              <span>최대 대상 수</span>
              <input type="number" min={1} max={10000} value={maxTargets} onChange={(event) => setMaxTargets(Number(event.target.value))} />
            </label>
          </div>
          <div className="bulk-targeting-actions">
            <button className="campaign-primary-button" type="button" disabled={isSubmitting || campaignName.trim() === ""} onClick={() => void handlePreview()}>
              {isSubmitting ? "처리 중..." : "미리보기 생성"}
            </button>
            {run?.status === "previewed" && (
              <button className="campaign-secondary-button" type="button" disabled={isSubmitting} onClick={() => void handleExecute()}>
                대상 등록 실행
              </button>
            )}
            {run !== null && run.status !== "cancelled" && (
              <button className="campaign-link-button" type="button" disabled={isSubmitting} onClick={() => void handleCancel()}>
                일괄 타기팅 취소
              </button>
            )}
            {run?.status === "cancelled" && (
              <button className="campaign-secondary-button" type="button" disabled={isSubmitting} onClick={() => void handleRerun()}>
                취소 정책 재실행
              </button>
            )}
          </div>
        </div>
        <div className="bulk-targeting-result">
          {run === null ? (
            <div className="bulk-targeting-empty">
              <strong>미리보기 결과</strong>
              <p>세그먼트를 선택하고 미리보기를 생성하면 활성 캠페인·최근 접촉·수신 거부 제외 결과가 표시됩니다.</p>
            </div>
          ) : (
            <>
              <div className="bulk-targeting-result-heading">
                <div>
                  <span className={`campaign-lifecycle campaign-lifecycle--${run.status}`}>{run.status}</span>
                  <strong>{run.campaign_name}</strong>
                </div>
                <small>배치 #{run.id}</small>
              </div>
              <div className="bulk-targeting-counts">
                <span><strong>{formatNumber(run.eligible_count)}</strong>등록 가능</span>
                <span><strong>{formatNumber(run.skipped_active_campaign_count)}</strong>활성 캠페인 제외</span>
                <span><strong>{formatNumber(run.skipped_recent_contact_count)}</strong>최근 접촉 제외</span>
                <span><strong>{formatNumber(run.skipped_opt_out_count)}</strong>수신 거부 제외</span>
              </div>
              <div className="bulk-targeting-preview-list">
                {run.items.slice(0, 6).map((item) => (
                  <div className="bulk-targeting-preview-item" key={item.customer_insight_id}>
                    <strong>고객 {item.customer_id}</strong>
                    <span>{item.risk_level} · 활동성 갭 {item.activity_gap.toFixed(1)}</span>
                    <small>{item.recommended_action}</small>
                  </div>
                ))}
                {run.items.length === 0 && <p className="campaign-empty-copy">등록 가능한 고객이 없습니다.</p>}
              </div>
            </>
          )}
        </div>
      </div>
      {message !== "" && <p className="campaign-management-message" role="status">{message}</p>}
      {error !== "" && <p className="campaign-management-error" role="alert">{error}</p>}
    </section>
  );
}
