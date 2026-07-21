#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function parseArguments(argumentsList) {
  const options = { canonical: "", dryRun: false };
  for (let index = 0; index < argumentsList.length; index += 1) {
    const argument = argumentsList[index];
    if (argument === "--dry-run") {
      options.dryRun = true;
    } else if (argument === "--canonical") {
      options.canonical = argumentsList[index + 1] ?? "";
      index += 1;
    } else {
      throw new Error(`Unknown argument: ${argument}`);
    }
  }
  if (!options.canonical) {
    throw new Error("Usage: install_wsl_orchestration.js --canonical <skill-directory> [--dry-run]");
  }
  return options;
}

function statOrNull(filePath) {
  try {
    return fs.lstatSync(filePath);
  } catch (error) {
    if (error.code === "ENOENT") {
      return null;
    }
    throw error;
  }
}

function recordMove(source, destination, actions, dryRun) {
  if (!statOrNull(source)) {
    return;
  }
  if (statOrNull(destination)) {
    throw new Error(`Archive destination already exists: ${destination}`);
  }
  actions.push({ action: "archive", source, destination });
  if (!dryRun) {
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.renameSync(source, destination);
  }
}

function ensureLink(linkPath, target, actions, dryRun) {
  const existing = statOrNull(linkPath);
  if (existing) {
    if (!existing.isSymbolicLink() || fs.readlinkSync(linkPath) !== target) {
      throw new Error(`Refusing to replace existing path: ${linkPath}`);
    }
    return;
  }
  actions.push({ action: "link", link: linkPath, target });
  if (!dryRun) {
    fs.mkdirSync(path.dirname(linkPath), { recursive: true });
    fs.symlinkSync(target, linkPath, "dir");
  }
}

function migrateSettings(settingsPath, actions, dryRun) {
  const settings = JSON.parse(fs.readFileSync(settingsPath, "utf8"));
  const legacyGate = settings.hooks?.PreToolUse;
  const changed = settings.model !== "claude-fable-5" || legacyGate !== undefined;
  if (!changed) {
    return;
  }
  const backup = `${settingsPath}.pre-orchestration-20260720`;
  if (!statOrNull(backup)) {
    actions.push({ action: "backup", source: settingsPath, destination: backup });
    if (!dryRun) {
      fs.copyFileSync(settingsPath, backup);
    }
  }
  settings.model = "claude-fable-5";
  if (settings.hooks) {
    delete settings.hooks.PreToolUse;
  }
  actions.push({ action: "update", path: settingsPath, detail: "set Fable 5 and remove legacy PreToolUse gate" });
  if (!dryRun) {
    fs.writeFileSync(settingsPath, `${JSON.stringify(settings, null, 2)}\n`, "utf8");
  }
}

function removeLegacyBashrcLoader(bashrcPath, actions, dryRun) {
  const original = fs.readFileSync(bashrcPath, "utf8");
  const updated = original
    .split("\n")
    .filter((line) => !line.includes("fable orchestration layer") && !line.includes("$HOME/.claude/fable/env.sh"))
    .join("\n");
  if (updated === original) {
    return;
  }
  const backup = `${bashrcPath}.pre-orchestration-20260720`;
  if (!statOrNull(backup)) {
    actions.push({ action: "backup", source: bashrcPath, destination: backup });
    if (!dryRun) {
      fs.copyFileSync(bashrcPath, backup);
    }
  }
  actions.push({ action: "update", path: bashrcPath, detail: "remove legacy Fable model-remapping loader" });
  if (!dryRun) {
    fs.writeFileSync(bashrcPath, updated, "utf8");
  }
}

function main() {
  if (process.platform === "win32") {
    throw new Error("Run this migration inside WSL, not Windows.");
  }
  const { canonical, dryRun } = parseArguments(process.argv.slice(2));
  const canonicalPath = path.resolve(canonical);
  if (!statOrNull(canonicalPath)?.isDirectory()) {
    throw new Error(`Canonical skill directory does not exist: ${canonicalPath}`);
  }
  const home = os.homedir();
  const claude = path.join(home, ".claude");
  const codex = path.join(home, ".codex");
  const actions = [];

  migrateSettings(path.join(claude, "settings.json"), actions, dryRun);
  removeLegacyBashrcLoader(path.join(home, ".bashrc"), actions, dryRun);
  recordMove(path.join(claude, "fable"), path.join(claude, "legacy", "fable-20260720"), actions, dryRun);
  recordMove(
    path.join(claude, "skills", "fable-orchestration"),
    path.join(claude, "legacy", "fable-orchestration-skill-20260720"),
    actions,
    dryRun,
  );
  ensureLink(path.join(claude, "skills", "orchestration"), canonicalPath, actions, dryRun);
  ensureLink(path.join(codex, "skills", "orchestration"), canonicalPath, actions, dryRun);
  process.stdout.write(`${JSON.stringify({ dryRun, actions }, null, 2)}\n`);
}

try {
  main();
} catch (error) {
  process.stderr.write(`WSL orchestration migration failed: ${error.message}\n`);
  process.exit(1);
}
