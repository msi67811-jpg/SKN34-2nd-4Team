import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DashboardPage } from "./DashboardPage";

const authUser = {
  id: 1,
  username: "analysis_team",
  display_name: "분석팀",
  role: "analyst" as const,
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
  activity_gap: 0.4,
  total_trans_amt: 5000,
  card_category: "Blue",
  contacts_count_12_mon: 1,
  cluster_name: "활성 저하군",
  cluster_confidence: 0.91,
  recommended_action: "개인화 혜택을 제안하세요.",
  reason_codes: { inactivity: "높음" },
  scored_at: "2026-08-01T00:00:00Z",
};

const insightDetail = {
  ...insight,
  customer: {
    customer_id: 1001,
    customer_age: 42,
    gender: "M",
    dependent_count: 2,
    education_level: "Graduate",
    marital_status: "Married",
    income_category: "$80K - $120K",
    card_category: "Blue",
    months_on_book: 36,
    total_relationship_count: 4,
    months_inactive_12_mon: 2,
    contacts_count_12_mon: 1,
    credit_limit: 10000,
    total_revolving_bal: 3000,
    avg_open_to_buy: 7000,
    total_amt_chng_q4_q1: 0.8,
    total_trans_amt: 5000,
    total_trans_ct: 45,
    total_ct_chng_q4_q1: 0.9,
    avg_utilization_ratio: 0.3,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  },
};

function successResponse(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  };
}

const insightList = {
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
    cluster_options: { "활성 저하군": 1, "우량(예상이상)": 2 },
  },
};

const insightHistory = {
  customer_id: 1001,
  items: [insight],
};

const latestBatch = {
  status: "succeeded",
  started_at: "2026-08-01T00:00:00Z",
  completed_at: "2026-08-01T00:05:00Z",
  processed_rows: 1,
  dataset_sha256: null,
  runs: [
    {
      id: 1,
      task: "classification",
      model_name: "xgboost",
      model_version: "v1",
      artifact_sha256: null,
      dataset_sha256: null,
      status: "succeeded",
      processed_rows: 1,
      started_at: "2026-08-01T00:00:00Z",
      completed_at: "2026-08-01T00:05:00Z",
    },
  ],
};

const campaignPerformance = {
  campaign_id: null,
  segment_code: null,
  assigned_to_user_id: null,
  summary: {
    target_count: 10,
    treatment_count: 8,
    control_count: 2,
    contacted_count: 6,
    converted_count: 2,
    retained_count: 1,
    retention_eligible_count: 2,
    retention_observed_count: 1,
    retention_observation_rate: 0.5,
    contact_rate: 0.6,
    conversion_rate: 0.2,
    retention_rate: 1,
    treatment_contact_rate: 0.75,
    control_contact_rate: 0,
    treatment_conversion_rate: 0.25,
    control_conversion_rate: 0,
    treatment_retention_rate: 1,
    control_retention_rate: null,
    incremental_conversion_effect: 0.25,
    incremental_retention_effect: null,
    incremental_conversions: 2,
    total_cost: 100,
    observed_revenue: 500,
    incremental_revenue: 500,
    total_revenue: 500,
    roi: 4,
  },
  by_campaign: [],
  by_segment: [],
  by_assignee: [],
  generated_at: "2026-08-01T00:05:00Z",
};

const categoricalChurnRate = {
  field: "Gender",
  items: [
    { group: "F", churn_rate: 0.175, count: 5358 },
    { group: "M", churn_rate: 0.146, count: 4769 },
  ],
};

const numericDistribution = {
  field: "Total_Trans_Ct",
  by_target: {
    "0": { min: 10, q1: 45, median: 71, q3: 81, max: 139, count: 8500 },
    "1": { min: 10, q1: 32, median: 43, q3: 56, max: 111, count: 1627 },
  },
};

const featureCorrelation = {
  items: [
    { feature: "Total_Trans_Ct", correlation: -0.371 },
    { feature: "Contacts_Count_12_mon", correlation: 0.204 },
  ],
};

function dashboardFetchMock() {
  return vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const path = String(input);
    if (path.includes("/analytics/categorical-churn-rate")) {
      return Promise.resolve(successResponse(categoricalChurnRate));
    }
    if (path.includes("/analytics/numeric-distribution")) {
      return Promise.resolve(successResponse(numericDistribution));
    }
    if (path.includes("/analytics/feature-correlation")) {
      return Promise.resolve(successResponse(featureCorrelation));
    }
    if (path.includes("/history/")) {
      return Promise.resolve(successResponse(insightHistory));
    }
    if (path.includes("/model-runs/latest")) {
      return Promise.resolve(successResponse(latestBatch));
    }
    if (path.includes("/campaign-performance")) {
      return Promise.resolve(successResponse(campaignPerformance));
    }
    if (path.includes("/campaign-targets")) {
      return Promise.resolve(successResponse({ items: [], page: 1, page_size: 20, total: 0, total_pages: 0 }));
    }
    if (path.endsWith("/1001")) {
      return Promise.resolve(successResponse(insightDetail));
    }
    return Promise.resolve(successResponse(insightList));
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("고객 분석 대시보드", () => {
  it("요약 통계와 고객 분석 목록을 표시합니다", async () => {
    const fetchMock = dashboardFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<DashboardPage user={authUser} />);

    expect(await screen.findByText("분석 대상 고객")).toBeInTheDocument();
    expect(screen.getByText("고객별 분석 결과")).toBeInTheDocument();
    expect(screen.getByText("1001")).toBeInTheDocument();
    expect(screen.getByText("개인화 혜택을 제안하세요.")).toBeInTheDocument();
    expect(screen.getAllByText("82%")).toHaveLength(2);
    expect(await screen.findByRole("heading", { name: "캠페인 실행 피드백" })).toBeInTheDocument();
    expect(screen.getByText("미처리 대상")).toBeInTheDocument();
    expect(screen.getByText("4명")).toBeInTheDocument();
    const metricHelpButton = screen.getByRole("button", { name: "지표 설명" });
    expect(metricHelpButton).toHaveAttribute("aria-expanded", "false");
    const user = userEvent.setup();
    await user.click(metricHelpButton);
    expect(screen.getByText(/전체 대상 중 실제로 전화·문자·이메일/)).toBeInTheDocument();
    expect(screen.getByText(/캠페인 메시지·혜택·상담을 실제로 받은 그룹/)).toBeInTheDocument();
    expect(screen.getByText(/비슷한 고객 중 일부러 캠페인을 받지 않도록/)).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "캠페인 지표 설명" })).toBeInTheDocument();
    expect(metricHelpButton).toHaveAttribute("aria-expanded", "true");
    await user.click(screen.getByRole("button", { name: "지표 설명 닫기" }));
    expect(screen.queryByRole("dialog", { name: "캠페인 지표 설명" })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "전체 군집" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "활성 저하군 (1명)" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "우량(예상이상) (2명)" })).toBeInTheDocument();
  });

  it("군집을 선택한 뒤에도 다른 군집으로 변경할 수 있습니다", async () => {
    const fetchMock = dashboardFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<DashboardPage user={authUser} />);

    const clusterFilter = await screen.findByRole("combobox", { name: "군집" });
    await user.selectOptions(clusterFilter, "활성 저하군");

    expect(screen.getByRole("option", { name: "우량(예상이상) (2명)" })).toBeInTheDocument();
    expect(clusterFilter).toHaveValue("활성 저하군");
  });

  it("EDA 차트 탭으로 전환하면 고객 인사이트 대신 EDA 차트를 표시합니다", async () => {
    const fetchMock = dashboardFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<DashboardPage user={authUser} />);

    expect(await screen.findByText("고객별 분석 결과")).toBeInTheDocument();
    expect(screen.queryByText("탐색적 데이터 분석 차트")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "EDA 차트" }));

    expect(await screen.findByText("탐색적 데이터 분석 차트")).toBeInTheDocument();
    expect(screen.queryByText("고객별 분석 결과")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "고객 인사이트" }));

    expect(await screen.findByText("고객별 분석 결과")).toBeInTheDocument();
    expect(screen.queryByText("탐색적 데이터 분석 차트")).not.toBeInTheDocument();
  });

  it("고객을 선택하면 상세 패널을 표시합니다", async () => {
    const fetchMock = dashboardFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<DashboardPage user={authUser} />);

    await user.click(await screen.findByRole("button", { name: /1001/ }));

    expect(
      await screen.findByRole("heading", { name: "고객 1001" }),
    ).toBeInTheDocument();
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("개인화 혜택을 제안하세요.")).toBeInTheDocument();
    expect(within(dialog).getByText("42세")).toBeInTheDocument();
  });
});
