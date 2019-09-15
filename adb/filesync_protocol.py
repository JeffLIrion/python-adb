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

"""ADB protocol implementation.

Implements the ADB protocol as seen in android's adb/adbd binaries, but only the
host side.


.. rubric:: Contents

* :class:`FileSyncConnection`

    * :meth:`FileSyncConnection.Read`
    * :meth:`FileSyncConnection.ReadUntil`
    * :meth:`FileSyncConnection.Send`
    * :meth:`FileSyncConnection._CanAddToSendBuffer`
    * :meth:`FileSyncConnection._Flush`
    * :meth:`FileSyncConnection._ReadBuffered`

* :class:`FilesyncProtocol`

    * :meth:`FilesyncProtocol._HandleProgress`
    * :meth:`FilesyncProtocol.List`
    * :meth:`FilesyncProtocol.Pull`
    * :meth:`FilesyncProtocol.Push`
    * :meth:`FilesyncProtocol.Stat`

* :class:`InterleavedDataError`
* :class:`InvalidChecksumError`
* :class:`PullFailedError`
* :class:`PushFailedError`

"""

import collections
import io
import os
import stat
import struct
import time

import libusb1

from adb import adb_protocol
from adb.debug import debug_print, FALSE, TRUE  # DEBUGGING, pragma: no cover
from adb import usb_exceptions


try:
    file_types = (file, io.IOBase)
except NameError:
    file_types = (io.IOBase,)

#: Default mode for pushed files.
DEFAULT_PUSH_MODE = stat.S_IFREG | stat.S_IRWXU | stat.S_IRWXG

#: Maximum size of a filesync DATA packet.
MAX_PUSH_DATA = 2 * 1024


class InvalidChecksumError(Exception):
    """Checksum of data didn't match expected checksum.

    .. image:: _static/adb.filesync_protocol.InvalidChecksumError.CALL_GRAPH.svg

    """


class InterleavedDataError(Exception):
    """We only support command sent serially.

    .. image:: _static/adb.filesync_protocol.InterleavedDataError.CALL_GRAPH.svg

    """


class PushFailedError(Exception):
    """Pushing a file failed for some reason.

    .. image:: _static/adb.filesync_protocol.PushFailedError.CALL_GRAPH.svg

    """


class PullFailedError(Exception):
    """Pulling a file failed for some reason.

    .. image:: _static/adb.filesync_protocol.PullFailedError.CALL_GRAPH.svg

    """


DeviceFile = collections.namedtuple('DeviceFile', ['filename', 'mode', 'size', 'mtime'])


class FilesyncProtocol(object):
    """Implements the FileSync protocol as described in sync.txt."""

    @staticmethod
    def Stat(connection, filename):
        """TODO

        .. image:: _static/adb.filesync_protocol.FilesyncProtocol.Stat.CALLER_GRAPH.svg

        Parameters
        ----------
        connection : TODO
            TODO
        filename : TODO
            TODO

        Returns
        -------
        mode : TODO
            TODO
        size : TODO
            TODO
        mtime : TODO
            TODO

        Raises
        ------
        adb.adb_protocol.InvalidResponseError
            Expected STAT response to STAT, got something else

        """
        cnxn = FileSyncConnection(connection, b'<4I')
        cnxn.Send(b'STAT', filename)
        command, (mode, size, mtime) = cnxn.Read((b'STAT',), read_data=False)

        if command != b'STAT':
            raise adb_protocol.InvalidResponseError(
                'Expected STAT response to STAT, got %s' % command)
        return mode, size, mtime

    @classmethod
    def List(cls, connection, path):
        """TODO

        Parameters
        ----------
        connection : TODO
            TODO
        path : TODO
            TODO

        Returns
        -------
        files : list
            TODO

        """
        cnxn = FileSyncConnection(connection, b'<5I')
        cnxn.Send(b'LIST', path)
        files = []
        for cmd_id, header, filename in cnxn.ReadUntil((b'DENT',), b'DONE'):
            if cmd_id == b'DONE':
                break
            mode, size, mtime = header
            files.append(DeviceFile(filename, mode, size, mtime))
        return files

    @classmethod
    def Pull(cls, connection, filename, dest_file, progress_callback):
        """Pull a file from the device into the file-like dest_file.

        .. image:: _static/adb.filesync_protocol.FilesyncProtocol.Pull.CALL_GRAPH.svg

        Parameters
        ----------
        connection : adb.adb_protocol._AdbConnection
            TODO
        filename : str
            TODO
        dest_file : _io.BytesIO
            TODO
        progress_callback : function, None
            TODO

        Raises
        ------
        PullFailedError
            Unable to pull file

        """
        if FALSE:  # DEBUGGING, pragma: no cover
            print("\n\nParameters\n----------")  # DEBUGGING, pragma: no cover
            debug_print('connection', connection)  # DEBUGGING, pragma: no cover
            debug_print('filename', filename)  # DEBUGGING, pragma: no cover
            debug_print('dest_file', dest_file)  # DEBUGGING, pragma: no cover
            debug_print('progress_callback', progress_callback)  # DEBUGGING, pragma: no cover
            print("\n\n")  # DEBUGGING, pragma: no cover
        if progress_callback:
            total_bytes = cls.Stat(connection, filename)[1]
            progress = cls._HandleProgress(lambda current: progress_callback(filename, current, total_bytes))
            next(progress)

        cnxn = FileSyncConnection(connection, b'<2I')
        try:
            cnxn.Send(b'RECV', filename)
            for cmd_id, _, data in cnxn.ReadUntil((b'DATA',), b'DONE'):
                if cmd_id == b'DONE':
                    break
                dest_file.write(data)
                if progress_callback:
                    progress.send(len(data))
        except usb_exceptions.CommonUsbError as e:
            raise PullFailedError('Unable to pull file %s due to: %s' % (filename, e))

    @classmethod
    def _HandleProgress(cls, progress_callback):
        """Calls the callback with the current progress and total bytes written/received.

        .. image:: _static/adb.filesync_protocol.FilesyncProtocol._HandleProgress.CALL_GRAPH.svg

        .. image:: _static/adb.filesync_protocol.FilesyncProtocol._HandleProgress.CALLER_GRAPH.svg

        Parameters
        ----------
        progress_callback : TODO
            Callback method that accepts filename, bytes_written and total_bytes; total_bytes will be -1 for file-like
            objects.

        """
        current = 0
        while True:
            current += yield
            try:
                progress_callback(current)
            except Exception:  # pylint: disable=broad-except
                continue

    @classmethod
    def Push(cls, connection, datafile, filename,
             st_mode=DEFAULT_PUSH_MODE, mtime=0, progress_callback=None):
        """Push a file-like object to the device.

        .. image:: _static/adb.filesync_protocol.FilesyncProtocol.Push.CALL_GRAPH.svg

        .. image:: _static/adb.filesync_protocol.FilesyncProtocol.Push.CALLER_GRAPH.svg

        Parameters
        ----------
        connection : adb.adb_protocol._AdbConnection
            ADB connection
        datafile : _io.BytesIO
            File-like object for reading from
        filename : str
            Filename to push to
        st_mode : int
            Stat mode for filename
        mtime : int
            Modification time
        progress_callback : function, None
            Callback method that accepts ``filename``, ``bytes_written``, and ``total_bytes``

        Raises
        ------
        PushFailedError
            Raised on push failure.

        """
        if FALSE:  # DEBUGGING, pragma: no cover
            print("\n\nParameters\n----------")  # DEBUGGING, pragma: no cover
            debug_print('connection', connection)  # DEBUGGING, pragma: no cover
            debug_print('datafile', datafile)  # DEBUGGING, pragma: no cover
            debug_print('filename', filename)  # DEBUGGING, pragma: no cover
            debug_print('st_mode', st_mode)  # DEBUGGING, pragma: no cover
            debug_print('mtime', mtime)  # DEBUGGING, pragma: no cover
            debug_print('progress_callback', progress_callback)  # DEBUGGING, pragma: no cover
            print("\n\n")  # DEBUGGING, pragma: no cover
        fileinfo = ('{},{}'.format(filename, int(st_mode))).encode('utf-8')

        cnxn = FileSyncConnection(connection, b'<2I')
        cnxn.Send(b'SEND', fileinfo)

        if progress_callback:
            total_bytes = os.fstat(datafile.fileno()).st_size if isinstance(datafile, file_types) else -1
            progress = cls._HandleProgress(lambda current: progress_callback(filename, current, total_bytes))
            next(progress)

        while True:
            data = datafile.read(MAX_PUSH_DATA)
            if data:
                cnxn.Send(b'DATA', data)

                if progress_callback:
                    progress.send(len(data))
            else:
                break

        if mtime == 0:
            mtime = int(time.time())
        # DONE doesn't send data, but it hides the last bit of data in the size
        # field.
        cnxn.Send(b'DONE', size=mtime)
        for cmd_id, _, data in cnxn.ReadUntil((), b'OKAY', b'FAIL'):
            if cmd_id == b'OKAY':
                return
            raise PushFailedError(data)


class FileSyncConnection(object):
    """Encapsulate a FileSync service connection.

    Parameters
    ----------
    adb_connection : TODO
        TODO
    recv_header_format : TODO
        TODO

    Attributes
    ----------
    adb : TODO
        TODO
    send_buffer : byte_array
        ``bytearray(adb_protocol.MAX_ADB_DATA)`` (see :const:`adb.adb_protocol.MAX_ADB_DATA`)
    send_idx : int
        TODO
    send_header_len : int
        ``struct.calcsize(b'<2I')``
    recv_buffer : bytearray
        TODO
    recv_header_format : TODO
        TODO
    recv_header_len : int
        ``struct.calcsize(recv_header_format)``

    """

    ids = [b'STAT', b'LIST', b'SEND', b'RECV', b'DENT', b'DONE', b'DATA', b'OKAY', b'FAIL', b'QUIT']
    id_to_wire, wire_to_id = adb_protocol.MakeWireIDs(ids)

    def __init__(self, adb_connection, recv_header_format):
        self.adb = adb_connection

        # Sending
        # Using a bytearray() saves a copy later when using libusb.
        self.send_buffer = bytearray(adb_protocol.MAX_ADB_DATA)
        self.send_idx = 0
        self.send_header_len = struct.calcsize(b'<2I')

        # Receiving
        self.recv_buffer = bytearray()
        self.recv_header_format = recv_header_format
        self.recv_header_len = struct.calcsize(recv_header_format)

    def Send(self, command_id, data=b'', size=0):
        """Send/buffer FileSync packets.

        Packets are buffered and only flushed when this connection is read from. All
        messages have a response from the device, so this will always get flushed.

        .. image:: _static/adb.filesync_protocol.FileSyncConnection.Send.CALL_GRAPH.svg

        Parameters
        ----------
        command_id : bytes
            Command to send.
        data : str, bytes
            Optional data to send, must set data or size.
        size : int
            Optionally override size from len(data).

        """
        if FALSE:  # DEBUGGING, pragma: no cover
            print("\n\nParameters\n----------")  # DEBUGGING, pragma: no cover
            debug_print('command_id', command_id)  # DEBUGGING, pragma: no cover
            debug_print('data', data)  # DEBUGGING, pragma: no cover
            debug_print('size', size)  # DEBUGGING, pragma: no cover
            print("\n\n")  # DEBUGGING, pragma: no cover
        if data:
            if not isinstance(data, bytes):
                data = data.encode('utf8')
            size = len(data)

        if not self._CanAddToSendBuffer(len(data)):
            self._Flush()
        buf = struct.pack(b'<2I', self.id_to_wire[command_id], size) + data
        self.send_buffer[self.send_idx:self.send_idx + len(buf)] = buf
        self.send_idx += len(buf)

    def Read(self, expected_ids, read_data=True):
        """Read ADB messages and return FileSync packets.

        .. image:: _static/adb.filesync_protocol.FileSyncConnection.Read.CALL_GRAPH.svg

        .. image:: _static/adb.filesync_protocol.FileSyncConnection.Read.CALLER_GRAPH.svg

        Parameters
        ----------
        expected_ids : tuple[bytes]
            If the received header ID is not in ``expected_ids``, an exception will be raised
        read_data : bool
            Whether to read the received data

        Returns
        -------
        command_id : bytes
            The received header ID
        tuple
            TODO
        data : bytearray
            The received data

        Raises
        ------
        adb.usb_exceptions.AdbCommandFailureException
            Command failed
        adb.adb_protocol.InvalidResponseError
            Received response was not in ``expected_ids``

        """
        if FALSE:  # DEBUGGING, pragma: no cover
            print("\n\nParameters\n----------")  # DEBUGGING, pragma: no cover
            debug_print('expected_ids', expected_ids)  # DEBUGGING, pragma: no cover
            debug_print('read_data', read_data)  # DEBUGGING, pragma: no cover
            print("\n\n")  # DEBUGGING, pragma: no cover
        if self.send_idx:
            self._Flush()

        # Read one filesync packet off the recv buffer.
        header_data = self._ReadBuffered(self.recv_header_len)
        header = struct.unpack(self.recv_header_format, header_data)
        # Header is (ID, ...).
        command_id = self.wire_to_id[header[0]]

        if command_id not in expected_ids:
            if command_id == b'FAIL':
                reason = ''
                if self.recv_buffer:
                    reason = self.recv_buffer.decode('utf-8', errors='ignore')

                raise usb_exceptions.AdbCommandFailureException('Command failed: {}'.format(reason))

            raise adb_protocol.InvalidResponseError('Expected one of %s, got %s' % (expected_ids, command_id))

        if not read_data:
            return command_id, header[1:]

        # Header is (ID, ..., size).
        size = header[-1]
        data = self._ReadBuffered(size)

        if FALSE:  # DEBUGGING, pragma: no cover
            print("\n\nReturns\n-------")  # DEBUGGING, pragma: no cover
            debug_print('command_id', command_id)  # DEBUGGING, pragma: no cover
            debug_print('header[1:-1]', header[1:-1])  # DEBUGGING, pragma: no cover
            debug_print('data', data)  # DEBUGGING, pragma: no cover
            print("\n\n")  # DEBUGGING, pragma: no cover
        return command_id, header[1:-1], data

    def ReadUntil(self, expected_ids, *finish_ids):
        """Useful wrapper around Read.

        .. image:: _static/adb.filesync_protocol.FileSyncConnection.ReadUntil.CALL_GRAPH.svg

        Parameters
        ----------
        expected_ids : tuple[bytes]
            If the received header ID is not in ``expected_ids``, an exception will be raised
        finish_ids : tuple[bytes]
            We will read until we find a header ID that is in ``finish_ids``

        Yields
        ------
        cmd_id : bytes
            The received header ID
        header : tuple
            TODO
        data : bytearray
            The received data

        """
        if FALSE:  # DEBUGGING, pragma: no cover
            print("\n\nParameters\n----------")  # DEBUGGING, pragma: no cover
            debug_print('expected_ids', expected_ids)  # DEBUGGING, pragma: no cover
            debug_print('finish_ids', finish_ids)  # DEBUGGING, pragma: no cover
            print("\n\n")  # DEBUGGING, pragma: no cover
        while True:
            cmd_id, header, data = self.Read(expected_ids + finish_ids)
            if FALSE:  # DEBUGGING, pragma: no cover
                print("\n\nReturns\n-------")  # DEBUGGING, pragma: no cover
                debug_print('cmd_id', cmd_id)  # DEBUGGING, pragma: no cover
                debug_print('header', header)  # DEBUGGING, pragma: no cover
                debug_print('data', data)  # DEBUGGING, pragma: no cover
                print("\n\n")  # DEBUGGING, pragma: no cover
            yield cmd_id, header, data
            if cmd_id in finish_ids:
                break

    def _CanAddToSendBuffer(self, data_len):
        """Determine whether ``data_len`` bytes of data can be added to the send buffer without exceeding :const:`adb.adb_protocol.MAX_ADB_DATA`.

        .. image:: _static/adb.filesync_protocol.FileSyncConnection._CanAddToSendBuffer.CALLER_GRAPH.svg

        Parameters
        ----------
        data_len : int
            The length of the data to be potentially added to the send buffer (not including the length of its header)

        Returns
        -------
        bool
            Whether ``data_len`` bytes of data can be added to the send buffer without exceeding :const:`adb.adb_protocol.MAX_ADB_DATA`

        """
        if FALSE:  # DEBUGGING, pragma: no cover
            print("\n\nParameters\n----------")  # DEBUGGING, pragma: no cover
            debug_print('data_len', data_len)  # DEBUGGING, pragma: no cover
            print("\n\n")  # DEBUGGING, pragma: no cover
        added_len = self.send_header_len + data_len
        if FALSE:  # DEBUGGING, pragma: no cover
            print("\n\nReturns\n-------")  # DEBUGGING, pragma: no cover
            debug_print('bool', self.send_idx + added_len < adb_protocol.MAX_ADB_DATA)  # DEBUGGING, pragma: no cover
            print("\n\n")  # DEBUGGING, pragma: no cover
        return self.send_idx + added_len < adb_protocol.MAX_ADB_DATA

    def _Flush(self):
        """TODO

        .. image:: _static/adb.filesync_protocol.FileSyncConnection._Flush.CALLER_GRAPH.svg

        Raises
        ------
        adb.usb_exceptions.WriteFailedError
            Could not send data

        """
        try:
            self.adb.Write(self.send_buffer[:self.send_idx])
        except libusb1.USBError as e:
            raise usb_exceptions.WriteFailedError('Could not send data %s' % self.send_buffer, e)
        self.send_idx = 0

    def _ReadBuffered(self, size):
        """Read ``size`` bytes of data from ``self.recv_buffer``.

        .. image:: _static/adb.filesync_protocol.FileSyncConnection._ReadBuffered.CALLER_GRAPH.svg

        Parameters
        ----------
        size : int
            The amount of data to read

        Returns
        -------
        result : bytearray
            The read data

        """
        if TRUE:  # DEBUGGING, pragma: no cover
            print("\n\nParameters\n----------")  # DEBUGGING, pragma: no cover
            debug_print('size', size)  # DEBUGGING, pragma: no cover
            print("\n\n")  # DEBUGGING, pragma: no cover
        # Ensure recv buffer has enough data.
        while len(self.recv_buffer) < size:
            _, data = self.adb.ReadUntil(b'WRTE')
            self.recv_buffer += data

        result = self.recv_buffer[:size]
        self.recv_buffer = self.recv_buffer[size:]
        if TRUE:  # DEBUGGING, pragma: no cover
            print("\n\nReturns\n-------")  # DEBUGGING, pragma: no cover
            debug_print('result', result)  # DEBUGGING, pragma: no cover
            print("\n\n")  # DEBUGGING, pragma: no cover
        return result
