# 장소 동기화 Tool과 Spring 계약

Python 에이전트와 Spring API 구현이 준비되어 있다. Spring 서버 재빌드 시 V6 마이그레이션을 적용해야 호출할 수 있다. Tool 실행 성공은 정상 서버 응답으로만 판단한다.

## 실행 흐름

1. `get_stored_locations`와 `get_tourapi_locations`로 전체 조회 스냅샷을 얻는다.
2. `compare_locations(resultType=all)`가 집합 비교와 삭제 상세 확인을 한 번 수행한다. 확인된 추가·삭제 결과를 메모리의 변경 계획으로 고정하고 `planId`를 반환한다. 부분 비교는 실행 계획을 만들지 않는다.
3. 동기화 요청이면 LLM은 별도 허가 질문 없이 `synchronize_locations({planId})`를 호출한다. 이 Tool은 DB·TourAPI 조회, 집합 비교, 삭제 상세 확인을 반복하지 않는다.
4. 실제 CLI가 해당 계획의 추가·삭제 전체 목록, requestId, 연관 데이터 영향을 보고하고 바로 `(y/n)`을 묻는다. 보고와 승인을 이 한 단계에서 처리한다.
5. `y`(대문자 Y도 허용)이면 한 번 호출하고 `n`이면 취소한다. 다른 입력은 실행하거나 취소하지 않고 y/n 재입력을 안내한다. EOF·Ctrl+C는 취소한다.
6. 정상 응답의 requestId·추가/삭제 contentId·건수가 승인 계획과 일치한 경우에만 완료한다.

승인은 프롬프트 권고가 아닌 실행 코드의 필수 조건이다. LLM이 `approved=true`를 인자로 보내거나 도구 결과에 승인 문구를 넣어도 승인되지 않는다. 승인 경로가 없는 직접 Tool 호출은 차단한다. 여러 실행이 동시에 터미널 승인을 요청할 수 없다.

계획은 비교 결과의 사본으로 보관한다. 반환된 비교 결과·승인 화면의 데이터를 바꿔도 실행 계획은 바뀌지 않는다. requestId도 계획을 만들 때 고정하며, Spring 요청 Body는 기존 계약대로 원본 스냅샷 ID와 변경 목록을 포함한다. `planId`는 에이전트 내부 참조이므로 Spring 스키마를 바꾸지 않는다.

동일 계획으로 완료한 작업은 기존 결과를 반환하고 다시 POST하지 않는다. 취소한 계획도 다시 승인 질문을 하지 않는다. 실패 후 반영 여부가 불명확하면 같은 계획의 재호출을 차단한다. `/clear`·프로세스 종료는 스냅샷·계획·로컬 실행 기록을 지우므로, 실패 후에는 먼저 requestId로 서버 상태를 확인해야 한다. 서버 멱등성을 대체하는 기능은 아니다.

## 설정

```dotenv
LOCATION_SYNC_API_URL=http://localhost:8080/api/v2/agent/locations/sync
AGENT_API_TOKEN=Spring과_동일한_고정_토큰
```

requestId는 location-sync- 뒤 소문자 16진수 32자리이며 기존 Tool이 UUID로 생성한다. 새 토큰을 발급받지 않고 기존 에이전트 인증을 재사용한다. 실제 토큰은 문서·로그에 출력하지 않는다.

## HTTP 계약

`POST /api/v2/agent/locations/sync`

- `X-Agent-Token`: 기존 에이전트 전용 토큰
- `Idempotency-Key`: 본문의 requestId와 동일
- `Content-Type: application/json`

```json
{
  "requestId": "location-sync-실행별고유값",
  "storedSnapshotId": "db-조회결과ID",
  "tourapiSnapshotId": "tourapi-조회결과ID",
  "addLocations": [
    {
      "contentId": 3000001,
      "title": "추가할 장소",
      "address": "제주시 테스트 주소",
      "category": "음식",
      "radius": 100,
      "latitude": 33.5,
      "longitude": 126.5
    }
  ],
  "deleteLocations": [
    {"locationId": 11, "contentId": 3000002}
  ]
}
```

snapshotId는 에이전트 프로세스의 메모리 참조이며 Spring이 해당 원본을 조회할 수 있다는 뜻은 아니다. 서버는 요청 동일성 해시에 참조 ID를 포함하고 현재 DB의 대상 동일성·중복을 검증하며 TourAPI를 다시 호출하지 않는다. 실행 대상은 본문의 검증된 목록이다. 삭제 항목의 locationId와 contentId를 모두 확인해 다른 행을 삭제하지 않아야 한다. 에이전트는 SQL을 만들거나 DB에 직접 연결하지 않는다.

### 정상 응답: 200

```json
{
  "requestId": "location-sync-실행별고유값",
  "status": "COMPLETED",
  "addedCount": 1,
  "deletedCount": 1,
  "addedContentIds": [3000001],
  "deletedContentIds": [3000002]
}
```

응답은 승인한 전체 대상과 일치해야 한다. 변경 대상이 없으면 Tool이 `NO_CHANGES`를 반환하고 승인 질문·POST를 생략한다.

## Spring에서 보장하는 사항

- 에이전트 고정 토큰 인증·인가와 요청 검증.
- requestId 멱등성: 같은 requestId·같은 본문은 한 번만 반영하고 동일 결과를 반환한다. 같은 requestId의 다른 본문은 충돌로 거부한다.
- 실행 직전 삭제 대상의 동일성·추가 대상 중복·전체 계획 유효성을 재검증한다. 비교 후 상태가 달라졌다면 임의로 변경 대상을 추가하지 않고 409로 거부해 새 비교와 승인을 요구한다.
- 추가·장소 삭제·관련 미션 비활성화·진행도 및 MISSION_REWARD_STATUS 삭제를 단일 트랜잭션으로 처리한다. 하나라도 실패하면 전체 롤백한다.
- 이미 지급한 캐릭터·포인트·획득 쿠폰 보상은 회수하지 않는다. 기존 FK에 따른 나머지 참조 처리도 서버에서 수행한다.
- 처리 내역을 requestId로 확인할 수 있도록 보관한다. 에이전트의 승인 UI가 현재 서버 검증·트랜잭션을 대신하지는 않는다.

404/405/501은 미구현 가능성, 401/403은 인증·권한, 409는 충돌로 안내한다. 타임아웃·5xx·성공 응답 불일치는 반영 여부 미확인으로 처리한다. POST는 어떤 경우에도 자동 재시도하지 않는다.

## 검증

실제 Gemini·TourAPI·Spring·DB를 호출하지 않고 MockTransport와 ScriptedModel로 검증한다. 승인 누락·취소·승인 사본 변조·정확한 대상 전송·중복 실행 차단·오류 및 타임아웃·응답 불일치·실제 LangChain 실행 중 CLI 승인 전달을 포함한다.
