import { defineRailway, github, project, service } from "railway/iac";
// This repository manages only its own resources in the environment. Other
// repositories export their own partial name.
// See https://docs.railway.com/infrastructure-as-code#multi-repo-projects
export const partial = "memecoin-radar";
export default defineRailway(() => {
  const memecoinRadar = service("memecoin-radar", {
    source: github("ONALISA001/memecoin-radar", { branch: "main", checkSuites: false, rootDirectory: "/backend" }),
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    healthcheck: "/",
    // Boot runs seed.py then the scheduler's first ingestion tick (~60-70 s each, and the
    // tick can hold the event loop), so 120 s was too tight: deploy 76f90a53 failed its
    // healthcheck on 2026-11-24 while startup was still in progress.
    healthcheckTimeout: 300,
    replicas: { "iad": 1 },
    // Restart policy must stay ALWAYS (backend/railway.toml had restartPolicyType = "ALWAYS"):
    // the watchdog exits the process when the scheduler stalls and Railway must bring it back
    // every time. IaC has no restart-policy field, so after cutover check the service's
    // Settings → Restart Policy in the dashboard is still "Always".
  });

  return project("victorious-cooperation", {
    variables: { managed: false },
    resources: [memecoinRadar],
  });
});
