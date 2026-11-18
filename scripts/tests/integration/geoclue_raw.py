#!/usr/bin/python3
"""Exercise the built GeoClue daemon on a private bus with synthetic RAW fixes.

Requires python3-gi and dbus-daemon. Configure the GeoClue build's sysconfdir
as a nonexistent directory below the build directory's parent, then pass the
Meson build directory. No hardware or desktop services are used.
"""

import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import time

from gi.repository import Gio, GLib


MM = "org.freedesktop.ModemManager1"
MODEM = MM + ".Modem"
LOCATION = MODEM + ".Location"
MODEM_PATH = "/org/freedesktop/ModemManager1/Modem/0"
GC = "org.freedesktop.GeoClue2"
GC_PATH = "/org/freedesktop/GeoClue2"
PROPS = "org.freedesktop.DBus.Properties"
OBJECTS = "org.freedesktop.DBus.ObjectManager"
RAW, CELL = 2, 1

XML = f"""<node>
<interface name='{OBJECTS}'>
  <method name='GetManagedObjects'><arg type='a{{oa{{sa{{sv}}}}}}' direction='out'/></method>
</interface>
<interface name='{MODEM}'>
  <property name='State' type='i' access='read'/>
</interface>
<interface name='{LOCATION}'>
  <method name='Setup'><arg type='u' direction='in'/><arg type='b' direction='in'/></method>
  <method name='SetGpsRefreshRate'><arg type='u' direction='in'/></method>
  <method name='GetLocation'><arg type='a{{uv}}' direction='out'/></method>
  <property name='Capabilities' type='u' access='read'/>
  <property name='Enabled' type='u' access='read'/>
  <property name='SignalsLocation' type='b' access='read'/>
  <property name='Location' type='a{{uv}}' access='read'/>
</interface>
<interface name='{GC}.Agent'>
  <method name='AuthorizeApp'>
    <arg type='s' direction='in'/><arg type='u' direction='in'/>
    <arg type='b' direction='out'/><arg type='u' direction='out'/>
  </method>
  <property name='MaxAccuracyLevel' type='u' access='read'/>
</interface>
</node>"""

CONFIG = """[agent]
whitelist=fuxi-integration-agent
[modem-gps]
enable=true
[3g]
enable=false
[cdma]
enable=false
[network-nmea]
enable=false
[wifi]
enable=false
url=http://127.0.0.1:9/disabled
submit-data=false
[compass]
enable=false
[static-source]
enable=true
"""


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


class Scenario:
    def __init__(self, address, daemon):
        self.daemon = daemon
        self.connection = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None,
        )
        self.enabled = CELL
        self.signals = False
        self.max_accuracy = 0
        self.calls = []
        self.updates = []
        self.errors = []
        self.registrations = []
        self.subscription = None
        self.introspection = Gio.DBusNodeInfo.new_for_xml(XML)
        for path, interfaces in (
            ("/org/freedesktop/ModemManager1", [OBJECTS]),
            (MODEM_PATH, [MODEM, LOCATION]),
            (GC_PATH + "/Agent", [GC + ".Agent"]),
        ):
            for interface in interfaces:
                self.registrations.append(self.connection.register_object(
                    path, self.introspection.lookup_interface(interface),
                    self.method, self.property, None,
                ))
        self.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                  "org.freedesktop.DBus", "RequestName", "(su)", (MM, 0))

    def wait(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        context = GLib.MainContext.default()
        while not predicate():
            if self.errors:
                raise AssertionError(self.errors)
            if self.daemon.poll() is not None:
                raise AssertionError(f"GeoClue exited: {self.daemon.returncode}")
            if time.monotonic() >= deadline:
                raise TimeoutError("D-Bus condition did not complete")
            while context.pending():
                context.iteration(False)
            time.sleep(0.005)

    def settle(self):
        deadline = time.monotonic() + 0.25
        self.wait(lambda: time.monotonic() >= deadline)

    def call(self, name, path, interface, method, signature=None, args=()):
        result = []

        def ready(connection, response):
            try:
                result.append(connection.call_finish(response))
            except GLib.Error as error:
                result.append(error)

        self.connection.call(
            name, path, interface, method,
            GLib.Variant(signature, args) if signature else None,
            None, Gio.DBusCallFlags.NO_AUTO_START, 4000, None, ready,
        )
        self.wait(lambda: bool(result))
        if isinstance(result[0], GLib.Error):
            raise result[0]
        return result[0].unpack()

    def properties(self, interface):
        if interface == MODEM:
            return {"State": GLib.Variant("i", 11)}
        if interface == GC + ".Agent":
            return {"MaxAccuracyLevel": GLib.Variant("u", self.max_accuracy)}
        return {
            "Capabilities": GLib.Variant("u", CELL | RAW),
            "Enabled": GLib.Variant("u", self.enabled),
            "SignalsLocation": GLib.Variant("b", self.signals),
            "Location": GLib.Variant("a{uv}", {}),
        }

    def property(self, connection, sender, path, interface, name):
        return self.properties(interface)[name]

    def changed(self, path, interface, values):
        self.connection.emit_signal(
            None, path, PROPS, "PropertiesChanged",
            GLib.Variant("(sa{sv}as)", (interface, values, [])),
        )

    def method(self, connection, sender, path, interface, method, args, invocation):
        try:
            self.calls.append((method, args.unpack()))
            if method == "GetManagedObjects":
                value = GLib.Variant("(a{oa{sa{sv}}})", ({MODEM_PATH: {
                    MODEM: self.properties(MODEM),
                    LOCATION: self.properties(LOCATION),
                }},))
            elif method == "Setup":
                self.enabled, self.signals = args.unpack()
                self.changed(MODEM_PATH, LOCATION, {
                    "Enabled": GLib.Variant("u", self.enabled),
                    "SignalsLocation": GLib.Variant("b", self.signals),
                })
                value = None
            elif method == "SetGpsRefreshRate":
                value = None
            elif method == "AuthorizeApp":
                value = GLib.Variant("(bu)", (True, self.max_accuracy))
            else:
                raise AssertionError(f"Unexpected method: {method}")
            invocation.return_value(value)
        except Exception as error:
            self.errors.append(str(error))
            invocation.return_dbus_error("org.fuxi.Test.Error", str(error))

    def client_call(self, method):
        return self.call(GC, self.client, GC + ".Client", method)

    def get(self, path, interface, name):
        return self.call(GC, path, PROPS, "Get", "(ss)", (interface, name))[0]

    def fix(self, timestamp, radius=7.25):
        dictionary = {
            "latitude": GLib.Variant("d", 0.0),
            "longitude": GLib.Variant("d", 0.0),
            "altitude": GLib.Variant("d", 12.5),
            "utc-time": GLib.Variant("s", "120000.000"),
            "x-fuxi-utc-ms": GLib.Variant("t", timestamp),
        }
        if radius is not None:
            dictionary["x-fuxi-horizontal-radius-m"] = GLib.Variant("d", radius)
        self.changed(MODEM_PATH, LOCATION, {
            "Location": GLib.Variant("a{uv}", {RAW: GLib.Variant("a{sv}", dictionary)}),
        })

    def setups(self):
        return [args for method, args in self.calls if method == "Setup"]

    def permission(self, accuracy):
        self.max_accuracy = accuracy
        self.changed(GC_PATH + "/Agent", GC + ".Agent", self.properties(GC + ".Agent"))
        self.settle()

    def run(self):
        def owned():
            return self.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                             "org.freedesktop.DBus", "NameHasOwner", "(s)", (GC,))[0]

        self.wait(owned)
        self.call(GC, GC_PATH + "/Manager", GC + ".Manager",
                  "AddAgent", "(s)", ("fuxi-integration-agent",))
        self.client = self.call(GC, GC_PATH + "/Manager", GC + ".Manager", "CreateClient")[0]
        for name, value in (("DesktopId", GLib.Variant("s", "fuxi-integration-client")),
                            ("RequestedAccuracyLevel", GLib.Variant("u", 8))):
            self.call(GC, self.client, PROPS, "Set", "(ssv)", (GC + ".Client", name, value))
        self.subscription = self.connection.signal_subscribe(
            GC, GC + ".Client", "LocationUpdated", self.client, None,
            Gio.DBusSignalFlags.NONE,
            lambda connection, sender, path, interface, signal, args:
                self.updates.append(args.unpack()),
        )
        try:
            self.client_call("Start")
        except GLib.Error as error:
            assert Gio.DBusError.get_remote_error(error) == "org.freedesktop.DBus.Error.AccessDenied"
            assert "Geolocation disabled" in error.message
        else:
            raise AssertionError("Disabled agent allowed Start")
        self.settle()
        assert not self.setups() and not self.updates
        assert self.get(self.client, GC + ".Client", "Active") is False

        self.permission(8)
        self.client_call("Start")
        self.wait(lambda: self.enabled == CELL | RAW and self.signals)
        self.settle()
        assert self.get(self.client, GC + ".Client", "Active") is True
        timestamp = time.time_ns() // 1_000_000
        self.fix(timestamp - 60_000)
        self.fix(timestamp, radius=None)
        self.settle()
        assert not self.updates
        self.fix(timestamp)
        self.wait(lambda: len(self.updates) == 1)
        location = self.call(GC, self.updates[-1][1], PROPS, "GetAll",
                             "(s)", (GC + ".Location",))[0]
        for name, value in (("Latitude", 0.0), ("Longitude", 0.0),
                            ("Accuracy", 7.25), ("Altitude", 12.5)):
            assert location[name] == value, (name, location[name])
        assert location["Timestamp"] == (timestamp // 1000, 0), location["Timestamp"]
        self.fix(timestamp)
        self.settle()
        assert len(self.updates) == 1

        self.client_call("Stop")
        self.wait(lambda: self.enabled == CELL and not self.signals)
        assert self.get(self.client, GC + ".Client", "Active") is False
        self.fix(timestamp + 1)
        self.settle()
        assert len(self.updates) == 1
        assert self.setups() == [(CELL | RAW, True), (CELL, False)], self.setups()

        self.client_call("Start")
        self.wait(lambda: self.enabled == CELL | RAW and self.signals)
        self.settle()
        assert len(self.updates) == 1, "Restart delivered cached location"
        self.fix(timestamp)
        self.settle()
        assert len(self.updates) == 1, "Restart accepted previous session's fix"
        self.fix(time.time_ns() // 1_000_000)
        self.wait(lambda: len(self.updates) == 2)

        self.permission(0)
        self.wait(lambda: self.enabled == CELL and not self.signals)
        assert self.get(self.client, GC + ".Client", "Active") is False
        self.fix(time.time_ns() // 1_000_000)
        self.settle()
        assert len(self.updates) == 2

        self.permission(8)
        self.wait(lambda: self.enabled == CELL | RAW and self.signals)
        assert self.get(self.client, GC + ".Client", "Active") is True
        self.settle()
        assert len(self.updates) == 2
        self.fix(time.time_ns() // 1_000_000)
        self.wait(lambda: len(self.updates) == 3)
        self.permission(0)
        self.wait(lambda: self.enabled == CELL and not self.signals)
        self.client_call("Stop")
        self.permission(8)
        assert self.get(self.client, GC + ".Client", "Active") is False, "Explicitly stopped client restarted"
        assert self.setups() == [(CELL | RAW, True), (CELL, False)] * 3, self.setups()
        assert len(self.updates) == 3
        assert not self.errors
        self.call(GC, GC_PATH + "/Manager", GC + ".Manager", "DeleteClient", "(o)", (self.client,))
        return {"disabled_denied": True, "raw_delivered": True,
                "invalid_rejected": True, "stop_restored": True,
                "restart_fresh": True, "revocation_stopped": True,
                "permission_resume": True,
                "explicit_stop_respected": True,
                "location_updates": len(self.updates), "setup_calls": len(self.setups())}

    def close(self):
        if self.subscription is not None:
            self.connection.signal_unsubscribe(self.subscription)
        for registration in self.registrations:
            self.connection.unregister_object(registration)
        self.connection.close_sync(None)


def main():
    if not __debug__:
        raise RuntimeError("Assertions must be enabled for this integration test")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_dir", type=Path)
    args = parser.parse_args()
    build = args.build_dir.resolve(strict=True)
    options = json.loads((build / "meson-info/intro-buildoptions.json").read_text())
    sysconf = Path(next(option["value"] for option in options if option["name"] == "sysconfdir"))
    if not sysconf.is_absolute() or not sysconf.resolve().is_relative_to(build.parent):
        parser.error("Configure an absolute sysconfdir below the build directory's parent")
    # Refuse existing configurations, including symlinks; remove only our directory.
    sysconf.mkdir()
    bus = daemon = scenario = None
    try:
        (sysconf / "geoclue/conf.d").mkdir(parents=True)
        (sysconf / "geoclue/geoclue.conf").write_text(CONFIG)
        with tempfile.TemporaryDirectory(prefix="fuxi-geoclue-") as temporary:
            root = Path(temporary)
            bus_config = root / "bus.conf"
            bus_config.write_text("""<busconfig><type>session</type>
<listen>unix:tmpdir=/tmp</listen><auth>EXTERNAL</auth>
<policy context='default'><allow send_destination='*'/><allow receive_sender='*'/>
<allow own='*'/></policy></busconfig>""")
            bus = subprocess.Popen(["dbus-daemon", "--nofork", "--print-address=1",
                                    f"--config-file={bus_config}"], stdout=subprocess.PIPE, text=True)
            with selectors.DefaultSelector() as selector:
                selector.register(bus.stdout, selectors.EVENT_READ)
                if not selector.select(timeout=5):
                    raise TimeoutError("Private bus did not publish its address")
            address = bus.stdout.readline().strip()
            if not address.startswith("unix:"):
                raise RuntimeError("Private bus failed to start")
            environment = dict(os.environ, DBUS_SYSTEM_BUS_ADDRESS=address,
                               DBUS_SESSION_BUS_ADDRESS=address, G_MESSAGES_DEBUG="all")
            with tempfile.TemporaryFile(mode="w+") as log:
                daemon = subprocess.Popen([str(build / "src/geoclue"), "--timeout=0"],
                                          env=environment, stdout=log, stderr=log)
                try:
                    scenario = Scenario(address, daemon)
                    print(json.dumps(scenario.run(), sort_keys=True))
                except Exception:
                    log.seek(0)
                    print(log.read())
                    raise
                finally:
                    stop(daemon)
                    if scenario:
                        scenario.close()
                    stop(bus)
    finally:
        stop(daemon)
        stop(bus)
        (sysconf / "geoclue/geoclue.conf").unlink(missing_ok=True)
        (sysconf / "geoclue/conf.d").rmdir()
        (sysconf / "geoclue").rmdir()
        sysconf.rmdir()


if __name__ == "__main__":
    main()
