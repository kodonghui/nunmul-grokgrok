# Grok 가족 v3 후속 · DUR 상세 미제공 행 수정

기존 가족 v3를 연결한 봇에만 보낸다. 전체 이관·역할 설정을 다시 할 필요는 없다.

```text
눈물이그록그록, Codex가 게보린정의 INVALID_DUR_ROW 원인을 확인하고 수정했어. 공식 API에 임부 분류·성분·갱신일은 있으나 상세 내용이 null인 경우였어. 다음 순서로 반영해.

1. /workspace/nunmul의 브랜치와 변경 파일 이름을 확인하고 main에서 git pull --ff-only origin main을 실행해. .env/state를 보존하고 충돌/미커밋 변경을 임의로 덮어쓰지 마. git rev-parse HEAD와 python3 -m unittest 결과를 알려 줘. 수정 기준 테스트는198개야.
2. docs/family-v3.md의 SPEC-DUR-002와 README, docs/grok-bot-v3-prompt.md의 추가된 TYPE_ONLY 지침을 읽고 기존 공식 조회·DUR 스킬에 반영해. 정확한 공식 유형·성분은 있으나 상세가 없는 행은 TYPE_ONLY/content=null/note/match=UNKNOWN이야. lookup=ALL이어도 UNKNOWN일 수 있고 금기 없음이 아니야. 상세나 등급을 지어 채우지 마.
3. docs/grok-bot-relay-setup.md대로 기존 PUBLIC_DATA_RELAY_URL과 보안 환경의 PUBLIC_DATA_RELAY_TOKEN을 같은 실행 환경으로 python3 tools/relay_config.py save에 전달해 한 번 저장해. status가 configured=true/mode=relay/source=saved인지 확인해. 모든 rx_lookup.py·dur_lookup.py 조회에 --saved-relay를 붙이고 임시 rx_env_check.sh는 사용하지 마. 보안 값이 없으면 보안 입력 기능으로만 요청해. .env를 수정하거나 토큰/헤더/전체 URL/원시 오류를 출력하지 마. 실패 시 직접 API로 전환하지 마.
4. 실제 원장 대신 state/probes/dur-recheck/ 아래에 새 격리 시험 원장을 사용해. 같은 디렉터리의 입력 JSON에 {"items":[{"name":"타이레놀정500밀리그램","itemSeq":"202106092"},{"name":"게보린정","itemSeq":"197900277"}]}을 준비해.
   python3 tools/dur_lookup.py --store state/probes/dur-recheck/medication_store.json --in state/probes/dur-recheck/items.json --out state/probes/dur-recheck/result.json
   Codex의 별도 노트북 중계 시험은 8요청 모두 OK, lookup=ALL/result=UNKNOWN, 게보린 임부 TYPE_ONLY1건·갱신일2025-11-10이었어. 네 실제 결과를 확인해서 보고하고 이 예상값을 그대로 답하지 마. 프로필의 실제 임신 여부를 추측하거나 기록하지 마.
5. 실제 원장/Google에 영향을 주지 않는 별도 합성 시험에서 방문1일 전09:00 알림이 한 번, 같은 시각 두 번째 실행은0건인지 확인해. 기존 실제 순찰에 가상 시각을 넣지 마.
6. 기존 ‘울보 순찰’ 한 개의 설정을 유지하고 실제 실행 기록과 다음 실행 시각을 확인해. 첫 실행 성공/아직 미실행/실패를 구분해. 루틴이나 스킬을 중복 생성하지 마. 실제 음성·Google Calendar 반영은 별도 미확인으로 남겨.

보고는 커밋·테스트, DUR의 조회 완료 수/전체 수·lookup/result·TYPE_ONLY와 갱신일, 합성1일 알림, 순찰 실행 상태 순서로 해. 원장·Calendar에는 이 조회 시험의 약이나 일정을 등록하지 마.
```
