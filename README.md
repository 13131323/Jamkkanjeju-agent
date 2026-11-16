# Jamkkanjeju-agent
잠깐제주 서비스 운영 업무 수행 특화 AI 에이전트

## 현재 구현 범위

첫 번째 LangChain Tool인 `get_stored_locations`만 구현했습니다. LLM 호출·에이전트 실행·TourAPI 비교·DB 변경은 포함하지 않습니다.

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
