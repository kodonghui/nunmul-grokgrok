# 내 약 목록 v2 계약

2026-11-01 사용자 요청: “필받음처럼 약 단위로 누적되는 내 약 목록으로 바꿔줘. 기존 약은 유지하고 약별 일정 관리·중단·삭제도 되게 해줘. 명세 먼저 고치고 테스트 통과한 뒤 main에 푸시해.”

## 관리 단위와 원장

- 사진은 입력 방법이다. 사진·약봉투 단위의 저장 엔티티를 만들지 않는다.
- 한 약 항목은 내부 `id`, `itemSeq` 또는 직접 입력 이름, `dosePerTime`, `timing`, `registeredAt`, `deletedAt`, `scheduleVersion`, `schedule`, `history`를 가진다.
- `schedule`은 없을 수 있다. 있으면 `version`, `startDate`, `endDate`, `doseTimes`, `confirmedAt`, `stoppedAt`, `revisitDate`를 가진다. 기본 시각·0.5 단위 용량·일수 검증은 기존 규칙을 유지한다. 사용자가 정한 `doseTimes` 1~3개도 받는다.
- 누적 원장은 `state/medication_store.json`이다. `schemaVersion=2`, 정수 `revision`, `medicines`, `doses`, `reminders`, `requests`, `calendar`, `migration`을 가진다.
- `medicines`는 약 ID별 객체, `doses`는 약별 복용 기록, `reminders`는 시각별 보낸 단계다. `calendar`는 실제 반영된 일정과 대기 작업·ack를 가진다. 인증정보·환자/병원 정보는 저장하지 않는다.
- 원장의 읽기·수정·저장은 같은 운영체제 잠금 안에서 하고 임시 파일 교체로 한 번에 확정한다. 실패하면 이전 원장을 유지한다. `schedule.json`은 원장에서 다시 계산할 수 있는 조회 결과다.

## 등록과 목록

`my_meds.py add --in <확인된 입력> [--request-id <값>]`은 약을 추가한다. 기존 약은 유지한다. 입력의 `items`에 약별 시작일·일수/종료일·복용 시각·확인 시각을 둘 수 있고, v1의 공통 `startDate`·`confirmedAt`도 호환된다.

- 같은 입력과 request ID의 재실행은 같은 약 ID를 반환하고 중복 저장하지 않는다. 같은 request ID로 다른 내용을 보내면 `REQUEST_CONFLICT`다.
- 품목코드는 숫자 문자열 또는 null이다. null은 직접 입력이며 공식 코드로 추측해 채우지 않는다.
- 미삭제 약과 같은 품목코드가 있으면 `DUPLICATE_MEDICINE`으로 전체 요청을 거부한다. 한 요청 내부의 중복도 거부한다. 직접 입력은 NFKC·공백 정규화 후 동일 이름의 중복을 거부한다.
- 일정 필드가 전혀 없으면 일정 없는 약으로 저장할 수 있다. 복용량도 미확인이면 null로 둔다. 일정 등록에는 확인된 복용량과 유효한 기간·시각이 필요하다.
- `list`는 미삭제 약을 반환한다. `list --all`은 목록에서 제외된 약도 상태와 함께 반환한다. 알림 대상이 없는 빈 목록은 정상 결과다.

## 약별 일정 변경·중단·삭제

`set-schedule <id> --in <patch.json> --at <ISO>`은 이름·품목코드·다른 약을 유지하며 선택한 약의 복용량·복용 때·일정을 변경한다. patch는 `dosePerTime`, `timing`, `startDate`, `endDate`, `durationDays`, `dailyCount`, `dosePeriods`, `doseTimes`만 허용한다.

- 기존 일정의 과거 회차와 완료 기록을 남기고, 변경 확인 시각 이후의 미완료 회차를 새 일정 버전으로 계산한다. 이전 일정은 history에 남긴다.
- 같은 약·같은 시각에 이미 TAKEN 기록이 있으면 새 버전으로 중복 재촉하지 않는다. 다른 약의 기록을 이 목적으로 재사용하지 않는다.
- `stop`은 현재 일정의 종료일을 오늘로 줄이고 `stoppedAt`을 남긴다. 해당 약의 남은 PENDING 재촉은 SKIPPED로 종료한다. 미래 일정은 캘린더 제거 대상이다. 이미 종료된/미래 시작 일정의 중단은 오류로 구분한다. 같은 중단의 재실행은 변경하지 않는다.
- `clear-schedule`은 선택한 약의 schedule만 null로 하고 남은 재촉을 멈춘다. 이름·품목코드·복용 정보·복용 기록은 유지한다.
- `delete`는 `deletedAt`을 남겨 현재 약 목록에서 제외하고 남은 재촉을 멈춘다. 이미 남긴 복용 기록을 지우지 않는다. 재실행해도 같으며, 같은 품목을 나중에 새로 등록하면 새 약 ID다.
- 모든 수정 명령의 `--if-revision`이 현재 revision과 다르면 `REVISION_CONFLICT`이며 변경하지 않는다.

## 복용 회차와 확인

- 알림·캘린더 묶음 key: `YYYY-MM-DD|HH:mm`.
- 복용 기록 key: `{medicineId}|v{scheduleVersion}|YYYY-MM-DD|HH:mm`.
- 기록에는 약 ID·일정 버전·시각·그때의 표시 문구·확인 시각·상태·보낸 단계·복용 확인 시각/출처·활성 여부를 남긴다.
- 약별 confirmedAt 이전 회차는 SKIPPED다. 새 약 등록 시각으로 기존 약의 회차를 다시 건너뛰지 않는다.
- `due --store`는 약별 상태를 갱신하고 같은 시각에 남은 약들을 한 메시지로 묶는다. 단계·야간·MISSED 규칙과 config/voice.json은 그대로 적용한다.
- `mark [회차key] --medicine-id <id>`와 `skip`은 한 약만 처리한다. ID를 생략하면 해당 회차의 약들을 함께 처리한다. key를 생략하면 기존 T−60분~T+90분의 가장 가까운 PENDING 회차를 고른다.
- 이미 TAKEN인 기록의 takenAt·source는 유지한다. skip/중단/삭제로 TAKEN을 지우지 않는다. 전체가 TAKEN이면 캘린더 제목에 ✅, 일부만 TAKEN이면 ‘일부 확인’을 표시한다.
- 복용 결과에 갱신된 캘린더 대기 작업을 함께 반환한다. 실제 메시지 전달 성공은 추적하지 않는다.

## 재방문과 캘린더 반영

- 재방문일은 약별 종료일 − 3일이며, 일정 확정 때 이미 지났으면 그날로 고정한다. 새로운 긴 일정 때문에 다른 약의 재방문일을 옮기지 않는다. 같은 날짜의 재방문 알림/일정은 한 건으로 묶는다.
- `calendar-plan`은 `UPSERT`/`DELETE`, `key`, `fingerprint`, 알고 있는 `eventId`, 일정 내용을 반환한다.
- 한 약을 중단해도 같은 시각에 다른 약이 남으면 UPSERT한다. 남는 약이 없고 이미 반영한 미래 일정이면 DELETE한다. 과거에 반영된 일정은 보존한다.
- Grok Bot은 ‘눈물이 테스트’ 캘린더에서 eventId 또는 설명의 key로 기존 일정을 확인해 반영한다. 생성 응답이 끊겨도 같은 key로 확인하고 중복 생성하지 않는다.
- 반영 성공 뒤 `calendar-ack <key> --fingerprint <값> --event-id <값>`을 실행한다. DELETE ack에는 event ID가 필요 없다. 같은 ack의 반복은 무해하다.
- 실패한 작업은 남긴다. 현재 작업과 fingerprint가 다르면 `STALE_CALENDAR_CHANGE`로 거부한다. 제목만 바꾸고 삭제 성공으로 ack하지 않는다. Python은 캘린더·채팅·외부 API를 호출하지 않는다.

## v1 호환과 이관

- 기존 순수 함수와 `--schedule`/`--doses` 명시 명령은 v1 모드로 유지한다. `--store`와 함께 지정하면 오류다.
- 기본 dose_state 호출은 누적 원장이 있으면 사용한다. 원장이 없고 legacy 처방이 있으면 한 번 이관한다. 명시적인 v1 파일 호출은 다른 상태 파일의 처리로 취급한다.
- 원장이 없을 때 `my_meds.py`는 같은 디렉터리의 `prescription.json`·`schedule.json`·`doses.json`을 확인한다. 원본 파일은 수정하지 않는다.
- v1 복용 기록이 있으면 처방과 일정의 confirmedAt·회차·표시 문구가 일치해야 이관한다. 다르면 `MIGRATION_CONFLICT`로 멈추고 추측하지 않는다. 알 수 없는 옛 기록을 새 약에 붙이지 않는다.
- 확인 가능한 v1 회차의 TAKEN·SKIPPED·MISSED·sentStep을 약별 기록으로 옮긴다. v1 재방문 sentStep도 보존한다.
- 기존 캘린더 event ID는 알 수 없으므로 key로 찾아 UPSERT 후 ack한다. 자동 루틴·새 서비스·책상 친구는 설치하지 않는다.

## 검증 기준

1. 다른 입력으로 A·B를 차례로 추가해도 A가 유지된다. 동시 등록도 유실되지 않는다.
2. 동일 입력 재실행은 중복되지 않고, 중복 품목/잘못된 두 번째 항목은 전체 저장을 취소한다.
3. 약별 시작일·기간·확인 시각이 적용된다. 새 약에 옛 TAKEN이 붙지 않는다.
4. 같은 시각 A만 mark/skip해도 B는 남는다. 모두 mark하면 ✅이며 반복 mark는 최초 기록을 유지한다.
5. 한 약의 일정 변경·중단·일정 삭제·목록 삭제가 다른 약이나 과거 TAKEN을 손상하지 않는다.
6. 중단·삭제 후 새 등록/일정에서 옛 취소 회차가 되살아나지 않는다.
7. 캘린더 반영 실패 후 재시도, ack 반복, 낡은 ack 거부, 남은 약의 일정 수정/마지막 약의 미래 일정 삭제가 맞다.
8. v1 기록 이관, 원본 보존, 출처 불일치 거부, revision 충돌·파일 쓰기 실패·동시 due/mark가 맞다.
9. 기존 80개와 추가 테스트를 `py -m unittest`로 통과한다. 모든 테스트는 합성 데이터·임시 파일만 사용하며 실제 키·네트워크를 사용하지 않는다.
