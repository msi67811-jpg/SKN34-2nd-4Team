// customer_insights 조회 API 클라이언트입니다.

import { request } from "./client";
import type { components } from "./schema";

export type CustomerInsight = components["schemas"]["CustomerInsightResponse"];
export type CustomerInsightDetail =
  components["schemas"]["CustomerInsightDetailResponse"];
export type CustomerInsightList =
  components["schemas"]["CustomerInsightListResponse"];
export type CustomerInsightHistory =
  components["schemas"]["CustomerInsightHistoryResponse"];

export type InsightQuery = {
  risk_level?: "low" | "medium" | "high";
  cluster_name?: string;
  customer_id?: number;
  campaign_candidates_only?: boolean;
  campaign_id?: number;
  sort_by?:
    | "churn_probability"
    | "actual_transaction_count"
    | "activity_gap"
    | "expected_transaction_count"
    | "total_trans_amt"
    | "card_category"
    | "contacts_count_12_mon"
    | "scored_at";
  sort_order?: "asc" | "desc";
  page?: number;
  page_size?: number;
};

function toQueryString(query: InsightQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) {
      params.set(key, String(value));
    }
  }
  const encoded = params.toString();
  return encoded.length > 0 ? `?${encoded}` : "";
}

export function listCustomerInsights(
  query: InsightQuery = {},
): Promise<CustomerInsightList> {
  return request<CustomerInsightList>(
    `/api/v1/customer-insights${toQueryString(query)}`,
  );
}

export function getCustomerInsight(
  customerId: number,
): Promise<CustomerInsightDetail> {
  return request<CustomerInsightDetail>(
    `/api/v1/customer-insights/${customerId}`,
  );
}

export function getCustomerInsightHistory(
  customerId: number,
  limit = 24,
): Promise<CustomerInsightHistory> {
  return request<CustomerInsightHistory>(
    `/api/v1/customer-insights/history/${customerId}?limit=${limit}`,
  );
}
