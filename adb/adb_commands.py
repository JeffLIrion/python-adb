import io
import os
import socket
import posixpath

from adb import adb_protocol
from adb import common
from adb import filesync_protocol

CLASS = 0xFF
SUBCLASS = 0x42
PROTOCOL = 0x01

DeviceIsAvailable = common.InterfaceMatcher(CLASS, SUBCLASS, PROTOCOL)

try:
    from adb.sign_cryptography import CryptographySigner
except ImportError:
    pass


class AdbCommands(object):
    protocol_handler = adb_protocol.AdbMessage
    filesync_handler = filesync_protocol.FilesyncProtocol

    def __init__(self):
        self.__reset()

    def __reset(self):
        self.build_props = None
        self._handle = None
        self._device_state = None
        self._service_connections = {}

    def _get_service_connection(
        self, service, service_command=None, create=True, timeout_ms=None
    ):
        connection = self._service_connections.get(service, None)
        if connection:
            return connection
        if not connection and not create:
            return None
        if service_command:
            destination_str = b"%s:%s" % (service, service_command)
        else:
            destination_str = service
        connection = self.protocol_handler.Open(
            self._handle, destination=destination_str, timeout_ms=timeout_ms
        )
        self._service_connections.update({service: connection})
        return connection

    def ConnectDevice(
        self, port_path=None, serial=None, default_timeout_ms=None, **kwargs
    ):
        if "handle" in kwargs:
            self._handle = kwargs.pop("handle")
        else:
            if isinstance(serial, (bytes, bytearray)):
                serial = serial.decode("utf-8")
            if serial and ":" in serial:
                self._handle = common.TcpHandle(serial, timeout_ms=default_timeout_ms)
            else:
                self._handle = common.UsbHandle.FindAndOpen(
                    DeviceIsAvailable,
                    port_path=port_path,
                    serial=serial,
                    timeout_ms=default_timeout_ms,
                )
        self._Connect(**kwargs)
        return self

    def Close(self):
        for conn in list(self._service_connections.values()):
            if conn:
                try:
                    conn.Close()
                except:
                    pass
        if self._handle:
            self._handle.Close()
        self.__reset()

    def _Connect(self, banner=None, **kwargs):
        if not banner:
            banner = socket.gethostname().encode()
        conn_str = self.protocol_handler.Connect(self._handle, banner=banner, **kwargs)
        parts = conn_str.split(b"::")
        self._device_state = parts[0]
        self.build_props = str(parts[1].split(b";"))
        return True

    @classmethod
    def Devices(cls):
        return common.UsbHandle.FindDevices(DeviceIsAvailable)

    def GetState(self):
        return self._device_state

    def Install(
        self,
        apk_path,
        destination_dir="",
        replace_existing=True,
        grant_permissions=False,
        timeout_ms=None,
        transfer_progress_callback=None,
    ):
        if not destination_dir:
            destination_dir = "/data/local/tmp/"
        basename = os.path.basename(apk_path)
        destination_path = posixpath.join(destination_dir, basename)
        self.Push(
            apk_path,
            destination_path,
            timeout_ms=timeout_ms,
            progress_callback=transfer_progress_callback,
        )
        cmd = ["pm install"]
        if grant_permissions:
            cmd.append("-g")
        if replace_existing:
            cmd.append("-r")
        cmd.append('"{}"'.format(destination_path))
        ret = self.Shell(" ".join(cmd), timeout_ms=timeout_ms)
        rm_cmd = ["rm", destination_path]
        rmret = self.Shell(" ".join(rm_cmd), timeout_ms=timeout_ms)
        return ret

    def Uninstall(self, package_name, keep_data=False, timeout_ms=None):
        cmd = ["pm uninstall"]
        if keep_data:
            cmd.append("-k")
        cmd.append('"%s"' % package_name)
        return self.Shell(" ".join(cmd), timeout_ms=timeout_ms)

    def Push(
        self,
        source_file,
        device_filename,
        mtime="0",
        timeout_ms=None,
        progress_callback=None,
        st_mode=None,
    ):
        if isinstance(source_file, str):
            if os.path.isdir(source_file):
                self.Shell("mkdir " + device_filename)
                for f in os.listdir(source_file):
                    self.Push(
                        os.path.join(source_file, f),
                        device_filename + "/" + f,
                        progress_callback=progress_callback,
                    )
                return
            source_file = open(source_file, "rb")
        with source_file:
            connection = self.protocol_handler.Open(
                self._handle, destination=b"sync:", timeout_ms=timeout_ms
            )
            kwargs = {}
            if st_mode is not None:
                kwargs["st_mode"] = st_mode
            self.filesync_handler.Push(
                connection,
                source_file,
                device_filename,
                mtime=int(mtime),
                progress_callback=progress_callback,
                **kwargs
            )
        connection.Close()

    def Pull(
        self, device_filename, dest_file=None, timeout_ms=None, progress_callback=None
    ):
        if not dest_file:
            dest_file = io.BytesIO()
        elif isinstance(dest_file, str):
            dest_file = open(dest_file, "wb")
        elif isinstance(dest_file, file):
            pass
        else:
            raise ValueError("destfile is of unknown type")
        conn = self.protocol_handler.Open(
            self._handle, destination=b"sync:", timeout_ms=timeout_ms
        )
        self.filesync_handler.Pull(conn, device_filename, dest_file, progress_callback)
        conn.Close()
        if isinstance(dest_file, io.BytesIO):
            return dest_file.getvalue()
        else:
            dest_file.close()
            if hasattr(dest_file, "name"):
                return os.path.exists(dest_file.name)
            return True

    def Stat(self, device_filename):
        connection = self.protocol_handler.Open(self._handle, destination=b"sync:")
        mode, size, mtime = self.filesync_handler.Stat(connection, device_filename)
        connection.Close()
        return mode, size, mtime

    def List(self, device_path):
        connection = self.protocol_handler.Open(self._handle, destination=b"sync:")
        listing = self.filesync_handler.List(connection, device_path)
        connection.Close()
        return listing

    def Reboot(self, destination=b""):
        self.protocol_handler.Open(self._handle, b"reboot:%s" % destination)

    def RebootBootloader(self):
        self.Reboot(b"bootloader")

    def Remount(self):
        return self.protocol_handler.Command(self._handle, service=b"remount")

    def Root(self):
        return self.protocol_handler.Command(self._handle, service=b"root")

    def EnableVerity(self):
        return self.protocol_handler.Command(self._handle, service=b"enable-verity")

    def DisableVerity(self):
        return self.protocol_handler.Command(self._handle, service=b"disable-verity")

    def Shell(self, command, timeout_ms=None):
        return self.protocol_handler.Command(
            self._handle, service=b"shell", command=command, timeout_ms=timeout_ms
        )

    def StreamingShell(self, command, timeout_ms=None):
        return self.protocol_handler.StreamingCommand(
            self._handle, service=b"shell", command=command, timeout_ms=timeout_ms
        )

    def Logcat(self, options, timeout_ms=None):
        return self.StreamingShell("logcat %s" % options, timeout_ms)

    def InteractiveShell(self, cmd=None, strip_cmd=True, delim=None, strip_delim=True):
        conn = self._get_service_connection(b"shell:")
        return self.protocol_handler.InteractiveShellCommand(
            conn, cmd=cmd, strip_cmd=strip_cmd, delim=delim, strip_delim=strip_delim
        )
