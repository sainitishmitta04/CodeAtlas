import express from "express";
import { OrderService } from "../services/orders";

export class BaseServer {
  constructor(public port: number) {}
  register(): void {}
}

export class Server extends BaseServer {
  async start(): Promise<void> {
    this.register();
    const orders = new OrderService();
    orders.place("book");
  }
}

export function boot(): Server {
  return new Server(8080);
}
