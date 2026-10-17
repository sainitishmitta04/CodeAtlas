import { lazy, Suspense, type ComponentProps } from "react";
import { Loading } from "../feedback/Feedback";

// Sigma + graphology are only downloaded when a page actually shows a graph.
const GraphCanvas = lazy(() => import("./GraphCanvas"));

export function Graph(props: ComponentProps<typeof GraphCanvas>) {
  return (
    <Suspense fallback={<div className="graph-canvas" style={{ height: props.height ?? 620 }}><Loading label="Loading graph renderer…" rows={0} /></div>}>
      <GraphCanvas {...props} />
    </Suspense>
  );
}
