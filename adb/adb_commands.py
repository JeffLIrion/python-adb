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
"""A libusb1-based ADB reimplementation.

ADB was giving us trouble with its client/server architecture, which is great
for users and developers, but not so great for reliable scripting. This will
allow us to more easily catch errors as Python exceptions instead of checking
random exit codes, and all the other great benefits from not going through
subprocess and a network socket.

All timeouts are in milliseconds.
"""

import io
import os
import socket
import posixpath

from adb import adb_protocol
from adb import common
from adb import filesync_protocol

# From adb.h
CLASS = 0xFF
SUBCLASS = 0x42
PROTOCOL = 0x01

# pylint: disable=invalid-name
DeviceIsAvailable = common.interface_matcher(CLASS, SUBCLASS, PROTOCOL)

try:
    # Imported locally to keep compatibility with previous code.
    from adb.sign_cryptography import CryptographySigner
except ImportError:
    # Ignore this error when cryptography is not installed, there are other options.
    pass


class AdbCommands(object):
    """Exposes adb-like methods for use.

    Some methods are more-pythonic and/or have more options.
    """
    protocol_handler = adb_protocol.AdbMessage
    filesync_handler = filesync_protocol.FilesyncProtocol

    def __init__(self):
        self.build_props = None
        self._handle = None
        self._device_state = None

        # Connection table tracks each open AdbConnection objects per service type for program functions
        # that choose to persist an AdbConnection object for their functionality, using
        # self._get_service_connection
        self._service_connections = {}

    def _reset(self):
        self.__init__()

    def _get_service_connection(self, service, service_command=None, create=True, timeout_ms=None):
        """Based on the service, get the AdbConnection for that service or create one if it doesnt exist

        Parameters
        ----------
        service : todo
            TODO
        service_command : TODO, None
            Additional service parameters to append
        create : bool
            If False, dont create a connection if it does not exist
        timeout_ms : TODO, None
            TODO

        Returns
        -------
        connection : TODO
            TODO

        """
        connection = self._service_connections.get(service, None)

        if connection:
            return connection

        if not connection and not create:
            return None

        if service_command:
            destination_str = b'%s:%s' % (service, service_command)
        else:
            destination_str = service

        connection = self.protocol_handler.open(
            self._handle, destination=destination_str, timeout_ms=timeout_ms)

        self._service_connections.update({service: connection})

        return connection

    def connect_device(self, port_path=None, serial=None, default_timeout_ms=None, **kwargs):
        """Convenience function to setup a transport handle for the adb device from usb path or serial then connect to it.

        Parameters
        ----------
        port_path : TODO, None
            The filename of usb port to use.
        serial : TODO, None
            The serial number of the device to use; If serial specifies a TCP address:port, then a TCP connection is
            used instead of a USB connection.
        default_timeout_ms : TODO, None
            The default timeout in milliseconds to use.
        handle : common.TcpHandle, common.UsbHandle
            Device handle to use
        banner : TODO
            Connection banner to pass to the remote device
        rsa_keys : list
            List of `AuthSigner` subclass instances to be used for authentication. The device can either accept one of
            these via the `Sign` method, or we will send the result of `GetPublicKey` from the first one if the device
            doesn't accept any of them.
        auth_timeout_ms : TODO
            Timeout to wait for when sending a new public key. This  is only relevant when we send a new public key. The
            device shows a dialog and this timeout is how long to wait for that dialog. If used in automation, this
            should be low to catch such a case as a failure quickly; while in interactive settings it should be high to
            allow users to accept the dialog. We default to automation here, so it's low by default.

        """
        # If there isn't a handle override (used by tests), build one here
        if 'handle' in kwargs:
            self._handle = kwargs.pop('handle')
        else:
            # if necessary, convert serial to a unicode string
            if isinstance(serial, (bytes, bytearray)):
                serial = serial.decode('utf-8')

            if serial and ':' in serial:
                self._handle = common.TcpHandle(serial, timeout_ms=default_timeout_ms)
            else:
                self._handle = common.UsbHandle.find_and_open(
                    DeviceIsAvailable, port_path=port_path, serial=serial,
                    timeout_ms=default_timeout_ms)

        self._connect(**kwargs)

        return self

    def close(self):
        """TODO

        """
        for conn in list(self._service_connections.values()):
            if conn:
                try:
                    conn.close()
                except:
                    pass

        if self._handle:
            self._handle.close()

        self._reset()

    def _connect(self, banner=None, **kwargs):
        """Connect to the device.

        Parameters
        ----------
          banner: See protocol_handler.Connect.
          **kwargs: See protocol_handler.Connect and adb_commands.ConnectDevice for kwargs.
               Includes handle, rsa_keys, and auth_timeout_ms.
        Returns:
          An instance of this class if the device connected successfully.
        """

        if not banner:
            banner = socket.gethostname().encode()

        conn_str = self.protocol_handler.connect(self._handle, banner=banner, **kwargs)

        # Remove banner and colons after device state (state::banner)
        parts = conn_str.split(b'::')
        self._device_state = parts[0]

        # Break out the build prop info
        self.build_props = str(parts[1].split(b';'))

        return True

    @classmethod
    def devices(cls):
        """Get a generator of UsbHandle for devices available.

        Returns
        -------
        TODO
            TODO

        """
        return common.UsbHandle.find_devices(DeviceIsAvailable)

    def get_state(self):
        """TODO

        Returns
        -------
        TODO
            TODO

        """
        return self._device_state

    def install(self, apk_path, destination_dir='', replace_existing=True, grant_permissions=False, timeout_ms=None,
                transfer_progress_callback=None):
        """Install an apk to the device.

        Doesn't support verifier file, instead allows destination directory to be
        overridden.

        Parameters
        ----------
        apk_path : TODO
            Local path to apk to install.
        destination_dir : str
            Optional destination directory. Use ``'/system/app/'`` for persistent applications.
        replace_existing : bool
            Whether to replace existing application
        grant_permissions : bool
            If True, grant all permissions to the app specified in its manifest
        timeout_ms : TODO, None
            Expected timeout for pushing and installing.
        transfer_progress_callback : TODO, None
            callback method that accepts filename, bytes_written and total_bytes of APK transfer

        Returns
        -------
        TODO
            The pm install output.

        """
        if not destination_dir:
            destination_dir = '/data/local/tmp/'
        basename = os.path.basename(apk_path)
        destination_path = posixpath.join(destination_dir, basename)
        self.push(apk_path, destination_path, timeout_ms=timeout_ms, progress_callback=transfer_progress_callback)

        cmd = ['pm install']
        if grant_permissions:
            cmd.append('-g')
        if replace_existing:
            cmd.append('-r')
        cmd.append('"{}"'.format(destination_path))

        ret = self.shell(' '.join(cmd), timeout_ms=timeout_ms)

        # Remove the apk
        rm_cmd = ['rm', destination_path]
        rmret = self.shell(' '.join(rm_cmd), timeout_ms=timeout_ms)

        return ret

    def uninstall(self, package_name, keep_data=False, timeout_ms=None):
        """Removes a package from the device.

        Parameters
        ----------
        package_name : TODO
            Package name of target package.
        keep_data : bool
            Whether to keep the data and cache directories
        timeout_ms : TODO, None
            Expected timeout for pushing and installing.

        Returns
        -------
        TODO
            The ``pm uninstall`` output.

        """
        cmd = ['pm uninstall']
        if keep_data:
            cmd.append('-k')
        cmd.append('"%s"' % package_name)

        return self.shell(' '.join(cmd), timeout_ms=timeout_ms)

    def push(self, source_file, device_filename, mtime='0', timeout_ms=None, progress_callback=None, st_mode=None):
        """Push a file or directory to the device.

        Parameters
        ----------
        source_file : TODO
            Either a filename, a directory or file-like object to push to the device.
        device_filename : TODO
            Destination on the device to write to.
        mtime : str
            Modification time to set on the file.
        timeout_ms : TODO, None
            Expected timeout for any part of the push.
        progress_callback : TODO, None
            Callback method that accepts filename, bytes_written and total_bytes; total_bytes will be -1 for file-like
            objects.
        st_mode : TODO, None
            Stat mode for filename

        """

        if isinstance(source_file, str):
            if os.path.isdir(source_file):
                self.shell("mkdir " + device_filename)
                for f in os.listdir(source_file):
                    self.push(os.path.join(source_file, f), device_filename + '/' + f,
                              progress_callback=progress_callback)
                return
            source_file = open(source_file, "rb")

        with source_file:
            connection = self.protocol_handler.open(
                self._handle, destination=b'sync:', timeout_ms=timeout_ms)
            kwargs={}
            if st_mode is not None:
                kwargs['st_mode'] = st_mode
            self.filesync_handler.push(connection, source_file, device_filename,
                                       mtime=int(mtime), progress_callback=progress_callback, **kwargs)
        connection.close()

    def pull(self, device_filename, dest_file=None, timeout_ms=None, progress_callback=None):
        """Pull a file from the device.

        Parameters
        ----------
        device_filename : TODO
            Filename on the device to pull.
        dest_file : None, TODO
            If set, a filename or writable file-like object.
        timeout_ms : None, TODO
            Expected timeout for any part of the pull.
        progress_callback : TODO, None
            Callback method that accepts filename, bytes_written and total_bytes; total_bytes will be -1 for file-like
            objects.

        Returns
        -------
        bool
            The file data if ``dest_file`` is not set. Otherwise, True if the destination file exists.

        """
        if not dest_file:
            dest_file = io.BytesIO()
        elif isinstance(dest_file, str):
            dest_file = open(dest_file, 'wb')
        elif isinstance(dest_file, file):
            pass
        else:
            raise ValueError("destfile is of unknown type")

        conn = self.protocol_handler.open(
            self._handle, destination=b'sync:', timeout_ms=timeout_ms)

        self.filesync_handler.pull(conn, device_filename, dest_file, progress_callback)

        conn.close()
        if isinstance(dest_file, io.BytesIO):
            return dest_file.getvalue()
        else:
            dest_file.close()
            if hasattr(dest_file, 'name'):
                return os.path.exists(dest_file.name)
            # We don't know what the path is, so we just assume it exists.
            return True

    def stat(self, device_filename):
        """Get a file's ``stat()`` information.

        Parameters
        ----------
        device_filename : TODO
            TODO

        Returns
        -------
        mode : TODO
            TODO
        size : TODO
            TODO
        mtime : TODO
            TODO

        """
        connection = self.protocol_handler.open(self._handle, destination=b'sync:')
        mode, size, mtime = self.filesync_handler.stat(
            connection, device_filename)
        connection.close()
        return mode, size, mtime

    def ls(self, device_path):
        """Return a directory listing of the given path.

        Parameters
        ----------
          device_path: Directory to list.
        """
        connection = self.protocol_handler.open(self._handle, destination=b'sync:')
        listing = self.filesync_handler.ls(connection, device_path)
        connection.close()
        return listing

    def reboot(self, destination=b''):
        """Reboot the device.

        Parameters
        ----------
          destination: Specify 'bootloader' for fastboot.
        """
        self.protocol_handler.open(self._handle, b'reboot:%s' % destination)

    def reboot_bootloader(self):
        """Reboot device into fastboot."""
        self.reboot(b'bootloader')

    def remount(self):
        """Remount / as read-write.

        Returns
        -------
        TODO
            TODO

        """
        return self.protocol_handler.command(self._handle, service=b'remount')

    def root(self):
        """Restart adbd as root on the device.

        Returns
        -------
        TODO
            TODO

        """
        return self.protocol_handler.command(self._handle, service=b'root')

    def enable_verity(self):
        """Re-enable dm-verity checking on userdebug builds.

        Returns
        -------
        TODO
            TODO

        """
        return self.protocol_handler.command(self._handle, service=b'enable-verity')

    def disable_verity(self):
        """Disable dm-verity checking on userdebug builds.

        Returns
        -------
        TODO
            TODO

        """
        return self.protocol_handler.command(self._handle, service=b'disable-verity')

    def shell(self, command, timeout_ms=None):
        """Run command on the device, returning the output.

        Parameters
        ----------
        command : TODO
            Shell command to run
        timeout_ms : TODO, None
            Maximum time to allow the command to run.

        Returns
        -------
        TODO
            TODO

        """
        return self.protocol_handler.command(
            self._handle, service=b'shell', command=command,
            timeout_ms=timeout_ms)

    def streaming_shell(self, command, timeout_ms=None):
        """Run command on the device, yielding each line of output.

        Parameters
        ----------
        command : TODO
            Command to run on the target.
        timeout_ms : TODO, None
            Maximum time to allow the command to run.

        Returns
        -------
        TODO
            The responses from the shell command.

        """
        return self.protocol_handler.streaming_command(
            self._handle, service=b'shell', command=command,
            timeout_ms=timeout_ms)

    def logcat(self, options, timeout_ms=None):
        """Run ``shell logcat`` and stream the output to stdout.

        Parameters
        ----------
        options : TODO
            Arguments to pass to ``logcat``.
        timeout_ms : TODO, None
            Maximum time to allow the command to run.

        Returns
        -------
        TODO
            TODO

        """
        return self.streaming_shell('logcat %s' % options, timeout_ms)

    def interactive_shell(self, cmd=None, strip_cmd=True, delim=None, strip_delim=True):
        """Get stdout from the currently open interactive shell and optionally run a  on the device, returning all
        output.

        Parameters
        ----------
        cmd : TODO, None
            Command to run on the target.
        strip_cmd : bool
            Strip command name from stdout.
        delim : TODO, None
            Delimiter to look for in the output to know when to stop expecting more output (usually the shell prompt).
        strip_delim : bool
            Strip the provided delimiter from the output

        Returns
        -------
        TODO
            The stdout from the shell command.

        """
        conn = self._get_service_connection(b'shell:')

        return self.protocol_handler.interactive_shell_command(
            conn, cmd=cmd, strip_cmd=strip_cmd,
            delim=delim, strip_delim=strip_delim)
