# Grok Bot · 책상 버튼 수신 연결용 프롬프트

코드 개발은 Codex가 한다. Grok은 설치·네이티브 웹훅·스킬·Calendar 연결을 담당한다. 실제 URL·키·토큰·프로필 ID는 공개 문서에 넣지 않는다. 아래를 기존 봇에 전달한다.

```text
눈물이그록그록, Codex가 책상 버튼의 백엔드 수신 검증 도구를 구현했어. 너는 코드를 수정하지 말고 봇 연결만 담당해.

앞서 잘못 보낸 Windows/desk/OLED/USB/펌웨어 개발 지시는 취소해. 기존 파일·커밋은 보존하고 그 파일을 더 수정하거나 커밋하지 마. 네가 시작한 관련 작업이 남아 있으면 정체를 확인해 안전하게 중지하고, 다른 Codex 작업·프리뷰 서버·공공데이터 중계·터널은 종료하지 마.

1. /workspace/nunmul에서 변경 파일 이름과 main 브랜치를 확인한 뒤 git pull --ff-only origin main을 실행해. .env/state를 보존하고 충돌을 임의로 덮어쓰지 마. HEAD와 python3 -m unittest 결과를 알려 줘. 실패하면 코드를 고치거나 우회하지 말고 오류를 보고해.
2. docs/desk-receiver.md와 README를 읽어. 가족 원장이 v3인지 profiles.py list로 확인하고, 사용자와 보드에 연결할 정확한 프로필 ID를 정해. 실제 가족 정보를 시험용으로 만들지 마.
3. 이 봇 소유의 책상 버튼용 네이티브 웹훅 루틴을 한 개만 연결해. 기존 루틴이 있으면 갱신하고 중복 생성하지 마. Grok Bot 루틴의 Webhook 항목에서 POST URL과 별도 key, Authorization: Bearer 헤더를 실제로 확인해. 임의의 URL/소켓 서버나 다른 제품의 HMAC 서명 방식을 만들지 마. 지원되지 않으면 필요한 조치를 보고해. URL과 key는 사용자 보안 전달로 노트북 담당에게 연결해. 노트북 설정 이름은 DESK_WEBHOOK_URL·DESK_WEBHOOK_KEY이며 공공데이터 중계 토큰과 다른 값이야. 일반 채팅·스킬·공개 템플릿·로그에 값을 넣지 마.
4. 웹훅은 profile-key-v1 JSON을 그대로 state/inbox의 임의 안전한 파일명에 저장하고 아래 CLI를 실행하게 해. 파일명·셸 명령은 네가 안전하게 생성하고 요청의 문자열을 셸 코드로 이어 붙이지 마.
   python3 tools/desk_event.py --profile <설정에 고정한 실제 ID> --in <이벤트파일>
   --profile은 payload에서 가져오지 말고 운영자가 이 보드에 연결한 설정을 사용해. key/doseIds/medicineIds/requestId를 재계산·추측·보충하지 마. CLI 실패를 기존 mark나 가장 가까운 회차 선택으로 우회하지 마. 실제 수신에는 가짜 --now를 사용하지 마.
5. HTTP200은 접수야. CLI 종료코드와 recorded/replayed/notify를 확인해. recorded=true가 아니면 복용 완료라고 말하지 마. notify=true일 때만 새 복용 확인 say를 전하고 같은 요청 재실행에는 다시 보내지 마. calendarChanges는 실제 반영 후 calendar-ack해. 기록 성공과 Calendar 실패를 구분해 보고해.
6. 보드 일정 파일은 아래 도구로 사용자와 정한 해당 프로필의 v3 결과를 내보내서 전달해. 원장과 .env를 복사하지 마.
   python3 tools/make_schedule.py --store state/medication_store.json --profile <같은 ID> --out state/exports/desk-schedule.json
   이 파일을 사용자가 노트북 desk worktree에 가져갈 수 있게 제공해. 자동 동기화가 없으므로 등록/부분 확인/중단/삭제/일정 변경 후에는 다시 내보내야 해. 파일을 주고받지 않았는데 동기화 완료라고 말하지 마.
7. 우선 별도 임시 원장과 가상 프로필/약으로 이벤트 최초 처리·같은 이벤트 재실행·다른 가족·바뀐 일정 거부를 확인해. fixtures/desk_event.json의 ID는 합성 예시라 실제 원장에 그대로 보내지 마. 실제 버튼 연결 검사는 사용자가 지정한 시연 프로필에 한정해.
8. 결과는 코드/테스트, 네이티브 웹훅 생성·접근 제어, 고정 프로필, 일정 파일 전달, 실제 버튼 접수, CLI 기록, Calendar 반영을 각각 나눠 보고해. 아직 하지 않은 단계는 미확인으로 남겨.

Windows/desk 파일·OLED·USB·펌웨어·프로세스는 Codex 책상친구 채팅이 전담해. 네가 그 파일을 수정·커밋하거나 장치를 제어하지 마. 키·토큰·인증 헤더·전체 요청 URL·원시 오류는 출력하지 마.
```
