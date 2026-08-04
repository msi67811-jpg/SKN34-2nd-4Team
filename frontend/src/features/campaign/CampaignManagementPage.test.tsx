import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CampaignManagementPage } from "./CampaignManagementPage";

const operationsUser = {
  id: 2,
  username: "operations_team",
  display_name: "운영팀",
  role: "operations" as const,
  created_at: "2026-08-01T00:00:00Z",
};

const marketingUser = {
  id: 4,
  username: "marketing_team",
  display_name: "마케팅팀",
  role: "marketing" as const,
  is_active: true,
  created_at: "2026-08-01T00:00:00Z",
};

function successResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body };
}

const campaign = {
  id: 1,
  name: "8월 고위험 고객 리텐션",
  description: "고위험 고객의 재활성화를 위한 캠페인입니다.",
  channel: "전화",
  status: "active",
  start_at: "2026-08-01T00:00:00Z",
  end_at: null,
  created_by_user_id: 2,
  created_by_display_name: "운영팀",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  stats: {
    total_targets: 1,
    unprocessed_targets: 0,
    contacted_targets: 1,
    converted_targets: 1,
  },
};

const target = {
  id: 10,
  customer_id: 1001,
  customer_insight_id: 20,
  campaign_id: 1,
  campaign_name: "8월 고위험 고객 리텐션",
  campaign_status: "active",
  assigned_to_user_id: 2,
  assigned_to_display_name: "운영팀",
  status: "completed",
  processed_at: "2026-08-01T01:00:00Z",
  result: "상담 완료",
  result_notes: null,
  result_code: "converted",
  converted: true,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T01:00:00Z",
};

const insight = {
  id: 20,
  customer_id: 1001,
  classification_run_id: 1,
  regression_run_id: 2,
  clustering_run_id: 3,
  churn_probability: 0.82,
  risk_level: "high",
  expected_transaction_count: 12.5,
  activity_gap: -4.2,
  cluster_name: "활성 저하군",
  cluster_confidence: 0.91,
  recommended_action: "개인화 혜택을 제안하세요.",
  reason_codes: { inactivity: "높음" },
  scored_at: "2026-08-01T00:00:00Z",
};

function campaignFetchMock(conflictOnTarget = false, updateError = false, paginatedTotal = 1) {
  return vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const requestUrl = new URL(path, "http://localhost");
    const requestedPage = Number(requestUrl.searchParams.get("page") ?? "1");
    if (path.includes("/customer-insights")) {
      return Promise.resolve(successResponse({
        items: [insight],
        page: 1,
        page_size: 8,
        total: 1,
        total_pages: 1,
        stats: {
          total: 1,
          average_churn_probability: 0.82,
          risk_counts: { high: 1, medium: 0, low: 0 },
          cluster_counts: { "활성 저하군": 1 },
          cluster_options: { "활성 저하군": 1 },
        },
      }));
    }
    if (path.includes("/campaign-targets") && init?.method === "POST" && conflictOnTarget) {
      return Promise.resolve({
        ok: false,
        status: 409,
        json: async () => ({ detail: "The customer already has an equal-or-higher priority active campaign." }),
      });
    }
    if (path.includes("/campaigns/1") && init?.method === "PATCH" && updateError) {
      return Promise.resolve({
        ok: false,
        status: 422,
        json: async () => ({ detail: "A campaign cannot be active after end_at." }),
      });
    }
    if (path.includes("/campaigns/1") && init?.method === "PATCH") {
      return Promise.resolve(successResponse({ ...campaign, name: "수정된 캠페인 이름" }));
    }
    if (path.includes("/campaigns/1/targets")) {
      return Promise.resolve(successResponse({
        items: [target],
        page: requestedPage,
        page_size: 8,
        total: paginatedTotal,
        total_pages: Math.max(Math.ceil(paginatedTotal / 8), 1),
        stats: campaign.stats,
      }));
    }
    if (path.includes("/campaigns/1/events")) {
      return Promise.resolve(successResponse({
        items: [{
          id: 30,
          campaign_id: 1,
          campaign_target_id: 10,
          event_type: "status_changed",
          from_status: "contacted",
          to_status: "completed",
          actor_user_id: 2,
          actor_display_name: "운영팀",
          note: null,
          metadata_json: null,
          created_at: "2026-08-01T01:00:00Z",
        }],
        page: requestedPage,
        page_size: 8,
        total: paginatedTotal,
      }));
    }
    if (path.includes("/auth/users")) {
      return Promise.resolve(successResponse([operationsUser]));
    }
    return Promise.resolve(successResponse({
      items: [campaign],
      page: 1,
      page_size: 8,
      total: 1,
      total_pages: 1,
    }));
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("캠페인 관리 화면", () => {
  it("캠페인 통계·대상·이벤트 이력을 표시합니다", async () => {
    vi.stubGlobal("fetch", campaignFetchMock());

    render(<CampaignManagementPage user={operationsUser} />);

    expect(await screen.findAllByText("8월 고위험 고객 리텐션")).toHaveLength(2);

    fireEvent.click(await screen.findByRole("tab", { name: "캠페인 대상" }));
    expect(await screen.findByText("고객 1001")).toBeInTheDocument();
    expect(screen.getByText(/운영팀의 대상 처리와 성과 입력은 WORK QUEUE에서 진행합니다/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "저장" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "캠페인 이벤트 이력" }));
    expect(screen.getByRole("heading", { name: "캠페인 이벤트 이력" })).toBeInTheDocument();
    expect(screen.getByText("상태 변경")).toBeInTheDocument();
  });

  it("대상과 이벤트 이력은 10개 페이지 단위로 이동합니다", async () => {
    const fetchMock = campaignFetchMock(false, false, 168);
    vi.stubGlobal("fetch", fetchMock);

    render(<CampaignManagementPage user={operationsUser} />);

    fireEvent.click(await screen.findByRole("tab", { name: "캠페인 대상" }));
    expect(await screen.findByRole("button", { name: "대상 1페이지" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "대상 10페이지" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "대상 11페이지" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "다음 대상 페이지 묶음" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => (
      String(input).includes("/campaigns/1/targets") && String(input).includes("page=11")
    ))).toBe(true));
    expect(screen.getByRole("button", { name: "대상 11페이지" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "대상 20페이지" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "대상 1페이지" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "캠페인 이벤트 이력" }));
    expect(screen.getByRole("button", { name: "이벤트 1페이지" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "이벤트 10페이지" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "이벤트 11페이지" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "다음 이벤트 페이지 묶음" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => (
      String(input).includes("/campaigns/1/events") && String(input).includes("page=11")
    ))).toBe(true));
    expect(screen.getByRole("button", { name: "이벤트 11페이지" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "이벤트 20페이지" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "이벤트 1페이지" })).not.toBeInTheDocument();
  });

  it("마케팅팀은 캠페인 센터에서 후보를 선택해 대상 등록을 수행합니다", async () => {
    const fetchMock = campaignFetchMock(true);
    vi.stubGlobal("fetch", fetchMock);

    render(<CampaignManagementPage user={marketingUser} />);

    fireEvent.click(await screen.findByRole("tab", { name: "캠페인 후보 고객" }));
    expect(await screen.findByRole("heading", { name: "캠페인 후보 고객" })).toBeInTheDocument();
    expect(await screen.findByText("고객 1001")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("campaign_id=1"))).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: "대상 등록" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "캠페인 대상 등록 불가" })).toBeInTheDocument();
  });

  it("캠페인 저장 오류를 한국어 다이얼로그로 표시합니다", async () => {
    vi.stubGlobal("fetch", campaignFetchMock(false, true));

    render(<CampaignManagementPage user={marketingUser} />);

    fireEvent.click(await screen.findByRole("button", { name: "캠페인 편집" }));
    fireEvent.change(screen.getByDisplayValue("8월 고위험 고객 리텐션"), {
      target: { value: "수정된 캠페인 이름" },
    });
    fireEvent.click(screen.getByRole("button", { name: "변경사항 저장" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "캠페인 변경 불가" })).toBeInTheDocument();
    expect(screen.getByText("진행 중 캠페인의 종료일은 현재 시각 이후여야 합니다. 종료일을 미래로 변경한 후 다시 저장해 주세요.")).toBeInTheDocument();
    expect(screen.queryByText("A campaign cannot be active after end_at.")).not.toBeInTheDocument();
  });

  it("캠페인 저장 성공을 다이얼로그로 표시합니다", async () => {
    vi.stubGlobal("fetch", campaignFetchMock());

    render(<CampaignManagementPage user={marketingUser} />);

    fireEvent.click(await screen.findByRole("button", { name: "캠페인 편집" }));
    fireEvent.change(screen.getByDisplayValue("8월 고위험 고객 리텐션"), {
      target: { value: "수정된 캠페인 이름" },
    });
    fireEvent.click(screen.getByRole("button", { name: "변경사항 저장" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "저장 완료" })).toBeInTheDocument();
    expect(screen.getByText("캠페인 정보를 저장했습니다.")).toBeInTheDocument();
  });
});
