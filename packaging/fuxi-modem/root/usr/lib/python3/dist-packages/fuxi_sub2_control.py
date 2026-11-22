"""One-shot SIM2 VOICE control. run() prints one JSON object on stdout.

Exit 0: known snapshot or accepted request. Exit 1: unavailable, blocked,
or modem rejection. Exit 2: invalid arguments. Exit 3: dispatched request
with uncertain outcome. Reconcile mutations with calls, never by replay.

run('wait-calls', seconds) is a blocking one-shot for an asynchronous caller.
The optional decimal timeout (1..60, default 20) starts after registration.
A matching indication returns ok/calls or calls_unknown for invalid metadata.
A quiet interval returns timeout (exit 0), without a snapshot.

For answer/end, a successful query missing call information can use a fresh
All Call Status on the same bound client. Registration and the subsequent
indication wait each have a two-second timeout.
"""

import json
import re
import sys
from contextlib import ExitStack
from time import monotonic

from fuxi_qmi_bind import bind_subscription


STATES = ('unknown', 'origination', 'incoming', 'conversation', 'cc-in-progress',
          'alerting', 'hold', 'waiting', 'disconnecting', 'end', 'setup')
DIRECTIONS = ('unknown', 'outgoing', 'incoming')
FALLBACK_SECONDS = 2
ENDABLE = {'origination', 'incoming', 'conversation', 'cc-in-progress', 'alerting',
           'hold', 'waiting', 'setup'}


def _snapshot(qmi, glib, wait, device, voice):
    cid = voice.get_cid()
    transaction = voice.get_next_transaction_id()
    request = qmi.message_new(qmi.Service.VOICE, cid, transaction, 0x2f)
    response = device.command_full_finish(wait(
        device.command_full, request, qmi.MessageContext.new(), 10, None)[1])
    if (not qmi.message_is_response(response) or qmi.message_get_service(response) != qmi.Service.VOICE
            or qmi.message_get_client_id(response) != cid or qmi.message_get_transaction_id(response) != transaction
            or qmi.message_get_message_id(response) != 0x2f):
        raise ValueError('Unexpected VOICE response')

    offset, length = qmi.message_tlv_read_init(response, 0x02)
    if length != 4:
        raise ValueError('Invalid VOICE result')
    valid, cursor, status = qmi.message_tlv_read_guint16(response, offset, 0, qmi.Endian.LITTLE)
    if not valid or status:
        raise ValueError('Failed VOICE query')
    valid, _, error = qmi.message_tlv_read_guint16(response, offset, cursor, qmi.Endian.LITTLE)
    if not valid or error:
        raise ValueError('Failed VOICE query')

    try:
        offset, length = qmi.message_tlv_read_init(response, 0x10)
    except glib.Error as exc:
        if exc.matches(qmi.core_error_quark(), qmi.CoreError.TLVNOTFOUND):
            return None
        raise

    def byte_at(index):
        valid, _, value = qmi.message_tlv_read_guint8(response, offset, index)
        if not valid:
            raise ValueError('Invalid call information')
        return value

    return _call_records(length, byte_at)


def _call_records(length, byte_at):
    if length < 1 or length != 1 + 7 * byte_at(0):
        raise ValueError('Invalid call information length')
    calls = {}
    for start in range(1, length, 7):
        call_id, state, direction = byte_at(start), byte_at(start + 1), byte_at(start + 3)
        if call_id in calls:
            raise ValueError('Ambiguous call ID')
        calls[call_id] = {'id': call_id, 'state': STATES[state] if state < len(STATES) else 'unknown',
                          'direction': DIRECTIONS[direction] if direction < len(DIRECTIONS) else 'unknown'}
    return calls


def _indication_calls(frame):
    if (len(frame) < 13 or int.from_bytes(frame[1:3], 'little') != len(frame) - 1
            or int.from_bytes(frame[11:13], 'little') != len(frame) - 13):
        raise ValueError('Invalid indication length')
    offset = 13
    calls = None
    while offset < len(frame):
        if offset + 3 > len(frame):
            raise ValueError('Truncated TLV header')
        tag = frame[offset]
        length = int.from_bytes(frame[offset + 1:offset + 3], 'little')
        offset += 3
        if offset + length > len(frame):
            raise ValueError('Truncated TLV')
        if tag == 0x01:
            if calls is not None:
                raise ValueError('Duplicate call information')
            value = memoryview(frame)[offset:offset + length]
            calls = _call_records(length, value.__getitem__)
        offset += length
    if calls is None:
        raise ValueError('Missing call information')
    return calls


def _observe_calls(resources, qmi, device, voice, receive):
    cid = voice.get_cid()
    if voice.get_device() != device or voice.get_service() != qmi.Service.VOICE or not 0 < cid < 255:
        raise ValueError('Expected an allocated VOICE client')
    header = bytes((int(qmi.Service.VOICE), cid, 0x04))

    def on_indication(sender, frame):
        if (sender != device or len(frame) < 11 or frame[0] != 1
                or frame[4:7] != header or frame[9:11] != b'\x2e\x00'):
            return
        try:
            calls = _indication_calls(frame)
        except ValueError:
            calls = None
        receive(calls)

    handler = device.connect('indication', on_indication)
    resources.callback(device.disconnect, handler)


def _register_calls(qmi, wait, voice, seconds):
    request = qmi.MessageVoiceIndicationRegisterInput.new()
    request.set_call_notification_events(True)
    voice.indication_register_finish(wait(voice.indication_register, request, seconds, None)[1]).get_result()


def _await_indication(glib, context, seconds, ready):
    if ready():
        return
    expired = False

    def on_timeout(*args):
        nonlocal expired
        expired = True
        return glib.SOURCE_REMOVE

    timer = glib.timeout_source_new(seconds * 1000)
    try:
        timer.set_callback(on_timeout)
        timer.attach(context)
        while not expired and not ready():
            context.iteration(True)
    finally:
        timer.destroy()


def _wait_calls(qmi, glib, context, wait, device, voice, seconds, reply):
    event = {}

    def receive(calls):
        if not event:
            if calls is None:
                event.update(result='calls_unknown')
            else:
                event.update(result='ok', calls=list(calls.values()))

    with ExitStack() as resources:
        _observe_calls(resources, qmi, device, voice, receive)
        bind_subscription(qmi, wait, device, voice, 0x44, 1)
        _register_calls(qmi, wait, voice, 10)
        _await_indication(glib, context, seconds, lambda: bool(event))
        reply.update(event or {'result': 'timeout'})


def _control_snapshot(qmi, glib, context, wait, device, voice):
    latest = None
    received_at = None

    def receive(calls):
        nonlocal latest, received_at
        latest, received_at = calls, monotonic()

    def fresh():
        return received_at is not None and monotonic() - received_at < FALLBACK_SECONDS

    with ExitStack() as resources:
        _observe_calls(resources, qmi, device, voice, receive)
        calls = _snapshot(qmi, glib, wait, device, voice)
        if calls is not None:
            return calls
        if not fresh():
            _register_calls(qmi, wait, voice, FALLBACK_SECONDS)
            _await_indication(glib, context, FALLBACK_SECONDS, fresh)
        return latest if fresh() else None


def _operate(qmi, glib, context, wait, device, voice, action, argument, reply):
    if action != 'dial':
        reply['result'] = 'calls_unknown'
        if action == 'calls':
            calls = _snapshot(qmi, glib, wait, device, voice)
            if calls is not None:
                reply.update(result='ok', calls=list(calls.values()))
            else:
                from fuxi_sub2_call_cache import read_snapshot
                observation = read_snapshot()
                if observation is not None:
                    reply.update(result='ok', calls=observation['calls'], source='indication',
                                 observed_boottime_ns=observation['observed_boottime_ns'])
            return
        calls = _control_snapshot(qmi, glib, context, wait, device, voice)
        if calls is None:
            return
        call_id = int(argument)
        selected = calls.get(call_id)
        if selected is None:
            reply.update(result='blocked', reason='id_absent')
            return
        if (selected['direction'] == 'unknown' or selected['state'] not in ENDABLE or
                (action == 'answer' and (selected['state'] not in ('incoming', 'waiting') or
                                         selected['direction'] != 'incoming'))):
            reply.update(result='blocked', reason='ineligible_call')
            return

    reply['result'] = 'unavailable'
    if action == 'dial':
        request = qmi.MessageVoiceDialCallInput.new()
        request.set_calling_number(argument)
    else:
        input_type = qmi.MessageVoiceAnswerCallInput if action == 'answer' else qmi.MessageVoiceEndCallInput
        request = input_type.new()
        request.set_call_id(call_id)

    start = getattr(voice, action + '_call')
    finish = getattr(voice, action + '_call_finish')
    reply['result'] = 'result_unknown'
    output = finish(wait(start, request, 30, None)[1])
    try:
        output.get_result()
    except glib.Error as exc:
        if exc.domain == glib.quark_to_string(qmi.protocol_error_quark()):
            reply['result'] = 'rejected'
            return
        raise
    reply.update(result='accepted', call_id=None if action == 'dial' else call_id)
    if action == 'dial':
        reply['call_id'] = output.get_call_id()


def _execute(action, argument, reply):
    import gi
    gi.require_version('Qmi', '1.0')
    gi.require_version('Qrtr', '1.0')
    from gi.repository import GLib, Qmi, Qrtr

    context = GLib.MainContext.new()

    def wait(start, *args):
        completed = []
        start(*args, lambda source, result: completed.append((source, result)))
        while not completed:
            context.iteration(True)
        return completed[0]

    def cleanup(start, finish, *args):
        try:
            if not finish(wait(start, *args, 10, None)[1]):
                reply['cleanup_failed'] = True
        except Exception:
            reply['cleanup_failed'] = True

    with ExitStack() as resources:
        context.push_thread_default()
        resources.callback(context.pop_thread_default)
        bus = Qrtr.Bus.new_finish(wait(Qrtr.Bus.new, 1000, None)[1])
        node = bus.peek_node(0)
        if node is None:
            return
        device = Qmi.Device.new_from_node_finish(wait(Qmi.Device.new_from_node, node, None)[1])
        resources.callback(cleanup, device.close_async, device.close_finish)
        if not device.open_finish(wait(device.open, Qmi.DeviceOpenFlags.PROXY, 10, None)[1]):
            return
        voice = device.allocate_client_finish(wait(device.allocate_client, Qmi.Service.VOICE, Qmi.CID_NONE, 10, None)[1])
        resources.callback(cleanup, device.release_client, device.release_client_finish,
                           voice, Qmi.DeviceReleaseClientFlags.RELEASE_CID)
        if action == 'wait-calls':
            _wait_calls(Qmi, GLib, context, wait, device, voice, int(argument or '20'), reply)
            return
        bind_subscription(Qmi, wait, device, voice, 0x44, 1)
        _operate(Qmi, GLib, context, wait, device, voice, action, argument, reply)


def run(action, argument=None):
    known = action in ('calls', 'wait-calls', 'dial', 'answer', 'end')
    reply = {'version': 1, 'slot': 2, 'action': action if known else None, 'result': 'invalid_argument'}
    valid = ((action in ('calls', 'wait-calls') and argument is None) or
             (isinstance(argument, str) and
              ((action == 'dial' and re.fullmatch(r'\+?[0-9]{3,15}', argument)) or
               (action == 'wait-calls' and re.fullmatch(r'[1-9][0-9]?', argument) and int(argument) <= 60) or
               (action in ('answer', 'end') and re.fullmatch(r'(?:0|[1-9][0-9]{0,2})', argument)
                and int(argument) <= 255))))
    if valid:
        reply['result'] = 'unavailable'
        try:
            _execute(action, argument, reply)
        except Exception:
            pass
    print(json.dumps(reply))
    return {'ok': 0, 'timeout': 0, 'accepted': 0, 'invalid_argument': 2, 'result_unknown': 3}.get(reply['result'], 1)


if __name__ == '__main__':
    sys.exit(run(*sys.argv[1:]) if 1 <= len(sys.argv) - 1 <= 2 else run(None))
