# 눈물이그록그록 · 중계 설정을 정식 도구로 전환

아래는 봇에 붙여 넣는 운영 프롬프트다. 실제 주소·토큰은 이 문서에 넣지 않는다. 최초 적용 후 약·공식 조회·DUR 스킬에도 같은 규칙을 저장한다.

```text
눈물이그록그록, Codex가 중계 설정 유지 기능을 정식 Python 도구에 반영했어. 아래 순서로 적용해.

1. /workspace/nunmul에서 현재 브랜치와 변경 파일 이름만 확인하고 .env와 state/를 보존해. git pull --ff-only origin main으로 업데이트한 뒤 git rev-parse HEAD와 python3 -m unittest 결과를 알려 줘. 충돌·실패를 임의로 덮어쓰지 마. 책상친구나 노트북 중계 프로세스는 건드리지 마.

2. docs/rx-relay.md와 이 문서의 설정 유지 규칙을 읽어. 네가 만든 tools/rx_env_check.sh 원본은 Codex가 아직 직접 확보하지 못했으므로 검토 완료로 간주하지 마. 이 임시 파일은 더 실행하거나 수정·커밋·푸시하지 마. 원본·비밀값이 포함될 수 있는 내용을 채팅이나 공개 파일에 출력하지 마. 이후 조회는 아래 정식 Python 명령만 사용해.

3. 기존 보안 입력에 저장된 PUBLIC_DATA_RELAY_URL과 PUBLIC_DATA_RELAY_TOKEN 두 개를 같은 실행 환경으로 다음 명령에 전달해.
   python3 tools/relay_config.py save
   값이 정말 없을 때만 보안 입력 카드로 해당 값을 요청해. 일반 채팅·명령 인자·코드에 실제 값을 쓰지 마. 기존 .env는 수정하지 마.
   RELAY_CONFIG_EXISTS이면 무조건 덮어쓰지 말고 status부터 확인해. 실제 운영 주소나 토큰을 갱신해야 할 때만 두 값을 함께 전달하고 save --replace를 사용해.

4. 별도 실행에서 python3 tools/relay_config.py status를 실행해 configured=true, mode=relay, source=saved를 확인해. 실제 주소·토큰·파일 본문·인증 헤더는 보여 주지 마. 설정은 비공개 state/runtime/rx-client/connection.json에서 이름 검색·설명·DUR가 공통으로 읽어. 이후 개별 조회마다 임시 셸 파일을 경유하지 마. RELAY_CONFIG_CONFLICT·설정 파일 누락/손상/권한 오류는 code만 보고해. 직접 API나 직접 입력 등록으로 몰래 바꾸지 마.

5. 실제 약 원장·캘린더를 바꾸지 않는 공개 제품 연결 시험만 해.
   - state/probes/ 아래 별도 입력·결과 파일로 타이레놀정500밀리그램, 지르텍정, 베아제정의 이름 검색을 python3 tools/rx_lookup.py --saved-relay --in <별도 입력> --out <별도 결과>로 실행해.
   - 다음 명령에는 두 중계 변수를 따로 넣지 않고, 기존 보안 환경변수도 제거한 자식 프로세스에서 실행해 저장된 한 쌍만으로 동작하는지 확인해. 토큰을 출력하는 env/printenv/set 명령은 쓰지 마.
   - 이름 검색이 실제 반환한 타이레놀 itemSeq로 python3 tools/rx_lookup.py --saved-relay --explain <itemSeq>를 실행해. 코드·갱신일을 추측하지 마.
   - DUR도 격리된 합성 프로필/원장에서만 실행해. 기존 시연용 프로필에 삭제한 약을 다시 등록하거나 실제 복용·가족 조건을 수정하지 마. 공식 조회가 반환한 품목만 사용하고 조건을 추측하지 마.

6. 약 등록·공식 조회·DUR 스킬을 고쳐서, 정식 Python 명령이 저장된 연결을 자동으로 읽는다고 기록해. 모든 rx_lookup.py·dur_lookup.py 조회에는 --saved-relay를 붙여. 설정 디렉터리 전체가 사라져도 직접 API로 넘어가지 않는 옵션이야. 임시 rx_env_check.sh를 필수로 부르는 규칙은 제거해. 컴퓨터 초기화로 state/가 사라졌으면 보안 입력 후 다시 설정해야 한다는 점도 기록해. 코드 개발·커밋·푸시는 Codex가 담당하고 너는 봇 설정·명령 실행·사용자 안내만 해.

7. 커밋, 테스트 수/결과, 중계 설정 상태(값 제외), 세 이름 검색 상태, 설명 조회 상태·출처·updateDe, 격리 DUR 조회 상태와 미확인 항목을 보고해. 실패는 정확한 오류 code로 알려 줘. 기존 약 원장·복용 기록·캘린더와 .env를 바꾸지 않았는지도 보고해.
```

기존 직접 조회용 명령은 호환을 유지한다. 봇 명령은 `--saved-relay`와 `status` 확인을 사용하며 configured=false이면 조회를 시작하지 않고 보안 입력으로 재설정한다.
