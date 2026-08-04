// 캠페인 대상 조회·등록·처리 API 클라이언트입니다.

import { request } from "./client";
import type { components } from "./schema";

export type CampaignTarget = components["schemas"]["CampaignTargetResponse"];
export type CampaignTargetList =
  components["schemas"]["CampaignTargetListResponse"];
export type CampaignStatus = components["schemas"]["CampaignStatus"];
export type Campaign = components["schemas"]["CampaignResponse"];
export type CampaignList = components["schemas"]["CampaignListResponse"];
export type CampaignLifecycleStatus =
  components["schemas"]["CampaignLifecycleStatus"];
export type CampaignEventList =
  components["schemas"]["CampaignEventListResponse"];
export type CampaignResultCode =
  components["schemas"]["CampaignResultCode"];
export type CampaignPerformance =
  components["schemas"]["CampaignPerformanceResponse"];
export type BulkTargetingSegment =
  components["schemas"]["BulkTargetingSegment"];
export type BulkTargetingRun =
  components["schemas"]["BulkTargetingRunResponse"];

function toQueryString(query: Record<string, string | number | boolean | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) {
      params.set(key, String(value));
    }
  }
  const encoded = params.toString();
  return encoded.length > 0 ? `?${encoded}` : "";
}

export function listCampaigns(query: {
  status?: CampaignLifecycleStatus;
  name?: string;
  created_by_user_id?: number;
  page?: number;
  page_size?: number;
} = {}): Promise<CampaignList> {
  return request<CampaignList>(`/api/v1/campaigns${toQueryString(query)}`);
}

export function createCampaign(
  payload: components["schemas"]["CampaignCreateRequest"],
): Promise<Campaign> {
  return request<Campaign>("/api/v1/campaigns", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getCampaign(campaignId: number): Promise<Campaign> {
  return request<Campaign>(`/api/v1/campaigns/${campaignId}`);
}

export function updateCampaign(
  campaignId: number,
  payload: components["schemas"]["CampaignUpdateRequest"],
): Promise<Campaign> {
  return request<Campaign>(`/api/v1/campaigns/${campaignId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function listCampaignTargetsByCampaign(
  campaignId: number,
  query: {
    status?: CampaignStatus;
    assigned_to_user_id?: number;
    customer_id?: number;
    converted?: boolean;
    sort_by_priority?: boolean;
    page?: number;
    page_size?: number;
  } = {},
): Promise<CampaignTargetList> {
  return request<CampaignTargetList>(
    `/api/v1/campaigns/${campaignId}/targets${toQueryString(query)}`,
  );
}

export function listCampaignEvents(
  campaignId: number,
  query: { campaign_target_id?: number; page?: number; page_size?: number } = {},
): Promise<CampaignEventList> {
  return request<CampaignEventList>(
    `/api/v1/campaigns/${campaignId}/events${toQueryString(query)}`,
  );
}

export function getCampaignPerformance(query: {
  campaign_id?: number;
  segment?: BulkTargetingSegment;
  assigned_to_user_id?: number;
} = {}): Promise<CampaignPerformance> {
  return request<CampaignPerformance>(
    `/api/v1/campaign-performance${toQueryString(query)}`,
  );
}

export function listCampaignTargets(query: {
  status?: CampaignStatus;
  campaign_id?: number;
  campaign_name?: string;
  assigned_to_user_id?: number;
  customer_id?: number;
  converted?: boolean;
  page?: number;
  page_size?: number;
} = {}): Promise<CampaignTargetList> {
  return request<CampaignTargetList>(
    `/api/v1/campaign-targets${toQueryString(query)}`,
  );
}

export function createCampaignTarget(payload: {
  customer_insight_id: number;
  campaign_id?: number;
  campaign_name?: string;
  assigned_to_user_id?: number | null;
}): Promise<CampaignTarget> {
  return request<CampaignTarget>("/api/v1/campaign-targets", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateCampaignTarget(
  targetId: number,
  payload: {
    status?: CampaignStatus;
    assigned_to_user_id?: number | null;
    result?: string | null;
    result_notes?: string;
    result_code?: CampaignResultCode | null;
    converted?: boolean;
    retained?: boolean | null;
    outcome_revenue?: number | null;
  },
): Promise<CampaignTarget> {
  return request<CampaignTarget>(`/api/v1/campaign-targets/${targetId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function previewBulkTargeting(
  payload: components["schemas"]["BulkTargetingPreviewRequest"],
): Promise<BulkTargetingRun> {
  return request<BulkTargetingRun>("/api/v1/campaign-targeting/preview", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function executeBulkTargeting(runId: number): Promise<BulkTargetingRun> {
  return request<BulkTargetingRun>(
    `/api/v1/campaign-targeting/runs/${runId}/execute`,
    { method: "POST" },
  );
}

export function cancelBulkTargeting(runId: number): Promise<BulkTargetingRun> {
  return request<BulkTargetingRun>(
    `/api/v1/campaign-targeting/runs/${runId}/cancel`,
    { method: "POST" },
  );
}

export function rerunBulkTargeting(
  runId: number,
  payload: components["schemas"]["BulkTargetingRerunRequest"] = {},
): Promise<BulkTargetingRun> {
  return request<BulkTargetingRun>(
    `/api/v1/campaign-targeting/runs/${runId}/rerun`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}
