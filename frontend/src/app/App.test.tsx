import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const user = {
  id: 1,
  username: "analysis_team",
  display_name: "분석팀",
  role: "analyst" as const,
  created_at: "2026-08-01T00:00:00Z",
};

const marketingUser = {
  id: 4,
  username: "marketing_team",
  display_name: "마케팅팀",
  role: "marketing" as const,
  created_at: "2026-08-01T00:00:00Z",
};

const adminUser = {
  id: 1,
  username: "admin_team",
  display_name: "관리자",
  role: "admin" as const,
  created_at: "2026-08-01T00:00:00Z",
};

const operationsUser = {
  id: 2,
  username: "operations_team",
  display_name: "운영팀",
  role: "operations" as const,
  created_at: "2026-08-01T00:00:00Z",
};

function successResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  };
}

function unauthorizedResponse() {
  return {
    ok: false,
    status: 401,
    json: async () => ({ detail: "Authentication is required." }),
  };
}

function insightsResponse() {
  return {
    items: [],
    page: 1,
    page_size: 8,
    total: 0,
    total_pages: 1,
    stats: {
      total: 0,
      average_churn_probability: 0,
      risk_counts: { high: 0, medium: 0, low: 0 },
      cluster_counts: {},
      cluster_options: {},
    },
  };
}

function dashboardResponse(input: RequestInfo | URL) {
  const path = String(input);
  if (path.includes("/analytics/categorical-churn-rate")) {
    return successResponse({ field: "Gender", items: [] });
  }
  if (path.includes("/analytics/numeric-distribution")) {
    return successResponse({ field: "Total_Trans_Ct", by_target: {} });
  }
  if (path.includes("/analytics/feature-correlation")) {
    return successResponse({ items: [] });
  }
  if (path.includes("/model-runs/latest")) {
    return successResponse({
      status: "succeeded",
      started_at: "2026-08-01T00:00:00Z",
      completed_at: "2026-08-01T00:05:00Z",
      processed_rows: 0,
      dataset_sha256: null,
      runs: [],
    });
  }
  if (path.includes("/campaign-targets")) {
    return successResponse({ items: [], page: 1, page_size: 20, total: 0, total_pages: 0 });
  }
  return successResponse(insightsResponse());
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("인증 상태 앱 셸", () => {
  it("HttpOnly 세션이 있으면 새로고침 후 대시보드를 표시합니다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(successResponse(user))
      .mockImplementation((input: RequestInfo | URL) => Promise.resolve(dashboardResponse(input)));
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "고객 분석 대시보드" }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/me",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("미인증 상태에서 로그인하면 대시보드로 전환합니다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(unauthorizedResponse())
      .mockResolvedValueOnce(successResponse({ user }))
      .mockImplementation((input: RequestInfo | URL) => Promise.resolve(dashboardResponse(input)));
    vi.stubGlobal("fetch", fetchMock);
    const userEventInstance = userEvent.setup();

    render(<App />);

    await userEventInstance.type(
      await screen.findByLabelText("팀 계정 아이디"),
      "analysis_team",
    );
    await userEventInstance.type(
      screen.getByLabelText("비밀번호"),
      "temporary-password",
    );
    await userEventInstance.click(screen.getByRole("button", { name: "로그인" }));

    expect(
      await screen.findByRole("heading", { name: "고객 분석 대시보드" }),
    ).toBeInTheDocument();
  });

  it("마케팅팀은 인증 후 캠페인 센터로 바로 진입합니다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(successResponse(marketingUser))
      .mockImplementation((input: RequestInfo | URL) => Promise.resolve(dashboardResponse(input)));
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "마케팅 캠페인 센터" }),
    ).toBeInTheDocument();
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
  });

  it("관리자는 하나의 화면 안에서 세 개의 탭을 오갑니다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(successResponse(adminUser))
      .mockImplementation((input: RequestInfo | URL) => Promise.resolve(dashboardResponse(input)));
    vi.stubGlobal("fetch", fetchMock);
    const userEventInstance = userEvent.setup();

    render(<App />);

    expect(await screen.findByRole("heading", { name: "관리자 콘솔" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "관리자 콘솔" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "캠페인 관리" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "고객 분석 대시보드" })).toBeInTheDocument();

    await userEventInstance.click(screen.getByRole("tab", { name: "고객 분석 대시보드" }));
    expect(await screen.findByRole("heading", { name: "고객 분석 대시보드" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "캠페인 실행 피드백" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "캠페인 처리 현황" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "관리자 콘솔" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "캠페인 관리" })).toBeInTheDocument();

    await userEventInstance.click(screen.getByRole("tab", { name: "캠페인 관리" }));
    expect(await screen.findByRole("heading", { name: "캠페인 관리" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "관리자 콘솔" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "고객 분석 대시보드" })).toBeInTheDocument();

    await userEventInstance.click(screen.getByRole("tab", { name: "관리자 콘솔" }));
    expect(await screen.findByRole("heading", { name: "관리자 콘솔" })).toBeInTheDocument();
  });

  it("운영 담당자는 운영 업무 센터와 캠페인 관리 탭만 오갈 수 있습니다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(successResponse(operationsUser))
      .mockImplementation((input: RequestInfo | URL) => Promise.resolve(dashboardResponse(input)));
    vi.stubGlobal("fetch", fetchMock);
    const userEventInstance = userEvent.setup();

    render(<App />);

    expect(await screen.findByRole("heading", { name: "운영 업무 센터" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "고객 분석 대시보드" })).not.toBeInTheDocument();

    await userEventInstance.click(screen.getByRole("tab", { name: "캠페인 관리" }));
    expect(await screen.findByRole("heading", { name: "캠페인 관리" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "운영 업무 센터" })).toBeInTheDocument();

    await userEventInstance.click(screen.getByRole("tab", { name: "운영 업무 센터" }));
    expect(await screen.findByRole("heading", { name: "운영 업무 센터" })).toBeInTheDocument();
  });

  it("로그아웃하면 로그인 화면으로 돌아갑니다", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(successResponse(user))
      .mockImplementation((input: RequestInfo | URL) =>
        String(input).includes("/auth/logout")
          ? Promise.resolve({ ok: true, status: 204 })
          : Promise.resolve(dashboardResponse(input)),
      );
    vi.stubGlobal("fetch", fetchMock);
    const userEventInstance = userEvent.setup();

    render(<App />);

    await screen.findByRole("heading", { name: "고객 분석 대시보드" });
    await userEventInstance.click(screen.getByRole("button", { name: "로그아웃" }));

    expect(await screen.findByLabelText("팀 계정 아이디")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/v1/auth/logout",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
