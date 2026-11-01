# 채팅 알림용 표정

기존 마스코트 원본을 덮어쓰지 않고 선택한 공개 사본만 이 폴더에 둔다. 개인정보·약 정보·키가 없는 고정 PNG이며 실행 중 생성 API를 호출하지 않는다.

- `step1-teary-v1.png`: 기존 두 방울 얼굴을 참고해 built-in image_gen으로 생성한 글썽 얼굴. 정확한 프롬프트는 같은 이름의 `.prompt.txt`다.
- `step2-one-tear-v1.png`: 사용자가 제공한 `nunmuri-grok-2d-one-tear-v1.png`의 사본.
- `step3-two-tears-v1.png`: 기존 `nunmuri-grok-2d-v2.png`의 사본.
- `taken-smile-v1.png`: 기존 `nunmuri-grok-2d-smile-v1.png`의 사본.

실행 파일과 SHA256은 `config/notification_images.json`, 반환/첨부 계약은 `docs/notification-images.md`를 따른다. Grok Bot을 참고한 이 프로젝트의 표정 변형이며 공식 원본 아트라고 표시하지 않는다.
