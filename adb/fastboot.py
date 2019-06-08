# Copyright 2014 Google Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""A libusb1-based fastboot implementation."""

import binascii
import collections
import io
import logging
import os
import struct

from adb import common
from adb import usb_exceptions

_LOG = logging.getLogger('fastboot')

DEFAULT_MESSAGE_CALLBACK = lambda m: logging.info('Got %s from device', m)
FastbootMessage = collections.namedtuple(  # pylint: disable=invalid-name
    'FastbootMessage', ['message', 'header'])

# From fastboot.c
VENDORS = {0x18D1, 0x0451, 0x0502, 0x0FCE, 0x05C6, 0x22B8, 0x0955,
           0x413C, 0x2314, 0x0BB4, 0x8087}
CLASS = 0xFF
SUBCLASS = 0x42
PROTOCOL = 0x03
# pylint: disable=invalid-name
DeviceIsAvailable = common.interface_matcher(CLASS, SUBCLASS, PROTOCOL)


# pylint doesn't understand cross-module exception baseclasses.
# pylint: disable=nonstandard-exception
class FastbootTransferError(usb_exceptions.FormatMessageWithArgumentsException):
    """Transfer error."""


class FastbootRemoteFailure(usb_exceptions.FormatMessageWithArgumentsException):
    """Remote error."""


class FastbootStateMismatch(usb_exceptions.FormatMessageWithArgumentsException):
    """Fastboot and uboot's state machines are arguing. You Lose."""


class FastbootInvalidResponse(
    usb_exceptions.FormatMessageWithArgumentsException):
    """Fastboot responded with a header we didn't expect."""


class FastbootProtocol(object):
    """Encapsulates the fastboot protocol."""
    FINAL_HEADERS = {b'OKAY', b'DATA'}

    def __init__(self, usb, chunk_kb=1024):
        """Constructs a FastbootProtocol instance.

        Parameters
        ----------
          usb: UsbHandle instance.
          chunk_kb: Packet size. For older devices, 4 may be required.
        """
        self.usb = usb
        self.chunk_kb = chunk_kb

    @property
    def usb_handle(self):
        return self.usb

    def send_command(self, command, arg=None):
        """Sends a command to the device.

        Parameters
        ----------
          command: The command to send.
          arg: Optional argument to the command.
        """
        if arg is not None:
            if not isinstance(arg, bytes):
                arg = arg.encode('utf8')
            command = b'%s:%s' % (command, arg)

        self._write(io.BytesIO(command), len(command))

    def handle_simple_responses(
            self, timeout_ms=None, info_cb=DEFAULT_MESSAGE_CALLBACK):
        """Accepts normal responses from the device.

        Parameters
        ----------
          timeout_ms: Timeout in milliseconds to wait for each response.
          info_cb: Optional callback for text sent from the bootloader.

        Returns:
          OKAY packet's message.
        """
        return self._accept_responses(b'OKAY', info_cb, timeout_ms=timeout_ms)

    def handle_data_sending(self, source_file, source_len,
                            info_cb=DEFAULT_MESSAGE_CALLBACK,
                            progress_callback=None, timeout_ms=None):
        """Handles the protocol for sending data to the device.

        Parameters
        ----------
          source_file: File-object to read from for the device.
          source_len: Amount of data, in bytes, to send to the device.
          info_cb: Optional callback for text sent from the bootloader.
          progress_callback: Callback that takes the current and the total progress
            of the current file.
          timeout_ms: Timeout in milliseconds to wait for each response.

        Raises:
          FastbootTransferError: When fastboot can't handle this amount of data.
          FastbootStateMismatch: Fastboot responded with the wrong packet type.
          FastbootRemoteFailure: Fastboot reported failure.
          FastbootInvalidResponse: Fastboot responded with an unknown packet type.

        Returns:
          OKAY packet's message.
        """
        accepted_size = self._accept_responses(
            b'DATA', info_cb, timeout_ms=timeout_ms)

        accepted_size = binascii.unhexlify(accepted_size[:8])
        accepted_size, = struct.unpack(b'>I', accepted_size)
        if accepted_size != source_len:
            raise FastbootTransferError(
                'Device refused to download %s bytes of data (accepts %s bytes)',
                source_len, accepted_size)
        self._write(source_file, accepted_size, progress_callback)
        return self._accept_responses(b'OKAY', info_cb, timeout_ms=timeout_ms)

    def _accept_responses(self, expected_header, info_cb, timeout_ms=None):
        """Accepts responses until the expected header or a FAIL.

        Parameters
        ----------
          expected_header: OKAY or DATA
          info_cb: Optional callback for text sent from the bootloader.
          timeout_ms: Timeout in milliseconds to wait for each response.

        Raises:
          FastbootStateMismatch: Fastboot responded with the wrong packet type.
          FastbootRemoteFailure: Fastboot reported failure.
          FastbootInvalidResponse: Fastboot responded with an unknown packet type.

        Returns:
          OKAY packet's message.
        """
        while True:
            response = self.usb.bulk_read(64, timeout_ms=timeout_ms)
            header = bytes(response[:4])
            remaining = bytes(response[4:])

            if header == b'INFO':
                info_cb(FastbootMessage(remaining, header))
            elif header in self.FINAL_HEADERS:
                if header != expected_header:
                    raise FastbootStateMismatch(
                        'Expected %s, got %s', expected_header, header)
                if header == b'OKAY':
                    info_cb(FastbootMessage(remaining, header))
                return remaining
            elif header == b'FAIL':
                info_cb(FastbootMessage(remaining, header))
                raise FastbootRemoteFailure('FAIL: %s', remaining)
            else:
                raise FastbootInvalidResponse(
                    'Got unknown header %s and response %s', header, remaining)

    def _handle_progress(self, total, progress_callback):
        """Calls the callback with the current progress and total ."""
        current = 0
        while True:
            current += yield
            try:
                progress_callback(current, total)
            except Exception:  # pylint: disable=broad-except
                _LOG.exception('Progress callback raised an exception. %s',
                               progress_callback)
                continue

    def _write(self, data, length, progress_callback=None):
        """Sends the data to the device, tracking progress with the callback."""
        if progress_callback:
            progress = self._handle_progress(length, progress_callback)
            next(progress)
        while length:
            tmp = data.read(self.chunk_kb * 1024)
            length -= len(tmp)
            self.usb.bulk_write(tmp)

            if progress_callback and progress:
                progress.send(len(tmp))


class FastbootCommands(object):
    """Encapsulates the fastboot commands."""

    def __init__(self):
        """Constructs a FastbootCommands instance.

        Parameters
        ----------
        usb: UsbHandle instance.

        """
        self._handle = None
        self._protocol = None

    def _reset(self):
        self.__init__()

    @property
    def usb_handle(self):
        return self._handle

    def close(self):
        self._handle.close()

    def connect_device(self, port_path=None, serial=None, default_timeout_ms=None, chunk_kb=1024, **kwargs):
        """Convenience function to get an adb device from usb path or serial.

        Parameters
        ----------
        port_path : TODO, None
            The filename of usb port to use.
        serial : TODO, None
            The serial number of the device to use.  If serial specifies a TCP address:port, then a TCP connection is
            used instead of a USB connection.
        default_timeout_ms : TODO, None
            The default timeout in milliseconds to use.
        chunk_kb : int
            Amount of data, in kilobytes, to break fastboot packets up into
        handle : TODO
            Device handle to use (instance of common.TcpHandle or common.UsbHandle)
        banner : TODO
            Connection banner to pass to the remote device
        rsa_keys : list
            List of AuthSigner subclass instances to be used for
              authentication. The device can either accept one of these via the Sign
              method, or we will send the result of GetPublicKey from the first one
              if the device doesn't accept any of them.
        auth_timeout_ms : TODO
            Timeout to wait for when sending a new public key. This
              is only relevant when we send a new public key. The device shows a
              dialog and this timeout is how long to wait for that dialog. If used
              in automation, this should be low to catch such a case as a failure
              quickly; while in interactive settings it should be high to allow
              users to accept the dialog. We default to automation here, so it's low
              by default.

        Returns
        -------
        self : FastbootCommands
            TODO

        """

        if 'handle' in kwargs:
            self._handle = kwargs['handle']

        else:
            self._handle = common.UsbHandle.find_and_open(
                DeviceIsAvailable, port_path=port_path, serial=serial,
                timeout_ms=default_timeout_ms)

        self._protocol = FastbootProtocol(self._handle, chunk_kb)

        return self

    @classmethod
    def devices(cls):
        """Get a generator of UsbHandle for devices available."""
        return common.UsbHandle.find_devices(DeviceIsAvailable)

    def _simple_command(self, command, arg=None, **kwargs):
        self._protocol.send_command(command, arg)
        return self._protocol.handle_simple_responses(**kwargs)

    def flash_from_file(self, partition, source_file, source_len=0,
                        info_cb=DEFAULT_MESSAGE_CALLBACK, progress_callback=None):
        """Flashes a partition from the file on disk.

        Parameters
        ----------
          partition: Partition name to flash to.
          source_file: Filename to download to the device.
          source_len: Optional length of source_file, uses os.stat if not provided.
          info_cb: See Download.
          progress_callback: See Download.

        Returns:
          Download and flash responses, normally nothing.
        """
        if source_len == 0:
            # Fall back to stat.
            source_len = os.stat(source_file).st_size
        download_response = self.download(
            source_file, source_len=source_len, info_cb=info_cb,
            progress_callback=progress_callback)
        flash_response = self.flash(partition, info_cb=info_cb)
        return download_response + flash_response

    def download(self, source_file, source_len=0,
                 info_cb=DEFAULT_MESSAGE_CALLBACK, progress_callback=None):
        """Downloads a file to the device.

        Parameters
        ----------
          source_file: A filename or file-like object to download to the device.
          source_len: Optional length of source_file. If source_file is a file-like
              object and source_len is not provided, source_file is read into
              memory.
          info_cb: Optional callback accepting FastbootMessage for text sent from
              the bootloader.
          progress_callback: Optional callback called with the percent of the
              source_file downloaded. Note, this doesn't include progress of the
              actual flashing.

        Returns:
          Response to a download request, normally nothing.
        """
        if isinstance(source_file, str):
            source_len = os.stat(source_file).st_size
            source_file = open(source_file)

        with source_file:
            if source_len == 0:
                # Fall back to storing it all in memory :(
                data = source_file.read()
                source_file = io.BytesIO(data.encode('utf8'))
                source_len = len(data)

            self._protocol.send_command(b'download', b'%08x' % source_len)
            return self._protocol.handle_data_sending(
                source_file, source_len, info_cb, progress_callback=progress_callback)

    def flash(self, partition, timeout_ms=0, info_cb=DEFAULT_MESSAGE_CALLBACK):
        """Flashes the last downloaded file to the given partition.

        Parameters
        ----------
          partition: Partition to overwrite with the new image.
          timeout_ms: Optional timeout in milliseconds to wait for it to finish.
          info_cb: See Download. Usually no messages.

        Returns:
          Response to a download request, normally nothing.
        """
        return self._simple_command(b'flash', arg=partition, info_cb=info_cb,
                                    timeout_ms=timeout_ms)

    def erase(self, partition, timeout_ms=None):
        """Erases the given partition.

        Parameters
        ----------
          partition: Partition to clear.
        """
        self._simple_command(b'erase', arg=partition, timeout_ms=timeout_ms)

    def get_var(self, var, info_cb=DEFAULT_MESSAGE_CALLBACK):
        """Returns the given variable's definition.

        Parameters
        ----------
          var: A variable the bootloader tracks. Use 'all' to get them all.
          info_cb: See Download. Usually no messages.

        Returns:
          Value of var according to the current bootloader.
        """
        return self._simple_command(b'getvar', arg=var, info_cb=info_cb)

    def oem(self, command, timeout_ms=None, info_cb=DEFAULT_MESSAGE_CALLBACK):
        """Executes an OEM command on the device.

        Parameters
        ----------
          command: Command to execute, such as 'poweroff' or 'bootconfig read'.
          timeout_ms: Optional timeout in milliseconds to wait for a response.
          info_cb: See Download. Messages vary based on command.

        Returns:
          The final response from the device.
        """
        if not isinstance(command, bytes):
            command = command.encode('utf8')
        return self._simple_command(
            b'oem %s' % command, timeout_ms=timeout_ms, info_cb=info_cb)

    def _continue(self):
        """Continues execution past fastboot into the system."""
        return self._simple_command(b'continue')

    def reboot(self, target_mode=b'', timeout_ms=None):
        """Reboots the device.

        Parameters
        ----------
            target_mode: Normal reboot when unspecified. Can specify other target
                modes such as 'recovery' or 'bootloader'.
            timeout_ms: Optional timeout in milliseconds to wait for a response.

        Returns:
            Usually the empty string. Depends on the bootloader and the target_mode.
        """
        return self._simple_command(
            b'reboot', arg=target_mode or None, timeout_ms=timeout_ms)

    def reboot_bootloader(self, timeout_ms=None):
        """Reboots into the bootloader, usually equiv to Reboot('bootloader')."""
        return self._simple_command(b'reboot-bootloader', timeout_ms=timeout_ms)
