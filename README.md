# Jamkkanjeju-agent
잠깐제주 서비스 운영 업무 수행 특화 AI 에이전트

## 현재 구현 범위

자연어로 대화하는 `jamkkanjeju` CLI와 LangChain 에이전트를 구현했습니다. Gemini가 필요한 때 `get_stored_locations` Tool을 선택해 Spring의 저장 장소를 조회합니다. TourAPI 비교·DB 변경 기능은 아직 포함하지 않습니다.

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
나 > 저장된 장소 5개 보여줘
[도구 호출] get_stored_locations {"page": 0, "size": 5}
[도구 완료] get_stored_locations
jamkkanjeju > 조회 결과에 근거한 답변
나 > 다음 페이지도 보여줘
```

- 자연어 입력 → Gemini 판단 → 필요한 Tool 호출 → 결과 확인 → 답변 순서로 동작합니다. 도구 사용 여부는 LLM이 결정합니다.
- `agent.py`가 `create_agent`로 모델과 도구·시스템 지침을 연결하고, `chat.py`가 입력·출력과 대화 상태를 관리합니다.
- `/help`: 도움말, `/clear`: 대화 초기화, `/exit`·`exit`·`quit`: 종료. 입력 대기 중 Ctrl+C나 EOF도 종료합니다. 작업 중 Ctrl+C는 해당 요청을 중단합니다.
- 최근 완료한 10개 대화 턴을 현재 프로세스에서만 유지합니다. 프로그램을 종료하면 대화는 사라집니다. API 오류로 중단된 불완전한 대화는 후속 요청에 전달하지 않습니다.
- 처리 단계를 20개로 제한합니다. 실제 추론 내용·비밀 설정은 출력하지 않고 Tool 호출·완료·오류와 답변을 표시합니다.
- 일반 채팅은 Gemini 연결만 필요합니다. 장소 조회는 Docker와 Spring 서버가 실행 중이어야 합니다. 서버가 꺼져 있으면 실패를 안내하고 대화는 계속할 수 있습니다.
- `jamkkanjeju --help`, `jamkkanjeju --version`으로 실행 옵션을 확인합니다.
- 기존 `python -m jamkkanjeju_agent --page 0 --size 100`은 LLM 없는 Tool 단독 실행 명령으로 유지합니다.

## Tool 단독 실행

Spring 서버가 실행 중이어야 합니다. 모델 API 키는 필요하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m jamkkanjeju_agent --page 0 --size 100
```

Python에서 직접 실행하는 방법입니다. `.env`를 사용할 때는 먼저 로드합니다.

```python
from dotenv import load_dotenv
from jamkkanjeju_agent.tools import get_stored_locations

load_dotenv()
result = get_stored_locations.invoke({"page": 0, "size": 100})
print(result)
```

입력은 `page`(0부터, 기본 0)와 `size`(1~100, 기본 100)뿐입니다. URL과 인증 토큰은 모델이 설정하는 Tool 입력에 포함되지 않습니다. 한 번 호출하면 한 페이지만 가져오며, `locationsPage.hasNext`가 true인 경우 다음 페이지를 호출합니다.

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

Spring과 Python이 동일한 응답 계약을 사용합니다. 빈 결과는 `locations: []`로 반환하지만, 인증·권한·통신·응답 형식 오류는 `ToolException`으로 구분합니다. 조회 실패를 장소가 없다는 뜻으로 처리해서는 안 됩니다.

페이지별 조회는 전체 데이터의 고정된 스냅샷을 보장하지 않습니다. 실제 동기화 시점의 일관성 제어는 후속 단계에서 서버와 함께 설계합니다.

## 개별 테스트

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_stored_locations.py -q
```

`httpx.MockTransport`로 HTTP 요청과 응답을 검증합니다. 실제 DB나 LLM은 호출하지 않습니다.

구현 파일: `src/jamkkanjeju_agent/tools/stored_locations.py`.

참고: [LangChain 공식 Tools 문서](https://docs.langchain.com/oss/python/langchain/tools).

검증 결과: Tool 개별 테스트 24개 통과. 로컬 Spring과 실제 연결해 page=0·size=2 조회에서 두 장소와 다음 페이지 정보를 확인했습니다.


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

실제 설치된 `jamkkanjeju` 명령에서도 Gemini의 이름 소개와 `get_stored_locations(page=0, size=2)` 선택을 확인했습니다. 이때 Docker가 실행 중이지 않아 실제 DB 조회는 연결 실패로 안내되었으며 대화가 정상 유지·종료되는 것을 확인했습니다. 정상 DB 조회는 앞선 Tool 연동에서 확인했고, 이번 CLI 실행에서 정상 DB 결과까지 재확인한 것은 아닙니다.

```powershell
python -m pytest tests/test_chat.py tests/test_gemini_client.py tests/test_stored_locations.py -q
```
