from parti.test import *
import parti.lowlevel as l
import gtk
from parti.error import *

class TestLowlevel(TestWithSession):
    def root(self, disp=None):
        if disp is None:
            disp = self.display
        return disp.get_default_screen().get_root_window()

    def window(self, disp=None):
        if disp is None:
            disp = self.display
        win = gtk.gdk.Window(self.root(disp), width=10, height=10,
                             # WINDOW_CHILD is sort of bogus, but it reduces
                             # the amount of magic that GDK will do (e.g.,
                             # WINDOW_TOPLEVELs automatically get a child
                             # window to be used in focus management).
                             window_type=gtk.gdk.WINDOW_CHILD,
                             wclass=gtk.gdk.INPUT_OUTPUT,
                             event_mask=0)
        return win

    def test_get_xwindow_pywindow(self):
        d2 = self.clone_display()
        r1 = self.root()
        r2 = self.root(d2)
        assert r1 is not r2
        assert l.get_xwindow(r1) == l.get_xwindow(r2)
        win = self.window()
        assert l.get_xwindow(r1) != l.get_xwindow(win)
        assert l.get_pywindow(r2, l.get_xwindow(r1)) is r2

        assert_raises(l.XError, l.get_pywindow, self.display, 0)

    def test_get_display_for(self):
        assert l.get_display_for(self.display) is self.display
        win = self.window()
        assert l.get_display_for(win) is self.display
        assert_raises(TypeError, l.get_display_for, None)
        widg = gtk.Window()
        assert l.get_display_for(widg) is self.display
        clipboard = gtk.Clipboard(self.display, "PRIMARY")
        assert l.get_display_for(clipboard) is self.display

    def test_get_xatom_pyatom(self):
        d2 = self.clone_display()
        asdf1 = l.get_xatom(self.display, "ASDF")
        asdf2 = l.get_xatom(d2, "ASDF")
        ghjk1 = l.get_xatom(self.display, "GHJK")
        ghjk2 = l.get_xatom(d2, "GHJK")
        assert asdf1 == asdf2
        assert ghjk1 == ghjk2
        assert l.get_pyatom(self.display, asdf2) == "ASDF"
        assert l.get_pyatom(d2, ghjk1) == "GHJK"
        
    def test_property(self):
        r = self.root()
        data = "\x01\x02\x03\x04\x05\x06\x07\x08"
        assert_raises(l.NoSuchProperty,
                      l.XGetWindowProperty, r, "ASDF", "ASDF")
        l.XChangeProperty(r, "ASDF", ("GHJK", 32, data))
        assert_raises(l.BadPropertyType,
                      l.XGetWindowProperty, r, "ASDF", "ASDF")

        for n in (8, 16, 32):
            print n
            l.XChangeProperty(r, "ASDF", ("GHJK", n, data))
            assert l.XGetWindowProperty(r, "ASDF", "GHJK") == data
        
        l.XDeleteProperty(r, "ASDF")
        assert_raises(l.NoSuchProperty,
                      l.XGetWindowProperty, r, "ASDF", "GHJK")

        badwin = self.window()
        badwin.destroy()
        assert_raises((l.PropertyError, XError),
                      trap.call, l.XGetWindowProperty, badwin, "ASDF", "ASDF")

        # Giant massive property
        l.XChangeProperty(r, "ASDF",
                          ("GHJK", 32, "\x00" * 512 * (2 ** 10)))
        assert_raises(l.PropertyOverflow,
                      l.XGetWindowProperty, r, "ASDF", "GHJK")

    def test_BadProperty_on_empty(self):
        win = self.window()
        l.XChangeProperty(win, "ASDF", ("GHJK", 32, ""))
        assert l.XGetWindowProperty(win, "ASDF", "GHJK") == ""
        assert_raises(l.BadPropertyType,
                      l.XGetWindowProperty, win, "ASDF", "ASDF")

    def test_get_children_and_reparent(self):
        d2 = self.clone_display()
        w1 = self.window(self.display)
        w2 = self.window(d2)
        gtk.gdk.flush()

        assert not l.get_children(w1)
        children = l.get_children(self.root())
        xchildren = map(l.get_xwindow, children)
        xwins = map(l.get_xwindow, [w1, w2])
        # GDK creates an invisible child of the root window on each
        # connection, so there are some windows we don't know about:
        for known in xwins:
            assert known in xchildren

        w1.reparent(l.get_pywindow(w1, l.get_xwindow(w2)), 0, 0)
        gtk.gdk.flush()
        assert map(l.get_xwindow, l.get_children(w2)) == [l.get_xwindow(w1)]

    def test_save_set(self):
        w1 = self.window(self.display)
        w2 = self.window(self.display)
        gtk.gdk.flush()
        
        import os
        def do_child(disp_name, xwindow1, xwindow2):
            d2 = gtk.gdk.Display(disp_name)
            w1on2 = l.get_pywindow(d2, xwindow1)
            w2on2 = l.get_pywindow(d2, xwindow2)
            mywin = self.window(d2)
            print "mywin == %s" % l.get_xwindow(mywin)
            w1on2.reparent(mywin, 0, 0)
            w2on2.reparent(mywin, 0, 0)
            gtk.gdk.flush()
            l.XAddToSaveSet(w1on2)
            gtk.gdk.flush()
            # But we don't XAddToSaveSet(w2on2)
        pid = os.fork()
        if not pid:
            # Child
            try:
                do_child(self.display.get_name(), l.get_xwindow(w1), l.get_xwindow(w2))
            finally:
                os._exit(0)
        # Parent
        os.waitpid(pid, 0)
        # Is there a race condition here, where the child exits but the X
        # server doesn't notice until after we send our commands?
        print map(l.get_xwindow, [w1, w2])
        print map(l.get_xwindow, l.get_children(self.root()))
        assert w1 in l.get_children(self.root())
        assert w2 not in l.get_children(self.root())

    def test_is_mapped(self):
        win = self.window()
        gtk.gdk.flush()
        assert not l.is_mapped(win)
        win.show()
        gtk.gdk.flush()
        assert l.is_mapped(win)

    def test_focus_stuff(self):
        w1 = self.window()
        w1.show()
        w2 = self.window()
        w2.show()
        gtk.gdk.flush()
        self.w1_got, self.w2_got = None, None
        self.w1_lost, self.w2_lost = None, None
        def in_callback(ev):
            if ev.window is w1:
                assert self.w1_got is None
                self.w1_got = ev
            else:
                assert self.w2_got is None
                self.w2_got = ev
            gtk.main_quit()
        def out_callback(ev):
            if ev.window is w1:
                assert self.w1_lost is None
                self.w1_lost = ev
            else:
                assert self.w2_lost is None
                self.w2_lost = ev
            gtk.main_quit()
        l.selectFocusChange(w1, in_callback, out_callback)
        l.selectFocusChange(w2, in_callback, out_callback)

        gtk.gdk.flush()
        l.XSetInputFocus(w1)
        gtk.gdk.flush()
        gtk.main()
        assert self.w1_got is not None
        assert self.w1_got.window is w1
        assert self.w1_got.mode == l.const["NotifyNormal"]
        assert self.w1_got.detail == l.const["NotifyNonlinear"]
        self.w1_got = None
        assert self.w2_got is None
        assert self.w1_lost is None
        assert self.w2_lost is None

        l.XSetInputFocus(w2)
        gtk.gdk.flush()
        gtk.main()
        gtk.main()
        assert self.w1_got is None
        assert self.w2_got is not None
        assert self.w2_got.window is w2
        assert self.w2_got.mode == l.const["NotifyNormal"]
        assert self.w2_got.detail == l.const["NotifyNonlinear"]
        self.w2_got = None
        assert self.w1_lost is not None
        assert self.w1_lost.window is w1
        assert self.w1_lost.mode == l.const["NotifyNormal"]
        assert self.w1_lost.detail == l.const["NotifyNonlinear"]
        self.w1_lost = None
        assert self.w2_lost is None

        l.XSetInputFocus(self.root())
        gtk.gdk.flush()
        gtk.main()
        assert self.w1_got is None
        assert self.w2_got is None
        assert self.w1_lost is None
        assert self.w2_lost is not None
        assert self.w2_lost.window is w2
        assert self.w2_lost.mode == l.const["NotifyNormal"]
        assert self.w2_lost.detail == l.const["NotifyAncestor"]
        self.w2_lost = None
        
    def test_select_clientmessage_and_xselectinput(self):
        root = self.root()
        win = self.window()
        gtk.gdk.flush()
        self.root_evs = []
        def root_callback(ev):
            print "root!"
            self.root_evs.append(ev)
            gtk.main_quit()
        self.win_evs = []
        def win_callback(ev):
            print "win!"
            self.win_evs.append(ev)
            gtk.main_quit()
        l.selectClientMessage(root, root_callback)
        l.selectClientMessage(win, win_callback)
        gtk.gdk.flush()

        data = (0x01020304, 0x05060708, 0x090a0b0c, 0x0d0e0f10, 0x11121314)
        l.sendClientMessage(root, False, 0, "NOMASK", *data)
        l.sendClientMessage(win, False, 0, "NOMASK", *data)
        gtk.main()
        assert not self.root_evs
        assert len(self.win_evs) == 1
        win_ev = self.win_evs[0]
        assert win_ev.window is win
        assert win_ev.message_type == "NOMASK"
        assert win_ev.format == 32
        assert win_ev.data == data

        self.win_evs = []
        l.sendClientMessage(root, False, l.const["Button1MotionMask"],
                            "BAD", *data)
        l.addXSelectInput(root, l.const["Button1MotionMask"])
        l.sendClientMessage(root, False, l.const["Button1MotionMask"],
                            "GOOD", *data)
        gtk.main()
        assert len(self.root_evs) == 1
        root_ev = self.root_evs[0]
        assert root_ev.window is root
        assert root_ev.message_type == "GOOD"
        assert root_ev.format == 32
        assert root_ev.data == data
        assert not self.win_evs

    def test_send_wm_take_focus(self):
        win = self.window()
        gtk.gdk.flush()
        self.event = None
        def callback(event):
            self.event = event
            gtk.main_quit()
        l.selectClientMessage(win, callback)
        l.send_wm_take_focus(win, 1234)
        gtk.main()
        assert self.event is not None
        assert self.event.window is win
        assert self.event.message_type == "WM_PROTOCOLS"
        assert self.event.format == 32
        assert self.event.data == (l.get_xatom(win, "WM_TAKE_FOCUS"),
                                   1234, 0, 0, 0)

    # myGetSelectionOwner gets tested in test_selection.py

    def test_substructure_redirect(self):
        root = self.root()
        d2 = self.clone_display()
        w2 = self.window(d2)
        gtk.gdk.flush()
        w1 = l.get_pywindow(self.display, l.get_xwindow(w2))

        self.map_ev = None
        def map_cb(ev):
            print "got map"
            self.map_ev = ev
            gtk.main_quit()
        self.conf_ev = None
        def conf_cb(ev):
            print "got conf"
            self.conf_ev = ev
            gtk.main_quit()
        l.substructureRedirect(root, map_cb, conf_cb)
        gtk.gdk.flush()

        # gdk_window_show does both a map and a configure (to raise the
        # window)
        print "showing w2"
        w2.show()
        # Can't just call gtk.main() twice, the two events may be delivered
        # together and processed in a single mainloop iteration.
        while None in (self.map_ev, self.conf_ev):
            gtk.main()

        assert self.map_ev.parent is root
        assert self.map_ev.window is w1

        assert self.conf_ev.parent is root
        assert self.conf_ev.window is w1
        for field in ("x", "y", "width", "height",
                      "border_width", "above", "detail", "value_mask"):
            print field
            assert hasattr(self.conf_ev, field)

        self.map_ev = None
        self.conf_ev = None
        w2.move_resize(1, 2, 3, 4)
        gtk.main()
        assert self.map_ev is None
        assert self.conf_ev is not None
        assert self.conf_ev.parent is root
        assert self.conf_ev.window is w1
        assert self.conf_ev.x == 1
        assert self.conf_ev.y == 2
        assert self.conf_ev.width == 3
        assert self.conf_ev.height == 4
        assert self.conf_ev.value_mask == (l.const["CWX"]
                                           | l.const["CWY"]
                                           | l.const["CWWidth"]
                                           | l.const["CWHeight"])

        self.map_ev = None
        self.conf_ev = None
        w2.move(5, 6)
        gtk.main()
        assert self.map_ev is None
        assert self.conf_ev.x == 5
        assert self.conf_ev.y == 6
        assert self.conf_ev.value_mask == (l.const["CWX"] | l.const["CWY"])
        
        self.map_ev = None
        self.conf_ev = None
        w2.raise_()
        gtk.main()
        assert self.map_ev is None
        assert self.conf_ev.detail == l.const["Above"]
        assert self.conf_ev.value_mask == l.const["CWStackMode"]
        
    def test_sendConfigureNotify(self):
        # GDK discards ConfigureNotify's sent to child windows, so we can't
        # use self.window():
        w1 = gtk.gdk.Window(self.root(), width=10, height=10,
                            window_type=gtk.gdk.WINDOW_TOPLEVEL,
                            wclass=gtk.gdk.INPUT_OUTPUT,
                            event_mask=gtk.gdk.ALL_EVENTS_MASK)
        self.ev = None
        def myfilter(ev, data=None):
            print "ev %s" % (ev.type,)
            if ev.type == gtk.gdk.CONFIGURE:
                self.ev = ev
                gtk.main_quit()
            gtk.main_do_event(ev)
        gtk.gdk.event_handler_set(myfilter)

        w1.show()
        gtk.gdk.flush()
        l.sendConfigureNotify(w1)
        gtk.main()
        
        assert self.ev is not None
        assert self.ev.type == gtk.gdk.CONFIGURE
        assert self.ev.window == w1
        assert self.ev.send_event
        assert self.ev.x == 0
        assert self.ev.y == 0
        assert self.ev.width == 10
        assert self.ev.height == 10
        
        # We have to create w2 on a separate connection, because if we just
        # did w1.reparent(w2, ...), then GDK would magically convert w1 from a
        # TOPLEVEL window into a CHILD window.
        w2 = self.window(self.clone_display())
        gtk.gdk.flush()
        w2on1 = l.get_pywindow(w1, l.get_xwindow(w2))
        # Doesn't generate an event, because event mask is zeroed out.
        w2.move(11, 12)
        # Reparenting doesn't trigger a ConfigureNotify.
        w1.reparent(w2on1, 13, 14)
        # To double-check that it's still a TOPLEVEL:
        print w1.get_window_type()
        w1.resize(15, 16)
        gtk.main()

        # w1 in root coordinates is now at (24, 26)
        self.ev = None
        l.sendConfigureNotify(w1)
        gtk.main()

        assert self.ev is not None
        assert self.ev.type == gtk.gdk.CONFIGURE
        assert self.ev.window == w1
        assert self.ev.send_event
        assert self.ev.x == 24
        assert self.ev.y == 26
        assert self.ev.width == 15
        assert self.ev.height == 16

    def test_configureAndNotify(self):
        self.ev = None
        def cb(ev):
            print "got ConfigureRequest"
            self.ev = ev
            gtk.main_quit()
        l.substructureRedirect(self.root(), None, cb)
        w1_client = self.window(self.clone_display())
        gtk.gdk.flush()
        w1_wm = l.get_pywindow(self.display, l.get_xwindow(w1_client))

        l.configureAndNotify(w1_client, 11, 12, 13, 14)
        gtk.main()

        assert self.ev is not None
        assert self.ev.parent is self.root()
        assert self.ev.window is w1_wm
        assert self.ev.x == 11
        assert self.ev.y == 12
        assert self.ev.width == 13
        assert self.ev.height == 14
        assert self.ev.border_width == 0
        assert self.ev.value_mask == (l.const["CWX"]
                                      | l.const["CWY"]
                                      | l.const["CWWidth"]
                                      | l.const["CWHeight"]
                                      | l.const["CWBorderWidth"])
        
        partial_mask = l.const["CWWidth"] | l.const["CWStackMode"]
        l.configureAndNotify(w1_client, 11, 12, 13, 14, partial_mask)
        gtk.main()
        
        assert self.ev is not None
        assert self.ev.parent is self.root()
        assert self.ev.window is w1_wm
        assert self.ev.width == 13
        assert self.ev.border_width == 0
        assert self.ev.value_mask == (l.const["CWWidth"]
                                      | l.const["CWBorderWidth"])
        
