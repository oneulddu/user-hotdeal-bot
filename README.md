# user-hotdeal-bot

한국 커뮤니티 유저 핫딜 알림 봇 포크

## 주요 기능
한국 커뮤니티들의 유저 핫딜 게시판을 크롤링하여 텔레그램 등의 봇으로 알림

- 핫딜 종료, 제목 수정, 게시글 삭제 등 상태 변화 대응
- 나름 유연한 구조로 만들어 그럭저럭 괜찮은 확장성
- **FastAPI 기반 REST API 서버** - 핫딜 데이터를 외부 서비스에서 조회 가능
- **RSS/Atom 피드 제공** - 피드 리더에서 핫딜 구독 가능
- **데이터베이스 저장** - SQLite/MySQL 지원, 핫딜 이력 영속적 저장

### 크롤링 방식 안내
1. 아래 게시판들에 대해 각각 **1분에 한번씩** 크롤링 작업을 실행합니다. [(참조)](src/main.py#L522) 각 작업마다 게시판 글 목록 1페이지에 한하여 **단 한번** 요청을 합니다. (즉 각 게시판마다 기본적으로 **1분에 1회의 요청**이 가해지게 됩니다.)
2. 작업 간격은 정확히 1분을 목표로 하고 있으나, 현재 구조 한계상 매 작업마다 수 ms정도의 지연이 누적되고 있는 듯 합니다.
3. 현재 본 봇은 오라클 클라우드의 서울 리전 무료 인스턴스에서 가동중입니다.
4. 같은 사이트, 동일한 구조의 게시판인 경우 크롤러 소스코드 수정 없이 URL만 변경하여 사용할 수 있습니다.


- [루리웹 - 유저 예판 핫딜 뽐뿌 게시판](https://bbs.ruliweb.com/market/board/1020?view=thumbnail&page=1)
  - 썸네일 모드를 사용합니다.
- [뽐뿌 - 뽐뿌 게시판](https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu)
  - RSS 크롤러가 구현되어 있으나, 사용하지 않습니다.
- [뽐뿌 - 해외뽐뿌](https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu4)
  - RSS 크롤러가 구현되어 있으나, 사용하지 않습니다.
- [클리앙 - 알뜰구매](https://www.clien.net/service/board/jirum)
- [쿨앤조이 - 지름, 알뜰정보](https://coolenjoy.net/bbs/jirum)
  - RSS 사용 [(링크)](https://coolenjoy.net/bbs/rss.php?bo_table=jirum)
- [퀘이사존 - 지름/할인정보](https://quasarzone.com/bbs/qb_saleinfo)
- [아카라이브 - 핫딜 채널](https://arca.live/b/hotdeal)
- [다모앙 - 알뜰구매 게시판](https://damoang.net/economy)
- [에펨코리아 - 핫딜 게시판](https://www.fmkorea.com/hotdeal)


## Links
- [텔레그램 채널 (한국 커뮤니티 핫딜 모아보기)](https://t.me/hotdeal_kr)
- [패치로그](PATCHLOG.md)


## How to use

### Requirements
- Python>=3.11
- 주요 의존성:
  - `aiohttp`, `beautifulsoup4` - 크롤링
  - `python-telegram-bot>=21.6` - 텔레그램 봇
  - `fastapi`, `uvicorn` - API 서버
  - `sqlalchemy[asyncio]>=2.0`, `alembic` - 데이터베이스
  - `feedgen` - RSS/Atom 피드 생성
  - `aiosqlite`, `aiomysql` - 비동기 DB 드라이버
- 전체 의존성은 [pyproject.toml](pyproject.toml) 참조

### 실행 방법

#### 1. 크롤러 + 텔레그램 봇 (기본)
```bash
uv run -m src.main       # uv 사용 (권장)
python -m src.main       # pip 환경
```

#### 2. API 서버
```bash
uv run -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

#### 3. 환경 변수
```bash
# 데이터베이스 URL (기본값: SQLite)
export DATABASE_URL="sqlite+aiosqlite:///./data/hotdeal.db"

# MySQL 사용시
export DATABASE_URL="mysql+aiomysql://user:password@localhost/hotdeal"
```

### config: `config.yaml`
  - 크롤러, 봇, 로깅 설정을 하나의 yaml 파일로 관리. ([예시](/config.example.yaml))
  - `cp config.example.yaml config.yaml`
  - `crawler`, `bot`
    - `crawler_name`, `bot_name` 에 적는 클래스 이름은 각각 [crawler](/crawler/__init__.py), [bot](/bot.py) 모듈에 임포트되어있어야 함.
  - `logging`
    - `logging.config.dictConfig()` 참조 [(로깅 요리책: Logging cookbook)](https://docs.python.org/ko/3/howto/logging-cookbook.html#customizing-handlers-with-dictconfig)
    - logger 종류
      - `root`: 루트 로거
      - `bot.BotClassName`: 크롤러 로거. 각 크롤러 클래스의 이름 사용됨.
      - `crawler.CrawlerClassName`: 봇 로거. 각 봇 클래스의 이름 사용됨.
      - `status`: 로그 레벨과 관계없이 텔레그램 핸들러로 메시지를 보내야 할 때 사용하려고 만든 특수 로거
    - handler
      - [util.TelegramHandler](util.py#L11): `logging.handlers.HTTPHandler`를 상속해 간단히 만든 텔레그램 핸들러

### Run with Docker

프로젝트는 멀티 타겟 Docker 빌드를 지원합니다:
- `crawler` 타겟: 크롤러 + 텔레그램 봇
- `api` 타겟: FastAPI API 서버

#### docker-compose 사용 (권장)

```bash
# 모든 서비스 실행 (크롤러 + API 서버)
docker-compose up -d

# 크롤러만 실행
docker-compose up -d crawler

# API 서버만 실행
docker-compose up -d api
```

#### 개별 빌드 및 실행

```bash
# 크롤러 이미지 빌드
docker build -t user-hotdeal-bot:crawler --target crawler .

# DB 마이그레이션 실행 (최초 실행 및 스키마 변경 시)
docker run --rm --name user-hotdeal-bot-migrate \
  -v hotdeal-db:/app/data \
  -e DATABASE_URL=sqlite+aiosqlite:///./data/hotdeal.db \
  user-hotdeal-bot:crawler \
  alembic upgrade head

# 크롤러 실행
docker run -d --name user-hotdeal-bot-crawler \
  -v ./config.yaml:/app/config.yaml:ro \
  -v ./log:/app/log \
  -v ./dump.json:/app/dump.json \
  -v hotdeal-db:/app/data \
  -e DATABASE_URL=sqlite+aiosqlite:///./data/hotdeal.db \
  user-hotdeal-bot:crawler

# API 서버 빌드 및 실행
docker build -t user-hotdeal-bot:api --target api .
docker run -d --name user-hotdeal-bot-api \
  -p 8000:8000 \
  -v hotdeal-db:/app/data \
  -e DATABASE_URL=sqlite+aiosqlite:///./data/hotdeal.db \
  user-hotdeal-bot:api
```

#### Docker Volume
- `hotdeal-db`: SQLite 데이터베이스 파일 저장용 볼륨 (크롤러와 API 서버가 공유)

### MariaDB 백업 (supercronic + S3 호환 스토리지)
- 전용 이미지: `Dockerfile.backup` (supercronic + mariadb-client + awscli)
- 설정은 **환경 변수만 사용**합니다 (`config.yaml`은 읽지 않음).

| 환경 변수 | 용도 | 기본값/예시 |
| --- | --- | --- |
| `BACKUP_SCHEDULE` | supercronic 스케줄 (TZ 기준) | `0 18 * * *` |
| `TZ` | 컨테이너 타임존 | `UTC` (compose 예시는 `Asia/Seoul`) |
| `MARIADB_HOST` / `MARIADB_PORT` | DB 주소/포트 | `db` / `3306` |
| `MARIADB_USER` / `MARIADB_PASSWORD` | DB 계정 | `hotdeal` / `hotdealpassword` |
| `MARIADB_DATABASE` | 대상 DB명 | `hotdeal` |
| `S3_BUCKET` | 백업 저장 버킷 | 예: `hotdeal-backup` |
| `S3_PREFIX` | 객체 경로 prefix | `mariadb` |
| `S3_ENDPOINT` | MinIO 등 S3 호환 endpoint | `http://minio:9000` |
| `S3_REGION` | 리전 | `us-east-1` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | S3 자격 증명 | 필수 |
| `BACKUP_NAME_PREFIX` | 파일명 prefix | `hotdeal` |
| `BACKUP_TMP_DIR` | 임시 디렉터리 | `/tmp` |
| `RETENTION_DAYS` | S3 보관 일수 (삭제) | unset 시 미사용 |
| `BACKUP_TIMESTAMP_OVERRIDE` | 강제 타임스탬프 (테스트용) | unset |

- docker-compose 실행 예시: `docker-compose -f docker-compose.local.example.yml up -d backup` (MariaDB 서비스와 연동)
- 단독 실행 예시: `docker build -t user-hotdeal-backup -f Dockerfile.backup . && docker run --rm --env-file .env.backup user-hotdeal-backup`
- 복구 예시: `aws s3 cp s3://<bucket>/<prefix>/<file>.sql.gz - | gunzip | mysql -h <host> -u <user> -p<pass> <db>`

## API 서버

FastAPI 기반 REST API 서버로 핫딜 데이터를 외부에서 조회할 수 있습니다.

### 엔드포인트

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/` | API 정보 |
| GET | `/health` | 헬스체크 |
| GET | `/api/v1/articles` | 핫딜 목록 조회 (페이지네이션, 필터링 지원) |
| GET | `/api/v1/articles/{id}` | 핫딜 상세 조회 |
| GET | `/api/v1/crawlers` | 크롤러 목록 |
| GET | `/api/v1/crawlers/sites` | 사이트 목록 |
| GET | `/feed/rss.xml` | RSS 2.0 피드 |
| GET | `/feed/atom.xml` | Atom 피드 |

### 인증

- **API Key 인증**: `X-API-Key` 헤더 또는 `api_key` 쿼리 파라미터
- **게스트 모드**: API Key 없이 접근 시 Rate limiting 적용

### 데이터베이스

크롤링된 핫딜 데이터는 SQLAlchemy를 통해 데이터베이스에 저장됩니다.

- **지원 DB**: SQLite (기본), MySQL
- **마이그레이션**: Alembic 사용

```bash
# 마이그레이션 적용
uv run alembic upgrade head

# 새 마이그레이션 생성
uv run alembic revision --autogenerate -m "description"
```

## 구현 방식

### 크롤링
1. 각 크롤러 객체로부터 `ArticleCollection` 객체를 반환받음.
2. 직전 크롤링 작업시 받아왔던 (또는 앱 시작 시 역직렬화 했던) `ArticleCollection` 객체와 비교
3. 이후 `new`, `update`, `delete` 세가지 종류로 변경사항을 분류하여 `CrawlingResult` 객체로 묶음.
4. 만료된 (더이상 추적하지 않는) 게시글들을 메모리에서 제거. 크롤러에서는 `BaseArticle` 객체를, 각 봇에서는 `MessageType` (각 봇 메시지 객체의 제네릭 타입) 제거.
5. `CrawlingResult` 객체를 한데 묶어서 반환.

### 메시지 전송/수정/삭제
1. 새로 올라온 게시글, 수정된 게시글, 삭제된 게시글의 리스트들을 받음. (`list[BaseArticle]`)
2. 봇 객체 `queue` 속성에 `tuple[str, BaseArcile]` 형태로 작업을 등록.
3. 봇 객체 생성시부터 작동을 시작한 `consumer` 메서드에서 큐로부터 작업을 받아 전송/수정/삭제 작업을 수행.
4. 각 작업은 상속받은 각 구현 봇 클래스의 `_send`, `_edit`, `_delete` 메서드를 호출해 수행.

## TODO List
### 중요도 높음
- [ ] docstring 작성
- [ ] 게시글 추천수 보여주기 (진행중)
- [ ] **테스트 코드 작성**
- [ ] util.TelegramHandler 도 비동기적 / 멀티스레드에서 메시지 보내게 하기
- [ ] Asyncio Lock 또는 유사한 기능 추가
- [ ] disabled: true 상태인 크롤러/봇도 초기화는 하도록 변경
  - [ ] 크롤러/봇에 disabled 상태 추가
  - [ ] disabled 상태인 크롤러 및 봇은 작동하지 않게 변경

### 중요도 낮음
- [x] SQLite 사용 (v2.2.1에서 구현됨)
- [ ] (봇) 필터 기능 추가
- [ ] 일간 통계 기능 제공 등
- [ ] 실시간 봇 정보 모니터링 기능 추가 (텔레그램 메신저) - pid, 활성화된 크롤러 및 봇 목록, 최근 크롤러 상태 등
