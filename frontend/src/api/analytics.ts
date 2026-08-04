// EDA 원천 데이터셋 기반 분석 통계(차트용) API 클라이언트입니다.

import { request } from "./client";

export type CategoricalField =
  | "Gender"
  | "Card_Category"
  | "Income_Category"
  | "Education_Level"
  | "Marital_Status";

export type NumericDistributionField =
  | "Total_Trans_Ct"
  | "Total_Trans_Amt"
  | "Avg_Utilization_Ratio"
  | "Months_Inactive_12_mon"
  | "Contacts_Count_12_mon"
  | "Total_Relationship_Count";

export type CategoricalChurnRateItem = {
  group: string;
  churn_rate: number;
  count: number;
};

export type CategoricalChurnRateResponse = {
  field: string;
  items: CategoricalChurnRateItem[];
};

export type NumericDistributionBucket = {
  min: number;
  q1: number;
  median: number;
  q3: number;
  max: number;
  count: number;
};

export type NumericDistributionResponse = {
  field: string;
  by_target: Record<string, NumericDistributionBucket>;
};

export type FeatureCorrelationItem = {
  feature: string;
  correlation: number;
};

export type FeatureCorrelationResponse = {
  items: FeatureCorrelationItem[];
};

export function getCategoricalChurnRate(
  field: CategoricalField,
): Promise<CategoricalChurnRateResponse> {
  return request<CategoricalChurnRateResponse>(
    `/api/v1/analytics/categorical-churn-rate?field=${encodeURIComponent(field)}`,
  );
}

export function getNumericDistribution(
  field: NumericDistributionField,
): Promise<NumericDistributionResponse> {
  return request<NumericDistributionResponse>(
    `/api/v1/analytics/numeric-distribution?field=${encodeURIComponent(field)}`,
  );
}

export function getFeatureCorrelation(): Promise<FeatureCorrelationResponse> {
  return request<FeatureCorrelationResponse>(
    "/api/v1/analytics/feature-correlation",
  );
}
