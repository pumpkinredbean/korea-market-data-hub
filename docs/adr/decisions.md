# Decisions Log

Append-only log of explicit hub decisions.  Each entry uses the H-series
(Hub decision) identifier and captures question, decision, rationale, and
impact so later sessions can branch with full context.

---

## H23 — multiprovider 구현 1차(step 1) 결과

- **질문**: multiprovider 구현 1차(step 1) 결과는 어디까지 완료되었는가?
- **결정**: **PARTIAL** — domain/contracts 확장과 provider adapter 경계
  skeleton, control-plane wiring까지는 완료되었으나 CCXT/CCXT Pro의 live
  adapter 구현과 admin UI의 provider/instrument_type 노출은 의도적으로
  이번 세션에서 수행하지 않았다.
- **근거**:
  - `packages/domain/enums.py`: `Provider` StrEnum(KXT/CCXT/CCXT_PRO/OTHER)
    신규 추가. `InstrumentType`에 `SPOT`, `PERPETUAL` 추가 (FUTURE/OPTION
    기존 유지)로 crypto spot + USDT perpetual 구조 준비 완료.
  - `packages/domain/models.py`: `InstrumentRef`, `CollectionTarget`,
    `InstrumentSearchResult`에 `provider`, `canonical_symbol` 추가.
    `build_canonical_symbol()` helper 도입 — 형식 `<provider>:<venue>:<instrument_type>:<symbol>`.
  - `packages/contracts/events.py`, `packages/contracts/admin.py`:
    `DashboardEventEnvelope`, `DashboardControlEnvelope`,
    `RecentRuntimeEvent`에 provider/canonical_symbol 필드를 additive
    optional로 추가하여 기존 KXT 응답 shape를 깨지 않음.
  - `packages/adapters/`: `registry.py`, `kxt.py`, `ccxt.py` 신규.
    `ProviderRegistry` + `build_default_registry()`가 KXT/CCXT/CCXT_PRO 3개
    provider를 등록. CCXT/CCXT_PRO는 `implemented=False` skeleton.
  - `src/collector_control_plane.py`: `upsert_target`, `search_instruments`,
    `record_runtime_event`가 provider/instrument_type를 수용. KXT 기본값
    유지, 비-KXT provider는 market_scope="" 허용 (not-applicable 의미).
  - `apps/collector/runtime.py`: `register_target`이 provider를 수용하고
    provider != KXT일 때 `NotImplementedError`로 실패 loud — silent
    degradation 방지.
  - `apps/collector/service.py`: `start_dashboard_publication`이
    provider/instrument_type/canonical_symbol kwargs를 수용하며
    `build_default_registry()`를 서비스 생성 시 호출.
- **영향**:
  - 다음 세션은 **CCXT/CCXT Pro live adapter 구현** 또는 **admin UI의
    provider/instrument_type 노출 통합** 중 하나로 진행 가능. blocker
    해소 세션은 필요 없음 — 기존 KXT/KRX runtime은 그대로 동작.
  - admin UI에서 provider 필드를 입력·표시하도록 확장하려면 이번
    세션의 additive 필드를 소비하는 frontend 작업이 남아 있음.
  - 운영상 추가 DB 스키마 변경이 필요한 시점은 crypto provider의
    subscription persistence가 붙을 때로 지연됨.

---

## H24 — Binance spot + USDT perpetual live adapter (step 2) 결과

- **질문**: Binance spot + USDT perpetual live adapter step2 결과는?
- **결정**: **PASS** — 최소 라이브 경로(target upsert → provider-aware
  dispatch → BinanceLiveAdapter subscribe → runtime event publish →
  control-plane recent event) 가 라이브 데이터로 검증됨.
- **근거**:
  - `packages/adapters/ccxt.py`에 step1 skeleton 을 대체하는 실제
    `BinanceLiveAdapter` 구현. `ccxt.pro` lazy import 기반,
    spot=`binance` / perpetual=`binanceusdm` 자동 분기.
    `to_unified_symbol` / `exchange_id_for` 헬퍼로 `BTCUSDT` ↔
    `BTC/USDT` ↔ `BTC/USDT:USDT` 정규화.
  - `apps/collector/runtime.py`에 crypto 채널 분기:
    `_CryptoChannelKey(canonical_symbol, event_name)`,
    `_acquire_crypto_channel`/`_release_crypto_channel`/
    `_consume_crypto_channel`. spot/perp BTCUSDT 가 구조적으로 별도
    채널이며 dedupe 충돌 없음.
  - `_publish_event` 가 provider/canonical_symbol/instrument_type 을
    forward (legacy KXT 핸들러는 TypeError fallback).
  - `apps/collector/service.py`/`publisher.py` 가 provider/
    canonical/instrument_type 을 envelope 및 control_plane 으로 전달.
  - `src/collector_control_plane.py`의 `upsert_target` dedupe 가
    instrument_type 포함, `record_runtime_event` 매칭이
    canonical_symbol 우선 — spot/perp 트레이드가 정확히 자기 target
    만 매칭.
  - 신규 `tests/test_multiprovider_step2_ccxt.py` 8 case 통과,
    갱신된 step1 9 case + 기존 KXT 17 case 회귀 0 → 총 34 passed.
  - 라이브 검증: BTC/USDT spot + BTC/USDT:USDT perpetual 양쪽 모두
    1초 이내 트레이드 수신 확인. End-to-end runtime 경로
    (`provider=ccxt_pro`, `canonical_symbol=ccxt_pro:binance:spot:BTCUSDT`,
    `instrument_type=spot`, payload 정상) 라이브 데이터로 PASS.
  - `/Users/minkyu/workspace/kxt` 변경 0건.
  - 상세: `reviews/ksxt-migration-progress/multiprovider-binance-live-step2-report.md`
    (vault).
- **영향**:
  - 다음 세션은 (1) **admin UI 통합** —
    `AdminTargetUpsertRequest`/`DashboardSubscriptionRequest` 에
    provider/instrument_type 필드 추가, frontend crypto 입력 UI; 또는
    (2) **capability/event-catalog 완성** — provider×event_type matrix,
    crypto symbol seed 중 선택. (1) 을 1순위로 권장.
  - blocker 해소 세션 불필요. KXT/KRX 경로 회귀 없음.
  - `git push` 금지(H6) 는 H21 미해결 조건으로 그대로 유지.
