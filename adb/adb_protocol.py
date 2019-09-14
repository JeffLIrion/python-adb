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

* :class:`_AdbConnection`

    * :meth:`_AdbConnection._Send`
    * :meth:`_AdbConnection.Close`
    * :meth:`_AdbConnection.Okay`
    * :meth:`_AdbConnection.ReadUntil`
    * :meth:`_AdbConnection.ReadUntilClose`
    * :meth:`_AdbConnection.Write`

* :class:`AdbMessage`

    * :meth:`AdbMessage.CalculateChecksum`
    * :meth:`AdbMessage.checksum`
    * :meth:`AdbMessage.Command`
    * :meth:`AdbMessage.Connect`
    * :meth:`AdbMessage.InteractiveShellCommand`
    * :meth:`AdbMessage.Open`
    * :meth:`AdbMessage.Pack`
    * :meth:`AdbMessage.Read`
    * :meth:`AdbMessage.Send`
    * :meth:`AdbMessage.StreamingCommand`
    * :meth:`AdbMessage.Unpack`

* :class:`AuthSigner`

    * :meth:`AuthSigner.GetPublicKey`
    * :meth:`AuthSigner.Sign`

* :func:`find_backspace_runs`
* :class:`InterleavedDataError`
* :class:`InvalidChecksumError`
* :class:`InvalidCommandError`
* :class:`InvalidResponseError`
* :func:`MakeWireIDs`

"""

import struct
import time
from io import BytesIO
from adb import usb_exceptions


#: Maximum amount of data in an ADB packet.
MAX_ADB_DATA = 4096

#: ADB protocol version.
VERSION = 0x01000000

#: AUTH constants for arg0.
AUTH_TOKEN = 1

#: AUTH constants for arg0.
AUTH_SIGNATURE = 2

#: AUTH constants for arg0.
AUTH_RSAPUBLICKEY = 3


class InvalidCommandError(Exception):
    """Got an invalid command over USB.

    .. image:: _static/adb.adb_protocol.InvalidCommandError.CALL_GRAPH.svg

    .. image:: _static/adb.adb_protocol.InvalidCommandError.__init__.CALLER_GRAPH.svg

    """
    def __init__(self, message, response_header, response_data):
        if response_header == b'FAIL':
            message = 'Command failed, device said so. (%s)' % message
        super(InvalidCommandError, self).__init__(message, response_header, response_data)


class InvalidResponseError(Exception):
    """Got an invalid response to our command.

    .. image:: _static/adb.adb_protocol.InvalidResponseError.CALL_GRAPH.svg

    """


class InvalidChecksumError(Exception):
    """Checksum of data didn't match expected checksum.

    .. image:: _static/adb.adb_protocol.InvalidChecksumError.CALL_GRAPH.svg

    """


class InterleavedDataError(Exception):
    """We only support command sent serially.

    .. image:: _static/adb.adb_protocol.InterleavedDataError.CALL_GRAPH.svg

    """


def find_backspace_runs(stdout_bytes, start_pos):
    """TODO

    .. image:: _static/adb.adb_protocol.find_backspace_runs.CALLER_GRAPH.svg

    Parameters
    ----------
    stdout_bytes : TODO
        TODO
    start_pos : TODO
        TODO

    Returns
    -------
    int
        The index/position of the first backspace.
    num_backspaces : int
        TODO

    """
    first_backspace_pos = stdout_bytes[start_pos:].find(b'\x08')
    if first_backspace_pos == -1:
        return -1, 0

    end_backspace_pos = (start_pos + first_backspace_pos) + 1
    while True:
        if chr(stdout_bytes[end_backspace_pos]) == '\b':
            end_backspace_pos += 1
        else:
            break

    num_backspaces = end_backspace_pos - (start_pos + first_backspace_pos)

    return (start_pos + first_backspace_pos), num_backspaces


def MakeWireIDs(ids):
    """TODO

    Parameters
    ----------
    ids : list[bytes]
        TODO

    Returns
    -------
    id_to_wire : TODO
        TODO
    wire_to_id : TODO
        TODO

    """
    id_to_wire = {
        cmd_id: sum(c << (i * 8) for i, c in enumerate(bytearray(cmd_id)))
        for cmd_id in ids
    }
    wire_to_id = {wire: cmd_id for cmd_id, wire in id_to_wire.items()}
    return id_to_wire, wire_to_id


class AuthSigner(object):
    """Signer for use with authenticated ADB, introduced in 4.4.x/KitKat."""

    def Sign(self, data):
        """Signs given data using a private key.

        Parameters
        ----------
        data : TODO
            TODO

        Raises
        ------
        NotImplementedError
            This method is implemented in subclasses.

        """
        raise NotImplementedError()

    def GetPublicKey(self):
        """Returns the public key in PEM format without headers or newlines.

        Raises
        ------
        NotImplementedError
            This method is implemented in subclasses.

        """
        raise NotImplementedError()


class _AdbConnection(object):
    """ADB Connection.

    .. image:: _static/adb.adb_protocol._AdbConnection.__init__.CALLER_GRAPH.svg

    Parameters
    ----------
    usb : TODO
        TODO
    local_id : TODO
        TODO
    remote_id : TODO
        TODO
    timeout_ms : TODO
        TODO

    Attributes
    ----------
    local_id : TODO
        TODO
    remote_id : TODO
        TODO
    timeout_ms : TODO
        TODO
    usb : TODO
        TODO

    """
    def __init__(self, usb, local_id, remote_id, timeout_ms):
        self.usb = usb
        self.local_id = local_id
        self.remote_id = remote_id
        self.timeout_ms = timeout_ms

    def _Send(self, command, arg0, arg1, data=b''):
        """TODO

        .. image:: _static/adb.adb_protocol._AdbConnection._Send.CALLER_GRAPH.svg

        Parameters
        ----------
        command : TODO
            TODO
        arg0 : TODO
            TODO
        arg1 : TODO
            TODO
        data : bytes
            TODO

        """
        message = AdbMessage(command, arg0, arg1, data)
        message.Send(self.usb, self.timeout_ms)

    def Write(self, data):
        """Write a packet and expect an Ack.

        .. image:: _static/adb.adb_protocol._AdbConnection.Write.CALL_GRAPH.svg

        Parameters
        ----------
        data : TODO
            TODO

        Returns
        -------
        int
            ``len(data)``

        Raises
        ------
        usb_exceptions.AdbCommandFailureException
            The command failed.
        adb.adb_protocol.InvalidCommandError
            Expected an OKAY in response to a WRITE, got something else.

        """
        self._Send(b'WRTE', arg0=self.local_id, arg1=self.remote_id, data=data)
        # Expect an ack in response.
        cmd, okay_data = self.ReadUntil(b'OKAY')
        if cmd != b'OKAY':
            if cmd == b'FAIL':
                raise usb_exceptions.AdbCommandFailureException(
                    'Command failed.', okay_data)
            raise InvalidCommandError('Expected an OKAY in response to a WRITE, got {0} ({1})'.format(cmd, okay_data), cmd, okay_data)
        return len(data)

    def Okay(self):
        """TODO

        .. image:: _static/adb.adb_protocol._AdbConnection.Okay.CALL_GRAPH.svg

        .. image:: _static/adb.adb_protocol._AdbConnection.Okay.CALLER_GRAPH.svg

        """
        self._Send(b'OKAY', arg0=self.local_id, arg1=self.remote_id)

    def ReadUntil(self, *expected_cmds):
        """Read a packet, Ack any write packets.

        .. image:: _static/adb.adb_protocol._AdbConnection.ReadUntil.CALL_GRAPH.svg

        .. image:: _static/adb.adb_protocol._AdbConnection.ReadUntil.CALLER_GRAPH.svg

        Parameters
        ----------
        *expected_cmds : TODO
            TODO

        Returns
        -------
        cmd : TODO
            TODO
        data : TODO
            TODO

        Raises
        ------
        adb.adb_protocol.InterleavedDataError
            We don't support multiple streams...
        adb.adb_protocol.InvalidResponseError
            Incorrect remote id.

        """
        cmd, remote_id, local_id, data = AdbMessage.Read(self.usb, expected_cmds, self.timeout_ms)

        if local_id not in (0, self.local_id):
            raise InterleavedDataError("We don't support multiple streams...")
        if remote_id not in (0, self.remote_id):
            raise InvalidResponseError('Incorrect remote id, expected {0} got {1}'.format(self.remote_id, remote_id))

        # Ack write packets.
        if cmd == b'WRTE':
            self.Okay()
        return cmd, data

    def ReadUntilClose(self):
        """Yield packets until a Close packet is received.

        .. image:: _static/adb.adb_protocol._AdbConnection.ReadUntilClose.CALL_GRAPH.svg

        Yields
        ------
        data : TODO
            TODO

        """
        while True:
            cmd, data = self.ReadUntil(b'CLSE', b'WRTE')
            if cmd == b'CLSE':
                self._Send(b'CLSE', arg0=self.local_id, arg1=self.remote_id)
                break
            if cmd != b'WRTE':
                if cmd == b'FAIL':
                    raise usb_exceptions.AdbCommandFailureException(
                        'Command failed.', data)
                raise InvalidCommandError('Expected a WRITE or a CLOSE, got {0} ({1})'.format(cmd, data), cmd, data)
            yield data

    def Close(self):
        """TODO

        .. image:: _static/adb.adb_protocol._AdbConnection.Close.CALL_GRAPH.svg

        .. image:: _static/adb.adb_protocol._AdbConnection.Close.CALLER_GRAPH.svg

        Raises
        ------
        usb_exceptions.AdbCommandFailureException
            Command failed.
        adb.adb_protocol.InvalidCommandError
            Expected a ``CLSE`` response but received something else.

        """
        self._Send(b'CLSE', arg0=self.local_id, arg1=self.remote_id)
        cmd, data = self.ReadUntil(b'CLSE')
        if cmd != b'CLSE':
            if cmd == b'FAIL':
                raise usb_exceptions.AdbCommandFailureException('Command failed.', data)
            raise InvalidCommandError('Expected a CLSE response, got {0} ({1})'.format(cmd, data), cmd, data)


class AdbMessage(object):
    """ADB Protocol and message class.

    .. rubric:: local_id/remote_id

    Turns out the documentation is host/device ambidextrous, so ``local_id`` is the id for 'the sender' and
    ``remote_id`` is for 'the recipient'. So since we're only on the host, we'll re-document with host_id and device_id:

    ::

      OPEN(host_id, 0, 'shell:XXX')
      READY/OKAY(device_id, host_id, '')
      WRITE(0, host_id, 'data')
      CLOSE(device_id, host_id, '')


    .. image:: _static/adb.adb_protocol.AdbMessage.__init__.CALLER_GRAPH.svg

    Parameters
    ----------
    command : bytes, None
        One of: ``[b'SYNC', b'CNXN', b'AUTH', b'OPEN', b'OKAY', b'CLSE', b'WRTE']``
    arg0 : TODO, None
        TODO
    arg1 : TODO, None
        TODO
    data : bytes
        TODO

    Attributes
    ----------
    command : int
        The value in :const:`AdbMessage.commands` that corresponds to the ``command`` parameter
    commands : dict
        A dictionary with keys ``[b'SYNC', b'CNXN', b'AUTH', b'OPEN', b'OKAY', b'CLSE', b'WRTE']``.
    connections : int
        TODO
    constants : dict
        A dictionary with values ``[b'SYNC', b'CNXN', b'AUTH', b'OPEN', b'OKAY', b'CLSE', b'WRTE']``.
    format : bytes
        The format for unpacking the ADB message.
    ids : list[bytes]
        ``[b'SYNC', b'CNXN', b'AUTH', b'OPEN', b'OKAY', b'CLSE', b'WRTE']``

    """

    ids = [b'SYNC', b'CNXN', b'AUTH', b'OPEN', b'OKAY', b'CLSE', b'WRTE']
    commands, constants = MakeWireIDs(ids)
    # An ADB message is 6 words in little-endian.
    format = b'<6I'

    connections = 0

    def __init__(self, command=None, arg0=None, arg1=None, data=b''):
        self.command = self.commands[command]
        self.magic = self.command ^ 0xFFFFFFFF
        self.arg0 = arg0
        self.arg1 = arg1
        self.data = data

    @property
    def checksum(self):
        """TODO

        .. image:: _static/adb.adb_protocol.AdbMessage.checksum.CALL_GRAPH.svg

        .. image:: _static/adb.adb_protocol.AdbMessage.checksum.CALLER_GRAPH.svg

        Returns
        -------
        TODO
            TODO

        """
        return self.CalculateChecksum(self.data)

    @staticmethod
    def CalculateChecksum(data):
        """TODO

        .. image:: _static/adb.adb_protocol.AdbMessage.CalculateChecksum.CALLER_GRAPH.svg

        Returns
        -------
        TODO
            TODO

        """
        # The checksum is just a sum of all the bytes. I swear.
        if isinstance(data, bytearray):
            total = sum(data)
        elif isinstance(data, bytes):
            if data and isinstance(data[0], bytes):
                # Python 2 bytes (str) index as single-character strings.
                total = sum(map(ord, data))
            else:
                # Python 3 bytes index as numbers (and PY2 empty strings sum() to 0)
                total = sum(data)
        else:
            # Unicode strings (should never see?)
            total = sum(map(ord, data))
        return total & 0xFFFFFFFF

    def Pack(self):
        """Returns this message in an over-the-wire format.

        .. image:: _static/adb.adb_protocol.AdbMessage.Pack.CALL_GRAPH.svg

        .. image:: _static/adb.adb_protocol.AdbMessage.Pack.CALLER_GRAPH.svg

        Returns
        -------
        bytes
            TODO

        """
        return struct.pack(self.format, self.command, self.arg0, self.arg1,
                           len(self.data), self.checksum, self.magic)

    @classmethod
    def Unpack(cls, message):
        """TODO

        .. image:: _static/adb.adb_protocol.AdbMessage.Unpack.CALLER_GRAPH.svg

        Parameters
        ----------
        message : TODO
            TODO

        Returns
        -------
        cmd : TODO
            TODO
        arg0 : TODO
            TODO
        arg1 : TODO
            TODO
        data_length : TODO
            TODO
        data_checksum : TODO
            TODO
        unused_magic : TODO
            TODO

        Raises
        ------
        ValueError
            Unable to unpack the ADB command.

        """
        try:
            cmd, arg0, arg1, data_length, data_checksum, unused_magic = struct.unpack(cls.format, message)
        except struct.error as e:
            raise ValueError('Unable to unpack ADB command.', cls.format, message, e)
        return cmd, arg0, arg1, data_length, data_checksum

    def Send(self, usb, timeout_ms=None):
        """Send this message over USB.

        .. image:: _static/adb.adb_protocol.AdbMessage.Send.CALL_GRAPH.svg

        Parameters
        ----------
        usb : TODO
            TODO
        timeout_ms : TODO, None
            TODO

        """
        usb.BulkWrite(self.Pack(), timeout_ms)
        usb.BulkWrite(self.data, timeout_ms)

    @classmethod
    def Read(cls, usb, expected_cmds, timeout_ms=None, total_timeout_ms=None):
        """Receive a response from the device.

        .. image:: _static/adb.adb_protocol.AdbMessage.Read.CALL_GRAPH.svg

        .. image:: _static/adb.adb_protocol.AdbMessage.Read.CALLER_GRAPH.svg

        Parameters
        ----------
        usb : TODO
            TODO
        expected_cmds : TODO
            TODO
        timeout_ms : TODO, None
            TODO
        total_timeout_ms : TODO, None
            TODO

        Returns
        -------
        command : TODO
            TODO
        arg0 : TODO
            TODO
        arg1 : TODO
            TODO
        bytes
            TODO

        Raises
        ------
        adb.adb_protocol.InvalidCommandError
            Unknown command *or* never got one of the expected responses.
        adb.adb_protocol.InvalidChecksumError
            Received checksum does not match the expected checksum.

        """
        total_timeout_ms = usb.Timeout(total_timeout_ms)
        start = time.time()
        while True:
            msg = usb.BulkRead(24, timeout_ms)
            cmd, arg0, arg1, data_length, data_checksum = cls.Unpack(msg)
            command = cls.constants.get(cmd)
            if not command:
                raise InvalidCommandError(
                    'Unknown command: %x' % cmd, cmd, (arg0, arg1))
            if command in expected_cmds:
                break

            if time.time() - start > total_timeout_ms:
                raise InvalidCommandError(
                    'Never got one of the expected responses (%s)' % expected_cmds,
                    cmd, (timeout_ms, total_timeout_ms))

        if data_length > 0:
            data = bytearray()
            while data_length > 0:
                temp = usb.BulkRead(data_length, timeout_ms)
                if len(temp) != data_length:
                    print(
                        "Data_length {} does not match actual number of bytes read: {}".format(data_length, len(temp)))
                data += temp

                data_length -= len(temp)

            actual_checksum = cls.CalculateChecksum(data)
            if actual_checksum != data_checksum:
                raise InvalidChecksumError('Received checksum {0} != {1}'.format(actual_checksum, data_checksum))
        else:
            data = b''
        return command, arg0, arg1, bytes(data)

    @classmethod
    def Connect(cls, usb, banner=b'notadb', rsa_keys=None, auth_timeout_ms=100):
        """Establish a new connection to the device.

        .. image:: _static/adb.adb_protocol.AdbMessage.Connect.CALL_GRAPH.svg

        Parameters
        ----------
        usb : TODO
            A USBHandle with ``BulkRead`` and ``BulkWrite`` methods.
        banner : str
            A string to send as a host identifier.
        rsa_keys : list[adb_protocol.AuthSigner]
            List of AuthSigner subclass instances to be used for authentication. The device can either accept one of
            these via the ``Sign`` method, or we will send the result of ``GetPublicKey`` from the first one if the
            device doesn't accept any of them.
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
        banner : TODO
            The device's reported banner. Always starts with the state (device, recovery, or sideload), sometimes
            includes information after a : with various product information.

        Raises
        ------
        adb.usb_exceptions.DeviceAuthError
            When the device expects authentication, but we weren't given any valid keys.
        adb.adb_protocol.InvalidResponseError
            When the device does authentication in an unexpected way.
        usb_exceptions.ReadFailedError
            TODO

        """
        # In py3, convert unicode to bytes. In py2, convert str to bytes.
        # It's later joined into a byte string, so in py2, this ends up kind of being a no-op.
        if isinstance(banner, str):
            banner = bytearray(banner, 'utf-8')

        msg = cls(
            command=b'CNXN', arg0=VERSION, arg1=MAX_ADB_DATA,
            data=b'host::%s\0' % banner)
        msg.Send(usb)
        cmd, arg0, arg1, banner = cls.Read(usb, [b'CNXN', b'AUTH'])
        if cmd == b'AUTH':
            if not rsa_keys:
                raise usb_exceptions.DeviceAuthError('Device authentication required, no keys available.')

            # Loop through our keys, signing the last 'banner' or token.
            for rsa_key in rsa_keys:
                if arg0 != AUTH_TOKEN:
                    raise InvalidResponseError('Unknown AUTH response: %s %s %s' % (arg0, arg1, banner))

                # Do not mangle the banner property here by converting it to a string
                signed_token = rsa_key.Sign(banner)
                msg = cls(command=b'AUTH', arg0=AUTH_SIGNATURE, arg1=0, data=signed_token)
                msg.Send(usb)
                cmd, arg0, unused_arg1, banner = cls.Read(usb, [b'CNXN', b'AUTH'])
                if cmd == b'CNXN':
                    return banner

            # None of the keys worked, so send a public key.
            msg = cls(
                command=b'AUTH', arg0=AUTH_RSAPUBLICKEY, arg1=0,
                data=rsa_keys[0].GetPublicKey() + b'\0')
            msg.Send(usb)
            try:
                cmd, arg0, unused_arg1, banner = cls.Read(usb, [b'CNXN'], timeout_ms=auth_timeout_ms)
            except usb_exceptions.ReadFailedError as e:
                if e.usb_error.value == -7:  # Timeout.
                    raise usb_exceptions.DeviceAuthError('Accept auth key on device, then retry.')

                raise

            # This didn't time-out, so we got a CNXN response.
            return banner
        return banner

    @classmethod
    def Open(cls, usb, destination, timeout_ms=None):
        """Opens a new connection to the device via an ``OPEN`` message.

        Not the same as the posix ``open`` or any other google3 Open methods.

        .. image:: _static/adb.adb_protocol.AdbMessage.Open.CALL_GRAPH.svg

        .. image:: _static/adb.adb_protocol.AdbMessage.Open.CALLER_GRAPH.svg

        Parameters
        ----------
        usb : TODO
            USB device handle with BulkRead and BulkWrite methods.
        destination : TODO
            The service:command string.
        timeout_ms : TODO, None
            Timeout in milliseconds for USB packets.

        Returns
        -------
        _AdbConnection, None
            The local connection id.

        Raises
        ------
        adb.adb_protocol.InvalidResponseError
            Wrong local_id sent to us.
        adb.adb_protocol.InvalidCommandError
            Didn't get a ready response.

        """
        local_id = 1
        msg = cls(
            command=b'OPEN', arg0=local_id, arg1=0,
            data=destination + b'\0')
        msg.Send(usb, timeout_ms)
        cmd, remote_id, their_local_id, _ = cls.Read(usb, [b'CLSE', b'OKAY'], timeout_ms=timeout_ms)

        if local_id != their_local_id:
            raise InvalidResponseError('Expected the local_id to be {}, got {}'.format(local_id, their_local_id))

        if cmd == b'CLSE':
            # Some devices seem to be sending CLSE once more after a request, this *should* handle it
            cmd, remote_id, their_local_id, _ = cls.Read(usb, [b'CLSE', b'OKAY'], timeout_ms=timeout_ms)
            # Device doesn't support this service.
            if cmd == b'CLSE':
                return None

        if cmd != b'OKAY':
            raise InvalidCommandError('Expected a ready response, got {}'.format(cmd),
                                      cmd, (remote_id, their_local_id))
        return _AdbConnection(usb, local_id, remote_id, timeout_ms)

    @classmethod
    def Command(cls, usb, service, command='', timeout_ms=None):
        """One complete set of USB packets for a single command.

        Sends ``service:command`` in a new connection, reading the data for the
        response. All the data is held in memory, large responses will be slow and
        can fill up memory.

        .. image:: _static/adb.adb_protocol.AdbMessage.Command.CALL_GRAPH.svg

        Parameters
        ----------
        usb : TODO
            USB device handle with BulkRead and BulkWrite methods.
        service : TODO
            The service on the device to talk to.
        command : str
            The command to send to the service.
        timeout_ms : TODO, None
            Timeout for USB packets, in milliseconds.

        Returns
        -------
        str
            The response from the service.

        Raises
        ------
        adb.adb_protocol.InterleavedDataError
            Multiple streams running over usb.
        adb.adb_protocol.InvalidCommandError
            Got an unexpected response command.

        """
        return ''.join(cls.StreamingCommand(usb, service, command, timeout_ms))

    @classmethod
    def StreamingCommand(cls, usb, service, command='', timeout_ms=None):
        """One complete set of USB packets for a single command.

        Sends ``service:command`` in a new connection, reading the data for the
        response. All the data is held in memory, large responses will be slow and
        can fill up memory.

        .. image:: _static/adb.adb_protocol.AdbMessage.StreamingCommand.CALL_GRAPH.svg

        .. image:: _static/adb.adb_protocol.AdbMessage.StreamingCommand.CALLER_GRAPH.svg

        Parameters
        ----------
        usb : TODO
            USB device handle with BulkRead and BulkWrite methods.
        service : TODO
            The service on the device to talk to.
        command : str
            The command to send to the service.
        timeout_ms : TODO, None
            Timeout for USB packets, in milliseconds.

        Yields
        ------
        str
            The responses from the service.

        Raises
        ------
        adb.adb_protocol.InterleavedDataError
            Multiple streams running over usb.
        adb.adb_protocol.InvalidCommandError
            Got an unexpected response command.

        """
        if not isinstance(command, bytes):
            command = command.encode('utf8')
        connection = cls.Open(
            usb, destination=b'%s:%s' % (service, command),
            timeout_ms=timeout_ms)
        for data in connection.ReadUntilClose():
            yield data.decode('utf8')

    @classmethod
    def InteractiveShellCommand(cls, conn, cmd=None, strip_cmd=True, delim=None, strip_delim=True, clean_stdout=True):
        """Retrieves stdout of the current InteractiveShell and sends a shell command if provided
        TODO: Should we turn this into a yield based function so we can stream all output?

        .. image:: _static/adb.adb_protocol.AdbMessage.InteractiveShellCommand.CALL_GRAPH.svg

        Parameters
        ----------
        conn : AdbConnection
            Instance of AdbConnection
        cmd : str, None
            Command to run on the target.
        strip_cmd : bool
            Strip command name from stdout.
        delim : TODO
            Delimiter to look for in the output to know when to stop expecting more output (usually the shell prompt)
        strip_delim : bool
            Strip the provided delimiter from the output
        clean_stdout : bool
            Cleanup the stdout stream of any backspaces and the characters that were deleted by the backspace

        Returns
        -------
        stdout : TODO
            The stdout from the shell command.

        """
        if delim is not None and not isinstance(delim, bytes):
            delim = delim.encode('utf-8')

        # Delimiter may be shell@hammerhead:/ $
        # The user or directory could change, making the delimiter somthing like root@hammerhead:/data/local/tmp $
        # Handle a partial delimiter to search on and clean up
        if delim:
            user_pos = delim.find(b'@')
            dir_pos = delim.rfind(b':/')
            if user_pos != -1 and dir_pos != -1:
                partial_delim = delim[user_pos:dir_pos + 1]  # e.g. @hammerhead:
            else:
                partial_delim = delim
        else:
            partial_delim = None

        stdout = ''
        stdout_stream = BytesIO()
        original_cmd = ''

        try:

            if cmd:
                original_cmd = str(cmd)
                cmd += '\r'  # Required. Send a carriage return right after the cmd
                cmd = cmd.encode('utf8')

                # Send the cmd raw
                conn.Write(cmd)

                if delim:
                    # Expect multiple WRTE cmds until the delim (usually terminal prompt) is detected

                    data = b''
                    while partial_delim not in data:
                        cmd, data = conn.ReadUntil(b'WRTE')
                        stdout_stream.write(data)

                else:
                    # Otherwise, expect only a single WRTE
                    cmd, data = conn.ReadUntil(b'WRTE')

                    # WRTE cmd from device will follow with stdout data
                    stdout_stream.write(data)

            else:

                # No cmd provided means we should just expect a single line from the terminal. Use this sparingly
                cmd, data = conn.ReadUntil(b'WRTE')
                if cmd == b'WRTE':
                    # WRTE cmd from device will follow with stdout data
                    stdout_stream.write(data)
                else:
                    print("Unhandled cmd: {}".format(cmd))

            cleaned_stdout_stream = BytesIO()
            if clean_stdout:
                stdout_bytes = stdout_stream.getvalue()

                bsruns = {}  # Backspace runs tracking
                next_start_pos = 0
                last_run_pos, last_run_len = find_backspace_runs(stdout_bytes, next_start_pos)

                if last_run_pos != -1 and last_run_len != 0:
                    bsruns.update({last_run_pos: last_run_len})
                    cleaned_stdout_stream.write(stdout_bytes[next_start_pos:(last_run_pos - last_run_len)])
                    next_start_pos += last_run_pos + last_run_len

                while last_run_pos != -1:
                    last_run_pos, last_run_len = find_backspace_runs(stdout_bytes[next_start_pos:], next_start_pos)

                    if last_run_pos != -1:
                        bsruns.update({last_run_pos: last_run_len})
                        cleaned_stdout_stream.write(stdout_bytes[next_start_pos:(last_run_pos - last_run_len)])
                        next_start_pos += last_run_pos + last_run_len

                cleaned_stdout_stream.write(stdout_bytes[next_start_pos:])

            else:
                cleaned_stdout_stream.write(stdout_stream.getvalue())

            stdout = cleaned_stdout_stream.getvalue()

            # Strip original cmd that will come back in stdout
            if original_cmd and strip_cmd:
                findstr = original_cmd.encode('utf-8') + b'\r\r\n'
                pos = stdout.find(findstr)
                while pos >= 0:
                    stdout = stdout.replace(findstr, b'')
                    pos = stdout.find(findstr)

                if b'\r\r\n' in stdout:
                    stdout = stdout.split(b'\r\r\n')[1]

            # Strip delim if requested
            # TODO: Handling stripping partial delims here - not a deal breaker the way we're handling it now
            if delim and strip_delim:
                stdout = stdout.replace(delim, b'')

            stdout = stdout.rstrip()

        except Exception as e:  # pylint: disable=broad-except
            print("InteractiveShell exception (most likely timeout): {}".format(e))

        return stdout
