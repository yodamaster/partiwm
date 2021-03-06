# This file is part of Parti.
# Copyright (C) 2008 Nathaniel Smith <njs@pobox.com>
# Parti is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import gobject
import sys
import os
import socket
import time
from optparse import OptionParser
import logging
from subprocess import Popen, PIPE

import xpra
from xpra.bencode import bencode
from xpra.dotxpra import DotXpra
from xpra.platform import (XPRA_LOCAL_SERVERS_SUPPORTED,
                           DEFAULT_SSH_CMD,
                           GOT_PASSWORD_PROMPT_SUGGESTION)
from xpra.protocol import TwoFileConnection, SocketConnection

def nox():
    if "DISPLAY" in os.environ:
        del os.environ["DISPLAY"]
    # This is an error on Fedora/RH, so make it an error everywhere so it will
    # be noticed:
    import warnings
    warnings.filterwarnings("error", "could not open display")

def main(script_file, cmdline):
    #################################################################
    ## NOTE NOTE NOTE
    ##
    ## If you modify anything here, then remember to update the man page
    ## (xpra.1) as well!
    ##
    ## NOTE NOTE NOTE
    #################################################################
    if XPRA_LOCAL_SERVERS_SUPPORTED:
        start_str = "\t%prog start DISPLAY\n"
        list_str = "\t%prog list\n"
        upgrade_str = "\t%prog upgrade DISPLAY"
        note_str = ""
    else:
        start_str = ""
        list_str = ""
        upgrade_str = ""
        note_str = "(This xpra install does not support starting local servers.)"
    parser = OptionParser(version="xpra v%s" % xpra.__version__,
                          usage="".join(["\n",
                                         start_str,
                                         "\t%prog attach [DISPLAY]\n",
                                         "\t%prog stop [DISPLAY]\n",
                                         list_str,
                                         upgrade_str,
                                         note_str]))
    if XPRA_LOCAL_SERVERS_SUPPORTED:
        parser.add_option("--start-child", action="append",
                          dest="children", metavar="CMD",
                          help="program to spawn in new server (may be repeated)")
        parser.add_option("--exit-with-children", action="store_true",
                          dest="exit_with_children", default=False,
                          help="Terminate server when --start-child command(s) exit")
        parser.add_option("--no-daemon", action="store_false",
                          dest="daemon", default=True,
                          help="Don't daemonize when running as a server")
        parser.add_option("--xvfb", action="store",
                          dest="xvfb", default="Xvfb", metavar="CMD",
                          help="How to run the headless X server (default: '%default')")
        parser.add_option("--bind-tcp", action="store",
                          dest="bind_tcp", default=None,
                          metavar="[HOST]:PORT",
                          help="Listen for connections over TCP (insecure)")
    parser.add_option("-z", "--compress", action="store",
                      dest="compression_level", type="int", default=3,
                      metavar="LEVEL",
                      help="How hard to work on compressing data."
                      + " 0 to disable compression,"
                      + " 9 for maximal (slowest) compression. Default: %default.")
    parser.add_option("--ssh", action="store",
                      dest="ssh", default=DEFAULT_SSH_CMD, metavar="CMD",
                      help="How to run ssh (default: '%default')")
    parser.add_option("--remote-xpra", action="store",
                      dest="remote_xpra", default=".xpra/run-xpra",
                      metavar="CMD",
                      help="How to run xpra on the remote host (default: '%default')")
    parser.add_option("-d", "--debug", action="store",
                      dest="debug", default=None, metavar="FILTER1,FILTER2,...",
                      help="List of categories to enable debugging for (or \"all\")")
    (options, args) = parser.parse_args(cmdline[1:])

    if not args:
        parser.error("need a mode")

    logging.root.setLevel(logging.INFO)
    if options.debug is not None:
        categories = options.debug.split(",")
        for cat in categories:
            if cat.startswith("-"):
                logging.getLogger(cat[1:]).setLevel(logging.INFO)
            if cat == "all":
                logger = logging.root
            else:
                logger = logging.getLogger(cat)
            logger.setLevel(logging.DEBUG)
    logging.root.addHandler(logging.StreamHandler(sys.stderr))

    mode = args.pop(0)
    
    if mode in ("start", "upgrade") and XPRA_LOCAL_SERVERS_SUPPORTED:
        nox()
        from xpra.scripts.server import run_server
        run_server(parser, options, mode, script_file, args)
    elif mode == "attach":
        try:
            run_client(parser, options, args)
        except KeyboardInterrupt:
            sys.stdout.write("Exiting on keyboard interrupt\n")
    elif mode == "stop":
        nox()
        run_stop(parser, options, args)
    elif mode == "list" and XPRA_LOCAL_SERVERS_SUPPORTED:
        run_list(parser, options, args)
    elif mode == "_proxy" and XPRA_LOCAL_SERVERS_SUPPORTED:
        nox()
        run_proxy(parser, options, args)
    else:
        parser.error("invalid mode '%s'" % mode)

def parse_display_name(parser, opts, display_name):
    if display_name.startswith("ssh:"):
        desc = {
            "type": "ssh",
            "local": False
            }
        sshspec = display_name[len("ssh:"):]
        if ":" in sshspec:
            (desc["host"], desc["display"]) = sshspec.split(":", 1)
            desc["display"] = ":" + desc["display"]
            desc["display_as_args"] = [desc["display"]]
        else:
            desc["host"] = sshspec
            desc["display"] = None
            desc["display_as_args"] = []
        desc["ssh"] = opts.ssh.split()
        desc["full_ssh"] = desc["ssh"] + ["-T", desc["host"]]
        desc["remote_xpra"] = opts.remote_xpra.split()
        desc["full_remote_xpra"] = desc["full_ssh"] + desc["remote_xpra"]
        return desc
    elif display_name.startswith(":"):
        desc = {
            "type": "unix-domain",
            "local": True,
            "display": display_name,
            }
        return desc
    elif display_name.startswith("tcp:"):
        desc = {
            "type": "tcp",
            "local": False,
            }
        host_spec = display_name[4:]
        (desc["host"], port_str) = host_spec.split(":", 1)
        desc["port"] = int(port_str)
        if desc["host"] == "":
            desc["host"] = "127.0.0.1"
        return desc
    else:
        parser.error("unknown format for display name")

def pick_display(parser, opts, extra_args):
    if len(extra_args) == 0:
        if not XPRA_LOCAL_SERVERS_SUPPORTED:
            parser.error("need to specify a display")
        # Pick a default server
        sockdir = DotXpra()
        servers = sockdir.sockets()
        live_servers = [display
                        for (state, display) in servers
                        if state is DotXpra.LIVE]
        if len(live_servers) == 0:
            parser.error("cannot find a live server to connect to")
        elif len(live_servers) == 1:
            return parse_display_name(parser, opts, live_servers[0])
        else:
            parser.error("there are multiple servers running, please specify")
    elif len(extra_args) == 1:
        return parse_display_name(parser, opts, extra_args[0])
    else:
        parser.error("too many arguments")

def _socket_connect(sock, target):
    try:
        sock.connect(target)
    except socket.error, e:
        sys.exit("Connection failed: %s" % (e,))
    return SocketConnection(sock)

def connect_or_fail(display_desc):
    if display_desc["type"] == "ssh":
        cmd = (display_desc["full_remote_xpra"]
               + ["_proxy"] + display_desc["display_as_args"])
        try:
            child = Popen(cmd, stdin=PIPE, stdout=PIPE)
        except OSError, e:
            sys.exit("Error running ssh program '%s': %s" % (cmd[0], e))
        return TwoFileConnection(child.stdin, child.stdout)

    elif XPRA_LOCAL_SERVERS_SUPPORTED and display_desc["type"] == "unix-domain":
        sockdir = DotXpra()
        sock = socket.socket(socket.AF_UNIX)
        return _socket_connect(sock,
                               sockdir.socket_path(display_desc["display"]))

    elif display_desc["type"] == "tcp":
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        return _socket_connect(sock,
                               (display_desc["host"], display_desc["port"]))

    else:
        assert False, "unsupported display type in connect"

def handshake_complete_msg(*args):
    sys.stdout.write("Attached (press Control-C to detach)\n")

def got_gibberish_msg(obj, data):
    if "assword" in data:
        sys.stdout.write("Your ssh program appears to be asking for a password.\n"
                         + GOT_PASSWORD_PROMPT_SUGGESTION)
        sys.stdout.flush()
    if "login" in data:
        sys.stdout.write("Your ssh program appears to be asking for a username.\n"
                         "Perhaps try using something like 'ssh:USER@host:display'?\n")
        sys.stdout.flush()

def run_client(parser, opts, extra_args):
    from xpra.client import XpraClient
    conn = connect_or_fail(pick_display(parser, opts, extra_args))
    if opts.compression_level < 0 or opts.compression_level > 9:
        parser.error("Compression level must be between 0 and 9 inclusive.")
    app = XpraClient(conn, opts.compression_level)
    app.connect("handshake-complete", handshake_complete_msg)
    app.connect("received-gibberish", got_gibberish_msg)
    app.run()

def run_proxy(parser, opts, extra_args):
    from xpra.proxy import XpraProxy
    assert "gtk" not in sys.modules
    server_conn = connect_or_fail(pick_display(parser, opts, extra_args))
    app = XpraProxy(TwoFileConnection(sys.stdout, sys.stdin), server_conn)
    app.run()

def run_stop(parser, opts, extra_args):
    assert "gtk" not in sys.modules
    magic_string = bencode(["hello", []]) + bencode(["shutdown-server"])

    display_desc = pick_display(parser, opts, extra_args)
    conn = connect_or_fail(display_desc)
    while magic_string:
        magic_string = magic_string[conn.write(magic_string):]
    while conn.read(4096):
        pass
    if display_desc["local"]:
        sockdir = DotXpra()
        for i in xrange(6):
            final_state = sockdir.server_state(display_desc["display"])
            if final_state is DotXpra.LIVE:
                time.sleep(0.5)
            else:
                break
        if final_state is DotXpra.DEAD:
            print "xpra at %s has exited." % display_desc["display"]
            sys.exit(0)
        elif final_state is DotXpra.UNKNOWN:
            print ("How odd... I'm not sure what's going on with xpra at %s"
                   % display_desc["display"])
            sys.exit(1)
        elif final_state is DotXpra.LIVE:
            print "Failed to shutdown xpra at %s" % display_desc["display"]
            sys.exit(1)
        else:
            assert False
    else:
        print "Sent shutdown command"

def run_list(parser, opts, extra_args):
    assert "gtk" not in sys.modules
    if extra_args:
        parser.error("too many arguments for mode")
    sockdir = DotXpra()
    results = sockdir.sockets()
    if not results:
        sys.stdout.write("No xpra sessions found\n")
    else:
        sys.stdout.write("Found the following xpra sessions:\n")
        for state, display in results:
            sys.stdout.write("\t%s session at %s" % (state, display))
            if state is DotXpra.DEAD:
                try:
                    os.unlink(sockdir.socket_path(display))
                except OSError:
                    pass
                else:
                    sys.stdout.write(" (cleaned up)")
            sys.stdout.write("\n")
