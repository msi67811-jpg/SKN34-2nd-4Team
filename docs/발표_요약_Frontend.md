# CardOps — 발표 요약 (Frontend 데모용)

> **SKN34-2nd-4Team** · 신용카드 고객 이탈 예측 & 통합 운영 플랫폼  
> 발표 시 **브라우저 `http://127.0.0.1:5173`** 를 열고 이 문서와 함께 진행하세요.

---

## 한 문장으로

**ML로 “누가·왜·어떤 유형으로” 이탈 위험인지 분석하고, CardOps 웹에서 역할별로 캠페인까지 실행·성과 측정하는 End-to-End 서비스입니다.**

---

## 프로젝트 개요

| 항목 | 내용 |
|------|------|
| **데이터** | Kaggle Bank Churners (10,127명) |
| **목표** | 이탈 예측 + 활동성 분석 + 고객 세그먼트 + 유지 캠페인 |
| **웹 서비스명** | **CardOps** (카드 통합 운영 플랫폼) |
| **Frontend** | React 19 · TypeScript · Vite |
| **Backend** | FastAPI · MySQL · ML 모델 3종 |
| **접속** | Frontend `http://127.0.0.1:5173` · API `http://127.0.0.1:8000/docs` |

---

## ML → 화면 연결 (발표에서 꼭 말할 것)

노트북/스크립트에서 만든 **3개 모델** 결과가 DB에 저장되고, Frontend가 API로 조회합니다.

| 모델 | 하는 일 | 화면에 보이는 값 |
|------|---------|------------------|
| **분류** | 이탈 확률 예측 | `예상 이탈 확률`, **위험도**(낮음/주의/높음) |
| **회귀** | 프로필 대비 정상 거래건수 | `예상 거래건수`, **활동성 갭** (= 실제 − 예측) |
| **군집** | 행동 유형 세분화 | `고객 군집`, `군집 신뢰도`, **추천 액션** |

```
BankChurners.csv
    → 분류 / 회귀 / 군집 (Python)
    → run_analysis_batch (Backend)
    → customer_insights (MySQL)
    → React 대시보드
```

**용어:** **갭(gap)** = 실제 거래건수 − 예측 거래건수 (음수면 예상보다 활동 감소)

---

## 시스템 구조

```text
[React CardOps]  ←→  [FastAPI]  ←→  [MySQL]
     :5173              :8000          :3307
                           ↑
                    outputs/models/
                    (분류·회귀·군집.pkl)
```

- Frontend는 **HttpOnly 쿠키**로 로그인 (JWT를 JS에 저장하지 않음)
- `/api/*` 요청은 Vite 프록시 → FastAPI
- 분석 결과는 **배치**로 갱신 → 화면 **“최근 배치 상태”** 카드에 표시

---

## 역할 4종 — 누구로 로그인해서 보여줄지

| 역할 | 계정 예시 | 첫 화면 | 발표 포인트 |
|------|-----------|---------|-------------|
| **analyst** (분석) | `analyst` | **고객 분석 대시보드** | ML 결과 조회·필터·CSV·상세 |
| **operations** (운영) | `operations` | **운영 업무 센터** | 고위험 상담·캠페인 **처리 큐** |
| **marketing** (마케팅) | `marketing` | **캠페인 관리 센터** | 세그먼트·**일괄 타기팅**·A/B |
| **admin** (관리자) | `admin` | **관리자 콘솔** | 팀 계정·역할·분석 대시보드 진입 |

> 로컬 테스트 계정: `seed_test_users` 실행 후 `.env`의 `TEST_*_PASSWORD` 사용

### 권한 요약

| 기능 | analyst | operations | marketing | admin |
|------|:-------:|:----------:|:---------:|:-----:|
| 분석 결과 조회 | ✅ | ✅ | ✅ | ✅ |
| 캠페인 대상 **등록** | ❌ | ❌ | ✅ | ✅ |
| 캠페인 **처리**(상태·결과) | ❌ | ✅ | ❌ | ✅ |
| 캠페인 **생성·일괄 타기팅** | ❌ | ❌ | ✅ | ✅ |
| 팀 역할 변경 | ❌ | ❌ | ❌ | ✅ |

---

## 발표 데모 시나리오 (추천 8~10분)

### 0. 사전 준비

```bash
docker compose up -d --build
docker compose exec backend python -m backend.scripts.import_customers
docker compose exec backend python -m backend.scripts.run_analysis_batch
# (선택) 데모 캠페인: seed_demo_campaign
```

브라우저: **`http://127.0.0.1:5173`**

---

### 1. 로그인 화면 (`LoginPage`)

**보이는 것:** CardOps 로고 · 아이디/비밀번호 · 회원가입

**말할 것:**
- 팀 계정 기반 B2B 콘솔
- Argon2 해시 + **HttpOnly 쿠키** (XSS에 토큰 노출 최소화)
- 로그인 성공 시 **역할에 따라 다른 업무 화면**으로 분기

**데모:** `analyst` 로그인 → 분석 대시보드

---

### 2. 분석 대시보드 (`DashboardPage`) — **analyst / admin**

헤더: **「고객 분석 대시보드」** — *고객 행동 데이터를 기반으로 이탈 위험과 실행 우선순위를 확인*

#### 벤토 그리드 카드 (위 → 아래)

| 카드 | 라벨 | 설명 멘트 |
|------|------|-----------|
| **Hero** | CUSTOMER HEALTH · 분석 대상 고객 | 배치로 스코어링된 고객 수 |
| **평균 이탈 확률** | CHURN PROBABILITY | 분류 모델 결과 요약 |
| **우선 관리 고객** | ACTION PRIORITY · HIGH | **클릭 → high 필터** (데모 포인트) |
| **위험도 분포** | RISK MONITOR | low / medium / high 막대 |
| **주요 고객 군집** | SEGMENTATION | 군집 모델 — 우선케어·우량 등 |
| **최근 배치 상태** | DATA FRESHNESS · SYNCED | 3모델 버전·실행 시각 |
| **고객별 분석 결과** | CUSTOMER INSIGHTS | 테이블 + 필터 |

#### 고객 목록 테이블

- **필터:** 고객 ID · 위험도 · **군집** · 정렬(이탈확률/갭/예상거래 등)
- **행 클릭** → 우측 **고객 상세 패널** (데모 핵심)
- **CSV 다운로드** — 현재 필터 결과 export

#### 고객 상세 패널 (`CustomerDetailPanel`)

| 항목 | ML 연결 |
|------|---------|
| 예상 이탈 확률 + 위험 배지 | 분류 |
| **예상 거래건수** · **활동성 갭** | 회귀 |
| **고객 군집** · **군집 신뢰도** | 군집 (GMM이면 확률) |
| 추천 액션 · 추천 근거 | Backend rule |
| **분석 이력** | 과거 배치별 추이 |
| 캠페인 업무 | 마케팅/운영 연계 |

**말할 한 줄:**  
> “한 고객 화면에 **분류·회귀·군집**이 합쳐져서, CS/마케팅이 **왜 이 고객인지** 바로 볼 수 있습니다.”

#### (admin) 캠페인 실행 피드백

- analyst 또는 admin 인사이트 뷰: **전환율·유지율·증분효과·ROI**
- “캠페인이 실제로 효과 있었는지” ML 예측 **이후** 검증

---

### 3. 운영 업무 센터 (`DepartmentDashboardPage`) — **operations**

**제목:** 운영 업무 센터 · *고위험 고객의 상담과 후속 처리를 관리*

| 섹션 | 내용 |
|------|------|
| **통계 카드** | HIGH RISK · OPEN QUEUE · 평균 이탈 · 배치 |
| **우선 관리 고객** | high 위험 목록 → (마케팅이 등록한) 캠페인 연결 |
| **캠페인 처리 현황 (WORK QUEUE)** | pending → assigned → contacted → completed |

**데모 동작:**
1. 대기 중인 대상 선택
2. **담당자 배정** → **접촉 완료** → **처리 완료**
3. 결과 코드(전환/거절/무응답 등) · 전환 체크 · 저장

**말할 것:**  
> “ML이 **누구를** 찾아주고, 운영팀이 **실제로 연락·결과**를 남깁니다.”

---

### 4. 마케팅 캠페인 센터

마케팅 로그인 시 **바로 `CampaignManagementPage`** (캠페인 관리가 메인)

#### 4-A. 부서 대시보드 (`DepartmentDashboardPage`) — marketing도 접근 가능

- 캠페인 상태 **퍼널** (대기→배정→접촉→완료)
- 세그먼트 필터로 고객 탐색

#### 4-B. 캠페인 관리 (`CampaignManagementPage`)

| 패널 | 기능 |
|------|------|
| **캠페인 목록** | 생성·상태(draft/active/completed…) |
| **캠페인 후보 고객** | `campaign_candidates_only` — 중복·수신거부 제외 |
| **일괄 타기팅** | 세그먼트 프리셋으로 대량 등록 |
| **캠페인 성과 대시보드** | A/B · ROI · 증분 효과 |

#### 일괄 타기팅 세그먼트 (`BulkTargetingPanel`)

| 세그먼트 | 선정 기준 |
|----------|-----------|
| **고위험 고객 리텐션** | risk = high, 이탈확률 순 |
| **중위험·활동성 하락 재활성화** | risk = medium + **activity_gap 하위 20%** |
| **저위험·우량군 업셀링** | risk = low + **우량(예상이상) 군집** |

- **A/B 테스트:** 대조군 비율 설정 → treatment / control 자동 배정
- **미리보기 → 실행** → 운영 WORK QUEUE에 대상 생성

**말할 것:**  
> “노트북의 **군집·갭·이탈확률**이 그대로 **캠페인 타기팅 규칙**이 됩니다.”

#### 성과 (`CampaignPerformancePanel`)

- 대상군 vs **대조군** 전환율
- **증분 ROI** · 유지율
- “마케팅 비용 대비 효과”를 같은 DB에서 집계

---

### 5. 관리자 콘솔 (`DepartmentDashboardPage`) — **admin**

- **활성 팀 계정** — 역할 변경(analyst/operations/marketing)
- 통계: 전체 고객 · 캠페인 큐 · HIGH RISK
- **「분석 대시보드」** 버튼 → admin용 insights 뷰 (캠페인 피드백 포함)

---

## 화면 ↔ 파일 매핑 (질문 대비)

| 화면 | 소스 파일 |
|------|-----------|
| 앱 진입·역할 분기 | `frontend/src/app/App.tsx` |
| 로그인 | `frontend/src/features/auth/LoginPage.tsx` |
| 분석 대시보드 | `frontend/src/features/dashboard/DashboardPage.tsx` |
| 운영/마케팅/관리자 | `frontend/src/features/department/DepartmentDashboardPage.tsx` |
| 캠페인 관리 | `frontend/src/features/campaign/CampaignManagementPage.tsx` |
| 일괄 타기팅 | `frontend/src/features/campaign/BulkTargetingPanel.tsx` |
| 캠페인 성과 | `frontend/src/features/campaign/CampaignPerformancePanel.tsx` |
| API 클라이언트 | `frontend/src/api/insights.ts`, `campaigns.ts`, `auth.ts` |

---

## End-to-End 스토리 (클로징 30초)

1. **데이터** — 카드 고객 행동 로그  
2. **ML** — 이탈 확률 · 활동 갭 · 행동 군집  
3. **CardOps** — 역할별로 **보고 → 타기팅 → 실행 → 성과**  
4. **가치** — “예측 모델”을 **실무 워크플로**까지 연결

---

## Q&A 예상

**Q. Streamlit 대시보드는?**  
→ 모델 **성능 평가**(ROC, Silhouette)는 `dashboard/app.py`. **운영 UI**는 CardOps React.

**Q. 실시간 예측?**  
→ 배치 스코어링 + DB 스냅샷. `/api/v1/predictions` 단건 API도 있음.

**Q. 왜 역할을 나눴나?**  
→ 마케팅(기획·등록) / 운영(접촉·결과) **책임 분리** + 감사 추적.

**Q. 갭이 음수인데 해지율이 낮을 수 있나?**  
→ **조기 경보** 지표. 아직 이탈 전 “활동만 줄어든” 고객을 먼저 케어.

**Q. 데모 데이터?**  
→ `[DEMO]` 캠페인 + `seed_demo_campaign` — 로컬 시연 전용.

---

## 데모 체크리스트

- [ ] `docker compose ps` — mysql healthy, backend/frontend Up  
- [ ] `http://127.0.0.1:8000/ready` — 모델 적재 OK  
- [ ] `run_analysis_batch` 완료 — 대시보드에 고객 수 > 0  
- [ ] 테스트 계정 4역할 로그인 확인  
- [ ] analyst: 고객 1명 클릭 → 갭·군집·이력  
- [ ] marketing: 일괄 타기팅 미리보기  
- [ ] operations: WORK QUEUE 상태 변경 1건  
- [ ] (선택) 캠페인 성과 ROI 패널

---

## 관련 문서

| 문서 | 내용 |
|------|------|
| [`README.md`](../README.md) | 프로젝트 전체·Docker 실행 |
| [`frontend/README.md`](../frontend/README.md) | Frontend 상세 |
| [`docs/customer_insights_api.md`](customer_insights_api.md) | 분석 API |
| [`docs/campaign_performance.md`](campaign_performance.md) | A/B·ROI |
| [`notebooks/README_05_clustering_발표.md`](../notebooks/README_05_clustering_발표.md) | 군집 분석 발표 |

---

## 발표용 6줄 스크립트

1. **CardOps**는 카드사 **고객 이탈**을 ML로 분석하고 **캠페인까지** 이어주는 플랫폼입니다.  
2. **분류·회귀·군집** 세 모델이 **이탈 확률, 활동 갭, 고객 유형**을 만듭니다.  
3. **분석 대시보드**에서 고객별로 한눈에 보고, 필터·CSV로 내려받습니다.  
4. **마케팅**은 세그먼트·일괄 타기팅으로 대상을 등록하고, **A/B·ROI**로 성과를 봅니다.  
5. **운영**은 WORK QUEUE에서 실제 **상담·결과**를 기록합니다.  
6. **노트북 실험 → 배치 → 웹**까지 연결된 **End-to-End** 프로젝트입니다.
