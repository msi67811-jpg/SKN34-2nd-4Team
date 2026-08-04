import { useEffect, useMemo, useState, type ClipboardEvent, type KeyboardEvent } from "react";

import type { AuthUser } from "../../api/auth";
import {
  createCampaign,
  listCampaignEvents,
  listCampaignTargetsByCampaign,
  listCampaigns,
  updateCampaign,
  updateCampaignTarget,
  type Campaign,
  type CampaignEventList,
  type CampaignLifecycleStatus,
  type CampaignList,
  type CampaignResultCode,
  type CampaignStatus,
  type CampaignTarget,
  type CampaignTargetList,
} from "../../api/campaigns";
import { listTeamMembers, type TeamMember } from "../../api/team";
import { BulkTargetingPanel } from "./BulkTargetingPanel";
import { CampaignCandidatePanel } from "./CampaignCandidatePanel";
import { CampaignPagination } from "./CampaignPagination";
import { CampaignPerformancePanel } from "./CampaignPerformancePanel";

const PAGE_SIZE = 8;

const lifecycleLabels: Record<CampaignLifecycleStatus, string> = {
  draft: "초안",
  scheduled: "예약",
  active: "진행 중",
  paused: "일시 중지",
  completed: "완료",
  cancelled: "취소",
};

const lifecycleTransitions: Record<CampaignLifecycleStatus, CampaignLifecycleStatus[]> = {
  draft: ["draft", "scheduled", "active", "cancelled"],
  scheduled: ["scheduled", "active", "paused", "cancelled"],
  active: ["active", "paused", "completed", "cancelled"],
  paused: ["paused", "active", "completed", "cancelled"],
  completed: ["completed"],
  cancelled: ["cancelled"],
};

const targetStatusLabels: Record<CampaignStatus, string> = {
  pending: "대기",
  assigned: "담당 배정",
  contacted: "접촉 완료",
  completed: "처리 완료",
  cancelled: "취소",
};

const targetStatusTransitions: Record<CampaignStatus, CampaignStatus[]> = {
  pending: ["pending", "assigned", "cancelled"],
  assigned: ["assigned", "contacted", "cancelled"],
  contacted: ["contacted", "completed", "cancelled"],
  completed: ["completed"],
  cancelled: ["cancelled"],
};

const resultCodeLabels: Record<CampaignResultCode, string> = {
  contacted: "접촉",
  converted: "전환",
  not_converted: "미전환",
  no_response: "응답 없음",
  declined: "거절",
  opted_out: "수신 거부",
  invalid_contact: "연락처 오류",
};

const eventLabels: Record<string, string> = {
  created: "캠페인 생성",
  status_changed: "상태 변경",
  assigned: "담당자 배정",
  result_updated: "처리 결과 변경",
  conversion_updated: "전환 여부 변경",
};

type DetailTabKey = "overview" | "candidates" | "targets" | "events";

const DETAIL_TABS: Array<{ key: DetailTabKey; label: string }> = [
  { key: "overview", label: "캠페인" },
  { key: "candidates", label: "캠페인 후보 고객" },
  { key: "targets", label: "캠페인 대상" },
  { key: "events", label: "캠페인 이벤트 이력" },
];

type CampaignManagementPageProps = {
  user: AuthUser;
};

type CampaignForm = {
  name: string;
  description: string;
  channel: string;
  status: CampaignLifecycleStatus;
  start_at: string;
  end_at: string;
  experiment_enabled: boolean;
  control_group_ratio: number;
  fixed_cost: number;
  cost_per_contact: number;
  revenue_per_conversion: number;
  retention_window_days: number;
};

type TargetDraft = {
  status: CampaignStatus;
  assigned_to_user_id: number | null;
  result: string;
  result_code: CampaignResultCode | "";
  converted: boolean;
  retained: boolean | null;
  outcome_revenue: string;
};

function emptyCampaignForm(): CampaignForm {
  return {
    name: "",
    description: "",
    channel: "",
    status: "draft",
    start_at: "",
    end_at: "",
    experiment_enabled: false,
    control_group_ratio: 0.2,
    fixed_cost: 0,
    cost_per_contact: 0,
    revenue_per_conversion: 0,
    retention_window_days: 30,
  };
}

function campaignToForm(campaign: Campaign): CampaignForm {
  return {
    name: campaign.name,
    description: campaign.description ?? "",
    channel: campaign.channel ?? "",
    status: campaign.status,
    start_at: toDateTimeInput(campaign.start_at),
    end_at: toDateTimeInput(campaign.end_at),
    experiment_enabled: campaign.experiment_enabled,
    control_group_ratio: campaign.control_group_ratio,
    fixed_cost: campaign.fixed_cost,
    cost_per_contact: campaign.cost_per_contact,
    revenue_per_conversion: campaign.revenue_per_conversion,
    retention_window_days: campaign.retention_window_days,
  };
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("ko-KR").format(value);
}

function formatDate(value: string | null): string {
  if (value === null) {
    return "-";
  }
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function toDateTimeInput(value: string | null): string {
  if (value === null) {
    return "";
  }
  const date = new Date(value);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function preventDateTextEntry(event: KeyboardEvent<HTMLInputElement>) {
  if ((event.ctrlKey || event.metaKey) && ["a", "c"].includes(event.key.toLowerCase())) {
    return;
  }
  if (event.key.length === 1 || event.key === "Backspace" || event.key === "Delete") {
    event.preventDefault();
  }
}

function preventDatePaste(event: ClipboardEvent<HTMLInputElement>) {
  event.preventDefault();
}

function toIsoDate(value: string): string | null {
  return value === "" ? null : new Date(value).toISOString();
}

function getCustomerId(value: string): number | undefined {
  const customerId = Number(value.trim());
  return Number.isInteger(customerId) && customerId > 0 ? customerId : undefined;
}

function getTargetDraft(target: CampaignTarget): TargetDraft {
  return {
    status: target.status,
    assigned_to_user_id: target.assigned_to_user_id,
    result: target.result ?? "",
    result_code: target.result_code ?? "",
    converted: target.converted,
    retained: target.retained ?? null,
    outcome_revenue: target.outcome_revenue === null ? "" : String(target.outcome_revenue),
  };
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function campaignActionErrorMessage(error: unknown, fallback: string): string {
  const message = errorMessage(error, fallback);
  const translations: Record<string, string> = {
    "A campaign cannot be active after end_at.": "진행 중 캠페인의 종료일은 현재 시각 이후여야 합니다. 종료일을 미래로 변경한 후 다시 저장해 주세요.",
    "A campaign cannot be active before start_at.": "캠페인의 시작일이 아직 지나지 않았습니다. 시작일을 현재 시각 이전으로 변경한 후 다시 저장해 주세요.",
    "A scheduled campaign requires start_at.": "예약 캠페인은 시작일을 입력해야 합니다.",
    "A scheduled campaign must start in the future.": "예약 캠페인의 시작일은 현재 시각 이후여야 합니다.",
    "Campaign end_at must be after start_at.": "종료일은 시작일 이후로 설정해야 합니다.",
    "A closed campaign is immutable.": "완료되거나 취소된 캠페인은 변경할 수 없습니다.",
    "Experiment, segment, and financial policies are immutable after targets exist.": "대상이 등록된 캠페인은 A/B 설정, 세그먼트, 비용 정책을 변경할 수 없습니다.",
    "A campaign cannot be completed while treatment targets remain open.": "처리 중인 대상군이 남아 있어 캠페인을 완료할 수 없습니다.",
    "A campaign with the same name already exists.": "같은 이름의 캠페인이 이미 존재합니다. 다른 이름을 입력해 주세요.",
    "The campaign was not found.": "캠페인을 찾을 수 없습니다. 목록을 새로고침한 후 다시 시도해 주세요.",
  };
  if (translations[message] !== undefined) {
    return translations[message];
  }
  if (message.startsWith("Campaign status cannot change from")) {
    return "현재 캠페인 상태에서는 선택한 상태로 변경할 수 없습니다. 허용된 상태 전이를 확인해 주세요.";
  }
  return fallback;
}

function CampaignActionDialog({
  title,
  message,
  onClose,
  variant = "error",
}: {
  title: string;
  message: string;
  onClose: () => void;
  variant?: "error" | "success";
}) {
  useEffect(() => {
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
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
        className={`campaign-feedback-help-dialog campaign-conflict-dialog campaign-action-dialog campaign-action-dialog--${variant}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="campaign-action-dialog-title"
      >
        <div className="campaign-feedback-help-dialog__header">
          <div>
            <p className="card-kicker">{variant === "success" ? "CAMPAIGN UPDATE" : "CAMPAIGN GUARD"}</p>
            <h3 id="campaign-action-dialog-title">{title}</h3>
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

function CampaignStats({ campaign }: { campaign: Campaign }) {
  const stats = campaign.stats;
  return (
    <section className="campaign-management-stats" aria-label="캠페인 통계">
      <article className="campaign-stat campaign-stat--purple">
        <span>ALL TARGETS</span>
        <strong>{formatNumber(stats.total_targets)}</strong>
        <small>전체 대상 고객</small>
      </article>
      <article className="campaign-stat campaign-stat--orange">
        <span>OPEN QUEUE</span>
        <strong>{formatNumber(stats.unprocessed_targets)}</strong>
        <small>미처리 대상</small>
      </article>
      <article className="campaign-stat campaign-stat--blue">
        <span>CONTACTED</span>
        <strong>{formatNumber(stats.contacted_targets)}</strong>
        <small>접촉 시작 대상</small>
      </article>
      <article className="campaign-stat campaign-stat--green">
        <span>CONVERTED</span>
        <strong>{formatNumber(stats.converted_targets)}</strong>
        <small>전환 완료 대상</small>
      </article>
    </section>
  );
}

function CampaignEditor({
  title,
  form,
  submitLabel,
  onChange,
  onSubmit,
  onCancel,
  isSubmitting,
  isCreate,
  statusOptions,
  lockPolicyFields = false,
}: {
  title: string;
  form: CampaignForm;
  submitLabel: string;
  onChange: (form: CampaignForm) => void;
  onSubmit: () => void;
  onCancel?: () => void;
  isSubmitting: boolean;
  isCreate?: boolean;
  statusOptions?: CampaignLifecycleStatus[];
  lockPolicyFields?: boolean;
}) {
  const availableStatuses = isCreate
    ? (["draft"] as CampaignLifecycleStatus[])
    : statusOptions ?? (Object.keys(lifecycleLabels) as CampaignLifecycleStatus[]);
  return (
    <form
      className={isCreate ? "campaign-editor" : "campaign-editor campaign-editor--edit"}
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <div className="campaign-editor__heading">
        <div>
          <p className="card-kicker">{isCreate ? "NEW CAMPAIGN" : "CAMPAIGN SETTINGS"}</p>
          <h3>{title}</h3>
        </div>
        {onCancel && (
          <button className="campaign-link-button" type="button" onClick={onCancel}>
            닫기
          </button>
        )}
      </div>
      {lockPolicyFields && (
        <p className="campaign-editor__notice">
          대상이 등록된 캠페인은 A/B 설정, 비용, 유지 관측 정책을 변경할 수 없습니다.
        </p>
      )}
      <label>
        <span>캠페인 이름</span>
        <input
          required
          maxLength={150}
          value={form.name}
          placeholder="예: 8월 고위험 고객 리텐션"
          onChange={(event) => onChange({ ...form, name: event.target.value })}
        />
      </label>
      <div className="campaign-editor__row">
        <label>
          <span>채널</span>
          <input
            value={form.channel}
            maxLength={80}
            placeholder="전화, 앱 푸시, 이메일"
            onChange={(event) => onChange({ ...form, channel: event.target.value })}
          />
        </label>
        <label>
          <span>상태</span>
          <select
            value={form.status}
            onChange={(event) => onChange({ ...form, status: event.target.value as CampaignLifecycleStatus })}
          >
            {availableStatuses.map((status) => (
              <option value={status} key={status}>{lifecycleLabels[status]}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="campaign-editor__row">
        <label>
          <span>시작 일시</span>
          <input
            type="datetime-local"
            inputMode="none"
            value={form.start_at}
            onKeyDown={preventDateTextEntry}
            onPaste={preventDatePaste}
            onChange={(event) => onChange({ ...form, start_at: event.target.value })}
          />
        </label>
        <label>
          <span>종료 일시</span>
          <input
            type="datetime-local"
            inputMode="none"
            value={form.end_at}
            onKeyDown={preventDateTextEntry}
            onPaste={preventDatePaste}
            onChange={(event) => onChange({ ...form, end_at: event.target.value })}
          />
        </label>
      </div>
      <div className="campaign-editor__row campaign-editor__row--metrics">
        <label className="campaign-editor__checkbox">
          <input
            type="checkbox"
            checked={form.experiment_enabled}
            disabled={lockPolicyFields}
            onChange={(event) => onChange({ ...form, experiment_enabled: event.target.checked })}
          />
          <span>A/B 테스트 활성화</span>
        </label>
        <label>
          <span>대조군 비율</span>
          <input
            type="number"
            min={0.01}
            max={0.99}
            step={0.01}
            disabled={!form.experiment_enabled || lockPolicyFields}
            value={form.control_group_ratio}
            onChange={(event) => onChange({ ...form, control_group_ratio: Number(event.target.value) })}
          />
        </label>
        <label>
          <span>유지 관측(일)</span>
          <input
            type="number"
            min={1}
            max={365}
            disabled={lockPolicyFields}
            value={form.retention_window_days}
            onChange={(event) => onChange({ ...form, retention_window_days: Number(event.target.value) })}
          />
        </label>
      </div>
      <div className="campaign-editor__row">
        <label>
          <span>고정 비용</span>
          <input type="number" min={0} step={1000} disabled={lockPolicyFields} value={form.fixed_cost} onChange={(event) => onChange({ ...form, fixed_cost: Number(event.target.value) })} />
        </label>
        <label>
          <span>접촉당 비용</span>
          <input type="number" min={0} step={100} disabled={lockPolicyFields} value={form.cost_per_contact} onChange={(event) => onChange({ ...form, cost_per_contact: Number(event.target.value) })} />
        </label>
        <label>
          <span>전환당 매출</span>
          <input type="number" min={0} step={1000} disabled={lockPolicyFields} value={form.revenue_per_conversion} onChange={(event) => onChange({ ...form, revenue_per_conversion: Number(event.target.value) })} />
        </label>
      </div>
      <label>
        <span>설명</span>
        <textarea
          maxLength={500}
          value={form.description}
          placeholder="캠페인 목적과 실행 기준을 입력하세요."
          onChange={(event) => onChange({ ...form, description: event.target.value })}
        />
      </label>
      <div className="campaign-editor__actions">
        {onCancel && (
          <button className="campaign-secondary-button" type="button" onClick={onCancel}>
            취소
          </button>
        )}
        <button className="campaign-primary-button" type="submit" disabled={isSubmitting || form.name.trim() === ""}>
          {isSubmitting ? "저장 중..." : submitLabel}
        </button>
      </div>
    </form>
  );
}

function TargetTable({
  data,
  drafts,
  assignees,
  canEditTargets,
  canViewPerformance,
  processingUser,
  onDraftChange,
  onSave,
  isSaving,
}: {
  data: CampaignTargetList;
  drafts: Record<number, TargetDraft>;
  assignees: TeamMember[];
  canEditTargets: boolean;
  canViewPerformance: boolean;
  processingUser: AuthUser;
  onDraftChange: (targetId: number, draft: TargetDraft) => void;
  onSave: (targetId: number) => void;
  isSaving: number | null;
}) {
  return (
    <div className="campaign-target-table-wrap">
      <div className="campaign-target-table" role="table" aria-label="캠페인 대상 목록">
        <div className="campaign-target-table__head" role="row">
          <span>고객</span>
          <span>처리 상태</span>
          <span>담당자</span>
          <span>결과</span>
          <span>전환</span>
          {canViewPerformance && <span>성과</span>}
          {canEditTargets && <span>작업</span>}
        </div>
        {data.items.map((target) => {
          const draft = drafts[target.id] ?? getTargetDraft(target);
          const canEditTarget = canEditTargets && (
            processingUser.role === "admin"
            || (
              target.experiment_group === "treatment"
              && (target.assigned_to_user_id === null || target.assigned_to_user_id === processingUser.id)
            )
          );
          const availableStatuses: CampaignStatus[] = target.experiment_group === "control"
            ? target.status === "pending" ? ["pending", "cancelled"] : [target.status]
            : targetStatusTransitions[target.status].filter((status) => (
              target.campaign_status === "active"
              || !["contacted", "completed"].includes(status)
            ));
          const finalCodeRequired = draft.status === "completed"
            && !["converted", "not_converted", "no_response", "declined", "opted_out", "invalid_contact"].includes(draft.result_code);
          return (
            <div className="campaign-target-table__row" role="row" key={target.id}>
              <div className="campaign-target-customer">
                <strong>고객 {target.customer_id}</strong>
                <small>대상 #{target.id} · {formatDate(target.updated_at)}</small>
                <span className={`campaign-experiment-badge campaign-experiment-badge--${target.experiment_group}`}>
                  {target.experiment_group === "control" ? "대조군" : "대상군"}
                </span>
              </div>
              {canEditTarget ? (
                <select
                  value={draft.status}
                  aria-label={`고객 ${target.customer_id} 처리 상태`}
                  onChange={(event) => onDraftChange(target.id, { ...draft, status: event.target.value as CampaignStatus })}
                >
                  {availableStatuses.map((status) => (
                    <option value={status} key={status}>{targetStatusLabels[status]}</option>
                  ))}
                </select>
              ) : (
                <span className={`campaign-status campaign-status--${target.status}`}>{targetStatusLabels[target.status]}</span>
              )}
              {canEditTarget ? (
                <select
                  value={draft.assigned_to_user_id ?? ""}
                  disabled={target.experiment_group === "control"}
                  aria-label={`고객 ${target.customer_id} 담당자`}
                  onChange={(event) => onDraftChange(target.id, {
                    ...draft,
                    assigned_to_user_id: event.target.value === "" ? null : Number(event.target.value),
                  })}
                >
                  <option value="">미배정</option>
                  {assignees.map((assignee) => (
                    <option value={assignee.id} key={assignee.id}>{assignee.display_name}</option>
                  ))}
                </select>
              ) : (
                <span className="campaign-target-muted">{target.assigned_to_display_name ?? "미배정"}</span>
              )}
              {canEditTarget ? (
                <div className="campaign-target-result">
                  <input
                    value={draft.result}
                    placeholder="처리 결과"
                    aria-label={`고객 ${target.customer_id} 처리 결과`}
                    onChange={(event) => onDraftChange(target.id, { ...draft, result: event.target.value })}
                  />
                  <select
                    value={draft.result_code}
                    aria-label={`고객 ${target.customer_id} 결과 코드`}
                    onChange={(event) => onDraftChange(target.id, {
                      ...draft,
                      result_code: event.target.value as CampaignResultCode | "",
                      converted: event.target.value === "converted",
                    })}
                  >
                    <option value="">코드 없음</option>
                    {Object.entries(resultCodeLabels)
                      .filter(([code]) => target.experiment_group !== "control" || code !== "contacted")
                      .map(([code, label]) => (
                      <option value={code} key={code}>{label}</option>
                      ))}
                  </select>
                </div>
              ) : (
                <span className="campaign-target-muted">{target.result ?? "결과 대기"}</span>
              )}
              {canEditTarget ? (
                <label className="campaign-conversion campaign-conversion--management">
                  <input
                    type="checkbox"
                    checked={draft.converted}
                    disabled={target.experiment_group === "treatment" && draft.status !== "completed"}
                    onChange={(event) => onDraftChange(target.id, {
                      ...draft,
                      converted: event.target.checked,
                      result_code: event.target.checked
                        ? "converted"
                        : draft.result_code === "converted" ? "not_converted" : draft.result_code,
                    })}
                  />
                  전환
                </label>
              ) : (
                <span className={target.converted ? "campaign-converted" : "campaign-target-muted"}>
                  {target.converted ? "전환" : "-"}
                </span>
              )}
              {canViewPerformance && (canEditTarget ? (
                <div className="campaign-target-retention">
                  <select
                    value={draft.retained === null ? "" : String(draft.retained)}
                    aria-label={`고객 ${target.customer_id} 유지 여부`}
                    onChange={(event) => onDraftChange(target.id, {
                      ...draft,
                      retained: event.target.value === "" ? null : event.target.value === "true",
                    })}
                  >
                    <option value="">미관측</option>
                    <option value="true">유지</option>
                    <option value="false">미유지</option>
                  </select>
                  <input
                    type="number"
                    min={0}
                    step={1000}
                    value={draft.outcome_revenue}
                    placeholder="매출"
                    aria-label={`고객 ${target.customer_id} 성과 매출`}
                    onChange={(event) => onDraftChange(target.id, { ...draft, outcome_revenue: event.target.value })}
                  />
                </div>
              ) : (
                <span className="campaign-target-muted campaign-target-performance-readonly">
                  <span>{target.retained === null ? "유지 미관측" : target.retained ? "유지" : "미유지"}</span>
                  {target.outcome_revenue != null && <small>매출 {formatNumber(Math.round(target.outcome_revenue))}</small>}
                </span>
              ))}
              {canEditTargets && (canEditTarget ? (
                <button
                  className="campaign-save-button"
                  type="button"
                  disabled={isSaving === target.id || finalCodeRequired}
                  onClick={() => onSave(target.id)}
                >
                  {isSaving === target.id ? "저장 중" : "저장"}
                </button>
              ) : <span className="campaign-target-muted">권한 없음</span>)}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EventTimeline({ data, page, onPageChange }: {
  data: CampaignEventList | null;
  page: number;
  onPageChange: (page: number) => void;
}) {
  if (data === null || data.items.length === 0) {
    return <p className="campaign-empty-copy">아직 기록된 이벤트가 없습니다.</p>;
  }
  return (
    <>
      <div className="campaign-event-list">
        {data.items.map((event) => (
          <article className="campaign-event" key={event.id}>
            <span className="campaign-event__dot" aria-hidden="true" />
            <div>
              <strong>{eventLabels[event.event_type] ?? event.event_type}</strong>
              <p>
                {event.campaign_target_id === null ? "캠페인 전체" : `대상 #${event.campaign_target_id}`}
                {event.from_status !== null || event.to_status !== null
                  ? ` · ${event.from_status ?? "신규"} → ${event.to_status ?? "-"}`
                  : ""}
              </p>
              <small>{event.actor_display_name ?? "시스템"} · {formatDate(event.created_at)}</small>
            </div>
          </article>
        ))}
      </div>
      <CampaignPagination
        label="이벤트"
        total={data.total}
        page={page}
        pageSize={data.page_size}
        totalPages={Math.max(Math.ceil(data.total / data.page_size), 1)}
        summarySuffix="개 이벤트"
        onPageChange={onPageChange}
      />
    </>
  );
}

export function CampaignManagementPage({ user }: CampaignManagementPageProps) {
  const canManageCampaigns = user.role === "admin" || user.role === "marketing";
  const canEditCampaignTargets = user.role === "admin";
  const canViewTargetPerformance = user.role === "admin" || user.role === "operations";
  const [campaignData, setCampaignData] = useState<CampaignList | null>(null);
  const [campaignStatusFilter, setCampaignStatusFilter] = useState<CampaignLifecycleStatus | "">("");
  const [campaignNameFilter, setCampaignNameFilter] = useState("");
  const [campaignPage, setCampaignPage] = useState(1);
  const [campaignRefreshKey, setCampaignRefreshKey] = useState(0);
  const [selectedCampaignId, setSelectedCampaignId] = useState<number | null>(null);
  const [campaignLoading, setCampaignLoading] = useState(true);
  const [campaignError, setCampaignError] = useState("");
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingCampaign, setEditingCampaign] = useState(false);
  const [createForm, setCreateForm] = useState<CampaignForm>(emptyCampaignForm);
  const [editForm, setEditForm] = useState<CampaignForm>(emptyCampaignForm);
  const [isSavingCampaign, setIsSavingCampaign] = useState(false);
  const [campaignMessage, setCampaignMessage] = useState("");
  const [campaignActionError, setCampaignActionError] = useState("");
  const [campaignActionSuccess, setCampaignActionSuccess] = useState("");
  const [targetData, setTargetData] = useState<CampaignTargetList | null>(null);
  const [targetStatusFilter, setTargetStatusFilter] = useState<CampaignStatus | "">("");
  const [targetAssigneeFilter, setTargetAssigneeFilter] = useState("");
  const [targetCustomerFilter, setTargetCustomerFilter] = useState("");
  const [targetConvertedFilter, setTargetConvertedFilter] = useState<"" | "true" | "false">("");
  const [targetPage, setTargetPage] = useState(1);
  const [targetLoading, setTargetLoading] = useState(false);
  const [targetError, setTargetError] = useState("");
  const [targetDrafts, setTargetDrafts] = useState<Record<number, TargetDraft>>({});
  const [savingTargetId, setSavingTargetId] = useState<number | null>(null);
  const [targetRefreshKey, setTargetRefreshKey] = useState(0);
  const [eventData, setEventData] = useState<CampaignEventList | null>(null);
  const [eventPage, setEventPage] = useState(1);
  const [eventLoading, setEventLoading] = useState(false);
  const [eventError, setEventError] = useState("");
  const [assignees, setAssignees] = useState<TeamMember[]>([]);
  const [detailTab, setDetailTab] = useState<DetailTabKey>("overview");

  const selectedCampaign = useMemo(
    () => campaignData?.items.find((campaign) => campaign.id === selectedCampaignId) ?? null,
    [campaignData, selectedCampaignId],
  );

  const selectCampaign = (campaign: Campaign) => {
    setSelectedCampaignId(campaign.id);
    setEditForm(campaignToForm(campaign));
    setEditingCampaign(false);
    setTargetPage(1);
    setEventPage(1);
    setTargetStatusFilter("");
    setTargetAssigneeFilter("");
    setTargetCustomerFilter("");
    setTargetConvertedFilter("");
    setTargetDrafts({});
    setCampaignMessage("");
    setCampaignActionError("");
    setCampaignActionSuccess("");
    setDetailTab("overview");
  };

  useEffect(() => {
    let isActive = true;
    const loadCampaigns = async () => {
      setCampaignLoading(true);
      try {
        const response = await listCampaigns({
          status: campaignStatusFilter || undefined,
          name: campaignNameFilter.trim() || undefined,
          page: campaignPage,
          page_size: PAGE_SIZE,
        });
        if (!isActive) {
          return;
        }
        setCampaignData(response);
        setCampaignError("");
        const nextCampaign = response.items.find((campaign) => campaign.id === selectedCampaignId)
          ?? response.items[0];
        if (nextCampaign !== undefined && nextCampaign.id !== selectedCampaignId) {
          selectCampaign(nextCampaign);
        } else if (nextCampaign === undefined) {
          setSelectedCampaignId(null);
        }
      } catch (error: unknown) {
        if (isActive) {
          setCampaignError(errorMessage(error, "캠페인 목록을 불러오지 못했습니다."));
        }
      } finally {
        if (isActive) {
          setCampaignLoading(false);
        }
      }
    };
    void loadCampaigns();
    return () => {
      isActive = false;
    };
  }, [campaignNameFilter, campaignPage, campaignRefreshKey, campaignStatusFilter, selectedCampaignId]);

  useEffect(() => {
    if (selectedCampaignId === null) {
      return;
    }
    let isActive = true;
    const loadTargets = async () => {
      setTargetLoading(true);
      try {
        const response = await listCampaignTargetsByCampaign(selectedCampaignId, {
          status: targetStatusFilter || undefined,
          assigned_to_user_id: targetAssigneeFilter === "" ? undefined : Number(targetAssigneeFilter),
          customer_id: getCustomerId(targetCustomerFilter),
          converted: targetConvertedFilter === "" ? undefined : targetConvertedFilter === "true",
          page: targetPage,
          page_size: PAGE_SIZE,
        });
        if (isActive) {
          setTargetData(response);
          setTargetError("");
        }
      } catch (error: unknown) {
        if (isActive) {
          setTargetError(errorMessage(error, "캠페인 대상 목록을 불러오지 못했습니다."));
        }
      } finally {
        if (isActive) {
          setTargetLoading(false);
        }
      }
    };
    void loadTargets();
    return () => {
      isActive = false;
    };
  }, [selectedCampaignId, targetAssigneeFilter, targetConvertedFilter, targetCustomerFilter, targetPage, targetRefreshKey, targetStatusFilter]);

  useEffect(() => {
    if (selectedCampaignId === null) {
      return;
    }
    let isActive = true;
    const loadEvents = async () => {
      setEventLoading(true);
      try {
        const response = await listCampaignEvents(selectedCampaignId, { page: eventPage, page_size: PAGE_SIZE });
        if (isActive) {
          setEventData(response);
          setEventError("");
        }
      } catch (error: unknown) {
        if (isActive) {
          setEventError(errorMessage(error, "캠페인 이력을 불러오지 못했습니다."));
        }
      } finally {
        if (isActive) {
          setEventLoading(false);
        }
      }
    };
    void loadEvents();
    return () => {
      isActive = false;
    };
  }, [eventPage, selectedCampaignId, targetRefreshKey]);

  useEffect(() => {
    let isActive = true;
    void listTeamMembers()
      .then((members) => {
        if (isActive) {
          const operationsMembers = members.filter((member) => member.role === "operations" && member.is_active);
          setAssignees(
            user.role === "operations"
              ? operationsMembers.filter((member) => member.id === user.id)
              : operationsMembers,
          );
        }
      })
      .catch(() => {
        if (isActive) {
          setAssignees([]);
        }
      });
    return () => {
      isActive = false;
    };
  }, [user.id, user.role]);

  const updateCampaignInList = (updated: Campaign) => {
    setCampaignData((current) => current === null ? current : {
      ...current,
      items: current.items.map((campaign) => campaign.id === updated.id ? updated : campaign),
    });
  };

  const handleCreateCampaign = async () => {
    setIsSavingCampaign(true);
    setCampaignMessage("");
    setCampaignActionError("");
    setCampaignActionSuccess("");
    try {
      const created = await createCampaign({
        name: createForm.name.trim(),
        description: createForm.description.trim() || null,
        channel: createForm.channel.trim() || null,
        status: createForm.status,
        start_at: toIsoDate(createForm.start_at),
        end_at: toIsoDate(createForm.end_at),
        experiment_enabled: createForm.experiment_enabled,
        control_group_ratio: createForm.experiment_enabled ? createForm.control_group_ratio : 0,
        fixed_cost: createForm.fixed_cost,
        cost_per_contact: createForm.cost_per_contact,
        revenue_per_conversion: createForm.revenue_per_conversion,
        retention_window_days: createForm.retention_window_days,
      });
      setCampaignData((current) => current === null ? {
        items: [created],
        page: 1,
        page_size: PAGE_SIZE,
        total: 1,
        total_pages: 1,
      } : {
        ...current,
        items: [created, ...current.items.filter((campaign) => campaign.id !== created.id)].slice(0, PAGE_SIZE),
        total: current.total + 1,
      });
      selectCampaign(created);
      setCampaignPage(1);
      setCampaignStatusFilter("");
      setCampaignNameFilter("");
      setCreateForm(emptyCampaignForm());
      setShowCreateForm(false);
      setCampaignActionSuccess("캠페인을 생성했습니다.");
    } catch (error: unknown) {
      setCampaignActionError(campaignActionErrorMessage(error, "캠페인 생성에 실패했습니다. 입력값을 확인한 후 다시 시도해 주세요."));
    } finally {
      setIsSavingCampaign(false);
    }
  };

  const handleUpdateCampaign = async () => {
    if (selectedCampaign === null) {
      return;
    }
    setIsSavingCampaign(true);
    setCampaignMessage("");
    setCampaignActionError("");
    setCampaignActionSuccess("");
    try {
      const updated = await updateCampaign(selectedCampaign.id, {
        name: editForm.name.trim(),
        description: editForm.description.trim() || null,
        channel: editForm.channel.trim() || null,
        status: editForm.status,
        start_at: toIsoDate(editForm.start_at),
        end_at: toIsoDate(editForm.end_at),
        experiment_enabled: editForm.experiment_enabled,
        control_group_ratio: editForm.experiment_enabled ? editForm.control_group_ratio : 0,
        fixed_cost: editForm.fixed_cost,
        cost_per_contact: editForm.cost_per_contact,
        revenue_per_conversion: editForm.revenue_per_conversion,
        retention_window_days: editForm.retention_window_days,
      });
      updateCampaignInList(updated);
      setEditingCampaign(false);
      setCampaignActionSuccess("캠페인 정보를 저장했습니다.");
      setTargetRefreshKey((current) => current + 1);
    } catch (error: unknown) {
      setCampaignActionError(campaignActionErrorMessage(error, "캠페인 정보 저장에 실패했습니다. 입력값과 캠페인 상태를 확인한 후 다시 시도해 주세요."));
    } finally {
      setIsSavingCampaign(false);
    }
  };

  const handleTargetDraftChange = (targetId: number, draft: TargetDraft) => {
    setTargetDrafts((current) => ({ ...current, [targetId]: draft }));
  };

  const handleSaveTarget = async (targetId: number) => {
    const target = targetData?.items.find((item) => item.id === targetId);
    if (target === undefined) {
      return;
    }
    const draft = targetDrafts[targetId] ?? getTargetDraft(target);
    setSavingTargetId(targetId);
    setTargetError("");
    try {
      await updateCampaignTarget(targetId, {
        status: draft.status,
        assigned_to_user_id: draft.assigned_to_user_id,
        result: draft.result.trim() || null,
        result_code: draft.result_code || null,
        converted: draft.converted,
        retained: draft.retained,
        outcome_revenue: draft.outcome_revenue === "" ? null : Number(draft.outcome_revenue),
      });
      setTargetDrafts((current) => {
        const next = { ...current };
        delete next[targetId];
        return next;
      });
      setTargetRefreshKey((current) => current + 1);
      setCampaignMessage("캠페인 대상 처리를 저장했습니다.");
    } catch (error: unknown) {
      setTargetError(errorMessage(error, "캠페인 대상 처리 저장에 실패했습니다."));
    } finally {
      setSavingTargetId(null);
    }
  };

  const targetStats = targetData?.stats ?? selectedCampaign?.stats;

  return (
    <>
      {campaignError !== "" && <p className="campaign-management-error" role="alert">{campaignError}</p>}
      {campaignActionError !== "" && (
        <CampaignActionDialog
          title="캠페인 변경 불가"
          message={campaignActionError}
          onClose={() => setCampaignActionError("")}
        />
      )}
      {campaignActionSuccess !== "" && (
        <CampaignActionDialog
          title="저장 완료"
          message={campaignActionSuccess}
          variant="success"
          onClose={() => setCampaignActionSuccess("")}
        />
      )}

      {canManageCampaigns && (
        <BulkTargetingPanel
          assignees={assignees}
          onExecuted={() => setCampaignRefreshKey((current) => current + 1)}
        />
      )}

      <section className="campaign-management-grid">
        <aside className="campaign-catalog-panel">
                      <div className="campaign-panel-heading">
            <div>
              <p className="card-kicker">CAMPAIGN CATALOG</p>
              <h2>캠페인 목록</h2>
            </div>
            {canManageCampaigns && (
              <button className="campaign-primary-button campaign-primary-button--small" type="button" onClick={() => setShowCreateForm((current) => !current)}>
                {showCreateForm ? "목록 보기" : "+ 새 캠페인"}
              </button>
            )}
          </div>

          {showCreateForm && canManageCampaigns ? (
            <CampaignEditor
              title="새 캠페인 만들기"
              form={createForm}
              submitLabel="캠페인 생성"
              onChange={setCreateForm}
              onSubmit={() => void handleCreateCampaign()}
              onCancel={() => setShowCreateForm(false)}
              isSubmitting={isSavingCampaign}
              isCreate
            />
          ) : (
            <>
              <div className="campaign-catalog-filters">
                <input
                  type="search"
                  value={campaignNameFilter}
                  placeholder="캠페인 이름 검색"
                  aria-label="캠페인 이름 검색"
                  onChange={(event) => {
                    setCampaignNameFilter(event.target.value);
                    setCampaignPage(1);
                  }}
                />
                <select
                  value={campaignStatusFilter}
                  aria-label="캠페인 상태 필터"
                  onChange={(event) => {
                    setCampaignStatusFilter(event.target.value as CampaignLifecycleStatus | "");
                    setCampaignPage(1);
                  }}
                >
                  <option value="">전체 상태</option>
                  {Object.entries(lifecycleLabels).map(([status, label]) => (
                    <option value={status} key={status}>{label}</option>
                  ))}
                </select>
              </div>
              {campaignLoading ? (
                <p className="campaign-empty-copy">캠페인 목록을 불러오는 중입니다.</p>
              ) : campaignData === null || campaignData.items.length === 0 ? (
                <p className="campaign-empty-copy">등록된 캠페인이 없습니다.</p>
              ) : (
                <div className="campaign-catalog-list">
                  {campaignData.items.map((campaign) => (
                    <button
                      className={campaign.id === selectedCampaignId ? "campaign-catalog-item campaign-catalog-item--active" : "campaign-catalog-item"}
                      type="button"
                      key={campaign.id}
                      onClick={() => selectCampaign(campaign)}
                    >
                      <div>
                        <strong>{campaign.name}</strong>
                        <small>{campaign.channel ?? "채널 미지정"} · {formatDate(campaign.created_at)}</small>
                      </div>
                      <span className={`campaign-lifecycle campaign-lifecycle--${campaign.status}`}>{lifecycleLabels[campaign.status]}</span>
                      <div className="campaign-catalog-item__stats">
                        <span>대상 {formatNumber(campaign.stats.total_targets)}</span>
                        <span>전환 {formatNumber(campaign.stats.converted_targets)}</span>
                      </div>
                    </button>
                  ))}
                </div>
              )}
              {campaignData !== null && campaignData.total > 0 && (
                <div className="campaign-inline-pagination">
                  <span>{formatNumber(campaignData.total)}개 캠페인</span>
                  <div>
                    <button type="button" disabled={campaignPage <= 1} onClick={() => setCampaignPage((current) => current - 1)} aria-label="이전 캠페인 페이지">←</button>
                    <strong>{campaignPage}</strong>
                    <button type="button" disabled={campaignPage >= Math.max(campaignData.total_pages, 1)} onClick={() => setCampaignPage((current) => current + 1)} aria-label="다음 캠페인 페이지">→</button>
                  </div>
                </div>
              )}
            </>
          )}
        </aside>

        <section className="campaign-detail-panel">
          {selectedCampaign === null ? (
            <div className="campaign-detail-empty">
              <span aria-hidden="true">◌</span>
              <h2>캠페인을 선택하세요</h2>
              <p>왼쪽 목록에서 캠페인을 선택하면 대상과 처리 이력을 확인할 수 있습니다.</p>
            </div>
          ) : (
            <>
              <div className="campaign-detail-heading">
                <div>
                  <p className="card-kicker">CAMPAIGN DETAIL · #{selectedCampaign.id}</p>
                  <h2>{selectedCampaign.name}</h2>
                  <p>{selectedCampaign.description ?? "등록된 캠페인 설명이 없습니다."}</p>
                </div>
                <div className="campaign-detail-heading__actions">
                  <span className={`campaign-lifecycle campaign-lifecycle--${selectedCampaign.status}`}>{lifecycleLabels[selectedCampaign.status]}</span>
                  {canManageCampaigns && (
                    <button className="campaign-secondary-button" type="button" onClick={() => setEditingCampaign((current) => !current)}>
                      {editingCampaign ? "상세 보기" : "캠페인 편집"}
                    </button>
                  )}
                </div>
              </div>

              {editingCampaign && canManageCampaigns ? (
                <CampaignEditor
                  title="캠페인 정보 수정"
                  form={editForm}
                  submitLabel="변경사항 저장"
                  onChange={setEditForm}
                  onSubmit={() => void handleUpdateCampaign()}
                  onCancel={() => setEditingCampaign(false)}
                  isSubmitting={isSavingCampaign}
                  lockPolicyFields={selectedCampaign.stats.total_targets > 0}
                  statusOptions={lifecycleTransitions[selectedCampaign.status]}
                />
              ) : (
                <>
                  <div className="campaign-detail-tabs" role="tablist" aria-label="캠페인 상세 보기 선택">
                    {DETAIL_TABS.filter((tab) => tab.key !== "candidates" || canManageCampaigns).map((tab) => (
                      <button
                        key={tab.key}
                        type="button"
                        role="tab"
                        aria-selected={detailTab === tab.key}
                        className={`campaign-detail-tab${detailTab === tab.key ? " is-active" : ""}`}
                        onClick={() => setDetailTab(tab.key)}
                      >
                        {tab.label}
                      </button>
                    ))}
                  </div>

                  {detailTab === "overview" && (
                    <>
                      {targetStats && <CampaignStats campaign={{ ...selectedCampaign, stats: targetStats }} />}
                      <CampaignPerformancePanel campaignId={selectedCampaign.id} refreshKey={targetRefreshKey} />
                    </>
                  )}

                  {detailTab === "candidates" && canManageCampaigns && (
                    <CampaignCandidatePanel
                      selectedCampaign={selectedCampaign}
                      canManage={canManageCampaigns}
                      onRegistered={() => {
                        setTargetRefreshKey((current) => current + 1);
                        setCampaignRefreshKey((current) => current + 1);
                      }}
                    />
                  )}

                  {detailTab === "targets" && (
                    <>
                      {campaignMessage !== "" && <p className="campaign-management-message" role="status">{campaignMessage}</p>}
                      <section className="campaign-target-panel">
                        <div className="campaign-panel-heading">
                          <div>
                            <p className="card-kicker">TARGET OPERATIONS</p>
                            <h3>캠페인 대상</h3>
                          </div>
                          <span className="campaign-panel-count">{formatNumber(targetData?.total ?? 0)}명</span>
                        </div>
                        {user.role === "operations" && (
                          <p className="campaign-target-panel__notice">
                            운영팀의 대상 처리와 성과 입력은 WORK QUEUE에서 진행합니다. 이 화면에서는 캠페인별 현황과 이력을 조회할 수 있습니다.
                          </p>
                        )}
                        <div className="campaign-target-filters">
                          <select
                            value={targetStatusFilter}
                            aria-label="대상 처리 상태 필터"
                            onChange={(event) => {
                              setTargetStatusFilter(event.target.value as CampaignStatus | "");
                              setTargetPage(1);
                            }}
                          >
                            <option value="">전체 처리 상태</option>
                            {Object.entries(targetStatusLabels).map(([status, label]) => (
                              <option value={status} key={status}>{label}</option>
                            ))}
                          </select>
                          <select
                            value={targetAssigneeFilter}
                            aria-label="대상 담당자 필터"
                            onChange={(event) => {
                              setTargetAssigneeFilter(event.target.value);
                              setTargetPage(1);
                            }}
                          >
                            <option value="">전체 담당자</option>
                            {assignees.map((assignee) => (
                              <option value={assignee.id} key={assignee.id}>{assignee.display_name}</option>
                            ))}
                          </select>
                          <input
                            type="search"
                            inputMode="numeric"
                            value={targetCustomerFilter}
                            placeholder="고객 ID"
                            aria-label="대상 고객 ID 필터"
                            onChange={(event) => {
                              setTargetCustomerFilter(event.target.value);
                              setTargetPage(1);
                            }}
                          />
                          <select
                            value={targetConvertedFilter}
                            aria-label="전환 여부 필터"
                            onChange={(event) => {
                              setTargetConvertedFilter(event.target.value as "" | "true" | "false");
                              setTargetPage(1);
                            }}
                          >
                            <option value="">전환 전체</option>
                            <option value="true">전환 완료</option>
                            <option value="false">미전환</option>
                          </select>
                        </div>
                        {targetError !== "" && <p className="campaign-management-error" role="alert">{targetError}</p>}
                        {targetLoading ? (
                          <p className="campaign-empty-copy">캠페인 대상을 불러오는 중입니다.</p>
                        ) : targetData === null || targetData.items.length === 0 ? (
                          <p className="campaign-empty-copy">조건에 맞는 캠페인 대상이 없습니다.</p>
                        ) : (
                          <TargetTable
                            data={targetData}
                            drafts={targetDrafts}
                            assignees={assignees}
                            canEditTargets={canEditCampaignTargets}
                            canViewPerformance={canViewTargetPerformance}
                            processingUser={user}
                            onDraftChange={handleTargetDraftChange}
                            onSave={(targetId) => void handleSaveTarget(targetId)}
                            isSaving={savingTargetId}
                          />
                        )}
                        {targetData !== null && targetData.total > 0 && (
                          <CampaignPagination
                            label="대상"
                            total={targetData.total}
                            page={targetPage}
                            pageSize={targetData.page_size}
                            totalPages={Math.max(targetData.total_pages, 1)}
                            summarySuffix="명"
                            onPageChange={setTargetPage}
                          />
                        )}
                      </section>
                    </>
                  )}

                  {detailTab === "events" && (
                    <section className="campaign-event-panel">
                      <div className="campaign-panel-heading">
                        <div>
                          <p className="card-kicker">AUDIT TRAIL</p>
                          <h3>캠페인 이벤트 이력</h3>
                        </div>
                        {eventLoading && <span className="campaign-loading-label">불러오는 중...</span>}
                      </div>
                      {eventError !== "" && <p className="campaign-management-error" role="alert">{eventError}</p>}
                      <EventTimeline data={eventData} page={eventPage} onPageChange={setEventPage} />
                    </section>
                  )}
                </>
              )}
            </>
          )}
        </section>
      </section>
    </>
  );
}
