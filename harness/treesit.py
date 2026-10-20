"""Tree-sitter definitions, line intervals and repo-path resolution (from ce-benchmarks-contextbench cbcore.py)."""
import functools
import os
import re

# Same tables as ContextBench's extractor (language by extension; which node types are "definitions").
LANG_MAP = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript",
    ".tsx": "tsx", ".java": "java", ".go": "go", ".rs": "rust", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".cs": "c_sharp", ".php": "php",
    ".rb": "ruby", ".swift": "swift", ".kt": "kotlin", ".scala": "scala",
}
DEF_NODES = {
    "python": {"function_definition", "class_definition", "async_function_definition"},
    "javascript": {"function_declaration", "class_declaration", "method_definition", "arrow_function"},
    "typescript": {"function_declaration", "class_declaration", "method_definition", "interface_declaration"},
    "tsx": {"function_declaration", "class_declaration", "method_definition", "interface_declaration"},
    "java": {"method_declaration", "class_declaration", "interface_declaration", "constructor_declaration"},
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "rust": {"function_item", "impl_item", "struct_item", "trait_item"},
    "c": {"function_definition", "struct_specifier"},
    "cpp": {"function_definition", "class_specifier", "struct_specifier"},
    "c_sharp": {"method_declaration", "class_declaration", "interface_declaration"},
    "php": {"function_definition", "method_declaration", "class_declaration"},
    "ruby": {"method", "class", "module"},
    "swift": {"function_declaration", "class_declaration", "protocol_declaration"},
    "kotlin": {"function_declaration", "class_declaration"},
    "scala": {"function_definition", "class_definition", "trait_definition"},
}
_NAME_RANK = {"identifier": 0, "field_identifier": 1, "property_identifier": 2, "method_identifier": 3,
              "type_identifier": 100, "scoped_identifier": 100, "namespace_identifier": 100,
              "constant_identifier": 100}


# ---------------------------------------------------------------- intervals (inclusive ends)

def merge(iv):
    """Merge overlapping or adjacent inclusive intervals."""
    out = []
    for s, e in sorted(iv):
        if out and s <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def total(iv):
    return sum(e - s + 1 for s, e in merge(iv))


def inter(a, b):
    """Size of the intersection of two interval lists."""
    a, b, i, j, n = merge(a), merge(b), 0, 0, 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if lo <= hi:
            n += hi - lo + 1
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return n


def by_file_total(d):
    return sum(total(v) for v in d.values())


def by_file_inter(a, b):
    return sum(inter(a[f], b[f]) for f in set(a) & set(b))


def prf(pred_n, gold_n, inter_n):
    """ContextBench edge cases: empty gold -> recall 1.0, empty prediction -> precision 1.0."""
    r = inter_n / gold_n if gold_n else 1.0
    p = inter_n / pred_n if pred_n else 1.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"recall": r, "precision": p, "f1": f, "inter": inter_n, "gold": gold_n, "pred": pred_n}


# ---------------------------------------------------------------- files, bytes, definitions

@functools.lru_cache(maxsize=4096)
def _line_offsets(abs_path):
    try:
        data = open(abs_path, "rb").read()
    except OSError:
        return None, 0
    offs = [0]
    i = data.find(b"\n")
    while i >= 0:
        offs.append(i + 1)
        i = data.find(b"\n", i + 1)
    return tuple(offs), len(data)


def lines_to_bytes(abs_path, start, end):
    """Inclusive byte span of lines start..end (1-based), same rule as ContextBench line_to_byte."""
    offs, size = _line_offsets(abs_path)
    if offs is None:
        return None
    if size == 0:
        return (0, 0)
    start = max(1, start)
    end = max(start, end)
    if start - 1 >= len(offs):
        return None
    sb = offs[start - 1]
    eb = offs[end] - 1 if end < len(offs) else size - 1
    return (sb, max(sb, eb))


@functools.lru_cache(maxsize=16)
def _parser(lang):
    from tree_sitter_language_pack import get_parser
    return get_parser(lang)


def _txt(src, n):
    return src[n.start_byte:n.end_byte].decode("utf-8", "replace").strip() if n is not None else ""


def _short(name):
    """`ns::Foo<T>` -> `Foo`."""
    return re.sub(r"<.*", "", name).split("::")[-1].strip()


_LEAF = {"identifier", "field_identifier", "qualified_identifier", "destructor_name", "operator_name",
         "type_identifier", "property_identifier"}


def _def_name(node, src):
    """Name of a definition node, or "" when it has none (never a name from its body or parameters)."""
    t, parent = node.type, node.parent
    if t == "type_declaration":  # Go: `type Foo struct {...}`
        spec = next((c for c in node.named_children if c.type in ("type_spec", "type_alias")), None)
        return _txt(src, spec.child_by_field_name("name")) if spec is not None else ""
    if t == "impl_item":  # Rust: `impl<T> Trait for Type<T>` -> Type
        return _short(_txt(src, node.child_by_field_name("type")))
    if t == "arrow_function":  # JS: named by what it is bound to
        if parent is not None and parent.type == "variable_declarator":
            return _txt(src, parent.child_by_field_name("name"))
        if parent is not None and parent.type == "pair":
            return _txt(src, parent.child_by_field_name("key")).strip("'\"")
        if parent is not None and parent.type == "assignment_expression":
            return _txt(src, parent.child_by_field_name("left")).split(".")[-1]
        return ""
    name = node.child_by_field_name("name")
    if name is not None and _txt(src, name):
        return _short(_txt(src, name))
    decl = node.child_by_field_name("declarator")  # C/C++: walk declarators down to the identifier
    for _ in range(12):
        if decl is None or decl.type in _LEAF:
            break
        nxt = decl.child_by_field_name("declarator")
        if nxt is None:
            nxt = next((c for c in decl.named_children if c.type in _LEAF or c.type.endswith("declarator")), None)
        decl = nxt
    return _short(_txt(src, decl)) if decl is not None and decl.type in _LEAF else ""


@functools.lru_cache(maxsize=4096)
def file_defs(abs_path, lang=None):
    """((kind, name, start_byte, end_byte_exclusive), ...) for every definition node in the file.

    `lang` overrides the extension table (ContextBench parses every `.h` as C; edit-gold naming parses
    C++ tasks' headers as C++). The parity metrics always use the default table.
    """
    lang = lang or LANG_MAP.get(os.path.splitext(abs_path.lower())[1])
    if lang not in DEF_NODES:
        return ()
    try:
        src = open(abs_path, "rb").read()
    except OSError:
        return ()
    try:
        tree = _parser(lang).parse(src)
    except LookupError:  # grammar not bundled (e.g. c_sharp): no definitions
        return ()
    out, stack, kinds = [], [tree.root_node], DEF_NODES[lang]
    while stack:
        n = stack.pop()
        if n.is_named and n.type in kinds:
            out.append((n.type, _def_name(n, src), n.start_byte, n.end_byte))
        stack.extend(reversed(n.children))
    return tuple(out)


def innermost_def(tree, f, line, lang=None):
    """(kind, name) of the smallest *named* definition overlapping `line` of file f, or None."""
    p = os.path.join(tree, f)
    b = lines_to_bytes(p, line, line)
    if not b:
        return None
    best = None
    for kind, name, ds, de in file_defs(p, lang):  # overlap, not containment: an indented def starts mid-line
        if name and ds <= b[1] and de > b[0] and (best is None or de - ds < best[2]):
            best = (kind, name, de - ds)
    return best[:2] if best else None


# ---------------------------------------------------------------- path resolution

def resolve(tree, path):
    """Repo-relative path of an existing file under tree, by trying suffixes (ContextBench rule).

    Paths under the tree's absolute location (/tmp or /private/tmp) are cut at the tree root first.
    Returns "" when nothing under the tree matches.
    """
    if not path or not isinstance(path, str):
        return ""
    p = path.strip().strip("'\"`").replace("\\", "/")
    real = os.path.realpath(tree)
    for root in (real, tree):
        if p.startswith(root + "/"):
            p = p[len(root) + 1:]
            break
    while p.startswith("./"):
        p = p[2:]
    parts = [x for x in p.lstrip("/").split("/") if x]
    for i in range(len(parts)):
        cand = "/".join(parts[i:])
        full = os.path.realpath(os.path.join(real, cand))
        if full.startswith(real + "/") and os.path.isfile(full):
            return cand
    return ""


def inside_tree(tree, path):
    if not path or not isinstance(path, str):
        return True
    if not os.path.isabs(path):
        return True
    real = os.path.realpath(tree)
    full = os.path.realpath(path)
    return full == real or full.startswith(real + "/")


