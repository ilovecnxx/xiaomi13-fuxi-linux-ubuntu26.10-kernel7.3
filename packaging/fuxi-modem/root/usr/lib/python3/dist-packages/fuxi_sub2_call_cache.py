"""Passive SIM2 call discovery. Mutations require a fresh, independently bound VOICE check."""

import json
import os
import re
import socket
import struct
import time

SOCKET_PATH = '/run/fuxi-sub2-msg/calls.sock'
MAX_AGE_NS = 30_000_000_000
MAX_PACKET = 32768


def _now():
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME)


def _valid_generation(generation):
    return isinstance(generation, str) and re.fullmatch('[0-9a-f]{32}', generation) is not None


def _valid_calls(calls):
    from fuxi_sub2_control import DIRECTIONS, STATES

    if not isinstance(calls, list) or len(calls) > 255:
        return False
    ids = set()
    for call in calls:
        if not isinstance(call, dict) or set(call) != {'id', 'state', 'direction'}:
            return False
        call_id = call['id']
        if type(call_id) is not int or not 0 <= call_id <= 255 or call_id in ids:
            return False
        if call['state'] not in STATES or call['direction'] not in DIRECTIONS:
            return False
        ids.add(call_id)
    return True


class CallCache:
    def __init__(self, generation):
        if not _valid_generation(generation):
            raise ValueError('invalid listener generation')
        self.generation = generation
        self.latest = None

    def observe(self, calls):
        self.latest = None
        if calls is None:
            return
        records = list(calls.values())
        if _valid_calls(records):
            self.latest = dict(version=1, slot=2, generation=self.generation,
                               observed_boottime_ns=_now(), calls=[dict(record) for record in records])

    def snapshot(self):
        if self.latest and not 0 <= _now() - self.latest['observed_boottime_ns'] < MAX_AGE_NS:
            self.latest = None
        return self.latest


def serve(resources, unix, glib, cache, path=SOCKET_PATH):
    endpoint = resources.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET))
    endpoint.setblocking(False)
    endpoint.bind(path)
    resources.callback(os.unlink, path)
    os.chmod(path, 0o600)
    endpoint.listen(8)

    def ready(*args):
        try:
            connection, _ = endpoint.accept()
            with connection:
                connection.setblocking(False)
                connection.send(json.dumps(cache.snapshot(), separators=(',', ':')).encode('ascii'))
        except OSError:
            pass
        return glib.SOURCE_CONTINUE

    source = unix.fd_add_full(glib.PRIORITY_DEFAULT, endpoint.fileno(), glib.IOCondition.IN, ready)
    resources.callback(glib.source_remove, source)


def read_snapshot(expected_generation=None):
    """Return a fresh discovery snapshot, or None for unknown/unavailable. An empty calls list is known."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
            connection.settimeout(0.5)
            connection.connect(SOCKET_PATH)
            creds = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
            if struct.unpack('3i', creds)[1] != 0:
                return None
            packet, _, flags, _ = connection.recvmsg(MAX_PACKET)
        if flags & socket.MSG_TRUNC:
            return None
        record = json.loads(packet)
        if not isinstance(record, dict) or set(record) != {'version', 'slot', 'generation', 'observed_boottime_ns', 'calls'}:
            return None
        if (type(record['version']) is not int or record['version'] != 1
                or type(record['slot']) is not int or record['slot'] != 2
                or not _valid_generation(record['generation'])
                or expected_generation is not None and record['generation'] != expected_generation):
            return None
        stamp = record['observed_boottime_ns']
        if type(stamp) is int and stamp >= 0 and 0 <= _now() - stamp < MAX_AGE_NS and _valid_calls(record['calls']):
            return record
    except (OSError, ValueError, RecursionError):
        pass
    return None
