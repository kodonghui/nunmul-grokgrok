```sh
git clone https://github.com/kodonghui/nunmul-grokgrok /workspace/nunmul
cd /workspace/nunmul && python3 -m unittest
# 선택: PUBLIC_DATA_SERVICE_KEY에 본인의 일반 인증키(Decoding)를 환경변수 또는 .env로 설정합니다.
```

봇이 부를 명령입니다. 품목코드·회차 key·시각은 해당 작업의 실제 값으로 바꿉니다.

```sh
python3 tools/rx_lookup.py --in state/rx_read.json --out state/rx_candidates.json
python3 tools/rx_lookup.py --explain <itemSeq>
python3 tools/make_schedule.py --in state/prescription.json --out state/schedule.json
python3 tools/dose_state.py due --now "2026-11-01T17:12:00+09:00"
python3 tools/dose_state.py mark "2026-11-01|17:00" --source chat --at "2026-11-01T17:13:00+09:00"
python3 tools/dose_state.py skip "2026-11-01|17:00" --at "2026-11-01T17:13:00+09:00"
```

# 눈물이그록그록 · 울보 약 비서

Grok Bot이 클라우드 컴퓨터에서 부르는 Python 도구 3개입니다. Python 3.9 이상 표준 라이브러리만 사용합니다. Windows에서는 `python3` 대신 `py`를 쓰면 됩니다. Windows Python 3.13에서 검증했습니다.

현재 범위는 약 후보 조회, 일정 계산, 복용 상태와 README입니다. 책상 친구의 펌웨어·PC 연결 프로그램·웹훅은 별도 작업입니다.

## Grok Bot과 도구의 역할

1. 봇이 가상 약봉투 사진에서 읽은 값을 `state/rx_read.json`으로 준비합니다. 못 읽은 칸은 `null`로 두고 개인정보는 쓰지 않습니다.
2. `rx_lookup.py`의 공식 후보를 보여 주고, 사용자가 선택한 제품·복용 숫자·시작일을 확인합니다.
3. 확인된 값과 `confirmedAt`을 `state/prescription.json`에 넣고 `make_schedule.py`를 실행합니다.
4. 회차와 병원 다시 가기 날짜를 보여 주고 동의받은 뒤, 봇의 Google Calendar 연결 앱으로 ‘눈물이 테스트’ 캘린더에 등록합니다. 설명의 `key`가 이미 있으면 기존 일정을 수정합니다.
5. 봇의 순찰 루틴이 현재 시각으로 `due`를 실행하고 `send`의 `say`만 전합니다. 빈 목록이면 메시지를 보내지 않습니다.
6. “먹었어”는 해당 회차의 `mark --source chat`, “그만”은 `skip`으로 처리합니다. 봇은 복용 확인 뒤 해당 캘린더 일정의 제목에 ✅를 붙입니다.

Python 도구는 사진 판독·캘린더 쓰기·채팅 전송을 하지 않습니다. `calendarEvents`와 `send`는 봇이 실행할 결과 데이터입니다. 예약 실행도 이 저장소에서 설치하지 않습니다.

## 파일과 출력

| 파일 | 내용 |
| --- | --- |
| `state/rx_read.json` | 사진에서 읽은 약 이름·제약사·복용 칸 |
| `state/rx_candidates.json` | 공식 후보 최대 5개와 조회 상태 |
| `state/prescription.json` | 사용자가 확인한 처방 |
| `state/slots.json` | 선택적인 생활 시간. 예: `{"MORNING":"08:30","EVENING":"18:30"}` |
| `state/schedule.json` | 복용 회차·재방문일·캘린더 일정 데이터 |
| `state/doses.json` | 회차별 상태·보낸 단계·복용 확인 |
| `config/voice.json` | 재촉·복용 확인·재방문 말투 |

`state/`는 필요할 때 생성하고 Git에서 제외합니다. JSON은 UTF-8이며, 파일은 같은 디렉터리의 임시 파일에 쓴 뒤 교체합니다. 복용 상태의 읽기·수정·저장은 운영체제 파일 잠금으로 함께 보호합니다. `doses.json.lock`은 잠금용 파일이며 복용 기록을 담지 않습니다. 실행이 끝나거나 프로세스가 종료되면 운영체제가 잠금을 해제합니다.

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
- 기본 경로의 `state/slots.json`이 있으면 자동으로 적용합니다. 다른 파일은 `--slots <경로>`로 지정합니다. 같은 약의 시각이 겹치거나 횟수와 복용 때가 모순되면 오류입니다.
- `timing`은 표시 문구입니다. “식후 30분”으로 시각을 이동하지 않습니다. 일수가 없으면 오류이며, 1회 양은 0보다 크고 10 이하인 0.5 단위 숫자만 받습니다.
- 종료일은 시작일 + 일수 − 1입니다. 병원 다시 가기 날짜는 가장 늦은 종료일 − 3일이며 이미 지났으면 오늘입니다. 재현할 때는 `--today YYYY-MM-DD`를 씁니다.
- 모든 시각은 서울 시간으로 정규화합니다. 시간대 없는 `--now`, `--at`, `confirmedAt`은 오류입니다. Windows에 IANA 데이터가 없으면 데모 계약의 `+09:00`을 표준 라이브러리로 사용합니다.
- `due`의 재촉 단계는 0·10·30분 기준입니다. 90분부터 상태가 `MISSED`가 되며 종료 문구를 한 번 반환합니다. 이 종료 알림의 `step`은 `4`이고 재촉 1~3단계와 구분합니다.
- 등록 전에 지난 회차는 `SKIPPED`입니다. 23:00~07:00은 조용히 상태만 갱신하고, 야간에 지난 단계를 아침에 몰아서 보내지 않습니다.
- 재방문일 09:00 이후 알림은 하루 한 번입니다. `doses.json`의 `revisit|YYYY-MM-DD` 항목은 이 알림의 `sentStep`만 저장합니다.
- `mark`에 같은 key를 다시 주면 처음 기록한 `takenAt`과 `source`를 유지합니다. key가 없으면 현재 시각 기준 T−60분~T+90분 안의 가장 가까운 `PENDING` 회차를 선택합니다. 재실행할 때는 반환된 key를 유지하세요.
- `skip`은 재촉을 멈추고 `SKIPPED`로 기록합니다. 이미 `TAKEN`인 기록은 보존합니다.
- `due`는 반환할 때 보낸 단계를 기록합니다. 이후 봇의 메시지 전송 성공 여부까지 추적하는 전달 확인 기능은 포함하지 않습니다.

`due`, `mark`, `skip` 모두 `--schedule <경로>`와 `--doses <경로>`로 다른 상태 파일을 지정할 수 있습니다. 동시에 다른 명령이 처리 중이면 잠깐 기다리고, 잠금을 얻지 못하면 `STATE_BUSY`를 반환합니다.

## 가상 입력으로 확인

`examples/`와 `fixtures/`의 약 이름·품목코드는 합성 시험 자료입니다. 실제 처방이나 공식 품목코드가 아닙니다. 가상 입력을 실제 공공데이터 조회의 성공 사례로 사용하지 않습니다.

```sh
python3 tools/make_schedule.py --in examples/prescription.json --today 2026-11-01 --out state/schedule.json
python3 tools/dose_state.py due --now "2026-11-01T17:12:00+09:00"
python3 tools/dose_state.py due --now "2026-11-01T17:12:00+09:00"
python3 tools/dose_state.py mark "2026-11-01|17:00" --source chat --at "2026-11-01T17:13:00+09:00"
python3 tools/dose_state.py due --now "2026-11-01T17:31:00+09:00"
```

새 상태 파일에서는 회차 10개, 종료일 11월 5일, 재방문일 11월 2일입니다. 첫 `due`는 step 2 한 건, 같은 시각의 두 번째 호출은 빈 목록입니다. 복용 확인 뒤에도 그 회차의 알림은 비어 있습니다. 이 명령은 데모 시각으로 기록을 실제 변경하므로 테스트 상태 파일에 실행합니다.

## 검증과 참고

`python3 -m unittest` 또는 Windows의 `py -m unittest`로 전체 테스트를 실행합니다. 조회 테스트는 합성 fixtures와 가짜 HTTP 응답만 사용하며 네트워크와 실제 키를 쓰지 않습니다. 테스트는 `.env`를 격리합니다. 일정·단계 경계·중복 실행·실제 동시 프로세스·공식 응답 모양·실패 구분을 검증합니다.

[필받음 개인 개발 보관본](https://github.com/kodonghui/pillboot-dev/tree/88dabed7d29aaecdc11d7a2df343685a52f0d141)의 약 조회·일정·검증 설계를 참고하고 새로 작성했습니다. 진단·용량 조절·복용법 추천 로직은 포함하지 않습니다. 실제 처방전·가족 정보로 시험하지 않습니다.
