import logging
import os
from collections import OrderedDict
from datetime import UTC, datetime
from functools import partial

from kopf._cogs.structs import bodies
from kr8s import NotFoundError
from kr8s.objects import Event

NAMESPACE = os.environ.get("NAMESPACE", "default")
POD_NAME = os.environ.get("POD_NAME", "lb4-multiplexer")
DRYRUN_MODE = os.environ.get("DRYRUN_MODE", "").lower() in ("true", "1", "yes", "on")


class CacheDict(OrderedDict):
    """Dict with a limited length, ejecting LRUs as needed."""

    def __init__(self, *args, cache_len: int = 10, **kwargs):
        assert cache_len > 0
        self.cache_len = cache_len

        super().__init__(*args, **kwargs)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        super().move_to_end(key)

        while len(self) > self.cache_len:
            oldkey = next(iter(self))
            super().__delitem__(oldkey)

    def __getitem__(self, key):
        val = super().__getitem__(key)
        super().move_to_end(key)

        return val


event_cache = CacheDict(cache_len=1024)


def record_event(body, event_type, reason, message, action=None, fieldPath=None):
    reason = reason.strip()
    message = message.strip()
    if hasattr(body, "to_dict"):
        body = body.to_dict()
    ref = bodies.build_object_reference(body)
    if fieldPath:
        ref["fieldPath"] = fieldPath
    timestamp = datetime.now(UTC).isoformat()
    create = False
    action_str = action or ""
    cached_event = event_cache.get(
        (ref["uid"], event_type, reason, message, action_str)
    )
    if cached_event:
        if not DRYRUN_MODE:
            try:
                cached_event.patch(
                    {
                        "count": cached_event.raw.get("count", 1) + 1,
                        "lastTimestamp": timestamp,
                    }
                )
            except NotFoundError:
                create = True
            except Exception as exc:
                logging.error(f"Event patch error: {exc}")
                create = True
            else:
                event_obj = cached_event
        else:
            logging.debug(f"[DRYRUN] Would patch event {reason}: {message}")
            event_obj = cached_event
    else:
        create = True
    if create and not DRYRUN_MODE:
        event_obj = Event(
            {
                "metadata": {
                    "name": f"{body['metadata']['name']}-{reason}.{timestamp}",
                    "namespace": body["metadata"]["namespace"],
                },
                "involvedObject": ref,
                "reason": reason,
                "message": message,
                "type": event_type,
                "action": action,
                "source": {"component": POD_NAME},
                "reportingComponent": POD_NAME,
                "firstTimestamp": timestamp,
                "lastTimestamp": timestamp,
            }
        )
        for attempt in range(1, 4):
            try:
                event_obj.create()
                break
            except Exception as exc:
                if attempt == 3:
                    logging.error(f"Event create error (attempt {attempt}/3): {exc}")
                    return
                logging.warning(
                    f"Event create failed (attempt {attempt}/3): {exc}, retrying..."
                )
        event_cache[(ref["uid"], event_type, reason, message, action_str)] = event_obj
        logging.info(f"Event: {event_obj.to_dict()}")
    elif create and DRYRUN_MODE:
        logging.debug(f"[DRYRUN] Would create event {event_type} {reason}: {message}")

    # Record event for webserver debug UI
    try:
        import webserver

        resource_name = f"{body['metadata']['namespace']}/{body['metadata']['name']}"
        webserver.record_event(event_type, resource_name, f"{reason}: {message}")
    except Exception:
        pass  # Don't fail if webserver module is not available


info = partial(record_event, event_type="Normal")
warn = partial(record_event, event_type="Warning")
error = partial(record_event, event_type="Error")
