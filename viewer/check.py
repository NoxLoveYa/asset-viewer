# SPDX-License-Identifier: AGPL-3.0-or-later
"""`--check`: the core self-tests, the project's catalog and materials, then each plugin's checks."""
import fnmatch
import os

from .catalog import is_link, model_maps, scan, set_maps
from .picks import parse_cut
from .project import PathError

MAX_HIDE_WALK = 300000
BAD_CUTS = ([2, 1], [-1, 1], [0, True], [0, float("nan")], [1], "1-2")


class Report:
    """What `--check` and plugin checks write to. `catalog` is a fresh scan of the project."""

    def __init__(self, app, catalog):
        self.app = app
        self.project = app.project
        self.config = app.state.config
        self.catalog = catalog
        self.failures = []
        self.warnings = []
        self.passed = []

    @property
    def items(self):
        return self.catalog["items"]

    def ok(self, msg):
        self.passed.append(msg)

    def fail(self, msg):
        self.failures.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)


def _core(report):
    config = report.config
    files = ["Widget.fbx", "Widget_Color.png", "Widget_Normal.png", "Widget_Worn_Color.png", "Widget_Worn_Roughness.png",
             "Widgets_Color.png", "gate.fbx", "gate_left.fbx", "gate_left_Color.png", "gate_Color.png"]
    want = {"Base": {"Color": "x/Widget_Color.png", "Normal": "x/Widget_Normal.png"},
            "Worn": {"Color": "x/Widget_Worn_Color.png", "Roughness": "x/Widget_Worn_Roughness.png"}}
    if model_maps("Widget.fbx", "x", files, config.maps) != want:
        report.fail("map naming rule: Widget.fbx got %r" % model_maps("Widget.fbx", "x", files, config.maps))
    if model_maps("gate.fbx", "x", files, config.maps) != {"Base": {"Color": "x/gate_Color.png"}} \
            or model_maps("gate_left.fbx", "x", files, config.maps) != {"Base": {"Color": "x/gate_left_Color.png"}}:
        report.fail("map naming rule: gate_left_Color.png belongs to gate_left.fbx only")
    try:
        if parse_cut([1.234, 2.5]) != [1.23, 2.5] or parse_cut(None) is not None:
            report.fail("cut rounding")
        for bad in BAD_CUTS:
            try:
                parse_cut(bad)
            except ValueError:
                continue
            report.fail("cut %r passed" % (bad,))
    except ValueError as e:
        report.fail("cut: %s" % e)

    models = [i for i in report.items if i["kind"] == "model"]
    sets = {}
    for pattern, names in config.materials.items():
        if not any(fnmatch.fnmatchcase(i["path"], pattern) for i in models):
            report.fail("materials %s matches no model" % pattern)
        for name, rule in names.items():
            if "Color" not in set_maps(report.project.root, rule["set"], sets, config.maps):
                report.fail("materials %s / %s: no %s_Color map" % (pattern, name, rule["set"]))
    _hide(report)
    for area in report.app.state.registry.read_areas:
        why = report.project.policy_problem(area)
        if why:
            report.fail("served area %s cannot be served: %s" % (area, why))


def _hide(report):
    """Every `hide` pattern must match something under the roots, or it hides nothing (a typo, a path that moved)."""
    config, project = report.config, report.project
    if not config.hide_rules:
        return
    rels = []
    visited = set()  # real paths of the folders walked: a link back up (a junction) is not entered, nothing is listed twice
    for root in config.roots:
        try:
            stack = [project.confine(root.path)]
        except PathError:
            continue
        while stack:
            folder = stack.pop()
            real = os.path.normcase(os.path.realpath(folder))
            if real in visited:
                continue
            visited.add(real)
            here = project.rel(folder)
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for e in entries:  # dot folders are walked too: a pattern may name one
                rels.append((here + "/" if here else "") + e.name)
                try:
                    if e.is_dir(follow_symlinks=False) and not is_link(e):
                        stack.append(e.path)
                except OSError:
                    continue
            if len(rels) > MAX_HIDE_WALK:
                report.warn("hide: more than %d entries under the roots, patterns not checked against them" % MAX_HIDE_WALK)
                return
    low = [r.lower() for r in rels]
    for pattern, globs in config.hide_rules:
        if not any(fnmatch.fnmatchcase(r, g.lower()) for r in low for g in globs):
            report.fail("hide pattern %r matches nothing under the roots" % pattern)


def run(app, out=print):
    """Run every check, print the result and return the Report (`report.failures` empty means ok)."""
    state = app.state
    catalog = scan(app.project, state.config, state.registry)
    report = Report(app, catalog)
    for w in state.config.warnings + state.registry.warnings + catalog["warnings"]:
        report.warn(w)
    _core(report)
    for plugin, fn in state.registry.checks:
        try:
            fn(report)
        except Exception as e:
            report.fail("plugin %s: check crashed: %s: %s" % (plugin, type(e).__name__, e))
    for w in report.warnings:
        out("warning: %s" % w)
    for m in report.passed:
        out("ok: %s" % m)
    for f in report.failures:
        out("FAIL: %s" % f)
    if not report.failures:
        kinds = {}
        for i in report.items:
            kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
        shared = sum(1 for i in report.items if i["kind"] == "model" and i.get("materials"))
        out("ok: " + (", ".join("%d %s" % (n, k) for k, n in sorted(kinds.items())) or "no items")
            + " (%d models on shared materials)" % shared)
    else:
        out("%d check(s) failed" % len(report.failures))
    return report
