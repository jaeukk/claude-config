# Global working instructions

Use English with American spelling in replies, code, documentation, results and worker briefs. Use Korean only when explicitly requested.

Keep reusable research code in version control. Generated outputs live with their data. A one-off script can live beside its dataset only if it is one human-authored file, reads one dataset directory, and has no generic input interface, sweep, driver, or companion code/config. Keep other experiments in the tracked experiments directory. Use editable installs for reusable Python modules. For computational physics, use C++23 or Python and document functions.

In the research vault, 10_ResearchNotes holds dated daily notes, 20_Progress holds progress reports, 30_Literature holds literature work, and 40_Manuscript holds manuscript notes. ResearchPlan.md is a task list. Write one concise, descriptive, linked #task line per task. Protocols, run settings, job IDs, and decisions go in the corresponding detailed note. Load research-note before edits covered by that skill and verify the note with its checker. Resolve the vault on the current machine using zotero-obsidian-sync.

For research-code promotion, note formatting, Zotero, cluster access, scheduler and data-transfer rules, or the shared Python library, consult the relevant section of ~/.claude/research-conventions.md before acting. In a restricted worker, use the materials supplied for the task and report missing inputs to the conductor.

Edit shared global instructions in the configuration repository's multiagent/global-instructions.md. Add shared research conventions to research-conventions.md. Define new personal skills in the repository's skills/<name>/ and select their supported clients in scripts/link-skills.sh. Keep host-specific settings in the native client configuration. Restricted workers propose configuration changes through their conductor. Start a fresh session after publishing a revision.
