import print_function
import argparse
import io
import inspect
import logging
import re
import sys
import types

from adb import usb_exceptions


class _PortPathAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(
            namespace, self.dest, [int(i) for i in values.replace("/", ",").split(",")]
        )


class PositionalArg(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        namespace.positional.append(values)


def GetDeviceArguments():
    group = argparse.ArgumentParser("Device", add_help=False)
    group.add_argument(
        "--timeout_ms",
        default=10000,
        type=int,
        metavar="10000",
        help="Timeout in milliseconds.",
    )
    group.add_argument(
        "--port_path",
        action=_PortPathAction,
        help="USB port path integers (eg 1,2 or 2,1,1)",
    )
    group.add_argument(
        "-s", "--serial", help="Device serial to look for (host:port or USB serial)"
    )
    return group


def GetCommonArguments():
    group = argparse.ArgumentParser("Common", add_help=False)
    group.add_argument("--verbose", action="store_true", help="Enable logging")
    return group


def _DocToArgs(doc):
    m = None
    offset = None
    in_arg = False
    out = {}
    for l in doc.splitlines():
        if l.strip() == "Args:":
            in_arg = True
        elif in_arg:
            if not l.strip():
                break
            if offset is None:
                offset = len(l) - len(l.lstrip())
            l = l[offset:]
            if l[0] == " " and m:
                out[m.group(1)] += " " + l.lstrip()
            else:
                m = re.match(r"^([a-z_]+): (.+)$", l.strip())
                out[m.group(1)] = m.group(2)
    return out


def MakeSubparser(subparsers, parents, method, arguments=None):
    name = ("-".join(re.split(r"([A-Z][a-z]+)", method.__name__)[1:-1:2])).lower()
    help = method.__doc__.splitlines()[0]
    subparser = subparsers.add_parser(
        name=name, description=help, help=help.rstrip("."), parents=parents
    )
    subparser.set_defaults(method=method, positional=[])
    argspec = inspect.getargspec(method)
    offset = len(argspec.args) - len(argspec.defaults or []) - 1
    positional = []
    for i in range(1, len(argspec.args)):
        if i > offset and argspec.defaults[i - offset - 1] is None:
            break
        positional.append(argspec.args[i])
    defaults = [None] * offset + list(argspec.defaults or [])
    args_help = _DocToArgs(method.__doc__)
    for name, default in zip(positional, defaults):
        if not isinstance(default, (None.__class__, str)):
            continue
        subparser.add_argument(
            name,
            help=(arguments or {}).get(name, args_help.get(name)),
            default=default,
            nargs="?" if default is not None else None,
            action=PositionalArg,
        )
    if argspec.varargs:
        subparser.add_argument(
            argspec.varargs,
            nargs=argparse.REMAINDER,
            help=(arguments or {}).get(argspec.varargs, args_help.get(argspec.varargs)),
        )
    return subparser


def _RunMethod(dev, args, extra):
    logging.info("%s(%s)", args.method.__name__, ", ".join(args.positional))
    result = args.method(dev, *args.positional, **extra)
    if result is not None:
        if isinstance(result, io.StringIO):
            sys.stdout.write(result.getvalue())
        elif isinstance(result, (list, types.GeneratorType)):
            r = ""
            for r in result:
                r = str(r)
                sys.stdout.write(r)
            if not r.endswith("\n"):
                sys.stdout.write("\n")
        else:
            result = str(result)
            sys.stdout.write(result)
            if not result.endswith("\n"):
                sys.stdout.write("\n")
    return 0


def StartCli(args, adb_commands, extra=None, **device_kwargs):
    try:
        dev = adb_commands()
        dev.ConnectDevice(
            port_path=args.port_path,
            serial=args.serial,
            default_timeout_ms=args.timeout_ms,
            **device_kwargs
        )
    except usb_exceptions.DeviceNotFoundError as e:
        print("No device found: {}".format(e), file=sys.stderr)
        return 1
    except usb_exceptions.CommonUsbError as e:
        print("Could not connect to device: {}".format(e), file=sys.stderr)
        return 1
    try:
        return _RunMethod(dev, args, extra or {})
    except Exception as e:
        sys.stdout.write(str(e))
        return 1
    finally:
        dev.Close()
