"""KIS realtime websocket client for narrow adapter runtime paths."""

from __future__ import annotations

import asyncio
import json
import contextlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator

import websockets

from packages.contracts import SubscriptionSpec

from .auth import KISAuthMaterial
from .config import KISSettings
from .mappers import KISRealtimeRow, KISSubscriptionBinding, resolve_realtime_columns, resolve_subscription_binding


@dataclass(frozen=True, slots=True)
class KISRealtimeSubscriptionMessage:
    """Serializable KIS websocket subscribe payload scaffold."""

    binding: KISSubscriptionBinding
    approval_key: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "header": {
                "approval_key": self.approval_key,
                "content-type": "utf-8",
                "custtype": "P",
                "tr_type": "1",
            },
            "body": {
                "input": {
                    "tr_id": self.binding.tr_id,
                    "tr_key": self.binding.tr_key,
                }
            },
        }


class KISRealtimeClient:
    """Realtime websocket client used by the first KIS trade-stream runtime path."""

    def __init__(self, settings: KISSettings):
        self._settings = settings

    @property
    def settings(self) -> KISSettings:
        return self._settings

    def build_subscription_message(
        self,
        subscription: SubscriptionSpec,
        auth: KISAuthMaterial,
    ) -> KISRealtimeSubscriptionMessage:
        binding = resolve_subscription_binding(subscription)
        return KISRealtimeSubscriptionMessage(
            binding=binding,
            approval_key=auth.approval_key or "",
        )

    def connect(self):
        """Open a websocket connection to the KIS realtime endpoint.

        KIS uses its own application-level PINGPONG keep-alive (tr_id == "PINGPONG"
        frames that the client must echo back with ws.pong()).  Enabling the
        websockets library's protocol-level ping on top of that causes a duplicate
        keep-alive path: KIS never responds to RFC-6455 pings, which triggers a
        ``sent 1011 (internal error) keepalive ping timeout`` close and drops the
        connection.  Disable protocol-level pings so only the app-level PINGPONG
        path is active.
        """

        return websockets.connect(
            f"{self._settings.ws_url}/tryitout",
            ping_interval=None,
            ping_timeout=None,
        )

    async def subscribe_many(
        self,
        subscriptions: list[SubscriptionSpec],
        auth: KISAuthMaterial,
    ) -> list[KISRealtimeSubscriptionMessage]:
        """Build outbound subscription messages for one websocket session."""

        return [self.build_subscription_message(subscription, auth) for subscription in subscriptions]

    async def stream_subscription_rows(
        self,
        subscription: SubscriptionSpec,
        auth: KISAuthMaterial,
    ) -> AsyncIterator[KISRealtimeRow]:
        """Yield parsed realtime rows for a single KIS subscription."""

        async for row in self.stream_subscriptions_rows([subscription], auth):
            yield row

    async def stream_subscriptions_rows(
        self,
        subscriptions: list[SubscriptionSpec],
        auth: KISAuthMaterial,
    ) -> AsyncIterator[KISRealtimeRow]:
        """Yield parsed realtime rows for multiple subscriptions over one websocket."""

        messages = await self.subscribe_many(subscriptions, auth)
        bindings_by_subscription_key = {(message.binding.tr_id, message.binding.tr_key): message.binding for message in messages}

        async with self.connect() as ws:
            for message in messages:
                await ws.send(json.dumps(message.as_dict()))

            async for raw in ws:
                parsed = await self._parse_many_message(raw=raw, bindings_by_subscription_key=bindings_by_subscription_key, ws=ws)
                if parsed is None:
                    continue
                for row in parsed:
                    yield row

    async def stream_subscriptions_rows_until(
        self,
        subscriptions: list[SubscriptionSpec],
        auth: KISAuthMaterial,
        *,
        until: asyncio.Event,
    ) -> AsyncIterator[KISRealtimeRow]:
        """Yield rows until the caller requests a session refresh."""

        messages = await self.subscribe_many(subscriptions, auth)
        bindings_by_subscription_key = {(message.binding.tr_id, message.binding.tr_key): message.binding for message in messages}

        async with self.connect() as ws:
            for message in messages:
                await ws.send(json.dumps(message.as_dict()))

            while not until.is_set():
                receive_task = asyncio.create_task(ws.recv())
                refresh_task = asyncio.create_task(until.wait())
                done, pending = await asyncio.wait(
                    {receive_task, refresh_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

                if refresh_task in done and until.is_set():
                    with contextlib.suppress(Exception):
                        await ws.close()
                    return

                raw = receive_task.result()
                parsed = await self._parse_many_message(raw=raw, bindings_by_subscription_key=bindings_by_subscription_key, ws=ws)
                if parsed is None:
                    continue
                for row in parsed:
                    yield row

    async def _parse_message(
        self,
        *,
        raw: str | bytes,
        binding: KISSubscriptionBinding,
        ws: Any,
    ) -> list[KISRealtimeRow] | None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")

        if not raw:
            return None

        if raw.startswith("{"):
            body = json.loads(raw)
            tr_id = body.get("header", {}).get("tr_id")
            if tr_id == "PINGPONG":
                await ws.pong(raw)
            return None

        if raw[0] not in {"0", "1"}:
            return None

        encrypted_flag, tr_id, count_text, payload = raw.split("|", 3)
        if encrypted_flag == "1":
            raise RuntimeError(f"Encrypted realtime frames are not supported yet: tr_id={tr_id}")
        if tr_id != binding.tr_id:
            return None

        row_count = int(count_text)
        columns = resolve_realtime_columns(tr_id)
        values = payload.split("^")
        row_width = len(columns)
        expected_value_count = row_count * len(columns)

        if len(values) != expected_value_count:
            if row_count <= 0 or len(values) % row_count != 0:
                raise ValueError(
                    f"Unexpected realtime payload width for {tr_id}: values={len(values)} count={count_text}"
                )
            row_width = len(values) // row_count

        received_at = datetime.now().astimezone()
        rows: list[KISRealtimeRow] = []
        for start in range(0, len(values), row_width):
            row_values = tuple(values[start:start + row_width][: len(columns)])
            fields = dict(zip(columns, row_values, strict=False))
            rows.append(
                KISRealtimeRow(
                    binding=binding,
                    tr_id=tr_id,
                    received_at=received_at,
                    raw_message=raw,
                    values=row_values,
                    fields=fields,
                )
            )
        return rows

    async def _parse_many_message(
        self,
        *,
        raw: str | bytes,
        bindings_by_subscription_key: dict[tuple[str, str], KISSubscriptionBinding],
        ws: Any,
    ) -> list[KISRealtimeRow] | None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")

        if not raw:
            return None

        if raw.startswith("{"):
            body = json.loads(raw)
            tr_id = body.get("header", {}).get("tr_id")
            if tr_id == "PINGPONG":
                await ws.pong(raw)
            return None

        if raw[0] not in {"0", "1"}:
            return None

        _, tr_id, count_text, payload = raw.split("|", 3)
        columns = resolve_realtime_columns(tr_id)
        if not columns:
            return None

        row_count = int(count_text)
        values = payload.split("^")
        row_width = len(columns)
        if row_count > 0 and len(values) % row_count == 0:
            row_width = len(values) // row_count

        bindings: list[KISSubscriptionBinding] = []
        symbol_column_index = 0
        for start in range(0, len(values), max(row_width, 1)):
            row_values = values[start:start + row_width]
            if not row_values:
                continue
            symbol = row_values[symbol_column_index]
            binding = bindings_by_subscription_key.get((tr_id, symbol))
            if binding is None:
                return None
            bindings.append(binding)

        if not bindings:
            return None

        parsed_rows: list[KISRealtimeRow] = []
        seen_binding_keys: set[tuple[str, str]] = set()
        for binding in bindings:
            binding_key = (binding.tr_id, binding.tr_key)
            if binding_key in seen_binding_keys:
                continue
            seen_binding_keys.add(binding_key)
            rows = await self._parse_message(raw=raw, binding=binding, ws=ws)
            if rows is None:
                continue
            parsed_rows.extend(row for row in rows if row.fields.get("MKSC_SHRN_ISCD") == binding.tr_key)
        return parsed_rows or None
