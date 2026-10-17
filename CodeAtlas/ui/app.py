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
# The Graph tab loads pages automatically up to this many nodes; beyond it, loading the rest
# is an explicit click (very large graphs make the browser layout slow).
AUTO_LOAD_MAX_NODES = int(os.environ.get("CODEATLAS_AUTO_LOAD_NODES", "2000"))
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
        component = body.get("component")
        body = body["detail"]
        if isinstance(body, str) and component:
            body = f"[{component}] {body}"  # which dependency failed: neo4j, llm, github, ...
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
        llm = health.get("llm") or {"configured": health["llm_configured"]}
        last = llm.get("last_call") or {}
        if not llm.get("configured"):
            st.write(f"⚪ LLM ({health['model']}): not configured")
        elif last.get("state") == "ok":
            st.write(f"🟢 LLM ({health['model']}): last call OK")
        elif last.get("state") == "failed":
            st.write(f"🔴 LLM ({health['model']}): last call failed: {last.get('code')}"
                     + (f" (HTTP {last['provider_status']})" if last.get("provider_status") else ""))
        else:
            st.write(f"⚪ LLM ({health['model']}): configured, not verified yet")
        ignored = (health.get("config") or {}).get("shell_vars_ignored_in_favour_of_env_file")
        if ignored:
            st.caption("Shell variables ignored in favour of the workspace .env: "
                       + ", ".join(ignored))

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
    labels = st.multiselect("Node types", LABELS, default=LABELS)
    rels = st.multiselect("Relationship types", REL_TYPES, default=REL_TYPES)
    page_size = st.slider("Nodes per page", 100, 2000, 500, step=100,
                          help="The graph is loaded page by page until it is complete; pages "
                               f"load automatically up to {AUTO_LOAD_MAX_NODES} nodes.")
    st.caption("A relationship is drawn when both of its endpoints' node types are selected.")
    depth = st.slider("Expansion depth", 1, 3, 1)
    if st.button("Reload graph"):
        ss.graph, ss.graph_meta = None, {}

pid = proj["id"]
if proj.get("sync_status") != "complete":
    st.warning(f"Project sync status: **{proj.get('sync_status')}**. The graph may be partially "
               f"updated ({proj.get('sync_error') or 'interrupted'}). Re-run Analyze to repair it.")

tab_graph, tab_stats, tab_paths, tab_ask, tab_arch, tab_tours, tab_explain, tab_search = st.tabs(
    ["Graph", "Statistics", "Paths & impact", "Ask", "Architecture", "Tours", "Explain",
     "Search"])

# ------------------------------------------------------------------ graph tab

def load_graph_pages(until_nodes: int) -> None:
    """Load overview pages from the current cursor until the selection is complete or
    `until_nodes` selected nodes are loaded. Pages merge into ss.graph by node/edge id."""
    meta = ss.graph_meta
    for _ in range(1000):  # hard stop; a page always advances the cursor
        if meta.get("complete") or (ss.graph is not None and not meta.get("cursor")):
            return
        page = api("GET", "/graph/overview", params={
            "project_id": pid, "labels": labels, "rel_types": rels, "limit": page_size,
            "after": meta.get("cursor") or ""})
        if page is None:
            meta["error"] = True
            return
        ss.graph = page if ss.graph is None else merge(ss.graph, page)
        meta["totals"] = page.get("totals") or meta.get("totals")
        meta["cursor"], meta["complete"] = page.get("next_cursor"), bool(page.get("complete"))
        meta["loaded"] = meta.get("loaded", 0) + (page.get("page_nodes") or 0)
        ss.graph["truncated"], ss.graph["note"] = not meta["complete"], None
        if meta["loaded"] >= until_nodes:
            return


def graph_status() -> None:
    """Displayed vs total counts, and whether the full selection has been loaded."""
    meta, g = ss.graph_meta, ss.graph or {"nodes": [], "edges": []}
    totals = meta.get("totals") or {}
    shown_nodes, shown_edges = len(g["nodes"]), len(g["edges"])
    if meta.get("mode") == "neighbourhood":
        st.info(f"Neighbourhood view ({shown_nodes} nodes, {shown_edges} relationships), not "
                "the full graph. Click **Reload graph** to return to the complete graph.")
        return
    sel_n, sel_r = totals.get("selected_nodes"), totals.get("selected_relationships")
    text = (f"Displayed **{shown_nodes}** nodes / {sel_n if sel_n is not None else '?'} selected "
            f"· **{shown_edges}** relationships / {sel_r if sel_r is not None else '?'} selected")
    if meta.get("complete"):
        st.success("✅ Complete graph for the selected types. " + text)
    else:
        remaining = (sel_n - meta.get("loaded", 0)) if sel_n is not None else None
        st.warning("⚠️ Partial view: more data remains to be loaded. " + text +
                   (f" · {remaining} more nodes" if remaining is not None else ""))
        c1, c2 = st.columns(2)
        if c1.button("Load next page"):
            load_graph_pages(meta.get("loaded", 0) + 1)
            st.rerun()
        if c2.button("Load all remaining", help="May be slow to lay out for very large graphs."):
            load_graph_pages(10 ** 9)
            st.rerun()
    notes = [f"Project total: {totals.get('nodes', '?')} nodes, "
             f"{totals.get('relationships', '?')} relationships."]
    if totals.get("hidden_relationships"):
        notes.append(f"{totals['hidden_relationships']} relationships are hidden because one "
                     "endpoint's node type is not selected.")
    st.caption(" ".join(notes))


with tab_graph:
    graph_key = (pid, tuple(labels), tuple(rels), page_size)
    if ss.get("graph_meta", {}).get("key") != graph_key:
        ss.graph, ss.graph_meta = None, {"key": graph_key, "mode": "overview"}
    if ss.graph is None and labels and not ss.graph_meta.get("error"):
        ss.graph_meta = {"key": graph_key, "mode": "overview"}
        load_graph_pages(AUTO_LOAD_MAX_NODES)
    if ss.graph is not None or ss.graph_meta.get("totals"):
        graph_status()
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
                ss.graph_meta["mode"] = "neighbourhood"
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
            code, body = api_raw("POST", "/ask", timeout=150, json={
                "project_id": pid, "question": question,
                "focus_id": sel["id"] if sel else None, "focus_label": sel["label"] if sel else None})
        if code is not None and code < 400:
            ss.answer, ss.ask_error = body, None
        else:
            ss.answer = None
            ss.ask_error = ({**body, "http_status": code} if isinstance(body, dict)
                            else {"message": error_text(code, body), "http_status": code})
        st.rerun()  # refresh the sidebar's LLM status with this call's outcome
    err = ss.get("ask_error")
    if err:
        if err.get("component") == "llm":
            st.error(f"**No answer: the LLM request failed** (`{err.get('code')}`"
                     + (f", provider HTTP {err['provider_status']}" if err.get("provider_status")
                        else "") + f"). {err.get('message', '')}")
            if err.get("hint"):
                st.info(err["hint"])
        else:
            st.error(f"**No answer.** {err.get('message')}")
        diag = {k: err.get(k) for k in ("code", "component", "http_status", "provider_status",
                                         "request_id", "provider_message", "attempts",
                                         "retryable") if err.get(k) is not None}
        with st.expander("Diagnostics (contains no credentials)"):
            st.json(diag)
        ctx = err.get("graph_context")
        if ctx:
            st.markdown("**Graph facts retrieved from Neo4j (not an LLM answer)**")
            with st.expander(f"Evidence facts ({len(ctx.get('facts', []))})", expanded=True):
                st.code("\n".join(ctx.get("facts", [])) or "(no facts)")
            show_graph(ctx.get("evidence") or {}, key=f"ask-failed-{pid}", height=380,
                       actions=False)
    ans = ss.get("answer")
    if ans and ans.get("kind") == "clarification":
        st.info(ans["answer"])
        st.caption("CodeAtlas needs a clearer target; the LLM was not called.")
    elif ans:
        st.caption(f"intent: {ans['intent']} · entity: "
                   f"{ans['entity']['caption'] if ans.get('entity') else '—'} · "
                   f"answer written by the LLM from {len(ans['facts'])} graph facts")
        st.markdown(ans["answer"])
        if ans["caveats"]:
            with st.expander("Caveats"):
                for c in ans["caveats"]:
                    st.write("- " + c)
        with st.expander(f"Evidence facts ({len(ans['facts'])})"):
            st.code("\n".join(ans["facts"]))
        show_graph(ans["evidence"], key=f"ask-{pid}", height=420, actions=False)
        show_cypher(ans["evidence"])


# ------------------------------------------------------------------ Phase 2: architecture intelligence
# All four tabs read the same project snapshot through the API; nothing here is canned.

LAYER_COLORS = {"presentation": "#f472b6", "api": "#60a5fa", "business": "#34d399",
                "domain": "#a78bfa", "data": "#fbbf24", "infrastructure": "#f87171",
                "utilities": "#94a3b8", "tests": "#cbd5e1", "unclassified": "#e5e7eb"}
CONF_ICON = {"high": "●●●", "medium": "●●○", "low": "●○○", "none": "○○○"}


def get_architecture() -> dict | None:
    """Server-side cached per sync run, so this is one cheap request per rerun."""
    status, body = api_raw("GET", f"/projects/{pid}/architecture", timeout=120)
    if status is None or status >= 400:
        st.error(error_text(status, body))
        return None
    return body


def request_tour(kind: str, target: str | None = None, narrate: bool = False):
    params = {"kind": kind, "narrate": narrate}
    if target:
        params["target"] = target
    with st.spinner("Building the tour from the graph" + (" and narrating…" if narrate else "…")):
        t = api("GET", f"/projects/{pid}/tours", timeout=180, params=params)
    if t:
        ss.tour, ss.tour_step = t, 0


def request_explanation(node_id: str, use_llm: bool = False):
    with st.spinner("Collecting evidence" + (" and asking the LLM…" if use_llm else "…")):
        e = api("GET", f"/projects/{pid}/explain", timeout=150,
                params={"node_id": node_id, "use_llm": use_llm})
    if e:
        ss.explanation = e


def layer_dot(arch: dict, max_components: int = 60) -> str:
    comps = sorted(arch["components"], key=lambda c: -len(c["files"]))[:max_components]
    shown = {c["name"] for c in comps}
    q = lambda x: '"' + str(x).replace('"', "'") + '"'  # noqa: E731
    lines = ["digraph G {", "rankdir=TB; compound=true; node [shape=box, style=filled, "
             "fontsize=10]; edge [fontsize=8, color=\"#64748b\"];"]
    for i, layer in enumerate(arch["layers"]):
        lines.append(f"subgraph cluster_{i} {{ label={q(layer['title'])}; style=rounded; "
                     f"color={q(LAYER_COLORS.get(layer['layer'], '#ddd'))};")
        for c in comps:
            if c["layer"] == layer["layer"]:
                lines.append(f"{q(c['name'])} [fillcolor={q(LAYER_COLORS.get(c['layer']))}, "
                             f"tooltip={q(c['confidence'] + ' confidence')}];")
        lines.append("}")
    bad = {(v["source_component"], v["target_component"]) for v in arch["violations"]}
    for c in comps:
        for d in c["depends_on"]:
            if d["component"] in shown:
                color = ', color="#dc2626", penwidth=2' if (c["name"], d["component"]) in bad else ""
                lines.append(f"{q(c['name'])} -> {q(d['component'])} "
                             f"[label={q(d['imports'])}{color}];")
    lines.append("}")
    return "\n".join(lines)


def render_ref_list(title: str, refs: list[dict], total: int | None, key: str):
    if not refs:
        return
    more = f" (showing {len(refs)} of {total})" if total and total > len(refs) else ""
    st.markdown(f"**{title}**{more}")
    for i, r in enumerate(refs):
        c1, c2 = st.columns([5, 1])
        loc = r.get("location") or {}
        extra = (f" · lines {', '.join(map(str, r['lines'][:5]))}" if r.get("lines") else "") + \
                (f" · {r['confidence']}" if r.get("confidence") else "") + \
                (f" · {r['layer']}" if r.get("layer") else "")
        c1.caption(f"`{r['label']}` {r['display']}{extra}")
        if r["label"] != "Module" and c2.button("Explain", key=f"{key}-{i}-{r['node_id']}"):
            request_explanation(r["node_id"])
            st.rerun()


def render_explanation(exp: dict, key: str):
    f = exp["facts"]
    loc = f.get("location") or {}
    st.markdown(f"### {f['kind'].title()} `{f.get('qualname') or f['name']}`")
    st.caption(f"{loc.get('file_path', '—')}"
               + (f" · lines {loc.get('start_line')}–{loc.get('end_line')}"
                  if loc.get("start_line") else "")
               + (f" · layer: **{f['layer']}** (inferred), component {f['component']}"
                  if f.get("layer") else ""))
    c_facts, c_more = st.columns([3, 2])
    with c_facts:
        st.markdown("#### Observed facts (from the graph)")
        if f.get("signature"):
            st.code(f["signature"], language="python")
        for label, value in (("Decorators", f.get("decorators")), ("Bases", f.get("bases")),
                             ("External calls", f.get("external_calls")),
                             ("Unresolved calls", f.get("unresolved_calls"))):
            if value:
                st.markdown(f"**{label}:** " + ", ".join(f"`{v}`" for v in value))
        if f.get("docstring"):
            st.markdown("**Docstring**")
            st.text(f["docstring"][:1500])
        counts = f.get("counts", {})
        render_ref_list("Belongs to", f.get("belongs_to", []), None, f"{key}-own")
        render_ref_list("Calls", f.get("calls", []), counts.get("calls"), f"{key}-calls")
        render_ref_list("Called by", f.get("callers", []),
                        counts.get("callers") or counts.get("callers_of_methods"), f"{key}-callers")
        render_ref_list("Methods", f.get("methods", []), counts.get("methods"), f"{key}-meth")
        render_ref_list("Parent classes", f.get("parents", []), None, f"{key}-par")
        render_ref_list("Subclasses", f.get("subclasses", []), counts.get("subclasses"),
                        f"{key}-sub")
        render_ref_list("Defines", f.get("defines", []), counts.get("defines"), f"{key}-def")
        render_ref_list("Imports", f.get("imports", []), counts.get("imports"), f"{key}-imp")
        render_ref_list("Imported by", f.get("imported_by", []), counts.get("imported_by"),
                        f"{key}-impby")
        if f.get("side_effect_hints"):
            st.markdown("**Possible side effects (from observed calls)**")
            for h in f["side_effect_hints"]:
                st.write("- " + h)
    with c_more:
        st.markdown("#### Unknowns")
        for u in f.get("unknowns", []):
            st.write("- " + u)
        ex = f.get("source_excerpt")
        if ex:
            with st.expander(f"Source excerpt (verified unchanged) · lines "
                             f"{ex['start_line']}–{ex['end_line']}"):
                st.code(ex["text"], language="python")
        if f["label"] == "Function":
            if st.button("Execution-path tour from here", key=f"{key}-exec"):
                request_tour("execution", f["node_id"])
                st.success("Tour ready: open the **Tours** tab.")
        if f["label"] in ("Function", "Class", "File"):
            if st.button("Impact tour", key=f"{key}-impact"):
                request_tour("impact", f["node_id"])
                st.success("Tour ready: open the **Tours** tab.")
    st.markdown("#### Interpretation")
    if exp.get("interpretation"):
        st.caption("Generated by the LLM from the observed facts above; verify against them.")
        st.markdown(exp["interpretation"])
    elif exp.get("llm_error"):
        err = exp["llm_error"]
        st.warning(f"No LLM interpretation: {err.get('message')} ({err.get('code')}). "
                   "The observed facts above are complete without it.")
    else:
        st.caption("No interpretation requested: the facts above are the evidence.")
    if st.button("Generate LLM interpretation", key=f"{key}-llm"):
        request_explanation(f["node_id"], use_llm=True)
        st.rerun()


# ------------------------------------------------------------------ architecture tab
with tab_arch:
    arch = get_architecture()
    if arch:
        st.caption("Layers are **inferred** from observed evidence (framework imports, "
                   "decorators, base classes, names, import structure). Dependencies are "
                   "observed imports between components.")
        cols = st.columns(len(arch["layers"]) or 1)
        for col, layer in zip(cols, arch["layers"]):
            col.metric(layer["title"], f"{len(layer['components'])} comp.",
                       f"{layer['file_count']} files", delta_color="off")
        if arch["components"]:
            st.graphviz_chart(layer_dot(arch))
            if len(arch["components"]) > 60:
                st.caption("Diagram shows the 60 largest components; all are listed below.")
        for note in arch["notes"]:
            st.caption("ℹ️ " + note)
        if arch["violations"]:
            with st.expander(f"⚠️ Possible layering violations ({len(arch['violations'])})"):
                for v in arch["violations"]:
                    st.write(f"- `{v['source_component']}` [{v['source_layer']}] → "
                             f"`{v['target_component']}` [{v['target_layer']}] · {v['imports']} "
                             f"import(s), e.g. {v['example']}")
        if arch["component_cycles"] or arch["file_cycles"]:
            with st.expander("🔁 Cycles (tightly coupled)"):
                for c in arch["component_cycles"]:
                    st.write("- components: " + " ↔ ".join(c))
                for c in arch["file_cycles"]:
                    st.write("- files: " + " ↔ ".join(c))

        names = [c["name"] for c in arch["components"]]
        if names:
            st.subheader("Inspect a component")
            by_name = {c["name"]: c for c in arch["components"]}
            comp_name = st.selectbox(
                "Component", names, key="arch_comp",
                format_func=lambda n: f"{n} · {by_name[n]['layer']} "
                                      f"{CONF_ICON[by_name[n]['confidence']]}")
            c = by_name[comp_name]
            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**Layer:** {c['layer']} ({c['confidence']} confidence)"
                            + (f" — ambiguous between {', '.join(c['ambiguous_between'])}"
                               if c["ambiguous_between"] else ""))
                st.markdown("**Evidence (observed)**")
                for ev in c["evidence"] or ["no evidence found"]:
                    st.write("- " + ev)
                st.markdown(f"**Files ({len(c['files'])})** · {c['symbol_count']} symbols · "
                            f"{c['loc']} lines")
                st.caption(", ".join(c["files"][:30]) + (" …" if len(c["files"]) > 30 else ""))
            with c2:
                st.markdown("**Depends on**")
                st.dataframe(c["depends_on"] or [{"component": "—"}], hide_index=True)
                st.markdown("**Used by**")
                st.dataframe(c["used_by"] or [{"component": "—"}], hide_index=True)
                if c["entry_points"]:
                    st.markdown("**Entry points:** " + ", ".join(c["entry_points"]))
            b1, b2, b3 = st.columns(3)
            for col, kind, label in ((b1, "component", "Component tour"),
                                     (b2, "dependency", "Dependency tour"),
                                     (b3, "impact", "Impact tour")):
                if col.button(label, key=f"arch-{kind}"):
                    request_tour(kind, comp_name)
                    st.success("Tour ready: open the **Tours** tab.")
        with st.expander("Per-file classification and evidence"):
            st.dataframe([{"file": f["path"], "component": f["component"], "layer": f["layer"],
                           "confidence": f["confidence"],
                           "evidence": "; ".join(e["detail"] for e in f["evidence"][:4])}
                          for f in arch["files"]], hide_index=True)

# ------------------------------------------------------------------ tours tab
with tab_tours:
    kinds = {"overview": "Project overview", "entry": "Entry point",
             "component": "Component", "execution": "Execution path",
             "dependency": "Dependencies", "impact": "Change impact"}
    c1, c2, c3 = st.columns([2, 3, 1])
    kind = c1.selectbox("Tour", list(kinds), format_func=kinds.get, key="tour_kind")
    target = None
    comp_names = [c["name"] for c in (arch or {}).get("components", [])]
    if kind in ("component", "dependency"):
        target = c2.selectbox("Component", comp_names, key="tour_comp") if comp_names else None
    elif kind == "execution":
        fns = api("GET", f"/projects/{pid}/functions") or []
        fmap = {f"{f['qualname']} ({f['file']})": f["id"] for f in fns}
        choice = c2.selectbox("Start function", list(fmap), key="tour_fn") if fmap else None
        target = fmap.get(choice) if choice else None
    elif kind == "impact":
        opts = {f"component: {n}": n for n in comp_names}
        opts.update({f"file: {f['path']}": f["id"]
                     for f in (api("GET", f"/projects/{pid}/files") or [])})
        choice = c2.selectbox("Changed component or file", list(opts), key="tour_imp") \
            if opts else None
        target = opts.get(choice) if choice else None
    elif kind == "entry":
        eps = (arch or {}).get("entry_points", [])
        target = c2.selectbox("Entry file", ["(auto-detect)"] + eps, key="tour_entry")
        target = None if target == "(auto-detect)" else target
    narrate = c3.checkbox("Narrate (LLM)", key="tour_narrate",
                          help="Ask the LLM to narrate the deterministic steps.")
    b1, b2 = st.columns([1, 4])
    if b1.button("Start tour", type="primary", disabled=kind not in ("overview", "entry")
                 and not target):
        request_tour(kind, target, narrate)
    if b2.button("Back to the architecture overview"):
        request_tour("overview")

    t = ss.get("tour")
    if t:
        st.markdown(f"## {t['title']}")
        st.caption(t["purpose"])
        for n in t.get("notes", []):
            st.info(n)
        if t.get("llm_error"):
            st.warning(f"No narration: {t['llm_error'].get('message')} "
                       f"({t['llm_error'].get('code')}). The steps below are complete.")
        if t.get("narrative"):
            with st.expander("Narration (generated by the LLM from the steps below)",
                             expanded=True):
                st.markdown(t["narrative"])
        steps = t["steps"]
        if steps:
            i = min(ss.get("tour_step", 0), len(steps) - 1)
            n1, n2, n3 = st.columns([1, 4, 1])
            if n1.button("◀ Previous", disabled=i == 0):
                ss.tour_step = i - 1
                st.rerun()
            n2.progress((i + 1) / len(steps), text=f"Step {i + 1} of {len(steps)}"
                        + (" (tour truncated)" if t.get("truncated") else ""))
            if n3.button("Next ▶", disabled=i >= len(steps) - 1):
                ss.tour_step = i + 1
                st.rerun()
            step = steps[i]
            st.markdown(f"### {step['index'] + 1}. {step['title']}")
            tags = [f"layer: **{step['layer']}**" if step.get("layer") else None,
                    f"component: `{step['component']}`" if step.get("component") else None,
                    "observed" if step["observed"] else "*inferred*"]
            st.caption(" · ".join(x for x in tags if x))
            st.write(step["explanation"])
            loc = step.get("location") or {}
            if loc.get("file_path"):
                st.caption(f"📄 {loc['file_path']}"
                           + (f" lines {loc['start_line']}–{loc['end_line']}"
                              if loc.get("start_line") else ""))
            for ev in step.get("evidence", []):
                st.caption("• " + ev)
            if step.get("node_id") and step.get("node_label") in ("Function", "Class", "File",
                                                                    "Module"):
                if st.button(f"Explain {step['node_label'].lower()}", key=f"tour-explain-{i}"):
                    request_explanation(step["node_id"])
            if step.get("kind") == "component" and step.get("component") in comp_names:
                if st.button("Tour this component", key=f"tour-comp-{i}"):
                    request_tour("component", step["component"])
                    st.rerun()
            exp = ss.get("explanation")
            if exp and exp["facts"]["node_id"] == step.get("node_id"):
                with st.container(border=True):
                    render_explanation(exp, key="tour-exp")
            with st.expander("All steps"):
                for s_ in steps:
                    st.write(f"{s_['index'] + 1}. {s_['title']} — "
                             f"{'observed' if s_['observed'] else 'inferred'}")
        else:
            st.info("This tour has no steps for the selected project (see the notes above).")

# ------------------------------------------------------------------ explain tab
with tab_explain:
    st.caption("Explanations separate **observed facts** (graph + verified source), "
               "**unknowns**, and an optional **LLM interpretation** written only from those "
               "facts.")
    c1, c2 = st.columns([3, 1])
    name_q = c1.text_input("Find a symbol by name", key="explain_q",
                           placeholder="e.g. sync_project, Cart, graph/db.py")
    sel_node = node_by_id(ss.graph, ss.selected) if ss.selected else None
    if sel_node and c2.button(f"Use selected graph node ({sel_node['caption'][:20]})"):
        request_explanation(sel_node["id"])
    if name_q:
        found = api("GET", f"/projects/{pid}/search", params={"q": name_q, "limit": 15}) or {}
        options = {f"{r['label']}: {r['display']}": r["node_id"]
                   for r in found.get("results", [])}
        if options:
            pick = st.selectbox("Matches", list(options), key="explain_pick")
            if st.button("Explain", type="primary", key="explain_go"):
                request_explanation(options[pick])
        else:
            st.info("No symbol matches that name in this project.")
    exp = ss.get("explanation")
    if exp:
        with st.container(border=True):
            render_explanation(exp, key="explain-tab")

# ------------------------------------------------------------------ search tab
with tab_search:
    st.caption("**Lexical + graph search** — matches identifiers, paths, docstrings, "
               "parameters, decorators and called/imported names, with synonym expansion and "
               "graph-based ranking. It is not embedding-based semantic search.")
    q = st.text_input("Describe what you are looking for", key="search_q",
                      placeholder="e.g. where is the database connection initialized?")
    f1, f2 = st.columns(2)
    s_labels = f1.multiselect("Symbol types", ["Function", "Class", "File", "Module"],
                              key="search_labels")
    layer_opts = [layer["layer"] for layer in (arch or {}).get("layers", [])]
    s_layers = f2.multiselect("Layers", layer_opts, key="search_layers")
    params_key = (q, tuple(s_labels), tuple(s_layers))
    if ss.get("search_key") != params_key:
        ss.search_key, ss.search_limit = params_key, 10
    if q:
        res = api("GET", f"/projects/{pid}/search", params={
            "q": q, "labels": s_labels, "layers": s_layers, "limit": ss.search_limit})
        if res:
            if res["expanded_terms"]:
                st.caption("Query terms: " + ", ".join(res["terms"]) + " · expanded: " + "; ".join(
                    f"{t} → {', '.join(v)}" for t, v in res["expanded_terms"].items()))
            if not res["results"]:
                st.info("No matches. Try other words, or remove the filters.")
            st.caption(f"{res['total']} match(es); showing {len(res['results'])}.")
            for i, r in enumerate(res["results"]):
                with st.container(border=True):
                    a, b = st.columns([5, 1])
                    loc = r.get("location") or {}
                    a.markdown(f"`{r['label']}` **{r['name']}**"
                               + (f" · layer {r['layer']}" if r.get("layer") else ""))
                    a.caption(f"📄 {loc.get('file_path', '—')}"
                              + (f" lines {loc['start_line']}–{loc['end_line']}"
                                 if loc.get("start_line") else "")
                              + f" · score {r['score']}")
                    if r.get("summary"):
                        a.caption(f"“{r['summary']}”")
                    a.caption("Why: " + "; ".join(r["matched"]))
                    if r["label"] != "Module" and b.button("Explain", key=f"search-x-{i}"):
                        request_explanation(r["node_id"])
            if res["total"] > len(res["results"]) and st.button("Show more results"):
                ss.search_limit += 10
                st.rerun()
            exp = ss.get("explanation")
            if exp and any(r["node_id"] == exp["facts"]["node_id"] for r in res["results"]):
                with st.container(border=True):
                    render_explanation(exp, key="search-exp")
