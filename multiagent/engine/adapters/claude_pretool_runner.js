#!/usr/bin/env node
"use strict";

const { spawnSync } = require("node:child_process");
const path = require("node:path");

const python = process.platform === "win32"
  ? "C:\\Users\\김재욱\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe"
  : "python3";
const adapter = path.join(__dirname, "claude_pretool.py");
const result = spawnSync(python, [adapter], { stdio: "inherit" });

if (result.error) {
  process.stderr.write(`Unable to start the multi-agent policy hook: ${result.error.message}\n`);
  process.exit(1);
}
process.exit(result.status ?? 1);
