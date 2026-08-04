# 캠페인 업무 흐름과 권한

캠페인이 어떤 상태를 거쳐 흘러가고, 각 단계를 누가 처리할 수 있는지 코드 기준으로 정리합니다.
근거 위치를 함께 표기했습니다.

---

## 1. 상태는 두 층으로 나뉩니다

혼동하기 쉬운 부분인데, **캠페인 자체의 상태**와 **고객 한 명(대상)의 처리 상태**는 별개입니다.

| 층 | enum | 값 | 의미 |
|---|---|---|---|
| 캠페인 | `CampaignLifecycleStatus` | draft / scheduled / active / paused / completed / cancelled | 캠페인 전체의 실행 단계 |
| 대상 | `CampaignStatus` | pending / assigned / contacted / completed / cancelled | 고객 한 명에 대한 처리 진행도 |

(`backend/app/enums.py:31-49`)

### 1.1 캠페인 상태 전이 (`campaign_service.py:60-82`)

```
draft ──→ scheduled ──→ active ⇄ paused
  │           │           │        │
  │           │           └──→ completed ←┘
  └───────────┴───────────────→ cancelled
```

- `completed`와 `cancelled`는 **종착점**입니다(전이 불가, 빈 집합).
- `draft`에서 `paused`로 바로 갈 수 없고, `scheduled`에서 `completed`로 바로 갈 수 없습니다.
- 추가 제약: 대상 중 **치료군에 미처리 건이 남아 있으면 `completed`로 못 바꿉니다**.
  대조군은 애초에 접촉하지 않으므로 이 검사에서 제외됩니다.

### 1.2 대상 상태 전이 (`campaign_service.py:85-100`)

```
pending ⇄ assigned ──→ contacted ──→ completed
   │          │            │
   └──────────┴────────────┴──→ cancelled
```

- `assigned → pending` 역방향이 허용됩니다(담당자 배정 해제).
- `contacted`에서 `assigned`로 되돌릴 수 **없습니다** — 접촉은 취소 불가한 사실이기 때문입니다.
- `completed`, `cancelled`는 종착점입니다.

---

## 2. 누가 무엇을 할 수 있나

### 2.1 백엔드 권한 (실제 강제되는 규칙)

| 동작 | 허용 역할 | 근거 |
|---|---|---|
| 캠페인 조회 | 전체(인증된 사용자) | `insights/campaigns` GET |
| 캠페인 생성·수정 | **admin, marketing** | `campaigns.py:51` `CAMPAIGN_MANAGE_ROLES` |
| 세그먼트 일괄 타기팅 | **admin, marketing** | `bulk_targeting.py:39` |
| 대상 등록(후보→캠페인) | **admin, marketing** | `campaigns.py:52` |
| **대상 처리(상태·결과·전환·유지)** | **admin, operations** | `campaigns.py:53` `CAMPAIGN_TARGET_UPDATE_ROLES` |
| 수신 거부 변경 | **admin** | `customers.py:30` |

**운영팀 추가 제약** (`campaigns.py:591-606`) — admin에는 적용되지 않습니다.
- 대조군 대상은 편집 불가 → 403
- **자기에게 배정된 대상만** 처리 가능 → 403
- 담당자를 **자기 자신에게만** 배정 가능 → 403

### 2.2 질문: "관리자만 전환으로 바꾸거나 저장할 수 있나?"

**아닙니다. 운영팀도 저장할 수 있습니다.** 다만 **화면에 따라 달라서** 그렇게 보일 수 있습니다.

| 화면 | 편집 가능 역할 | 근거 |
|---|---|---|
| 캠페인 관리 → 대상 처리 탭 | **admin만** | `CampaignManagementPage.tsx:725` `canEditCampaignTargets = role === "admin"` |
| 운영 업무 센터 → 캠페인 처리 큐 | **admin + operations** | `DepartmentDashboardPage.tsx:945` `canProcessTargets` |

즉 **운영팀의 작업 공간은 "운영 업무 센터"의 처리 큐**이고, 캠페인 관리 화면의 대상 테이블은
관리자 전용 조회·수정 도구입니다. 캠페인 관리 화면에도 운영팀에게 이 점을 알리는 안내
문구가 있습니다("운영팀의 대상 처리와 성과 입력은 WORK QUEUE에서 진행합니다").

백엔드는 두 경로 모두 `admin, operations`를 허용하므로, 운영팀이 캠페인 관리 화면에서
편집 못 하는 것은 **UI 정책이지 권한 부족이 아닙니다.**

---

## 3. 전환·유지·매출을 저장할 때의 규칙

여기가 가장 제약이 많은 부분입니다. 순서를 어기면 저장이 거부됩니다.

### 3.1 전환(converted) — `campaign_service.py:1112-1129`

- 치료군은 **`completed` 상태여야** 전환 처리 가능
  → 아니면 `"A target must be completed before it can be marked converted."`
- 전환으로 저장하면 `result_code`가 자동으로 `converted`로 맞춰지고, 해제하면 `not_converted`로 바뀝니다.
  (전환 여부와 결과 코드는 **한 값이 다른 값을 따라가는 단일 입력**입니다)

### 3.2 결과 코드 — `campaign_service.py:1072-1111`

- `contacted` 상태로 바꾸면 결과 코드가 **자동으로 `contacted`** 로 채워집니다.
- 최종 코드(converted/not_converted/no_response/declined/opted_out/invalid_contact)는
  치료군의 경우 **`completed` 상태에서만** 입력 가능합니다.
- 치료군을 `completed`로 저장하려면 **최종 코드가 반드시 있어야** 합니다.
- `opted_out`을 선택하면 **고객의 `marketing_opt_out`이 자동으로 켜집니다** — 이후 모든
  타기팅에서 그 고객이 제외됩니다.

### 3.3 유지(retained) — `campaign_service.py:1130-1160`

- 치료군은 `completed` 이후에만 입력 가능
- **관측 기간이 지나야 합니다**: 기준 시점(치료군은 완료일, 대조군은 캠페인 시작일)에서
  `retention_window_days`(기본 30일)가 지나기 전에는 거부
  → `"Retention cannot be recorded before retention_window_days has elapsed."`
- 즉 처리 완료와 유지 입력은 **시차를 두고 두 번** 저장하는 구조입니다.

### 3.4 성과 매출(outcome_revenue)

- 전환 처리된 대상에만 입력 가능, 음수 불가

---

## 4. 캠페인 상태가 대상 처리를 막는 경우

| 캠페인 상태 | 대상에 허용되는 작업 |
|---|---|
| draft / scheduled / paused | 담당자 배정, 취소는 가능하나 **접촉·완료는 불가** |
| **active** | 전체 가능 (치료군 접촉·완료는 여기서만) |
| completed | 상태·담당자 변경 불가 (결과 보정은 일부 가능) |
| cancelled | **모든 변경 불가** |

(`campaign_service.py:939-989`)

핵심: 치료군을 접촉·완료 처리하려면 캠페인이 **`active`여야 합니다**
→ `"Treatment targets can only be contacted or completed in an active campaign."`

---

## 5. 대조군(control)은 다르게 취급됩니다

A/B 실험이 켜진 캠페인에서 대조군으로 배정된 고객은 **의도적으로 접촉하지 않는 비교군**입니다.

- 담당자 배정 불가, `assigned`/`contacted`/`completed` 전이 불가 (`campaign_service.py:964-972`)
- `contacted` 결과 코드 사용 불가
- 가능한 것: `pending` 유지 또는 `cancelled`, 그리고 **유지 여부 관측**(캠페인 시작일 기준)

이 규칙 덕분에 성과 집계에서 "접촉하지 않았는데 접촉한 것으로 잡히는" 오염이 구조적으로 차단됩니다.

---

## 6. 전형적인 진행 시나리오

```
[마케팅]  세그먼트 일괄 타기팅 미리보기 → 실행
             ↓  draft 캠페인 + 대상(pending) 생성, 일부는 대조군으로 배정
[마케팅]  캠페인을 active로 전환
             ↓
[운영]    운영 업무 센터에서 자기 대상을 assigned → contacted 로 처리
             ↓  (결과 코드 contacted 자동 기록, 고객 last_contacted_at 갱신)
[운영]    상담 종료 후 completed + 최종 결과 코드(전환/미전환/거절 등) 저장
             ↓
        ... 유지 관측 기간(기본 30일) 경과 ...
             ↓
[운영]    유지 여부(retained)와 성과 매출 입력
             ↓
[마케팅]  치료군 미처리가 0이 되면 캠페인을 completed로 전환
             ↓
[전체]    성과 대시보드에서 전환율·유지율·증분효과·ROI 확인
```

모든 단계는 `campaign_events`에 누적 기록되어 캠페인 상세의 "이벤트 이력"에서 조회할 수 있습니다.

---

## 7. 알아두면 좋은 자동 동작

| 트리거 | 자동으로 벌어지는 일 |
|---|---|
| `contacted` 또는 `completed`로 전이 | `processed_at` 기록, 고객 `last_contacted_at` 갱신 → 이후 "최근 접촉 제외" 필터에 반영 |
| 담당자만 배정(상태 미지정) | `pending` → `assigned` 자동 전이 |
| 담당자만 해제(상태 미지정) | `assigned` → `pending` 자동 복귀 |
| `contacted`로 전이 시 결과 코드 미입력 | 결과 코드 `contacted` 자동 설정 |
| 결과 코드 `opted_out` 저장 | 고객 `marketing_opt_out = true` → 향후 전 캠페인에서 제외 |
| 결과 코드 `converted` 저장 | `converted = true`, `converted_at` 기록 |
