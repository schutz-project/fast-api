# SCHUTZ 직원 포털 API

SCHUTZ에서 관측하는 직원 포털의 FastAPI 애플리케이션입니다. 로그인·직원 검색·로그아웃, 상태 점검과 내부 업무 점검 경로를 제공합니다. 관리 웹 및 Worker는 별도 저장소에서 관리합니다.

## 코드와 이미지

| 경로 | 역할 |
|---|---|
| `portal/main.py` | `portal.main:create_app` 앱 팩토리와 API 라우트 |
| `portal/security.py`, `portal/config.py` | 인증·세션 및 환경 설정 |
| `portal/db.py`, `portal/models.py` | DB 연결과 모델 |
| `portal/seed.py` | 초기 데이터 관련 코드 |
| `portal/telemetry.py` | 애플리케이션 계측 |
| `migrations/`, `alembic.ini` | DB 마이그레이션 |
| `static/` | 포털 화면과 정적 리소스 |
| `tests/` | 포털 회귀 시험과 브라우저 검증 코드 |
| `deploy/Dockerfile` | 서비스 이미지 빌드 |
| `deploy/fastapi-release.sh`, `deploy/auto-release.py` | 기존 배포·복구 경로 |
| `scripts/db-restore-check.py` | 격리된 DB 복원 점검 코드 |

저장소 루트에서 CI와 같은 Dockerfile로 빌드합니다.

```bash
docker build -f deploy/Dockerfile -t schutz-fast-api:ci .
```

이미지 기본 진입점은 Uvicorn의 `portal.main:create_app`이며 포트는 8000입니다. 실제 Nginx·DB·환경변수·포트 연결은 [infra 저장소](https://github.com/schutz-project/infra)의 서비스 구성을 함께 확인합니다.

## 주요 경로

- `GET /api/auth/context`, `POST /api/auth/login`, `POST /api/auth/logout`
- `GET /api/employees`
- `GET /health`, `GET /internal/check/employee`

경로마다 인증·CSRF 또는 내부 점검 조건이 다릅니다. 내부 점검 경로를 외부 공개하거나 시험 목적으로 실제 계정·운영 데이터를 변경하지 않습니다.

## 검증과 배포

개발 의존성은 `requirements-dev.txt`에 있습니다. `.github/workflows/ci.yml`은 Python 회귀 시험, 이미지 빌드와 PostgreSQL 시험을 정의합니다. 기존 main push 및 수동 실행에는 조건에 따라 ECR 게시·EC2 배포를 수행하는 job도 있습니다.

`.github/workflows/`의 개별 진단·백업·릴리스 Workflow는 이름만으로 미사용이라고 판단하지 않습니다. 실제 실행 경로를 정리할 때 별도로 대조합니다. `deploy/rotate-portal-logs.sh`의 외부 사용 여부도 확인 전까지 유지합니다.

## 관련 저장소와 비밀정보

- [management-web](https://github.com/schutz-project/management-web): 관리자 대시보드와 사건 처리
- [management-worker](https://github.com/schutz-project/management-worker): 수집·탐지·AI·Slack 처리
- [infra](https://github.com/schutz-project/infra): Nginx·Compose·모니터링·Terraform

`.env`, DB 접속 비밀번호, 앱·프록시 비밀값, 내부 점검 토큰과 세션 정보를 커밋하거나 로그에 출력하지 않습니다. DB 초기화와 마이그레이션은 문서 정리 또는 이미지 빌드의 일부로 자동 실행하지 않습니다.
