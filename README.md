```sh
git clone https://github.com/kodonghui/nunmul-grokgrok /workspace/nunmul
cd /workspace/nunmul && python3 -m unittest
# 선택: PUBLIC_DATA_SERVICE_KEY에 본인의 일반 인증키(Decoding)를 환경변수 또는 .env로 설정합니다.
```

봇이 부를 명령입니다. 꺾쇠 자리표시자는 실제 결과 값으로 바꾸며 그대로 실행하지 않습니다. 프로필이 여러 개면 대상의 정확한 별칭 또는 ID를 반드시 지정합니다.

```sh
python3 tools/rx_lookup.py --in state/rx_read.json --out state/rx_candidates.json
python3 tools/rx_lookup.py --explain <itemSeq>
python3 tools/profiles.py list
python3 tools/profiles.py create --alias "할머니" --relationship FAMILY
python3 tools/profiles.py update <프로필> --in state/profile_patch.json
python3 tools/my_meds.py add --profile <프로필> --in state/confirmed_medicines.json
python3 tools/my_meds.py list --profile <프로필>
python3 tools/my_meds.py set-schedule <약ID> --profile <프로필> --in state/schedule_patch.json
python3 tools/my_meds.py stop <약ID> --profile <프로필>
python3 tools/my_meds.py clear-schedule <약ID> --profile <프로필>
python3 tools/my_meds.py delete <약ID> --profile <프로필>
python3 tools/intake.py propose --profile <프로필> --in state/intent.json
python3 tools/intake.py confirm <초안ID> --profile <프로필> --accept
python3 tools/intake.py cancel <초안ID> --profile <프로필>
python3 tools/intake.py appointments --profile <프로필>
python3 tools/dur_lookup.py --profile <프로필> --out state/dur_result.json
python3 tools/make_schedule.py --store state/medication_store.json --out state/schedule.json
python3 tools/dose_state.py due --now "2026-11-01T17:12:00+09:00"
python3 tools/dose_state.py mark <scoped-key> --profile <프로필> --medicine-id <약ID> --source chat --at "2026-11-01T17:13:00+09:00"
python3 tools/dose_state.py skip <scoped-key> --profile <프로필> --medicine-id <약ID> --at "2026-11-01T17:13:00+09:00"
python3 tools/my_meds.py calendar-plan
python3 tools/my_meds.py calendar-ack <scoped-key> --profile <프로필> --fingerprint <fingerprint> --event-id <eventId>
```

# 눈물이그록그록 · 울보 약 비서

Grok Bot이 클라우드 컴퓨터에서 부르는 약 조회·내 약 목록·일정·복용 상태 도구입니다. Python 3.9 이상 표준 라이브러리만 사용합니다. Windows에서는 `python3` 대신 `py`를 쓰면 됩니다. Windows Python 3.13에서 검증했습니다.

내 약은 **한 계정 안에서 가족 프로필별·약별로 누적**됩니다. 사진·음성·텍스트는 입력 방법입니다. 새 약을 등록해도 기존 약·일정·복용 기록이 유지됩니다. 상세 계약은 [AGENTS.md](AGENTS.md), [가족·음성·DUR v3 명세](docs/family-v3.md), [논리 ERD](docs/05-erd/family-v3.md)입니다. [v2 약별 규칙](docs/my-meds-v2.md)은 프로필 안의 하위 원장에 유지합니다. 책상 친구는 별도 작업입니다.

기존 Grok Bot에 붙여 넣을 [업데이트·이관·가족·약/캘린더 등록·DUR 전체 프롬프트](docs/grok-bot-v3-prompt.md)를 제공합니다. 그록에서 공공데이터 TLS 연결이 실패하는 경우 [한국 노트북 조회 중계](docs/rx-relay.md)를 사용할 수 있습니다. 실제 주소·인증값은 공개 문서에 넣지 않습니다. 실제 음성 인식, Grok 스킬 설치와 순찰 실행, Google Calendar 쓰기는 Python 단위 테스트와 별도로 확인합니다.

## 가족 프로필과 말로 등록

새 원장에는 ‘나’ 프로필이 생깁니다. `profiles.py create --alias "할머니"`처럼 별칭으로 가족을 추가합니다. 여러 프로필이 있으면 `--profile`에 정확한 별칭 또는 도구가 반환한 ID를 지정합니다. 같은 품목도 엄마·할머니에게 각각 등록할 수 있으며 복용 확인·중단·삭제가 서로 섞이지 않습니다. 현재 계정이 관리하며 다른 가족 계정으로 공유하거나 메시지를 보내는 기능은 아닙니다.

Grok의 음성/텍스트 해석 → `intake.py propose` → 실제 동의 또는 명확한 직접 실행 지시 확인 → `intake.py confirm --accept` 순서입니다. propose는 초안만 만들고 약·캘린더 일정은 저장하지 않습니다. 15분 만료, revision 충돌, 대상 불일치, 취소/교체된 초안은 확정할 수 없습니다. 같은 confirm 재실행은 중복 등록하지 않습니다. 정정은 새 요청 ID와 `propose --replace <이전 초안ID>`를 사용합니다. 음성 원본·전체 전사는 저장하지 않습니다.

입력 예시는 [음성 약 등록](examples/voice_medicine.json), [동행 병원 일정](examples/voice_calendar.json)입니다. 예시의 프로필 별칭은 실제 생성한 대상과 대조하고 시각은 사용자가 확인한 값으로 바꿉니다. Python은 음성 인식 모델을 실행하지 않습니다.

## 병원 방문·동행·사전 알림

“할머니 11월 10일 오후 3시 병원 방문, 엄마 동행, 일주일 전·하루 전 알려 줘” 같은 지시를 `appointment.add` 초안으로 만듭니다. 사용자가 정한 방문의 캘린더 기록이며 병원 접수·실제 예약 확정은 하지 않습니다. 종료 시각이 없으면 확인합니다. 병원 실명·주소 등 식별정보는 저장하지 않습니다.

- 대상은 프로필, `caregiver`는 동행자 별칭입니다. 기본 `reminderDays`는 `[7,1]`, `reminderTime`은 `09:00`(Asia/Seoul)입니다. 최대7개의 서로 다른 0~365일을 설정하며 `[]`는 사전 알림을 끕니다.
- `intake.py appointments --profile <프로필>`로 실제 일정 ID를 조회합니다. `appointment.update`/`appointment.cancel`도 초안·확정으로 처리하며 이력과 실제 Calendar 수정/삭제 작업을 남깁니다.
- `dose_state.py due --store state/medication_store.json --now <현재 ISO>`가 약 재촉과 방문 사전 알림을 함께 반환합니다. 대상·방문 시각·동행자와 함께 해당 날짜에 한 번 보냅니다. 등록 전에 지난 알림은 소급하지 않고 날짜 변경은 재계산하며 취소 후에는 보내지 않습니다. 23:00~07:00은 조용한 시간입니다.
- Grok의 순찰 루틴이 실제로 실행돼야 채팅 알림이 갑니다. 도구 설치만으로 루틴이 생기지는 않습니다. 현재 계정의 채팅 알림이며 Google 자체 팝업·다른 가족에게 전송을 설정했다고 표시하지 않습니다.

## 공식 DUR 확인

`dur_lookup.py --profile <프로필>`은 해당 가족의 미삭제 약만 확인합니다. 새 제품과 비교하려면 실제로 확인한 기존 약+새 품목을 담은 `--in` 파일(`{"items":[{"name":"...","itemSeq":"..."}]}`)로 조회하고 그 제품을 목록에 자동 등록하지 않습니다. 서로 다른 가족의 약을 합치지 않습니다.

공식 DUR 품목정보의 병용·연령·노인·임부 유형을 대조합니다. 연령·임신 조건은 사용자가 명시한 현재 값만 `profiles.py update`로 저장할 수 있고 확인 당일만 적용합니다. 별칭으로 나이·성별·임신을 추측하지 않습니다. 복합 연령 조건과 미확인 조건, 품목코드 없는 약은 `UNKNOWN`입니다.

결과의 `lookup`은 `ALL/PARTIAL/FAILED/NO_DRUG`, `result`는 `FOUND/NONE/UNKNOWN`입니다. 출처·조회 시각·유형별 공식 갱신일을 함께 냅니다. 부분 실패·전체 실패·미확인을 ‘금기 없음’으로 바꾸지 않습니다. `NONE`도 확인한 범위의 미발견이며 복용 가능·안전을 보장하지 않습니다. e약은요 설명은 DUR 검사를 대신하지 않습니다.

## Grok Bot과 도구의 역할

1. 봇이 대상 프로필을 확인하고 가상 사진·음성·텍스트에서 읽은 값을 `state/rx_read.json`으로 준비합니다. 못 읽은 칸은 `null`로 두고 개인정보는 쓰지 않습니다.
2. `rx_lookup.py`의 공식 후보를 보여 주고, 사용자가 선택한 제품·복용 숫자·시작일을 확인합니다.
3. 확인된 값으로 `intake.py propose`와 `confirm`을 실행합니다. `my_meds.py add --profile ...` 직접 도구는 이미 확인된 입력의 호환 연결에 사용합니다. 실제 약 ID를 보관하고 `make_schedule.py --store`로 일정을 확인합니다. 원장을 직접 편집하지 않습니다.
4. 회차와 병원 다시 가기 날짜를 보여 주고 동의받은 뒤, 아래 캘린더 반영 절차로 ‘눈물이 테스트’ 캘린더에 적용합니다. 다른 캘린더로 대체하지 않습니다.
5. 봇의 순찰 루틴이 현재 시각으로 `due`를 실행하고 `send`의 `say`만 전합니다. 빈 목록이면 메시지를 보내지 않습니다.
6. “먹었어”는 해당 회차의 `mark --source chat`으로 처리합니다. 특정 약만 먹었다면 `--medicine-id`를 사용합니다. 전체 복용은 ✅, 일부 복용은 ‘일부 확인’으로 캘린더 변경 작업이 나옵니다.
7. “내 약 보여 줘”는 `list`, “이 약 그만 먹기”는 `stop`, “이 약 일정 지우기”는 `clear-schedule`, “내 약 목록에서 빼기”는 `delete`로 처리합니다. “이번 재촉 그만”은 `skip`입니다. 대상 약이나 삭제 의미가 모호하면 먼저 확인합니다.

Python 도구는 사진 판독·캘린더 쓰기·채팅 전송을 하지 않습니다. `calendarChanges`와 `send`는 봇이 실행할 결과 데이터입니다. 예약 실행도 이 저장소에서 설치하지 않습니다.

## 약별 일정 관리

| 명령 | 결과 |
| --- | --- |
| `add --in <확인한 입력>` | 기존 약을 유지하면서 추가. 약별 시작·종료·시각을 따로 저장 |
| `list` / `list --all` | 현재 목록 / 삭제 표시된 약까지 조회 |
| `set-schedule <약ID> --in <변경 입력>` | 해당 약의 복용량·복용 때·기간·시각 변경. 다른 약과 과거 복용 기록 보존 |
| `stop <약ID>` | 종료일을 오늘로 줄이고 해당 약의 남은 재촉을 즉시 중단 |
| `clear-schedule <약ID>` | 약은 남기고 일정만 비움 |
| `delete <약ID>` | 현재 목록에서 제외하고 재촉 중단. 과거 복용 기록은 유지 |

미삭제 약과 같은 품목코드의 중복 등록은 `DUPLICATE_MEDICINE`으로 거부하며, 여러 항목 중 하나라도 잘못되면 전체 요청을 저장하지 않습니다. 직접 입력 약은 `itemSeq: null`로 받고 정규화한 동일 이름의 중복을 거부합니다. 일정 없는 약은 미확인 복용량을 `null`로 둘 수 있습니다.

동일 등록 입력을 다시 실행하면 같은 약 ID를 반환합니다. 봇은 등록 건마다 `requestId`를 정하고 재시도에는 같은 값을 사용합니다. 같은 request ID로 다른 내용을 보내면 `REQUEST_CONFLICT`입니다. 삭제 후 새 복용 건을 등록할 때는 새 request ID를 씁니다. 기존 약의 기간·시각 변경은 새 등록 대신 `set-schedule`을 사용합니다.

모든 내 약 명령은 `--store <경로>`, `--at <시간대 있는 ISO>`를 받습니다. 수정 명령에 `--if-revision <조회한 revision>`을 주면 목록이 바뀐 사이의 오래된 초안은 `REVISION_CONFLICT`로 거부합니다. 일정 입력 예시는 [examples/my_meds.json](examples/my_meds.json), 변경 예시는 [examples/schedule_patch.json](examples/schedule_patch.json)입니다.

## 캘린더 반영 절차

1. `calendar-plan`의 `changes` 또는 수정 결과의 `calendarChanges`에서 `operation`, `key`, `eventId`, `fingerprint`, `event`를 읽습니다.
2. ‘눈물이 테스트’ 캘린더에서 `eventId` 또는 설명의 `key: ...`로 기존 일정을 확인합니다. v3 key는 `프로필ID|기존key`입니다. 이관 작업에 `legacyKey`가 있으면 이전 key로도 찾아 기존 event ID를 유지합니다. `UPSERT`는 있으면 수정하고 없으면 생성합니다. 응답이 끊긴 생성도 key로 먼저 확인해 중복 생성하지 않습니다.
3. `DELETE`는 해당 일정만 실제 삭제합니다. event ID가 없으면 key로 찾고, 이미 없음이 확인돼도 완료로 처리할 수 있습니다. 같은 시각에 다른 약이 남으면 삭제 대신 내용 수정 작업이 나옵니다.
4. 반영 성공 뒤 해당 작업의 `key`·`fingerprint`로 `calendar-ack`를 실행합니다. `UPSERT`에는 실제 응답의 `--event-id`가 필수이고 `DELETE`에는 필요 없습니다. 실패하면 ack하지 않아 작업이 남습니다.

같은 ack의 재실행은 무해합니다. 작업 내용이 바뀌면 이전 fingerprint는 `STALE_CALENDAR_CHANGE`로 거부합니다. 생성 성공 여부가 불명확한 채 취소한 일정도 DELETE로 남겨 정리합니다. 제목에 ‘중단’을 붙이는 것만으로 삭제 완료를 기록하지 않습니다. 약 목록 변경과 캘린더 반영을 순서대로 실행하고, 반영 도중 목록이 바뀌면 최신 계획을 다시 읽습니다.

## 파일과 출력

| 파일 | 내용 |
| --- | --- |
| `state/rx_read.json` | 사진에서 읽은 약 이름·제약사·복용 칸 |
| `state/rx_candidates.json` | 공식 후보 최대 5개와 조회 상태 |
| `state/confirmed_medicines.json` | 사용자가 확인한 등록 입력. 원장을 대신하지 않음 |
| `state/medication_store.json` | v3 가족 프로필, 프로필별 약·일정·복용·재촉·동행 일정·초안·캘린더 반영 대기 |
| `state/backups/` | v2→v3 변환 전 원본 백업. Git 제외 |
| `state/prescription.json` | v1 이관 원본 또는 호환 등록 입력 |
| `state/slots.json` | 선택적인 생활 시간. 예: `{"MORNING":"08:30","EVENING":"18:30"}` |
| `state/schedule.json` | 원장에서 다시 만들 수 있는 회차·재방문일·캘린더 조회 결과 |
| `state/doses.json` | v1 복용 기록 이관 원본. 누적 모드에서는 원장 안의 약별 기록 사용 |
| `config/voice.json` | 재촉·복용 확인·재방문 말투 |

`state/`는 필요할 때 생성하고 Git에서 제외합니다. JSON은 UTF-8이며, 파일은 같은 디렉터리의 임시 파일에 쓴 뒤 교체합니다. 원장 전체의 읽기·수정·저장은 운영체제 파일 잠금으로 함께 보호합니다. `.lock`은 잠금용 파일이며 복용 기록을 담지 않습니다. 실행이 끝나거나 프로세스가 종료되면 운영체제가 잠금을 해제합니다. 잠금을 얻지 못하면 `STATE_BUSY`를 반환합니다.

표준 출력은 JSON 한 덩어리입니다. 파일·입력·인자 오류는 `{"error":{"code":"...","message":"..."}}`와 0이 아닌 종료 코드로 표시합니다. 조회가 완료됐지만 특정 카드가 `FAILED`인 경우에는 `cards`를 그대로 반환합니다. 봇은 종료 코드뿐 아니라 각 카드의 `status`도 확인해야 합니다.

## 공식 조회와 키

- 키는 `PUBLIC_DATA_SERVICE_KEY`의 **일반 인증키(Decoding)**를 사용합니다. 도구가 URL 인코딩합니다.
- 기본 `PUBLIC_DATA_BASE_URL`은 `https://apis.data.go.kr`입니다.
- 루트 `.env`가 있으면 위 두 변수와 `PUBLIC_DATA_RELAY_URL`·`PUBLIC_DATA_RELAY_TOKEN` 중 아직 없는 값만 읽습니다. 기존 환경변수는 빈 문자열이어도 유지합니다. 파일을 수정하거나 내용을 출력하지 않습니다. 임의 명령이나 변수 확장도 실행하지 않습니다.
- 직접 모드에서 키가 없으면 모든 카드가 `FAILED` / `NO_KEY`이고 HTTP 요청은 0회입니다. 중계 모드는 별도 토큰으로 조회하며 공식 서비스 키는 노트북에만 필요합니다. 일정과 복용 상태는 키 없이 동작합니다.
- 봇 컴퓨터의 키 입력은 보안 입력 기능을 이용하고, 키를 채팅·커밋에 넣지 않습니다. 공유 가능한 설정 예시는 `.env.example`입니다.
- 연결 제한은 2초, 연결 후 응답 읽기 제한은 10초입니다. 연결 오류·429·5xx만 같은 요청을 0.5초 뒤 한 번 다시 시도합니다. 다른 4xx, JSON·API 오류 뒤에는 다른 이름으로 재시도하지 않습니다.

| 카드 상태 | 뜻 |
| --- | --- |
| `OK` | 공식 응답에서 조건을 만족하는 후보를 찾음 |
| `PARTIAL` | 일부 응답 행의 필수 정보가 없어 제외함. 후보가 비어 있어도 공식적인 부재로 단정하지 않음 |
| `NOT_FOUND` | 수행한 정상 조회에서 일치 후보가 없음 |
| `FAILED` | 키·통신·응답 오류. 모든 행의 필수 정보가 불완전한 경우도 포함 |
| `NEEDS_NAME` | 읽을 수 없거나 단위·제형이 잘려 정확한 이름이 필요함 |

원문 `readName`을 보존하고 조회 표기만 정규화합니다. 함량을 읽었다면 질량을 마이크로그램으로 환산해 비교하며 복합 함량 순서를 유지합니다. 기본 제품명·제형·서방·장용 여부도 같아야 후보가 됩니다. 숫자 접두어만 읽힌 이름은 접두어를 그대로 조회하며 함량을 채워 넣지 않습니다. 공식 설명은 `--explain`으로 조회하고 출처·`updateDe`를 함께 반환합니다.

실제 허가정보의 전문/일반 구분 필드는 `ETC_OTC_CODE`입니다. `ETC_OTC_NAME` 호환 응답도 받고 두 값이 모순되면 제외합니다. 합성 fixtures와 실제 응답 필드의 호환성을 구분해 검증합니다.

## 한국 노트북 조회 중계

그록 환경의 TLS 실패를 피하려면 노트북에서 `py tools/rx_relay.py --port 8765 --token-file state/runtime/relay-token.txt`를 실행하고, 해당 loopback 포트를 임시 HTTPS 터널로 연결합니다. 토큰 파일은 운영 준비 과정에서 별도로 만들며 내용을 출력하지 않습니다. 그록에서는 보안 입력으로 `PUBLIC_DATA_RELAY_URL`과 `PUBLIC_DATA_RELAY_TOKEN`을 설정한 뒤 기존 `rx_lookup.py` 명령을 그대로 사용합니다.

둘 중 하나라도 설정되면 중계 모드입니다. 토큰 누락·주소 오류·연결 실패 시 직접 조회로 몰래 전환하지 않습니다. 직접 모드로 돌아가려면 두 중계 설정을 모두 비웁니다. 공식 키를 중계 토큰으로 재사용하지 않습니다. 노트북 서버는 공식 약 검색·설명·DUR 네 유형만 허용하고 원장·사진·캘린더·가족 조건은 다루지 않습니다. 명령 한 번에만 환경변수를 설정했다면 다음 명령에도 필요합니다. 연결했다고 가정하지 말고 모든 조회 실행 환경에서 두 중계 변수를 안전하게 전달합니다.

중계는 행사 동안만 켤 수 있으며, 서버와 터널 프로세스를 종료하면 새 공식 조회가 중단됩니다. 저장된 약 목록·복용 기록·캘린더는 유지됩니다. 자동 시작 서비스는 만들지 않습니다. 준비·연결·종료 방법과 실제 검증 범위는 [중계 문서](docs/rx-relay.md), API 계약은 [relay.openapi.json](api/relay.openapi.json)을 따릅니다. 실제 주소·키·토큰은 공개 저장소/템플릿에 넣지 않습니다.

## 일정과 복용 상태 규칙

- 기본 시각은 아침 07:00, 점심 11:00, 저녁 17:00, 자기 전 22:00입니다. 복용 때가 있으면 그 값을 사용하고, 없으면 하루 1회는 07:00, 2회는 07:00·17:00, 3회는 07:00·11:00·17:00입니다.
- 등록·일정 변경 때 원장과 같은 디렉터리의 `slots.json`이 있으면 적용합니다. 다른 파일은 `--slots <경로>`로 지정합니다. 직접 지정한 `doseTimes`는 그 시각을 사용합니다. 같은 약의 시각이 겹치거나 횟수와 복용 때가 모순되면 오류입니다. slots 파일만 바꿔도 저장된 일정이 저절로 바뀌지는 않습니다.
- `timing`은 표시 문구입니다. “식후 30분”으로 시각을 이동하지 않습니다. 일수가 없으면 오류이며, 1회 양은 0보다 크고 10 이하인 0.5 단위 숫자만 받습니다.
- 약별 종료일은 시작일 + 일수 − 1입니다. 확인된 `endDate`로 직접 지정할 수도 있습니다. 병원 다시 가기 날짜는 **약별** 종료일 − 3일이며 이미 지났으면 일정 확인일로 고정합니다. 같은 날짜끼리 묶고, 새 약 때문에 기존 약의 날짜를 밀지 않습니다.
- 모든 시각은 서울 시간으로 정규화합니다. 시간대 없는 `--now`, `--at`, `confirmedAt`은 오류입니다. Windows에 IANA 데이터가 없으면 데모 계약의 `+09:00`을 표준 라이브러리로 사용합니다.
- `due`의 재촉 단계는 0·10·30분 기준입니다. 90분부터 상태가 `MISSED`가 되며 종료 문구를 한 번 반환합니다. 이 종료 알림의 `step`은 `4`이고 재촉 1~3단계와 구분합니다.
- 등록 전에 지난 회차는 `SKIPPED`입니다. 23:00~07:00은 조용히 상태만 갱신하고, 야간에 지난 단계를 아침에 몰아서 보내지 않습니다.
- 재방문일 09:00 이후 알림은 같은 날짜에 한 번입니다. 원장의 `reminders`에서 `revisit|YYYY-MM-DD`의 보낸 단계를 관리합니다.
- `mark`에 같은 key를 다시 주면 처음 기록한 `takenAt`과 `source`를 유지합니다. key가 없으면 현재 시각 기준 T−60분~T+90분 안의 가장 가까운 `PENDING` 회차를 선택합니다. 재실행할 때는 반환된 key를 유지하세요.
- `mark`·`skip`은 `--medicine-id <약ID>`를 반복 지정해 특정 약만 처리할 수 있습니다. 생략하면 해당 회차 전체를 처리합니다. `skip`은 그 회차 재촉만 멈추고 `SKIPPED`로 기록합니다. 이미 `TAKEN`인 기록은 보존합니다.
- `due`는 반환할 때 보낸 단계를 기록합니다. 이후 봇의 메시지 전송 성공 여부까지 추적하는 전달 확인 기능은 포함하지 않습니다.

`due`, `mark`, `skip`은 기본적으로 `state/medication_store.json`을 사용합니다. `--store <경로>`로 다른 원장을 지정할 수 있습니다. 같은 시각에 약을 묶어도 복용 기록은 약 ID와 일정 버전별로 분리하므로 새 약이 옛 약의 TAKEN을 물려받지 않습니다.

## 기존 자료 이관과 v1 호환

`profiles.py list` 등 v3 도구의 첫 실행에서 기존 v2 원장을 `state/backups/`에 보존한 뒤 변환합니다. 약·기록이 있으면 소유자를 추측하지 않고 ‘기존 약’ 프로필로 감쌉니다. 내부 약 ID·일정 버전·복용 기록·Calendar event ID를 유지하며 기존 일정의 key 갱신은 `legacyKey`로 연결합니다. 기존 자료의 대상을 확인한 뒤 프로필 별칭을 변경합니다. 약이 없으면 ‘나’ 하나로 시작합니다. 여러 프로필이 있는 경우 `my_meds.py list --profile <대상>`으로 각각 확인합니다.

처음 `my_meds.py list` 또는 기본 `dose_state.py`를 실행할 때 누적 원장이 없으면 같은 디렉터리의 `prescription.json`·`schedule.json`·`doses.json`을 확인해 한 번 이관합니다. 원본 파일은 수정하지 않고, 복용 확인 시각·출처·보낸 단계도 유지합니다. 처방과 일정이 맞지 않거나 기록의 출처를 확인할 수 없으면 `MIGRATION_CONFLICT`로 멈춥니다. 이 경우 원본을 지우거나 덮어써서 통과시키지 않습니다. 원장이 생긴 뒤 새 약은 `add`로 등록합니다.

v1의 `build_schedule`·복용 상태 순수 함수는 유지합니다. `make_schedule.py --in ...`은 단일 입력 계산이며 누적 원장에 등록하지 않습니다. v1 파일을 다루려면 `dose_state.py`에도 `--schedule <경로> --doses <경로>`를 명시합니다. `--store`와 v1 인자를 섞으면 오류입니다. 기존 봇의 단일 처방 덮어쓰기 지시문은 위 누적 흐름으로 교체해야 합니다.

## 가상 입력으로 확인

`examples/`와 `fixtures/`의 약 이름·품목코드는 합성 시험 자료입니다. 실제 처방이나 공식 품목코드가 아닙니다. 가상 입력을 실제 공공데이터 조회의 성공 사례로 사용하지 않습니다.

```sh
python3 tools/my_meds.py add --store state/demo/medication_store.json --in examples/my_meds.json --at "2026-11-01T13:00:00+09:00"
python3 tools/my_meds.py list --store state/demo/medication_store.json --at "2026-11-01T13:00:00+09:00"
python3 tools/make_schedule.py --store state/demo/medication_store.json --out state/demo/schedule.json
python3 tools/dose_state.py due --store state/demo/medication_store.json --now "2026-11-01T17:12:00+09:00"
python3 tools/dose_state.py due --store state/demo/medication_store.json --now "2026-11-01T17:12:00+09:00"
python3 tools/dose_state.py mark "2026-11-01|17:00" --store state/demo/medication_store.json --source chat --at "2026-11-01T17:13:00+09:00"
python3 tools/dose_state.py due --store state/demo/medication_store.json --now "2026-11-01T17:31:00+09:00"
```

새 데모 원장에서는 약 2개가 독립 일정으로 저장됩니다. 첫 약은 11월 1~5일 07:00·17:00, 두 번째는 11월 2~4일 17:00입니다. 같은 시각을 묶어 회차 10개, 재방문일 11월 1일·2일입니다. 첫 `due`는 복용 알림 step 2 한 건과 당일 재방문 알림 한 건, 같은 시각의 두 번째 호출은 빈 목록입니다. 복용 확인 뒤에도 그 회차의 알림은 비어 있습니다. 일부 약만 확인하려면 `add`/`list`에서 반환된 실제 약 ID로 `--medicine-id`를 추가합니다. 데모는 분리된 `state/demo/`를 쓰며 실제 목록에서 실행하지 않습니다.

## 검증과 참고

`python3 -m unittest` 또는 Windows의 `py -m unittest`로 전체 테스트를 실행합니다. 조회 테스트는 합성 fixtures와 가짜 HTTP 응답만 사용하며 네트워크와 실제 키를 쓰지 않습니다. 테스트는 `.env`를 격리합니다. 누적 등록·약별 관리·이관·동시 저장·캘린더 재시도/ack, 가족 간 격리, 음성 초안의 만료/충돌/재실행, 동행 일정의 7일/1일 알림·변경/취소, DUR 전체 페이지·조건 미확인·부분 실패를 검증합니다.

[필받음 개인 개발 보관본](https://github.com/kodonghui/pillboot-dev/tree/88dabed7d29aaecdc11d7a2df343685a52f0d141)의 약 조회·일정·검증 설계를 참고하고 새로 작성했습니다. 진단·용량 조절·복용법 추천 로직은 포함하지 않습니다. 실제 처방전·가족 정보로 시험하지 않습니다.
