"""Minimal async Kafka/Redpanda JSON utilities."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any

try:
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
except ImportError:  # pragma: no cover
    AIOKafkaConsumer = None
    AIOKafkaProducer = None


class AsyncKafkaJsonBroker:
    """Lazy JSON producer/consumer wrapper for Kafka-compatible brokers."""

    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._producer: Any | None = None
        self._producer_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, *, topic: str, value: dict[str, Any], key: str | None = None) -> None:
        producer = await self._ensure_producer_started()
        await producer.send_and_wait(topic, value=value, key=key.encode("utf-8") if key is not None else None)

    @asynccontextmanager
    async def open_subscription(self, *, topic: str, group_id: str) -> AsyncIterator[Any]:
        consumer = self._build_consumer(topic=topic, group_id=group_id)
        await consumer.start()
        try:
            yield consumer
        finally:
            await consumer.stop()

    async def subscribe(self, *, topic: str, group_id: str) -> AsyncIterator[dict[str, Any]]:
        async with self.open_subscription(topic=topic, group_id=group_id) as consumer:
            async for message in consumer:
                if isinstance(message.value, dict):
                    yield message.value

    async def _ensure_producer_started(self) -> Any:
        if AIOKafkaProducer is None:
            raise RuntimeError("aiokafka is required for Kafka/Redpanda dashboard runtime")
        if self._producer is None:
            async with self._producer_lock:
                if self._producer is None:
                    producer = AIOKafkaProducer(
                        bootstrap_servers=self._bootstrap_servers,
                        value_serializer=lambda value: json.dumps(value, ensure_ascii=False).encode("utf-8"),
                    )
                    await producer.start()
                    self._producer = producer
        return self._producer

    def _build_consumer(self, *, topic: str, group_id: str) -> Any:
        if AIOKafkaConsumer is None:
            raise RuntimeError("aiokafka is required for Kafka/Redpanda dashboard runtime")
        return AIOKafkaConsumer(
            topic,
            bootstrap_servers=self._bootstrap_servers,
            group_id=group_id,
            enable_auto_commit=False,
            auto_offset_reset="latest",
            value_deserializer=lambda value: json.loads(value.decode("utf-8")),
        )
