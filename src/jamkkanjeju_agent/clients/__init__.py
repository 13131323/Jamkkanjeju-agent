"""에이전트 실행 범위에서 서비스별 클라이언트와 HTTP 연결을 공유한다."""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock

import httpx

from .gemini import create_gemini_client
from .spring_client import SpringClient
from .tourapi_client import TourApiClient, TourApiError


class AgentClients:
    """필요한 서비스만 지연 생성하며 종료 시 연결을 닫는다."""

    def __init__(self):
        self._lock = RLock()
        self._spring = None
        self._tourapi = None
        self._connections = []
        self._closed = False

    def _get(self, attribute, client_type):
        with self._lock:
            if self._closed:
                raise RuntimeError("종료된 에이전트 클라이언트입니다.")
            client = getattr(self, attribute)
            if client is None:
                connection = httpx.Client()
                try:
                    client = client_type(connection)
                except BaseException:
                    connection.close()
                    raise
                self._connections.append(connection)
                setattr(self, attribute, client)
            return client

    @property
    def spring(self) -> SpringClient:
        return self._get("_spring", SpringClient)

    @property
    def tourapi(self) -> TourApiClient:
        return self._get("_tourapi", TourApiClient)

    def close(self):
        with self._lock:
            if not self._closed:
                for connection in self._connections:
                    connection.close()
                self._closed = True


_current_clients: ContextVar[AgentClients | None] = ContextVar("agent_clients", default=None)
_default_clients = None
_default_lock = RLock()


def get_clients() -> AgentClients:
    """CLI 범위를 우선 사용하며 직접 Tool 호출도 동일 인스턴스를 재사용한다."""
    scoped = _current_clients.get()
    if scoped is not None:
        return scoped
    global _default_clients
    with _default_lock:
        if _default_clients is None:
            _default_clients = AgentClients()
        return _default_clients


def close_default_clients():
    """CLI 외부에서 직접 Tool을 호출한 사용자가 연결을 정리할 때 사용한다."""
    global _default_clients
    with _default_lock:
        if _default_clients is not None:
            _default_clients.close()
            _default_clients = None


@contextmanager
def client_scope():
    """중첩 호출은 기존 범위를 공유하고 최상위 실행 종료 때만 닫는다."""
    if _current_clients.get() is not None:
        yield get_clients()
        return
    clients = AgentClients()
    token = _current_clients.set(clients)
    try:
        yield clients
    finally:
        _current_clients.reset(token)
        clients.close()
