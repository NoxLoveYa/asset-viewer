# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asset viewer: python serve.py [--project DIR] [--config FILE] [--port N] [--no-open] [--check] [--debug]"""
import argparse
import sys

from viewer import VERSION
from viewer.app import App
from viewer.check import run as run_checks
from viewer.config import DEFAULT_PORT, ConfigError
from viewer.launch import StartError, run
from viewer.plugins import PluginError


def port_number(text):
    try:
        n = int(text)
    except ValueError:
        n = 0
    if not 1 <= n <= 65535:
        raise argparse.ArgumentTypeError("%r is not a port (1-65535)" % text)
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description="Asset viewer on localhost: browse a project's models, textures, VFX and sounds.")
    ap.add_argument("--project", default=".", help="the project folder (default: the current folder)")
    ap.add_argument("--config", help="config file, relative to the project (default: asset-viewer.json, .asset-viewer/config.json)")
    ap.add_argument("--port", type=port_number, help="port (default: the config's, else %d)" % DEFAULT_PORT)
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    ap.add_argument("--check", action="store_true", help="self-check the project and exit")
    ap.add_argument("--debug", action="store_true", help="print tracebacks and requests to stderr")
    ap.add_argument("--trust-project-plugins", action="store_true",
                    help="load plugins from the config's pluginDirs: Python code of the project, run with your rights")
    ap.add_argument("--version", action="version", version=VERSION)
    args = ap.parse_args(argv)
    try:
        app = App(args.project, args.config, debug=args.debug, trust_project_plugins=args.trust_project_plugins)
    except (ConfigError, PluginError) as e:
        sys.stderr.write("error: %s\n" % e)
        return 2
    if args.check:  # prints the warnings itself
        return 1 if run_checks(app).failures else 0
    for w in app.state.config.warnings + app.state.registry.warnings:
        sys.stderr.write("warning: %s\n" % w)
    try:
        return run(app, args.port or app.state.config.port, explicit=bool(args.port), open_browser=not args.no_open)
    except StartError as e:
        sys.stderr.write("error: %s\n" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
