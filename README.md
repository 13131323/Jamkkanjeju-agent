# Jamkkanjeju-agent
잠깐제주 서비스 운영 업무 수행 특화 AI 에이전트

## 현재 구현 범위

자연어로 대화하는 `jamkkanjeju` CLI와 LangChain 에이전트를 구현했습니다. 저장 장소 조회(`get_stored_locations`), TourAPI 전체 조회(`get_tourapi_locations`), 스냅샷 비교(`compare_locations`), 개별 존재 확인(`get_tourapi_location`), 점검 예약 등록 요청(`schedule_maintenance`) 도구를 제공합니다. 점검 예약은 실행 중인 Spring 예약 API에 등록하며, `synchronize_locations`는 비교에서 발급한 planId의 추가·삭제 목록을 표시하고 실제 사용자의 y/n 승인을 한 번 받은 뒤 Spring 동기화 API를 호출합니다.

이 도구는 Python에서 Spring의 `GET /api/v2/agent/locations`를 호출해 실제 DB의 장소를 ID순으로 조회합니다. 사용자 로그인 JWT 대신 에이전트 전용 고정 토큰을 `X-Agent-Token` 헤더로 전달합니다. 토큰은 일반 사용자 API에 대한 권한을 주지 않습니다.

## 설치

Python 3.11 이상, PowerShell 기준입니다.

```powershell
cd D:\tour\jamkkanjeju-agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
# .env가 없는 새 환경에서만 복사합니다.
Copy-Item .env.example .env
```

Python `.env`의 `STORED_LOCATIONS_API_URL`은 로컬 기준 `http://localhost:8080/api/v2/agent/locations`입니다. `AGENT_API_TOKEN`은 Spring `.env`의 같은 변수와 일치해야 합니다. 사용자 로그인·JWT 갱신은 필요하지 않으며 `Bearer ` 접두사를 붙이지 않습니다. 두 `.env`는 Git에서 제외합니다.

현재 로컬 설정에는 양쪽에 동일한 난수 토큰을 설정했습니다. 새 환경에서는 비밀 토큰을 생성해 양쪽에 같은 값을 넣으세요. 서버 설정이 비어 있으면 에이전트 인증은 허용되지 않습니다. 키를 바꿀 때는 양쪽 설정을 바꾸고 Spring을 재시작해야 합니다. Docker 실행에서도 `compose.yaml`을 통해 변수가 전달됩니다.

이전 `SPRING_ACCESS_TOKEN` 설정은 사용하지 않습니다. 외부 서버와 연결할 때는 HTTPS를 사용합니다.

## 대화형 에이전트 CLI

새 의존성과 실행 명령을 설치한 뒤 가상환경을 활성화합니다. 현재 로컬 환경에는 설치가 완료되어 있습니다.

```powershell
cd D:\tour\jamkkanjeju-agent
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
jamkkanjeju
```

가상환경 활성화 없이 실행하려면 `.\.venv\Scripts\jamkkanjeju.exe`를 사용합니다. 프로젝트 밖에서 실행해도 Gemini 클라이언트가 이 프로젝트의 `.env`를 읽습니다.

```text
나 > 삭제될 장소 뽑아줘
[도구 호출] get_stored_locations {}
[도구 완료] get_stored_locations
[도구 호출] get_tourapi_locations {}
[도구 완료] get_tourapi_locations
[도구 호출] compare_locations {"storedSnapshotId":"db-...","tourapiSnapshotId":"tourapi-...","resultType":"deletions"}
[도구 완료] compare_locations
jamkkanjeju > 개별 상세 조회까지 확인한 삭제 검토 후보 안내
```

- 자연어 입력 → Gemini 판단 → 필요한 Tool 호출 → 결과 확인 → 답변 순서로 동작합니다. 도구 사용 여부는 LLM이 결정합니다.
- `agent.py`가 `create_agent`로 모델과 도구·시스템 지침을 연결하고, `chat.py`가 입력·출력과 대화 상태를 관리합니다.
- `/help`: 도움말, `/clear`: 대화·장소 스냅샷 초기화, `/exit`·`exit`·`quit`: 종료. 입력 대기 중 Ctrl+C나 EOF도 종료합니다. 작업 중 Ctrl+C는 해당 요청을 중단합니다.
- 최근 완료한 10개 대화 턴을 현재 프로세스에서만 유지합니다. 프로그램을 종료하면 대화는 사라집니다. API 오류로 중단된 불완전한 대화는 후속 요청에 전달하지 않습니다.
- 처리 단계를 20개로 제한합니다. 실제 추론 내용·비밀 설정은 출력하지 않고 Tool 호출·완료·오류와 답변을 표시합니다.
- 일반 채팅은 Gemini 연결만 필요합니다. 장소 조회는 Docker와 Spring 서버가 실행 중이어야 합니다. 서버가 꺼져 있으면 실패를 안내하고 대화는 계속할 수 있습니다.
- `jamkkanjeju --help`, `jamkkanjeju --version`으로 실행 옵션을 확인합니다.
- 기존 `python -m jamkkanjeju_agent`은 LLM 없는 Tool 단독 실행 명령으로 유지합니다.

## Tool 단독 실행

Spring 서버가 실행 중이어야 합니다. 모델 API 키는 필요하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m jamkkanjeju_agent
```

Python에서 직접 실행하는 방법입니다. `.env`를 사용할 때는 먼저 로드합니다.

```python
from dotenv import load_dotenv
from jamkkanjeju_agent.tools import get_stored_locations

load_dotenv()
result = get_stored_locations.invoke({})
print(result)
```

DB·TourAPI 전체 조회 Tool은 입력 없이 마지막 페이지까지 수집한 뒤 메모리에 보관합니다. Spring API 자체는 페이지 조회를 유지하며 Python의 `StoredLocationClient`가 순회합니다.
조회 Tool의 반환값은 다음처럼 원본 목록을 포함하지 않습니다.

```json
{"snapshotId":"db-...","source":"DB","status":"COMPLETE","count":20,"createdAt":"2026-11-17T16:00:00+09:00"}
```

이 ID는 장소 contentId가 아니라 현재 실행 메모리의 장소 목록을 가리킵니다. 프로세스 종료·재시작 또는 `/clear` 후에는 사용할 수 없습니다.
단독 실행 명령은 스냅샷 생성 진단용이며 종료 후 ID는 소멸합니다. 여러 Tool 비교는 같은 CLI 실행이나 같은 Python 프로세스에서 수행해야 합니다.

## Spring 조회 API 응답

```json
{
  "locations": [
    {
      "locationId": 1,
      "contentId": 2853982,
      "title": "라토커피제주",
      "address": "제주시",
      "category": "음식",
      "radius": 100,
      "latitude": 33.4987305369,
      "longitude": 126.5281672061
    }
  ],
  "locationsPage": {"page": 0, "size": 100, "hasNext": false}
}
```

이 응답은 Spring HTTP 페이지 계약이며 LLM에 직접 반환하는 Tool 응답이 아닙니다. 빈 DB는 정상 0건 스냅샷이 되지만 인증·권한·통신·응답 형식 오류는 `ToolException`으로 구분합니다. 조회 실패를 장소가 없다는 뜻으로 처리해서는 안 됩니다.

메모리 스냅샷은 수집을 끝낸 목록의 사본입니다. 페이지 순회 중 DB가 변하지 않는 트랜잭션 스냅샷을 보장하지는 않습니다. 실제 동기화 시점의 일관성 제어는 후속 단계에서 서버와 함께 설계합니다.

## 개별 테스트

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_stored_locations.py -q
```

`httpx.MockTransport`로 HTTP 요청과 응답을 검증합니다. 실제 DB나 LLM은 호출하지 않습니다.

구현 파일: `src/jamkkanjeju_agent/tools/location_tools.py`.

참고: [LangChain 공식 Tools 문서](https://docs.langchain.com/oss/python/langchain/tools).

이전 페이지 조회 버전의 검증 결과: 24개 통과 및 로컬 Spring 연동 확인. 현재의 전체 조회·스냅샷 구조 검증 결과는 문서 하단을 참고하세요.


## Gemini 환경설정

LLM 연결용 설정은 `.env`의 `GOOGLE_API_KEY`와 `GEMINI_MODEL`을 사용합니다. 현재 모델은 `gemini-3.5-flash-lite`입니다. 키 원문은 `.env.example`이나 Git에 저장하지 않습니다.

환경설정 후 Gemini Developer API의 실제 텍스트 생성 요청에서 HTTP 200과 `OK` 응답을 확인했습니다(총 6토큰). 무료 등급 지원 모델이며 실제 사용 한도·과금은 계정 설정을 따릅니다. Gemini 클라이언트를 대화형 CLI의 LangChain 에이전트와 연결했습니다. 모델 키와 Spring 통신 토큰은 별도로 관리합니다.

[공식 Gemini API 요금 안내](https://ai.google.dev/gemini-api/docs/pricing)


## Gemini 클라이언트 생성·호출

```python
from jamkkanjeju_agent.clients import create_gemini_client

client = create_gemini_client()  # 설정 로드와 객체 생성. 아직 API 호출은 하지 않음
response = client.invoke("안녕하세요")  # 이 시점에 Gemini API 호출
print(response.text)
```

`clients/gemini.py`가 프로젝트 루트 `.env`를 읽고 `GOOGLE_API_KEY`와 `GEMINI_MODEL`로 `ChatGoogleGenerativeAI` 클라이언트를 생성합니다. 이미 설정된 프로세스 환경변수는 덮어쓰지 않습니다. Gemini Developer API를 명시적으로 사용하며, 사용자 로그인이나 Spring 통신 토큰과는 별개입니다. 설정 누락은 호출 전에 오류로 알립니다. 타임아웃은 30초, 최대 재시도는 1회입니다.

현재 설치된 연동 패키지는 `langchain-google-genai` 4.5.0입니다. 클라이언트와 Tool 테스트 27개가 통과했고, 생성한 클라이언트의 실제 호출에서 `OK` 응답(총 7토큰)을 확인했습니다. 실제 요청은 테스트 스위트에 자동 포함하지 않습니다.


## CLI 검증 결과

CLI·LangChain 도구 실행 및 대화 기록·Gemini 클라이언트·장소 Tool 테스트 총 34개가 통과했습니다. 외부 모델 없는 테스트에서 실제 LangChain 그래프와 HTTP 응답 대역을 사용해 도구 호출·다음 페이지·오류 후 대화 유지·기록 초기화·비밀 정보 마스킹을 검증했습니다.

스냅샷 분리 전 버전에서는 실제 `jamkkanjeju` 명령에서 Gemini의 이름 소개와 `get_stored_locations(page=0, size=2)` 선택을 확인했습니다. 이때 Docker가 실행 중이지 않아 실제 DB 조회는 연결 실패로 안내되었으며 대화가 정상 유지·종료되는 것을 확인했습니다. 정상 DB 조회는 앞선 Tool 연동에서 확인했고, 이번 CLI 실행에서 정상 DB 결과까지 재확인한 것은 아닙니다.

```powershell
python -m pytest tests/test_chat.py tests/test_gemini_client.py tests/test_stored_locations.py -q
```


## 전체 장소 비교 Tool

`get_stored_locations` → `get_tourapi_locations` → `compare_locations` 순서로 각각의 Tool을 호출합니다.
앞의 두 Tool이 반환한 `snapshotId`를 비교 Tool의 `storedSnapshotId`, `tourapiSnapshotId`로 전달합니다.
비교 Tool은 메모리의 목록을 contentId로 비교하며 두 원본 목록을 다시 수집하지 않습니다.
`resultType`은 `all`(기본), `additions`, `deletions`입니다. 삭제 질문에서는 additions의 수천 건 원본을 응답에 포함하지 않습니다.

```python
from jamkkanjeju_agent.tools import get_stored_locations, get_tourapi_locations, compare_locations
stored = get_stored_locations.invoke({})
tourapi = get_tourapi_locations.invoke({})
result = compare_locations.invoke({
    "storedSnapshotId": stored["snapshotId"],
    "tourapiSnapshotId": tourapi["snapshotId"],
    "resultType": "deletions",
})
```
기존 CLI에서 “DB와 TourAPI 장소를 비교해 주세요”라고 요청하면 사용할 수 있습니다.

- 수집 기준: `areaBasedList2`, 제주 법정동 시도코드 `lDongRegnCd=50`, 페이지당 1000개. 기존 FastAPI의 6개 분류는 유지하지만 삭제 예정인 `areaCode=39` 조건은 사용하지 않습니다.
- 콘텐츠 유형으로 나누지 않고 전체 조회 후 `lclsSystm1`의 EV·EX·FD·SH·VE·LS만 선택합니다. 주소는 addr1+addr2, 반경은 100m입니다.
- `.env`의 `KTO_BASE_URL`, `KTO_SERVICE_KEY`, `KTO_MOBILE_OS`, `KTO_MOBILE_APP`을 사용합니다. 실제 키는 `.env.example`에 넣지 않습니다.
- 연결 오류·429·5xx는 해당 TourAPI 페이지를 최대 3회 시도합니다. API 내부 오류·잘못된 데이터·페이지 누락·전체 건수 변경·중복 ID는 실패 처리합니다.
- 완전 수집된 서로 다른 출처의 스냅샷만 비교합니다. 공통·추가·누락 건수는 항상 반환하며 `all`에는 교집합 ID와 전체 결과, `additions`에는 추가 목록, `deletions`에는 개별 확인을 거친 삭제 검토 결과를 반환합니다.
- 실패 시 ToolException으로 종료하며 부분 집합이나 빈 성공 결과를 반환하지 않습니다. 모델도 실패한 조회로 직접 비교하지 않도록 지시합니다.
- `missingLocations`는 수집 목록과 DB의 차집합입니다. 각 누락 ID를 필터 없는 `detailCommon2`로 다시 확인해 정상 0건이면 `confirmedMissingLocations`, 상세에 존재하면 `existingOutsideListLocations`로 구분합니다. 상세 재확인에 실패하면 비교 결과 전체를 반환하지 않습니다. 실제 삭제·동기화는 수행하지 않습니다.
- 두 API는 DB 트랜잭션 스냅샷이 아닙니다. 실제 삭제 작업을 구현할 때에는 점검 중 쓰기 차단 및 적용 직전 재검증이 필요합니다.


TourAPI 연동은 `clients/tourapi_client.py`의 `TourApiClient`가 담당합니다.
TourAPI 전체 조회 Tool은 `TourApiClient(http_client).get_locations()`를 호출해 메모리 스냅샷을 저장합니다.
클라이언트가 환경변수 인증 설정, HTTP 요청·재시도, 전체 페이지 수집 및 응답 검증을 처리하고,
Tool이 클라이언트 오류를 `ToolException`으로 변환하여 집합 비교를 차단합니다.
CLI는 `client_scope()`로 대화 전체의 연결 수명을 관리합니다. 각 서비스의 첫 호출 때 클라이언트를 한 번 만들고 이후 Tool에서 재사용하며, CLI 종료 시 HTTP 연결을 닫습니다. `TourApiClient` 생성만으로는 API를 호출하지 않습니다.


## 점검 예약 Tool (Spring API 연동 준비)

`schedule_maintenance`는 Spring에 점검 예약 데이터를 등록하는 도구입니다.
Spring에는 예약 API·상태 전환 스케줄러·API 차단 필터가 구현돼 있습니다. Tool 테스트에서는 HTTP 응답 대역을 사용하며 실제 사용 시 최신 Spring 서버를 실행해야 합니다.
성공 응답을 가정하거나 실제 DB에 직접 접근하지 않습니다.

- 입력: 선택 `message`(앞뒤 공백 제거 후 최대 500자). 생략·null·빈 문자열·공백은 다음 기본 문구를 사용합니다.
  `장소 데이터 동기화를 위한 서비스 점검이 예정되어 있습니다. 점검 시간 동안 서비스 이용이 제한됩니다.`
- `.env`의 `MAINTENANCE_API_URL`로 POST하고 기존 `AGENT_API_TOKEN`을 `X-Agent-Token` 헤더로 보냅니다.
- 선택 입력 `startsAt`을 KST `YYYY-MM-DDTHH:mm:ss`로 받습니다. 사용자가 지정한 시각이 있으면 그대로 전송하고, 생략·null이면 Spring이 요청 처리 시각 1시간 뒤로 정합니다. 종료 예정은 시작 1시간 뒤입니다.
- LLM에는 매 모델 호출의 최신 KST 시각을 전달해 오늘·내일을 해석하게 합니다. 이미 지난 시각·모호한 날짜는 임의로 변경하지 않고 확인하도록 지시합니다.
- 요청 1회만 전송하며 타임아웃·HTTP 오류·응답 검증 실패에 자동 재시도하지 않습니다. 저장 여부가 불분명하면 DB 예약 내역부터 확인해야 합니다.
- 예약 등록만 담당합니다. Spring이 점검 시작·종료와 API 차단(홈·로그인·토큰 재발급·에이전트 작업 API 예외)을 처리합니다. 장소 동기화는 별도로 요청해야 합니다.
- CLI를 재시작한 뒤 “점검 예약해 줘” 또는 “공지 문구를 '장소 정보 갱신을 위한 점검입니다.'로 해서 점검 예약해 줘”라고 요청할 수 있습니다.
- 예약 변경·취소, 중복 예약 처리 정책은 이번 범위에 포함하지 않습니다.

### Spring 예약 API 계약

`POST /api/v2/agent/maintenances` (`MAINTENANCE_API_URL`로 변경 가능)

```json
{"message":"장소 데이터 동기화를 위한 서비스 점검이 예정되어 있습니다. 점검 시간 동안 서비스 이용이 제한됩니다."}
```

저장 성공은 `201 Created`와 다음 응답으로 확인합니다. 시각은 기존 서비스와 동일한 KST·offset 없는 ISO 8601이며 종료 예정 시각은 시작 시각보다 뒤여야 합니다.

```json
{
  "maintenanceId": 1,
  "message": "장소 데이터 동기화를 위한 서비스 점검이 예정되어 있습니다. 점검 시간 동안 서비스 이용이 제한됩니다.",
  "startsAt": "2026-11-18T02:00:00",
  "endsAt": "2026-11-18T03:00:00",
  "status": "SCHEDULED"
}
```

`MaintenanceClient.schedule()`이 요청과 응답 검증을 담당하고, `schedule_maintenance` Tool이 결과를 반환합니다.
Spring 에이전트 필터는 장소 GET과 점검 예약 POST를 각각 전용 권한으로 허용합니다.

점검 예약 Tool·클라이언트 및 CLI 관련 테스트 45개가 통과했습니다. 기본 문구·사용자 문구·입력 길이·인증·API 미구현·타임아웃·응답 검증과 실제 LangChain 그래프의 성공/오류 전달을 HTTP 대역으로 확인했습니다. 실제 Spring 예약 API와 DB 저장은 아직 검증하지 않았습니다.


시작 시각 지정 예시:

```python
schedule_maintenance.invoke({"startsAt": "2026-11-17T15:27:00"})
```

CLI에서 “오늘 오후 3시 27분에 점검 예약해 줘”라고 요청하면 LLM이 현재 KST 날짜의 15:27:00을 startsAt으로 전달합니다. 시간을 언급하지 않으면 startsAt을 생략합니다.
동적 시스템 프롬프트는 [LangChain dynamic_prompt](https://docs.langchain.com/oss/python/langchain/middleware/custom)를 사용합니다.


선택 시작 시각 변경 후 Tool·CLI 테스트 54개가 통과했습니다. 실제 Gemini 호출에서도 KST 기준을 고정하여 지정 시각 전송·시각 생략을 확인했습니다. 해당 확인은 Spring 클라이언트를 대체했으므로 실제 예약을 생성하지 않았습니다.
적용 시 Spring 서버를 재빌드하고 실행 중인 `jamkkanjeju` CLI도 종료 후 다시 시작해야 합니다.


## 2026-11-17 누락 오판 원인과 수정

기존 `areaCode=39` 요청은 제주 전체 275건·6개 분류 217건만 반환했으며, 상세 조회에 실제 존재하는 장모식탁도 빠졌습니다.
장모식탁 상세 응답의 areacode는 빈 값, lDongRegnCd는 50, lclsSystm1은 FD였습니다.
[공식 TourAPI 명세](https://www.data.go.kr/data/15101578/openapi.do)는 areaCode를 미사용·삭제 예정 항목으로 안내하며 법정동 코드를 제공합니다.
현행 법정동 코드로 수집한 결과는 제주 전체 2,110건, 기존 6개 분류 대상 1,527건이었습니다. 이는 해당 실행 시점의 건수입니다.

- 장모식탁을 포함해 DB 20곳 중 19곳은 수집 목록에 포함됐습니다.
- 나머지 라토커피제주(2853982)는 개별 상세 API도 정상 0건이었습니다. 이는 조회 시점의 API 미노출 확인이며 폐업 사실을 단정하지 않습니다.
- `get_tourapi_location({"contentId":2861967})`는 지역·분류 필터 없이 개별 존재를 직접 확인합니다.
- 에이전트는 결과에 이의가 제기되면 이 Tool로 재확인하며, 확인 없이 정상 조회·일시적 동기화 오류라고 단정하지 않도록 지침을 수정했습니다.
- 실제 Gemini도 해당 이의 제기에서 get_tourapi_location을 호출하고 exists=true를 확인한 뒤 답변하는 것을 검증했습니다.


## 스냅샷 기반 Tool 분리 검증

원본 목록은 `memory/store.py`의 프로세스 메모리에만 보관하며 입력·출력 사본을 분리해 외부 수정이 비교 원본을 바꾸지 않게 합니다.
누락·오류가 있는 수집에서는 새 스냅샷을 발급하지 않습니다. 비교 Tool에는 ID만 전달하며 임의 장소 배열을 입력할 수 없습니다.
기존 create_get_stored_locations 팩토리·단일 페이지 Tool과 비교 Tool 내부의 전체 수집 코드는 제거했습니다.

이번 검증에서는 실제 LLM·Spring·TourAPI를 호출하지 않습니다. 테스트의 외부 HTTP 전송도 차단하고 대역만 사용합니다.
실제 모델의 도구 선택은 API 한도 때문에 실험하지 않았으며, ScriptedModel과 실제 LangChain 그래프로 세 Tool의 순서·ID 전달·오류 중단을 검증합니다.

스냅샷 분리 후 관련 테스트 126개 통과. 실제 외부 HTTP 전송을 차단한 상태에서 검증했으며 유료·무료 모델 API 토큰을 사용하지 않았습니다. 변경 적용에는 실행 중인 CLI를 종료한 뒤 다시 시작해야 합니다.

## 승인 후 장소 동기화

`장소를 비교해서 동기화해 줘`처럼 실행을 요청하면 DB·TourAPI 조회와 비교를 거쳐 `synchronize_locations`를 사용할 수 있다. 조회 → 비교에서 고정된 planId 생성 → 동기화 Tool이 해당 결과 보고와 단일 (y/n) 확인을 수행한다. 동기화 Tool의 재비교나 LLM의 별도 실행 허가 질문은 없다. y이면 Spring API를 호출하고 n이면 취소한다.

Spring 동기화 API 코드도 구현했으며 서버 재빌드 시 V6 마이그레이션을 적용해야 한다. 호출 예정 계약·인증·트랜잭션 및 멱등성 요구사항은 [LOCATION_SYNC_API.md](docs/LOCATION_SYNC_API.md)에 정리했다. `.env`의 `LOCATION_SYNC_API_URL`과 기존 `AGENT_API_TOKEN`을 사용한다. 이 프로젝트에서 SQL이나 실제 DB 변경을 실행하지 않는다.


## 코드 구조와 클라이언트 공유

```text
src/jamkkanjeju_agent/
├── __main__.py                 # LLM 없는 DB 조회 진단 진입점
├── agent.py                    # 에이전트 구성·프롬프트
├── chat.py                     # jamkkanjeju CLI·사용자 승인
├── tools/
│   ├── location_tools.py       # 장소 조회·비교·동기화 Tool
│   ├── maintenance_tools.py    # 기존 점검 예약 Tool
│   ├── mission_tools.py        # 추후 미션 Tool 추가 위치
│   └── review_tools.py         # 추후 후기 Tool 추가 위치
├── clients/
│   ├── __init__.py             # 공유 인스턴스와 연결 수명 관리
│   ├── spring_client.py        # DB 조회·점검 예약·동기화 API
│   ├── tourapi_client.py       # TourAPI 전체·상세 조회
│   └── gemini.py               # 기존 LLM 객체 생성
├── services/
│   ├── location_comparison.py  # 집합 비교·삭제 후보 상세 확인
│   └── sync_approval.py        # 실제 CLI 승인 전달
└── memory/
    └── store.py               # 스냅샷·동기화 계획
```

장소 조회·점검 예약·동기화 Tool은 모두 동일한 `SpringClient`를 공유합니다.
TourAPI Tool과 비교 서비스의 상세 재확인도 동일한 `TourApiClient`를 사용합니다.
서비스별 클라이언트는 필요할 때 생성하므로 DB 조회만 할 때 TourAPI 설정은 필요하지 않습니다.
기존 환경변수 이름과 HTTP 계약은 유지합니다.

Python에서 Tool을 직접 호출할 때도 연결 수명을 명시하려면 다음처럼 사용합니다.

```python
from jamkkanjeju_agent.clients import client_scope
from jamkkanjeju_agent.tools import get_stored_locations

with client_scope():
    result = get_stored_locations.invoke({})
```

스냅샷·계획은 실행 중 메모리에만 남습니다. 이번 구조 변경으로 DB·Redis 영속 저장이 추가되지는 않았습니다.

## 한국어 감성 분류 모델 단독 실행

`services/sentiment_classifier.py`의 `KoreanSentimentClassifier`는 공개 모델 `daekeun-ml/koelectra-small-v3-nsmc`를 로컬 CPU에서 실행한다. 아직 LangChain Tool·Spring 방명록 API·장소 집계에는 연결하지 않았다.

감성 분석을 사용할 때만 선택 의존성을 설치한다. CPU 환경에서는 PyTorch 공식 CPU 패키지를 먼저 설치할 수 있다.

```powershell
.\.venv\Scripts\python.exe -m pip install "torch>=2.6,<3" --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -e ".[sentiment]"
.\.venv\Scripts\python.exe -X utf8 tests/manual_sentiment_model.py
```

- 최초 분석 시 모델 파일 약 56.5MB와 토크나이저를 Hugging Face에서 내려받는다. 이후 로컬 캐시와 같은 객체의 모델을 재사용한다. 일반 에이전트 CLI 실행만으로는 모델을 불러오지 않는다.
- 공개 모델이므로 Gemini·OpenAI API 키는 필요하지 않다. 텍스트 추론은 로컬에서 실행하며 문장을 외부 LLM에 전송하지 않는다.
- 모델 리비전은 `d1c0233fc013ae8bd879d7b61f2bec69aa3af4c9`로 고정했다. 모델 카드의 클래스 순서에 따라 0=NEGATIVE, 1=POSITIVE로 변환한다. `config.id2label`은 숫자만 있으므로 문자열을 추측해서 해석하지 않는다.
- `classify(["좋았어요", None, " "])`는 입력 순서대로 결과를 반환한다. 텍스트 없음·공백만 UNKNOWN/score=None이며, 텍스트가 있으면 점수 임계값 없이 POSITIVE 또는 NEGATIVE로 분류한다. score는 선택한 클래스의 softmax 점수이며 실제 정확도를 보장하는 수치가 아니다.
- 기본 배치는 16개이며 한 글은 최대 512토큰까지 사용한다. 더 긴 글은 잘리므로 추후 실제 방명록 Tool에서는 긴 글 처리 방식을 별도 결정해야 한다.
- 실제 5문장 테스트는 수동 실행 파일이라 기본 pytest에 포함되지 않는다. 일반 단위 테스트는 `python -m pytest tests/test_sentiment_classifier.py -q`이며 모델 다운로드 없이 동작한다.

## 장소 방명록 분석
`analyze_location_guestbooks`는 ACTIVE 장소 방명록을 로컬 감성 모델로 분류합니다. sentiment 선택 의존성을 설치해야 합니다. 모든 장소에 대해 positive/negative/unknown ID 배열, 각각 건수, totalCount와 negativeRatio(미확인 포함 전체 대비 부정 비율, 0~1)를 반환합니다. 방명록 없는 장소는 비율 null입니다. 원문과 모델 점수는 보관하지 않습니다. 메모리 스냅샷은 /clear 또는 CLI 종료 시 삭제합니다. 원문 조회·보고서 Tool은 아직 구현하지 않았습니다.
