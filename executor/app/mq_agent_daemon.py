"""
MQ 任务消费者：订阅 **`MQ_TOPIC_AGENT`**（默认 `execute_tasks_agent`），
与 `mq_execute_consumer.process_mq_execute_task_body` 同源执行路径（`_execute_impl` → Redis）。

已移除 fast-lane / `execute_tasks` 与 `V1_AGENT_LANE_*` 开关；编排器在 MQ 模式下仅向本队列发布。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time

logger = logging.getLogger(__name__)


def _worker_id() -> str:
    from app.mq_execute_consumer import mq_consumer_worker_id

    return mq_consumer_worker_id()


async def _handle_agent_message(body: bytes) -> None:
    """单测兼容入口；与 `_handle_message_sync` 同源。"""
    from app.mq_execute_consumer import process_mq_execute_task_body

    await process_mq_execute_task_body(body, log_role="agent_daemon")


def _handle_message_sync(body: bytes) -> None:
    try:
        asyncio.run(_handle_agent_message(body))
    except Exception as e:
        logger.exception("agent_daemon handle_message failed: %s", e)


def run_agent_daemon() -> None:
    import pika
    from pika.exceptions import AMQPConnectionError, ChannelWrongStateError, StreamLostError

    broker_url = (os.getenv("MQ_BROKER_URL") or "").strip()
    if not broker_url:
        logger.error("MQ_BROKER_URL is required for agent daemon")
        sys.exit(1)
    topic = (os.getenv("MQ_TOPIC_AGENT") or "execute_tasks_agent").strip() or "execute_tasks_agent"
    retry_delay = max(1, int(os.getenv("MQ_AGENT_CONNECT_RETRY_DELAY", os.getenv("MQ_WORKER_CONNECT_RETRY_DELAY", "5"))))
    max_retries = int(os.getenv("MQ_AGENT_CONNECT_MAX_RETRIES", os.getenv("MQ_WORKER_CONNECT_MAX_RETRIES", "0")))

    params = pika.URLParameters(broker_url)

    while True:
        conn = None
        attempt = 0
        while True:
            attempt += 1
            try:
                conn = pika.BlockingConnection(params)
                break
            except Exception as e:
                if max_retries and attempt >= max_retries:
                    logger.error("agent mq connect failed after %d attempts: %s", max_retries, e)
                    sys.exit(1)
                logger.warning(
                    "agent mq connect failed (attempt %s), retry in %ss: %s",
                    attempt,
                    retry_delay,
                    e,
                )
                time.sleep(retry_delay)

        try:
            ch = conn.channel()
            ch.queue_declare(queue=topic, durable=True)
            ch.basic_qos(prefetch_count=1)

            def on_message(channel, method, properties, body):
                delivery_tag = method.delivery_tag

                def work() -> None:
                    try:
                        _handle_message_sync(body)
                    finally:
                        def ack_on_ioloop() -> None:
                            try:
                                ch.basic_ack(delivery_tag=delivery_tag)
                            except (StreamLostError, AMQPConnectionError, ChannelWrongStateError, OSError) as ack_err:
                                logger.warning("agent basic_ack failed: %s", ack_err)
                            except Exception:
                                logger.exception("agent basic_ack failed")

                        try:
                            conn.add_callback_threadsafe(ack_on_ioloop)
                        except Exception as sched_err:
                            logger.exception("agent add_callback_threadsafe failed: %s", sched_err)

                threading.Thread(target=work, name="mq-agent-daemon", daemon=True).start()

            ch.basic_consume(queue=topic, on_message_callback=on_message)
            logger.info("agent_daemon consuming queue=%s worker_id=%s", topic, _worker_id())
            ch.start_consuming()
        except (AMQPConnectionError, StreamLostError, OSError, ConnectionResetError) as e:
            logger.warning("agent mq consumer lost, reconnect in %ss: %s", retry_delay, e)
        except KeyboardInterrupt:
            logger.info("agent_daemon interrupted")
            try:
                if conn and conn.is_open:
                    conn.close()
            except Exception:
                pass
            raise
        except Exception:
            logger.exception("agent_daemon consumer unexpected error, reconnect")
        finally:
            try:
                if conn and conn.is_open:
                    conn.close()
            except Exception:
                pass
        time.sleep(retry_delay)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_agent_daemon()
