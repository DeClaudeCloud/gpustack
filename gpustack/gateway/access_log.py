"""User agents for gateway-served requests, read from the embedded gateway's
access log.

The gateway's usage report -- what writes ``model_usage_details`` for gateway
traffic -- carries no User-Agent. In ``embedded`` gateway mode, Envoy runs
beside the server and writes every request to an access log
(``<log_dir>/higress/access.log``, one JSON object per line, see
``pack/rootfs/etc/istio/config/mesh``) that does carry it, keyed by the same
``x-request-id`` the usage row stores. ``GatewayAccessLog`` follows that file
and lends the user agent to the usage rows.

Neither the usage report nor the log line is guaranteed to arrive first, so
two paths meet here:

* at insert time, ``store_usage_metrics`` asks ``user_agent_for`` and stores
  what the log has already shown;
* rows inserted before their log line was read are handed to ``defer``, and
  the follower fills them in once the line appears.

Only user agents seen in the last ``retention_seconds`` are kept, and a
deferred row whose line never appears within that window is given up on.
"""

import asyncio
import json
import logging
import os
import time
from collections import OrderedDict
from typing import IO, Callable, Dict, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

# The width of ``user_agent`` as ``AutoString`` renders it on MySQL and
# OceanBase (VARCHAR(255)), which reject longer values instead of truncating.
USER_AGENT_MAX_LENGTH = 255

_POLL_INTERVAL_SECONDS = 1.0
_RETENTION_SECONDS = 300.0
_MAX_ENTRIES = 100_000
# Upper bound on one poll's read, so a large backlog cannot stall the loop.
_MAX_READ_BYTES = 4 * 1024 * 1024


def normalize_user_agent(value: object) -> Optional[str]:
    """A User-Agent as stored: stripped, at most ``USER_AGENT_MAX_LENGTH``
    characters, or ``None`` when absent (Envoy logs a missing header as ``-``).
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value == "-":
        return None
    return value[:USER_AGENT_MAX_LENGTH]


class GatewayAccessLog:
    """Follows the embedded gateway's access log; see the module docstring."""

    def __init__(
        self,
        path: str,
        *,
        retention_seconds: float = _RETENTION_SECONDS,
        max_entries: int = _MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = path
        self._retention = retention_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._user_agents: "OrderedDict[str, Tuple[str, float]]" = OrderedDict()
        self._deferred: Dict[str, float] = {}
        self._file: Optional[IO[str]] = None
        self._inode: Optional[int] = None
        self._partial = ""

    # -- lookups ------------------------------------------------------------

    def user_agent_for(self, request_id: str) -> Optional[str]:
        entry = self._user_agents.get(request_id)
        return entry[0] if entry else None

    def defer(self, request_ids: Iterable[str]) -> None:
        """Remember rows stored without a user agent, to fill in later."""
        now = self._clock()
        for request_id in request_ids:
            self._deferred[request_id] = now

    def take_resolved(self) -> Dict[str, str]:
        """Deferred request ids whose user agent has since been read.

        Resolved and expired deferrals are forgotten.
        """
        now = self._clock()
        resolved: Dict[str, str] = {}
        for request_id, since in list(self._deferred.items()):
            user_agent = self.user_agent_for(request_id)
            if user_agent is not None:
                resolved[request_id] = user_agent
                del self._deferred[request_id]
            elif now - since > self._retention:
                del self._deferred[request_id]
        return resolved

    # -- reading ------------------------------------------------------------

    def ingest_line(self, line: str) -> None:
        try:
            record = json.loads(line)
        except ValueError:
            return
        if not isinstance(record, dict):
            return
        request_id = record.get("request_id")
        user_agent = normalize_user_agent(record.get("user_agent"))
        if not isinstance(request_id, str) or request_id in ("", "-"):
            return
        if user_agent is None:
            return
        self._user_agents[request_id] = (user_agent, self._clock())
        self._user_agents.move_to_end(request_id)
        while len(self._user_agents) > self._max_entries:
            self._user_agents.popitem(last=False)

    def poll(self) -> None:
        """Read whatever has been appended since the last poll.

        On the first successful open the file is read from its end: lines
        written before the server started belong to reports it will never
        receive. After a rotation (the path now names a new file) the old
        file is drained first and the new one is read from its start.
        """
        try:
            stat = os.stat(self._path)
        except FileNotFoundError:
            return

        if self._file is None:
            self._open(stat.st_ino, from_start=False)
        elif stat.st_ino != self._inode:
            self._read_available()
            self._close()
            self._open(stat.st_ino, from_start=True)
        elif stat.st_size < self._file.tell():
            # Truncated in place.
            self._file.seek(0)
            self._partial = ""

        self._read_available()

    def expire(self) -> None:
        cutoff = self._clock() - self._retention
        while self._user_agents:
            _, (_, seen) = next(iter(self._user_agents.items()))
            if seen >= cutoff:
                break
            self._user_agents.popitem(last=False)

    def _open(self, inode: int, *, from_start: bool) -> None:
        self._file = open(self._path, "r", encoding="utf-8", errors="replace")
        self._inode = inode
        self._partial = ""
        if not from_start:
            self._file.seek(0, os.SEEK_END)

    def _close(self) -> None:
        if self._file is not None:
            self._file.close()
        self._file = None
        self._inode = None

    def _read_available(self) -> None:
        if self._file is None:
            return
        chunk = self._file.read(_MAX_READ_BYTES)
        if not chunk:
            return
        lines = (self._partial + chunk).split("\n")
        # The last element is an unterminated line still being written.
        self._partial = lines.pop()
        for line in lines:
            if line:
                self.ingest_line(line)

    # -- follower -----------------------------------------------------------

    async def run(self) -> None:
        logger.info(f"Reading gateway user agents from {self._path}")
        while True:
            try:
                self.poll()
                self.expire()
                resolved = self.take_resolved()
                if resolved:
                    await apply_user_agents(resolved)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Best effort: a user agent is display data, never worth
                # stopping the follower over.
                logger.exception("Failed to read the gateway access log")
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def apply_user_agents(user_agents: Dict[str, str]) -> None:
    """Fill ``user_agent`` on usage rows stored without one."""
    from sqlmodel import update

    from gpustack.schemas.model_usage_details import ModelUsageDetails
    from gpustack.server.db import async_session

    by_user_agent: Dict[str, list] = {}
    for request_id, user_agent in user_agents.items():
        by_user_agent.setdefault(user_agent, []).append(request_id)

    async with async_session() as session:
        for user_agent, request_ids in by_user_agent.items():
            await session.exec(
                update(ModelUsageDetails)
                .where(
                    ModelUsageDetails.request_id.in_(request_ids),
                    ModelUsageDetails.user_agent.is_(None),
                )
                .values(user_agent=user_agent)
            )
        await session.commit()


_instance: Optional[GatewayAccessLog] = None


def set_gateway_access_log(access_log: Optional[GatewayAccessLog]) -> None:
    global _instance
    _instance = access_log


def get_gateway_access_log() -> Optional[GatewayAccessLog]:
    """The running follower, or ``None`` outside ``embedded`` gateway mode."""
    return _instance
