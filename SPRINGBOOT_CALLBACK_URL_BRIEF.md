# 브리핑: AI 분석 콜백이 `localhost:8080`으로 나가서 연결 거부됨

## 이 문서에 대해

이 문서는 `Contextory_AI` 레포(FastAPI, AI/RAG 워커) 쪽에서 관측한 증상을 정리한 것으로,
`Contextory_BackEnd`(Spring Boot) 코드는 직접 보지 못한 상태에서 작성한 **가설 기반 브리핑**입니다.
그대로 구현하지 말고, 실제 코드를 확인해 원인을 검증한 뒤 진행해 주세요.

## 증상

FastAPI(`Contextory_AI`) 서버 로그에 아래와 같은 패턴이 반복 관측됩니다:

```
INFO:     ... - "POST /internal/v1/analyses HTTP/1.1" 202 Accepted
...
[Analysis Callback Unreachable] job_id=rag-job-xxxxx analysis_id=2
callback_url=http://localhost:8080/internal/v1/analyses/2/callback
...
httpx.ConnectError: [WinError 10061] 대상 컴퓨터에서 연결을 거부했으므로 연결하지 못했습니다
```

- FastAPI는 요청을 정상적으로 접수(202)하고, 분석도 정상 완료됩니다(FastAPI 쪽 `GET /internal/v1/analyses/{jobId}`로 상태 조회 시 `COMPLETED` 확인됨, DB(`ai_analysis_jobs`)에도 정상 적재됨).
- 실패하는 지점은 **FastAPI → Spring Boot 콜백 전송** 단 하나입니다. FastAPI가 `POST {callbackUrl}`로 결과를 통지하려는데, 그 `callbackUrl` 값 자체가 `http://localhost:8080/internal/v1/analyses/2/callback`으로 와 있어서 연결이 거부됩니다.

## 핵심 포인트: `callbackUrl`은 Spring Boot가 보낸 값

FastAPI의 `POST /internal/v1/analyses` 요청 바디에는 Spring Boot가 채워 보내는 `callbackUrl` 필드가 있고,
FastAPI는 분석이 끝나면 그 URL로 그대로 `httpx.post(callback_url, ...)`를 호출할 뿐입니다(URL을 스스로 만들지 않음).

그런데 이번 요청에서 그 값이 `http://localhost:8080/...`였습니다. 확인된 배포 환경은:

- Spring Boot 배포 주소: `https://api.contextory.kro.kr`
- FastAPI(AI 서버)는 현재 로컬 개발 머신에서 실행 중(요청을 보낸 이 세션에서는 아마 터널/포워딩을 통해 Spring Boot로부터 호출을 받는 구조로 추정됨)

즉 FastAPI 프로세스 입장에서 `localhost:8080`은 "FastAPI 자기 자신이 떠 있는 머신의 8080 포트"를 뜻할 뿐, Spring Boot가 실제로 떠 있는 곳(`api.contextory.kro.kr`)이 아닙니다. 그래서 매번 연결이 거부됩니다.

## 원인 가설

Spring Boot가 FastAPI에게 보내는 `callbackUrl`을 만들 때, **배포된 프로필에서도 로컬 기본값(`localhost:8080`)을 그대로 쓰고 있을 가능성이 높습니다.** 확인해볼 만한 지점:

1. `callbackUrl`을 생성하는 코드(예: `FastApiClient` 또는 AI 분석 요청을 보내는 서비스 클래스)에서 base URL을 어떻게 얻는지 확인.
   - 하드코딩된 `"http://localhost:8080"` 문자열인지
   - `application.yml`/`application-prod.yml` 등의 프로퍼티(`@Value` 등)로 주입받는지, 그리고 배포용 프로필에 해당 프로퍼티가 실제로 오버라이드되어 있는지
   - `ServletUriComponentsBuilder.fromCurrentRequest()` 같은 방식으로 "현재 요청의 host"를 기준으로 동적으로 만드는 방식이라면, 리버스 프록시/로드밸런서 뒤에 있을 때 `X-Forwarded-Host` 등 forwarded 헤더를 Spring이 신뢰하도록 설정(`server.forward-headers-strategy=framework` 또는 `native`)되어 있는지, 그리고 프록시가 해당 헤더를 실제로 전달하는지
2. 배포 환경(`application-prod.yml` 등)에 콜백 base URL을 명시적으로 지정하는 프로퍼티가 있는지, 없다면 추가가 필요할 수 있음. 예: `app.callback-base-url: https://api.contextory.kro.kr`
3. 로컬 개발/테스트 프로필에서는 `localhost:8080`이 맞는 값일 수 있으므로, "이 요청이 실제로 어느 프로필로 실행됐는지"도 함께 확인 필요 — 배포된 서버가 아니라 로컬에서 띄운 Spring Boot로 테스트한 거라면 애초에 `Contextory_AI`도 로컬에서 같이 떠 있어야 함(그 경우엔 설정 문제가 아니라 "테스트 시점에 로컬 Spring Boot가 8080에서 안 떠 있었다"는 별개 이슈일 수 있음 — 이것도 확인 대상).

## 제안하는 수정 방향

- `callbackUrl`을 만드는 로직이 배포 환경에서는 항상 `https://api.contextory.kro.kr`(또는 실제 콜백 라우트의 공인 주소)을 가리키도록 설정값을 프로필별로 명확히 분리.
- 가능하면 "현재 요청의 host를 추론"하는 방식보다는, 환경변수/설정 프로퍼티로 명시적인 콜백 base URL을 갖는 편이 디버깅하기 쉬움(지금처럼 로그에 실제 잘못된 값이 그대로 찍히므로 원인 파악은 어렵지 않지만, 재발 방지 차원에서).

## 참고: FastAPI 쪽은 이미 이 상황을 정상적으로 처리 중 (변경 불필요)

- 콜백 전송 실패는 FastAPI 쪽에서 best-effort로 처리됩니다: 로그만 남기고 백그라운드 작업이나 서버를 죽이지 않으며, 분석 결과 자체는 이미 `ai_analysis_jobs` 테이블에 최종 상태로 저장되어 `GET /internal/v1/analyses/{jobId}`로 조회 가능합니다.
- 다만 Spring Boot 쪽에 `GET`으로 재조회하는 폴백/스케줄러가 없다면, 콜백이 유실될 때 `AiAnalysis` 행이 `PENDING`에 영원히 남는 문제가 있을 수 있습니다(이건 `Contextory_AI` 레포의 이전 인시던트 브리프에서도 이미 언급된, `Contextory_BackEnd` 쪽 별도 팔로우업 과제입니다). 이번 `callbackUrl` 수정과는 별개 이슈이니 필요하면 따로 다뤄주세요.
