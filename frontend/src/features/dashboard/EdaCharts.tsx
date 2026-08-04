// EDA(탐색적 데이터 분석) 노트북의 인사이트를 분석팀 대시보드용 차트로 재현합니다.
// 위험도/군집 분포는 기존 customer-insights 통계를 재사용하고, 범주형 이탈률·
// 수치형 분포·상관관계는 /api/v1/analytics/* (원천 학습 데이터셋 집계)를 사용합니다.

import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  Cell,
  ComposedChart,
  Legend,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  getCategoricalChurnRate,
  getFeatureCorrelation,
  getNumericDistribution,
  type CategoricalChurnRateItem,
  type CategoricalField,
  type FeatureCorrelationItem,
  type NumericDistributionField,
} from "../../api/analytics";
import type { CustomerInsightList } from "../../api/insights";

const RISK_COLORS: Record<string, string> = {
  low: "#50b88b",
  medium: "#e8a34f",
  high: "#e66c78",
};

const CLUSTER_COLORS: Record<string, string> = {
  "우량(예상이상)": "#1baf7a",
  "일반관리(유지)": "#727b99",
  "우선케어(거래 감소)": "#e66c78",
};
const CLUSTER_FALLBACK_COLORS = ["#2a78d6", "#eda100", "#4a3aa7", "#e87ba4"];

const CATEGORICAL_FIELD_OPTIONS: { value: CategoricalField; label: string }[] = [
  { value: "Gender", label: "성별" },
  { value: "Card_Category", label: "카드 등급" },
  { value: "Income_Category", label: "소득 구간" },
  { value: "Education_Level", label: "학력" },
  { value: "Marital_Status", label: "결혼 상태" },
];

const NUMERIC_FIELD_OPTIONS: { value: NumericDistributionField; label: string }[] = [
  { value: "Total_Trans_Ct", label: "거래 건수" },
  { value: "Total_Trans_Amt", label: "거래 금액" },
  { value: "Avg_Utilization_Ratio", label: "한도소진율" },
  { value: "Months_Inactive_12_mon", label: "비활성 개월수" },
  { value: "Contacts_Count_12_mon", label: "문의 횟수" },
  { value: "Total_Relationship_Count", label: "보유 상품 수" },
];

const RISK_INCREASE_COLOR = "#d13f57";
const RISK_DECREASE_COLOR = "#2a78d6";
const RETAINED_COLOR = "#1baf7a";
const CHURNED_COLOR = "#d13f57";

function formatPercent(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}

function formatCompactNumber(value: number): string {
  return new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 }).format(value);
}

function toNumber(value: unknown): number {
  return typeof value === "number" ? value : Number(value ?? 0);
}

function clusterColor(name: string, index: number): string {
  return CLUSTER_COLORS[name] ?? CLUSTER_FALLBACK_COLORS[index % CLUSTER_FALLBACK_COLORS.length];
}

function ChartCard({
  kicker,
  title,
  action,
  wide = false,
  children,
}: {
  kicker: string;
  title: string;
  action?: React.ReactNode;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <article className={`eda-chart-card${wide ? " eda-chart-card--wide" : ""}`}>
      <div className="table-card__header eda-chart-card__header">
        <div>
          <p className="card-kicker">{kicker}</p>
          <h3>{title}</h3>
        </div>
        {action}
      </div>
      {children}
    </article>
  );
}

function RiskDistributionChart({ stats }: { stats: CustomerInsightList["stats"] }) {
  const data = (["high", "medium", "low"] as const)
    .map((risk) => ({
      name: risk === "high" ? "높음" : risk === "medium" ? "주의" : "낮음",
      value: stats.risk_counts[risk] ?? 0,
      color: RISK_COLORS[risk],
    }))
    .filter((entry) => entry.value > 0);

  return (
    <ChartCard kicker="RISK MONITOR" title="위험도 분포">
      {data.length === 0 ? (
        <p className="empty-copy">표시할 데이터가 없습니다.</p>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="name"
              innerRadius={52}
              outerRadius={84}
              paddingAngle={2}
            >
              {data.map((entry) => (
                <Cell key={entry.name} fill={entry.color} stroke="#fff" strokeWidth={2} />
              ))}
            </Pie>
            <Tooltip
              formatter={(value: unknown, name: unknown) => [
                `${formatCompactNumber(toNumber(value))}명`,
                String(name),
              ]}
            />
            <Legend verticalAlign="bottom" height={28} />
          </PieChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

function ClusterDistributionChart({ stats }: { stats: CustomerInsightList["stats"] }) {
  const data = Object.entries(stats.cluster_counts)
    .filter(([, count]) => count > 0)
    .sort(([, a], [, b]) => b - a)
    .map(([name, value], index) => ({ name, value, color: clusterColor(name, index) }));

  return (
    <ChartCard kicker="SEGMENTATION" title="활동성 군집 분포">
      {data.length === 0 ? (
        <p className="empty-copy">표시할 데이터가 없습니다.</p>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="name"
              innerRadius={52}
              outerRadius={84}
              paddingAngle={2}
            >
              {data.map((entry) => (
                <Cell key={entry.name} fill={entry.color} stroke="#fff" strokeWidth={2} />
              ))}
            </Pie>
            <Tooltip
              formatter={(value: unknown, name: unknown) => [
                `${formatCompactNumber(toNumber(value))}명`,
                String(name),
              ]}
            />
            <Legend verticalAlign="bottom" height={28} />
          </PieChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

function weightedAverage(items: CategoricalChurnRateItem[]): number {
  const totalCount = items.reduce((sum, item) => sum + item.count, 0);
  if (totalCount === 0) {
    return 0;
  }
  return items.reduce((sum, item) => sum + item.churn_rate * item.count, 0) / totalCount;
}

function CategoricalChurnRateChart() {
  const [field, setField] = useState<CategoricalField>("Gender");
  const [result, setResult] = useState<{
    field: CategoricalField;
    items: CategoricalChurnRateItem[];
  } | null>(null);
  const [error, setError] = useState("");
  const items = result?.field === field ? result.items : null;

  useEffect(() => {
    let isActive = true;
    getCategoricalChurnRate(field)
      .then((response) => {
        if (isActive) {
          setResult({ field, items: response.items });
          setError("");
        }
      })
      .catch((requestError) => {
        if (isActive) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : "이탈률 데이터를 불러오지 못했습니다.",
          );
        }
      });
    return () => {
      isActive = false;
    };
  }, [field]);

  const average = useMemo(() => (items === null ? 0 : weightedAverage(items)), [items]);

  return (
    <ChartCard
      kicker="EDA · CATEGORICAL"
      title="범주형 변수별 이탈률"
      action={
        <select
          aria-label="범주형 변수 선택"
          value={field}
          onChange={(event) => setField(event.target.value as CategoricalField)}
        >
          {CATEGORICAL_FIELD_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      }
    >
      {error !== "" ? (
        <p className="empty-copy">{error}</p>
      ) : items === null ? (
        <p className="empty-copy">불러오는 중입니다.</p>
      ) : (
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={items} layout="vertical" margin={{ left: 12, right: 24 }}>
            <XAxis
              type="number"
              tickFormatter={(value: number) => formatPercent(value)}
              stroke="#c3c2b7"
              tick={{ fontSize: 11 }}
            />
            <YAxis
              type="category"
              dataKey="group"
              width={90}
              stroke="#c3c2b7"
              tick={{ fontSize: 11 }}
            />
            <Tooltip
              formatter={(value: unknown, _name: unknown, entry: unknown) => {
                const payload = (entry as { payload?: CategoricalChurnRateItem } | undefined)
                  ?.payload;
                return [
                  `${formatPercent(toNumber(value), 1)} (${formatCompactNumber(payload?.count ?? 0)}명)`,
                  "이탈률",
                ];
              }}
            />
            <ReferenceLine
              x={average}
              stroke="#898781"
              strokeDasharray="4 4"
              label={{ value: `평균 ${formatPercent(average, 1)}`, position: "top", fontSize: 11, fill: "#898781" }}
            />
            <Bar dataKey="churn_rate" radius={[0, 4, 4, 0]} maxBarSize={22}>
              {items.map((item) => (
                <Cell
                  key={item.group}
                  fill={item.churn_rate > average ? RISK_INCREASE_COLOR : RISK_DECREASE_COLOR}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
      <div className="eda-chart-legend">
        <span><i style={{ backgroundColor: RISK_INCREASE_COLOR }} /> 평균보다 높음</span>
        <span><i style={{ backgroundColor: RISK_DECREASE_COLOR }} /> 평균 이하</span>
      </div>
    </ChartCard>
  );
}

const BOX_GROUP_LABELS: Record<string, string> = {
  "0": "유지(0)",
  "1": "이탈(1)",
};

type NumericDistributionBuckets = Record<
  string,
  { min: number; q1: number; median: number; q3: number; max: number; count: number }
>;

function NumericDistributionChart() {
  const [field, setField] = useState<NumericDistributionField>("Total_Trans_Ct");
  const [result, setResult] = useState<{
    field: NumericDistributionField;
    byTarget: NumericDistributionBuckets;
  } | null>(null);
  const [error, setError] = useState("");
  const byTarget = result?.field === field ? result.byTarget : null;

  useEffect(() => {
    let isActive = true;
    getNumericDistribution(field)
      .then((response) => {
        if (isActive) {
          setResult({ field, byTarget: response.by_target });
          setError("");
        }
      })
      .catch((requestError) => {
        if (isActive) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : "수치형 분포 데이터를 불러오지 못했습니다.",
          );
        }
      });
    return () => {
      isActive = false;
    };
  }, [field]);

  const chartData = useMemo(() => {
    if (byTarget === null) {
      return [];
    }
    return Object.entries(byTarget)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([target, bucket]) => {
        const range = Math.max(bucket.max - bucket.min, 1e-6);
        const gap = range * 0.012;
        return {
          name: BOX_GROUP_LABELS[target] ?? target,
          color: target === "1" ? CHURNED_COLOR : RETAINED_COLOR,
          base: bucket.min,
          whiskerLow: Math.max(bucket.q1 - bucket.min, 0),
          boxLower: Math.max(bucket.median - bucket.q1 - gap / 2, 0),
          medianGap: gap,
          boxUpper: Math.max(bucket.q3 - bucket.median - gap / 2, 0),
          whiskerHigh: Math.max(bucket.max - bucket.q3, 0),
          raw: bucket,
        };
      });
  }, [byTarget]);

  return (
    <ChartCard
      kicker="EDA · NUMERIC"
      title="이탈 여부별 수치형 분포"
      action={
        <select
          aria-label="수치형 변수 선택"
          value={field}
          onChange={(event) => setField(event.target.value as NumericDistributionField)}
        >
          {NUMERIC_FIELD_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      }
    >
      {error !== "" ? (
        <p className="empty-copy">{error}</p>
      ) : byTarget === null ? (
        <p className="empty-copy">불러오는 중입니다.</p>
      ) : (
        <ResponsiveContainer width="100%" height={240}>
          <ComposedChart data={chartData} margin={{ left: 12, right: 12 }}>
            <XAxis dataKey="name" stroke="#c3c2b7" tick={{ fontSize: 11 }} />
            <YAxis stroke="#c3c2b7" tick={{ fontSize: 11 }} tickFormatter={formatCompactNumber} />
            <Tooltip
              content={({ payload }) => {
                const entry = payload?.[0]?.payload as (typeof chartData)[number] | undefined;
                if (entry === undefined) {
                  return null;
                }
                return (
                  <div className="eda-boxplot-tooltip">
                    <strong>{entry.name}</strong>
                    <span>최대 {formatCompactNumber(entry.raw.max)}</span>
                    <span>Q3 {formatCompactNumber(entry.raw.q3)}</span>
                    <span>중앙값 {formatCompactNumber(entry.raw.median)}</span>
                    <span>Q1 {formatCompactNumber(entry.raw.q1)}</span>
                    <span>최소 {formatCompactNumber(entry.raw.min)}</span>
                  </div>
                );
              }}
            />
            <Bar dataKey="base" stackId="box" fill="transparent" isAnimationActive={false} />
            <Bar dataKey="whiskerLow" stackId="box" fill="#c3c2b7" maxBarSize={48} />
            <Bar dataKey="boxLower" stackId="box" maxBarSize={48}>
              {chartData.map((entry) => (
                <Cell key={`${entry.name}-lower`} fill={entry.color} />
              ))}
            </Bar>
            <Bar dataKey="medianGap" stackId="box" fill="#fcfcfb" isAnimationActive={false} />
            <Bar dataKey="boxUpper" stackId="box" maxBarSize={48}>
              {chartData.map((entry) => (
                <Cell key={`${entry.name}-upper`} fill={entry.color} />
              ))}
            </Bar>
            <Bar dataKey="whiskerHigh" stackId="box" fill="#c3c2b7" radius={[4, 4, 0, 0]} maxBarSize={48} />
          </ComposedChart>
        </ResponsiveContainer>
      )}
      <div className="eda-chart-legend">
        <span><i style={{ backgroundColor: RETAINED_COLOR }} /> 유지 고객</span>
        <span><i style={{ backgroundColor: CHURNED_COLOR }} /> 이탈 고객</span>
        <span><i style={{ backgroundColor: "#c3c2b7" }} /> 최소~최대 범위</span>
      </div>
    </ChartCard>
  );
}

function FeatureCorrelationChart() {
  const [items, setItems] = useState<FeatureCorrelationItem[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let isActive = true;
    getFeatureCorrelation()
      .then((response) => {
        if (isActive) {
          setItems(response.items);
        }
      })
      .catch((requestError) => {
        if (isActive) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : "상관관계 데이터를 불러오지 못했습니다.",
          );
        }
      });
    return () => {
      isActive = false;
    };
  }, []);

  return (
    <ChartCard kicker="EDA · CORRELATION" title="변수-이탈 상관관계" wide>
      {error !== "" ? (
        <p className="empty-copy">{error}</p>
      ) : items === null ? (
        <p className="empty-copy">불러오는 중입니다.</p>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <BarChart
            data={items}
            layout="vertical"
            margin={{ left: 12, right: 24 }}
          >
            <XAxis
              type="number"
              domain={[-0.5, 0.5]}
              tickFormatter={(value: number) => value.toFixed(1)}
              stroke="#c3c2b7"
              tick={{ fontSize: 11 }}
            />
            <YAxis
              type="category"
              dataKey="feature"
              width={140}
              stroke="#c3c2b7"
              tick={{ fontSize: 10 }}
            />
            <Tooltip formatter={(value: unknown) => [toNumber(value).toFixed(3), "상관계수"]} />
            <ReferenceLine x={0} stroke="#898781" />
            <Bar dataKey="correlation" maxBarSize={16}>
              {items.map((item) => (
                <Cell
                  key={item.feature}
                  fill={item.correlation > 0 ? RISK_INCREASE_COLOR : RISK_DECREASE_COLOR}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
      <div className="eda-chart-legend">
        <span><i style={{ backgroundColor: RISK_INCREASE_COLOR }} /> 이탈 위험 ↑ (양의 상관)</span>
        <span><i style={{ backgroundColor: RISK_DECREASE_COLOR }} /> 이탈 위험 ↓ (음의 상관)</span>
      </div>
    </ChartCard>
  );
}

export function EdaChartsSection({ stats }: { stats: CustomerInsightList["stats"] }) {
  return (
    <article className="bento-card bento-card--eda">
      <div className="table-card__header">
        <div>
          <p className="card-kicker">EDA INSIGHTS</p>
          <h2>탐색적 데이터 분석 차트</h2>
        </div>
        <span className="table-count">학습 데이터셋 기준</span>
      </div>
      <p className="eda-charts-intro">
        분석팀 EDA 노트북에서 확인된 이탈 패턴을 대시보드에서 바로 확인합니다. 위험도·군집 분포는
        최신 분석 스냅샷 기준이고, 나머지 차트는 모델 학습에 사용한 원천 데이터셋 기준입니다.
      </p>
      <div className="eda-charts-grid">
        <RiskDistributionChart stats={stats} />
        <ClusterDistributionChart stats={stats} />
        <CategoricalChurnRateChart />
        <NumericDistributionChart />
        <FeatureCorrelationChart />
      </div>
    </article>
  );
}
