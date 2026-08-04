# 신용카드 고객 이탈 예측 프로젝트

Kaggle의 `Credit Card Customers` 데이터를 활용해 고객 이탈 위험을 예측하고,
거래 활동성과 고객 행동 유형을 함께 분석하는 머신러닝 프로젝트입니다.

## 분석 목표

1. **분류**: 고객별 이탈 확률을 예측합니다.
2. **회귀**: 고객 프로필 대비 기대 거래건수를 추정해 활동성 격차를 확인합니다.
3. **군집**: 행동 특성이 유사한 고객을 묶어 군집별 유지 전략을 설계합니다.
4. **의사결정 지원**: 이탈 위험도, 활동성, 고객 유형을 결합해 관리 우선순위를 제안합니다.

## 저장소 구조

```text
SKN34-2nd-4Team/
├── README.md
├── requirements.txt
├── data/
│   ├── README.md
│   ├── raw/
│   │   └── BankChurners.csv
│   └── processed/
│       └── bankchurners_clean.csv
├── docs/
│   ├── README.md
│   └── guides/
│       └── git_upstream_push_pr_guide.pdf
├── notebooks/
│   ├── 00_project_roadmap.ipynb
│   ├── 01_data_load_clean.ipynb
│   ├── 02_eda.ipynb
│   ├── 03_classification.ipynb
│   ├── 04_regression.ipynb
│   └── 05_clustering.ipynb
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   └── routes/
│   │   │       ├── auth.py
│   │   │       ├── insights.py
│   │   │       ├── predictions.py
│   │   │       └── system.py
│   │   ├── services/
│   │   │   └── insight_service.py
│   │   ├── config.py
│   │   ├── main.py
│   │   ├── model_manifest.py
│   │   ├── model_registry.py
│   │   └── schemas.py
│   ├── tests/
│   │   └── test_api.py
│   ├── README.md
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   │   ├── auth.ts
│   │   │   ├── client.ts
│   │   │   └── insights.ts
│   │   ├── features/
│   │   │   ├── auth/
│   │   │   └── insights/
│   │   ├── styles/
│   │   ├── test/
│   │   └── main.tsx
│   ├── README.md
│   ├── package.json
│   ├── pnpm-lock.yaml
│   ├── pnpm-workspace.yaml
│   └── vite.config.ts
├── dashboard/
│   └── app.py
├── src/
│   ├── README.md
│   ├── classification.py
│   ├── regression.py
│   └── clustering.py
└── outputs/
    └── README.md
```

Git의 `upstream` 설정부터 커밋, 포크 저장소 푸시, Pull Request 생성까지의 과정은
[`docs/guides/git_upstream_push_pr_guide.pdf`](docs/guides/git_upstream_push_pr_guide.pdf)에서 확인할 수 있습니다.

## 실행 순서

노트북은 `notebooks/` 디렉터리를 작업 디렉터리로 사용합니다.

```bash
python -m venv project_venv
source project_venv/bin/activate
pip install -r requirements.txt
cd notebooks
jupyter lab
```

아래 순서로 실행합니다.

1. `01_data_load_clean.ipynb`
2. `02_eda.ipynb`
3. `03_classification.ipynb`
4. `04_regression.ipynb`
5. `05_clustering.ipynb`

`00_project_roadmap.ipynb`는 역할 분담과 실행 계획을 정리한 문서입니다.

## 모델 코드 실행

노트북의 모델링 내용을 공부하기 쉬운 독립 Python 파일로 분리했습니다.

```bash
source project_venv/bin/activate

python src/classification.py
python src/regression.py
python src/clustering.py
```

학습 모델은 `outputs/models/`, 평가 결과는 `outputs/reports/`에 저장됩니다.
코드 구성과 모델별 입력 변수는 `src/README.md`에서 확인할 수 있습니다.

## FastAPI 백엔드 실행

분류 모델을 학습한 뒤 프로젝트 루트에서 FastAPI 서버를 실행합니다.

```powershell
.\project_venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
.\project_venv\Scripts\python.exe -m backend.app.migration_runner
.\project_venv\Scripts\python.exe -m backend.scripts.import_customers
.\project_venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
```

기본 주소는 `http://127.0.0.1:8000`이며 다음 API를 제공합니다.

- `GET /live`: API 프로세스 생존 여부 확인
- `GET /ready`: 모델 적재 상태 확인
- `POST /api/v1/predictions`: 고객 한 명의 이탈 상태와 확률 예측
- `GET /docs`: Swagger UI

백엔드의 파일별 책임, 모델 적재 흐름, 19개 입력 필드, React 연결, 테스트와
오류 해결 방법은 [`backend/README.md`](backend/README.md)에서 확인할 수
있습니다.

## Docker Compose 실행

Docker Desktop을 설치한 뒤 프로젝트 루트에서 실행합니다. 모델 파일은 별도
컨테이너로 띄우지 않고 FastAPI 컨테이너에 읽기 전용으로 마운트합니다.

구성도, 포트 매핑, 다른 노트북에서의 재현 방법과 문제 해결 방법은
[`docs/README.md`](docs/README.md)에서 확인할 수 있습니다.
Alembic migration, 고객 데이터 적재와 테이블별 상세 명세는
[`docs/phase1_database_implementation.md`](docs/phase1_database_implementation.md)를
확인합니다.

### 처음 실행하는 방법

다음 프로그램이 필요합니다.

- Git
- Docker Desktop
- Python 3.13 권장

1. 저장소를 받고 루트 디렉터리로 이동합니다.

```bash
git clone <저장소 주소>
cd SKN34-2nd-4Team
```

2. 환경변수를 준비합니다.

```bash
cp .env.example .env
openssl rand -hex 32
```

생성된 값을 `.env`의 `JWT_SECRET`에 입력합니다. 기존 Mac MySQL과의 포트
충돌을 피하려면 `MYSQL_PORT=3307`을 유지합니다.

`.env.example`에는 `ALLOW_TEST_USER_SEEDING=false`가 들어 있습니다. 로컬에서
테스트 계정으로 로그인하려면 **`true`로 바꿔야 합니다.** 

3. Frontend·Backend·MySQL을 시작합니다. 모델은 `model-builder` 컨테이너가
   자동으로 학습합니다

```bash
docker compose up -d --build
docker compose ps
```

`mysql`은 `healthy`, `backend`와 `frontend`는 `Up` 상태여야 합니다.
`model-builder`는 모델 생성을 마치면 `Exited (0)`이 되는 일회성 서비스이며,
산출물이 이미 있으면 학습을 건너뜁니다. Backend는 시작 시 Alembic migration을
자동 적용하고, `ALLOW_TEST_USER_SEEDING=true`이면 테스트 계정도 함께 만듭니다.

4. 고객 데이터를 적재합니다. (합성 목데이터)

<!-- ```bash
docker compose exec backend python -m backend.scripts.import_customers
``` -->

위험도 구간이 고르게 분포한 합성 고객 2,000명

```bash
docker compose exec backend python -m backend.scripts.generate_synthetic_customers
docker compose exec backend python -m backend.scripts.import_customers \
  --data-path /app/data/synthetic/synthetic_customers.csv --replace
```

`import_customers`는 `CLIENTNUM` 기준 upsert 방식이므로 다시 실행해도 고객이
중복되지 않습니다. `--replace`는 기존 고객과 연관 데이터를 모두 지우고
교체하므로 로컬 DB에서만 사용합니다.

5. 분석 배치를 실행합니다.

```bash
docker compose exec backend python -m backend.scripts.run_analysis_batch
```

분석 배치는 `customers`를 읽어 `customer_feature_snapshots`,
`scoring_batches`, `model_runs`, `customer_insights`에 결과를 저장합니다.

6. 테스트 계정 비밀번호를 직접 정하고 싶을 때만 `.env`에 다음을 추가하고
Backend를 재생성합니다. 값을 비워두면 `compose.yaml`의 기본값이 사용됩니다.
컨테이너는 **생성 시점**의 환경변수를 쓰므로 반드시 재생성해야 반영됩니다.

```env
TEST_ADMIN_PASSWORD=<12자 이상의 로컬 전용 비밀번호>
TEST_ANALYST_PASSWORD=<12자 이상의 로컬 전용 비밀번호>
TEST_OPERATIONS_PASSWORD=<12자 이상의 로컬 전용 비밀번호>
TEST_MARKETING_PASSWORD=<12자 이상의 로컬 전용 비밀번호>
```

```bash
docker compose up -d --force-recreate backend
```

테스트 계정 비밀번호는 저장소에 포함하지 않습니다.

7. 대상군·대조군 성과를 시연하려면 선택적으로 합성 Demo 데이터를 생성합니다.
   **4번에서 고객을 교체했다면 이 단계를 다시 실행해야** 캠페인이 새 고객에
   연결됩니다. 두 스크립트 모두 자기가 만든 캠페인을 지우고 새로 만듭니다.

```bash
docker compose exec backend python -m backend.scripts.seed_demo_campaign \
  --limit-per-campaign 60
docker compose exec backend python -m backend.scripts.seed_segment_scenarios
```

`[DEMO]` 캠페인 3개와 대상군·대조군, 전환·유지·매출 결과가 생성되고,
`[시나리오]` 캠페인 4개로 초안·처리중·관측대기·측정완료 업무 흐름이 재현됩니다.
시연 데이터는 로컬 개발 DB에서만 사용하며, 실제 비즈니스 성과 판단에 사용하지
않습니다. 자세한 내용은 [`docs/demo_data.md`](docs/demo_data.md)와
[`docs/campaign_workflow.md`](docs/campaign_workflow.md)를 확인합니다.

명령을 그대로 따라 검증한 실행 기록과 실측 수치는
[`docs/verified_setup_windows.md`](docs/verified_setup_windows.md)에 있습니다.

### 접속 주소

| 서비스 | 주소 | 용도 |
| --- | --- | --- |
| React Frontend | `http://127.0.0.1:5173` | 로그인·대시보드·캠페인 관리 |
| FastAPI Swagger | `http://127.0.0.1:8000/docs` | API 확인 |
| Backend 준비 상태 | `http://127.0.0.1:8000/ready` | 모델·DB 상태 확인 |
| MySQL | `127.0.0.1:3307` | Mac 호스트에서 직접 접속 |

Docker 내부 Backend의 DB 주소는 `mysql:3306`이며, Mac 호스트에서 직접 접속할
때만 `127.0.0.1:3307`을 사용합니다.

### 로그와 종료

```bash
docker compose logs -f backend
docker compose down
```

MySQL 데이터 볼륨까지 삭제하는 `docker compose down -v`는 로컬 DB를 초기화할
때만 사용합니다.

### 테스트

```bash
python -m pip install -r backend/requirements-dev.txt
project_venv/bin/python -m pytest backend/tests -q
```

Frontend 품질 검사는 Node.js 24와 pnpm 11이 필요하며, 자세한 명령은
[`frontend/README.md`](frontend/README.md)에서 확인할 수 있습니다.

## React 프론트엔드 환경

`frontend/`에는 React·TypeScript·Vite 기반의 반응형 팀 계정 로그인 화면이
구현되어 있습니다. API 클라이언트는 인증과 customer_insights 도메인별로
분리되어 있으며, 인증된 역할에 따라 분석·운영·마케팅·관리자 화면으로 분기합니다. 흰색 배경
중앙에 CardOps 로고와 로그인 폼을 배치했으며,
필수 입력값 검증, 비밀번호 표시 전환, 회원가입, MySQL 기반 로그인 연동을
제공합니다.

Node.js 24 LTS와 pnpm 11을 준비한 뒤 실행합니다.

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

Vite 개발 서버는 `/api`, `/live`, `/ready` 요청을
`http://127.0.0.1:8000`의 FastAPI로 전달합니다. 프론트엔드 파일 구조,
품질 검사와 OpenAPI 타입 생성 방법은
[`frontend/README.md`](frontend/README.md)에서 확인할 수 있습니다.

로그인·회원가입 요청은 Vite 프록시를 통해 FastAPI 인증 API로 전달됩니다.
인증 성공 시 Backend가 HttpOnly 쿠키를 발급하며, 역할별 전용 업무 화면으로
이동합니다. 자세한 인증 API와 화면 흐름은
[`backend/README.md`](backend/README.md)와 [`frontend/README.md`](frontend/README.md)를
확인합니다.

## 성능 대시보드 실행

세 모델 코드를 실행해 최신 결과를 만든 뒤 Streamlit 대시보드를 실행합니다.

```bash
source project_venv/bin/activate

python src/classification.py
python src/regression.py
python src/clustering.py

streamlit run dashboard/app.py
```

대시보드에서는 다음 내용을 확인할 수 있습니다.

- 분류: Train/Test 성능과 과적합 점검, 혼동행렬, ROC 곡선
- 회귀: Train/Test 성능과 과적합 점검, 실제값과 예측값, 잔차 분포
- 군집: Silhouette Score, 군집 프로파일, 군집별 고객 수와 이탈률

## 데이터 계보

- 원본: `data/raw/BankChurners.csv` — 10,127행, 23열
- 정제본: `data/processed/bankchurners_clean.csv` — 10,127행, 20열
- 정제 과정:
  - 식별자 `CLIENTNUM` 제거
  - 기존 모델 출력인 `Naive_Bayes_Classifier_..._1`, `_2` 제거
  - `Attrition_Flag`를 `Target`으로 변환
  - `Existing Customer=0`, `Attrited Customer=1`
  - `Unknown` 범주는 삭제·대체하지 않고 유지

자세한 데이터 설명과 무결성 정보는 `data/README.md`를 확인합니다.

## 평가 원칙

이탈 고객은 전체의 약 16.1%이므로 정확도만으로 모델을 평가하지 않습니다.

- 분류: Recall, Precision, F1, ROC-AUC, Lift/Gain
- 회귀: MAE, RMSE, R²
- 군집: Silhouette Score와 군집별 비즈니스 해석

## 주의사항

- `CLIENTNUM`과 두 개의 `Naive_Bayes_Classifier` 열은 모델 입력으로 사용하지 않습니다.
- 이 데이터는 시간 순서가 없는 공개·가공 데이터이므로 실제 운영 성능으로 직접 일반화하지 않습니다.
- 회귀 결과는 미래 LTV가 아니라 현재 데이터에 기반한 거래 활동성 분석으로 해석합니다.

## 데이터 출처

- https://www.kaggle.com/datasets/sakshigoyal7/credit-card-customers
