import binascii
import collections
import io
import logging
import os
import struct

from adb import common
from adb import usb_exceptions

_LOG = logging.getLogger("fastboot")

DEFAULT_MESSAGE_CALLBACK = lambda m: logging.info("Got %s from device", m)

FastbootMessage = collections.namedtuple("FastbootMessage", ["message", "header"])

VENDORS = {
    0x18D1,
    0x0451,
    0x0502,
    0x0FCE,
    0x05C6,
    0x22B8,
    0x0955,
    0x413C,
    0x2314,
    0x0BB4,
    0x8087,
}

CLASS = 0xFF

SUBCLASS = 0x42

PROTOCOL = 0x03

DeviceIsAvailable = common.InterfaceMatcher(CLASS, SUBCLASS, PROTOCOL)

class FastbootTransferError(usb_exceptions.FormatMessageWithArgumentsException):

class FastbootRemoteFailure(usb_exceptions.FormatMessageWithArgumentsException):

class FastbootStateMismatch(usb_exceptions.FormatMessageWithArgumentsException):

# class FastbootInvalidResponse(usb_exceptions.FormatMessageWithArgumentsException):


class FastbootProtocol(object):
    FINAL_HEADERS = {b"OKAY", b"DATA"}

    def __init__(self, usb, chunk_kb=1024):
        self.usb = usb
        self.chunk_kb = chunk_kb

    @property
    def usb_handle(self):
        return self.usb

    def SendCommand(self, command, arg=None):
        if arg is not None:
            if not isinstance(arg, bytes):
                arg = arg.encode("utf8")
            command = b"%s:%s" % (command, arg)
        self._Write(io.BytesIO(command), len(command))

    def HandleSimpleResponses(self, timeout_ms=None, info_cb=DEFAULT_MESSAGE_CALLBACK):
        return self._AcceptResponses(b"OKAY", info_cb, timeout_ms=timeout_ms)

    def HandleDataSending(
        self,
        source_file,
        source_len,
        info_cb=DEFAULT_MESSAGE_CALLBACK,
        progress_callback=None,
        timeout_ms=None,
    ):
        accepted_size = self._AcceptResponses(b"DATA", info_cb, timeout_ms=timeout_ms)
        accepted_size = binascii.unhexlify(accepted_size[:8])
        accepted_size, = struct.unpack(b">I", accepted_size)
        if accepted_size != source_len:
            raise FastbootTransferError(
                "Device refused to download {0} bytes of data (accepts {1} bytes)".format(
                    source_len, accepted_size
                )
            )
        self._Write(source_file, accepted_size, progress_callback)
        return self._AcceptResponses(b"OKAY", info_cb, timeout_ms=timeout_ms)

    def _AcceptResponses(self, expected_header, info_cb, timeout_ms=None):
        while True:
            response = self.usb.BulkRead(64, timeout_ms=timeout_ms)
            header = bytes(response[:4])
            remaining = bytes(response[4:])
            if header == b"INFO":
                info_cb(FastbootMessage(remaining, header))
            elif header in self.FINAL_HEADERS:
                if header != expected_header:
                    raise FastbootStateMismatch(
                        "Expected {0}, got {1}".format(expected_header, header)
                    )
                if header == b"OKAY":
                    info_cb(FastbootMessage(remaining, header))
                return remaining
            elif header == b"FAIL":
                info_cb(FastbootMessage(remaining, header))
                raise FastbootRemoteFailure("FAIL: {0}".format(remaining))
            else:
                raise FastbootInvalidResponse(
                    "Got unknown header {0} and response {1}".format(header, remaining)
                )

    @staticmethod
    def _HandleProgress(total, progress_callback):
        current = 0
        while True:
            current += yield
            try:
                progress_callback(current, total)
            except Exception:
                _LOG.exception(
                    "Progress callback raised an exception. %s", progress_callback
                )
                continue

    def _Write(self, data, length, progress_callback=None):
        if progress_callback:
            progress = self._HandleProgress(length, progress_callback)
            next(progress)
        while length:
            tmp = data.read(self.chunk_kb * 1024)
            length -= len(tmp)
            self.usb.BulkWrite(tmp)
            if progress_callback and progress:
                progress.send(len(tmp))


class FastbootCommands(object):
    def __init__(self):
        self._handle = None
        self._protocol = None

    def __reset(self):
        self.__init__()

    @property
    def usb_handle(self):
        return self._handle

    def Close(self):
        self._handle.Close()

    def ConnectDevice(
        self,
        port_path=None,
        serial=None,
        default_timeout_ms=None,
        chunk_kb=1024,
        **kwargs
    ):
        if "handle" in kwargs:
            self._handle = kwargs["handle"]
        else:
            self._handle = common.UsbHandle.FindAndOpen(
                DeviceIsAvailable,
                port_path=port_path,
                serial=serial,
                timeout_ms=default_timeout_ms,
            )
        self._protocol = FastbootProtocol(self._handle, chunk_kb)
        return self

    @classmethod
    def Devices(cls):
        return common.UsbHandle.FindDevices(DeviceIsAvailable)

    def _SimpleCommand(self, command, arg=None, **kwargs):
        self._protocol.SendCommand(command, arg)
        return self._protocol.HandleSimpleResponses(**kwargs)

    def FlashFromFile(
        self,
        partition,
        source_file,
        source_len=0,
        info_cb=DEFAULT_MESSAGE_CALLBACK,
        progress_callback=None,
    ):
        if source_len == 0:
            source_len = os.stat(source_file).st_size
        download_response = self.Download(
            source_file,
            source_len=source_len,
            info_cb=info_cb,
            progress_callback=progress_callback,
        )
        flash_response = self.Flash(partition, info_cb=info_cb)
        return download_response + flash_response

    def Download(
        self,
        source_file,
        source_len=0,
        info_cb=DEFAULT_MESSAGE_CALLBACK,
        progress_callback=None,
    ):
        if isinstance(source_file, str):
            source_len = os.stat(source_file).st_size
            source_file = open(source_file)
        with source_file:
            if source_len == 0:
                data = source_file.read()
                source_file = io.BytesIO(data.encode("utf8"))
                source_len = len(data)
            self._protocol.SendCommand(b"download", b"%08x" % source_len)
            return self._protocol.HandleDataSending(
                source_file, source_len, info_cb, progress_callback=progress_callback
            )

    def Flash(self, partition, timeout_ms=0, info_cb=DEFAULT_MESSAGE_CALLBACK):
        return self._SimpleCommand(
            b"flash", arg=partition, info_cb=info_cb, timeout_ms=timeout_ms
        )

    def Erase(self, partition, timeout_ms=None):
        self._SimpleCommand(b"erase", arg=partition, timeout_ms=timeout_ms)

    def Getvar(self, var, info_cb=DEFAULT_MESSAGE_CALLBACK):
        return self._SimpleCommand(b"getvar", arg=var, info_cb=info_cb)

    def Oem(self, command, timeout_ms=None, info_cb=DEFAULT_MESSAGE_CALLBACK):
        if not isinstance(command, bytes):
            command = command.encode("utf8")
        return self._SimpleCommand(
            b"oem %s" % command, timeout_ms=timeout_ms, info_cb=info_cb
        )

    def Continue(self):
        return self._SimpleCommand(b"continue")

    def Reboot(self, target_mode=b"", timeout_ms=None):
        return self._SimpleCommand(
            b"reboot", arg=target_mode or None, timeout_ms=timeout_ms
        )

    def RebootBootloader(self, timeout_ms=None):
        return self._SimpleCommand(b"reboot-bootloader", timeout_ms=timeout_ms)
