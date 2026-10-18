# Supported languages

CodeAtlas parses each file with [tree-sitter](https://tree-sitter.github.io/), extracts its
symbols, imports and call sites, and resolves those to project files and symbols. Every file
becomes a `File` node with a `language` property.

| Language | Extensions | Parser |
|---|---|---|
| Python | `.py` | tree-sitter-python |
| JavaScript | `.js`, `.jsx`, `.mjs`, `.cjs` | tree-sitter-javascript |
| TypeScript | `.ts`, `.mts`, `.cts` | tree-sitter-typescript |
| TSX | `.tsx` | tree-sitter-typescript (TSX grammar) |

JavaScript and TypeScript share one namespace, so a `.ts` file can import a `.js` file and
vice versa. Python is kept separate: a Python `clean()` never links to a JavaScript `clean()`.

Files in generated or vendored folders (`node_modules`, `dist`, `build`, `.next`, `coverage`,
`.venv`, …), minified bundles (`*.min.js`) and type declarations (`*.d.ts`) are skipped.

## What is extracted

| Concept | Python | JavaScript / TypeScript |
|---|---|---|
| Classes, inheritance | `class A(B)` | `class A extends B` (TypeScript `extends` clause included) |
| Functions | `def` | `function`, generator functions, `const f = () => …`, `const f = function () {…}` |
| Methods | `def` in a class | class methods, including `async` and `constructor` |
| Nested functions | yes | yes (`outer.inner`) |
| Imports | `import`, `from … import` | `import` default / named / namespace / side-effect, `export … from`, `export * from`, `require("x")`, `const x = require("x")` |
| Calls | bare, `self.m()`, `module.f()` | bare, `this.m()`, `obj.m()`, `new C()`, `a.b.c()` |
| Parameters | names | names (TypeScript types are not recorded) |

## How imports are resolved

**Python** uses dotted module names and `__init__.py` packages (see `resolver.py`).

**JavaScript / TypeScript** resolve relative specifiers (`./x`, `../y/z`) the way Node and
TypeScript do:

1. the exact file;
2. the path plus an extension (`.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`, …);
3. the path as a folder with an `index` file;
4. TypeScript's convention where `./x.js` means `./x.ts`.

Bare specifiers (`react`, `@scope/pkg/sub`) become external `Module` nodes named by the
package (`react`, `@scope/pkg`). Stylesheets, images and data files are skipped. A relative
import that matches no scanned file is recorded as unresolved.

## Confidence

The same rules as Python apply to every call edge:

- `exact`: a same-file function, an imported name, `this.method()` on a class with a known
  base chain, or a constructor (including one inherited from a base class);
- `name_match`: the only project-wide function or class with that name;
- no edge: unknown receivers (`service.place()` where `service` is a local variable), external
  packages, builtins and ambiguous names. These are listed on the caller instead.

## Known limits

- **Path aliases** (`@/components/x`, `~/lib/y` from `tsconfig.json` `paths`) are read as
  external packages. Reading `tsconfig.json` would fix this; it is not done yet.
- **Default exports** are not mapped to the function that is exported. Calls through a
  default import (`import App from "./App"; App()`) are not resolved.
- **Namespace constructors** such as `new Orders.OrderService()` are not resolved.
- **Re-export chains** (`export * from "./a"` in `index.ts`) are recorded as an edge to the
  index file but not followed further.
- **Dynamic imports** with computed paths are not resolved; literal `import("./x")` and
  `require("./x")` are.
- **Class fields** holding arrow functions are not extracted as methods.
- **JSX components** are called like functions, so `<Button />` is not an edge.
- No type inference: TypeScript types, interfaces and generics are not nodes.

## Adding another language

Each extractor returns the same `FileAnalysis` model, so the graph, the API and the UI need no
changes. The steps:

1. **Add the grammar** to the project: `uv add --active tree-sitter-<lang>` from `CodeAtlas/`.
2. **Write the extractor** in `src/codeatlas/analysis/<lang>.py`, following `javascript.py`.
   It walks the tree with the helpers in `analysis/syntax.py` and returns `FileAnalysis` with
   `symbols`, `imports`, `calls` and `language`.
3. **Register the extensions** in `src/codeatlas/analysis/languages.py`.
4. **Teach the resolver** how the language's imports map to project files. Add a branch in
   `_Resolver.resolve_imports` (see `_js_import`) and, if the language has a constructor name
   other than `__init__`, update `_ctor`. Bare-name lookups are already split by
   `_family`, so new languages need their own family only if they must not link to others.
5. **Add a fixture** under `tests/fixtures/` and tests in `tests/test_<lang>.py`. Use
   `tests/test_javascript.py` as the template.
6. **Check on real code:** run `python -m codeatlas.cli analyze <a real repo> --dry-run` and look
   at the `syntax errors` and `unresolved` counts.

Candidates for next, roughly in order of how often they appear in repositories: Go, Java,
Rust, C#. Each needs a grammar, an extractor and an import resolver of similar size.
