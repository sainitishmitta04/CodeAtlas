"""CodeAtlas Streamlit UI. Every graph shown here is a FastAPI response built from Neo4j results.

Run (from CodeAtlas/):  streamlit run ui/app.py
"""

import hashlib
import json
import os
import warnings

import httpx
import streamlit as st
from st_link_analysis import EdgeStyle, Event, NodeStyle, st_link_analysis
from st_link_analysis.component.styles import LinkAnalysisDeprecationWarning

# EdgeStyle warns about its own deprecated `labeled` argument even when we don't pass it.
warnings.filterwarnings("ignore", category=LinkAnalysisDeprecationWarning)

API = os.environ.get("CODEATLAS_API_URL", "http://127.0.0.1:8000")
LABELS = ["Project", "File", "Module", "Class", "Function"]
REL_TYPES = ["CONTAINS", "DEFINES", "HAS_METHOD", "IMPORTS", "INHERITS", "CALLS"]
NODE_STYLES = [
    NodeStyle("Project", "#1f2937", "caption", "inventory_2"),
    NodeStyle("File", "#2563eb", "caption", "description"),
    NodeStyle("Class", "#7c3aed", "caption", "category"),
    NodeStyle("Function", "#16a34a", "caption", "functions"),
    NodeStyle("Module", "#9ca3af", "caption", "extension"),
]
EDGE_STYLES = [
    EdgeStyle("CONTAINS", "#94a3b8", caption="label", directed=True),
    EdgeStyle("DEFINES", "#a78bfa", caption="label", directed=True),
    EdgeStyle("HAS_METHOD", "#c084fc", caption="label", directed=True),
    EdgeStyle("IMPORTS", "#3b82f6", caption="label", directed=True),
    EdgeStyle("INHERITS", "#f59e0b", caption="label", directed=True),
    EdgeStyle("CALLS", "#22c55e", caption="label", directed=True),
]
EXAMPLES = [
    "What does this repository do?",
    "Which files contain the main functionality?",
    "What depends on `{focus}`?",
    "Which functions are connected to `{focus}`?",
    "What could be affected if `{focus}` changes?",
]

st.set_page_config(page_title="CodeAtlas", layout="wide")


# ------------------------------------------------------------------ API helpers

def api_raw(method: str, path: str, timeout: float = 60, **kw) -> tuple[int | None, object]:
    """(status, body); status None means the backend could not be reached."""
    try:
        r = httpx.request(method, f"{API}{path}", timeout=timeout, **kw)
    except httpx.HTTPError as exc:
        return None, (
            f"Backend not reachable at {API} ({type(exc).__name__}). Start the API in a second "
            "terminal, from CodeAtlas/ with the workspace venv active:\n\n"
            "`source ../.venv/bin/activate && uvicorn codeatlas.api.main:app --port 8000`")
    try:
        body = r.json()
    except ValueError:
        body = r.text
    if r.status_code >= 400 and isinstance(body, dict) and "detail" in body:
        body = body["detail"]
    return r.status_code, body


def error_text(status: int | None, detail) -> str:
    if isinstance(detail, dict):
        detail = detail.get("message") or json.dumps(detail)
    elif isinstance(detail, list):  # FastAPI validation errors
        detail = "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
    return str(detail) if status is None else f"{status}: {detail}"


def api(method: str, path: str, timeout: float = 60, **kw):
    status, body = api_raw(method, path, timeout=timeout, **kw)
    if status is None or status >= 400:
        st.error(error_text(status, body))
        return None
    return body


def to_elements(payload: dict) -> dict:
    """Cytoscape elements; data comes verbatim from the Neo4j-backed payload."""
    nodes = []
    for n in payload.get("nodes", []):
        data = {k: (", ".join(map(str, v)) if isinstance(v, list) else v)
                for k, v in n["props"].items() if k not in ("label",)}
        data.update(id=n["id"], label=n["label"], caption=n["caption"])
        nodes.append({"data": data})
    edges = []
    for e in payload.get("edges", []):
        data = {k: (", ".join(map(str, v)) if isinstance(v, list) else v)
                for k, v in e["props"].items()}
        data.update(id=e["id"], label=e["type"], source=e["source"], target=e["target"])
        edges.append({"data": data})
    return {"nodes": nodes, "edges": edges}


def merge(base: dict | None, extra: dict) -> dict:
    if not base:
        return extra
    nodes = {n["id"]: n for n in base["nodes"]} | {n["id"]: n for n in extra["nodes"]}
    edges = {e["id"]: e for e in base["edges"]} | {e["id"]: e for e in extra["edges"]}
    cypher = base.get("cypher", []) + [c for c in extra.get("cypher", [])
                                       if c not in base.get("cypher", [])]
    return {**base, "nodes": list(nodes.values()), "edges": list(edges.values()), "cypher": cypher}


def node_by_id(payload: dict | None, node_id: str) -> dict | None:
    return next((n for n in (payload or {}).get("nodes", []) if n["id"] == node_id), None)


def show_graph(payload: dict, key: str, height: int = 620, actions: bool = True):
    if not payload or not payload.get("nodes"):
        st.info("No graph elements returned by Neo4j for this view.")
        return None
    st.caption(f"{len(payload['nodes'])} nodes · {len(payload['edges'])} relationships "
               "(from Neo4j)" + (" · result limit reached" if payload.get("truncated") else ""))
    if payload.get("note"):
        st.caption(payload["note"])
    return st_link_analysis(
        to_elements(payload), layout="cose", node_styles=NODE_STYLES, edge_styles=EDGE_STYLES,
        height=height, key=key, node_actions=["expand", "remove"] if actions else [],
        events=[Event("node_click", "tap", "node")])


def show_cypher(payload: dict):
    with st.expander("Cypher used (run it in Aura Query to see the same graph)"):
        for q in payload.get("cypher", []):
            st.code(q, language="cypher")
        if payload.get("params"):
            st.code(json.dumps(payload["params"], indent=2), language="json")


# ------------------------------------------------------------------ repository ingestion

SOURCES = ["Local Directory", "GitHub URL", "ZIP Upload"]


def target_card(t: dict):
    """The project a write would go to, shown before the write is confirmed."""
    state = ("**existing project — will be updated**" if t.get("exists") else
             "**new project**" if t.get("exists") is False else "existence unknown")
    st.markdown(f"**Target:** `{t['project_id']}` ({state})  \n"
                f"Name: **{t['project_name']}**  \nSource: `{t['label']}`")
    if t.get("ref"):
        st.caption(f"Ref: {t['ref']}" + (" (default branch)" if t.get("ref_is_default") else ""))
    if t.get("exists"):
        st.caption(f"Currently: {t.get('existing_name')} · status {t.get('existing_status')} · "
                   f"synced {t.get('existing_synced_at') or '—'}")
    for note in t.get("notes", []):
        st.caption("ℹ️ " + note)


def ingest_panel(projects: list[dict], max_upload: int | None):
    st.subheader("Analyze repository")
    kind = st.radio("Source", SOURCES, key="src_kind")
    name = st.text_input("Project name (optional)", key="src_name").strip()

    if kind == "Local Directory":
        path = st.text_input("Local repository path", placeholder="/path/to/repo",
                             key="src_path").strip()
        ready, inputs = bool(path), ("local", path, name)

        def build(dry: bool, guard: dict):
            return "/projects/analyze", {"json": {"path": path, "name": name or None,
                                                  "dry_run": dry, **guard}}
    elif kind == "GitHub URL":
        url = st.text_input("Public GitHub repository URL",
                            placeholder="https://github.com/owner/repo", key="src_url").strip()
        ref = st.text_input("Branch, tag or commit (optional)", key="src_ref",
                            help="Empty = the repository's default branch.").strip()
        st.caption("Public repositories only. No token is requested or stored; the source "
                   "archive is downloaded to a temporary folder and deleted after analysis.")
        ready, inputs = bool(url), ("github", url, ref, name)

        def build(dry: bool, guard: dict):
            return "/projects/analyze/github", {"json": {
                "url": url, "ref": ref or None, "name": name or None, "dry_run": dry, **guard}}
    else:
        upload = st.file_uploader("ZIP archive", type=["zip"], key="src_zip")
        zip_projects = {f"{p['name']} ({p['id']})": p["id"] for p in projects
                        if p.get("source_kind") == "zip"}
        mode = st.radio("Target", ["New project", "Update an existing ZIP project"],
                        key="src_zip_mode", disabled=not zip_projects,
                        help="Updating replaces that project's graph with this upload's files.")
        target_pid = None
        if mode != "New project" and zip_projects:
            target_pid = zip_projects[st.selectbox("Project to update", list(zip_projects),
                                                   key="src_zip_target")]
        data = upload.getvalue() if upload else b""
        too_big = bool(max_upload and len(data) > max_upload)
        if too_big:
            st.error(f"The file is {len(data):,} bytes; the limit is {max_upload:,}.")
        ready = bool(data) and not too_big
        inputs = ("zip", hashlib.sha256(data).hexdigest(), upload.name if upload else "",
                  name, target_pid)

        def build(dry: bool, guard: dict):
            params = {"filename": upload.name, "dry_run": dry, **guard}
            if name:
                params["name"] = name
            if target_pid:
                params["target_project_id"] = target_pid
            return "/projects/analyze/zip", {
                "content": data, "params": params, "headers": {"Content-Type": "application/zip"}}

    key = hashlib.sha1(repr(inputs).encode()).hexdigest()
    pending = ss.get("pending_target") if ss.get("pending_key") == key else None
    confirmed = False
    if pending:
        target_card(pending)
        confirmed = st.checkbox(
            "Yes, update this existing project in Neo4j Aura" if pending.get("exists")
            else "Yes, create this project in Neo4j Aura",
            key=f"confirm-{key}-{pending['project_id']}-{pending.get('exists')}")

    dry_clicked = st.button("Dry Run (no database writes)", disabled=not ready)
    write_clicked = st.button("Analyze & Write to Neo4j Aura", type="primary", disabled=not ready)
    st.caption("Dry Run never writes. *Analyze & Write* writes this project's graph to Neo4j "
               "Aura and asks you to confirm the target project first.")

    if not (dry_clicked or write_clicked):
        return
    dry = dry_clicked
    if write_clicked and pending and not confirmed:
        st.warning("Tick the confirmation above to write to Neo4j Aura.")
        return
    guard = ({"expected_project_id": pending["project_id"],
              "confirm_update": bool(pending.get("exists"))} if write_clicked and pending else {})
    path_, kw = build(dry, guard)
    stage = ("Resolving the repository on GitHub, downloading and " if kind == "GitHub URL" else
             "Uploading, validating and extracting the ZIP, then " if kind == "ZIP Upload" else "")
    with st.status(f"{stage}scanning and parsing" + (" (dry run)…" if dry else
                   " and syncing to Neo4j Aura…"), expanded=False) as status:
        code, body = api_raw("POST", path_, timeout=900, **kw)
        if code == 409 and isinstance(body, dict) and body.get("code") == "confirmation_required":
            ss.pending_key, ss.pending_target = key, body["target"]
            status.update(label="Confirm the target project", state="complete")
            st.rerun()
        if code is None or code >= 400:
            status.update(label="Analysis failed", state="error")
            st.error(error_text(code, body))
            ss.last_report = None
            return
        status.update(label="Dry run finished" if dry else "Synced to Neo4j Aura",
                      state="complete")
    ss.last_report = body
    if dry and body.get("source"):
        ss.pending_key, ss.pending_target = key, body["source"]  # ready to confirm a write
    else:
        ss.pending_key = ss.pending_target = None
    if not dry:
        ss.graph = None
    st.rerun()  # show the summary / confirmation and refresh the project list


def render_report(rep: dict | None):
    if not rep:
        return
    src = rep.get("source") or {}
    title = (f"Dry run (nothing written): {rep['project_name']}" if rep["dry_run"] else
             f"Synced to Neo4j Aura: {rep['project_name']} — status {rep.get('sync_status')}")
    with st.expander(title, expanded=True):
        st.markdown(f"**Project:** `{rep['project_id']}`  \n**Source:** "
                    f"{src.get('kind', 'local')} · `{src.get('label') or rep['root_path']}`" +
                    (f"  \n**Ref:** {src['ref']}" + (" (default branch)"
                     if src.get("ref_is_default") else "") if src.get("ref") else ""))
        if src.get("exists") is not None:
            verb = "would update" if rep["dry_run"] else "updated"
            st.caption((f"{verb} an existing project" if src["exists"] else
                        "new project" if rep["dry_run"] else "created a new project"))
        nodes, rels = rep.get("node_counts", {}), rep.get("rel_counts", {})
        c = st.columns(4)
        c[0].metric("Files analyzed", rep["files_analyzed"])
        c[1].metric("Nodes", sum(nodes.values()))
        c[2].metric("Relationships", sum(rels.values()))
        c[3].metric("Skipped files", len(rep.get("skipped", [])))
        c1, c2, c3 = st.columns(3)
        c1.markdown("**Nodes**")
        c1.dataframe([{"label": k, "count": v} for k, v in nodes.items()], hide_index=True)
        c2.markdown("**Relationships**")
        c2.dataframe([{"type": k, "count": v} for k, v in rels.items()], hide_index=True)
        c3.markdown("**Resolution**")
        c3.dataframe([{"metric": k, "value": v} for k, v in rep.get("resolution", {}).items()],
                     hide_index=True)
        if not rep["dry_run"]:
            st.caption(f"Run {rep.get('run_id')} · removed stale: {rep.get('swept_nodes', 0)} "
                       f"nodes, {rep.get('swept_rels', 0)} relationships · "
                       f"{rep.get('duration_s', 0)} s")
        for note in src.get("notes", []):
            st.info(note)
        if rep.get("files_with_syntax_errors"):
            st.warning("Files with syntax errors (partially parsed): "
                       + ", ".join(rep["files_with_syntax_errors"]))
        if rep.get("skipped"):
            with st.expander(f"Skipped files ({len(rep['skipped'])})"):
                st.dataframe(rep["skipped"], hide_index=True)
        with st.expander("Diagnostics (raw API response)"):
            st.json(rep, expanded=False)


# ------------------------------------------------------------------ sidebar

ss = st.session_state
ss.setdefault("graph", None)
ss.setdefault("selected", None)
ss.setdefault("last_event_ts", None)

with st.sidebar:
    st.title("CodeAtlas")
    health = api("GET", "/health", timeout=20)
    if health:
        neo = health["neo4j"]
        st.write(("🟢" if neo.get("ok") else "🔴") + " Neo4j" +
                 ("" if neo.get("ok") else f": {neo.get('error')}"))
        st.write(("🟢" if health["llm_configured"] else "⚪") + f" LLM ({health['model']})")

    projects = api("GET", "/projects", timeout=20) or []
    ingest_panel(projects, (health or {}).get("max_upload_bytes"))

render_report(ss.get("last_report"))

with st.sidebar:
    if not projects:
        st.info("No CodeAtlas projects in Neo4j yet.")
        st.stop()
    options = {f"{p['name']} ({p['id']})": p for p in projects}
    choice = st.selectbox("Project", list(options))
    proj = options[choice]
    if ss.get("project_id") != proj["id"]:
        ss.project_id, ss.graph, ss.selected = proj["id"], None, None
    st.subheader("Filters")
    labels = st.multiselect("Node types", LABELS, default=["Project", "File", "Class", "Function"])
    rels = st.multiselect("Relationship types", REL_TYPES, default=REL_TYPES)
    limit = st.slider("Max nodes", 20, 400, 150, step=10)
    depth = st.slider("Expansion depth", 1, 3, 1)
    if st.button("Reload graph"):
        ss.graph = None

pid = proj["id"]
if proj.get("sync_status") != "complete":
    st.warning(f"Project sync status: **{proj.get('sync_status')}**. The graph may be partially "
               f"updated ({proj.get('sync_error') or 'interrupted'}). Re-run Analyze to repair it.")

tab_graph, tab_stats, tab_paths, tab_ask = st.tabs(["Graph", "Statistics", "Paths & impact", "Ask"])

# ------------------------------------------------------------------ graph tab
with tab_graph:
    if ss.graph is None and labels:
        ss.graph = api("GET", "/graph/overview", params={
            "project_id": pid, "labels": labels, "rel_types": rels, "limit": limit})
    col_g, col_p = st.columns([3, 1])
    with col_g:
        event = show_graph(ss.graph, key=f"main-{pid}")
    if event and event.get("timestamp") != ss.last_event_ts:
        ss.last_event_ts = event.get("timestamp")
        action, data = event.get("action"), event.get("data", {})
        if action == "node_click":
            ss.selected = data.get("target_id")
        elif action == "expand":
            for nid in data.get("node_ids", []):
                node = node_by_id(ss.graph, nid)
                if node:
                    more = api("GET", "/graph/neighborhood", params={
                        "label": node["label"], "node_id": nid, "depth": depth,
                        "rel_types": rels, "limit": 80})
                    if more:
                        ss.graph = merge(ss.graph, more)
                        ss.selected = nid
            st.rerun()
        elif action == "remove":
            gone = set(data.get("node_ids", []))
            ss.graph["nodes"] = [n for n in ss.graph["nodes"] if n["id"] not in gone]
            ss.graph["edges"] = [e for e in ss.graph["edges"]
                                 if e["source"] not in gone and e["target"] not in gone]
    with col_p:
        st.markdown("**Selected node**")
        node = node_by_id(ss.graph, ss.selected) if ss.selected else None
        if node:
            st.write(f"`{node['label']}` **{node['caption']}**")
            st.json(node["props"], expanded=True)
            if st.button("Expand neighbours from Neo4j"):
                more = api("GET", "/graph/neighborhood", params={
                    "label": node["label"], "node_id": node["id"], "depth": depth,
                    "rel_types": rels, "limit": 80})
                if more:
                    ss.graph = merge(ss.graph, more)
                    st.rerun()
            if st.button("Show only this neighbourhood"):
                ss.graph = api("GET", "/graph/neighborhood", params={
                    "label": node["label"], "node_id": node["id"], "depth": depth,
                    "rel_types": rels, "limit": 120})
                st.rerun()
        else:
            st.caption("Click a node to inspect it; double-click (or the expand button in the "
                       "graph toolbar) to load its neighbours from Neo4j.")
    if ss.graph:
        show_cypher(ss.graph)

# ------------------------------------------------------------------ stats tab
with tab_stats:
    stats = api("GET", f"/projects/{pid}/stats")
    if stats:
        c1, c2 = st.columns(2)
        c1.markdown("**Nodes**")
        c1.dataframe([{"label": k, "count": v} for k, v in stats["nodes"].items()],
                     hide_index=True)
        c2.markdown("**Relationships**")
        c2.dataframe([{"type": k, "count": v} for k, v in stats["relationships"].items()],
                     hide_index=True)
        c1.markdown("**CALLS by confidence**")
        c1.dataframe(stats["call_confidence"], hide_index=True)
        c2.markdown("**IMPORTS by resolution**")
        c2.dataframe(stats["import_resolution"], hide_index=True)
        st.markdown("**Most imported files**")
        st.dataframe(stats["top_imported_files"], hide_index=True)
        st.markdown("**Most called functions**")
        st.dataframe(stats["top_called_functions"], hide_index=True)
        if st.button("Run graph integrity checks"):
            st.json(api("GET", f"/projects/{pid}/integrity"))

# ------------------------------------------------------------------ paths & impact tab
with tab_paths:
    files = api("GET", f"/projects/{pid}/files") or []
    file_ids = {f["path"]: f["id"] for f in files}
    st.subheader("Dependency path (shortest IMPORTS chain)")
    if len(file_ids) >= 2:
        c1, c2 = st.columns(2)
        src = c1.selectbox("From file", list(file_ids), key="path_from")
        dst = c2.selectbox("To file", list(file_ids), index=1, key="path_to")
        if st.button("Find path") and src != dst:
            ss.path_graph = api("GET", "/graph/path", params={
                "mode": "dependency", "from_id": file_ids[src], "to_id": file_ids[dst]})
        if ss.get("path_graph"):
            show_graph(ss.path_graph, key=f"path-{pid}", height=380, actions=False)
            show_cypher(ss.path_graph)

    st.subheader("Change impact")
    target = st.selectbox("Changed file", list(file_ids), key="impact_file") if file_ids else None
    sel = node_by_id(ss.graph, ss.selected) if ss.selected else None
    use_sel = sel is not None and sel["label"] in ("File", "Function", "Class", "Module") and \
        st.checkbox(f"Use selected graph node instead ({sel['label']} {sel['caption']})")
    imp_depth = st.slider("Impact depth", 1, 3, 2, key="impact_depth")
    if st.button("Analyze impact") and (target or use_sel):
        label, nid = (sel["label"], sel["id"]) if use_sel else ("File", file_ids[target])
        ss.impact = api("GET", "/graph/impact",
                        params={"label": label, "node_id": nid, "depth": imp_depth})
    imp = ss.get("impact")
    if imp:
        c1, c2 = st.columns(2)
        c1.markdown("**Defined in target**")
        c1.write(imp["defined_symbols"] or "—")
        c1.markdown("**Files importing it (file-level)**")
        c1.dataframe(imp["importing_files"], hide_index=True)
        c2.markdown("**Functions calling into it (function-level)**")
        c2.dataframe(imp["calling_functions"], hide_index=True)
        c2.markdown("**Subclasses**")
        c2.dataframe(imp["subclasses"], hide_index=True)
        st.markdown("**Candidate affected files:** " + (", ".join(imp["affected_files"]) or "none found"))
        with st.expander("What this analysis cannot see"):
            for n in imp["coverage_notes"]:
                st.write("- " + n)
        show_graph(imp["graph"], key=f"impact-{pid}", height=420, actions=False)
        show_cypher(imp["graph"])

# ------------------------------------------------------------------ ask tab
with tab_ask:
    sel = node_by_id(ss.graph, ss.selected) if ss.selected else None
    focus_name = (sel["props"].get("path") or sel["props"].get("qualname") or sel["caption"]) \
        if sel else "graph/db.py"
    st.caption("Answers use fixed Cypher retrieval patterns over the stored graph; the LLM only "
               "writes prose from the retrieved facts.")
    cols = st.columns(len(EXAMPLES))
    for i, ex in enumerate(EXAMPLES):
        if cols[i].button(ex.format(focus=focus_name), key=f"ex{i}"):
            ss.question = ex.format(focus=focus_name)
    question = st.text_input("Question", key="question")
    if st.button("Ask", type="primary") and question:
        with st.spinner("Retrieving from Neo4j and asking the model..."):
            ss.answer = api("POST", "/ask", timeout=120, json={
                "project_id": pid, "question": question,
                "focus_id": sel["id"] if sel else None, "focus_label": sel["label"] if sel else None})
    ans = ss.get("answer")
    if ans:
        st.caption(f"intent: {ans['intent']} · entity: "
                   f"{ans['entity']['caption'] if ans.get('entity') else '—'} · "
                   f"LLM used: {ans['llm_used']}")
        st.markdown(ans["answer"])
        if ans["caveats"]:
            with st.expander("Caveats"):
                for c in ans["caveats"]:
                    st.write("- " + c)
        with st.expander(f"Evidence facts ({len(ans['facts'])})"):
            st.code("\n".join(ans["facts"]))
        show_graph(ans["evidence"], key=f"ask-{pid}", height=420, actions=False)
        show_cypher(ans["evidence"])
