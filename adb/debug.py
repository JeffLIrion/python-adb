"""TEMPORARY.

"""


TRUE = True  # pragma: no cover
FALSE = False  # pragma: no cover
PRINTABLE_TYPES = (int, float, str, bool, bytes, bytearray, type(None))  # pragma: no cover


def debug_print(name, var):  # pragma: no cover
    """Print debugging info."""
    if isinstance(var, PRINTABLE_TYPES):
        print("type({}) = {}, value = {}".format(name, type(var).__name__, var))
    elif isinstance(var, (tuple, list)):
        if not var:
            print("type({}) = {}, value = {}".format(name, type(var).__name__, var))
        elif isinstance(var[0], PRINTABLE_TYPES):
            print("type({}) = {}[{}], value = {}".format(name, type(var).__name__, type(var[0]).__name__, var))
        else:
            print("type({}) = {}[{}]".format(name, type(var).__name__, type(var[0]).__name__))
    else:
        print("type({}) = {}".format(name, type(var).__name__))
