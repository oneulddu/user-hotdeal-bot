# 🔥 user-hotdeal-bot

> 한국 커뮤니티 핫딜 게시판을 크롤링해 텔레그램으로 알리고, REST API · RSS/Atom 피드로 제공하는 봇

여러 커뮤니티의 유저 핫딜 게시판을 1분 간격으로 크롤링하여, 새 글·수정·삭제 등 상태 변화를 추적합니다. 수집한 핫딜은 텔레그램 메신저로 실시간 전송하고, 데이터베이스에 적재하여 REST API와 RSS/Atom 피드로도 노출합니다.

[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/ghcr.io-user--hotdeal--bot-2496ED?logo=docker&logoColor=white)](https://github.com/oneulddu/user-hotdeal-bot/pkgs/container/user-hotdeal-bot%2Fcrawler)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> [krepe90/user-hotdeal-bot](https://github.com/krepe90/user-hotdeal-bot)의 포크로, REST API 서버 · RSS/Atom 피드 · 데이터베이스 영속화 기능을 추가했습니다.

📢 **[텔레그램 채널 (한국 커뮤니티 핫딜 모아보기)](https://t.me/hotdeal_kr)** 에서 실제 동작을 확인할 수 있습니다.

<br>

## ✨ 특징

- **상태 변화 추적** — 신규 게시글뿐 아니라 핫딜 종료, 제목 수정, 게시글 삭제까지 감지해 보낸 메시지를 갱신/삭제합니다.
- **텔레그램 알림** — 수집 즉시 메신저로 전송하며, 재시작 후에도 수정·삭제를 이어갑니다.
- **REST API 서버** — FastAPI 기반으로 핫딜 데이터를 외부 서비스에서 조회할 수 있습니다.
- **RSS / Atom 피드** — 피드 리더에서 핫딜을 구독할 수 있습니다.
- **데이터베이스 영속화** — SQLite(기본)·MySQL/MariaDB를 지원하며 Alembic으로 스키마를 관리합니다.
- **유연한 확장 구조** — `BaseCrawler` / `BaseBot` 추상 클래스 기반이라 크롤러·봇을 손쉽게 추가할 수 있습니다.
- **선언적 설정** — 크롤러·봇·로깅 설정을 `config.yaml` 하나로 관리합니다.
- **가벼운 부하** — 게시판당 기본 **1분에 1회**, 글 목록 첫 페이지만 요청합니다.

> 현재 봇은 오라클 클라우드 서울 리전 무료 인스턴스에서 가동 중입니다.

<br>

## 🛒 지원 게시판

| 사이트 | 게시판 | 크롤러 클래스 |
| --- | --- | --- |
| [루리웹](https://bbs.ruliweb.com/market/board/1020) | 유저 예판 핫딜 뽐뿌 | `RuliwebCrawler` |
| [뽐뿌](https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu) | 뽐뿌 / 해외뽐뿌 | `PpomppuCrawler`, `PpomppuRSSCrawler` |
| [클리앙](https://www.clien.net/service/board/jirum) | 알뜰구매 | `ClienCrawler` |
| [쿨앤조이](https://coolenjoy.net/bbs/jirum) | 지름/알뜰정보 | `CoolenjoyCrawler`, `CoolenjoyRSSCrawler` |
| [퀘이사존](https://quasarzone.com/bbs/qb_saleinfo) | 지름/할인정보 | `QuasarzoneCrawler`, `QuasarzoneMobileCrawler` |
| [아카라이브](https://arca.live/b/hotdeal) | 핫딜 채널 | `ArcaLiveCrawler`, `ArcaLiveCrawlerV15`, `ArcaLiveCrawlerV2` |
| [다모앙](https://damoang.net/economy) | 알뜰구매 | `DamoangCrawler` |
| [에펨코리아](https://www.fmkorea.com/hotdeal) | 핫딜 | `FmkoreaCrawler` |
| [zod](https://zod.kr/deal) | 특가 | `ZodCrawler` |

> 같은 사이트의 동일 구조 게시판이라면 소스 수정 없이 `url_list` 만 바꿔 재사용할 수 있습니다.
> RSS가 구현된 크롤러는 일반/RSS 두 방식 중 선택해서 쓸 수 있습니다.

<br>

## 🧱 구성 요소

| 컴포넌트 | 진입점 | 역할 |
| --- | --- | --- |
| **Crawler + Bot** | [`src/main.py`](src/main.py) | 게시판 크롤링, 변화 추적, 텔레그램 전송, DB 적재 |
| **API 서버** | [`src/api/main.py`](src/api/main.py) | 핫딜 조회 REST API + RSS/Atom 피드 (FastAPI) |
| **Database** | [`src/db`](src/db) | SQLAlchemy 모델·저장소, Alembic 마이그레이션 |

<br>

## 🚀 시작하기

### 1. 설정 파일 준비

```bash
cp config.example.yaml config.yaml
```

`config.yaml` 의 `bots.telegram.kwargs` 에 [BotFather](https://t.me/BotFather)에서 발급받은 토큰과 전송 대상을 입력합니다.

```yaml
bots:
  telegram:
    bot_name: TelegramBot
    kwargs:
      token: TELEGRAM_API_TOKEN       # 봇 API 토큰
      target: CHAT_ID_OR_CHANNEL_NAME # 채팅 ID 또는 채널 이름
    enabled: true
```

### 2. Docker Compose로 실행 (권장)

`docker-compose.yml` 은 **마이그레이션 → 크롤러 → API** 세 서비스를 정의하며, SQLite DB 볼륨(`hotdeal-db`)을 공유합니다.

```bash
docker compose up -d            # 전체 (migrate → crawler + api)
docker compose up -d crawler    # 크롤러 + 텔레그램 봇만
docker compose up -d api        # API 서버만 (http://localhost:8000)
```

> `migrate` 서비스가 먼저 `alembic upgrade head` 로 스키마를 적용한 뒤 크롤러·API가 기동됩니다.

운영 예시는 [`docker-compose.prod.example.yml`](docker-compose.prod.example.yml), 로컬 예시는 [`docker-compose.local.example.yml`](docker-compose.local.example.yml) 을 참고하세요.

<details>
<summary>Compose 없이 단독 <code>docker run</code> 으로 실행하기</summary>

Compose를 쓰지 않을 때는 **마이그레이션 → 크롤러 → (선택) API** 순서로 직접 실행합니다. 크롤러를 띄우기 전에 반드시 마이그레이션을 먼저 수행하세요.

```bash
# 0) 이미지 빌드
docker build -t user-hotdeal-bot:crawler --target crawler .
docker build -t user-hotdeal-bot:crawler-scrapling --target crawler-scrapling .  # 아카라이브 v2 실험용
docker build -t user-hotdeal-bot:api --target api .

# 1) DB 마이그레이션 (최초 1회 및 스키마 변경 시)
docker run --rm --name user-hotdeal-bot-migrate \
  -v hotdeal-db:/app/data \
  -e DATABASE_URL=sqlite+aiosqlite:///./data/hotdeal.db \
  user-hotdeal-bot:crawler \
  alembic upgrade head

# 2) 크롤러 + 텔레그램 봇
docker run -d --name user-hotdeal-bot-crawler \
  -e TZ=Asia/Seoul \
  -e DATABASE_URL=sqlite+aiosqlite:///./data/hotdeal.db \
  -v ./config.yaml:/app/config.yaml:ro \
  -v ./log:/app/log \
  -v ./dump.json:/app/dump.json \
  -v hotdeal-db:/app/data \
  user-hotdeal-bot:crawler

# 3) (선택) API 서버
docker run -d --name user-hotdeal-bot-api \
  -p 8000:8000 \
  -e DATABASE_URL=sqlite+aiosqlite:///./data/hotdeal.db \
  -v hotdeal-db:/app/data \
  user-hotdeal-bot:api
```

> 크롤러와 API는 `hotdeal-db` 볼륨으로 같은 SQLite DB를 공유합니다.

</details>

### 3. 로컬에서 실행 ([uv](https://docs.astral.sh/uv/))

```bash
uv sync
uv run alembic upgrade head                                   # DB 초기화
uv run -m src.main                                            # 크롤러 + 텔레그램 봇
uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8000   # API 서버
```

<br>

## ⚙️ 설정

### `config.yaml`

크롤러·봇·로깅 설정을 한 파일에서 관리합니다. ([예시](config.example.yaml))

| 섹션 | 설명 |
| --- | --- |
| `crawlers` | 크롤링할 게시판 목록. `crawler_name` 은 [`src/crawler`](src/crawler/__init__.py) 에 등록된 클래스명, `url_list` 는 대상 URL, `enabled` 로 on/off |
| `bots` | 메시지 전송 봇. `bot_name` 은 [`src/bot.py`](src/bot.py) 에 정의된 클래스명, `kwargs` 로 토큰 등 전달 |
| `logging` | [`logging.config.dictConfig`](https://docs.python.org/ko/3/howto/logging-cookbook.html#customizing-handlers-with-dictconfig) 형식의 로깅 설정 |
| `logfire` | [Logfire](https://logfire.pydantic.dev/) 원격 로깅 설정 (기본 `enabled: false`) |

아카라이브가 Cloudflare 챌린지로 403을 반환하는 환경에서는 V1.5 또는 실험용 Scrapling 크롤러를 사용할 수 있습니다. V1.5는 `curl_cffi`로 Chrome TLS fingerprint를 흉내내며, 브라우저를 띄우는 V2보다 가볍습니다.

```yaml
crawlers:
  arcalive_hotdeal:
    enabled: false
  arcalive_hotdeal_v15:
    url_list:
    - https://arca.live/b/hotdeal
    crawler_name: ArcaLiveCrawlerV15
    enabled: true
    # 필요 시 프록시를 추가
    # proxy: http://127.0.0.1:8080
  arcalive_hotdeal_v2:
    url_list:
    - https://arca.live/b/hotdeal
    crawler_name: ArcaLiveCrawlerV2
    enabled: false
    # 필요 시 같은 실행 환경에서 얻은 쿠키 또는 프록시를 추가
    # cookie_env: ARCALIVE_COOKIE
    # proxy: http://127.0.0.1:8080
```

Docker에서는 브라우저 의존성이 포함된 이미지를 빌드해야 합니다.

```bash
docker build -t user-hotdeal-bot:crawler-scrapling --target crawler-scrapling .
```

### 주요 환경 변수

| 변수 | 설명 | 기본값 |
| --- | --- | --- |
| `DATABASE_URL` | DB 접속 URL | `sqlite+aiosqlite:///./data/hotdeal.db` |
| `TZ` | 타임존 | `UTC` (compose 예시는 `Asia/Seoul`) |
| `API_CORS_ORIGINS` | API CORS 허용 출처 (쉼표 구분) | `*` |

```bash
# MySQL / MariaDB 사용 예시
export DATABASE_URL="mysql+aiomysql://user:password@localhost/hotdeal"
```

<br>

## 🌐 REST API

FastAPI 기반 API 서버로 수집된 핫딜을 조회할 수 있습니다. 기동 후 `/docs`(Swagger) · `/redoc` 에서 문서를 확인하세요.

| Method | Endpoint | 설명 |
| --- | --- | --- |
| `GET` | `/` | API 정보 |
| `GET` | `/health` | 헬스체크 |
| `GET` | `/api/v1/articles` | 핫딜 목록 (페이지네이션·필터링) |
| `GET` | `/api/v1/articles/{id}` | 핫딜 상세 |
| `GET` | `/api/v1/crawlers` | 크롤러 목록 |
| `GET` | `/api/v1/crawlers/sites` | 사이트 목록 |
| `GET` | `/feed/rss.xml` | RSS 2.0 피드 |
| `GET` | `/feed/atom.xml` | Atom 피드 |

**인증** — `X-API-Key` 헤더 또는 `api_key` 쿼리 파라미터로 API Key를 전달합니다. 키 없이 접근하는 게스트에는 IP 기준 Rate limit이 적용됩니다.

<br>

## 🗄️ 데이터베이스

크롤링된 핫딜은 SQLAlchemy(async)를 통해 DB에 적재되고 Alembic으로 스키마를 관리합니다. 게시글 ID는 ULID를 사용합니다.

- **지원 DB**: SQLite(기본), MySQL / MariaDB
- **주요 테이블**: `articles`(핫딜), `api_keys`(API 키), `guest_rate_limits`(게스트 제한)

```bash
uv run alembic upgrade head                          # 마이그레이션 적용
uv run alembic revision --autogenerate -m "메시지"   # 새 마이그레이션 생성
```

<br>

## 💾 MariaDB 백업 (선택)

`Dockerfile.backup` 으로 빌드하는 전용 이미지가 supercronic 스케줄에 따라 MariaDB를 덤프하여 S3 호환 스토리지에 업로드합니다. 설정은 **환경 변수만** 사용합니다(`config.yaml` 미사용).

<details>
<summary>백업 환경 변수 펼치기</summary>

| 환경 변수 | 용도 | 기본값/예시 |
| --- | --- | --- |
| `BACKUP_SCHEDULE` | supercronic 스케줄 (TZ 기준) | `0 18 * * *` |
| `TZ` | 컨테이너 타임존 | `UTC` |
| `MARIADB_HOST` / `MARIADB_PORT` | DB 주소/포트 | `db` / `3306` |
| `MARIADB_USER` / `MARIADB_PASSWORD` | DB 계정 | `hotdeal` / `hotdealpassword` |
| `MARIADB_DATABASE` | 대상 DB명 | `hotdeal` |
| `S3_BUCKET` | 백업 저장 버킷 | 예: `hotdeal-backup` |
| `S3_PREFIX` | 객체 경로 prefix | `mariadb` |
| `S3_ENDPOINT` | S3 호환 endpoint (MinIO 등) | `http://minio:9000` |
| `S3_REGION` | 리전 | `us-east-1` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | S3 자격 증명 | 필수 |
| `BACKUP_NAME_PREFIX` | 파일명 prefix | `hotdeal` |
| `BACKUP_TMP_DIR` | 임시 디렉터리 | `/tmp` |
| `RETENTION_DAYS` | S3 보관 일수 (초과분 삭제) | unset 시 미사용 |
| `BACKUP_TIMESTAMP_OVERRIDE` | 강제 타임스탬프 (테스트용) | unset |

```bash
# 단독 실행
docker build -t user-hotdeal-backup -f Dockerfile.backup .
docker run --rm --env-file .env.backup user-hotdeal-backup

# 복구
aws s3 cp s3://<bucket>/<prefix>/<file>.sql.gz - | gunzip | mysql -h <host> -u <user> -p<pass> <db>
```

</details>

<br>

## 🧩 동작 구조

### 크롤링

```
[게시판] --요청--> [Crawler] --> ArticleCollection
                                        │
                       직전 결과와 비교 (diff)
                                        ▼
                  new / update / remove 로 분류 → CrawlingResult
```

1. 각 크롤러가 게시판을 파싱해 `ArticleCollection` 을 반환합니다.
2. 직전 사이클(또는 시작 시 `dump.json` 에서 복원)의 결과와 비교합니다.
3. 변경사항을 `new` / `update` / `remove` 로 분류합니다.
4. 분류된 게시글을 DB에 반영하고, 추적이 끝난 만료 게시글은 메모리에서 정리합니다.

### 메시지 전송

1. 분류된 게시글 목록을 봇의 작업 큐에 `(action, article)` 형태로 등록합니다.
2. 봇의 `consumer` 코루틴이 큐를 소비하며 `_send` / `_edit` / `_delete` 를 호출합니다.
3. 보낸 메시지 정보는 `dump.json` 으로 직렬화되어, 재시작 후에도 수정·삭제를 이어갈 수 있습니다.

전체 사이클은 [`run()`](src/main.py) 에서 **60초마다** 반복됩니다.

<br>

## 🛠️ 유틸리티

[`src/tools`](src/tools) 에 운영용 스크립트가 있습니다.

| 스크립트 | 용도 |
| --- | --- |
| [`crawler.py`](src/tools/crawler.py) | CLI에서 크롤러를 직접 실행해보는 테스트 도구 (`typer` 기반) |
| [`recovery.py`](src/tools/recovery.py) | 비정상 중단 후 밀린 핫딜 처리를 위해 `dump.json` 정리 |
| [`remove_message.py`](src/tools/remove_message.py) | 중복 전송된 메시지 삭제 |
| [`migration.py`](src/tools/migration.py) | 구버전 `dump.json` 포맷 마이그레이션 |

<br>

## 🧪 개발

```bash
uv sync            # 의존성 설치 (dev 포함)
uv run pytest      # 테스트 실행
uv run ruff check  # 린트
uv run ruff format # 포맷
```

| 항목 | 사용 기술 |
| --- | --- |
| 언어 | Python 3.11+ |
| 패키지 관리 | uv |
| 크롤링 | aiohttp, BeautifulSoup4, curl_cffi, Scrapling |
| 메신저 | python-telegram-bot 21+ |
| API | FastAPI, Uvicorn, feedgen |
| DB | SQLAlchemy 2.0(async), Alembic, SQLite / MySQL |
| 관측성 | Logfire |
| 테스트 / 품질 | pytest, ruff, pre-commit |

<br>

## 🔗 링크

- [텔레그램 채널 — 한국 커뮤니티 핫딜 모아보기](https://t.me/hotdeal_kr)
- [패치 로그 (PATCHLOG.md)](PATCHLOG.md)
- [원본 저장소 — krepe90/user-hotdeal-bot](https://github.com/krepe90/user-hotdeal-bot)

<br>

## 📄 라이선스

[MIT License](LICENSE) © Krepe.Z
