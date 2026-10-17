import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { api, buildQuery, request } from "./api";
import { ApiError, detailMessage, errorText } from "./errors";
import { EMPTY_GRAPH, mergeGraphs, removeNodes, drawableEdges } from "./graph";
import { buildHash, parseHash } from "./router";
import { layoutLayers } from "./layerLayout";
import { fmtLocation } from "./format";
import { Prose } from "../components/feedback/Prose";
import type { ArchitectureReport, GraphPayload } from "./types";

function respond(status: number, body: unknown) {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  return vi.fn(async (_url: string, _init?: RequestInit) => new Response(typeof body === "string" ? body : JSON.stringify(body), { status }));
}

/** Resolve to the rejection of `p` (and fail if it succeeds). */
function failure(p: Promise<unknown>): Promise<ApiError> {
  return p.then(() => { throw new Error("expected the request to fail"); }, (e: unknown) => e as ApiError);
}

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("repeats list parameters the way FastAPI expects", () => {
    expect(buildQuery({ q: "db", labels: ["File", "Class"], limit: 10, skip: undefined, none: null }))
      .toBe("?q=db&labels=File&labels=Class&limit=10");
    expect(buildQuery({})).toBe("");
  });

  it("calls the real endpoint paths with encoded ids", async () => {
    const fetch = respond(200, { results: [] });
    vi.stubGlobal("fetch", fetch);
    await api.search("proj/1", { q: "open db", labels: ["Function"], limit: 5, offset: 10 });
    expect(fetch.mock.calls[0][0]).toBe("/api/projects/proj%2F1/search?q=open+db&labels=Function&limit=5&offset=10");
    await api.tour("p", { kind: "overview", target: null, narrate: false });
    expect(fetch.mock.calls[1][0]).toBe("/api/projects/p/tours?kind=overview");
    await api.explain("p", "p:function:a.py:f", true);
    expect(fetch.mock.calls[2][0]).toBe("/api/projects/p/explain?node_id=p%3Afunction%3Aa.py%3Af&use_llm=true");
  });

  it("posts JSON for /ask and raw bytes for ZIP uploads", async () => {
    const fetch = respond(200, {});
    vi.stubGlobal("fetch", fetch);
    await api.ask({ project_id: "p", question: "What?" });
    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ project_id: "p", question: "What?" });
    await api.analyzeZip(new Blob(["PK"]), { filename: "a.zip", dry_run: true, expected_project_id: "x", confirm_update: true });
    const [url, zinit] = fetch.mock.calls[1] as unknown as [string, RequestInit];
    expect(url).toBe("/api/projects/analyze/zip?filename=a.zip&dry_run=true&expected_project_id=x&confirm_update=true");
    expect((zinit.headers as Record<string, string>)["Content-Type"]).toBe("application/zip");
  });

  it("maps backend errors to ApiError with component and structured detail", async () => {
    vi.stubGlobal("fetch", respond(429, { detail: { code: "llm_rate_limited", message: "LLM provider returned HTTP 429.", graph_context: { facts: ["a"] } }, component: "llm" }));
    const err = await failure(request("/ask"));
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(429);
    expect(err.component).toBe("llm");
    expect(err.code).toBe("llm_rate_limited");
    expect(err.message).toBe("LLM provider returned HTTP 429.");
    expect(errorText(err)).toBe("LLM error (429): LLM provider returned HTTP 429.");
  });

  it("explains FastAPI validation errors and unreachable backends", async () => {
    vi.stubGlobal("fetch", respond(422, { detail: [{ loc: ["query", "q"], msg: "String should have at least 1 character" }] }));
    const err = await failure(request("/x"));
    expect(err.message).toBe("q: String should have at least 1 character");
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));
    const down = await failure(request("/x"));
    expect(down.status).toBe(0);
    expect(down.component).toBe("network");
    vi.stubGlobal("fetch", respond(502, "Bad Gateway"));
    const proxy = await failure(request("/x"));
    expect(proxy.component).toBe("network");
  });

  it("reports cancellation as an AbortError, not as a failure", async () => {
    vi.stubGlobal("fetch", vi.fn((_u: string, init: RequestInit) => new Promise((_, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));
    const ctrl = new AbortController();
    const p = request("/slow", { signal: ctrl.signal });
    ctrl.abort();
    const err = await failure(p);
    expect(err.name).toBe("AbortError");
  });
});

describe("errors", () => {
  it("renders every detail shape", () => {
    expect(detailMessage("plain")).toBe("plain");
    expect(detailMessage({ message: "m", code: "c" })).toBe("m");
    expect(detailMessage(null)).toBe("");
  });
});

const node = (id: string, label = "File") => ({ id, label, caption: id, props: {} });
const edge = (id: string, source: string, target: string) => ({ id, type: "IMPORTS", source, target, props: {} });

describe("graph helpers", () => {
  it("merges pages by id without duplicating nodes, edges or Cypher", () => {
    const a: GraphPayload = { ...EMPTY_GRAPH, nodes: [node("1"), node("2")], edges: [edge("e1", "1", "2")], cypher: ["Q1"] };
    const b: GraphPayload = { ...EMPTY_GRAPH, nodes: [node("2"), node("3")], edges: [edge("e1", "1", "2"), edge("e2", "2", "3")], cypher: ["Q1", "Q2"] };
    const m = mergeGraphs(a, b);
    expect(m.nodes.map((n) => n.id)).toEqual(["1", "2", "3"]);
    expect(m.edges.map((e) => e.id)).toEqual(["e1", "e2"]);
    expect(m.cypher).toEqual(["Q1", "Q2"]);
    expect(mergeGraphs(null, b)).toBe(b);
  });

  it("drops edges of removed nodes and never draws half-attached edges", () => {
    const g: GraphPayload = { ...EMPTY_GRAPH, nodes: [node("1"), node("2")], edges: [edge("e1", "1", "2"), edge("e2", "2", "9")] };
    expect(drawableEdges(g).map((e) => e.id)).toEqual(["e1"]);
    expect(removeNodes(g, new Set(["2"])).edges).toEqual([]);
  });
});

describe("router", () => {
  it("round-trips project pages and parameters", () => {
    const hash = buildHash("explain", "my proj", { node: "a:b c", empty: "" });
    expect(hash).toBe("#/p/my%20proj/explain?node=a%3Ab+c");
    expect(parseHash(hash)).toEqual({ page: "explain", pid: "my proj", params: { node: "a:b c" } });
    expect(parseHash("#/analyze")).toEqual({ page: "analyze", pid: null, params: {} });
    expect(parseHash("#/p/x/unknown").page).toBe("overview");
    expect(parseHash("").page).toBe("overview");
    expect(buildHash("analyze", "x")).toBe("#/analyze");
  });
});

describe("layer diagram layout", () => {
  const comp = (name: string, layer: string, deps: string[] = [], files = 1) => ({
    name, layer, confidence: "high" as const, ambiguous_between: [], scores: {}, evidence: [], files: Array(files).fill("f.py"),
    symbol_count: 0, loc: 0, depends_on: deps.map((d) => ({ component: d, imports: 2, calls: 0 })), used_by: [], entry_points: [],
  });
  const arch: ArchitectureReport = {
    project_id: "p",
    layers: [
      { layer: "api", title: "API", components: ["routes"], file_count: 1 },
      { layer: "data", title: "Data access", components: ["repo"], file_count: 1 },
    ],
    components: [comp("routes", "api", ["repo"]), comp("repo", "data", ["routes"])],
    files: [], layer_edges: [], component_cycles: [], file_cycles: [], entry_points: [], notes: [],
    violations: [{ source_component: "repo", target_component: "routes", source_layer: "data", target_layer: "api", imports: 1, example: "repo.py -> routes.py" }],
  };

  it("places components in their backend layer and marks only reported violations", () => {
    const l = layoutLayers(arch);
    expect(l.bands.map((b) => b.layer)).toEqual(["api", "data"]);
    const routes = l.nodes.find((n) => n.name === "routes")!;
    const repo = l.nodes.find((n) => n.name === "repo")!;
    expect(routes.y).toBeLessThan(repo.y);
    expect(l.edges.find((e) => e.key === "repo→routes")!.violation).toBe(true);
    expect(l.edges.find((e) => e.key === "routes→repo")!.violation).toBe(false);
  });

  it("bounds the number of drawn components and reports the rest", () => {
    const many = { ...arch, components: Array.from({ length: 70 }, (_, i) => comp(`c${i}`, "api", [], 70 - i)) };
    const l = layoutLayers(many, 60);
    expect(l.nodes).toHaveLength(60);
    expect(l.hidden).toBe(10);
    expect(l.nodes.some((n) => n.name === "c69")).toBe(false);
  });
});

describe("formatting and prose", () => {
  it("formats source locations", () => {
    expect(fmtLocation({ file_path: "a.py", start_line: 3, end_line: 9 })).toBe("a.py:3–9");
    expect(fmtLocation({ file_path: "a.py" })).toBe("a.py");
    expect(fmtLocation(null)).toBe("—");
  });

  it("renders LLM markdown as text, never as HTML", () => {
    const html = renderToStaticMarkup(createElement(Prose, { text: "## Summary\n**Bold** and `code`\n\n- one\n- <img src=x onerror=alert(1)>" }));
    expect(html).toContain("<strong>Bold</strong>");
    expect(html).toContain('<code class="inline-code">code</code>');
    expect(html).toContain("<li>one</li>");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });
});
