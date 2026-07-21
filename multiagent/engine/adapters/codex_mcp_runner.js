#!/usr/bin/env node
"use strict";

const { spawn } = require("node:child_process");

const isWindows = process.platform === "win32";
const child = isWindows
  ? spawn("cmd.exe", ["/d", "/s", "/c", "codex.cmd mcp-server"], { stdio: "inherit" })
  : spawn("codex", ["mcp-server"], { stdio: "inherit" });

child.on("error", (error) => {
  process.stderr.write(`Unable to start Codex MCP: ${error.message}\n`);
  process.exit(1);
});
child.on("exit", (code) => process.exit(code ?? 1));
