from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import json
from math import isfinite
import os
import signal
import socket
import struct
from time import CLOCK_BOOTTIME, clock_gettime

from fuxi_qmi_bind import bind_subscription


COLLECTOR_SOCKET = '/run/fuxi-voice-collector/calls.sock'
MAX_AGE = 5
MAX_PACKET = 32768
REFRESH_SECONDS = 2
MAX_WAITERS = 8


def _boottime():
    return clock_gettime(CLOCK_BOOTTIME)


class IncompleteVoiceCalls(RuntimeError):
    def __init__(self, snapshot):
        self.snapshot = snapshot
        unknown = ', '.join(str(slot) for slot, calls in snapshot.items() if calls is None)
        super().__init__(f'Unknown VOICE subscription(s): {unknown}')


def _call_records(length, byte_at):
    if length < 1 or length != 1 + 7 * byte_at(0):
        raise ValueError('Invalid VOICE call information length')
    records = [tuple(byte_at(start + field) for field in (0, 1, 2, 4, 5))
               for start in range(1, length, 7)]
    if len({record[0] for record in records}) != len(records):
        raise ValueError('Duplicate VOICE call ID')
    return records


def _check_result(qmi, response, evidence):
    offset, length = qmi.message_tlv_read_init(response, 0x02)
    if length != 4:
        raise ValueError("Invalid VOICE result length")
    valid, cursor, status = qmi.message_tlv_read_guint16(response, offset, 0, qmi.Endian.LITTLE)
    if not valid:
        raise ValueError("Invalid VOICE result status")
    valid, _, error = qmi.message_tlv_read_guint16(response, offset, cursor, qmi.Endian.LITTLE)
    if valid:
        evidence['result'] = [status, error]
    if not valid or status or error:
        raise RuntimeError(f"VOICE request failed (status={status}, error={error})")


def _call_metadata(qmi, response, evidence):
    from gi.repository import GLib

    _check_result(qmi, response, evidence)

    try:
        offset, length = qmi.message_tlv_read_init(response, 0x10)
    except GLib.Error as exc:
        if exc.matches(qmi.core_error_quark(), qmi.CoreError.TLVNOTFOUND):
            evidence['tlv_0x10'] = 'absent'
        raise
    evidence['tlv_0x10_length'] = length

    def byte_at(index):
        valid, _, value = qmi.message_tlv_read_guint8(response, offset, index)
        if not valid:
            raise ValueError("Invalid VOICE call information")
        return int(value)

    calls = _call_records(length, byte_at)
    evidence['count'] = len(calls)
    return calls


def _indicated_calls(frame):
    if (len(frame) < 13 or int.from_bytes(frame[1:3], 'little') != len(frame) - 1
            or int.from_bytes(frame[11:13], 'little') != len(frame) - 13):
        raise ValueError('Invalid VOICE indication length')
    offset = 13
    calls = None
    while offset < len(frame):
        if offset + 3 > len(frame):
            raise ValueError('Truncated VOICE TLV header')
        tag = frame[offset]
        length = int.from_bytes(frame[offset + 1:offset + 3], 'little')
        offset += 3
        if offset + length > len(frame):
            raise ValueError('Truncated VOICE TLV value')
        if tag == 0x01:
            if calls is not None:
                raise ValueError('Duplicate VOICE call information')
            value = memoryview(frame)[offset:offset + length]
            calls = _call_records(length, value.__getitem__)
        offset += length
    if calls is None:
        raise ValueError('Missing VOICE call information')
    return calls


@dataclass(eq=False)
class _VoiceBinding:
    device: object
    cid: int
    handler: int = 0
    received: float = 0.0
    calls: list | None = None
    revision: int = 0


class VoiceIndicationSource:
    """Observe bound VOICE clients on their owning GLib main context.

    Call bind after successful subscription binding, and unbind before a
    rebind attempt, client release or transport teardown. The caller owns the
    clients and modem indication registration.
    """

    def __init__(self, max_age, *, clock=_boottime):
        if not isfinite(max_age) or max_age <= 0:
            raise ValueError('max_age must be finite and positive (seconds)')
        self._max_age = max_age
        self._clock = clock
        self._bindings = {}

    def bind(self, slot, client):
        """Start a new observation generation for an already bound client."""
        if slot not in (0, 1):
            raise ValueError('Invalid VOICE slot')
        self.unbind(slot)
        cid = client.get_cid()
        device = client.get_device()
        if client.get_service() != 0x09 or not 0 < cid < 255 or device is None:
            raise ValueError('Expected allocated VOICE client')
        for other, previous in list(self._bindings.items()):
            if previous.device == device and previous.cid == cid:
                self.unbind(other)
        binding = _VoiceBinding(device, cid)

        def on_indication(sender, frame):
            if (self._bindings.get(slot) is not binding or sender != device or len(frame) < 11
                    or frame[0] != 1 or frame[4:7] != bytes((0x09, cid, 0x04))
                    or frame[9:11] != b'\x2e\x00'):
                return
            binding.revision += 1
            try:
                binding.calls = _indicated_calls(frame)
            except ValueError:
                binding.calls = None
            else:
                binding.received = self._clock()

        binding.handler = device.connect('indication', on_indication)
        self._bindings[slot] = binding

    def unbind(self, slot):
        """Forget this slot immediately and disconnect its indication handler."""
        binding = self._bindings.pop(slot, None)
        if binding is not None:
            binding.device.disconnect(binding.handler)

    def begin_readback(self, slot):
        """Capture the binding and indication revision before starting a read."""
        binding = self._bindings[slot]
        revision = binding.revision
        started = self._clock()

        def apply(calls):
            if self._bindings.get(slot) is binding and binding.revision == revision:
                binding.calls = calls
                binding.received = started

        return apply

    def readback(self, slot, query):
        apply = self.begin_readback(slot)
        calls = query()
        if calls is not None:
            apply(calls)

    def observations(self):
        now = self._clock()
        calls = {0: None, 1: None}
        for slot, binding in self._bindings.items():
            if binding.calls is not None and 0 <= now - binding.received < self._max_age:
                calls[slot] = (binding.received, binding.calls.copy())
        return calls

    def snapshot(self):
        """Return select_subscription input, or raise with a partial snapshot."""
        calls = {slot: None if item is None else item[1] for slot, item in self.observations().items()}
        if None in calls.values():
            raise IncompleteVoiceCalls(calls)
        return calls


def _command(qmi, device, wait, request):
    response = device.command_full_finish(wait(
        device.command_full, request, qmi.MessageContext.new(), 10, None)[1])
    return _checked_response(qmi, request, response)


def _checked_response(qmi, request, response):
    header = (qmi.message_get_service, qmi.message_get_client_id, qmi.message_get_transaction_id,
              qmi.message_get_message_id)
    if not qmi.message_is_response(response) or any(field(response) != field(request) for field in header):
        raise ValueError('Unexpected VOICE response header')
    return response


def _read_calls(qmi, device, wait, client, evidence):
    cid, txn = client.get_cid(), client.get_next_transaction_id()
    evidence.update(bound=True, cid=cid, txn=txn)
    request = qmi.message_new(qmi.Service.VOICE, cid, txn, 0x2f)
    response = _command(qmi, device, wait, request)
    evidence['response_service_cid_txn_id'] = [int(qmi.Service.VOICE), cid, txn, 0x2f]
    return _call_metadata(qmi, response, evidence)


def _release(qmi, device, wait, client):
    if not device.release_client_finish(wait(device.release_client, client,
            qmi.DeviceReleaseClientFlags.RELEASE_CID, 10, None)[1]):
        raise RuntimeError('VOICE CID release failed')


def _query_slot(qmi, device, wait, slot, evidence):
    evidence['stage'] = 'allocate'
    client = device.allocate_client_finish(wait(
        device.allocate_client, qmi.Service.VOICE, qmi.CID_NONE, 10, None)[1])
    try:
        evidence['stage'] = 'bind'
        bind_subscription(qmi, wait, device, client, 0x44, slot)
        evidence['stage'] = 'query'
        return _read_calls(qmi, device, wait, client, evidence)
    finally:
        _release(qmi, device, wait, client)


@contextmanager
def _transport(context=None):
    import gi
    gi.require_version("Qmi", "1.0")
    gi.require_version("Qrtr", "1.0")
    from gi.repository import GLib, Qmi, Qrtr

    if context is None:
        context = GLib.MainContext.new()

    def wait(start, *args):
        completed = []
        start(*args, lambda source, result: completed.append((source, result)))
        while not completed:
            context.iteration(True)
        return completed[0]

    context.push_thread_default()
    try:
        bus = Qrtr.Bus.new_finish(wait(Qrtr.Bus.new, 1000, None)[1])
        node = bus.peek_node(0)
        if node is None:
            raise RuntimeError("QRTR node 0 unavailable")
        device = Qmi.Device.new_from_node_finish(wait(Qmi.Device.new_from_node, node, None)[1])
        try:
            if not device.open_finish(wait(device.open, Qmi.DeviceOpenFlags.PROXY, 10, None)[1]):
                raise RuntimeError("QMI device open failed")
            yield Qmi, GLib, device, wait
        finally:
            if not device.close_finish(wait(device.close_async, 10, None)[1]):
                raise RuntimeError("QMI device close failed")
    finally:
        context.pop_thread_default()


def _collect_voice_calls(evidence):
    calls = {}
    with _transport() as (qmi, glib, device, wait):
        for slot in (0, 1):
            entry = evidence[slot] = {'state': 'unknown'}
            try:
                calls[slot] = _query_slot(qmi, device, wait, slot, entry)
                entry['state'] = 'known'
            except (glib.Error, RuntimeError, ValueError) as exc:
                entry['error'] = type(exc).__name__
                calls[slot] = None
        if None in calls.values():
            raise IncompleteVoiceCalls(calls)
        return calls


def collect_voice_calls() -> dict[int, list[tuple[int, int, int, int, int]]]:
    """Read (call_id, state, type, mode, multipart_indicator) or raise on incomplete data."""
    return _collect_voice_calls({})


def _observe_dual(resources, qmi, glib, device, wait, source):
    cids = set()
    clients = {}
    for slot in (0, 1):
        client = device.allocate_client_finish(wait(
            device.allocate_client, qmi.Service.VOICE, qmi.CID_NONE, 10, None)[1])
        cid = client.get_cid()
        if cid in cids:
            raise ValueError('Duplicate VOICE CID')
        resources.callback(_release, qmi, device, wait, client)
        if client.get_device() != device or client.get_service() != qmi.Service.VOICE or not 0 < cid < 255:
            raise ValueError('Unexpected VOICE client')
        cids.add(cid)
        request = qmi.message_new(qmi.Service.VOICE, cid, client.get_next_transaction_id(), 0x44)
        offset = qmi.message_tlv_write_init(request, 1)
        qmi.message_tlv_write_guint8(request, slot)
        qmi.message_tlv_write_complete(request, offset)
        _check_result(qmi, _command(qmi, device, wait, request), {})
        source.bind(slot, client)
        resources.callback(source.unbind, slot)
        registration = qmi.MessageVoiceIndicationRegisterInput.new()
        registration.set_call_notification_events(True)
        result = client.indication_register_finish(wait(
            client.indication_register, registration, 10, None)[1])
        result.get_result()

        def read(client=client):
            evidence = {}
            try:
                return _read_calls(qmi, device, wait, client, evidence)
            except glib.Error:
                if evidence.get('tlv_0x10') == 'absent':
                    return None
                raise

        source.readback(slot, read)
        clients[slot] = client
    return clients


def _read_async(qmi, glib, device, client, cancel, done):
    request = qmi.message_new(qmi.Service.VOICE, client.get_cid(), client.get_next_transaction_id(), 0x2f)

    def complete(sender, result):
        calls = None
        try:
            response = sender.command_full_finish(result)
            if sender == device and not cancel.is_cancelled():
                calls = _call_metadata(qmi, _checked_response(qmi, request, response), {})
        except (glib.Error, ValueError, RuntimeError):
            pass
        done(calls)

    device.command_full(request, qmi.MessageContext.new(), REFRESH_SECONDS, cancel, complete)


class _DemandRefresh:
    """One batch on the collector's default GLib context, with bounded waiters and deadline."""

    def __init__(self, source, read, glib):
        self.source = source
        self.read = read
        self.glib = glib
        self.waiters = []
        self.cancel = None
        self.timer = None
        self.last_attempt = {}
        self.closed = False

    def reply(self, connection):
        with connection:
            packet = {'version': 1, 'slots': self.source.observations()}
            try:
                connection.send(json.dumps(packet, separators=(',', ':')).encode('ascii'))
            except OSError:
                pass

    def accept(self, connection):
        from gi.repository import Gio

        if self.closed or len(self.waiters) >= MAX_WAITERS:
            connection.close()
            return
        if self.cancel is not None:
            self.waiters.append(connection)
            return
        observations = self.source.observations()
        now = _boottime()
        if all(item is not None for item in observations.values()):
            self.reply(connection)
            return
        pending = {slot for slot, item in observations.items()
                   if (item is None or now - item[0] >= MAX_AGE - REFRESH_SECONDS)
                   and now - self.last_attempt.get(slot, -REFRESH_SECONDS) >= REFRESH_SECONDS}
        if not pending:
            self.reply(connection)
            return
        self.waiters.append(connection)
        cancel = self.cancel = Gio.Cancellable.new()

        def expire():
            self.timer = None
            self.finish()
            return self.glib.SOURCE_REMOVE

        self.timer = self.glib.timeout_add(REFRESH_SECONDS * 1000, expire)
        for slot in tuple(pending):
            self.last_attempt[slot] = now
            apply = self.source.begin_readback(slot)

            def done(calls, slot=slot, apply=apply):
                if self.cancel is cancel and slot in pending:
                    if _boottime() - now >= REFRESH_SECONDS:
                        self.finish()
                        return
                    apply(calls)
                    pending.remove(slot)
                    if not pending:
                        self.finish()

            try:
                self.read(slot, cancel, done)
            except (self.glib.Error, RuntimeError, ValueError):
                done(None)

    def finish(self):
        cancel, self.cancel = self.cancel, None
        if cancel is not None:
            cancel.cancel()
        if self.timer is not None:
            self.glib.source_remove(self.timer)
            self.timer = None
        for connection in self.waiters:
            if self.closed:
                connection.close()
            else:
                self.reply(connection)
        self.waiters.clear()

    def close(self):
        self.closed = True
        self.finish()


def _serve(resources, glib, unix, refresh, path):
    endpoint = resources.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET))
    endpoint.setblocking(False)
    endpoint.bind(path)
    resources.callback(os.unlink, path)
    os.chmod(path, 0o600)
    endpoint.listen(8)

    def serve_one(*_):
        try:
            connection, _ = endpoint.accept()
            connection.setblocking(False)
            refresh.accept(connection)
        except OSError:
            pass
        return glib.SOURCE_CONTINUE

    handler = unix.fd_add_full(glib.PRIORITY_DEFAULT, endpoint.fileno(), glib.IOCondition.IN, serve_one)
    resources.callback(glib.source_remove, handler)


def _decode_observations(packet):
    data = json.loads(packet)
    if (type(data) is not dict or set(data) != {'version', 'slots'} or type(data['version']) is not int
            or data['version'] != 1 or type(data['slots']) is not dict or set(data['slots']) != {'0', '1'}):
        raise ValueError('Invalid VOICE snapshot')
    now = _boottime()
    calls = {0: None, 1: None}
    for slot in calls:
        item = data['slots'][str(slot)]
        if item is None:
            continue
        if type(item) is not list or len(item) != 2:
            raise ValueError('Invalid observation')
        stamp, records = item
        if type(stamp) not in (int, float) or stamp < 0 or not 0 <= now - stamp < MAX_AGE:
            continue
        if type(records) is not list or len(records) > 255:
            raise ValueError('Invalid call list')
        if any(type(record) is not list or len(record) != 5 or
               any(type(field) is not int or not 0 <= field <= 255 for field in record) for record in records):
            raise ValueError('Invalid call metadata')
        if len({record[0] for record in records}) != len(records):
            raise ValueError('Duplicate call ID')
        calls[slot] = [tuple(record) for record in records]
    return calls


def read_indicated_voice_calls():
    """Read both slots for select_subscription, raising IncompleteVoiceCalls on uncertainty."""
    calls = {0: None, 1: None}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
            connection.settimeout(REFRESH_SECONDS + 1)
            connection.connect(COLLECTOR_SOCKET)
            creds = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
            if struct.unpack('3i', creds)[1] == 0:
                packet, _, flags, _ = connection.recvmsg(MAX_PACKET)
                if not flags & socket.MSG_TRUNC:
                    calls = _decode_observations(packet)
    except (OSError, ValueError, RecursionError, OverflowError):
        pass
    if None in calls.values():
        raise IncompleteVoiceCalls(calls)
    return calls


def _notify_ready():
    address = os.environ.get('NOTIFY_SOCKET')
    if address is None:
        return
    if address.startswith('@'):
        address = '\0' + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
        notifier.setblocking(False)
        notifier.sendto(b'READY=1', address)


def run_collector():
    import gi
    gi.require_version('GLibUnix', '2.0')
    from gi.repository import GLib, GLibUnix

    loop = GLib.MainLoop()
    stopped = False
    removed = False
    refresh = None

    def stop(*_):
        nonlocal stopped
        stopped = True
        if refresh is not None:
            refresh.close()
        loop.quit()
        return GLib.SOURCE_CONTINUE

    def device_removed(*_):
        nonlocal removed
        removed = True
        stop()

    with ExitStack() as resources:
        signal_add = getattr(GLibUnix, 'signal_add', None) or GLib.unix_signal_add
        for sig in (signal.SIGTERM, signal.SIGINT):
            resources.callback(GLib.source_remove, signal_add(GLib.PRIORITY_DEFAULT, sig, stop))
        qmi, glib, device, wait = resources.enter_context(_transport(loop.get_context()))
        resources.callback(device.disconnect, device.connect('device-removed', device_removed))
        source = VoiceIndicationSource(MAX_AGE)
        clients = _observe_dual(resources, qmi, glib, device, wait, source)
        if not stopped:
            def read(slot, cancel, done):
                _read_async(qmi, glib, device, clients[slot], cancel, done)

            refresh = _DemandRefresh(source, read, glib)
            resources.callback(refresh.close)
            _serve(resources, glib, GLibUnix, refresh, COLLECTOR_SOCKET)
            _notify_ready()
            loop.run()
    return int(removed)


if __name__ == '__main__':
    import json

    evidence = {}
    try:
        _collect_voice_calls(evidence)
    except IncompleteVoiceCalls:
        raise SystemExit(1)
    finally:
        print(json.dumps(evidence, sort_keys=True))
