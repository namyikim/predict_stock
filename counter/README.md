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
   이름 `GH_DISPATCH_TOKEN` 또는 `GITHUB_DISPATCH_TOKEN`(둘 중 있는 쪽을 읽는다), 값은 위 토큰.
3. **Cron Trigger 추가** — Worker → **Settings → Triggers → Cron Triggers** → Add. 입력 방식에서
   "Execute worker every"(간격) 대신 **Cron** 식(custom expression)을 고르고 UTC로 넣는다.
   - `37 0 * * MON-FRI` — 09:37 KST 시가 채점
   - `10,45 7 * * MON-FRI` — 16:10 KST 마감 채점·회고, 16:45 KST 회고 재시도(네이버 투자자별 매매가 16:10 에는 아직 없다)
   - `30 8 * * MON-FRI` — 17:30 KST 회고 재시도

   **트리거는 계정당 5개까지다**(2026-10-02 확인: 여섯 번째를 넣으려 하면 거절된다). 그래서 16:10과 16:45를 한 식으로
   합쳤다. Worker는 트리거 식이 아니라 발화 시각으로 할 일을 정하므로 합쳐도 동작이 같다. 지금 쓰는 5개는
   보관기간 정리(`0 3 * * *`) + 위 셋 + 7번의 아침 호출이다. 더 늘리려면 다시 합쳐야 한다.

   **요일은 반드시 이름(MON-FRI)으로 적는다.** Cloudflare는 요일 숫자를 표준 cron과 다르게 세어 `1-5`를 **일~목**으로
   해석한다(2026-09-30 확인: 예정 목록에 일요일이 있고 금요일이 없었다). 저장한 뒤 "Estimated upcoming events"에
   금요일이 들어오고 일요일이 빠졌는지 확인한다.

   기존의 보관기간 정리 트리거는 그대로 둔다(그 시각에는 정리만 한다).
4. **Worker 다시 배포** — Edit code에 `worker.js`의 최신 내용을 붙여넣고 Deploy. 배포하지 않으면
   트리거가 와도 옛 코드가 돈다.
5. **확인** — 다음 평일 16:10 KST 직후 GitHub → Actions → "채점 갱신 (개장 후·마감 후)"에
   `채점 갱신 · cloudflare-cron` 실행이 생기면 된다. Worker → Logs에서
   `workflow_dispatch {"status":"dispatched","code":204}` 를 볼 수 있다. `code` 가 401·403이면 토큰
   권한(Actions: write)이나 만료를, 404면 저장소 접근 범위를 확인한다.

토큰이 새도 할 수 있는 일은 이 저장소의 워크플로를 실행시키는 것뿐이다(코드·원장 변경 불가).
그래도 의심되면 GitHub에서 토큰을 폐기하고 새로 만들어 시크릿을 바꾼다.

### 7. 아침 보고서 정시 호출

GitHub의 cron은 아침 회차도 빠뜨린다. 2026-10-02에는 06:22~07:52 KST의 11개 회차가 **하나도 만들어지지 않아**
종목 보고서가 08:23에 손으로 돌려서야 나왔다(9/30은 08:17, 10/1은 아침 기록 없음). 장 시작 전 예측은 09:00을
넘기면 사전 예측으로 인정되지 않으므로, 채점처럼 이 Worker가 정시에 일일 보고서 워크플로(`daily-report.yml`)를 부른다.

| KST | UTC | 하는 일 |
| --- | --- | --- |
| 06:20 (월~금) | 21:20 (일~목) | 본 호출 — 종목 보고서와 보조 보고서(뉴스·금은·중국 등) |
| 07:00 · 07:40 | 22:00 · 22:40 | 재시도 — 오늘 예측이 이미 기록됐으면 몇 초 만에 끝난다. 보조 보고서는 다시 만들지 않는다 |

토큰은 6번에서 넣은 것을 그대로 쓴다(같은 저장소의 Actions 권한). 할 일은 두 가지다.

1. **Cron Trigger 하나 추가** — Worker → Settings → Triggers → Cron Triggers → Add → Cron 식:
   - `*/20 21-22 * * SUN-THU`

   21:00~22:40 UTC에 20분마다 여섯 번 발화하지만 Worker는 위 세 시각에만 GitHub를 부른다(나머지 발화는 보관기간
   정리만 한다). **요일은 이름(SUN-THU)으로 적는다** — KST 월~금 아침은 UTC로 일~목 저녁이다.
   트리거는 계정당 5개까지라 6번의 16:10·16:45를 `10,45 7 * * MON-FRI` 하나로 합쳐 자리를 만들었다(2026-10-02).
2. **Worker 다시 배포** — `worker.js` 전체를 붙여넣고 Deploy.

확인: 다음 평일 06:20 KST 직후 GitHub → Actions에 `일일 보고서 · 정시 호출` 실행이 생기면 된다. Worker → Logs에는
`workflow_dispatch {"status":"dispatched","code":204,"workflow":"daily-report.yml","retry":false}` 가 남는다.
GitHub cron(06:22~07:52)은 백업으로 그대로 둔다 — 늦게 도착해도 오늘 예측이 이미 있으면 건너뛴다.

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

## 구독 신청 (2026-09-28)

메인·삼성전자·SK하이닉스 페이지 맨 위의 **구독** 버튼을 누르면 이메일 입력칸이 열린다. 신청은
`POST /subscribe`로 이 Worker에 오고 D1의 `subscribers` 표에 (주소, 페이지)마다 한 행으로 남는다.
목록은 관리자 페이지의 **구독자** 메뉴에서 `STATS_TOKEN`으로 보고, CSV로 받고, 한 건씩 지울 수 있다.

### 켜는 법 (한 번)

1. D1 Console에서 아래 세 문장을 한 문장씩 실행한다(`schema.sql` 끝부분과 같다).

   ```sql
   CREATE TABLE IF NOT EXISTS subscribers (email TEXT NOT NULL, page TEXT NOT NULL, ts TEXT NOT NULL, PRIMARY KEY (email, page));
   ```

   ```sql
   CREATE TABLE IF NOT EXISTS subscribe_log (visitor TEXT NOT NULL, day TEXT NOT NULL);
   ```

   ```sql
   CREATE INDEX IF NOT EXISTS idx_subscribe_log ON subscribe_log (visitor, day);
   ```

2. `worker.js`를 대시보드에 붙여넣고 Deploy 한다(아래 "Worker를 고친 뒤에는…" 참고). 배포 전에는
   구독 버튼을 눌러도 "신청을 보내지 못했습니다"가 뜬다.

### 지키는 것

- **수집 항목은 이메일·신청한 페이지·신청 시각뿐이다.** IP·위치는 남기지 않는다. 입력칸 아래에
  수집 항목·목적·보관 기간을 적고, 동의 체크를 해야 보낼 수 있다.
- **응답으로 가입 여부를 알 수 없다.** 새 신청·중복 신청·해지·없는 주소 해지가 모두 같은 응답이다.
- **남용 방지** — 허용 출처에서 온 요청만 받고, 하루 단위 방문자 해시마다 하루 10건까지 받는다.
  사람에게 보이지 않는 칸(`website`)이 채워진 신청은 받은 척만 하고 저장하지 않는다.
- **해지** — 같은 입력창의 "구독 해지"로 본인이 지울 수 있다. 관리자 페이지에서도 지울 수 있다.
- **이메일 소유 확인은 아직 없다.** 기존에 동의를 받아 저장한 구독을 발송 대상으로 사용한다. 확인 메일(더블 옵트인)은 별도 개선 항목이다.

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


## 보고서 갱신 이메일 (2026-10-04)

**시스템이 개장 전 보고서와 마감 후 회고를 게시하면, 수신 주소별 하루 최대 두 통**을 보낸다.
한국시간을 기준으로 개장 전 한 통(09:00 전), 마감 후 한 통(15:30 이후)이다.
등록된 갱신은 Worker의 다음 Cron 실행에서 발송하며, 설정 저장은 메일을 보내지 않는다.

- 개장 전: 대표 모델 방향·상승/보합/하락 확률, 같은 실행의 예상 시초가·종가.
- 마감 후: 종가·등락률·시초가 갭·장중 변화, 장 흐름·수급, 아침 예측 검증과 예상 구간 평가.
- 메인 구독자는 두 종목과 **금·은·중국·거시 경제·AI 뉴스·로봇 뉴스·급상승 검색어·장기 관심도**를 한 통에 받는다. 공개 메뉴의 핵심 내용·자료 기준 시각·전체 보고서 링크를 담고, 두 종목 이벤트에 같은 메뉴가 있어도 한 번만 넣는다. 종목 구독자는 신청한 종목만 받으며, 두 종목을 각각 구독한 주소도 한 통이다. 메인·종목 중복 신청도 추가 발송하지 않는다.
- 다른 메뉴는 종목 갱신을 접수하는 시점의 동일한 게시 리비전에서 읽는다. 메뉴별 갱신 주기·자료 기준일이 다르므로 최신 게시본의 시각을 표시하며, 읽지 못한 메뉴는 확인 불가로 표시한다. 관리자·실험실 정보는 포함하지 않는다. 메인 구독을 해지했다면 종목 구독이 남아 있어도 기존 전체 메뉴 묶음의 재시도는 취소한다.
- 한 회차에서 구독한 모든 종목이 준비되어야 보낸다. 한 종목이 끝내 준비되지 않으면 묶음 메일은 만료된다. 내용 보완·소스 리비전 변경으로 세 번째 메일을 보내지 않는다.
- 회차의 첫 자동 게시 당시 구독한 사람만 대상이다. 이후 가입은 다음 회차부터 적용한다. 해당 한국 날짜의 보고서만 등록하며 지난 날짜 대기를 다음 날 이월하지 않는다.
- GitHub `schedule` 또는 Worker Cron이 서명한 자동 호출의 **첫 시도**만 등록한다. 수동 Run workflow·Re-run jobs·push·관리자 ‘지금 호출’·`DISPATCH_FORCE` 시험 호출에서는 등록하지 않는다. 장중 시가 채점도 대상이 아니다.
- 발신 주소는 **관리자 → 구독자 → 보고서 이메일 발송 설정**에서 변경한다. 새 메일에 적용하며, 이미 시도한 메일의 재시도는 중복 접수 방지를 위해 기존 발신 주소와 본문을 유지한다. 빈 값 저장은 발송 중지다.

### 한 번 필요한 설정

1. 아래 SQL을 D1 Console에서 **한 문장씩** 실행한다. 기존 구독자는 유지된다. 이전 이메일 기능을 설치했다면 없는 표만 추가한다. 발송 관리 기능에는 `mail_controls`도 필요하다.

```sql
CREATE TABLE IF NOT EXISTS mail_events (id TEXT PRIMARY KEY, target TEXT NOT NULL, phase TEXT NOT NULL, session_date TEXT NOT NULL, content TEXT NOT NULL, source_revision TEXT NOT NULL, created_at TEXT NOT NULL);
```

```sql
CREATE TABLE IF NOT EXISTS mail_deliveries (event_id TEXT NOT NULL, email TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, lease_until TEXT NOT NULL DEFAULT '', next_attempt TEXT NOT NULL DEFAULT '', sent_at TEXT, provider_id TEXT, error_code TEXT, PRIMARY KEY (event_id, email));
```

```sql
CREATE TABLE IF NOT EXISTS mail_unsubscribe (email TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE);
```

```sql
CREATE INDEX IF NOT EXISTS idx_mail_events_created ON mail_events(created_at);
```

```sql
CREATE INDEX IF NOT EXISTS idx_mail_deliveries_email ON mail_deliveries(email);
```

```sql
CREATE TABLE IF NOT EXISTS mail_editions (id TEXT PRIMARY KEY, day TEXT NOT NULL, phase TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(day,phase));
```

```sql
CREATE TABLE IF NOT EXISTS mail_settings (id INTEGER PRIMARY KEY CHECK(id=1), from_email TEXT NOT NULL, updated_at TEXT NOT NULL);
```

```sql
CREATE TABLE IF NOT EXISTS mail_controls (id TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
```

2. Gmail 발송은 Google 계정의 2단계 인증과 앱 비밀번호를 준비한다. 비밀번호는 Cloudflare Secret에 직접 입력하며 채팅이나 저장소에 남기지 않는다. Resend를 사용할 때는 발신 도메인을 인증한다.

3. Cloudflare Worker에 다음을 넣는다. **키와 토큰은 코드·GitHub Pages·채팅에 쓰지 않는다.**

| 종류 | 이름 | 값 |
| --- | --- | --- |
| Variable | `MAIL_PROVIDER` | Gmail은 `gmail`, Resend는 `resend`(기본값) |
| Variable | `GMAIL_USER` | 발신 Gmail 주소 |
| Secret | `GMAIL_APP_PASSWORD` | Gmail 앱 비밀번호 (Gmail 사용 시) |
| Secret | `RESEND_API_KEY` | Resend 발송 API 키 (Resend 사용 시) |
| Secret | `MAIL_PUBLISH_TOKEN` | 보고서 발행 전용의 긴 임의 토큰. `STATS_TOKEN`과 분리 |
| Variable | `MAIL_PUBLIC_URL` | `https://predict-stock-counter.kimname1.workers.dev` |
| Variable | `MAIL_ENABLED` | 준비 중 `0`, 준비 완료 후 `1` |

기존 `MAIL_FROM`은 관리자 저장 전까지만 호환용으로 읽는다. 새 설치에서는 필요 없다.
관리자에서 한 번 저장하면 그 값이 우선하며, 빈 값으로 저장해도 환경변수 주소로 되돌아가지 않는다.

4. GitHub 저장소 Settings → Secrets and variables → Actions에 다음을 설정한다.
   - **Secret** `MAIL_PUBLISH_TOKEN`: Worker와 같은 값. Cloudflare 자동 호출의 서명 검증에도 사용한다.
   - **Variable** `REPORT_MAIL_ENDPOINT`: `https://predict-stock-counter.kimname1.workers.dev`.
   - 연동 설정이 없으면 기존 보고서 작업은 그대로 성공하고 이메일 단계는 건너뛴다.
5. `counter/worker.js` **전체**를 Worker 편집기에 붙여넣고 Deploy 한다. git push만으로 Worker는 배포되지 않는다. 워크플로 변경도 저장소에 반영해야 한다.
6. 기존 보고서 호출 시각을 유지하고 `*/5 * * * *`(UTC)를 추가한다. 발송 대기를 최대 5명씩 처리하므로 이 트리거만 기준으로 약 60명/시간이다. 수신자 수가 많으면 시간창 내 처리 가능량을 확인하고 Queue 기반 확장을 고려한다.
   - 운영 계정의 무료 Cron 제한은 5개다. `37 0 * * MON-FRI`와 `30 8 * * MON-FRI`를 `30,37 0,8 * * MON-FRI` 하나로 합쳐 메일 예약 자리를 확보했다. 추가되는 00:30·08:37 UTC에는 `dispatchDue`가 보고서 호출을 하지 않고 기존 보관기간 정리만 수행한다(메일 대기 처리는 다른 예약과 동일).
   - 현재 5개 예약: `30,37 0,8 * * MON-FRI`, `*/20 21-22 * * SUN-THU`, `10,45 7 * * MON-FRI`, `0 3 * * *`, `*/5 * * * *`.
7. 관리자 → 구독자에서 관리자 토큰으로 불러온 뒤, **발신 이메일**에 본인 주소를 저장한다. 설정·최근 결과를 확인하고 `MAIL_ENABLED=1`로 켠다. 켜는 시점에 해당 날짜·시간창의 기존 대기가 있다면 함께 처리된다.

### 신뢰성과 개인정보

- `POST /mail/events`는 발행 전용 토큰과 자동 실행 정보를 요구한다. Cloudflare 서명은 워크플로별로 검증하며 유효시간은 4시간이다. `caller=cloudflare-cron`만 입력한 수동 실행은 허용하지 않는다. 보고서 HTML과 원장의 게시 시각도 확인한다.
- `GET/POST /mail/settings`, `GET /mail/status`, `GET /mail/deliveries`, `POST /mail/control`, `POST /mail/cancel`은 기존 `STATS_TOKEN`이 필요하다. 집계에는 구독자 주소가 없으며 상세 조회에만 수신 주소·상태·시도 횟수·처리 시각을 50명씩 반환한다. 인증 값·본문은 반환하지 않는다. 설정은 D1에 영구 저장한다.
- 발송 요청을 시작하는 한국 날짜·회차·수신 주소로 발송 기록을 고정한다. 배치 중 날짜나 시간창이 바뀌면 남은 발송을 중단한다. 서비스 내부 처리·수신함 도착 지연은 별개다. D1 원자적 임대와 Resend의 `Idempotency-Key`를 함께 사용해 동시 실행과 접수 응답 유실에 대비한다. 같은 본문으로 최대 5회 재시도하고 해당 날짜·시간창 종료 시 만료한다. 따라서 제공자의 24시간 키 유효기간을 넘겨 재시도하지 않는다.
- Resend는 네트워크 오류·429·5xx 등만 재시도한다. 영구 오류는 실패로 남으며 주소를 고쳐도 실패한 회차를 다시 발송하지 않는다. 이전 버전에서 당일 같은 회차의 메일을 이미 시도한 주소는 새 묶음 메일을 보내지 않는다.
- Gmail은 TLS로 `smtp.gmail.com:465`에 연결한다. 발신 주소는 인증 계정과 같아야 한다. SMTP에는 중복 요청 제거 키가 없어 한 회차에 한 번만 시도한다. 접수 응답 유실·작업 중단도 자동 재시도하지 않고 실패/접수 불확실로 남긴다. Gmail의 별도 발송 한도는 Google 정책을 따른다.
- ‘발송 완료’는 Gmail의 DATA 접수 성공 또는 Resend의 ID 응답을 받은 상태다. 실제 수신함 도착·읽음 여부는 확인하지 않는다. 관리자 → 구독자에서 회차별 집계, 수신자별 이력과 한국시간 발송 시각을 확인하고 새로고침할 수 있다.
- **대기 취소**는 선택 회차의 미전송 대기를 취소한다. 아직 배송 행이 없는 주소에도 적용하며 다음 Cron에서 재생성하지 않는다. 다음 회차의 구독은 유지한다. **자동 발송 일시 중지/재개**는 발신 주소와 독립적으로 저장한다. 재개해도 `MAIL_ENABLED=0` 또는 설정 미완료면 보내지 않는다. 재개 시 취소하지 않은 당일 유효한 대기만 처리한다. 전송 임대 확보와 중지·취소 판정을 하나의 SQL로 처리하며 이미 임대한 전송은 회수할 수 없다.
- 발송 수신 주소·본문은 D1에 보관하며 활성화된 발송 작업이 30일 초과 기록을 정리한다. 장기간 기능을 끌 때는 운영자가 정리한다. 개인정보가 없는 날짜별 회차와 공개 요약은 중복 방지를 위해 유지한다.
- 메일 본문 하단에 수신 중지 안내와 **구독 취소** 버튼을 넣고, 텍스트 메일에도 같은 링크를 넣는다. 주소 대신 임의 토큰을 쓰며 GET은 확인 화면만 연다. **모든 보고서 구독 취소** 버튼의 POST는 해당 이메일의 모든 구독·발송 기록 및 대기·토큰을 삭제한다. 관리자 구독자 목록을 새로고침하면 해당 주소가 빠진다. One-Click POST도 지원한다. 웹 폼·관리자 삭제도 관련 기록과 토큰을 정리한다. 구독 종목이 달라지면 재시도 직전 다시 검사한다. 이미 전송 중이거나 서비스에 접수된 메일은 회수하지 못한다.
- 수신 목록·본문·API 오류 원문을 로그에 쓰지 않는다. 실제 구독자에게 테스트 메일을 보내지 않는다. 이메일 소유 확인·반송 webhook 자동 정리는 아직 없다.

### 로컬 점검

```sh
# 로컬 내용 미리보기만 허용한다. 실제 발송은 시스템 호출에서만 등록된다.
python tools/notify_report_update.py --target samsung --phase post_close --session 2026-10-02 --preview
python -m unittest tests.test_report_email tests.test_mail_site_reports tests.test_counter_worker tests.test_admin_page -v
```

테스트는 실제 SQLite 메모리 DB와 가짜 Resend 응답을 사용한다. 공식 자료:
[이메일 전송 API](https://resend.com/docs/api-reference/emails/send-email),
[중복 요청 키와 24시간 보관](https://resend.com/docs/dashboard/emails/idempotency-keys).

최초 구현 검증 당시에는 운영 D1 변경·Worker 배포·실제 이메일 전송을 수행하지 않았다.

검증 기록(2026-10-04): 관련 테스트 19개 통과(내부 Node/SQLite 계약 포함). 관리자 주소 저장·빈 값 중지 안내를 로컬 가짜 서버에서 확인했으며, 모바일 375px에서 가로 넘침이 없다. 수동 CLI 외부 호출 차단, 자동 Cron 서명, 자정·개장 경계, 부분 해지·신규 가입, 중복·동시 실행·재시도를 검증했다. 전체 테스트와 실제 서비스 접수·수신함 도착은 이번 변경에서 확인하지 않았다.

전체 메뉴 확장 검증(2026-10-04): 관련 테스트 23개 통과. 7개 공개 메뉴 요약·기준 시각, 고정 URL 검증, 중복 섹션 제외, 종목 구독 범위, 메인 구독 해지 후 재시도 취소를 확인했다. 샘플은 종목 2개와 추가 메뉴 7개로 구성했다. 실제 이메일을 보내지 않았고, 이번 샘플의 자동 브라우저 화면 검증은 로컬 파일 URL 제한으로 수행하지 못했다.

Gmail 및 발송 관리 검증(2026-10-04): 가짜 SMTP의 TLS·한글 MIME·인증 오류·응답 유실, 관리자 권한, 대기 취소 후 재생성 차단, 배치 중 일시 중지·재개 및 취소, 전송 중 결과 보존, 수신자 상세 페이지 나눔을 로컬에서 검증했다(관련 테스트 24개 통과). 운영 수신함 도착 검증은 Gmail Secret 설정 후 별도로 필요하다. [Google 앱 비밀번호](https://support.google.com/accounts/answer/185833), [Gmail SMTP](https://developers.google.com/workspace/gmail/imap/imap-smtp).

운영 설정 완료(2026-10-04): D1 메일 표 6개·인덱스 2개와 발신 주소, Gmail 앱 비밀번호, Cloudflare·GitHub 양쪽 `MAIL_PUBLISH_TOKEN`, GitHub `REPORT_MAIL_ENDPOINT`를 등록했다. 대시보드 편집기 초기화 오류를 피하기 위해 사용자 승인 후 공식 Wrangler로 배포했다. 기존 환경변수·Secret·DB를 유지하고 미리보기 URL은 끔, 운영 로그는 켬 상태를 유지했다. 메일 전용 5분 Cron을 포함한 예약 5개를 적용하고 `MAIL_ENABLED=1`로 활성화했다. 활성화 버전은 `f51d81c1-785a-48a5-b236-2272219ef983`이다. 10월 4일 야간(KST) 조회에서 구독자 2명, 일시 중지 아님, 등록 보고서·발송·실패·대기 모두 0건이었다. 실제 Gmail 수신과 GitHub에서 Worker까지의 자동 발행 연동은 다음 자동 보고서 갱신에서 확인해야 한다. 설정용 임시 토큰 파일은 삭제했다.

Gmail 실제 수신 확인(2026-10-04): 사용자가 앱 비밀번호를 갱신한 뒤 본인 Gmail 주소로 요청한 연결 시험에서 SMTP 접수에 성공했고, 사용자가 수신을 확인했다. 임시 시험 경로는 제거하고 운영 Worker로 복구했다. GitHub 자동 발행부터 메일 수신까지의 전체 자동 경로는 별도 확인이 필요하다.

구독 취소 안내 보완(2026-10-04): 관련 테스트 27개 통과. 메일의 HTML·텍스트 링크 일치, 링크 열람만으로 해지되지 않음, 확인 후 모든 구독·발송 대기·토큰 삭제, 관리자 목록 제외 및 다른 구독자 보존을 검증했다. 로컬 가짜 구독자로 메일 → 확인 → 완료 화면을 확인했으며 375px에서 가로 넘침이 없다. 이 변경으로 실제 구독자를 해지하거나 메일을 발송하지 않았다.
