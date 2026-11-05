# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static checks of the client: tokens only (no colour literals outside themes/), every token used is defined, no
innerHTML, the offline guarantee, and the shipped themes."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
PLUGINS = ROOT / "plugins"
THEMES = ROOT / "themes"
AERO = THEMES / "aero" / "theme.css"
DATA_MARK = "colour from data"  # a line carrying this is data (rig part colours...), not a theme colour

COLOUR = re.compile(
    r"#[0-9a-fA-F]{3,8}\b"                                    # #fff #a8d4de
    r"|\b(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color-mix)\("
    r"|\b0x[0-9a-fA-F]{6}\b"                                  # three's 0xrrggbb
)
WHERE_ROOT = re.compile(r":where\(:root\)\s*\{([^}]*)\}")
DECL = re.compile(r"(--[a-z0-9-]+)\s*:")
VAR_USE = re.compile(r"var\(\s*--([a-z][a-z0-9-]*)")
TOKEN_USE = re.compile(r"""\b(?:token|color|num|themeColor)\(\s*["']([a-z][a-z0-9-]*)["']""")
SET_PROP = re.compile(r"""setProperty\(\s*["'](--[a-z0-9-]+)["']""")
HTML_SINKS = re.compile(r"\b(?:innerHTML|outerHTML|insertAdjacentHTML)\b|document\.write\(")


def files(folder, *suffixes):
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix in suffixes) if folder.exists() else []


def client_files(*suffixes):
    return files(APP, *suffixes) + files(PLUGINS, *suffixes)


def strip_comments(text, suffix):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    if suffix in (".js", ".html"):
        text = re.sub(r"(^|\s)//.*$", "", text, flags=re.M)
    return text


def tokens_defined_by(css):
    return {d[2:] for d in DECL.findall(css)}


def aero_tokens():
    return tokens_defined_by(AERO.read_text("utf-8"))


def plugin_default_tokens():
    out = set()
    for p in files(PLUGINS, ".css"):
        for block in WHERE_ROOT.findall(p.read_text("utf-8")):
            out |= tokens_defined_by(block)
    return out


class NoColourLiterals(unittest.TestCase):
    def test_client_has_no_colour_literals(self):
        for path in client_files(".js", ".css", ".html"):
            text = path.read_text("utf-8")
            if path.suffix == ".css" and PLUGINS in path.parents:
                text = WHERE_ROOT.sub("", text)  # a plugin's default token values may be colours
            text = "\n".join(line for line in text.split("\n") if DATA_MARK not in line)
            text = strip_comments(text, path.suffix)
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertEqual(COLOUR.findall(text), [], "colour literal outside themes/ (use a token)")

    def test_themes_do_hold_colours(self):
        self.assertTrue(COLOUR.search(AERO.read_text("utf-8")), "the aero theme is where colours live")


class TokensAreDefined(unittest.TestCase):
    def defined(self):
        out = aero_tokens() | plugin_default_tokens()
        for p in client_files(".css"):
            out |= tokens_defined_by(strip_comments(p.read_text("utf-8"), ".css")) if APP in p.parents else set()
        for p in client_files(".js"):
            out |= {m[2:] for m in SET_PROP.findall(p.read_text("utf-8"))}
        return out

    def test_every_token_used_is_defined(self):
        defined = self.defined()
        for path in client_files(".js", ".css", ".html"):
            text = strip_comments(path.read_text("utf-8"), path.suffix)
            used = {n for n in VAR_USE.findall(text) if not n.endswith("-")}  # var(--icon-${name}) is built at run time
            if path.suffix == ".js":
                used |= set(TOKEN_USE.findall(text))
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertEqual(sorted(used - defined), [], "token used but defined by neither aero nor a plugin's :where(:root)")

    def test_every_icon_file_exists(self):
        css = AERO.read_text("utf-8")
        for rel in re.findall(r'url\("(/app/icons/[^"]+)"\)', css):
            with self.subTest(icon=rel):
                self.assertTrue((ROOT / rel.lstrip("/")).is_file())

    def test_core_icon_tokens_exist_for_core_categories(self):
        for kind in ("model", "texture", "vfx", "sound", "review"):
            self.assertIn("icon-" + kind, aero_tokens())


class SafeDom(unittest.TestCase):
    def test_no_html_sinks(self):
        for path in client_files(".js"):
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertIsNone(HTML_SINKS.search(strip_comments(path.read_text("utf-8"), ".js")),
                                  "build the DOM with textContent / el(), never an HTML string")

    def test_page_has_no_inline_script_besides_the_importmap_and_no_inline_style(self):
        html = (APP / "index.html").read_text("utf-8")
        scripts = re.findall(r"<script\b([^>]*)>", html)
        for attrs in scripts:
            self.assertTrue('type="importmap"' in attrs or "src=" in attrs, "inline script: " + attrs)
        self.assertNotRegex(html, r"\sstyle\s*=", "inline style attributes are blocked by the CSP")
        self.assertNotRegex(html, r"\son[a-z]+\s*=", "inline event handlers are blocked by the CSP")

    def test_token_placeholder_present(self):
        self.assertIn('<meta name="viewer-token" content="__VIEWER_TOKEN__">', (APP / "index.html").read_text("utf-8"))


class Offline(unittest.TestCase):
    def test_page_and_themes_load_nothing_from_the_network(self):
        for path in [APP / "index.html", APP / "base.css"] + files(THEMES, ".css"):
            text = strip_comments(path.read_text("utf-8"), path.suffix)
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertNotRegex(text, r"https?://", "everything is served from this server")

    def test_importmap_aliases(self):
        html = (APP / "index.html").read_text("utf-8")
        for alias, target in (('"three"', "/vendor/three/build/three.module.js"), ('"three/addons/"', "/vendor/three/examples/jsm/"),
                              ('"viewer/"', "/app/")):
            self.assertIn(f'{alias}: "{target}"', html)

    def test_every_module_import_resolves(self):
        imp = re.compile(r"""(?:from|import)\s*\(?\s*["'](\.{1,2}/[^"']+)["']""")
        for path in files(APP, ".js"):
            for rel in imp.findall(strip_comments(path.read_text("utf-8"), ".js")):
                with self.subTest(path=path.relative_to(ROOT).as_posix(), imports=rel):
                    self.assertTrue((path.parent / rel).resolve().is_file())


class Themes(unittest.TestCase):
    def test_paper_overrides_only_known_tokens(self):
        known = aero_tokens()
        paper = tokens_defined_by((THEMES / "paper" / "theme.css").read_text("utf-8"))
        self.assertEqual(sorted(paper - known), [])

    def test_paper_covers_every_colour_token(self):
        """A light theme must restyle every colour (surfaces, text, stage, waveforms): tokens it may leave alone are
        shapes, motion, type, icons, the old-name aliases and the intensities/numbers that are not colours."""
        css = AERO.read_text("utf-8")
        colour_tokens = {name for name, value in re.findall(r"--([a-z0-9-]+)\s*:\s*([^;]+);", css) if COLOUR.search(value)}
        paper = tokens_defined_by((THEMES / "paper" / "theme.css").read_text("utf-8"))
        # --img-bg-add is additive-blend black in any theme
        missing = colour_tokens - paper - {"img-bg-add"}
        self.assertEqual(sorted(missing), [])

    def test_every_font_file_the_theme_names_exists(self):
        css = AERO.read_text("utf-8")
        for rel in re.findall(r'url\("(fonts/[^"]+)"\)', css):
            with self.subTest(font=rel):
                self.assertTrue((AERO.parent / rel).is_file())
        self.assertTrue((AERO.parent / "fonts" / "OFL.txt").is_file())


if __name__ == "__main__":
    unittest.main()
