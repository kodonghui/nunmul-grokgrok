# 눈물이그록그록 · 울보 약 비서

2026-11-01 Grok Bot 빌드데이 데모용이다. Grok Bot이 클라우드 컴퓨터 명령줄에서 부르는 약 조회·내 약 목록·일정·복용 상태 도구를 만든다. 필받음(PillBoot) 설계를 참고했고 코드는 새로 쓴다.
레포: https://github.com/kodonghui/nunmul-grokgrok

## 범위와 순서
1~3절의 도구와 내 약 목록, 한 계정의 가족 프로필, 음성/텍스트 공통 등록 초안, 일반 캘린더 예약, 공식 DUR 조회, 테스트, README가 범위다. 상세 계약은 `docs/family-v3.md`와 `docs/05-erd/family-v3.md`를 먼저 따른다. 내 약은 프로필별·약별로 누적하며 새 등록 때문에 기존 약을 덮어쓰지 않는다. 음성 입력·인식과 자연어 해석은 Grok이 담당하고 Python은 구조화된 요청과 확정 초안을 검증한다. 4절(책상 친구)은 사용자가 따로 시킬 때만 한다.

## 실행 환경
- Python 3 표준 라이브러리만 쓴다(urllib.request, json, datetime, zoneinfo, argparse, re, unicodedata, unittest, pathlib, os, tempfile). 봇 컴퓨터는 업데이트·초기화 때 설치 패키지가 지워진다. 예외는 4절의 desk/ 하나다(내 PC에서만 돈다).
- 시간대는 Asia/Seoul. 날짜는 YYYY-MM-DD, 시각은 ISO 8601(+09:00).
- 모든 도구는 JSON 파일을 읽고 쓴다. 표준 출력에는 결과 JSON 한 덩어리만 낸다. 오류는 `{"error": {"code": "...", "message": "..."}}`를 내고 0이 아닌 코드로 끝낸다.
- 상태 파일은 state/ 아래에 둔다. state/는 .gitignore에 있으므로 없으면 도구가 만든다. 쓸 때는 임시 파일에 쓴 뒤 이름을 바꾼다(루틴이 겹쳐도 파일이 깨지지 않게).
- 비밀값(공공데이터 서비스 키, 웹훅 주소)은 코드·커밋·로그·출력에 넣지 않는다. 키는 환경변수 PUBLIC_DATA_SERVICE_KEY(일반 인증키, Decoding)에서 읽고, 기본 주소는 PUBLIC_DATA_BASE_URL(없으면 https://apis.data.go.kr)이다. 레포 루트에 .env가 있으면 KEY=VALUE 줄을 표준 라이브러리로 읽어, 아직 없는 환경변수만 채운다. .env는 .gitignore에 있고 비밀값 없는 .env.example만 커밋한다. .env 내용을 출력하지 않는다.
- 키가 없으면 rx_lookup은 조회하지 않고 모든 카드를 FAILED(code=NO_KEY)로 낸다. make_schedule·dose_state는 키 없이 동작한다.
- 레포는 공개해도 되게 만든다(비밀값·개인정보 없음). Bot Marketplace에 봇 템플릿으로 올리면 다른 사람 봇이 이 레포를 받아 쓴다.
- 로컬(윈도우)에서는 py 또는 python으로 돌려도 된다. 경로는 pathlib로 다루고 파일은 UTF-8로 읽고 쓴다.
- 한국 노트북 조회 중계는 1절의 선택 연결 방식이다. `PUBLIC_DATA_RELAY_URL`·`PUBLIC_DATA_RELAY_TOKEN`이 있으면 별도 인증으로 조회만 중계하며 공식 서비스 키는 노트북에 둔다. 중계 설정이 불완전하면 직접 조회로 몰래 전환하지 않는다. 중계 계약은 `docs/rx-relay.md`와 `api/relay.openapi.json`을 먼저 따른다. 4절 책상 친구와는 별개다.
- 중계 클라이언트는 `tools/relay_config.py save`로 같은 보안 실행 환경의 주소·별도 토큰을 비공개 `state/runtime/rx-client/connection.json`에 한 쌍으로 저장할 수 있다. 이름 검색·설명·DUR가 같은 설정을 자동으로 읽는다. `.env`는 수정하지 않는다. 값 출력·첨부·커밋은 금지하며, 설정 충돌·손상·누락은 직접 API fallback 없이 오류로 중단한다. 값 변경은 `save --replace`, 확인은 값이 없는 `status`를 사용한다. 임시 `rx_env_check.sh`에 의존하지 않는다.
- 사용자가 승인한 책상 버튼의 백엔드 수신은 `tools/desk_event.py`와 `docs/desk-receiver.md`를 따른다. 장치 소스는 별도 desk worktree에서만 수정한다. 수신 CLI는 기존 v3 원장 안에서 프로필·회차·일정 버전·중복을 검증하며 HTTP 접수와 실제 기록 결과를 구분한다.
- 채팅의 우는·웃는 PNG 첨부는 `docs/notification-images.md`를 따른다. `dose_state.py due/mark --with-images`, `intake.py confirm --with-images`가 검증한 공개 파일의 `image` 정보를 반환하고 실제 첨부는 Grok이 한다. 프로필 아바타 변경·음성 울음·책상친구와는 별개이며, 옵션 없는 출력/원장 스키마는 유지한다. 자산은 상태 변경 전에 검사하고 부분 확인·재실행·desk 접수에는 전체 복용 완료 미소를 붙이지 않는다.

## 데이터 모양
누적형 내 약의 원장은 `state/medication_store.json`이다. 현행은 가족별 v2 하위 원장을 포함하는 schemaVersion=3이다. 상세 계약은 [docs/family-v3.md](docs/family-v3.md)를 먼저 따른다. [docs/my-meds-v2.md](docs/my-meds-v2.md)의 약·복용 규칙과 순수 함수는 하위 원장/호환 기준으로 유지한다. `profiles.py`, `my_meds.py`, `intake.py`, `dur_lookup.py`를 사용한다. 프로필이 여러 개면 수정·복용 확인에는 프로필을 명시한다.

- 약별 내부 ID, 품목코드, 복용량, 복용 때, 등록 시각과 약별 일정(시작·종료·시각·확인 시각)을 저장한다. 사진별 엔티티는 만들지 않는다.
- 같은 시각의 알림/캘린더 key는 `YYYY-MM-DD|HH:mm`이고, 실제 복용 기록 key는 `약ID|v일정버전|YYYY-MM-DD|HH:mm`이다. 다른 약의 TAKEN을 물려받지 않는다.
- 원장에 약 목록·약별 복용 기록·재촉 단계·캘린더 반영 대기를 한 번에 저장한다. `schedule.json`은 다시 만들 수 있는 계산 결과다.
- 기존 `prescription.json`은 확인된 약을 가져오는 입력으로도 받는다. 원장이 없고 기존 처방/일정/복용 파일이 있으면 확인 가능한 기록을 원장에 한 번 이관하고 원본 파일은 수정하지 않는다. 출처가 맞지 않으면 추측하지 않고 오류를 낸다.
- 아래 단일 처방 JSON과 기존 순수 함수는 v1 호환 입력/검증으로 유지한다. 실제 누적 등록은 `my_meds.py add`, 누적 일정은 `make_schedule.py --store`, 누적 복용 처리는 `dose_state.py --store`를 사용한다.

state/rx_read.json (봇이 사진에서 읽은 값, 못 읽은 칸은 null)
```json
{"readable": true, "medicines": [{"readNo": 1, "readName": "타이레놀정500밀리그램", "readMaker": null, "dosePerTime": 1, "dailyCount": 2, "durationDays": 5, "dosePeriods": null, "timing": "식후 30분", "doseRawText": "1일 2회 5일분"}]}
```

state/rx_candidates.json
```json
{"cards": [{"readNo": 1, "readName": "...", "status": "OK", "queries": ["..."], "excludedCount": 0, "candidates": [{"itemSeq": "...", "itemName": "...", "entpName": "...", "etcOtc": "일반의약품", "cancelled": false}]}]}
```

state/prescription.json (사용자가 확인한 값)
```json
{"startDate": "2026-11-01", "confirmedAt": "2026-11-01T13:00:00+09:00", "items": [{"readNo": 1, "itemSeq": "...", "name": "타이레놀정500밀리그램", "dosePerTime": 1, "dailyCount": 2, "dosePeriods": null, "durationDays": 5, "timing": "식후 30분"}]}
```

state/schedule.json
```json
{"startDate": "2026-11-01", "endDate": "2026-11-05", "revisitDate": "2026-11-02", "confirmedAt": "2026-11-01T13:00:00+09:00", "occurrences": [{"key": "2026-11-01|17:00", "at": "2026-11-01T17:00:00+09:00", "label": "저녁", "lines": ["타이레놀정500밀리그램 1회 1개 · 식후 30분"]}], "calendarEvents": [{"key": "2026-11-01|17:00", "summary": "약 먹을 시간 · 저녁 17:00", "start": "2026-11-01T17:00:00+09:00", "end": "2026-11-01T17:10:00+09:00", "description": "..."}, {"key": "revisit|2026-11-02", "summary": "병원 다시 가기 · 약 떨어지기 3일 전", "allDayDate": "2026-11-02", "description": "..."}]}
```

state/doses.json
```json
{"2026-11-01|17:00": {"state": "PENDING", "sentStep": 1, "takenAt": null, "source": null}}
```

dose_state.py due 출력
```json
{"now": "2026-11-01T17:12:00+09:00", "send": [{"key": "2026-11-01|17:00", "step": 2, "say": "…아직이세요? 눈물이 그록그록 😢"}]}
```

## 1. tools/rx_lookup.py
사용: `python3 tools/rx_lookup.py --in state/rx_read.json --out state/rx_candidates.json`
설명: `python3 tools/rx_lookup.py --explain <itemSeq>`
- 이름 검색 API(의약품 제품 허가정보 상세): https://apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService08/getDrugPrdtPrmsnDtlInq08
  파라미터 serviceKey, type=json, item_name, entp_name(제약사를 읽었을 때만), pageNo=1, numOfRows=100
  쓰는 필드 ITEM_SEQ, ITEM_NAME, ENTP_NAME, ETC_OTC_CODE(실제 API의 전문/일반 구분), ETC_OTC_NAME(호환 별칭), CANCEL_NAME, ITEM_INGR_NAME, CHANGE_DATE. 두 구분 필드가 모두 있으면 뜻이 일치해야 한다.
- 설명 API(e약은요): https://apis.data.go.kr/1471000/DrbEasyDrugInfoService/getDrbEasyDrugList
  파라미터 serviceKey, type=json, itemSeq, pageNo=1, numOfRows=1
  쓰는 필드 itemName, efcyQesitm, useMethodQesitm, atpnWarnQesitm, atpnQesitm, intrcQesitm, seQesitm, depositMethodQesitm, updateDe. 출처 이름과 updateDe를 함께 낸다. 없으면 NOT_FOUND.
- 응답 처리: header.resultCode가 "00"이 아니면 FAILED. JSON이 아니면(키 오류 때 XML이 옴) FAILED("키·파라미터 확인"). 바깥이 {"response": {...}}로 한 번 더 감싸져도 받는다. items는 배열 · {"item":[...]} · {"item":{...}} 세 모양을 모두 받는다.
- 연결 2초, 읽기 10초. 연결 오류·429·5xx만 0.5초 뒤 1회 다시 시도한다. 4xx는 다시 시도하지 않는다.
- 정규화: NFKC → 공백 모두 제거 → 끝의 투여 단위(/1정, /1캡슐, /1포 등) 제거. 함량(60mg 등)은 제품 식별 정보라 지우지 않는다.
- 질의는 최대 3개, 일치가 나오면 멈춘다: (1) 정규화한 이름 (2) 단위 표기를 바꾼 이름(mg↔밀리그램, g↔그램, mcg·μg↔마이크로그램) (3) 함량을 뺀 제품명(함량이 있고 %·IU·단위·/mL 같은 농도형이 아닐 때만).
- 채택(모두 맞아야 후보): 함량 수치 일치(마이크로그램으로 환산해 비교. 0.5mg=500마이크로그램, 복합 5/10mg은 순서 유지) · 괄호 속 성분 설명을 뺀 기본 제품명 일치 · 제형 끝말(정·캡슐·시럽·과립·액·산) 일치 · 서방·장용 여부가 양쪽 모두 같음. 함량을 못 읽었으면 같은 제품의 모든 함량을 후보로 둔다(함량을 추측해 채우지 않는다).
- 이름 끝 말줄임표(... … ⋯ ‥)는 원문을 그대로 두고 조회 값에서만 뗀다.
  - 함량이 끝까지 읽힘(합성정60mg…, 합성정60mg(성분…) → 떼고 정상 조회
  - 단위·제형이 중간에 잘림(합성정0.5m…, 합성서방…, 합성정5mg/…) → 조회하지 않고 NEEDS_NAME
  - 숫자 접두어만(합성정60…) → "합성정60"으로 조회해 60mg·600mg은 남기고 6mg·서방정은 뺀다
  - 제품명만(합성정…) → 제품명으로 조회해 모든 함량을 후보로
  - 복합 함량 접두어(듀비메트서방정0.5/10…) → "0.5/10"을 소수점 그대로 조회
- 행 검증: ITEM_SEQ(숫자)·ITEM_NAME·ENTP_NAME·전문/일반 구분이 모두 있어야 후보. 빠진 행은 빼고 excludedCount로 센다.
- 정렬: 취하(CANCEL_NAME이 정상이 아님)는 뒤로 → 제품명 정확 일치 먼저 → 제약사 정확 일치 먼저. 품목코드 중복을 뺀 뒤 최대 5개.
- 카드 상태: OK / PARTIAL(일부 행 제외) / NOT_FOUND(공식 일치 없음) / FAILED(통신·키 오류) / NEEDS_NAME. FAILED를 NOT_FOUND로 바꾸지 않고, 실패했다고 다른 이름으로 다시 찾지 않는다.
- 같은 정상 응답은 5분 동안 메모리에 둬도 된다(실패 응답은 두지 않는다).

## 2. tools/make_schedule.py
누적형 사용: `python3 tools/make_schedule.py --store state/medication_store.json --out state/schedule.json`
약별 기간·확인 시각과 일정을 사용하며, 중단·일정 삭제·목록 삭제를 반영한 원장에서 계산한다. 같은 시각의 약은 묶되 약별 복용 기록은 분리한다. 재방문일은 약별 종료일에서 계산하고 같은 날짜끼리 묶는다. 이미 복용한 약이 있는 회차는 일부 확인, 모두 복용한 회차는 ✅로 구분한다.

v1 호환 사용과 규칙:
사용: `python3 tools/make_schedule.py --in state/prescription.json [--slots state/slots.json] [--today YYYY-MM-DD] --out state/schedule.json`
- 약마다 복용 시각 1~3개(HH:mm, 중복 없음, 정렬). 기본 시각은 아침 07:00 · 점심 11:00 · 저녁 17:00 · 자기 전 22:00.
  - dosePeriods(MORNING·NOON·EVENING·BEDTIME)가 있으면 그 시각
  - 없으면 dailyCount로 정한다: 1 → 07:00, 2 → 07:00·17:00, 3 → 07:00·11:00·17:00
  - state/slots.json(`{"MORNING": "08:30", ...}`, 사용자 생활 시간)이 있으면 기본 시각 대신 쓴다
- timing(식후 30분 등)은 표시 문구일 뿐 시각을 옮기지 않는다.
- endDate = startDate + durationDays - 1(양끝 포함). durationDays가 없거나 1보다 작으면 오류(추측하지 않는다). dosePerTime은 0.5 단위 숫자, 0보다 크고 10 이하.
- 같은 날짜·시각의 약은 회차 1개로 묶는다. key = "YYYY-MM-DD|HH:mm".
- revisitDate = 약들 중 가장 늦은 endDate - 3일. 그날이 오늘(--today, 기본은 Asia/Seoul 오늘)보다 이르면 오늘.
- calendarEvents: 회차마다 10분짜리 일정 1개 + 병원 다시 가기 종일 일정 1개.
  - 회차 제목 "약 먹을 시간 · 저녁 17:00"(04:00~10:59 아침, 11:00~15:59 점심, 16:00~21:59 저녁, 그 밖은 밤)
  - 설명: 약마다 "- {이름} 1회 {dosePerTime}개 · {timing}" 한 줄, 그다음 "확인용 정보 · 약은 약사·의사와 상담하세요", 마지막 줄 "key: {key}"
  - 병원 일정 제목 "병원 다시 가기 · 약 떨어지기 3일 전", key = "revisit|{revisitDate}"

## 3. tools/dose_state.py
누적형은 `--store state/medication_store.json`을 사용한다. 기본 경로에 원장이 있으면 자동 사용한다. `--medicine-id <약ID>`로 같은 회차 중 한 약만 mark/skip할 수 있다. 중단·일정 삭제·목록 삭제는 `my_meds.py`로 수행한다. 기존 `--schedule`/`--doses` 명시 호출과 순수 함수는 v1 호환 모드다. 두 모드의 상태 파일을 섞지 않는다.

### 내 약 목록 명령
- `python3 tools/my_meds.py list [--all]`
- `python3 tools/my_meds.py add --in state/prescription.json [--request-id <등록요청ID>]`
- `python3 tools/my_meds.py set-schedule <약ID> --in state/schedule_patch.json [--at <ISO>]`
- `python3 tools/my_meds.py stop <약ID> [--at <ISO>]`
- `python3 tools/my_meds.py clear-schedule <약ID> [--at <ISO>]`
- `python3 tools/my_meds.py delete <약ID> [--at <ISO>]`
- `python3 tools/my_meds.py calendar-plan`
- `python3 tools/my_meds.py calendar-ack <key> --fingerprint <값> [--event-id <일정ID>]`
- 모든 명령에 `--store <경로>`를 줄 수 있다. 수정 명령은 `--if-revision <조회한 revision>`으로 낡은 초안의 저장을 거부할 수 있다.
- 같은 품목코드의 미삭제 약을 자동 합산하거나 덮어쓰지 않는다. 중복 등록은 전체 요청을 거부하고 명시적인 일정 변경을 사용한다. 품목코드 없는 직접 입력은 정규화한 동일 이름의 중복을 거부한다.
- 중단은 종료일을 오늘로 줄이고 해당 약의 남은 PENDING 재촉·미래 일정을 중지한다. 봇이 복용 중단을 판단하지 않는다.
- 일정 삭제는 약을 남기고 일정만 비운다. 목록 삭제는 deletedAt을 기록해 현재 목록에서 제외한다. 과거 복용 기록은 유지한다.
- 캘린더는 UPSERT/DELETE 작업과 fingerprint를 반환한다. Grok Bot이 실제 반영하고 calendar-ack로 성공을 기록한다. 실패한 작업은 남겨 두며, 제목 변경만으로 삭제 완료를 기록하지 않는다. 실제 캘린더 호출·자동화 생성은 Python 도구가 하지 않는다.

### v1 호환 명령과 공통 말투/시간 규칙
사용:
- `python3 tools/dose_state.py due --now <ISO> [--schedule state/schedule.json] [--doses state/doses.json]`
- `python3 tools/dose_state.py mark [<key>] --source chat|desk [--at <ISO>]`
- `python3 tools/dose_state.py skip [<key>] [--at <ISO>]`

- 회차 상태: PENDING / TAKEN / MISSED / SKIPPED. confirmedAt보다 앞선 회차는 SKIPPED(등록 전에 지난 약으로 울지 않는다).
- 단계: now < T → 0, T ≤ now < T+10분 → 1, T+10분 ≤ now < T+30분 → 2, T+30분 ≤ now < T+90분 → 3, now ≥ T+90분 → MISSED. 이 단계 계산은 따로 불러 쓸 수 있는 함수로 둔다(4절이 같이 쓴다).
- due는 PENDING 회차마다 지금 단계가 이미 보낸 단계(sentStep)보다 높을 때만 {key, step, say}를 내보내고 sentStep을 갱신한다. 밀린 단계는 건너뛰고 지금 단계만 보낸다. MISSED로 바뀔 때 한 번 say="아직 확인이 없어요. 괜찮아요, 다음 약은 같이 챙겨요 🥲"(안 먹었다고 단정하지 않는다). revisitDate 당일 09:00 이후 한 번 revisit 말투.
- 같은 now로 두 번 부르면 두 번째는 빈 목록이어야 한다.
- 23:00~07:00에는 아무것도 보내지 않는다(그 사이에 넘은 단계는 건너뛴다). 같은 회차의 재촉(step1~3)은 최대 3번이다.
- skip은 사용자가 '그만'이라고 할 때 그 회차를 SKIPPED로 바꿔 더 울지 않게 한다. key가 없으면 mark와 같은 방식으로 고른다.
- mark는 회차를 TAKEN으로 바꾸고 takenAt·source를 적는다. 여러 번 불러도 결과가 같다. key가 없으면 now 기준 T-60분~T+90분 안의 가장 가까운 PENDING 회차. 대상이 없으면 {"marked": null}.
- 말투는 config/voice.json에서 읽는다.
  step1 "약 드실 시간이에요 🥹" / step2 "…아직이세요? 눈물이 그록그록 😢" / step3 "엉엉, 저 이러다 탈수 와요 😭 '먹었어'라고만 해 주세요" / taken "흑, 감동의 눈물… 오늘도 해내셨군요 🥲" / revisit "약 떨어지기 3일 전이에요… 병원 일정 넣어 놨어요 🥹"

## 4. desk/ · 책상 친구 (선택 · 사용자가 따로 시킬 때만)
봇 컴퓨터가 아니라 내 윈도우 PC에서 돈다. 보드는 누코드 NU-40 DK(nRF52840) 하나이고 대여품이다. 버튼 4개·LED 4개가 보드에 들어 있다.
- 펌웨어 desk/nunmul_desk/nunmul_desk.ino (Arduino)
  - 보드 패키지 URL https://raw.githubusercontent.com/Nucode01/Adafruit_nRF52_Arduino/refs/heads/master/package_nuduino_index.json · 보드 NUBoards nRF52 > NU40DK nRF52840 · FQBN nucode:nrf52:nu40dk
  - 핀은 보드 패키지 매크로로만 쓴다: PIN_LED1~PIN_LED4(PIN_LED1이 맨 위), LED_STATE_ON, PIN_BUTTON1. 버튼은 INPUT_PULLUP으로 읽는다.
  - LED·버튼 극성은 첫 업로드에서 눈으로 확인한다(핀맵 그림은 LED를 'Negative logic'으로 표기했고, 보드 패키지 v1.0.2는 LED_STATE_ON=1). S0인데 LED가 켜져 있으면 뒤집는다.
  - USB 시리얼 115200, 한 줄씩. PC→보드 S0~S4·P·K. 보드→PC B(BUTTON1, 디바운스 50ms, 누를 때 한 번).
  - LED: S0 모두 꺼짐 · S1 LED1 켜짐 · S2 LED1·2가 1초 간격으로 깜빡 · S3 LED1→4가 위에서 아래로 흐르듯 반복 · S4 넷이 함께 3번 깜빡, 5초 뒤 S0 · P LED4 빠르게 깜빡 · K LED4 1초 켜짐.
  - BLE는 쓰지 않는다. Burn Bootloader·SoftDevice 굽기는 하지 않는다.
- PC 프로그램 desk/desk_bridge.py (추가 패키지는 pyserial 하나, desk/requirements.txt)
  사용: `py desk/desk_bridge.py --port COM5 --schedule state/schedule.json [--now <ISO> --speed 60] [--dry-run]`
  - pyserial은 포트를 열 때만 import한다. 봇 컴퓨터의 `python3 -m unittest`가 pyserial 없이 통과해야 한다.
  - 단계는 tools/dose_state.py의 단계 계산 함수를 불러와 같은 기준으로 정한다. 단계가 바뀔 때만 S 줄을 보낸다. MISSED·회차 없음은 S0.
  - --now를 주면 그 시각부터 가상 시계로 돌고, --speed 60이면 1분이 1초다.
  - B를 받으면 지금 시각 기준 T-60분~T+90분 안의 가장 가까운 회차를 고르고, 그 회차에서 처음일 때만 웹훅을 보낸다: P → POST DESK_WEBHOOK_URL `{"event":"dose_taken","source":"desk","pressedAt":"<ISO>"}`(5초 제한, 실패하면 1회 다시 시도) → 200이면 K → S4. 실패하면 지금 단계로 돌아가고 콘솔에 실패만 적는다. 200은 '접수'이지 기록 완료가 아니다.
  - DESK_WEBHOOK_URL은 .env에서만 읽고 출력하지 않는다. --dry-run은 시리얼 없이 콘솔에 S 줄을 찍는다.
  - 채팅의 '먹었어'는 책상에 반영되지 않는다(데모는 버튼으로).

## 검사
`python3 -m unittest`가 통과해야 끝이다. rx_lookup 테스트는 fixtures/의 응답 예시로 돌리고 네트워크·키를 쓰지 않는다.
- 2026-11-01 시작, 하루 2번, 5일분, --today 2026-11-01 → 회차 10개(07:00·17:00), endDate 2026-11-05, revisitDate 2026-11-02
- durationDays 없음 → 오류
- 17:00 회차(confirmedAt 13:00): 17:05 → step1, 17:12 → step2, 17:31 → step3, 18:31 → MISSED 한 번, 그 뒤 출력 없음
- 처음 due가 17:12 → step2만 한 번. 같은 now로 두 번째 → 빈 목록
- 07:00 회차(confirmedAt 13:00) → SKIPPED, 아무 말도 안 함
- mark 두 번 → TAKEN 한 번, 그 뒤 due 출력 없음
- skip → SKIPPED, 그 뒤 due 출력 없음
- 22:00 회차: 23:31에는 MISSED로 바뀌지만 조용한 시간이라 출력 없음
- 키 없음 → rx_lookup 카드가 모두 FAILED(NO_KEY), 네트워크 호출 0회
- rx_lookup: 말줄임표 다섯 경우, FAILED와 NOT_FOUND 구분, 함량·제형·서방이 다르면 탈락, 후보 5개 상한·정확 일치 먼저
- v2: 여러 번 등록해도 기존 약 유지, 약별 시작일/확인 시각, 부분 복용, 다른 약의 TAKEN 분리, 약별 일정 변경·중단·일정 삭제·목록 삭제, 과거 기록 보존
- v2: 동일 요청 중복 실행, 중복 품목 거부 시 전체 롤백, 동시 등록/복용 확인, revision 충돌, v1 이관과 출처 불일치 거부
- v2: 같은 시각의 다른 약을 남긴 캘린더 수정, 마지막 약 제거 시 삭제 작업, 반영 실패 재시도·중복/낡은 ack 거부
- (4절을 할 때) 보드 없이 가짜 시리얼로: 단계 전환, B 두 번 → 웹훅 한 번, 웹훅 실패 처리

## README
- 맨 위에 봇이 따라 할 설치 세 줄: `git clone https://github.com/kodonghui/nunmul-grokgrok /workspace/nunmul`(레포가 비공개면 사용자가 파일을 첨부한다) → `cd /workspace/nunmul && python3 -m unittest` → (선택) 공공데이터 키는 PUBLIC_DATA_SERVICE_KEY 환경변수나 .env로 넣는다.
- 그 아래에 봇이 부를 명령(rx_lookup·make_schedule·dose_state due/mark/skip)을 한 줄씩.

## 하지 말 것
- 진단·용량 조절·복용법 추천 로직을 만들지 않는다.
- 환자 이름·생년월일·주소·병원 식별정보를 저장하지 않는다. 가족은 별칭·관계로 관리하고, 사용자가 명시한 선택적 현재 나이·임신 조건만 DUR에 사용할 수 있다. 미확인은 UNKNOWN으로 두며 나이는 확인 당일만 자동 대조한다. 실제 처방전·가족 정보로 시험하지 않는다.
- 공공데이터 조회와 책상 친구 웹훅 말고는 외부로 데이터를 보내지 않는다.
- 누코드가 행사에서 준 프롬프트·ZIP 파일은 커밋하지 않는다.
