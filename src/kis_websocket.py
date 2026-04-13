from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from typing import Any, AsyncIterator
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import websockets

from src.config import Settings


PROGRAM_TRADE_COLUMNS = [
    "MKSC_SHRN_ISCD",
    "STCK_CNTG_HOUR",
    "SELN_CNQN",
    "SELN_TR_PBMN",
    "SHNU_CNQN",
    "SHNU_TR_PBMN",
    "NTBY_CNQN",
    "NTBY_TR_PBMN",
    "SELN_RSQN",
    "SHNU_RSQN",
    "WHOL_NTBY_QTY",
]

DISPLAY_RENAME_MAP = {
    "MKSC_SHRN_ISCD": "종목코드",
    "STCK_CNTG_HOUR": "체결시각",
    "SELN_CNQN": "프로그램매도체결량",
    "SELN_TR_PBMN": "프로그램매도거래대금",
    "SHNU_CNQN": "프로그램매수체결량",
    "SHNU_TR_PBMN": "프로그램매수거래대금",
    "NTBY_CNQN": "프로그램순매수체결량",
    "NTBY_TR_PBMN": "프로그램순매수거래대금",
    "SELN_RSQN": "매도호가잔량",
    "SHNU_RSQN": "매수호가잔량",
    "WHOL_NTBY_QTY": "전체순매수호가잔량",
}

MARKET_TR_ID = {
    "krx": "H0STPGM0",
    "nxt": "H0NXPGM0",
    "total": "H0UNPGM0",
}

TRADE_PRICE_TR_ID = {
    "krx": "H0STCNT0",
    "nxt": "H0NXCNT0",
    "total": "H0UNCNT0",
}

TRADE_PRICE_COLUMNS = [
    "MKSC_SHRN_ISCD",
    "STCK_CNTG_HOUR",
    "STCK_PRPR",
]

TRADE_PRICE_DISPLAY_RENAME_MAP = {
    "MKSC_SHRN_ISCD": "종목코드",
    "STCK_CNTG_HOUR": "체결시각",
    "STCK_PRPR": "현재가",
}

ORDER_BOOK_COLUMNS_KRX = [
    "MKSC_SHRN_ISCD",
    "BSOP_HOUR",
    "HOUR_CLS_CODE",
    "ASKP1",
    "ASKP2",
    "ASKP3",
    "ASKP4",
    "ASKP5",
    "ASKP6",
    "ASKP7",
    "ASKP8",
    "ASKP9",
    "ASKP10",
    "BIDP1",
    "BIDP2",
    "BIDP3",
    "BIDP4",
    "BIDP5",
    "BIDP6",
    "BIDP7",
    "BIDP8",
    "BIDP9",
    "BIDP10",
    "ASKP_RSQN1",
    "ASKP_RSQN2",
    "ASKP_RSQN3",
    "ASKP_RSQN4",
    "ASKP_RSQN5",
    "ASKP_RSQN6",
    "ASKP_RSQN7",
    "ASKP_RSQN8",
    "ASKP_RSQN9",
    "ASKP_RSQN10",
    "BIDP_RSQN1",
    "BIDP_RSQN2",
    "BIDP_RSQN3",
    "BIDP_RSQN4",
    "BIDP_RSQN5",
    "BIDP_RSQN6",
    "BIDP_RSQN7",
    "BIDP_RSQN8",
    "BIDP_RSQN9",
    "BIDP_RSQN10",
    "TOTAL_ASKP_RSQN",
    "TOTAL_BIDP_RSQN",
    "OVTM_TOTAL_ASKP_RSQN",
    "OVTM_TOTAL_BIDP_RSQN",
    "ANTC_CNPR",
    "ANTC_CNQN",
    "ANTC_VOL",
    "ANTC_CNTG_VRSS",
    "ANTC_CNTG_VRSS_SIGN",
    "ANTC_CNTG_PRDY_CTRT",
    "ACML_VOL",
    "TOTAL_ASKP_RSQN_ICDC",
    "TOTAL_BIDP_RSQN_ICDC",
    "OVTM_TOTAL_ASKP_ICDC",
    "OVTM_TOTAL_BIDP_ICDC",
    "STCK_DEAL_CLS_CODE",
]

ORDER_BOOK_COLUMNS_NXT_TOTAL = ORDER_BOOK_COLUMNS_KRX + [
    "KMID_PRC",
    "KMID_TOTAL_RSQN",
    "KMID_CLS_CODE",
    "NMID_PRC",
    "NMID_TOTAL_RSQN",
    "NMID_CLS_CODE",
]

ORDER_BOOK_DISPLAY_RENAME_MAP = {
    "MKSC_SHRN_ISCD": "종목코드",
    "BSOP_HOUR": "호가시각",
    "ASKP1": "매도호가1",
    "ASKP2": "매도호가2",
    "ASKP3": "매도호가3",
    "ASKP4": "매도호가4",
    "ASKP5": "매도호가5",
    "ASKP6": "매도호가6",
    "ASKP7": "매도호가7",
    "ASKP8": "매도호가8",
    "ASKP9": "매도호가9",
    "ASKP10": "매도호가10",
    "BIDP1": "매수호가1",
    "BIDP2": "매수호가2",
    "BIDP3": "매수호가3",
    "BIDP4": "매수호가4",
    "BIDP5": "매수호가5",
    "BIDP6": "매수호가6",
    "BIDP7": "매수호가7",
    "BIDP8": "매수호가8",
    "BIDP9": "매수호가9",
    "BIDP10": "매수호가10",
    "ASKP_RSQN1": "매도잔량1",
    "ASKP_RSQN2": "매도잔량2",
    "ASKP_RSQN3": "매도잔량3",
    "ASKP_RSQN4": "매도잔량4",
    "ASKP_RSQN5": "매도잔량5",
    "ASKP_RSQN6": "매도잔량6",
    "ASKP_RSQN7": "매도잔량7",
    "ASKP_RSQN8": "매도잔량8",
    "ASKP_RSQN9": "매도잔량9",
    "ASKP_RSQN10": "매도잔량10",
    "BIDP_RSQN1": "매수잔량1",
    "BIDP_RSQN2": "매수잔량2",
    "BIDP_RSQN3": "매수잔량3",
    "BIDP_RSQN4": "매수잔량4",
    "BIDP_RSQN5": "매수잔량5",
    "BIDP_RSQN6": "매수잔량6",
    "BIDP_RSQN7": "매수잔량7",
    "BIDP_RSQN8": "매수잔량8",
    "BIDP_RSQN9": "매수잔량9",
    "BIDP_RSQN10": "매수잔량10",
    "TOTAL_ASKP_RSQN": "총매도잔량",
    "TOTAL_BIDP_RSQN": "총매수잔량",
    "OVTM_TOTAL_ASKP_RSQN": "시간외총매도잔량",
    "OVTM_TOTAL_BIDP_RSQN": "시간외총매수잔량",
    "ANTC_CNPR": "예상체결가",
    "ANTC_CNQN": "예상체결량",
    "ANTC_VOL": "예상거래량",
    "ACML_VOL": "누적거래량",
    "KMID_PRC": "중간가매도",
    "KMID_TOTAL_RSQN": "중간가매도잔량",
    "NMID_PRC": "중간가매수",
    "NMID_TOTAL_RSQN": "중간가매수잔량",
}

ORDER_BOOK_TR_ID = {
    "krx": "H0STASP0",
    "nxt": "H0NXASP0",
    "total": "H0UNASP0",
}

ORDER_BOOK_COLUMNS_BY_TR_ID = {
    "H0STASP0": ORDER_BOOK_COLUMNS_KRX,
    "H0NXASP0": ORDER_BOOK_COLUMNS_NXT_TOTAL,
    "H0UNASP0": ORDER_BOOK_COLUMNS_NXT_TOTAL,
}

SCHEMA_BY_TR_ID = {
    **{
        tr_id: {
            "event": "program_trade",
            "columns": PROGRAM_TRADE_COLUMNS,
            "rename_map": DISPLAY_RENAME_MAP,
            "numeric_columns": PROGRAM_TRADE_COLUMNS[2:],
        }
        for tr_id in MARKET_TR_ID.values()
    },
    **{
        tr_id: {
            "event": "order_book",
            "columns": columns,
            "rename_map": ORDER_BOOK_DISPLAY_RENAME_MAP,
            "numeric_columns": columns[3:],
        }
        for tr_id, columns in ORDER_BOOK_COLUMNS_BY_TR_ID.items()
    },
    **{
        tr_id: {
            "event": "trade_price",
            "columns": TRADE_PRICE_COLUMNS,
            "rename_map": TRADE_PRICE_DISPLAY_RENAME_MAP,
            "numeric_columns": ["STCK_PRPR"],
        }
        for tr_id in TRADE_PRICE_TR_ID.values()
    },
}

KST = ZoneInfo("Asia/Seoul")
DOMESTIC_REGULAR_SESSION_START = "090000"
DOMESTIC_REGULAR_SESSION_END = "153000"


class KISProgramTradeClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._active_ws: Any | None = None
        self._access_token: str | None = None
        self._access_token_expires_at: datetime | None = None
        self._access_token_lock = threading.Lock()

    def _is_access_token_valid(self) -> bool:
        if not self._access_token:
            return False
        if self._access_token_expires_at is None:
            return True
        return datetime.utcnow() < (self._access_token_expires_at - timedelta(minutes=1))

    @staticmethod
    def _parse_access_token_expiry(body: dict[str, Any]) -> datetime | None:
        expires_at_text = str(body.get("access_token_token_expired") or "").strip()
        if expires_at_text:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.strptime(expires_at_text, fmt)
                except ValueError:
                    continue

        expires_in = body.get("expires_in")
        try:
            expires_in_seconds = int(expires_in)
        except (TypeError, ValueError):
            return None

        if expires_in_seconds <= 0:
            return None
        return datetime.utcnow() + timedelta(seconds=expires_in_seconds)

    def get_access_token(self) -> str:
        if self._is_access_token_valid():
            return str(self._access_token)

        with self._access_token_lock:
            if self._is_access_token_valid():
                return str(self._access_token)

        self.settings.require_kis_credentials()
        url = f"{self.settings.rest_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.settings.app_key,
            "appsecret": self.settings.app_secret,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/plain",
            "charset": "UTF-8",
        }

        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=15)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise RuntimeError(f"KIS access token request failed: {body}") from exc

        body = response.json()
        access_token = body.get("access_token")
        if not access_token:
            raise RuntimeError(f"access_token 발급 실패: {body}")
        self._access_token = str(access_token)
        self._access_token_expires_at = self._parse_access_token_expiry(body)
        return access_token

    def get_approval_key(self) -> str:
        self.settings.require_kis_credentials()
        url = f"{self.settings.rest_url}/oauth2/Approval"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.settings.app_key,
            "secretkey": self.settings.app_secret,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "charset": "UTF-8",
        }

        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=15)
        response.raise_for_status()

        body = response.json()
        approval_key = body.get("approval_key")
        if not approval_key:
            raise RuntimeError(f"approval_key 발급 실패: {body}")
        return approval_key

    def build_subscribe_message(self, approval_key: str, symbol: str, tr_id: str) -> dict:
        return {
            "header": {
                "approval_key": approval_key,
                "content-type": "utf-8",
                "custtype": "P",
                "tr_type": "1",
            },
            "body": {
                "input": {
                    "tr_id": tr_id,
                    "tr_key": symbol,
                }
            },
        }

    def parse_realtime_frame(self, raw: str) -> tuple[str, pd.DataFrame]:
        parts = raw.split("|", 3)
        if len(parts) != 4:
            raise ValueError(f"예상하지 못한 실시간 메시지 형식: {raw}")

        encrypted_flag, tr_id, count_text, payload = parts
        if encrypted_flag == "1":
            raise RuntimeError(f"암호화된 메시지는 현재 샘플에서 처리하지 않음: tr_id={tr_id}")

        try:
            row_count = int(count_text)
        except ValueError as exc:
            raise ValueError(f"유효하지 않은 실시간 row count: {count_text}") from exc

        schema = SCHEMA_BY_TR_ID.get(tr_id)
        if not schema:
            raise ValueError(f"지원하지 않는 실시간 TR ID: {tr_id}")

        values = payload.split("^")
        columns = schema["columns"]
        column_count = len(columns)
        expected_value_count = row_count * column_count
        row_width = column_count

        if len(values) != expected_value_count:
            if len(values) > expected_value_count and row_count > 0 and len(values) % row_count == 0:
                row_width = len(values) // row_count
            else:
                raise ValueError(
                    f"컬럼 수가 맞지 않음: values={len(values)} columns={column_count} count={count_text}"
                )

        if len(values) % row_width != 0:
            raise ValueError(
                f"컬럼 수가 맞지 않음: values={len(values)} columns={column_count} count={count_text}"
            )

        rows = [
            values[index:index + row_width][:column_count]
            for index in range(0, len(values), row_width)
        ]
        frame = pd.DataFrame(rows, columns=columns)

        for col in schema["numeric_columns"]:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

        return schema["event"], frame.rename(columns=schema["rename_map"])

    def fetch_intraday_chart(self, symbol: str, market: str, max_calls: int = 4) -> list[dict[str, Any]]:
        market = market.lower()
        market_div = {
            "krx": "J",
            "nxt": "NX",
            "total": "UN",
        }.get(market)
        if not market_div:
            raise ValueError(f"지원하지 않는 market: {market}")

        url = f"{self.settings.rest_url}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.get_access_token()}",
            "appkey": self.settings.app_key,
            "appsecret": self.settings.app_secret,
            "tr_id": "FHKST03010200",
            "custtype": "P",
        }

        current_time = self._get_intraday_chart_anchor_time()
        rows: list[dict[str, Any]] = []
        seen_times: set[str] = set()

        for _ in range(max_calls):
            params = {
                "FID_ETC_CLS_CODE": "",
                "FID_COND_MRKT_DIV_CODE": market_div,
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_HOUR_1": current_time,
                "FID_PW_DATA_INCU_YN": "Y",
            }
            response = requests.get(url, headers=headers, params=params, timeout=15)
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                try:
                    body = response.json()
                except ValueError:
                    body = response.text
                raise RuntimeError(f"KIS intraday chart request failed: {body}") from exc
            body = response.json()

            if body.get("rt_cd") not in {None, "0"}:
                raise RuntimeError(body.get("msg1") or f"분봉 조회 실패: {body}")

            output = body.get("output2") or []
            if not output:
                break

            page_min_time = None
            added_count = 0
            for item in output:
                time_text = str(item.get("stck_cntg_hour") or "").strip()
                if not time_text or time_text in seen_times:
                    continue
                seen_times.add(time_text)
                rows.append(item)
                added_count += 1
                page_min_time = time_text if page_min_time is None else min(page_min_time, time_text)

            if added_count == 0 or not page_min_time or page_min_time <= "090000" or len(output) < 30:
                break

            next_dt = datetime.strptime(page_min_time, "%H%M%S") - timedelta(seconds=1)
            current_time = next_dt.strftime("%H%M%S")

        rows.sort(key=lambda item: str(item.get("stck_cntg_hour") or ""))
        return rows

    @staticmethod
    def _get_intraday_chart_anchor_time(now: datetime | None = None) -> str:
        """Return a practical KIS minute-chart cursor for domestic regular session rows.

        KIS treats FID_INPUT_HOUR_1 as a real query-time cursor, so using a blanket
        late-night value like 235959 can surface synthetic-looking late rows. This
        first-pass policy keeps the collector on the regular-session minute chart
        path by anchoring to the current KST time during the session and to the
        regular-session close (15:30:00) outside the session.

        This intentionally does not claim to solve after-hours/session-calendar
        semantics in this step.
        """

        current = now.astimezone(KST) if now is not None else datetime.now(KST)
        time_text = current.strftime("%H%M%S")
        if DOMESTIC_REGULAR_SESSION_START <= time_text <= DOMESTIC_REGULAR_SESSION_END:
            return time_text
        return DOMESTIC_REGULAR_SESSION_END

    async def aclose(self) -> None:
        ws = self._active_ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def _iter_realtime_frames(self, ws: Any) -> AsyncIterator[tuple[str, pd.DataFrame]]:
        async for raw in ws:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")

            if raw.startswith("{"):
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    print(f"[WARN] JSON 파싱 실패: {raw}")
                    continue

                tr_id = body.get("header", {}).get("tr_id")
                if tr_id == "PINGPONG":
                    await ws.pong(raw)
                    continue

                msg = body.get("body", {}).get("msg1")
                rt_cd = body.get("body", {}).get("rt_cd")
                if msg:
                    print(f"[SYS] rt_cd={rt_cd} tr_id={tr_id} msg={msg}")
                continue

            if raw and raw[0] in {"0", "1"}:
                yield self.parse_realtime_frame(raw)

    async def subscribe(self, symbol: str, market: str) -> AsyncIterator[pd.DataFrame]:
        market = market.lower()
        if market not in MARKET_TR_ID:
            raise ValueError(f"지원하지 않는 market: {market}")

        approval_key = self.get_approval_key()
        ws_url = f"{self.settings.ws_url}/tryitout"
        message = self.build_subscribe_message(
            approval_key=approval_key,
            symbol=symbol,
            tr_id=MARKET_TR_ID[market],
        )

        async with websockets.connect(ws_url, ping_interval=30, ping_timeout=30) as ws:
            self._active_ws = ws
            try:
                await ws.send(json.dumps(message))

                async for event_type, frame in self._iter_realtime_frames(ws):
                    if event_type == "program_trade":
                        yield frame
            finally:
                if self._active_ws is ws:
                    self._active_ws = None

    async def subscribe_dashboard(self, symbol: str, market: str) -> AsyncIterator[dict[str, Any]]:
        market = market.lower()
        if market not in MARKET_TR_ID or market not in ORDER_BOOK_TR_ID or market not in TRADE_PRICE_TR_ID:
            raise ValueError(f"지원하지 않는 market: {market}")

        approval_key = self.get_approval_key()
        ws_url = f"{self.settings.ws_url}/tryitout"
        messages = [
            self.build_subscribe_message(approval_key=approval_key, symbol=symbol, tr_id=MARKET_TR_ID[market]),
            self.build_subscribe_message(approval_key=approval_key, symbol=symbol, tr_id=ORDER_BOOK_TR_ID[market]),
            self.build_subscribe_message(approval_key=approval_key, symbol=symbol, tr_id=TRADE_PRICE_TR_ID[market]),
        ]

        async with websockets.connect(ws_url, ping_interval=30, ping_timeout=30) as ws:
            self._active_ws = ws
            try:
                for message in messages:
                    await ws.send(json.dumps(message))

                async for event_type, frame in self._iter_realtime_frames(ws):
                    yield {"event": event_type, "frame": frame}
            finally:
                if self._active_ws is ws:
                    self._active_ws = None
