"""All the goo needed to deal with X properties.

Everyone else should just use prop_set/prop_get with nice clean Python calling
conventions, and if you need more (un)marshalling smarts, add them here."""

import struct
import array
from cStringIO import StringIO
import gtk.gdk
import cairo
from wimpiggy.lowlevel import \
     XGetWindowProperty, XChangeProperty, PropertyError, \
     get_xatom, get_pyatom, get_xwindow, get_pywindow, const, \
     get_display_for, premultiply_argb_in_place
from wimpiggy.error import trap, XError
from wimpiggy.log import Logger
log = Logger()

def unsupported(*args):
    raise UnsupportedException

def _force_length(data, length):
    if len(data) != length:
        log.warn("Odd-lengthed prop, wanted %s bytes, got %s: %r"
                 % (length, len(data), data))
    # Zero-pad data
    data += "\0" * length
    return data[:length]

class WMSizeHints(object):
    def __init__(self, disp, data):
        data = _force_length(data, 18 * 4)
        (flags,
         pad1, pad2, pad3, pad4,
         min_width, min_height,
         max_width, max_height,
         width_inc, height_inc,
         min_aspect_num, min_aspect_denom,
         max_aspect_num, max_aspect_denom,
         base_width, base_height,
         win_gravity) = struct.unpack("@" + "I" * 18, data)
        #print repr(data)
        #print struct.unpack("@" + "i" * 18, data)
        # We only extract the pieces we care about:
        if flags & const["PMaxSize"]:
            self.max_size = (max_width, max_height)
        else:
            self.max_size = None
        if flags & const["PMinSize"]:
            self.min_size = (min_width, min_height)
        else:
            self.min_size = None
        if flags & const["PBaseSize"]:
            self.base_size = (base_width, base_height)
        else:
            self.base_size = None
        if flags & const["PResizeInc"]:
            self.resize_inc = (width_inc, height_inc)
        else:
            self.resize_inc = None
        if flags & const["PAspect"]:
            self.min_aspect = min_aspect_num * 1.0 / min_aspect_denom
            self.min_aspect_ratio = (min_aspect_num, min_aspect_denom)
            self.max_aspect = max_aspect_num * 1.0 / max_aspect_denom
            self.max_aspect_ratio = (max_aspect_num,  max_aspect_denom)
        else:
            self.min_aspect, self.max_aspect = (None, None)
            self.min_aspect_ratio, self.max_aspect_ratio = (None, None)

class WMHints(object):
    def __init__(self, disp, data):
        data = _force_length(data, 9 * 4)
        (flags, input, initial_state,
         icon_pixmap, icon_window, icon_x, icon_y, icon_mask,
         window_group) = struct.unpack("@" + "i" * 9, data)
        # NB the last field is missing from at least some ICCCM 2.0's (typo).
        # FIXME: extract icon stuff too
        self.urgency = bool(flags & const["XUrgencyHint"])
        if flags & const["WindowGroupHint"]:
            self.group_leader = window_group
        else:
            self.group_leader = None
        if flags & const["StateHint"]:
            self.start_iconic = (initial_state == const["IconicState"])
        else:
            self.start_iconic = None
        if flags & const["InputHint"]:
            self.input = input
        else:
            self.input = None

class NetWMStrut(object):
    def __init__(self, disp, data):
        # This eats both _NET_WM_STRUT and _NET_WM_STRUT_PARTIAL.  If we are
        # given a _NET_WM_STRUT instead of a _NET_WM_STRUT_PARTIAL, then it
        # will be only length 4 instead of 12, but _force_length will zero-pad
        # and _NET_WM_STRUT is *defined* as a _NET_WM_STRUT_PARTIAL where the
        # extra fields are zero... so it all works out.
        data = _force_length(data, 4 * 12)
        (self.left, self.right, self.top, self.bottom,
         self.left_start_y, self.left_end_y,
         self.right_start_y, self.right_end_y,
         self.top_start_x, self.top_end_x,
         self.bottom_start_x, self.bottom_stop_x,
         ) = struct.unpack("@" + "I" * 12, data)

def _read_image(disp, stream):
    try:
        header = stream.read(2 * 4)
        if not header:
            return None
        (width, height) = struct.unpack("@II", header)
        bytes = stream.read(width * height * 4)
        if len(bytes) < width * height * 4:
            log.warn("Corrupt _NET_WM_ICON")
            return None
    except Exception, e:
        log.warn("Weird corruption in _NET_WM_ICON: %s", e)
        return None
    # Cairo wants a native-endian array here, and since the icon is
    # transmitted as CARDINALs, that's what we get. It might seem more
    # sensible to use ImageSurface.create_for_data (at least it did to me!)
    # but then you end up with a surface that refers to the memory you pass in
    # directly, and also .get_data() doesn't work on it, and it breaks the
    # test suite and blah. This at least works, as odd as it is:
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    surf.get_data()[:] = bytes
    # Cairo uses premultiplied alpha. EWMH actually doesn't specify what it
    # uses, but apparently the de-facto standard is non-premultiplied. (At
    # least that's what Compiz's sources say.)
    premultiply_argb_in_place(surf.get_data())
    return (width * height, surf)

# This returns a cairo ImageSurface which contains the largest icon defined in
# a _NET_WM_ICON property.
def NetWMIcons(disp, data):
    icons = []
    stream = StringIO(data)
    while True:
        size_image = _read_image(disp, stream)
        if size_image is None:
            break
        icons.append(size_image)
    if not icons:
        return None
    icons.sort()
    return icons[-1][1]

def _get_atom(disp, d):
    return str(get_pyatom(disp, struct.unpack("@I", d)[0]))

_prop_types = {
    # Python type, X type Atom, format, serializer, deserializer, list
    # terminator
    "utf8": (unicode, "UTF8_STRING", 8,
             lambda disp, u: u.encode("UTF-8"),
             lambda disp, d: d.decode("UTF-8"),
             "\0"),
    # In theory, there should be something clever about COMPOUND_TEXT here.  I
    # am not sufficiently clever to deal with COMPOUNT_TEXT.  Even knowing
    # that Xutf8TextPropertyToTextList exists.
    "latin1": (unicode, "STRING", 8,
               lambda disp, u: u.encode("latin1"),
               lambda disp, d: d.decode("latin1"),
               "\0"),
    "atom": (str, "ATOM", 32,
             lambda disp, a: struct.pack("@I", get_xatom(disp, a)),
              _get_atom,
             ""),
    "u32": ((int, long), "CARDINAL", 32,
            lambda disp, c: struct.pack("@I", c),
            lambda disp, d: struct.unpack("@I", d)[0],
            ""),
    "window": (gtk.gdk.Window, "WINDOW", 32,
               lambda disp, c: struct.pack("@I", get_xwindow(c)),
               lambda disp, d: get_pywindow(disp, struct.unpack("@I", d)[0]),
               ""),
    "wm-size-hints": (WMSizeHints, "WM_SIZE_HINTS", 32,
                      unsupported,
                      WMSizeHints,
                      None),
    "wm-hints": (WMHints, "WM_HINTS", 32,
                 unsupported,
                 WMHints,
                 None),
    "strut": (NetWMStrut, "CARDINAL", 32,
              unsupported, NetWMStrut, None),
    "strut-partial": (NetWMStrut, "CARDINAL", 32,
                      unsupported, NetWMStrut, None),
    "icon": (cairo.ImageSurface, "CARDINAL", 32,
             unsupported, NetWMIcons, None),
    # For uploading ad-hoc instances of the above complex structures to the
    # server, so we can test reading them out again:
    "debug-CARDINAL": (str, "CARDINAL", 32,
                       lambda disp, c: c,
                       lambda disp, d: d,
                       None),
    # For fetching the extra information on a MULTIPLE clipboard conversion
    # request. The exciting thing about MULTIPLE is that it's not actually
    # specified what 'type' one should use; you just fetch with
    # AnyPropertyType and assume that what you get is a bunch of pairs of
    # atoms.
    "multiple-conversion": (str, 0, 32, unsupported, _get_atom, None),
    }

def _prop_encode(disp, type, value):
    if isinstance(type, list):
        return _prop_encode_list(disp, type[0], value)
    else:
        return _prop_encode_scalar(disp, type, value)

def _prop_encode_scalar(disp, type, value):
    (pytype, atom, format, serialize, deserialize, terminator) = _prop_types[type]
    assert isinstance(value, pytype)
    return (atom, format, serialize(disp, value))

def _prop_encode_list(disp, type, value):
    (pytype, atom, format, serialize, deserialize, terminator) = _prop_types[type]
    value = list(value)
    serialized = [_prop_encode_scalar(disp, type, v)[2] for v in value]
    # Strings in X really are null-separated, not null-terminated (ICCCM
    # 2.7.1, see also note in 4.1.2.5)
    return (atom, format, terminator.join(serialized))


def prop_set(target, key, type, value):
    trap.call_unsynced(XChangeProperty, target, key,
                       _prop_encode(target, type, value))


def _prop_decode(disp, type, data):
    if isinstance(type, list):
        return _prop_decode_list(disp, type[0], data)
    else:
        return _prop_decode_scalar(disp, type, data)

def _prop_decode_scalar(disp, type, data):
    (pytype, atom, format, serialize, deserialize, terminator) = _prop_types[type]
    value = deserialize(disp, data)
    assert value is None or isinstance(value, pytype)
    return value

def _prop_decode_list(disp, type, data):
    (pytype, atom, format, serialize, deserialize, terminator) = _prop_types[type]
    if terminator:
        datums = data.split(terminator)
    else:
        datums = []
        while data:
            datums.append(data[:(format // 8)])
            data = data[(format // 8):]
    props = [_prop_decode_scalar(disp, type, datum) for datum in datums]
    assert None not in props
    return props

# May return None.
def prop_get(target, key, type):
    if isinstance(type, list):
        scalar_type = type[0]
    else:
        scalar_type = type
    (pytype, atom, format, serialize, deserialize, terminator) = _prop_types[scalar_type]
    try:
        #print atom
        data = trap.call_synced(XGetWindowProperty, target, key, atom)
        #print atom, repr(data[:100])
    except (XError, PropertyError):
        log.info("Missing window or missing property or wrong property type %s (%s)",
                 key, type)
        return None
    try:
        return _prop_decode(target, type, data)
    except:
        log.warn("Error parsing property %s (type %s); this may be a\n"
                 + "  misbehaving application, or bug in Wimpiggy\n"
                 + "  Data: %r[...?]",
                 key, type, data[:100])
        raise
        return None
