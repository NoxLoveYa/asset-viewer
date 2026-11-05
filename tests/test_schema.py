# SPDX-License-Identifier: AGPL-3.0-or-later
"""schemas/*.schema.json describe the config and review files for editors. A small checker for the subset the schemas use
(no dependency) shows that the sample files fit them and that they agree with what the viewer itself accepts."""
import json
import os
import re
import unittest

import helpers
from helpers import REPO
from viewer.config import ConfigError, parse
from viewer.picks import _check_review
from viewer.project import Project

SCHEMAS = os.path.join(REPO, "schemas")
SAMPLE = os.path.join(REPO, "examples", "sample")


def load(name):
    with open(os.path.join(SCHEMAS, name), encoding="utf-8") as f:
        return json.load(f)


TYPES = {
    "object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list), "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool), "null": lambda v: v is None,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


def problems(value, schema, path="$"):
    """The problems of `value` against a draft-07 schema, for the keywords these schemas use."""
    out = []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(TYPES[t](value) for t in types):
            return ["%s: %s expected" % (path, "/".join(types))]
    if "enum" in schema and value not in schema["enum"]:
        out.append("%s: one of %r expected" % (path, schema["enum"]))
    if "oneOf" in schema and sum(1 for s in schema["oneOf"] if not problems(value, s, path)) != 1:
        out.append("%s: matches none or several of the alternatives" % path)
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 1 << 30):
            out.append("%s: length" % path)
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append("%s: does not match %s" % (path, schema["pattern"]))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", value) or value > schema.get("maximum", value):
            out.append("%s: out of range" % path)
    if isinstance(value, list):
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            out.append("%s: duplicate items" % path)
        for i, v in enumerate(value):
            out += problems(v, schema.get("items", {}), "%s[%d]" % (path, i))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                out.append("%s: %s is required" % (path, key))
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, v in value.items():
            if "propertyNames" in schema and not re.search(schema["propertyNames"]["pattern"], key):
                out.append("%s: key %r is not allowed" % (path, key))
            if key in props:
                out += problems(v, props[key], "%s.%s" % (path, key))
            elif extra is False:
                out.append("%s: unknown key %s" % (path, key))
            elif isinstance(extra, dict):
                out += problems(v, extra, "%s.%s" % (path, key))
    return out


def sample(name):
    with open(os.path.join(SAMPLE, name), encoding="utf-8") as f:
        return json.load(f)


class SchemaFilesTest(unittest.TestCase):
    def test_the_schemas_are_json_schema_documents(self):
        for name in ("config.schema.json", "review.schema.json"):
            s = load(name)
            self.assertEqual(s["$schema"], "http://json-schema.org/draft-07/schema#")
            self.assertEqual(s["type"], "object")

    def test_the_sample_files_fit_their_schemas(self):
        self.assertEqual(problems(sample("asset-viewer.json"), load("config.schema.json")), [])
        self.assertEqual(problems(sample("review.json"), load("review.schema.json")), [])

    def test_every_top_level_config_key_is_in_the_schema(self):
        from viewer.config import TOP_KEYS
        self.assertEqual(sorted(load("config.schema.json")["properties"]), sorted(TOP_KEYS))

    def test_the_checker_finds_mistakes(self):
        cfg = load("config.schema.json")
        for bad in ({"port": "8765"}, {"port": 0}, {"roots": "assets"}, {"roots": [{"label": "x"}]}, {"roots": [5]},
                    {"model": {"front": "x"}}, {"model": {"extra": 1}}, {"materials": {"a": {"b": {"alpha": "blend", "set": "s"}}}},
                    {"materials": {"a": {"b": {}}}}, {"nope": 1}, {"plugins": {"Bad Name": {}}}, {"plugins": {"a": 5}},
                    {"categories": ["a", "a"]}, {"vfxFolders": ["a/b"]}, {"theme": ""}, {"data": {"x": "y"}}):
            self.assertTrue(problems(bad, cfg), bad)
        self.assertTrue(problems({}, load("review.schema.json")))
        self.assertTrue(problems({"reviews": [{"title": "t"}]}, load("review.schema.json")))


class AgreementTest(helpers.ProjectCase):
    """The schema and the viewer's own validator accept and refuse the same documents."""

    CONFIGS = [
        {}, {"title": "T", "port": 9000}, {"roots": ["assets", {"path": "x", "label": ""}, {"path": "."}]}, {"hide": ["a/*", "b\\c"]},
        {"categories": ["model", "anim"]}, {"vfxFolders": ["fx"]}, {"model": {"front": "-z", "glassNames": ["^Glass"], "maps": ["Color"]}},
        {"materials": {"a/*.fbx": {"*": {"set": "s/atlas", "alpha": "cutout", "doubleSided": True}}}},
        {"materials": {"a/*.fbx": {"*": {"set": "s/atlas", "alpha": None}}}}, {"theme": "paper"}, {"theme": "t/theme.css"},
        {"data": {"picks": "p.json"}}, {"prefsPrefix": "x."}, {"pluginDirs": ["plugins"]}, {"plugins": {"a-b": {}, "c": None}},
        # refused by both
        {"port": "x"}, {"port": 0}, {"port": 70000}, {"roots": "a"}, {"roots": [{"label": "x"}]}, {"roots": [5]}, {"hide": "a"},
        {"categories": ["a", "a"]}, {"vfxFolders": ["a/b"]}, {"vfxFolders": [""]}, {"model": {"front": "x"}}, {"model": []},
        {"materials": {"a": {"b": {"set": "s", "alpha": "blend"}}}}, {"materials": {"a": {"b": {"alpha": "cutout"}}}},
        {"materials": {"a": {"b": {"set": "s", "doubleSided": 1}}}}, {"theme": ""}, {"theme": 3}, {"data": {"picks": 1}},
        {"prefsPrefix": "x" * 65}, {"pluginDirs": "p"}, {"plugins": {"Bad": {}}}, {"plugins": {"a": 5}}, {"title": ""}, {"title": 5},
    ]

    def test_config_agreement(self):
        schema = load("config.schema.json")
        project = Project(self.project_dir)
        for doc in self.CONFIGS:
            with self.subTest(doc=doc):
                try:
                    parse(doc, project)
                    accepted = True
                except ConfigError:
                    accepted = False
                self.assertEqual(not problems(doc, schema), accepted, problems(doc, schema))

    def test_review_agreement(self):
        schema = load("review.schema.json")
        item = {"id": "a.wav", "name": "A", "by": "me", "note": "n", "ref": True}
        group = {"id": "g", "label": "G", "about": "x", "items": [item]}
        docs = [{"reviews": []}, {"reviews": [{"title": "T", "groups": [group]}]}, {"reviews": [{"title": "T", "groups": [], "extra": 1}]},
                {"reviews": [{"title": "T", "groups": [{"id": "g", "items": [{"id": "a", "ref": None}]}]}]},
                {}, [], {"reviews": {}}, {"reviews": [1]}, {"reviews": [{"groups": []}]}, {"reviews": [{"title": 1, "groups": []}]},
                {"reviews": [{"title": "T", "groups": [{"id": "g"}]}]}, {"reviews": [{"title": "T", "groups": [{"id": "g", "items": [{}]}]}]},
                {"reviews": [{"title": "T", "groups": [{"id": "g", "items": [{"id": "a", "ref": 1}]}]}]}]
        for doc in docs:
            with self.subTest(doc=doc):
                self.assertEqual(not problems(doc, schema), _check_review(doc) is None)


if __name__ == "__main__":
    unittest.main()
