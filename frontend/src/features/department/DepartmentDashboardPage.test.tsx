import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DepartmentDashboardPage } from "./DepartmentDashboardPage";

const operationsUser = {
  id: 2,
  username: "operations_team",
  display_name: "운영팀",
  role: "operations" as const,
  is_active: true,
  created_at: "2026-08-01T00:00:00Z",
};

const adminUser = {
  id: 1,
  username: "admin_team",
  display_name: "관리자",
  role: "admin" as const,
  is_active: true,
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

const insight = {
  id: 10,
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

const campaignTarget = {
  id: 10,
  customer_id: 1001,
  customer_insight_id: 10,
  campaign_id: 1,
  campaign_name: "고위험 고객 리텐션",
  experiment_group: "treatment",
  campaign_status: "active",
  assigned_to_user_id: 2,
  assigned_to_display_name: "운영팀",
  status: "completed",
  processed_at: "2026-08-01T01:00:00Z",
  contacted_at: "2026-08-01T00:30:00Z",
  completed_at: "2026-08-01T01:00:00Z",
  converted_at: null,
  result: "상담 완료",
  result_notes: null,
  result_code: "not_converted",
  converted: false,
  retained: null,
  retention_checked_at: null,
  outcome_revenue: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T01:00:00Z",
};

function successResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body };
}

function departmentFetchMock(
  isAdmin = false,
  conflictOnCreate = false,
  includeTarget = false,
  insightTotal = 42,
  insightTotalPages = 6,
  targetStatus: "completed" | "contacted" = "completed",
  retentionError = false,
) {
  return vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const query = new URL(path, "http://localhost").searchParams;
    const insightPage = Number(query.get("page") ?? "1");
    if (path.includes("/campaign-targets")) {
      if (conflictOnCreate && init?.method === "POST") {
        return Promise.resolve({
          ok: false,
          status: 409,
          json: async () => ({ detail: "The customer already has an equal-or-higher priority active campaign." }),
        });
      }
      if (init?.method === "PATCH") {
        if (retentionError) {
          return Promise.resolve({
            ok: false,
            status: 422,
            json: async () => ({ detail: "Retention cannot be recorded before retention_window_days has elapsed." }),
          });
        }
        return Promise.resolve(successResponse({
          ...campaignTarget,
          result_code: "converted",
          converted: true,
          retained: true,
          outcome_revenue: 15000,
        }));
      }
      return Promise.resolve(successResponse({
        items: includeTarget ? [{ ...campaignTarget, status: targetStatus, retained: targetStatus === "contacted" ? false : campaignTarget.retained }] : [],
        page: 1,
        page_size: 8,
        total: includeTarget ? 1 : 0,
        total_pages: includeTarget ? 1 : 0,
        stats: {
          total_targets: includeTarget ? 1 : 0,
          unprocessed_targets: 0,
          contacted_targets: includeTarget ? 1 : 0,
          converted_targets: 0,
          status_counts: { pending: 0, assigned: 0, contacted: 0, completed: includeTarget ? 1 : 0, cancelled: 0 },
        },
      }));
    }
    if (path.includes("/model-runs/latest")) {
      return Promise.resolve(successResponse({
        status: "succeeded",
        started_at: "2026-08-01T00:00:00Z",
        completed_at: "2026-08-01T00:05:00Z",
        processed_rows: 1,
        dataset_sha256: null,
        runs: [],
      }));
    }
    if (path.includes("/auth/users")) {
      return Promise.resolve(successResponse(isAdmin ? [adminUser, operationsUser] : []));
    }
    return Promise.resolve(successResponse({
      items: [insight],
      page: insightPage,
      page_size: 8,
        total: insightTotal,
        total_pages: insightTotalPages,
        stats: {
          total: insightTotal,
          average_churn_probability: 0.82,
          risk_counts: { high: insightTotal, medium: 0, low: 0 },
        cluster_counts: { "활성 저하군": 1 },
        cluster_options: { "활성 저하군": 1 },
      },
    }));
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("부서별 대시보드", () => {
  it("운영팀은 고위험 고객 처리 화면을 봅니다", async () => {
    const fetchMock = departmentFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<DepartmentDashboardPage user={operationsUser} />);

    expect(await screen.findByRole("heading", { name: "우선 관리 고객" })).toBeInTheDocument();
    expect(screen.getByText("42명")).toBeInTheDocument();
    expect(screen.getByText("1–8 / 42명")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "리텐션 등록" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "캠페인 처리 현황" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "우선 고객 2페이지" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("page=2"))).toBe(true));
    expect(await screen.findByText("9–16 / 42명")).toBeInTheDocument();
  });

  it("우선 관리 고객은 10개 페이지 단위로 페이지 번호를 이동합니다", async () => {
    const fetchMock = departmentFetchMock(false, false, false, 168, 21);
    vi.stubGlobal("fetch", fetchMock);

    render(<DepartmentDashboardPage user={operationsUser} />);

    expect(await screen.findByRole("heading", { name: "우선 관리 고객" })).toBeInTheDocument();
    for (let page = 1; page <= 10; page += 1) {
      expect(screen.getByRole("button", { name: `우선 고객 ${page}페이지` })).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "우선 고객 11페이지" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "다음 우선 고객 페이지 묶음" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("page=11"))).toBe(true));
    expect(screen.getByRole("button", { name: "우선 고객 11페이지" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "우선 고객 20페이지" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "우선 고객 1페이지" })).not.toBeInTheDocument();
  });

  it("마케팅팀은 전체 우선 고객을 서버 페이지 단위로 확인합니다", async () => {
    const fetchMock = departmentFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<DepartmentDashboardPage user={marketingUser} />);

    expect(await screen.findByRole("heading", { name: "캠페인 후보 고객" })).toBeInTheDocument();
    expect(screen.getByText("42명")).toBeInTheDocument();
    expect(screen.getByText("1–8 / 42명")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("campaign_candidates_only=true"))).toBe(true));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/campaign-targets?page=1&page_size=8"))).toBe(true));

    fireEvent.change(screen.getByRole("combobox", { name: "캠페인 후보 위험도" }), {
      target: { value: "high" },
    });
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("risk_level=high"))).toBe(true));

    fireEvent.change(screen.getByRole("combobox", { name: "캠페인 후보 정렬 기준" }), {
      target: { value: "expected_transaction_count" },
    });
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("sort_by=expected_transaction_count"))).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: "우선 고객 2페이지" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes("page=2"))).toBe(true));
    expect(await screen.findByText("9–16 / 42명")).toBeInTheDocument();
  });

  it("운영팀은 WORK QUEUE에서 전환·유지·매출 성과를 저장합니다", async () => {
    const fetchMock = departmentFetchMock(false, false, true);
    vi.stubGlobal("fetch", fetchMock);

    render(<DepartmentDashboardPage user={operationsUser} />);

    expect(await screen.findByRole("heading", { name: "캠페인 처리 현황" })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "전환" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "1001 결과 코드" }), {
      target: { value: "converted" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "1001 유지 여부" }), {
      target: { value: "true" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: "1001 성과 매출" }), {
      target: { value: "15000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => (
      String(input).includes("/campaign-targets/10") && init?.method === "PATCH"
    ))).toBe(true));
    expect(await screen.findByRole("heading", { name: "저장 완료" })).toBeInTheDocument();
    const patchCall = fetchMock.mock.calls.find(([input, init]) => (
      String(input).includes("/campaign-targets/10") && init?.method === "PATCH"
    ));
    expect(JSON.parse(String(patchCall?.[1]?.body))).toMatchObject({
      converted: true,
      retained: true,
      outcome_revenue: 15000,
    });
  });

  it("처리 완료만 저장할 때 유지율을 다시 전송하지 않습니다", async () => {
    const fetchMock = departmentFetchMock(false, false, true, 42, 6, "contacted");
    vi.stubGlobal("fetch", fetchMock);

    render(<DepartmentDashboardPage user={operationsUser} />);

    expect(await screen.findByRole("heading", { name: "캠페인 처리 현황" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "1001 처리 상태" }), {
      target: { value: "completed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => (
      String(input).includes("/campaign-targets/10") && init?.method === "PATCH"
    ))).toBe(true));
    const patchCall = fetchMock.mock.calls.find(([input, init]) => (
      String(input).includes("/campaign-targets/10") && init?.method === "PATCH"
    ));
    expect(JSON.parse(String(patchCall?.[1]?.body))).not.toHaveProperty("retained");
  });

  it("유지 관측기간 오류를 한국어 다이얼로그로 표시합니다", async () => {
    vi.stubGlobal("fetch", departmentFetchMock(false, false, true, 42, 6, "completed", true));

    render(<DepartmentDashboardPage user={operationsUser} />);

    expect(await screen.findByRole("heading", { name: "캠페인 처리 현황" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "1001 결과 코드" }), {
      target: { value: "converted" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "1001 유지 여부" }), {
      target: { value: "true" },
    });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "캠페인 처리 저장 안내" })).toBeInTheDocument();
    expect(screen.getByText(/유지 결과는 유지 관측 기간\(기본 30일\)이 지난 후에 입력할 수 있습니다/)).toBeInTheDocument();
    expect(screen.queryByText("Retention cannot be recorded before retention_window_days has elapsed.")).not.toBeInTheDocument();
  });

  it("마케팅팀 대시보드는 후보 확인용으로만 제공하고 개별 등록은 캠페인 센터에서 처리합니다", async () => {
    const fetchMock = departmentFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<DepartmentDashboardPage user={marketingUser} />);

    expect(await screen.findByRole("heading", { name: "캠페인 후보 고객" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "캠페인 등록" })).not.toBeInTheDocument();
  });

  it("관리자는 팀 계정과 권한 현황을 봅니다", async () => {
    vi.stubGlobal("fetch", departmentFetchMock(true));

    render(<DepartmentDashboardPage user={adminUser} />);

    expect(await screen.findByRole("heading", { name: "활성 팀 계정" })).toBeInTheDocument();
    expect(screen.getByText("운영팀")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "관리자 역할" })).toBeDisabled();
    expect(screen.queryByText("역할별 업무 권한")).not.toBeInTheDocument();
  });
});
