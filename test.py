#!/usr/bin/env python3
"""
GenOS Chat with Socket.io

GenOS API를 사용하여 로그인, 채팅 정보 조회, Socket.IO 실시간 채팅을 제공하는 클라이언트입니다.

주요 기능:
- JWT 토큰 기반 인증
- 실시간 토큰 스트리밍
- agentFlowExecutedData 실시간 수신
- 콜백 인터페이스를 통한 실시간 이벤트 처리

사용 방법:
1. GenosChatClient 인스턴스 생성
2. login() 메서드로 로그인
3. get_chat_info() 메서드로 채팅 정보 조회  
4. chat_with_socketio() 또는 chat_with_socketio_callback()으로 채팅 시작
"""

import asyncio
import json
import logging
import ssl
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from urllib.parse import urljoin

import aiohttp
import socketio

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class ChatInfo:
    """채팅 정보 클래스"""
    chat_id: int
    workflow_id: int


@dataclass
class ChatResponse:
    """채팅 응답 클래스"""
    tokens: List[str] = field(default_factory=list)
    source_documents: List[str] = field(default_factory=list)
    agent_flow_data: List[Dict[str, Any]] = field(default_factory=list)
    node_execution_data: List[Dict[str, Any]] = field(default_factory=list)
    workflow_data: List[Dict[str, Any]] = field(default_factory=list)
    final_response: Optional[Dict[str, Any]] = None
    complete_response: Optional[Dict[str, Any]] = None
    completed: bool = False
    
    def add_token(self, token: str) -> None:
        """토큰 추가"""
        self.tokens.append(token)
    
    def add_source_document(self, doc: str) -> None:
        """참조 문서 추가"""
        self.source_documents.append(doc)
    
    def add_agent_flow_data(self, data: Dict[str, Any]) -> None:
        """에이전트 플로우 데이터 추가"""
        self.agent_flow_data.append(data)
    
    def add_node_execution_data(self, data: Dict[str, Any]) -> None:
        """노드 실행 데이터 추가"""
        self.node_execution_data.append(data)
    
    def add_workflow_data(self, data: Dict[str, Any]) -> None:
        """워크플로우 데이터 추가"""
        self.workflow_data.append(data)


class RealTimeCallback(ABC):
    """실시간 콜백 인터페이스"""
    
    @abstractmethod
    def on_token(self, token: str) -> None:
        """토큰 스트리밍 - 실시간으로 응답 텍스트를 받음"""
        pass
    
    @abstractmethod
    def on_agent_flow_data(self, agent_flow_data: Dict[str, Any]) -> None:
        """에이전트 플로우 데이터 - 워크플로우 실행 정보를 실시간으로 받음"""
        pass
    
    @abstractmethod
    def on_node_execution(self, node_data: Dict[str, Any]) -> None:
        """노드 실행 데이터 - 개별 노드 실행 정보를 받음"""
        pass
    
    @abstractmethod
    def on_workflow_update(self, workflow_data: Dict[str, Any]) -> None:
        """워크플로우 상태 업데이트 - 메타데이터 및 상태 정보를 받음"""
        pass
    
    @abstractmethod
    def on_source_documents(self, source_doc: str) -> None:
        """참조 문서 - 벡터 스토어 소스 문서를 받음"""
        pass
    
    @abstractmethod
    def on_complete(self) -> None:
        """완료 이벤트 - 모든 처리가 완료되었을 때 호출"""
        pass


class GenosChatClient:
    """GenOS Chat Client"""
    
    def __init__(self, genos_url: str, user_id: str, password: str):
        """
        클라이언트 초기화
        
        Args:
            genos_url: GenOS 서버 URL
            user_id: 사용자 ID
            password: 비밀번호
        """
        self.genos_url = genos_url.rstrip('/')
        self.user_id = user_id
        self.password = password
        self.access_token: Optional[str] = None
        self.chat_info: Optional[ChatInfo] = None
        
        # SSL 검증 비활성화 (개발 환경용)
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        
        # HTTP 클라이언트 설정
        self.timeout = aiohttp.ClientTimeout(total=60, connect=30)
        self.connector = aiohttp.TCPConnector(
            ssl=self.ssl_context,
            limit=100,
            limit_per_host=30
        )
    
    async def login(self) -> bool:
        """
        GenOS에 로그인하여 액세스 토큰 획득
        
        Returns:
            로그인 성공 여부
        """
        logger.info("🔐 로그인 중...")
        
        try:
            url = f"{self.genos_url}/app/api/chat/auth/login"
            
            data = {
                "user_id": self.user_id,
                "password": self.password
            }
            
            # 새로운 커넥터 생성
            connector = aiohttp.TCPConnector(
                ssl=self.ssl_context,
                limit=100,
                limit_per_host=30
            )
            
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=self.timeout
            ) as session:
                async with session.post(url, json=data) as response:
                    if response.status == 200:
                        response_json = await response.json()
                        
                        if response_json.get("code") == 0:
                            self.access_token = response_json["data"]["access_token"]
                            logger.info("✅ 로그인 성공!")
                            logger.info("Access Token: %s...", 
                                      self.access_token[:50] if len(self.access_token) > 50 else self.access_token)
                            return True
                        else:
                            logger.error("❌ 로그인 실패: %s", response_json.get("errMsg"))
                            return False
                    else:
                        logger.error("❌ HTTP 오류: %d", response.status)
                        return False
            
            # 커넥터 정리
            await connector.close()
                        
        except Exception as e:
            logger.error("❌ 로그인 오류: %s", str(e))
            return False
    
    async def get_chat_info(self, chat_endpoint: str) -> bool:
        """
        채팅 정보를 조회하여 chat_id와 workflow_id 획득
        
        Args:
            chat_endpoint: 채팅 엔드포인트
            
        Returns:
            조회 성공 여부
        """
        logger.info("📋 채팅 정보 조회 중...")
        
        if not self.access_token:
            logger.error("❌ 로그인이 필요합니다.")
            return False
        
        try:
            url = f"{self.genos_url}/app/api/chat/chat/info"
            params = {"chat_endpoint": chat_endpoint}
            headers = {"Authorization": f"Bearer {self.access_token}"}
            
            # 새로운 커넥터 생성 (세션 재사용 문제 해결)
            connector = aiohttp.TCPConnector(
                ssl=self.ssl_context,
                limit=100,
                limit_per_host=30
            )
            
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=self.timeout
            ) as session:
                async with session.get(url, params=params, headers=headers) as response:
                    if response.status == 200:
                        response_json = await response.json()
                        
                        if response_json.get("code") == 0:
                            data = response_json["data"]
                            self.chat_info = ChatInfo(
                                chat_id=data["chat_id"],
                                workflow_id=data["workflow_id"]
                            )
                            logger.info("✅ 채팅 정보 조회 성공!")
                            logger.info("   - Chat ID: %d", self.chat_info.chat_id)
                            logger.info("   - Workflow ID: %d", self.chat_info.workflow_id)
                            return True
                        else:
                            logger.error("❌ 채팅 정보 조회 실패: %s", response_json.get("errMsg"))
                            return False
                    else:
                        logger.error("❌ HTTP 오류: %d", response.status)
                        return False
            
            # 커넥터 정리
            await connector.close()
                        
        except Exception as e:
            logger.error("❌ 채팅 정보 조회 오류: %s", str(e))
            return False
    
    def _setup_basic_socket_event_handlers(
        self, 
        sio: socketio.AsyncClient, 
        chat_response: ChatResponse,
        completion_event: asyncio.Event
    ) -> None:
        """Socket.IO 이벤트 핸들러 설정 (기본 버전)"""
        
        @sio.event
        async def connect():
            logger.info("🔗 Socket.IO 연결됨")
        
        @sio.event
        async def disconnect():
            logger.info("🔌 Socket.IO 연결 해제됨")
        
        @sio.event
        async def connect_error(data):
            logger.error("❌ Socket.IO 연결 오류: %s", data)
        
        @sio.on('error')
        async def error_handler(data):
            logger.error("❌ Socket.IO 일반 오류: %s", data)
        
        @sio.on('start')
        async def start_handler(data):
            logger.info("🚀 응답 시작")
        
        @sio.on('token')
        async def token_handler(data):
            if data:
                token = str(data)
                print(token, end='', flush=True)
                chat_response.add_token(token)
        
        @sio.on('agentFlowExecutedData')
        async def agent_flow_handler(data):
            if data:
                try:
                    if isinstance(data, str):
                        agent_flow_data = json.loads(data)
                    else:
                        agent_flow_data = data
                    logger.info("\n🎯 실시간 agentFlowExecutedData 수신")
                    chat_response.add_agent_flow_data(agent_flow_data)
                except Exception as e:
                    logger.error("agentFlowExecutedData 파싱 오류: %s", str(e))
        
        @sio.on('metadata')
        async def metadata_handler(data):
            if data:
                try:
                    if isinstance(data, str):
                        metadata = json.loads(data)
                    else:
                        metadata = data
                    logger.info("\n📋 메타데이터 수신")
                    
                    if "agentFlowExecutedData" in metadata:
                        agent_flow_data = metadata["agentFlowExecutedData"]
                        chat_response.add_agent_flow_data(agent_flow_data)
                    
                    chat_response.add_workflow_data(metadata)
                except Exception as e:
                    logger.error("메타데이터 파싱 오류: %s", str(e))
        
        @sio.on('sourceDocuments')
        async def source_docs_handler(data):
            if data:
                logger.info("\n📚 참조 문서 수신")
                chat_response.add_source_document(str(data))
        
        @sio.on('end')
        async def end_handler(data):
            logger.info("\n✅ 응답 완료")
            chat_response.completed = True
            completion_event.set()
    
    def _setup_callback_socket_event_handlers(
        self, 
        sio: socketio.AsyncClient, 
        chat_response: ChatResponse,
        callback: RealTimeCallback,
        completion_event: asyncio.Event
    ) -> None:
        """Socket.IO 이벤트 핸들러 설정 (콜백 버전)"""
        
        @sio.event
        async def connect():
            logger.info("🔗 Socket.IO 연결됨")
        
        @sio.event
        async def disconnect():
            logger.info("🔌 Socket.IO 연결 해제됨")
        
        @sio.event
        async def connect_error(data):
            logger.error("❌ Socket.IO 연결 오류: %s", data)
        
        @sio.on('error')
        async def error_handler(data):
            logger.error("❌ Socket.IO 일반 오류: %s", data)
        
        @sio.on('start')
        async def start_handler(data):
            logger.info("\n🚀 스트리밍 시작")
        
        @sio.on('token')
        async def token_handler(data):
            if data:
                token = str(data)
                chat_response.add_token(token)
                callback.on_token(token)
        
        @sio.on('agentFlowExecutedData')
        async def agent_flow_handler(data):
            if data:
                try:
                    if isinstance(data, str):
                        agent_flow_data = json.loads(data)
                    else:
                        agent_flow_data = data
                    chat_response.add_agent_flow_data(agent_flow_data)
                    callback.on_agent_flow_data(agent_flow_data)
                except Exception as e:
                    logger.error("agentFlowExecutedData 파싱 오류: %s", str(e))
        
        @sio.on('metadata')
        async def metadata_handler(data):
            if data:
                try:
                    if isinstance(data, str):
                        metadata = json.loads(data)
                    else:
                        metadata = data
                    
                    if "agentFlowExecutedData" in metadata:
                        agent_flow_data = metadata["agentFlowExecutedData"]
                        chat_response.add_agent_flow_data(agent_flow_data)
                        callback.on_agent_flow_data(agent_flow_data)
                    
                    chat_response.add_workflow_data(metadata)
                    callback.on_workflow_update(metadata)
                except Exception as e:
                    logger.error("메타데이터 파싱 오류: %s", str(e))
        
        @sio.on('usedTools')
        async def used_tools_handler(data):
            if data:
                try:
                    if isinstance(data, str):
                        tools_data = json.loads(data)
                    else:
                        tools_data = data
                    chat_response.add_node_execution_data(tools_data)
                    callback.on_node_execution(tools_data)
                except Exception as e:
                    logger.error("도구 데이터 파싱 오류: %s", str(e))
        
        @sio.on('sourceDocuments')
        async def source_docs_handler(data):
            if data:
                source_doc = str(data)
                chat_response.add_source_document(source_doc)
                callback.on_source_documents(source_doc)
        
        @sio.on('end')
        async def end_handler(data):
            logger.info("\n✅ 응답 완료")
            chat_response.completed = True
            callback.on_complete()
            completion_event.set()
    
    async def chat_with_socketio(self, question: str, chat_endpoint: str) -> Optional[ChatResponse]:
        """
        Socket.IO를 사용한 실시간 채팅 (기본 버전)
        
        Args:
            question: 질문 내용
            chat_endpoint: 채팅 엔드포인트
            
        Returns:
            채팅 응답 객체
        """
        logger.info("💬 질문: %s", question)
        logger.info("🔄 Socket.IO 연결 중...")
        
        if not self.access_token or not self.chat_info:
            logger.error("❌ 로그인 및 채팅 정보 조회가 필요합니다.")
            return None
        
        chat_response = ChatResponse()
        completion_event = asyncio.Event()
        
        try:
            # Socket.IO 클라이언트 설정
            sio = socketio.AsyncClient(
                ssl_verify=False,
                logger=False,
                engineio_logger=False
            )
            
            # 이벤트 핸들러 설정
            self._setup_basic_socket_event_handlers(sio, chat_response, completion_event)
            
            # Socket.IO 연결
            socket_url = self.genos_url
            socket_path = f"/workflow/{self.chat_info.workflow_id}/socket.io"
            
            logger.info("Socket.IO 연결 URL: %s", socket_url)
            logger.info("Socket.IO 경로: %s", socket_path)
            
            await sio.connect(
                socket_url,
                socketio_path=socket_path,
                transports=['websocket', 'polling']
            )
            
            logger.info("✅ Socket.IO 연결 성공!")
            
            # 세션 ID 및 Socket ID 생성
            session_id = str(uuid.uuid4())
            trace_id = str(uuid.uuid4())
            sid = sio.get_sid()
            
            if not sid:
                logger.error("❌ Socket ID를 가져올 수 없습니다")
                await sio.disconnect()
                return None
            
            logger.info("Socket ID: %s", sid)
            
            # HTTP POST 요청으로 채팅 시작
            url = f"{self.genos_url}/app/api/chat/chat/v2/query/{chat_endpoint}"
            
            data = {
                "question": question,
                "chatId": session_id,
                "socketIOClientId": sid
            }
            
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "x-genos-session-id": session_id,
                "x-genos-trace-id": trace_id,
                "x-genos-workflow-sid": sid,
                "Content-Type": "application/json"
            }
            
            # 새로운 커넥터 생성 (세션 재사용 문제 해결)
            http_connector = aiohttp.TCPConnector(
                ssl=self.ssl_context,
                limit=100,
                limit_per_host=30
            )
            
            async with aiohttp.ClientSession(
                connector=http_connector,
                timeout=self.timeout
            ) as session:
                async with session.post(url, json=data, headers=headers) as response:
                    if response.status == 200:
                        response_json = await response.json()
                        chat_response.final_response = response_json
                    else:
                        logger.error("❌ HTTP 응답 오류: %d", response.status)
                        await sio.disconnect()
                        return None
            
            # HTTP 커넥터 정리
            await http_connector.close()
            
            # 응답 완료 대기 (최대 60초)
            try:
                await asyncio.wait_for(completion_event.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                logger.warning("⏰ 타임아웃: 60초 대기 후 연결 종료")
            
            await sio.disconnect()
            return chat_response
            
        except Exception as e:
            logger.error("❌ Socket.IO 채팅 오류: %s", str(e))
            try:
                await sio.disconnect()
            except:
                pass
            return None
    
    async def chat_with_socketio_callback(
        self, 
        question: str, 
        chat_endpoint: str, 
        callback: RealTimeCallback
    ) -> Optional[ChatResponse]:
        """
        Socket.IO를 사용한 실시간 채팅 (콜백 버전)
        실시간으로 토큰 스트리밍과 agentFlowExecutedData를 수신할 수 있습니다.
        
        Args:
            question: 질문 내용
            chat_endpoint: 채팅 엔드포인트
            callback: 실시간 이벤트를 처리할 콜백 인터페이스
            
        Returns:
            채팅 응답 객체
        """
        logger.info("💬 질문: %s", question)
        logger.info("🔄 Socket.IO 연결 중... (콜백 모드)")
        
        if not self.access_token or not self.chat_info:
            logger.error("❌ 로그인 및 채팅 정보 조회가 필요합니다.")
            return None
        
        chat_response = ChatResponse()
        completion_event = asyncio.Event()
        
        try:
            # Socket.IO 클라이언트 설정
            sio = socketio.AsyncClient(
                ssl_verify=False,
                logger=False,
                engineio_logger=False
            )
            
            # 이벤트 핸들러 설정
            self._setup_callback_socket_event_handlers(sio, chat_response, callback, completion_event)
            
            # Socket.IO 연결
            socket_url = self.genos_url
            socket_path = f"/workflow/{self.chat_info.workflow_id}/socket.io"
            
            await sio.connect(
                socket_url,
                socketio_path=socket_path,
                transports=['websocket', 'polling']
            )
            
            # 세션 ID 및 Socket ID 생성
            session_id = str(uuid.uuid4())
            trace_id = str(uuid.uuid4())
            sid = sio.get_sid()
            
            if not sid:
                logger.error("❌ Socket ID를 가져올 수 없습니다")
                await sio.disconnect()
                return None
            
            # HTTP POST 요청으로 채팅 시작
            url = f"{self.genos_url}/app/api/chat/chat/v2/query/{chat_endpoint}"
            
            data = {
                "question": question,
                "chatId": session_id,
                "socketIOClientId": sid
            }
            
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "x-genos-session-id": session_id,
                "x-genos-trace-id": trace_id,
                "x-genos-workflow-sid": sid,
                "Content-Type": "application/json"
            }
            
            # 새로운 커넥터 생성 (세션 재사용 문제 해결)
            http_connector = aiohttp.TCPConnector(
                ssl=self.ssl_context,
                limit=100,
                limit_per_host=30
            )
            
            async with aiohttp.ClientSession(
                connector=http_connector,
                timeout=self.timeout
            ) as session:
                async with session.post(url, json=data, headers=headers) as response:
                    if response.status == 200:
                        response_json = await response.json()
                        chat_response.final_response = response_json
                    else:
                        logger.error("❌ HTTP 응답 오류: %d", response.status)
                        await sio.disconnect()
                        return None
            
            # HTTP 커넥터 정리
            await http_connector.close()
            
            # 응답 완료 대기 (최대 60초)
            try:
                await asyncio.wait_for(completion_event.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                logger.warning("⏰ 타임아웃: 60초 대기 후 연결 종료")
            
            await sio.disconnect()
            return chat_response
            
        except Exception as e:
            logger.error("❌ Socket.IO 채팅 오류: %s", str(e))
            try:
                await sio.disconnect()
            except:
                pass
            return None
    
    async def close(self) -> None:
        """리소스 정리"""
        if hasattr(self, 'connector'):
            await self.connector.close()


class TestRealTimeCallback(RealTimeCallback):
    """테스트용 실시간 콜백 구현"""
    
    def on_token(self, token: str) -> None:
        print(token, end='', flush=True)
    
    def on_agent_flow_data(self, agent_flow_data: Dict[str, Any]) -> None:
        logger.info("\n🎯 [실시간] AgentFlow 데이터 수신!")
    
    def on_node_execution(self, node_data: Dict[str, Any]) -> None:
        logger.info("\n⚙️ [실시간] 노드 실행 데이터 수신!")
    
    def on_workflow_update(self, workflow_data: Dict[str, Any]) -> None:
        logger.info("\n🔄 [실시간] 워크플로우 업데이트!")
    
    def on_source_documents(self, source_doc: str) -> None:
        logger.info("\n📚 [실시간] 참조 문서 수신!")
    
    def on_complete(self) -> None:
        logger.info("\n✅ [실시간] 모든 처리 완료!")


async def main():
    """메인 실행 함수"""
    # GenOS 클라이언트 초기화
    client = GenosChatClient(
        genos_url="https://genos.mnc.ai:3443/",
        user_id="seeheonlee-admin",
        password="Thdjfla7185!"
    )
    
    try:
        # 1. 로그인
        if not await client.login():
            return
        
        # 2. 채팅 정보 조회
        chat_endpoint = "lepi_wn4z_4rm4"
        if not await client.get_chat_info(chat_endpoint):
            return
        
        # 3. Socket.IO를 사용한 실시간 채팅
        logger.info("\n" + "=" * 50)
        logger.info("Socket.IO 채팅 테스트")
        logger.info("=" * 50)
        
        response = await client.chat_with_socketio(
            "안녕하세요! Python에서 연결했습니다!", 
            chat_endpoint
        )
        
        if response:
            logger.info("\n📊 최종 응답 토큰 수: %d", len(response.tokens))
            logger.info("📚 참조 문서 수: %d", len(response.source_documents))
            logger.info("🔄 실시간 AgentFlow 데이터 수: %d", len(response.agent_flow_data))
            logger.info("🎯 실시간 노드 실행 데이터 수: %d", len(response.node_execution_data))
            logger.info("⚙️ 실시간 워크플로우 데이터 수: %d", len(response.workflow_data))
            
            # HTTP 응답 확인
            if response.final_response:
                logger.info("\n🎯 HTTP 응답에서 완전한 데이터 확인:")
                try:
                    http_response = response.final_response
                    logger.info("HTTP 응답 전체: %s", json.dumps(http_response, indent=2, ensure_ascii=False))
                    
                    if "data" in http_response:
                        data = http_response["data"]
                        if "agentFlowExecutedData" in data:
                            logger.info("📋 Agent Flow 데이터 발견!")
                            logger.info("Agent Flow 데이터: %s", data["agentFlowExecutedData"])
                        if "chatId" in data:
                            logger.info("📋 Chat ID: %s", data["chatId"])
                        if "sessionId" in data:
                            logger.info("📋 Session ID: %s", data["sessionId"])
                        if "text" in data:
                            logger.info("📋 최종 텍스트: %s", data["text"])
                except Exception as e:
                    logger.error("HTTP 응답 데이터 파싱 오류: %s", str(e))
        
        # 실시간 콜백 테스트
        logger.info("\n" + "=" * 50)
        logger.info("실시간 콜백 테스트")
        logger.info("=" * 50)
        
        callback = TestRealTimeCallback()
        callback_response = await client.chat_with_socketio_callback(
            "실시간 콜백 테스트입니다!", 
            chat_endpoint, 
            callback
        )
        
        if callback_response:
            logger.info("\n📊 실시간 콜백 테스트 결과:")
            logger.info("   - 토큰: %d개", len(callback_response.tokens))
            logger.info("   - AgentFlow 데이터: %d개", len(callback_response.agent_flow_data))
            logger.info("   - 노드 실행 데이터: %d개", len(callback_response.node_execution_data))
            logger.info("   - 워크플로우 데이터: %d개", len(callback_response.workflow_data))
    
    finally:
        await client.close()


if __name__ == "__main__":
    # Python 3.7+ 환경에서 실행
    asyncio.run(main())
