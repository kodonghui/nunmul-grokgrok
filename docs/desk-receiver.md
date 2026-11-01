# 책상 버튼 수신 계약 · profile-key-v1

사용자 승인(2026-11-01): “백앤드 고칠건 너가 하고 그록한테는 그록봇관련한거만 시키자”. 백엔드 코드는 원래 Codex 채팅, 장치/desk 소스는 Codex 책상친구 채팅, Grok은 네이티브 웹훅·스킬·Calendar·순찰 연결을 담당한다.

## SPEC-DESK-RX-001 · 입력과 신뢰 경계

`python3 tools/desk_event.py --profile <연결한 실제 프로필ID> --in state/inbox/<요청파일>.json [--store state/medication_store.json] [--now <ISO>]`

- HTTP 서버를 추가하지 않는다. Grok 네이티브 웹훅이 인증/접근 제어한 이벤트 JSON만 이 CLI에 넘긴다. 수신 URL과 토큰은 보안 설정이며 공개 저장소·로그에 넣지 않는다. requestId의 SHA256은 내용 식별이며 인증 서명이 아니다.
- 프로필은 운영자가 보드 한 대에 연결한 정확한 ID를 CLI의 필수 --profile에 설정한다. payload에서 이 설정을 덮어쓰지 않는다. 기존 schemaVersion=3 원장이 있어야 하며 수신 명령은 빈 원장을 생성하거나 v1/v2 이관을 실행하지 않는다.
- 허용 필드는 event=dose_taken, source=desk, pressedAt, profileId, key, doseIds, medicineIds, scheduleRevision, requestId뿐이다. 추가 개인정보/명령/URL 필드는 거부한다. UTF-8 JSON 최대64KiB, 중복 JSON 필드·NaN/Infinity를 거부한다.
- key는 `profileId|YYYY-MM-DD|HH:mm`, doseIds는 v3 schedule이 반환한 프로필 내부의 원본 약별 ID 배열, medicineIds는 대응 약 ID 배열이다. 각 목록은 중복 없는 1~100개다. profileId·key·각 doseId의 약·일정 버전·시각이 일치해야 한다.
- requestId는 책상 브리지와 같은 `desk_`+SHA256이다. 입력 순서를 유지한 {profileId,key,doseIds}를 Python json.dumps(sort_keys=True,separators=(",",":"))로 직렬화한 UTF-8의 해시다. 전체 payload의 정규화 digest도 별도로 보관해 같은 요청 ID의 내용 변경을 거부한다.

## SPEC-DESK-RX-002 · 원자적 검증과 기록

- 서버 현재 시각은 실제 Asia/Seoul이다. 최초 요청은 pressedAt이 수신 시각보다 최대2분 미래/최대10분 과거이며, 대상 T의 T-60분~T+90분 안이어야 한다. --now는 격리 시험에만 쓴다.
- scheduleRevision은 미래 revision이면 거부한다. 낮은 revision은 전역 revision 차이만으로 거부하지 않으며 실제 doseIds·약별 일정 버전·현재 활성 상태·기간/시각을 원장 잠금 안에서 다시 검사한다. 순찰·다른 가족·Calendar ack는 원장의 revision을 바꿀 수 있다.
- 삭제/중단/일정 비우기/일정 변경으로 달라진 대상, inactive·SKIPPED·MISSED 회차는 거부한다. 새로운 약이나 다른 가족의 가까운 회차로 대체하지 않는다. 요청에 명시된 약만 처리한다.
- 유효한 PENDING을 TAKEN/source=desk/takenAt=pressedAt으로 기록한다. 이미 TAKEN인 같은 대상의 기존 takenAt/source는 유지한다. 이미 채팅으로 확인한 약을 책상 출처로 덮어쓰지 않는다.
- profiles[id].deskRequests에 requestId→digest·receivedAt·결과 영수증을 선택적으로 저장한다. 기존 v3는 이 필드 없이도 읽을 수 있다. 복용 상태와 영수증은 같은 잠금·같은 원자적 파일 교체로 저장한다. 실패하면 둘 다 반영되지 않는다.
- 같은 요청/내용은 저장한 영수증으로 재실행 처리한다. 이미 수락한 요청의 재실행은 이후 약 삭제나 시간 경과가 있어도 새 복용을 기록하지 않는다. 다른 내용은 REQUEST_CONFLICT다.
- 출력의 recorded=true는 CLI가 원장 기록/기존 확인을 검증한 결과다. changedDoseIds는 이번 최초 처리에서 바뀐 기록이며, notify=true일 때만 새 복용 확인 알림을 보낸다. 재실행은 replayed=true/notify=false이고 기존 say를 재전송하지 않는다.
- Calendar 변경은 기존 calendarChanges/실제 반영/calendar-ack 절차다. 웹훅 HTTP200은 여전히 접수이며 CLI 기록 성공·Calendar 반영 성공과 구분한다.

## SPEC-DESK-SYNC-001 · 노트북 일정 전달

Grok에서 `make_schedule.py --store state/medication_store.json --profile <ID> --out state/exports/desk-schedule.json`으로 실제 도구가 반환한 v3 일정을 만든다. 원장·.env 대신 이 파일을 사용자 승인 경로로 노트북의 desk worktree에 전달한다. 보드는 해당 ID 하나에만 연결한다. 약 등록/부분 확인/중단/삭제/일정 변경 뒤에는 새 파일을 전달한다. 자동 동기화가 없는 시연에서는 이 수동 단계가 필요하다. 오래된 파일의 버튼 요청은 서버가 재검증하며 다른 약으로 해석하지 않는다.

## 검증과 완료 경계

가짜 이벤트와 임시 원장으로 대상 불일치·시간 경계·오래된 일정·부분 복용·재실행·내용 충돌·동시 요청·저장 실패·기존 v3 호환·추가 패키지 없는 CLI를 검사한다. 네트워크·실제 키·웹훅·실제 가족 정보를 쓰지 않는다. 네이티브 웹훅 생성과 노트북 버튼의 실제 원장 반영은 코드 테스트와 분리해 확인한다.
