import struct
import time
from io import BytesIO
from adb import usb_exceptions


MAX_ADB_DATA = 4096

VERSION = 0x01000000


AUTH_TOKEN = 1
AUTH_SIGNATURE = 2
AUTH_RSAPUBLICKEY = 3


def find_backspace_runs(stdout_bytes, start_pos):
    first_backspace_pos = stdout_bytes[start_pos:].find(b"\x08")
    if first_backspace_pos == -1:
        return -1, 0

    end_backspace_pos = (start_pos + first_backspace_pos) + 1
    while True:
        if chr(stdout_bytes[end_backspace_pos]) == "\b":
            end_backspace_pos += 1
        else:
            break

    num_backspaces = end_backspace_pos - (start_pos + first_backspace_pos)

    return (start_pos + first_backspace_pos), num_backspaces


class InvalidCommandError(Exception):
    def __init__(self, message, response_header, response_data):
        if response_header == b"FAIL":
            message = "Command failed, device said so. (%s)" % message
        super(InvalidCommandError, self).__init__(
            message, response_header, response_data
        )


class InvalidResponseError(Exception):


class InvalidChecksumError(Exception):


class InterleavedDataError(Exception):


def MakeWireIDs(ids):
    id_to_wire = {
        cmd_id: sum(c << (i * 8) for i, c in enumerate(bytearray(cmd_id)))
        for cmd_id in ids
    }
    wire_to_id = {wire: cmd_id for cmd_id, wire in id_to_wire.items()}
    return id_to_wire, wire_to_id


class AuthSigner(object):
    def Sign(self, data):

        raise NotImplementedError()

    def GetPublicKey(self):

        raise NotImplementedError()


class _AdbConnection(object):
    def __init__(self, usb, local_id, remote_id, timeout_ms):
        self.usb = usb
        self.local_id = local_id
        self.remote_id = remote_id
        self.timeout_ms = timeout_ms

    def _Send(self, command, arg0, arg1, data=b""):
        message = AdbMessage(command, arg0, arg1, data)
        message.Send(self.usb, self.timeout_ms)

    def Write(self, data):

        self._Send(b"WRTE", arg0=self.local_id, arg1=self.remote_id, data=data)

        cmd, okay_data = self.ReadUntil(b"OKAY")
        if cmd != b"OKAY":
            if cmd == b"FAIL":
                raise usb_exceptions.AdbCommandFailureException(
                    "Command failed.", okay_data
                )
            raise InvalidCommandError(
                "Expected an OKAY in response to a WRITE, got %s (%s)", cmd, okay_data
            )
        return len(data)

    def Okay(self):
        self._Send(b"OKAY", arg0=self.local_id, arg1=self.remote_id)

    def ReadUntil(self, *expected_cmds):

        cmd, remote_id, local_id, data = AdbMessage.Read(
            self.usb, expected_cmds, self.timeout_ms
        )
        if local_id != 0 and self.local_id != local_id:
            raise InterleavedDataError("We don't support multiple streams...")
        if remote_id != 0 and self.remote_id != remote_id:
            raise InvalidResponseError(
                "Incorrect remote id, expected %s got %s" % (self.remote_id, remote_id)
            )

        if cmd == b"WRTE":
            self.Okay()
        return cmd, data

    def ReadUntilClose(self):

        while True:
            cmd, data = self.ReadUntil(b"CLSE", b"WRTE")
            if cmd == b"CLSE":
                self._Send(b"CLSE", arg0=self.local_id, arg1=self.remote_id)
                break
            if cmd != b"WRTE":
                if cmd == b"FAIL":
                    raise usb_exceptions.AdbCommandFailureException(
                        "Command failed.", data
                    )
                raise InvalidCommandError(
                    "Expected a WRITE or a CLOSE, got %s (%s)", cmd, data
                )
            yield data

    def Close(self):
        self._Send(b"CLSE", arg0=self.local_id, arg1=self.remote_id)
        cmd, data = self.ReadUntil(b"CLSE")
        if cmd != b"CLSE":
            if cmd == b"FAIL":
                raise usb_exceptions.AdbCommandFailureException("Command failed.", data)
            raise InvalidCommandError(
                "Expected a CLSE response, got %s (%s)", cmd, data
            )


class AdbMessage(object):

    ids = [b"SYNC", b"CNXN", b"AUTH", b"OPEN", b"OKAY", b"CLSE", b"WRTE"]
    commands, constants = MakeWireIDs(ids)

    format = b"<6I"

    connections = 0

    def __init__(self, command=None, arg0=None, arg1=None, data=b""):
        self.command = self.commands[command]
        self.magic = self.command ^ 0xFFFFFFFF
        self.arg0 = arg0
        self.arg1 = arg1
        self.data = data

    @property
    def checksum(self):
        return self.CalculateChecksum(self.data)

    @staticmethod
    def CalculateChecksum(data):

        if isinstance(data, bytearray):
            total = sum(data)
        elif isinstance(data, bytes):
            if data and isinstance(data[0], bytes):

                total = sum(map(ord, data))
            else:

                total = sum(data)
        else:

            total = sum(map(ord, data))
        return total & 0xFFFFFFFF

    def Pack(self):

        return struct.pack(
            self.format,
            self.command,
            self.arg0,
            self.arg1,
            len(self.data),
            self.checksum,
            self.magic,
        )

    @classmethod
    def Unpack(cls, message):
        try:
            cmd, arg0, arg1, data_length, data_checksum, unused_magic = struct.unpack(
                cls.format, message
            )
        except struct.error as e:
            raise ValueError("Unable to unpack ADB command.", cls.format, message, e)
        return cmd, arg0, arg1, data_length, data_checksum

    def Send(self, usb, timeout_ms=None):

        usb.BulkWrite(self.Pack(), timeout_ms)
        usb.BulkWrite(self.data, timeout_ms)

    @classmethod
    def Read(cls, usb, expected_cmds, timeout_ms=None, total_timeout_ms=None):

        total_timeout_ms = usb.Timeout(total_timeout_ms)
        start = time.time()
        while True:
            msg = usb.BulkRead(24, timeout_ms)
            cmd, arg0, arg1, data_length, data_checksum = cls.Unpack(msg)
            command = cls.constants.get(cmd)
            if not command:
                raise InvalidCommandError(
                    "Unknown command: %x" % cmd, cmd, (arg0, arg1)
                )
            if command in expected_cmds:
                break

            if time.time() - start > total_timeout_ms:
                raise InvalidCommandError(
                    "Never got one of the expected responses (%s)" % expected_cmds,
                    cmd,
                    (timeout_ms, total_timeout_ms),
                )

        if data_length > 0:
            data = bytearray()
            while data_length > 0:
                temp = usb.BulkRead(data_length, timeout_ms)
                if len(temp) != data_length:
                    print(
                        "Data_length {} does not match actual number of bytes read: {}".format(
                            data_length, len(temp)
                        )
                    )
                data += temp

                data_length -= len(temp)

            actual_checksum = cls.CalculateChecksum(data)
            if actual_checksum != data_checksum:
                raise InvalidChecksumError(
                    "Received checksum %s != %s", (actual_checksum, data_checksum)
                )
        else:
            data = b""
        return command, arg0, arg1, bytes(data)

    @classmethod
    def Connect(cls, usb, banner=b"notadb", rsa_keys=None, auth_timeout_ms=100):

        if isinstance(banner, str):
            banner = bytearray(banner, "utf-8")

        msg = cls(
            command=b"CNXN",
            arg0=VERSION,
            arg1=MAX_ADB_DATA,
            data=b"host::%s\0" % banner,
        )
        msg.Send(usb)
        cmd, arg0, arg1, banner = cls.Read(usb, [b"CNXN", b"AUTH"])
        if cmd == b"AUTH":
            if not rsa_keys:
                raise usb_exceptions.DeviceAuthError(
                    "Device authentication required, no keys available."
                )

            for rsa_key in rsa_keys:
                if arg0 != AUTH_TOKEN:
                    raise InvalidResponseError(
                        "Unknown AUTH response: %s %s %s" % (arg0, arg1, banner)
                    )

                signed_token = rsa_key.Sign(banner)
                msg = cls(
                    command=b"AUTH", arg0=AUTH_SIGNATURE, arg1=0, data=signed_token
                )
                msg.Send(usb)
                cmd, arg0, unused_arg1, banner = cls.Read(usb, [b"CNXN", b"AUTH"])
                if cmd == b"CNXN":
                    return banner

            msg = cls(
                command=b"AUTH",
                arg0=AUTH_RSAPUBLICKEY,
                arg1=0,
                data=rsa_keys[0].GetPublicKey() + b"\0",
            )
            msg.Send(usb)
            try:
                cmd, arg0, unused_arg1, banner = cls.Read(
                    usb, [b"CNXN"], timeout_ms=auth_timeout_ms
                )
            except usb_exceptions.ReadFailedError as e:
                if e.usb_error.value == -7:
                    raise usb_exceptions.DeviceAuthError(
                        "Accept auth key on device, then retry."
                    )
                raise

            return banner
        return banner

    @classmethod
    def Open(cls, usb, destination, timeout_ms=None):

        local_id = 1
        msg = cls(command=b"OPEN", arg0=local_id, arg1=0, data=destination + b"\0")
        msg.Send(usb, timeout_ms)
        cmd, remote_id, their_local_id, _ = cls.Read(
            usb, [b"CLSE", b"OKAY"], timeout_ms=timeout_ms
        )
        if local_id != their_local_id:
            raise InvalidResponseError(
                "Expected the local_id to be {}, got {}".format(
                    local_id, their_local_id
                )
            )
        if cmd == b"CLSE":

            cmd, remote_id, their_local_id, _ = cls.Read(
                usb, [b"CLSE", b"OKAY"], timeout_ms=timeout_ms
            )

            if cmd == b"CLSE":
                return None
        if cmd != b"OKAY":
            raise InvalidCommandError(
                "Expected a ready response, got {}".format(cmd),
                cmd,
                (remote_id, their_local_id),
            )
        return _AdbConnection(usb, local_id, remote_id, timeout_ms)

    @classmethod
    def Command(cls, usb, service, command="", timeout_ms=None):

        return "".join(cls.StreamingCommand(usb, service, command, timeout_ms))

    @classmethod
    def StreamingCommand(cls, usb, service, command="", timeout_ms=None):

        if not isinstance(command, bytes):
            command = command.encode("utf8")
        connection = cls.Open(
            usb, destination=b"%s:%s" % (service, command), timeout_ms=timeout_ms
        )
        for data in connection.ReadUntilClose():
            yield data.decode("utf8")

    @classmethod
    def InteractiveShellCommand(
        cls,
        conn,
        cmd=None,
        strip_cmd=True,
        delim=None,
        strip_delim=True,
        clean_stdout=True,
    ):

        if delim is not None and not isinstance(delim, bytes):
            delim = delim.encode("utf-8")

        if delim:
            user_pos = delim.find(b"@")
            dir_pos = delim.rfind(b":/")
            if user_pos != -1 and dir_pos != -1:
                partial_delim = delim[user_pos : dir_pos + 1]
            else:
                partial_delim = delim
        else:
            partial_delim = None

        stdout = ""
        stdout_stream = BytesIO()
        original_cmd = ""

        try:

            if cmd:
                original_cmd = str(cmd)
                cmd += "\r"
                cmd = cmd.encode("utf8")

                bytes_written = conn.Write(cmd)

                if delim:

                    data = b""
                    while partial_delim not in data:
                        cmd, data = conn.ReadUntil(b"WRTE")
                        stdout_stream.write(data)

                else:

                    cmd, data = conn.ReadUntil(b"WRTE")

                    stdout_stream.write(data)

            else:

                cmd, data = conn.ReadUntil(b"WRTE")
                if cmd == b"WRTE":

                    stdout_stream.write(data)
                else:
                    print("Unhandled cmd: {}".format(cmd))

            cleaned_stdout_stream = BytesIO()
            if clean_stdout:
                stdout_bytes = stdout_stream.getvalue()

                bsruns = {}
                next_start_pos = 0
                last_run_pos, last_run_len = find_backspace_runs(
                    stdout_bytes, next_start_pos
                )

                if last_run_pos != -1 and last_run_len != 0:
                    bsruns.update({last_run_pos: last_run_len})
                    cleaned_stdout_stream.write(
                        stdout_bytes[next_start_pos : (last_run_pos - last_run_len)]
                    )
                    next_start_pos += last_run_pos + last_run_len

                while last_run_pos != -1:
                    last_run_pos, last_run_len = find_backspace_runs(
                        stdout_bytes[next_start_pos:], next_start_pos
                    )

                    if last_run_pos != -1:
                        bsruns.update({last_run_pos: last_run_len})
                        cleaned_stdout_stream.write(
                            stdout_bytes[next_start_pos : (last_run_pos - last_run_len)]
                        )
                        next_start_pos += last_run_pos + last_run_len

                cleaned_stdout_stream.write(stdout_bytes[next_start_pos:])

            else:
                cleaned_stdout_stream.write(stdout_stream.getvalue())

            stdout = cleaned_stdout_stream.getvalue()

            if original_cmd and strip_cmd:
                findstr = original_cmd.encode("utf-8") + b"\r\r\n"
                pos = stdout.find(findstr)
                while pos >= 0:
                    stdout = stdout.replace(findstr, b"")
                    pos = stdout.find(findstr)

                if b"\r\r\n" in stdout:
                    stdout = stdout.split(b"\r\r\n")[1]

            if delim and strip_delim:
                stdout = stdout.replace(delim, b"")

            stdout = stdout.rstrip()

        except Exception as e:
            print("InteractiveShell exception (most likely timeout): {}".format(e))

        return stdout
