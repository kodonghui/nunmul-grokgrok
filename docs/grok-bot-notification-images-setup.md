# 눈물이그록그록 · 채팅 표정 이미지 연결 프롬프트

아래 전체를 기존 봇에 붙여 넣는다. 코드는 표정 파일을 선택하며, Grok의 실제 PNG 첨부 성공은 별도로 확인한다.

```text
눈물이그록그록, Codex가 복용 알림에 우는·웃는 이미지를 붙이는 기능을 구현했어. 아래 순서로 기존 봇에 적용해.

1. /workspace/nunmul에서 브랜치와 변경 파일 이름을 확인하고 .env·state/를 보존한 채 git pull --ff-only origin main을 실행해. git rev-parse HEAD와 python3 -m unittest 결과를 보고해. 미커밋 변경·충돌·실패를 임의로 덮어쓰지 마. 책상친구·노트북 중계·터널은 건드리지 마.

2. docs/notification-images.md를 읽고 python3 tools/notification_images.py를 실행해 공개 PNG 4개의 검증된 경로를 확인해. 새 이미지 생성 API나 추가 Python 패키지·키는 필요 없어. 자산 검증이 실패하면 오류 code를 보고하고 운영 원장에 due/mark를 실행하지 마.

3. 기존 ‘울보 순찰’ 한 개를 갱신해. 새 루틴을 복제하지 말고 Asia/Seoul의 현재 5분 주기와 조용한 시간을 유지해. 매 실행 실제 현재 시각으로 다음을 한 번만 호출해.
   python3 tools/dose_state.py due --store state/medication_store.json --now <실제 현재 ISO 시각> --with-images
   send가 비어 있으면 조용히 끝내. 각 send.say를 보내고 image가 있으면 image.path의 PNG 파일 자체를 채팅에 첨부해. 캡션이 가능하면 같은 메시지로, 아니면 같은 회차의 문구와 이미지를 이어서 보내. 파일 경로나 alt만 쓰고 그림을 보냈다고 말하지 마. image가 없는 방문·재방문·MISSED 알림에는 우는 그림을 임의로 붙이지 마.

4. 사용자 ‘먹었어’의 실제 처리에도 --with-images를 적용해.
   - 직접 mark 경로: python3 tools/dose_state.py mark <실제 회차key> --profile <실제 ID> --store state/medication_store.json --source chat --with-images
   - 공통 초안 경로: 기존 dose.mark 제안→사용자 확인 후 python3 tools/intake.py confirm <실제 draftId> --profile <실제 ID> --store state/medication_store.json --accept --with-images
   두 경로를 같은 복용 확인에 중복 실행하지 마. 명령이 성공하고 반환 결과에 image가 있을 때만 웃는 PNG를 첨부해. 부분 확인이나 이미 기록한 요청의 재실행에는 image가 없으니 전체 완료 미소를 따로 보내지 마. Calendar는 기존 실제 반영·ack 절차를 유지하고 기록 저장과 Calendar 성공을 구분해 보고해.

5. 첫 결과를 보관해. PNG 첨부가 실패했다면 그 결과의 image.path로 첨부만 재시도해. 이미 발송 단계/복용 기록을 처리한 due·mark·confirm을 이미지 재전송 목적으로 다시 실행하지 마. 첨부 도구가 없거나 실제 표시를 확인하지 못하면 ‘문구 전송 / 이미지 미확인’처럼 사실대로 보고해.

6. 실제 원장·약 목록·Calendar를 바꾸지 않는 합성 시연으로 이미지 표시를 확인해. state/probes/ 아래 이번 시험만의 새 디렉터리를 만들고 아래 명령의 <시험 폴더>에 사용해. 기존 파일이 있으면 새 폴더를 고르고 지우거나 초기화하지 마.
   python3 tools/dose_state.py due --schedule fixtures/notification_schedule.json --doses <시험 폴더>/doses.json --now 2026-11-01T17:05:00+09:00 --with-images
   python3 tools/dose_state.py due --schedule fixtures/notification_schedule.json --doses <시험 폴더>/doses.json --now 2026-11-01T17:12:00+09:00 --with-images
   python3 tools/dose_state.py due --schedule fixtures/notification_schedule.json --doses <시험 폴더>/doses.json --now 2026-11-01T17:31:00+09:00 --with-images
   python3 tools/dose_state.py mark '2026-11-01|17:00' --schedule fixtures/notification_schedule.json --doses <시험 폴더>/doses.json --source chat --at 2026-11-01T17:35:00+09:00 --with-images
   각 결과의 PNG를 ‘합성 시연 · 실제 복용 기록 아님’이라는 설명과 함께 실제 채팅에 첨부해. 글썽→한 방울→두 방울→미소 네 장이 보이는지 확인해. 마지막 mark를 같은 인자로 다시 실행해 image가 없는지도 확인하되 미소를 다시 보내지 마. 시험의 가상 시각을 운영 원장에 적용하지 마.

7. 약 등록·복용 확인 스킬과 순찰에 이 규칙을 반영해. 아직 기존 공개 템플릿까지 자동 갱신됐다고 말하지 마. 앱 프로필 사진이 바뀌거나 우는 소리가 난다고 설명하지 마. 이 기능은 알림에 우는·웃는 PNG를 첨부하는 것이야. 책상친구의 접수 표시와 실제 복용 기록 완료를 섞지 마.

8. 현재 커밋·테스트 수/결과·스킬/기존 루틴 갱신 여부·네 이미지의 실제 첨부/표시 여부·운영 원장과 Calendar를 변경하지 않았는지 보고해. 백엔드 테스트 성공과 실제 채팅 이미지 표시 성공을 구분해. 비밀값·.env·토큰·인증 헤더·운영 원장 파일은 출력하거나 첨부하지 마.
```
