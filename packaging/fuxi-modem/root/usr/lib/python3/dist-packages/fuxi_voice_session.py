"""Select and coordinate a single VoLTE session across both subscriptions."""

from threading import Lock


SUBSCRIPTION_VSIDS = {0: 0x11c05000, 1: 0x11dc5000}
CONVERSATION, END = 3, 9
VOICE_IP, LTE = 2, 4
SWITCH_RETAINED_SUBGRAPHS = frozenset((0xb000003f, 0xb0000041, 0xb0000040))


def select_subscription(snapshot):
    if snapshot.keys() != SUBSCRIPTION_VSIDS.keys() or any(calls is None for calls in snapshot.values()):
        raise ValueError('incomplete subscription snapshot')
    live = [(subscription, call) for subscription, calls in snapshot.items()
            for call in calls if call[1] != END]
    if not live:
        return None
    if len(live) != 1:
        raise ValueError('multiple live calls')
    subscription, (_, state, call_type, mode, multipart) = live[0]
    if (state, call_type, mode, multipart) != (CONVERSATION, VOICE_IP, LTE, 0):
        raise ValueError('call is outside single connected VoLTE scope')
    return subscription


class CommandRejected(RuntimeError):
    """A command definitely had no effect, including a confirmed DSP rejection."""


class VoiceSession:
    """Coordinate one transport owner. Each execute must return only on confirmed success.

    Any execute exception other than CommandRejected leaves the command unresolved.
    The transport must retain the graph, command token, callbacks and DMA and match
    the pending reply before calling resolve. Tickets are local to this instance.
    """

    _direction = ('open', 'set-cfg', 'cal-alloc', 'cal-map', 'register', 'media',
                  'interface', 'control', 'start')
    _peer = ('open', 'set-cfg', 'media', 'interface', 'prepare', 'start')
    _acquires = {'open': 'close', 'start': 'stop', 'cal-alloc': 'unmap', 'register': 'deregister'}
    _cleanup = (('rx', 'stop'), ('tx', 'stop'), ('peer', 'stop'), ('peer', 'close'),
                ('tx', 'deregister'), ('tx', 'unmap'), ('tx', 'close'),
                ('rx', 'deregister'), ('rx', 'unmap'), ('rx', 'close'))

    def __init__(self, execute):
        self.execute = execute
        self.lock = Lock()
        self.call = None
        self.pending = None
        self.ticket = 0
        self.held = set()
        self.active = False

    def _confirmed(self, graph, command):
        if command in self._acquires:
            self.held.add((graph, self._acquires[command]))
        else:
            self.held.discard((graph, command))

    def _send(self, graph, command):
        self.ticket += 1
        self.pending = (self.ticket, graph, command)
        try:
            self.execute(self.ticket, graph, command, self.call[0])
        except CommandRejected:
            self.pending = None
            raise
        self._confirmed(graph, command)
        self.pending = None

    def resolve(self, ticket, applied):
        with self.lock:
            if type(applied) is not bool or self.pending is None or ticket != self.pending[0]:
                raise ValueError('unexpected command resolution')
            if applied:
                self._confirmed(*self.pending[1:])
            self.pending = None

    def _stop(self):
        self.active = False
        if self.pending is not None:
            raise RuntimeError('awaiting command resolution')
        for graph, command in self._cleanup:
            if (graph, command) in self.held:
                self._send(graph, command)
        self.call = None

    def stop(self):
        with self.lock:
            self._stop()

    @staticmethod
    def _eligible(snapshot, active_subgraphs):
        calls = snapshot()
        slot = select_subscription(calls)
        if slot is None:
            return None
        if not SWITCH_RETAINED_SUBGRAPHS.issubset(active_subgraphs()):
            raise ValueError('external voice routes unavailable')
        call_id, = [call[0] for call in calls[slot] if call[1] != END]
        return slot, call_id

    def sync(self, snapshot, active_subgraphs):
        """Read current calls and confirmed running subgraphs for the archived switch route."""
        with self.lock:
            if self.pending is not None:
                raise RuntimeError('awaiting command resolution')
            try:
                call = self._eligible(snapshot, active_subgraphs)
            except Exception:
                self._stop()
                raise
            if self.active and self.call == call:
                return
            self._stop()
            if call is None:
                return
            self.call = call
            try:
                for graph, commands in (('tx', self._direction), ('peer', self._peer),
                                        ('rx', (*self._direction, 'cal-keys'))):
                    for command in commands:
                        if self._eligible(snapshot, active_subgraphs) != call:
                            raise ValueError('call changed during voice setup')
                        self._send(graph, command)
                if self._eligible(snapshot, active_subgraphs) != call:
                    raise ValueError('call changed during voice setup')
                self.active = True
            except Exception:
                if self.pending is None:
                    self._stop()
                raise


def current_voice_calls():
    from fuxi_voice_calls import IncompleteVoiceCalls, collect_voice_calls, read_indicated_voice_calls

    try:
        return read_indicated_voice_calls()
    except IncompleteVoiceCalls:
        return collect_voice_calls()


def main():
    from fuxi_voice_calls import IncompleteVoiceCalls

    try:
        subscription = select_subscription(current_voice_calls())
    except (IncompleteVoiceCalls, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    if subscription is None:
        print('idle')
    else:
        print(f'SIM{subscription + 1} VSID=0x{SUBSCRIPTION_VSIDS[subscription]:08x}')


if __name__ == '__main__':
    main()
