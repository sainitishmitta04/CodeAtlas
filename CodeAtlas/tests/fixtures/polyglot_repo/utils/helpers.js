const path = require("path");

export function clean(text) {
  return text.trim();
}

export default function unused() {
  return path.join("a", "b");
}
