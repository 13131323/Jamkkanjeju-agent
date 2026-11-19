# 장소 방명록 분석 Tool

## 범위

1번 Tool `analyze_location_guestbooks`만 구현한다. Spring에서 장소 전체와 장소에 연결된 ACTIVE 방명록 전체를 읽고, 로컬 모델로 각 방명록을 분류한다. 원문 조회 및 보고서 Tool은 후속 작업이다.

## 흐름

`Tool.invoke({})` → 공유 `SpringClient.get_stored_locations()` → `get_location_guestbooks()` → `analyze_guestbooks()` → 공유 로컬 `KoreanSentimentClassifier.classify()` → `analysis_store.save()` → 아래 결과 반환.

- HTTP 요청은 고정 `X-Agent-Token`을 사용한다. `GUESTBOOKS_API_URL`을 생략하면 장소 조회 API와 같은 서버의 `/api/v2/agent/guestbooks`를 사용한다.
- 전체 페이지의 형식·순서·중복을 검증한다. 여러 요청 사이 DB 변경을 고정하는 보장은 없다.
- null·빈 문자열·공백은 미확인이다. 글이 있으면 긍정 또는 부정으로 분류한다.
- 길바닥 방명록·INACTIVE 방명록·장소 연결이 사라진 방명록은 분석 대상에서 제외한다.
- 부정 비율은 **부정 수 / 미확인 포함 전체 방명록 수**이며 반올림하지 않는다. 방명록 없는 장소도 포함하며 비율은 null이다.
- 조회 또는 분석 실패 시 부분 스냅샷을 저장하지 않는다. DB 변경은 없다.

## 스냅샷 및 Tool 반환 형식

```json
{
  "snapshotId": "guestbook-analysis-UUID",
  "createdAt": "2026-11-19T14:00:00+09:00",
  "locations": [{
    "locationId": 3,
    "locationName": "예시 장소",
    "positive": [101, 102],
    "negative": [103, 104, 105, 106, 107, 108, 109],
    "unknown": [110],
    "positiveCount": 2,
    "negativeCount": 7,
    "unknownCount": 1,
    "totalCount": 10,
    "negativeRatio": 0.7
  }]
}
```

원문은 조회·분류 중 일시적으로만 사용한다. 스냅샷·Tool 반환에는 원문, 모델 점수, 개별 sentiment 필드가 없다. 스냅샷은 프로세스 메모리에 보관하며 `/clear` 또는 CLI 종료 시 삭제한다. 반환 사본을 수정해도 보관된 원본은 바뀌지 않는다.

## 검증 계획

- Tool: 전체 수 대비 비율, 미확인 ID, 빈 장소, 원문·점수 비저장, 사본 격리, CLI 초기화.
- 오류: 조회·분류·장소 불일치·중복 실패 시 저장 금지.
- 클라이언트: 모든 페이지 순회, 고정 인증 헤더, 잘못된 페이지·스키마·정렬·ID·HTTP 오류 거부.
- 기존 로컬 분류기 및 대화·공유 클라이언트 회귀.

## 실행 결과

2026-11-19: Python 관련 테스트 45개 통과. Spring 컴파일 통과. Spring API 통합 테스트 결과는 서버 agent/docs/TEST.md에 기록한다. Gemini/OpenAI 호출 없이 검증한다.

Spring 새 방명록 API와 기존 장소 조회 API 통합 테스트 23개가 통과했다. 최초 Docker 종료에 따른 접속 실패는 Docker 시작 후 재검증했다. 테스트용 임시 DB는 자동 삭제됐다. 실행 중인 app 컨테이너는 재빌드하지 않았으므로 실제 연결 전 Spring 재빌드가 필요하다.
