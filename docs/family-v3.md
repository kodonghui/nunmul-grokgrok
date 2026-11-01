# 가족·음성/텍스트 등록·DUR v3 계약

2026-11-01 이 Codex 대화의 사용자 승인: “한 계정에서 가족 프로필별로 약 관리하고, 말로 약·캘린더 등록하고, 필받음처럼 DUR도 조회하게 구현해줘. 명세랑 ERD부터 고치고 테스트 통과하면 main에 푸시해. 책상 친구는 아직 하지 마.”

## 데이터·이관 · SPEC-FAM-001 / SPEC-MIG-001

- 원장은 기존 경로의 schemaVersion=3이다. 루트는 revision, profiles, drafts, intakeRequests, migration을 가진다. profiles[id]는 id·alias·relationship·createdAt·clinical·store·appointments·calendarAcks·legacyKeys를 가진다. store는 기존 schemaVersion=2 하위 원장이다. 다른 계정과의 공유·새 로그인 서비스는 만들지 않는다.
- 프로필 ID는 내부 profile_...이며 별칭은 1~30자, 중복 불가다. clinical은 ageYears(0~130) 또는 ageMonths(0~1560), pregnant(true/false/null), confirmedOn을 선택적으로 가진다. 확인 당일이 아니면 나이/임신 조건은 미확인으로 대조한다. 생년월일·실명·주소·병원 정보·음성 녹음은 저장하지 않는다.
- 한 프로필 안에서 같은 품목 중복은 거부하고 다른 프로필의 같은 품목은 허용한다. 약·복용 기록·requestId·재촉·캘린더 반영 상태는 하위 원장으로 격리한다. 관리자는 현재 계정이며 복용 대상과 구분한다.
- 빈 새 원장에는 기본 ‘나’ 프로필 하나를 만든다. v2 자료는 내부 ID와 복용 기록·일정 버전·event ID를 보존해 ‘기존 약’ 프로필로 감싼다. 소유 대상을 추측하지 않는다. 변환 전 원본은 state/backups에 한 번 보존하고 원장 저장 실패 시 원본을 유지한다. v1은 기존 검증 이관을 거쳐 v3로 감싼다.
- 모든 읽기/수정/확정은 원장 잠금 하나 안에서 검증 후 원자적으로 저장한다. revision 충돌·손상된 프로필·다른 프로필의 약 ID는 오류이며 저장하지 않는다.

## 프로필·일정·복용 · SPEC-FAM-002 / SPEC-FAM-003

- profiles.py create/list/update로 가족을 관리한다. 현재 프로필이 하나면 생략할 수 있다. 둘 이상이면 목록·등록·변경·mark/skip에 --profile이 필요하며 별칭도 정확히 일치할 때만 선택한다. 추측·유사 이름 선택은 하지 않는다.
- due와 전체 일정 조회, calendar-plan은 프로필 생략 시 모든 프로필의 결과를 각각 반환한다. 알림과 결과에 profileId·profileAlias를 넣고 말투에는 별칭을 붙인다. ‘먹었어’의 범위는 선택한 프로필뿐이다.
- 외부 회차/캘린더 key는 {profileId}|{기존key}다. 하위 복용 기록 ID는 그대로 유지한다. 외부 응답의 doseId도 선택한 프로필 안에서만 해석한다. 다른 프로필의 scoped key를 넘기면 거부한다.
- 캘린더 제목에 [별칭]을 붙인다. migration의 legacyKey와 기존 eventId를 함께 제공해 기존 일정을 새로 중복 생성하지 않고 수정한다. 외부 fingerprint는 프로필과 변환된 일정 내용을 포함한다. 낡은 ack를 거부하고 같은 ack 반복은 무해하다. 프로필 별칭 변경도 기존 캘린더를 갱신 대상으로 만든다.
- 캘린더는 계속 ‘눈물이 테스트’에만 실제 반영한다. 이 도구는 Google 앱을 호출하지 않으며 실제 성공 뒤에만 ack한다. 프로필별 새 캘린더를 자동 생성하지 않는다.

## 말로 약·캘린더 등록 · SPEC-VOICE-001 / SPEC-VOICE-002 / SPEC-CAL-001

- 그록이 음성/텍스트에서 명령 JSON을 만들고 intake.py propose로 검증·초안을 저장한다. action, profileId, source(chat|voice), requestId와 해당 payload만 받는다. action은 medicine.add/schedule/stop/clear/delete, dose.mark/skip, appointment.add/update/cancel이다. 구조화 요청이 자연어 인식 자체의 정확성을 보장하지 않는다.
- 제안은 실제 약·예약을 저장하지 않는다. 분리된 복사본으로 검증·미리보기만 만든다. draft에는 대상·내용·revision·15분 만료·상태를 고정한다. 원문 음성·전체 전사는 저장하지 않는다.
- confirm --accept는 사용자의 실제 동의 또는 모든 값이 명확한 직접 실행 요청을 그록이 확인한 뒤에만 호출한다. 모델이 만든 동의는 근거가 아니다. 만료·원장 변경·대상 변경·취소·이전 초안은 저장하지 않는다. confirm 재실행은 같은 결과이며 중복 약/예약을 만들지 않는다. 정정은 replace로 이전 초안을 무효화한다.
- 약 등록 입력은 기존 dosePerTime·기간·시각 검증과 공식 후보 선택 규칙을 유지한다. 임의 품목코드/용량 추정은 하지 않는다. 모든 수정은 선택 프로필에 한정한다.
- 캘린더 일정은 id·title·start/end 또는 allDayDate·confirmedAt·cancelledAt·history를 가진다. 병원 시스템에 접수하거나 실제 예약을 확정하는 기능은 아니다. 제목은 일반 목적만 쓰고 병원 식별정보는 저장하지 않는다. 시작/종료는 시간대가 있는 ISO, 종료는 시작 뒤다. all-day와 시간제 필드는 섞지 않는다. key는 appointment|id다. 변경/취소는 실제 캘린더 대기 작업으로 반영하고 저장 실패와 실제 Calendar 실패를 구분한다.
- 추가 사용자 요청(15:07~15:08 전달): 할머니의 병원 방문에 엄마가 동행하는 경우와 일주일 전·하루 전 알림을 지원한다. caregiver는 별칭이고, reminderDays 기본 [7,1], reminderTime 기본 09:00(Asia/Seoul)이다. 일수는 중복 없는 0~365 정수 최대7개, 시각은 HH:mm이다. due가 해당 날짜·시각 이후 현재 계정에 한 번 보낸다. 등록 전에 지난 알림을 소급 전송하지 않고, 변경한 날짜로 재계산하며 취소 후에는 보내지 않는다. Google 자체 푸시 설정을 변경했다고 표시하지 않는다.
- `intake.py appointments --profile`로 현재 방문/일반 일정의 실제 ID를 조회한다. 조용한 시간에는 알림을 반환하지 않고 해당 알림 날짜의 07:00~23:00 안에서만 전송한다. `sentReminders`에 날짜·일수·시각별 기록을 남긴다. `reminderDays=[]`는 해제다. 변경 history는 날짜·동행·알림 설정을 보존한다. 실제 알림 실행은 Grok 순찰 루틴이 담당하며 설치/단위 테스트만으로 실행 중이라고 표시하지 않는다.

## 공식 DUR · SPEC-DUR-001

- dur_lookup.py는 선택 프로필의 약 또는 명시적으로 확인한 품목 목록을 검사한다. 다른 가족의 목록을 자동 합치지 않는다. 품목코드 없는 직접 입력 약은 미확인으로 표시한다. 임의 코드·제품·함량을 생성하거나 조회 목적으로 약을 자동 등록하지 않는다.
- 공식 DURPrdlstInfoService03의 INTERACTION(병용), AGE(연령), ELDERLY(노인), PREGNANCY(임부)를 필요한 조건에 따라 조회한다. 이름·e약은요 설명만으로 DUR 결과를 만들지 않는다.
- totalCount·pageNo·페이지별 수·ITEM_SEQ·유형·내용·상대 품목코드를 검증하고 전체 페이지를 확인한다. 반복 페이지·출처 불일치·일부 페이지 실패·상한 초과는 완전 조회로 보지 않는다. 한 유형 최대100페이지/10,000행이며 초과는 미확인이다.
- 병용은 두 실제 품목코드를 대조한다. 연령은 확인된 나이와 단순 세/개월 단일 조건만 대조하며 복합 조건은 UNKNOWN이다. 노인/임부 조건 미확인은 조건부 안내로 남기고 별칭·성별로 추측하지 않는다.
- 결과는 lookup=ALL/PARTIAL/FAILED/NO_DRUG, result=FOUND/NONE/UNKNOWN, 조회 수·미확인 품목/유형·출처·조회 시각·공식 갱신일을 반환한다. FAILED/PARTIAL·UNKNOWN은 ‘금기 없음’이 아니다. NONE은 확인한 범위의 미발견이며 안전/복용 가능을 보장하지 않는다. 진단·처방·용량 조절은 하지 않는다.
- 공식 유형 분류만 있는 행(SPEC-DUR-002): 품목코드가 정확하고 TYPE_NAME이 요청한 연령/노인/임부 분류와 일치하며 성분명이 있지만 PROHBT_CONTENT가 null/공백인 경우가 있다. 이를 `detailStatus=TYPE_ONLY`로 보존한다. 내용이 있는 행은 PROVIDED, 내용/상대품목이 없는 병용 호환 행은 EMPTY_INTERACTION이다. 분류만 있는 행도 공식 갱신일·성분을 유지하고 출처별 missingDetailCount로 센다. 적용 조건이 있는 경고는 content=null, 상세 미제공 note, match=UNKNOWN으로 반환하며 NONE으로 누락하지 않는다. lookup=ALL은 모든 요청/페이지를 수집했다는 뜻이고 상세의 충분성은 result/detailStatus로 구분한다. 다른 유형 분류, 품목 불일치, 성분도 없는 빈 행, 숫자 등 잘못된 상세 필드는 기존 오류로 유지한다. 외부 웹페이지의 내용을 API의 상세값으로 자동 채우지 않는다.
- 직접/노트북 중계 모두 같은 코드와 결과 계약이다. 중계에는 operation=dur, type·itemSeq·pageNo만 추가 허용하고 공식 경로·100행 페이지 크기를 고정한다. 공식 키·환자 프로필/나이/임신 조건은 중계에 보내지 않는다. 조건 대조는 그록 컴퓨터의 Python이 한다.
- PublicDataClient.get_page/RelayClient.get_page가 페이지 메타데이터를 제공한다. 기존 get과 permit/explain 응답은 유지한다. 공급자 실패를 다른 이름 재검색이나 직접 API fallback으로 감추지 않는다.

## 검증

- 기존 단위/CLI 계약 중 v1/v2 순수 함수는 유지한다. 가족 CLI는 v3 포맷과 scoped key로 갱신한다.
- 같은 품목·같은 시각의 두 가족을 등록해도 mark/skip/stop/삭제·재촉·캘린더가 섞이지 않아야 한다. 프로필 ID/별칭 오류·소속 불일치·동시 저장·낡은 revision·v2 이관·기존 Calendar ID/legacyKey 보존을 검사한다.
- voice/chat 제안만으로 실제 약/예약이 생기지 않아야 한다. 동의·정정·취소·만료·재실행·원장 변경과 예약 생성/수정/취소의 작업을 검사한다.
- DUR 4유형·두 품목 대조·조건 UNKNOWN·빈 전체 응답·다중 페이지·반복/누락/잘못된 행·부분 실패·키 없음·중계 인증/범위/비밀값 차단을 fixtures와 가짜 HTTP로 검사한다. 단위 테스트는 네트워크·실제 키를 사용하지 않는다.
- 실제 음성 인식·Grok 앱의 스킬 설치·실제 Google Calendar와 공식 API 승인 범위는 단위 테스트와 분리해 보고한다. 책상 친구는 구현하지 않는다.
