const { execSync } = require("child_process");

const jobs = [
  {
    name: "Velib ingestion",
    argv: ["cmd.exe", "/c", "C:/Users/secre/.openclaw/velib-cron/ingest.cmd"],
  },
  {
    name: "Velib alertes KPI",
    argv: ["cmd.exe", "/c", "C:/Users/secre/.openclaw/velib-cron/kpi-alert.cmd"],
  },
];

function listIds() {
  const out = execSync("openclaw cron list", { encoding: "utf8" });
  return out;
}

function removeByName(name) {
  const out = listIds();
  const re = new RegExp(`([0-9a-f-]{36})\\s+${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`);
  const m = out.match(re);
  if (m) {
    execSync(`openclaw cron rm ${m[1]}`, { stdio: "inherit" });
  }
}

for (const job of jobs) {
  removeByName(job.name);
  const argvJson = JSON.stringify(JSON.stringify(job.argv));
  const cmd = [
    "openclaw cron add",
    '--cron "*/5 * * * *"',
    `--name "${job.name}"`,
    "--no-deliver",
    `--command-argv ${argvJson}`,
  ].join(" ");
  console.log(">", cmd);
  execSync(cmd, { stdio: "inherit", shell: true });
}

execSync("openclaw cron list", { stdio: "inherit" });
