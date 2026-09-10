# 보고서 조회수 카운터

GitHub Pages는 정적 호스팅이라 서버에서 접속을 셀 수 없다. 이 폴더의 Cloudflare
Worker가 그 역할을 대신한다. 외부 카운터 서비스에 의존하지 않으므로 서비스가
없어질 걱정이 없고, 원본 데이터도 저장소 소유자 계정에 남는다.

| 파일 | 내용 |
| --- | --- |
| `worker.js` | Cloudflare Worker 본체. 대시보드 편집기에 그대로 붙여넣는다. |
| `schema.sql` | D1 테이블 정의. D1 Console에 그대로 붙여넣는다. |

세는 단위는 **페이지 키**다(`main` / `samsung` / `sk_hynix` / `china` / `metals` / `trends` / `interest`). 날짜별 보관본
(`reports/<날짜>.html`)은 해당 종목 키로 합산된다.

## 개인정보

**방문자 IP는 저장하지 않는다.** 재방문 판별에는 `비밀소금 + 날짜 + IP + UA`의
SHA-256 해시만 쓴다. 소금에 날짜가 섞여 있어 날짜가 바뀌면 같은 방문자도 다른 값이
되므로, 날짜를 넘는 추적은 구조적으로 불가능하고 **하루 단위 순방문자**만 셀 수 있다.
국가 코드는 Cloudflare가 제공하는 값을 쓰고(IP 자체는 남기지 않음), 유입경로는
도메인까지만 남긴다(전체 URL에는 검색어가 붙어 오는 일이 있다).

IP를 그대로 적재하도록 바꾸는 것도 기술적으로는 가능하지만, IP는 한국
개인정보보호법상 개인정보이고 EU 방문자에게는 GDPR이 적용된다. 보관하려면
개인정보처리방침 고지와 처리 근거·보관기간 관리가 따라붙는다.

## 설치

Node·wrangler 없이 대시보드만으로 끝난다.

### 1. D1 데이터베이스 만들기

대시보드 → **Storage & Databases → D1 SQL Database → Create**
이름은 `predict-stock-counter`.

만든 DB의 **Console** 탭에서 [`schema.sql`](schema.sql)의 문장을 **한 문장씩** 실행한다.

콘솔에 파일을 통째로 붙여넣으면 줄바꿈이 사라지면서 `--` 주석이 뒤따르는 코드까지
삼켜 `incomplete input: SQLITE_ERROR`가 난다. 주석을 빼고 한 줄로 만든 아래 다섯 문장을
차례로 실행하는 편이 확실하다(`schema.sql`과 같은 내용이다).

```sql
CREATE TABLE IF NOT EXISTS hits (id INTEGER PRIMARY KEY AUTOINCREMENT, page TEXT NOT NULL, ts TEXT NOT NULL, day TEXT NOT NULL, country TEXT NOT NULL DEFAULT '', region TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '', referrer TEXT NOT NULL DEFAULT '', ua TEXT NOT NULL DEFAULT '', visitor TEXT NOT NULL DEFAULT '');
```

```sql
CREATE INDEX IF NOT EXISTS idx_hits_day_page ON hits (day, page);
```

```sql
CREATE INDEX IF NOT EXISTS idx_hits_visitor ON hits (visitor, page, day);
```

```sql
CREATE INDEX IF NOT EXISTS idx_hits_day_geo ON hits (day, region, city);
```

```sql
CREATE TABLE IF NOT EXISTS counters (page TEXT PRIMARY KEY, total INTEGER NOT NULL DEFAULT 0);
```

```sql
INSERT OR IGNORE INTO counters (page, total) VALUES ('main', 0), ('samsung', 0), ('sk_hynix', 0), ('china', 0), ('metals', 0), ('trends', 0), ('interest', 0);
```

`SELECT * FROM counters;` 가 7행을 0으로 돌려주면 정상이다.

**이미 예전 안내로 표를 만들었다면** `region`·`city` 열과 인덱스가 없어 `/hit`가
`no such column: region`으로 실패한다(보고서의 조회수가 "—"로만 보인다). 아래를 한 문장씩
실행하면 된다.

```sql
ALTER TABLE hits ADD COLUMN region TEXT NOT NULL DEFAULT '';
```

```sql
ALTER TABLE hits ADD COLUMN city TEXT NOT NULL DEFAULT '';
```

```sql
CREATE INDEX IF NOT EXISTS idx_hits_visitor ON hits (visitor, page, day);
```

```sql
CREATE INDEX IF NOT EXISTS idx_hits_day_geo ON hits (day, region, city);
```

```sql
INSERT OR IGNORE INTO counters (page, total) VALUES ('china', 0), ('metals', 0), ('trends', 0), ('interest', 0);
```

### 2. Worker 만들기

대시보드 → **Workers & Pages → Create → Start with Hello World → Deploy**
이름은 `predict-stock-counter`.

배포되면 **Edit code**로 들어가 기본 코드를 지우고 [`worker.js`](worker.js) 내용을
붙여넣은 뒤 다시 **Deploy**.

### 3. 바인딩과 시크릿

Worker → **Settings → Bindings**

- **D1 database** 추가 — Variable name `DB`, 앞서 만든 `predict-stock-counter`

Worker → **Settings → Variables and Secrets** (둘 다 Type은 **Secret**)

- `VISITOR_SALT` — 방문자 해시용 비밀값
- `STATS_TOKEN` — `/stats` 접근 토큰

값은 아래처럼 만들어 쓴다(각각 다른 값으로).

```bash
openssl rand -hex 32
```

시크릿을 바꾸면 그 시점부터 방문자 해시가 달라진다. 누적 조회수는 영향받지 않는다.

### 4. 페이지에 Worker 주소 넣기

이 저장소에는 이미 배포된 주소가 들어가 있다.

```
https://predict-stock-counter.kimname1.workers.dev
```

Worker를 다시 만들어 주소가 바뀌었다면 아래처럼 한 번에 바꾼다.

```bash
grep -rl 'predict-stock-counter\.[a-z0-9]*\.workers\.dev' -r . --exclude-dir=.git \
  | xargs sed -i '' 's/predict-stock-counter\.[a-z0-9]*\.workers\.dev/predict-stock-counter.<새 서브도메인>.workers.dev/g'
```

주소가 들어가는 곳은 네 군데다.

- `docs/index.html` — 메인 페이지
- `docs/samsung/`, `docs/sk_hynix/` 의 `index.html`과 `reports/*.html` — 이미 발행된 보고서
- `samsung_direction_model_colab.ipynb` 첫 셀의 `COUNTER_ENDPOINT` — 이후 실행에서 생성될 보고서

카운터를 끄려면 주소를 빈 문자열로 두면 된다. 노트북은 `COUNTER_ENDPOINT = ""`,
페이지 쪽은 스크립트의 `E`/`ENDPOINT` 값을 비우면 호출하지 않는다(페이지는 정상 표시).

### 5. 커밋과 배포

```bash
git add -A && git commit -m "feat: 보고서 페이지에 조회수 표시" && git push
```

GitHub Pages 반영에 1~2분 걸린다.

### 6. (선택) 채점 워크플로 정시 호출

GitHub의 cron은 이 저장소에서 예정보다 4~5시간 늦게 실행을 만든다(2026-09-08~10 사흘 모두 09:37 회차가
14:10에, 16:10 회차가 21:06~21:18에 실행). Cloudflare의 Cron Trigger는 분 단위로 정확하므로, 이 Worker가
그 시각에 GitHub의 `workflow_dispatch` API로 채점 워크플로(`afternoon-report.yml`)를 깨운다. 토큰이 없으면
이 기능은 꺼져 있고 카운터만 동작한다.

1. **토큰 만들기** — GitHub → Settings → Developer settings → Personal access tokens →
   **Fine-grained tokens** → Generate new token.
   - Repository access: **Only select repositories** → `predict_stock` 하나
   - Permissions → Repository permissions → **Actions: Read and write** (Metadata: Read는 자동으로 붙는다).
     다른 권한은 주지 않는다.
   - Expiration: 만료일을 정하고 달력에 적어 둔다. 만료되면 정시 호출만 멈추고 GitHub cron 백업은 계속 돈다.

   토큰은 생성 화면에서 한 번만 보인다. 저장소·노트북·채팅 어디에도 붙여 넣지 않는다.
2. **Worker에 시크릿 넣기** — Worker → **Settings → Variables and Secrets** → Add → Type **Secret**,
   이름 `GITHUB_DISPATCH_TOKEN`, 값은 위 토큰.
3. **Cron Trigger 두 개 추가** — Worker → **Settings → Triggers → Cron Triggers** → Add. 입력 방식에서
   "Execute worker every"(간격) 대신 **Cron** 식(custom expression)을 고르고 UTC로 넣는다.
   - `37 0 * * 1-5` — 09:37 KST 시가 채점
   - `10 7 * * 1-5` — 16:10 KST 마감 채점·회고

   기존의 보관기간 정리 트리거는 그대로 둔다(그 시각에는 정리만 한다).
4. **Worker 다시 배포** — Edit code에 `worker.js`의 최신 내용을 붙여넣고 Deploy. 배포하지 않으면
   트리거가 와도 옛 코드가 돈다.
5. **확인** — 다음 평일 16:10 KST 직후 GitHub → Actions → "채점 갱신 (개장 후·마감 후)"에
   `채점 갱신 · cloudflare-cron` 실행이 생기면 된다. Worker → Logs에서
   `workflow_dispatch {"status":"dispatched","code":204}` 를 볼 수 있다. `code` 가 401·403이면 토큰
   권한(Actions: write)이나 만료를, 404면 저장소 접근 범위를 확인한다.

토큰이 새도 할 수 있는 일은 이 저장소의 워크플로를 실행시키는 것뿐이다(코드·원장 변경 불가).
그래도 의심되면 GitHub에서 토큰을 폐기하고 새로 만들어 시크릿을 바꾼다.

## 통계 보기

### 대시보드

```
https://namyikim.github.io/predict_stock/admin/
```

`STATS_TOKEN`을 입력하면 요약·일별 추이·페이지별 누적·국가·유입 경로를 그래프로 보여준다.
토큰은 브라우저를 벗어나지 않고, 저장 체크박스를 켰을 때만 이 브라우저의 localStorage에
남는다. 페이지 자체는 공개되어 있지만 토큰 없이는 아무 데이터도 나오지 않는다.
검색엔진에는 `noindex`로 막아 두었다.

### API 직접 호출

```bash
curl -H "Authorization: Bearer <STATS_TOKEN>" "https://predict-stock-counter.kimname1.workers.dev/stats?days=30"
```

돌려주는 값은 다음과 같다.

- `totals` — 페이지별 누적 조회수
- `daily` — 날짜·페이지별 조회수와 순방문자 수
- `countries` — 국가별 조회수
- `referrers` — 유입 도메인별 조회수

D1 Console에서 직접 SQL을 돌려도 된다.

```sql
-- 최근 14일 일별 추이
SELECT day, page, COUNT(*) AS views, COUNT(DISTINCT visitor) AS visitors
FROM hits WHERE day >= date('now', '-14 day')
GROUP BY day, page ORDER BY day DESC;
```

## 위치 정보

`hits.region`(시/도)과 `hits.city`(시/군/구)는 Cloudflare가 IP로 추정해 붙여 주는 값이다.
정확도의 한계가 분명하다.

- **시/도**는 대체로 맞는다. 분포를 보는 용도로는 쓸 만하다.
- **시/군/구**는 참고용이다. 모바일 통신사 NAT나 회사 회선을 거치면 실제와 크게 다르다.
- **동 단위는 얻을 수 없다.** IP 기반 추정의 해상도 한계이며, 브라우저 위치권한(GPS)을
  받지 않는 한 방법이 없다. 좌표도 도시 중심점이라 저장하지 않는다.

`/geo` 엔드포인트는 **호출자 자신의** 위치만 돌려준다(다른 방문자 정보는 나오지 않는다).
Cloudflare가 이 계정에서 시/도·도시를 실제로 채워 주는지 확인할 때 쓴다.

```bash
curl "https://predict-stock-counter.kimname1.workers.dev/geo"
```

## 운영 메모

- **봇 제외** — User-Agent로 명백한 크롤러를 걸러 집계에서 뺀다. 봇에게도 현재
  숫자는 돌려주되 카운트는 올리지 않는다.
- **오리진 제한** — `worker.js`의 `ALLOWED_ORIGINS`에 있는 사이트에서 온 요청만
  받는다. 페이지 키도 `ALLOWED_PAGES` 화이트리스트로 고정했다. 종목을 추가하면
  이 두 곳과 `schema.sql`의 초기 INSERT를 같이 고쳐야 한다.
- **표 크기** — 조회 1건마다 `hits`에 한 행이 쌓인다. 오래된 원본이 필요 없으면
  주기적으로 지운다(누적 조회수는 `counters`에 따로 있어 영향받지 않는다).

  ```sql
  DELETE FROM hits WHERE day < date('now', '-365 day');
  ```

- **무료 플랜 한도** — 조회 1건당 D1 쓰기 2회(로그 1 + 누적 1)를 쓴다. 개인
  사이트 트래픽에서는 여유가 크지만, 한도 수치는 Cloudflare 문서에서 확인한다.

## Worker를 고친 뒤에는 반드시 다시 배포한다

`counter/worker.js`는 저장소에 커밋해도 자동으로 배포되지 않는다. Cloudflare 대시보드 →
Workers & Pages → 해당 Worker → Edit code → 내용을 붙여넣고 Deploy 해야 반영된다.

배포를 잊으면 admin 페이지의 일별 조회수만 비어 보인다(누적 조회수는 `counters` 표에서 오므로
정상으로 보인다). 2026-09-08에 실제로 그런 일이 있었다 — D1의 `hits`에는 하루 140여 건이
쌓이는데 화면만 비어 원인을 찾는 데 시간이 걸렸다. 지금은 `/stats` 응답에 `daily` 키가 아예
없으면 admin 페이지가 "배포된 Worker가 오래되었습니다"라고 알려 준다.

확인: admin 페이지의 "기간 조회수"·"기록된 날" 타일에 숫자가 뜨면 정상이다.

## /stats 응답 항목

| 키 | 내용 |
| --- | --- |
| `totals` | 페이지별 누적 조회수(전체 기간) |
| `daily` | 날짜 × 페이지 조회수·방문자 |
| `countries` / `regions` / `cities` | 기간 합계 |
| `regionsDaily` | **날짜 × 시/도** 조회수·방문자 (2026-09-08 추가) |
| `referrers` | 유입 경로 |

admin 페이지는 응답에 키가 없으면 "Worker가 오래되었습니다"로 안내한다. 새 키를 추가한 뒤에는
Cloudflare에 다시 배포해야 화면에 나온다.
