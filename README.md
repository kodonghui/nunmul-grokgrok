```sh
git clone https://github.com/kodonghui/nunmul-grokgrok /workspace/nunmul
cd /workspace/nunmul && python3 -m unittest
# 선택: PUBLIC_DATA_SERVICE_KEY에 본인의 일반 인증키(Decoding)를 환경변수 또는 .env로 설정합니다.
```

봇이 부를 명령입니다. `<itemSeq>`, `<약ID>`, `<fingerprint>`, `<eventId>`와 시각은 실제 결과 값으로 바꿉니다. 자리표시자를 그대로 실행하지 않습니다.

```sh
python3 tools/rx_lookup.py --in state/rx_read.json --out state/rx_candidates.json
python3 tools/rx_lookup.py --explain <itemSeq>
python3 tools/my_meds.py add --in state/confirmed_medicines.json
python3 tools/my_meds.py list
python3 tools/my_meds.py set-schedule <약ID> --in state/schedule_patch.json
python3 tools/my_meds.py stop <약ID>
python3 tools/my_meds.py clear-schedule <약ID>
python3 tools/my_meds.py delete <약ID>
python3 tools/make_schedule.py --store state/medication_store.json --out state/schedule.json
python3 tools/dose_state.py due --now "2026-11-01T17:12:00+09:00"
python3 tools/dose_state.py mark "2026-11-01|17:00" --medicine-id <약ID> --source chat --at "2026-11-01T17:13:00+09:00"
python3 tools/dose_state.py skip "2026-11-01|17:00" --medicine-id <약ID> --at "2026-11-01T17:13:00+09:00"
python3 tools/my_meds.py calendar-plan
python3 tools/my_meds.py calendar-ack "2026-11-01|17:00" --fingerprint <fingerprint> --event-id <eventId>
```

# 눈물이그록그록 · 울보 약 비서

Grok Bot이 클라우드 컴퓨터에서 부르는 약 조회·내 약 목록·일정·복용 상태 도구입니다. Python 3.9 이상 표준 라이브러리만 사용합니다. Windows에서는 `python3` 대신 `py`를 쓰면 됩니다. Windows Python 3.13에서 검증했습니다.

내 약은 **약 단위로 누적**됩니다. 사진은 입력 방법이며 사진별 저장 단위는 없습니다. 새 약을 등록해도 기존 약·일정·복용 기록이 유지됩니다. 상세 계약은 [AGENTS.md](AGENTS.md)와 [내 약 목록 v2 명세](docs/my-meds-v2.md)입니다. 책상 친구의 펌웨어·PC 연결 프로그램·웹훅은 별도 작업입니다.

## Grok Bot과 도구의 역할

1. 봇이 가상 사진이나 직접 입력에서 읽은 값을 `state/rx_read.json`으로 준비합니다. 못 읽은 칸은 `null`로 두고 개인정보는 쓰지 않습니다.
2. `rx_lookup.py`의 공식 후보를 보여 주고, 사용자가 선택한 제품·복용 숫자·시작일을 확인합니다.
3. 약별로 확인된 값과 `confirmedAt`을 `state/confirmed_medicines.json`에 넣고 `my_meds.py add`를 실행합니다. 반환된 약 ID를 보관하고 `make_schedule.py --store`로 일정을 확인합니다. 원장을 직접 편집하지 않습니다.
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
2. ‘눈물이 테스트’ 캘린더에서 `eventId` 또는 설명의 `key: ...`로 기존 일정을 확인합니다. `UPSERT`는 있으면 수정하고 없으면 생성합니다. 응답이 끊긴 생성도 key로 먼저 확인해 중복 생성하지 않습니다.
3. `DELETE`는 해당 일정만 실제 삭제합니다. event ID가 없으면 key로 찾고, 이미 없음이 확인돼도 완료로 처리할 수 있습니다. 같은 시각에 다른 약이 남으면 삭제 대신 내용 수정 작업이 나옵니다.
4. 반영 성공 뒤 해당 작업의 `key`·`fingerprint`로 `calendar-ack`를 실행합니다. `UPSERT`에는 실제 응답의 `--event-id`가 필수이고 `DELETE`에는 필요 없습니다. 실패하면 ack하지 않아 작업이 남습니다.

같은 ack의 재실행은 무해합니다. 작업 내용이 바뀌면 이전 fingerprint는 `STALE_CALENDAR_CHANGE`로 거부합니다. 생성 성공 여부가 불명확한 채 취소한 일정도 DELETE로 남겨 정리합니다. 제목에 ‘중단’을 붙이는 것만으로 삭제 완료를 기록하지 않습니다. 약 목록 변경과 캘린더 반영을 순서대로 실행하고, 반영 도중 목록이 바뀌면 최신 계획을 다시 읽습니다.

## 파일과 출력

| 파일 | 내용 |
| --- | --- |
| `state/rx_read.json` | 사진에서 읽은 약 이름·제약사·복용 칸 |
| `state/rx_candidates.json` | 공식 후보 최대 5개와 조회 상태 |
| `state/confirmed_medicines.json` | 사용자가 확인한 등록 입력. 원장을 대신하지 않음 |
| `state/medication_store.json` | 약별 목록·일정·복용 기록·재촉 단계·캘린더 반영 대기의 원장 |
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
- 루트 `.env`가 있으면 위 두 환경변수 중 아직 없는 값만 읽습니다. 기존 환경변수는 빈 문자열이어도 유지합니다. 파일을 수정하거나 내용을 출력하지 않습니다. 임의 명령이나 변수 확장도 실행하지 않습니다.
- 키가 없으면 모든 카드가 `FAILED` / `NO_KEY`이고 HTTP 요청은 0회입니다. 일정과 복용 상태는 키 없이 동작합니다.
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

`python3 -m unittest` 또는 Windows의 `py -m unittest`로 전체 테스트를 실행합니다. 조회 테스트는 합성 fixtures와 가짜 HTTP 응답만 사용하며 네트워크와 실제 키를 쓰지 않습니다. 테스트는 `.env`를 격리합니다. 누적 등록·약별 변경·중단·삭제·부분 복용·이관·동시 명령·저장 실패 시 원본 보존·캘린더 재시도/ack와 기존 조회·시간 경계 규칙을 검증합니다.

[필받음 개인 개발 보관본](https://github.com/kodonghui/pillboot-dev/tree/88dabed7d29aaecdc11d7a2df343685a52f0d141)의 약 조회·일정·검증 설계를 참고하고 새로 작성했습니다. 진단·용량 조절·복용법 추천 로직은 포함하지 않습니다. 실제 처방전·가족 정보로 시험하지 않습니다.
