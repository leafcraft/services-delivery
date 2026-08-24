"""services-delivery — Services Delivery."""
import asyncio
import signal
import sys

from dotenv import load_dotenv
from leafmesh import EventType, LeafMesh, LeafMeshLogger

import agency.tools  # noqa: F401 — registers @global_tool tools BEFORE from_yaml/start()
from agency._shared import audit_logger as _audit_logger

load_dotenv()
logger = LeafMeshLogger(__name__)

leafmesh = LeafMesh.from_yaml("configs/config.yaml")

# Register the WORM audit sink as the intelligence function for the
# custom external delivery_audit_logger_agent (SDK 2.4+ API).
_audit_logger.register(leafmesh, "delivery_audit_logger_agent")


async def _on_agent_error(event):
    logger.warning(f"[delivery-audit] AGENT_ERROR session={event.session_id} data={(event.data or {})!r}")

async def _on_manager_intervention(event):
    logger.warning(f"[delivery-ops] MANAGER_INTERVENTION session={event.session_id} data={(event.data or {})!r}")

async def _on_intervention_needed(event):
    logger.error(f"[delivery-ops] INTERVENTION_NEEDED session={event.session_id} data={(event.data or {})!r}")

async def _on_human_input_timeout(event):
    logger.warning(f"[delivery-audit] HITL TIMEOUT session={event.session_id} data={(event.data or {})!r}")


async def main():
    await leafmesh.start()

    leafmesh.event_bus.subscribe(EventType.AGENT_ERROR,           _on_agent_error)
    leafmesh.event_bus.subscribe(EventType.MANAGER_INTERVENTION,  _on_manager_intervention)
    leafmesh.event_bus.subscribe(EventType.INTERVENTION_NEEDED,   _on_intervention_needed)
    leafmesh.event_bus.subscribe(EventType.HUMAN_INPUT_TIMEOUT,   _on_human_input_timeout)

    try:
        await leafmesh.enable_self_healing()
        logger.info("Self-healing enabled")
    except AttributeError:
        pass

    logger.info("services-delivery — Services Delivery mesh running")
    logger.info("API docs: http://127.0.0.1:18820/docs")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)
        await stop_event.wait()
    else:
        try:
            await stop_event.wait()
        except KeyboardInterrupt:
            pass

    logger.info("Shutting down...")
    await leafmesh.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
