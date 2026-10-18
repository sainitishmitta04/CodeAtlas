import React from "react";
import * as Orders from "../services/orders";
import { boot } from "../server/app";
import "./theme.css";

export default function App() {
  const service = new Orders.OrderService();
  service.place("pen");
  boot();
  return <div>{React.version}</div>;
}
