// Deterministic layout for the layer diagram: one horizontal band per inferred layer (in the
// backend's order), components as boxes inside their band, dependencies as curves.

import type { ArchitectureReport, Component } from "./types";

export interface LaidBand { layer: string; title: string; y: number; h: number; count: number }
export interface LaidNode { name: string; layer: string; confidence: Component["confidence"]; x: number; y: number; w: number; h: number; files: number }
export interface LaidEdge { key: string; source: string; target: string; imports: number; calls: number; violation: boolean; d: string }
export interface LayerLayout { width: number; height: number; bands: LaidBand[]; nodes: LaidNode[]; edges: LaidEdge[]; hidden: number }

const WIDTH = 1120;
const LABEL_W = 150;
const PAD = 14;
const BOX_H = 34;
const GAP_X = 12;
const GAP_Y = 12;

export function boxWidth(name: string): number {
  return Math.max(84, Math.min(230, name.length * 7.2 + 30));
}

export function layoutLayers(arch: ArchitectureReport, maxComponents = 60): LayerLayout {
  const shown = [...arch.components].sort((a, b) => b.files.length - a.files.length || a.name.localeCompare(b.name)).slice(0, maxComponents);
  const shownNames = new Set(shown.map((c) => c.name));
  const order = arch.layers.map((l) => l.layer);
  for (const c of shown) if (!order.includes(c.layer)) order.push(c.layer);
  const titles = new Map(arch.layers.map((l) => [l.layer, l.title]));

  const bands: LaidBand[] = [];
  const nodes: LaidNode[] = [];
  let y = 0;
  for (const layer of order) {
    const comps = shown.filter((c) => c.layer === layer).sort((a, b) => a.name.localeCompare(b.name));
    if (!comps.length) continue;
    let x = LABEL_W + PAD;
    let rowY = y + PAD;
    for (const c of comps) {
      const w = boxWidth(c.name);
      if (x + w > WIDTH - PAD && x > LABEL_W + PAD) {
        x = LABEL_W + PAD;
        rowY += BOX_H + GAP_Y;
      }
      nodes.push({ name: c.name, layer, confidence: c.confidence, x, y: rowY, w, h: BOX_H, files: c.files.length });
      x += w + GAP_X;
    }
    const h = rowY + BOX_H + PAD - y;
    bands.push({ layer, title: titles.get(layer) ?? layer, y, h, count: comps.length });
    y += h + 10;
  }

  const pos = new Map(nodes.map((n) => [n.name, n]));
  const violations = new Set(arch.violations.map((v) => `${v.source_component}→${v.target_component}`));
  const edges: LaidEdge[] = [];
  for (const c of shown) {
    for (const dep of c.depends_on) {
      if (!shownNames.has(dep.component) || dep.component === c.name) continue;
      const a = pos.get(c.name)!;
      const b = pos.get(dep.component)!;
      const ax = a.x + a.w / 2;
      const bx = b.x + b.w / 2;
      let d: string;
      if (Math.abs(a.y - b.y) < 1) {
        // same row: arc above the boxes
        const lift = 26 + Math.min(60, Math.abs(ax - bx) / 6);
        d = `M${ax},${a.y} C${ax},${a.y - lift} ${bx},${b.y - lift} ${bx},${b.y}`;
      } else if (b.y > a.y) {
        const ay = a.y + a.h;
        const my = (ay + b.y) / 2;
        d = `M${ax},${ay} C${ax},${my} ${bx},${my} ${bx},${b.y}`;
      } else {
        const by = b.y + b.h;
        const my = (a.y + by) / 2;
        d = `M${ax},${a.y} C${ax},${my} ${bx},${my} ${bx},${by}`;
      }
      edges.push({
        key: `${c.name}→${dep.component}`,
        source: c.name,
        target: dep.component,
        imports: dep.imports,
        calls: dep.calls,
        violation: violations.has(`${c.name}→${dep.component}`),
        d,
      });
    }
  }
  return { width: WIDTH, height: Math.max(y - 10, 60), bands, nodes, edges, hidden: arch.components.length - shown.length };
}
